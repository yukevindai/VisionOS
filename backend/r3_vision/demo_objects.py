"""DEMO mode: six registered physical objects, recognized locally from reference photos; Gemini only when unsure.

Objects: Pepsi can, Red Bull can, Coca-Cola can, deck of cards (Bicycle box), white half bagel, brown bagel.

Proposals come from YOLO-World with one fixed, cached vocabulary (loaded and warmed at launch; no background
discovery) and, for the card box, which YOLO-World misses (0/25 recorded frames), from SIFT + RANSAC homography
against the registered card photos. Identity is decided per proposal by appearance against the registered reference
photos: CLIP ViT-B/32 image embedding (the encoder YOLO-World already uses) plus an HSV colour histogram, nearest
reference per object, with an open-set rule (minimum score and margin over every other object and over registered
non-target objects). Detector class names and confidence never decide identity. Two proposals identified as the same
requested object, or any doubt, go to Gemini through the normal HYBRID flow; a confident single match locks locally.

Reference photos live in backend/.runtime/demo-objects/<object>/ (ignored by Git; photos of the team's objects).

    python -m r3_vision.demo_objects harvest                       # bootstrap from recorded PathOn captures
    python -m r3_vision.demo_objects register pepsi photo.jpg      # whole photo is the object
    python -m r3_vision.demo_objects register cards photo.jpg --box 410,220,760,700   # pixel box
    python -m r3_vision.demo_objects list
"""
from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault('YOLO_AUTOINSTALL', 'false')

import numpy as np
from PIL import Image

from . import selector

BACKEND = Path(__file__).resolve().parents[1]
STORE = BACKEND / '.runtime/demo-objects'
CLIP_WEIGHTS = BACKEND / 'weights/clip/ViT-B-32.pt'
CAPTURES = BACKEND / 'recordings/r3-tracking'
OTHER = 'other'  # registered non-target objects (cube, bottle, cup, laptop, ...) for open-set rejection
# Fixed detector vocabulary: the targets' generic classes plus common desk distractors so they get their own boxes.
VOCABULARY = ('can', 'bagel', 'box', 'cup', 'bottle', 'cube', 'laptop', 'phone', 'keyboard')
# Open-set acceptance (calibrated on held-out recorded sessions with demo_eval; see HANDOFF).
MIN_SCORE = 1.2
MIN_MARGIN = .02
SIFT_MIN_INLIERS = 14


@dataclass(frozen=True)
class DemoObject:
    key: str
    name: str
    aliases: tuple
    classes: tuple  # detector classes that may propose this object
    planar: bool = False  # flat printed object: SIFT homography proposals
    category: str = ''


OBJECTS = {o.key: o for o in (
    DemoObject('pepsi', 'Pepsi can', ('pepsi', 'diet pepsi', 'pepsi can'), ('can', 'cup', 'bottle'), category='can'),
    DemoObject('redbull', 'Red Bull can', ('red bull', 'redbull', 'energy drink'), ('can', 'cup', 'bottle'),
               category='can'),
    DemoObject('coke', 'Coca-Cola can', ('coca cola', 'cocacola', 'coke', 'cola'), ('can', 'cup', 'bottle'),
               category='can'),
    DemoObject('cards', 'deck of cards', ('deck of cards', 'playing cards', 'card deck', 'cards', 'deck', 'card box',
                                          'bicycle', 'card'), ('box', 'phone'), planar=True, category='cards'),
    DemoObject('white_bagel', 'white half bagel', ('white half bagel', 'white bagel', 'half bagel', 'seeded bagel',
                                                   'everything bagel', 'sesame bagel', 'light bagel'), ('bagel',),
               category='bagel'),
    DemoObject('brown_bagel', 'brown bagel', ('brown bagel', 'plain bagel', 'whole bagel', 'dark bagel'), ('bagel',),
               category='bagel'),
)}
CATEGORY_WORDS = {'can': ('can', 'soda', 'drink', 'soda can'), 'bagel': ('bagel',), 'cards': ()}


def parse_request(request):
    """(object keys the request can mean, description) or None if it is not about the demo objects."""
    text = ' '.join(selector.tokens(request))
    joined = text.replace(' ', '')
    best = None
    for o in OBJECTS.values():
        for alias in o.aliases:
            if re.search(rf'\b{re.escape(alias)}\b', text) or (len(alias) > 5 and alias.replace(' ', '') in joined):
                if best is None or len(alias) > best[1]:
                    best = (o.key, len(alias))
    if best is not None:
        return (best[0],), OBJECTS[best[0]].name
    for category, words in CATEGORY_WORDS.items():
        if any(re.search(rf'\b{w}s?\b', text) for w in words):
            return tuple(k for k, o in OBJECTS.items() if o.category == category), category
    return None


# ---- reference photos ------------------------------------------------------------------------------------------
class ReferenceStore:
    def __init__(self, root=STORE):
        self.root = Path(root)

    def add(self, key, image, box=None, name=None):
        if key not in OBJECTS and key != OTHER:
            raise ValueError(f'unknown demo object {key!r}; use one of {sorted(OBJECTS)} or {OTHER!r}')
        image = image.convert('RGB')
        if box is not None:
            image = image.crop(tuple(int(round(v)) for v in box))
        if min(image.size) < 16:
            raise ValueError('reference photo is too small (needs at least 16 px each side)')
        folder = self.root / key
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'{name or uuid.uuid4().hex[:12]}.png'
        image.save(path)
        return path

    def entries(self):
        if not self.root.exists():
            return []
        return [(folder.name, path) for folder in sorted(self.root.iterdir())
                if folder.is_dir() and (folder.name in OBJECTS or folder.name == OTHER)
                for path in sorted(folder.glob('*.png'))]

    def counts(self):
        out = {}
        for key, _ in self.entries():
            out[key] = out.get(key, 0) + 1
        return out


def key_of_label(label):
    """Map a saved Gemini label from the recorded demo captures to an object key; None if the wording is unclear."""
    text = label.lower()
    if 'bagel' in text:
        if re.search(r'seeded|everything|sesame|white', text):
            return 'white_bagel'
        if re.search(r'\bplain\b|brown|blueberry|cinnamon|raisin', text):
            return 'brown_bagel'
        return None
    for key, pattern in (('pepsi', r'pepsi'), ('redbull', r'red ?bull'), ('coke', r'coca|coke'),
                         ('cards', r'playing card|\bcards\b|card deck|deck of cards')):
        if re.search(pattern, text):
            return key
    if re.search(r'\bcan\b', text):
        return None  # another can wording: not a reliable negative
    return OTHER


def labelled_captures(root=CAPTURES):
    """Recorded PathOn frames with saved Gemini object lists: dict(name, path, t, items=[(key, box, label)])."""
    out = []
    for result_path in sorted(Path(root).glob('track-*/result.json')):
        if not (result_path.parent / 'captured.png').exists():
            continue
        try:
            result = json.loads(result_path.read_text(encoding='utf-8'))
            meta = json.loads((result_path.parent / 'capture.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not result.get('provider', {}).get('ok') or result.get('query') == 'target':
            continue
        items = []
        for control in (result.get('screenModel') or {}).get('controls') or []:
            region, label = control.get('region'), control.get('label') or ''
            key = key_of_label(label) if label else None
            if key is None or not selector.region_is_valid(region):
                continue
            items.append((key, (region['x'], region['y'], region['x'] + region['width'],
                                region['y'] + region['height']), label))
        if any(k in OBJECTS for k, _, _ in items):
            out.append(dict(name=result_path.parent.name, path=result_path.parent / 'captured.png',
                            t=meta.get('snapshotUTCms', 0), items=items))
    out.sort(key=lambda c: c['t'])
    session, last = 0, None
    for c in out:
        if last is not None and c['t'] - last > 15 * 60 * 1000:
            session += 1
        c['session'] = session
        last = c['t']
    return out


def crop(image, box, pad=.04):
    """Normalized box -> crop with a small margin."""
    w, h = image.size
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    return image.crop((max(0, round((x0 - pad * bw) * w)), max(0, round((y0 - pad * bh) * h)),
                       min(w, round((x1 + pad * bw) * w)), min(h, round((y1 + pad * bh) * h))))


def harvest(store, captures=None, per_key=24, negatives=60, sessions=None):
    """Bootstrap references from the recorded captures, spread over sessions and time."""
    captures = labelled_captures() if captures is None else captures
    chosen = {}
    for c in captures:
        if sessions is not None and c['session'] not in sessions:
            continue
        for key, box, label in c['items']:
            chosen.setdefault(key, []).append((c, box))
    written = {}
    for key, items in chosen.items():
        limit = negatives if key == OTHER else per_key
        step = max(1, len(items) / limit)
        picks = [items[int(i * step)] for i in range(min(limit, len(items)))]
        for c, box in picks:
            store.add(key, crop(Image.open(c['path']), box), name=f"{c['name'][6:18]}-{uuid.uuid4().hex[:4]}")
            written[key] = written.get(key, 0) + 1
    return written


# ---- appearance identity ---------------------------------------------------------------------------------------
def hsv_hist(image):
    import cv2
    a = cv2.cvtColor(np.asarray(image.convert('RGB').resize((64, 64))), cv2.COLOR_RGB2HSV)[8:56, 8:56]
    hist = cv2.calcHist([a], [0, 1, 2], None, [12, 4, 4], [0, 180, 0, 256, 0, 256]).ravel()
    return hist / max(hist.sum(), 1.)


class Identifier:
    """Nearest registered reference per object: CLIP cosine + 0.5 x HSV histogram intersection. Open-set."""
    def __init__(self, weights=CLIP_WEIGHTS):
        import clip
        import torch
        self.torch = torch
        self.model, self.preprocess = clip.load(str(weights), device='cpu')
        self.model.eval()
        self.keys, self.clip, self.hsv = [], np.zeros((0, 512), np.float32), np.zeros((0, 192), np.float32)

    def embed(self, images):
        if not images:
            return np.zeros((0, 512), np.float32)
        with self.torch.no_grad():
            features = self.model.encode_image(self.torch.stack([self.preprocess(i) for i in images])).float()
        return (features / features.norm(dim=1, keepdim=True)).numpy()

    def set_references(self, keys, images, embeddings=None):
        self.keys = list(keys)
        self.clip = self.embed(images) if embeddings is None else np.asarray(embeddings, np.float32)
        self.hsv = np.stack([hsv_hist(i) for i in images]) if images else np.zeros((0, 192), np.float32)

    def load_store(self, store, cache=True):
        entries = store.entries()
        stamp = [[k, p.name, p.stat().st_mtime_ns] for k, p in entries]
        cache_file = store.root / 'features.npz'
        images = [Image.open(p).convert('RGB') for _, p in entries]
        if cache and cache_file.exists():
            data = np.load(cache_file, allow_pickle=False)
            if json.loads(str(data['stamp'])) == stamp:
                self.set_references([k for k, _ in entries], images, data['clip'])
                return len(entries)
        self.set_references([k for k, _ in entries], images)
        if cache and entries:
            np.savez(cache_file, stamp=json.dumps(stamp), clip=self.clip)
        return len(entries)

    def scores(self, embeddings, hists):
        """Per crop: {key: best score over that key's references}."""
        out = []
        if not len(self.keys):
            return [{} for _ in hists]
        combined = embeddings @ self.clip.T + .5 * np.minimum(self.hsv[None], np.asarray(hists)[:, None]).sum(2)
        keys = np.array(self.keys)
        for row in combined:
            out.append({k: float(row[keys == k].max()) for k in set(self.keys)})
        return out

    def identify(self, images, allowed=None):
        """[dict(key or None, score, margin, scores)] per crop. key None: not confidently any one object."""
        if not images:
            return []
        per = self.scores(self.embed(images), [hsv_hist(i) for i in images])
        return [decide(s, allowed) for s in per]


def decide(scores, allowed=None, min_score=None, min_margin=None):
    min_score = MIN_SCORE if min_score is None else min_score
    min_margin = MIN_MARGIN if min_margin is None else min_margin
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    if not ranked:
        return dict(key=None, score=0., margin=0., scores=scores, why='no references')
    best, score = ranked[0]
    margin = score - (ranked[1][1] if len(ranked) > 1 else 0.)
    if best == OTHER:
        return dict(key=None, score=score, margin=margin, scores=scores, why='looks like a non-target object')
    if allowed is not None and best not in allowed:
        return dict(key=None, score=score, margin=margin, scores=scores, best=best,
                    why=f'looks like {OBJECTS[best].name}')
    if score < min_score or margin < min_margin:
        return dict(key=None, score=score, margin=margin, scores=scores, best=best,
                    why=f'unsure ({OBJECTS[best].name}? score {score:.2f}, margin {margin:.2f})')
    return dict(key=best, score=score, margin=margin, scores=scores, why='')


class PlanarMatcher:
    """SIFT + RANSAC homography against registered photos of flat printed objects (the card box)."""
    def __init__(self, min_inliers=SIFT_MIN_INLIERS, max_edge=720, max_refs=8):
        import cv2
        self.cv2 = cv2
        self.sift = cv2.SIFT_create(nfeatures=1500)
        self.matcher = cv2.BFMatcher(cv2.NORM_L2)
        self.min_inliers, self.max_edge, self.max_refs = min_inliers, max_edge, max_refs
        self.refs = []  # (key, keypoint xy array, descriptors, (w, h))

    def set_references(self, keys, images):
        self.refs = []
        planar = [(k, i) for k, i in zip(keys, images) if k in OBJECTS and OBJECTS[k].planar]
        step = max(1., len(planar) / self.max_refs)  # a spread of views keeps matching fast
        for key, image in [planar[int(i * step)] for i in range(min(self.max_refs, len(planar)))]:
            gray = self._gray(image, 400)
            points, desc = self.sift.detectAndCompute(gray, None)
            if desc is not None and len(points) >= self.min_inliers:
                self.refs.append((key, np.float32([p.pt for p in points]), desc, gray.shape[::-1]))

    def _gray(self, image, edge):
        rgb = np.asarray(image.convert('RGB'))
        scale = min(1., edge / max(rgb.shape[:2]))
        gray = self.cv2.cvtColor(rgb, self.cv2.COLOR_RGB2GRAY)
        if scale < 1:
            gray = self.cv2.resize(gray, None, fx=scale, fy=scale, interpolation=self.cv2.INTER_AREA)
        return gray

    def find(self, image, keys=None):
        """[dict(key, box normalized, inliers)] best match per key on this image."""
        cv2 = self.cv2
        refs = [r for r in self.refs if keys is None or r[0] in keys]
        if not refs:
            return []
        gray = self._gray(image, self.max_edge)
        h, w = gray.shape
        points, desc = self.sift.detectAndCompute(gray, None)
        if desc is None or len(points) < self.min_inliers:
            return []
        frame_xy = np.float32([p.pt for p in points])
        best = {}
        for key, ref_xy, ref_desc, (rw, rh) in refs:
            pairs = [m[0] for m in self.matcher.knnMatch(ref_desc, desc, k=2)
                     if len(m) == 2 and m[0].distance < .75 * m[1].distance]
            if len(pairs) < self.min_inliers:
                continue
            src = ref_xy[[m.queryIdx for m in pairs]]
            dst = frame_xy[[m.trainIdx for m in pairs]]
            H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.)
            if H is None or mask is None or int(mask.sum()) < self.min_inliers:
                continue
            quad = cv2.perspectiveTransform(np.float32([[0, 0], [rw, 0], [rw, rh], [0, rh]])[None], H)[0]
            if not cv2.isContourConvex(quad.astype(np.int32)):
                continue
            area = cv2.contourArea(quad)
            if not (.0008 * w * h <= area <= .5 * w * h):
                continue
            (x0, y0), (x1, y1) = quad.min(0), quad.max(0)
            box = (max(0., x0 / w), max(0., y0 / h), min(1., x1 / w), min(1., y1 / h))
            inliers = int(mask.sum())
            if key not in best or inliers > best[key]['inliers']:
                best[key] = dict(key=key, box=box, inliers=inliers)
        return list(best.values())


def _iou(a, b):
    inter = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.


def _contained(a, b):
    inter = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return inter / smaller if smaller > 0 else 0.


# ---- recognizer used by the live tool --------------------------------------------------------------------------
class DemoRecognizer:
    """Loads and warms everything at launch (background); answers requests on the request's own frame."""
    def __init__(self, detector, root=STORE, background=True, identifier=None, planar=None, store=None):
        self.detector = detector
        self.store = store or ReferenceStore(root)
        self.identifier, self.planar = identifier, planar
        self.ready = threading.Event()
        self.error = ''
        self.load_s = None
        self.references = 0
        if identifier is not None and planar is not None:
            # Prepared by the caller (evaluation with held-out references).
            self.references, self.load_s = len(identifier.keys), 0.
            self.ready.set()
        elif background:
            threading.Thread(target=self._load, daemon=True, name='r3-demo-load').start()
        else:
            self._load()

    def _load(self):
        started = time.perf_counter()
        try:
            if self.identifier is None:
                self.identifier = Identifier()
            self.references = self.identifier.load_store(self.store)
            if self.references == 0:
                raise RuntimeError('no reference photos; run: python -m r3_vision.demo_objects harvest')
            if self.planar is None:
                self.planar = PlanarMatcher()
            entries = self.store.entries()
            self.planar.set_references([k for k, _ in entries], [Image.open(p) for _, p in entries])
            if self.detector is not None:
                self.detector.ready.wait(120)
                if getattr(self.detector, 'model', None) is not None:
                    # Warm the fixed vocabulary, the CLIP image path and SIFT once before the first request.
                    warm = Image.new('RGB', (640, 480), (128, 128, 128))
                    self.detector.detect(warm, VOCABULARY)
            self.identifier.identify([Image.new('RGB', (96, 96))])
            self.load_s = time.perf_counter() - started
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'[:200]
        finally:
            self.ready.set()

    @property
    def status(self):
        if not self.ready.is_set():
            return 'LOADING'
        if self.error:
            return f'UNAVAILABLE ({self.error})'
        return f'READY ({self.references} reference photos, {self.load_s:.1f}s warm-up)'

    def proposals(self, image, keys):
        """Candidate boxes (normalized) on `image` for the requested objects: detector classes + SIFT."""
        from .visual_tracker import working_image
        timing = {}
        small = working_image(image, 640)
        wanted = set().union(*(OBJECTS[k].classes for k in keys))
        found = []
        if self.detector is not None and getattr(self.detector, 'model', None) is not None:
            started = time.perf_counter()
            detections, _ = self.detector.detect(small, VOCABULARY)
            timing['detect'] = (time.perf_counter() - started) * 1000
            for d in detections or []:
                if set(d['names']) & wanted:
                    x0, y0, x1, y1 = d['box']
                    found.append(dict(box=(x0 / small.width, y0 / small.height, x1 / small.width, y1 / small.height),
                                      names=list(d['names']), score=d['score'], source='YOLO'))
        planar_keys = [k for k in keys if OBJECTS[k].planar]
        if planar_keys and self.planar is not None:
            started = time.perf_counter()
            for m in self.planar.find(image, planar_keys):
                same = [f for f in found if _iou(f['box'], m['box']) > .5]
                if same:
                    same[0].update(sift=m)
                else:
                    found.append(dict(box=m['box'], names=['sift ' + m['key']], score=0., source='SIFT', sift=m))
            timing['sift'] = (time.perf_counter() - started) * 1000
        return found, timing

    def choose(self, job):
        """dict(kind 'LOCAL' | 'AMBIGUOUS' | None, key, box, candidates, reason, timing). None: ask Gemini."""
        parsed = parse_request(job.query)
        if parsed is None:
            return dict(kind=None, reason='not one of the six demo objects', timing={}, candidates=[], key=None)
        keys, description = parsed
        if not self.ready.is_set() or self.error:
            return dict(kind=None, reason=f'demo recognizer {self.status}', timing={}, candidates=[], key=None)
        started = time.perf_counter()
        found, timing = self.analyse(job.image, keys)
        out = resolve(keys, description, found)
        timing['total'] = (time.perf_counter() - started) * 1000
        out['timing'] = timing
        return out

    def describe(self, image):
        """Instant local summary of the six objects in view: ([(key, position)], timing ms). No Gemini."""
        if not self.ready.is_set() or self.error:
            return [], {}
        started = time.perf_counter()
        found, timing = self.analyse(image, list(OBJECTS))
        items = summarize(found)
        timing['total'] = (time.perf_counter() - started) * 1000
        return items, timing

    def analyse(self, image, keys):
        """Proposals on `image` with each one's raw appearance scores (decided later by resolve)."""
        found, timing = self.proposals(image, keys)
        started = time.perf_counter()
        crops = [crop(image, f['box']) for f in found]
        if crops:
            per = self.identifier.scores(self.identifier.embed(crops), [hsv_hist(c) for c in crops])
            for f, scores in zip(found, per):
                f['scores'] = scores
        timing['identify'] = (time.perf_counter() - started) * 1000
        return found, timing


def position(box):
    """Camera-relative left / center / right from the box centre in the image."""
    x = (box[0] + box[2]) / 2
    return 'left' if x < .4 else 'right' if x > .6 else 'center'


def summarize(found, min_score=None, min_margin=None):
    """Confidently identified demo objects, left to right: [(key, position)]. Unsure proposals are left out."""
    resolve(tuple(OBJECTS), 'all', found, min_score, min_margin)  # fills f['identity']
    kept = []
    for f in sorted(found, key=lambda f: -_area(f['box'])):
        key = f['identity']['key']
        if key is not None and not any(k == key and _contained(f['box'], b) > .6 for k, b in kept):
            kept.append((key, f['box']))
    if len({k for k, _ in kept}) != len(kept):
        # Two separate boxes with the same identity: say it once rather than invent a count.
        kept = [(k, b) for i, (k, b) in enumerate(kept) if k not in [k2 for k2, _ in kept[:i]]]
    return [(k, position(b)) for k, b in sorted(kept, key=lambda kb: (kb[1][0] + kb[1][2]) / 2)]


def _area(b):
    return (b[2] - b[0]) * (b[3] - b[1])


def resolve(keys, description, found, min_score=None, min_margin=None):
    """Request decision from analysed proposals. Pure: no models."""
    min_score = MIN_SCORE if min_score is None else min_score
    for f in found:
        v = decide(f.get('scores', {}), None, min_score, min_margin)
        sift = f.get('sift')
        if sift and v['key'] is None and v.get('best') == sift['key'] and v['score'] >= min_score - .1:
            # A planar homography with many inliers is strong evidence of the same printed object.
            v = dict(v, key=sift['key'], why=f"SIFT {sift['inliers']} inliers")
        f['identity'] = v
    matches = [f for f in found if f['identity']['key'] in keys]
    # Overlapping boxes on one physical object (e.g. 'can' and 'cup' halves) are one candidate, not two.
    unique = []
    for f in sorted(matches, key=lambda f: -(f['box'][2] - f['box'][0]) * (f['box'][3] - f['box'][1])):
        if not any(_contained(f['box'], u['box']) > .6 and f['identity']['key'] == u['identity']['key']
                   for u in unique):
            unique.append(f)
    # A separate proposal that probably is the requested object but is not confidently identified could be a second
    # one: never pick between them locally.
    unsure = [f for f in found if f['identity']['key'] is None and f['identity'].get('best') in keys
              and not any(_contained(f['box'], u['box']) > .6 for u in unique)]
    out = dict(kind=None, key=None, box=None, candidates=found, description=description)
    if len(unique) == 1 and not unsure:
        f = unique[0]
        out.update(kind='LOCAL', key=f['identity']['key'], box=f['box'],
                   reason=(f"{OBJECTS[f['identity']['key']].name} matched to registered photos "
                           f"(score {f['identity']['score']:.2f}, margin {f['identity']['margin']:.2f}"
                           + (f", SIFT {f['sift']['inliers']} inliers" if f.get('sift') else '') + ')'))
        return out
    if len(unique) > 1:
        names = sorted({OBJECTS[f['identity']['key']].name for f in unique})
        if len(keys) > 1 and len(names) == len(unique) and not unsure:
            out.update(kind='AMBIGUOUS', labels=names,
                       reason=f"{len(unique)} matches for '{description}': {', '.join(names)}")
            return out
        out['reason'] = f"{len(unique)} objects look like {' / '.join(names)}; Gemini must compare them"
        return out
    if unsure:
        out['reason'] = f"not sure locally: {unsure[0]['identity']['why']}"
        return out
    out['reason'] = f'no local match among {len(found)} proposals'
    return out


# ---- CLI -------------------------------------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--store', type=Path, default=STORE)
    sub = p.add_subparsers(dest='command', required=True)
    h = sub.add_parser('harvest', help='Reference crops from recorded PathOn captures (saved Gemini boxes)')
    h.add_argument('--per-object', type=int, default=24)
    h.add_argument('--negatives', type=int, default=60)
    r = sub.add_parser('register', help='Add one reference photo of a demo object (or "other")')
    r.add_argument('object', choices=sorted(OBJECTS) + [OTHER])
    r.add_argument('image', type=Path)
    r.add_argument('--box', help='x0,y0,x1,y1 in pixels of the photo; default: the whole photo')
    sub.add_parser('list', help='Reference photo counts')
    args = p.parse_args(argv)
    store = ReferenceStore(args.store)
    if args.command == 'harvest':
        print(json.dumps(harvest(store, per_key=args.per_object, negatives=args.negatives)))
    elif args.command == 'register':
        box = tuple(float(v) for v in args.box.split(',')) if args.box else None
        print(store.add(args.object, Image.open(args.image), box))
    print(json.dumps(store.counts()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
