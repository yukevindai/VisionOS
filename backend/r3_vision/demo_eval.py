"""DEMO recognizer evaluation on recorded PathOn frames, holding out whole recording sessions.

For each session, references come only from the OTHER sessions (as harvest would choose them); every frame of the
held-out session is then analysed as recorded, with SIMULATED camera motion (pan/zoom/rotation/blur), with a
SIMULATED hand-coloured occluder over 45% of one target, and with one target erased (absent). Ground truth is the
saved Gemini object list of each frame (labels and boxes were checked on a contact sheet). Each of the six requests
is resolved per frame: correct local lock, false match (wrong physical object or absent target), or Gemini fallback.
Thresholds are swept on the stored scores. Writes .runtime/demo-eval/report.json and false-match thumbnails.

    python -m r3_vision.demo_eval            # DeskGuide venv (Ultralytics)
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import demo_objects as demo
from .demo_objects import OBJECTS, OTHER, _contained, _iou, crop, labelled_captures, resolve

REPORT = demo.BACKEND / '.runtime/demo-eval'
REQUESTS = {'pepsi': 'Find the Pepsi can', 'redbull': 'Find the Red Bull can', 'coke': 'Find the Coca-Cola can',
            'cards': 'Find the deck of cards', 'white_bagel': 'Find the white half bagel',
            'brown_bagel': 'Find the brown bagel'}


def truth(box, items, erased=None):
    """Physical object a proposal box shows: target key (IoU >= .5 or mostly inside it), else OTHER."""
    best = None
    for key, gt, _ in items:
        if key == OTHER or key == erased:
            continue
        if _iou(box, gt) >= .5 or _contained(box, gt) > .6 and _area(box) <= _area(gt) * 1.3:
            if best is None or _iou(box, gt) > best[1]:
                best = (key, _iou(box, gt))
    return best[0] if best else OTHER


def _area(b):
    return (b[2] - b[0]) * (b[3] - b[1])


def references(captures, sessions, per_key=24, negatives=60):
    keys, images = [], []
    pools = defaultdict(list)
    for c in captures:
        if c['session'] in sessions:
            for key, box, _ in c['items']:
                pools[key].append((c, box))
    for key, items in pools.items():
        limit = negatives if key == OTHER else per_key
        step = max(1, len(items) / limit)
        for c, box in [items[int(i * step)] for i in range(min(limit, len(items)))]:
            keys.append(key)
            images.append(crop(Image.open(c['path']).convert('RGB'), box))
    return keys, images


def camera(image, rng):
    a = np.asarray(image)
    h, w = a.shape[:2]
    A = cv2.getRotationMatrix2D((w / 2, h / 2), rng.uniform(-8, 8), rng.uniform(.85, 1.15))
    A[:, 2] += (rng.uniform(-.08, .08) * w, rng.uniform(-.06, .06) * h)
    out = cv2.warpAffine(a, A, (w, h), borderMode=cv2.BORDER_REPLICATE)
    k = rng.choice([1, 5, 9])
    if k > 1:  # horizontal motion blur
        kernel = np.zeros((k, k), np.float32)
        kernel[k // 2] = 1 / k
        out = cv2.filter2D(out, -1, kernel)
    return Image.fromarray(out), A


def warp_items(items, A, size):
    w, h = size
    moved = []
    for key, (x0, y0, x1, y1), label in items:
        corners = np.float32([[x0 * w, y0 * h], [x1 * w, y0 * h], [x1 * w, y1 * h], [x0 * w, y1 * h]])
        c = corners @ A[:, :2].T + A[:, 2]
        (mx0, my0), (mx1, my1) = c.min(0), c.max(0)
        box = (max(0., mx0 / w), max(0., my0 / h), min(1., mx1 / w), min(1., my1 / h))
        if box[2] - box[0] > .01 and box[3] - box[1] > .01:
            moved.append((key, box, label))
    return moved


def occlude(image, box, rng):
    """Hand-coloured, slightly textured ellipse over ~45% of the box, entering from a random side."""
    out = image.copy()
    w, h = image.size
    x0, y0, x1, y1 = box[0] * w, box[1] * h, box[2] * w, box[3] * h
    bw, bh = x1 - x0, y1 - y0
    side = rng.choice(['left', 'right', 'bottom'])
    if side == 'left':
        e = (x0 - .5 * bw, y0 - .1 * bh, x0 + .45 * bw, y1 + .1 * bh)
    elif side == 'right':
        e = (x1 - .45 * bw, y0 - .1 * bh, x1 + .5 * bw, y1 + .1 * bh)
    else:
        e = (x0 - .1 * bw, y1 - .45 * bh, x1 + .1 * bw, y1 + .5 * bh)
    mask = Image.new('L', image.size, 0)
    ImageDraw.Draw(mask).ellipse(e, fill=255)
    tone = tuple(int(v) for v in rng.choice([(224, 172, 140), (198, 134, 99), (141, 85, 56), (240, 200, 170)]))
    skin = Image.new('RGB', image.size, tone)
    noise = Image.effect_noise(image.size, 12).convert('RGB')
    skin = Image.blend(skin, noise, .08).filter(ImageFilter.GaussianBlur(2))
    out.paste(skin, (0, 0), mask.filter(ImageFilter.GaussianBlur(3)))
    return out


def erase(image, box):
    """Remove one object: inpaint its box (on a reduced image, then scaled back into the frame)."""
    w, h = image.size
    scale = 640 / max(w, h)
    small = np.asarray(image.resize((round(w * scale), round(h * scale))))
    mask = np.zeros(small.shape[:2], np.uint8)
    x0, y0, x1, y1 = box
    sh, sw = mask.shape
    mask[max(0, int(y0 * sh) - 3):int(y1 * sh) + 3, max(0, int(x0 * sw) - 3):int(x1 * sw) + 3] = 255
    filled = cv2.inpaint(small, mask, 7, cv2.INPAINT_TELEA)
    patch = Image.fromarray(filled).resize((w, h))
    out = image.copy()
    region = (max(0, int(x0 * w) - 10), max(0, int(y0 * h) - 10), min(w, int(x1 * w) + 10), min(h, int(y1 * h) + 10))
    out.paste(patch.crop(region), region[:2])
    return out


def outcome(decision, key, items, erased=None):
    if decision['kind'] != 'LOCAL':
        return 'fallback' if decision['kind'] is None else 'ambiguous'
    shown = truth(decision['box'], items, erased)
    return 'correct' if shown == key and decision['key'] == key else 'false_match'


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--limit', type=int, help='frames per session (quick run)')
    p.add_argument('--seed', type=int, default=7)
    args = p.parse_args(argv)
    import torch
    torch.set_num_threads(4)
    cv2.setNumThreads(1)
    from .local_detector import LocalDetector
    REPORT.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    detector = LocalDetector(background=False)
    identifier = demo.Identifier()
    planar = demo.PlanarMatcher()
    load_s = time.perf_counter() - started
    captures = labelled_captures()
    sessions = sorted({c['session'] for c in captures})
    rng = random.Random(args.seed)
    records, timings = [], []
    for s in sessions:
        keys, images = references(captures, set(sessions) - {s})
        identifier.set_references(keys, images)
        planar.set_references(keys, images)
        rec = demo.DemoRecognizer(detector, identifier=identifier, planar=planar)
        frames = [c for c in captures if c['session'] == s][:args.limit]
        print(f'session {s}: {len(frames)} frames, {len(keys)} references {dict(Counter(keys))}', flush=True)
        for c in frames:
            image = Image.open(c['path']).convert('RGB')
            present = [k for k, _, _ in c['items'] if k in OBJECTS]
            scenes = [('original', image, c['items'], None, list(OBJECTS))]
            moved, A = camera(image, rng)
            scenes.append(('camera', moved, warp_items(c['items'], A, image.size), None, list(OBJECTS)))
            target = rng.choice(present)
            box = next(b for k, b, _ in c['items'] if k == target)
            scenes.append(('hand', occlude(image, box, rng), c['items'], None, [target]))
            gone = rng.choice(present)
            gone_box = next(b for k, b, _ in c['items'] if k == gone)
            scenes.append(('absent', erase(image, gone_box), c['items'], gone, [gone]))
            for scene, img, items, erased, wanted in scenes:
                found, timing = rec.analyse(img, list(OBJECTS))
                for f in found:
                    f['truth'] = truth(f['box'], items, erased)
                records.append(dict(capture=c['name'], session=s, scene=scene, erased=erased, wanted=wanted,
                                    present=sorted({k for k, _, _ in items if k in OBJECTS and k != erased}),
                                    found=[dict(box=f['box'], scores=f.get('scores', {}), truth=f['truth'],
                                                names=f['names'], sift=f.get('sift')) for f in found],
                                    timing=timing))
            # Real per-request latency, exactly as the live tool calls it (one request per frame).
            key = rng.choice(list(OBJECTS))
            job = type('Job', (), dict(query=REQUESTS[key], image=image))()
            decision = rec.choose(job)
            timings.append(dict(key=key, kind=decision['kind'], **{k: round(v, 1) for k, v in decision['timing'].items()},
                                proposals=len(decision['candidates'])))
    report = dict(simulated_parts='camera motion, hand occluder and erased targets are SIMULATED edits of real frames',
                  frames=len(captures), sessions=len(sessions), modelLoadS=round(load_s, 1),
                  latency=summarize_latency(timings), sweep=sweep(records))
    best = choose_point(report['sweep'])
    report['chosen'] = best
    report['atChosen'] = evaluate(records, best['min_score'], best['min_margin'], save=True)
    report['atDefaults'] = evaluate(records, demo.MIN_SCORE, demo.MIN_MARGIN)
    (REPORT / 'records.json').write_text(json.dumps(records), encoding='utf-8')
    (REPORT / 'report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('latency', 'chosen', 'atChosen', 'atDefaults')}, indent=1))
    return 0


def summarize_latency(timings):
    out = {}
    for field in ('total', 'detect', 'sift', 'identify', 'proposals'):
        values = [t[field] for t in timings if field in t]
        if values:
            out[field] = dict(median=round(statistics.median(values), 1), p90=round(float(np.percentile(values, 90)), 1),
                              max=round(max(values), 1))
    out['kinds'] = dict(Counter(t['kind'] or 'GEMINI' for t in timings))
    return out


def evaluate(records, min_score, min_margin, save=False):
    table = defaultdict(Counter)
    false_matches = []
    for r in records:
        for key in r['wanted']:
            found = [dict(f) for f in r['found']]
            decision = resolve((key,), OBJECTS[key].name, found, min_score, min_margin)
            if r['scene'] == 'absent' or key not in r['present']:
                kind = 'absent'
                result = 'false_match' if decision['kind'] == 'LOCAL' else 'correctly_not_locked'
            else:
                kind = r['scene']
                if decision['kind'] != 'LOCAL':
                    result = 'fallback'
                else:
                    chosen = next(f for f in found if f['box'] == decision['box'])
                    result = 'correct' if chosen['truth'] == key else 'false_match'
            table[f'{kind}/{key}'][result] += 1
            table[kind][result] += 1
            if result == 'false_match':
                false_matches.append(dict(capture=r['capture'], scene=r['scene'], request=key,
                                          chosen=decision['box'], reason=decision['reason']))
    if save:
        sheet(false_matches[:24])
    return dict(minScore=min_score, minMargin=min_margin, byScene={k: dict(v) for k, v in table.items() if '/' not in k},
                byObject={k: dict(v) for k, v in sorted(table.items()) if '/' in k}, falseMatches=false_matches[:40])


def sweep(records):
    rows = []
    for min_score in (.95, 1.0, 1.05, 1.1, 1.15, 1.2):
        for min_margin in (0., .02, .04, .06, .08, .12):
            e = evaluate(records, min_score, min_margin)
            correct = sum(v.get('correct', 0) for v in e['byScene'].values())
            false = sum(v.get('false_match', 0) for v in e['byScene'].values())
            present = sum(v.get('correct', 0) + v.get('false_match', 0) + v.get('fallback', 0)
                          for k, v in e['byScene'].items() if k != 'absent')
            rows.append(dict(min_score=min_score, min_margin=min_margin, correct=correct, present=present,
                             false_matches=false))
    return rows


def choose_point(rows):
    """Most correct local locks with zero false matches; else fewest false matches."""
    zero = [r for r in rows if r['false_matches'] == 0]
    pool = zero or sorted(rows, key=lambda r: r['false_matches'])[:5]
    return max(pool, key=lambda r: (r['correct'], -r['min_score']))


def sheet(false_matches):
    if not false_matches:
        return
    tiles = []
    for f in false_matches:
        image = Image.open(demo.CAPTURES / f['capture'] / 'captured.png').convert('RGB')
        tile = crop(image, f['chosen'], .3)
        tile.thumbnail((160, 160))
        canvas = Image.new('RGB', (160, 185), 'white')
        canvas.paste(tile, (0, 0))
        ImageDraw.Draw(canvas).text((2, 165), f"{f['scene']}: {f['request']}", fill='black')
        tiles.append(canvas)
    out = Image.new('RGB', (160 * min(8, len(tiles)), 185 * ((len(tiles) + 7) // 8)), 'white')
    for i, t in enumerate(tiles):
        out.paste(t, ((i % 8) * 160, (i // 8) * 185))
    out.save(REPORT / 'false-matches.png')


if __name__ == '__main__':
    raise SystemExit(main())
