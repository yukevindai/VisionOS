"""HYBRID identification: the usual Gemini search runs in parallel with a local detector on the same frame.

A local lock is allowed only for a plain object word with exactly one candidate that the detector named only with that
word; Gemini's answer then confirms it, or the box is removed as UNCERTAIN. Brand, colour or other descriptive words,
spatial relations, several candidates, or no candidate wait for Gemini, whose box is replaced by an overlapping local
box when one exists. Detector class names are never brand evidence, and detector confidence never breaks a tie.
(Measured: local relation ranking chose a can for "cup beside the laptop" in 5/5 frames, because the detector calls
cans cups, so relations are left to Gemini.)
"""
from __future__ import annotations

import re

from . import selector

RELATIONS = [('to the left of', 'left'), ('left of', 'left'), ('to the right of', 'right'), ('right of', 'right'),
             ('next to', 'near'), ('beside', 'near'), ('near', 'near'), ('by', 'near'), ('on top of', 'on'),
             ('on', 'on'), ('under', 'below'), ('below', 'below'), ('above', 'above'), ('behind', 'behind'),
             ('in front of', 'front')]
AGREE_IOU = .5


def parse(request):
    text = ' '.join(selector.tokens(request))
    for phrase, relation in RELATIONS:
        match = re.search(rf'\b{phrase}\b', text)
        if match:
            target = ' '.join(w for w in text[:match.start()].split() if w not in selector.FILLER_WORDS)
            anchor = ' '.join(w for w in text[match.end():].split() if w not in selector.FILLER_WORDS)
            if target and anchor:
                return target, relation, anchor
    return ' '.join(selector.content_tokens(request)), None, None


def head_noun(phrase):
    """(head noun, descriptive words before it). "cup of coffee" -> ("cup", []); "black tumbler" -> ("tumbler", ["black"])."""
    words = phrase.split()
    index = words.index('of') - 1 if 'of' in words[1:] else len(words) - 1
    return words[index], words[:index]


def phrases(request):
    target, _, anchor = parse(request)
    out = []
    for phrase in (target, anchor):
        if phrase:
            out += [phrase, head_noun(phrase)[0]]
    return list(dict.fromkeys(out))


def local_choice(request, candidates):
    """(candidate or None, reason). None means: wait for Gemini."""
    target, relation, anchor_phrase = parse(request)
    if not target:
        return None, 'empty request'
    head, modifiers = head_noun(target)
    matches = [c for c in candidates if target in c['names'] or head in c['names']]
    if modifiers:
        return None, f'descriptive words {modifiers} need Gemini'
    if relation:
        return None, f'relation "{relation} {anchor_phrase}" needs Gemini'
    if len(matches) == 1 and matches[0]['names'] == [head]:
        return matches[0], f'one {head} detected'
    if len(matches) == 1:
        return None, f"one candidate but detector also called it {matches[0]['names']}"
    return None, f'{len(matches)} local {head} candidates'


def iou(a, b):
    inter = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.


def region(box, size):
    w, h = size
    x0, y0 = max(0., box[0]), max(0., box[1])
    x1, y1 = min(float(w), box[2]), min(float(h), box[3])
    return {'x': round(x0 / w, 4), 'y': round(y0 / h, 4), 'width': round((x1 - x0) / w, 4), 'height': round((y1 - y0) / h, 4)}


def pixels(area, size):
    w, h = size
    return (area['x'] * w, area['y'] * h, (area['x'] + area['width']) * w, (area['y'] + area['height']) * h)


def result(job, status, source, reason, timing, candidates, chosen=None, label=''):
    size = job.image.size
    controls = [{'id': f"det-{c['id']:03d}", 'label': '/'.join(c['names']), 'type': 'other', 'legibility': 'clear',
                 'region': region(c['box'], size), 'detectorScore': round(c['score'], 3)} for c in candidates]
    model = {'frameId': job.frame_id, 'coordinateSystem': selector.SUPPORTED_COORDINATES,
             'image': {'widthPx': size[0], 'heightPx': size[1]}, 'controls': controls, 'availability': 'available'}
    selection = {'status': status, 'reason': reason, 'request': job.query, 'frameId': job.frame_id}
    if chosen is not None:
        selection['target'] = {'label': label or '/'.join(chosen['names']), 'imageRegion': region(chosen['box'], size),
                               'frameId': job.frame_id, 'coordinateSystem': selector.SUPPORTED_COORDINATES,
                               'detectorId': chosen['id']}
    return {'mode': 'HYBRID', 'detectionSource': source, 'screenModel': model, 'selection': selection,
            'provider': {'ok': True}, 'timingMs': timing}


def verify(answer, chosen, size):
    """Gemini's independent answer on the same frame versus the local lock."""
    seconds = answer.get('timingMs', {}).get('provider', 0)
    selection = answer.get('selection', {})
    if not answer.get('provider', {}).get('ok'):
        agrees, detail = None, 'Gemini unavailable'
    elif selection.get('status') == 'SELECTED' and selection.get('target', {}).get('imageRegion'):
        overlap = iou(pixels(selection['target']['imageRegion'], size), chosen['box'])
        agrees = overlap >= AGREE_IOU
        detail = f"Gemini chose '{selection['target'].get('label')}' (overlap {overlap:.2f} with the local box)"
    else:
        agrees, detail = False, f"Gemini: {selection.get('status')} / {selection.get('reason')}"
    return {'mode': 'HYBRID_VERIFY', 'agrees': agrees, 'detail': detail, 'timingMs': {'provider': seconds}}


def snap(answer, candidates, size, local_ms):
    """Gemini-selected object, with the overlapping local box (if any) as its region. Same frame and coordinates."""
    answer = dict(answer)
    answer['timingMs'] = dict(answer.get('timingMs', {}), local=round(local_ms, 1))
    answer['detectionSource'] = 'GEMINI'
    selection = answer.get('selection', {})
    target = selection.get('target')
    if selection.get('status') == 'SELECTED' and target and target.get('imageRegion'):
        gemini_box = pixels(target['imageRegion'], size)
        best = max(candidates, key=lambda c: iou(c['box'], gemini_box), default=None)
        if best is not None and iou(best['box'], gemini_box) >= AGREE_IOU:
            answer['selection'] = dict(selection, target=dict(target, imageRegion=region(best['box'], size),
                                                              geminiRegion=target['imageRegion']))
            answer['detectionSource'] = 'GEMINI+YOLO box'
    return answer
