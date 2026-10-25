"""Local detector benchmark on saved PathOn captures. Reference boxes are Gemini's saved boxes (checked visually).

Runs YOLOE-26n prompt-free, YOLO-World v2 small with text classes, and the existing YOLO11s COCO model.
No Gemini calls: Gemini-only latency comes from the saved results. Outputs JSON and an annotated contact sheet.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path

os.environ.setdefault('YOLO_AUTOINSTALL', 'false')

import numpy as np
import psutil
from PIL import Image, ImageDraw

BACKEND = Path(__file__).resolve().parents[1]
CAPTURES = BACKEND / 'recordings/r3-tracking'
MODELS = BACKEND / '.runtime/models'
COCO = BACKEND.parent / 'ios/Packages/Perception/Prototypes/DeskGuide-Laptop/models/yolo11s.pt'

CASES = [
    ('Red Bull can', ['red bull']),
    ('Pepsi can', ['pepsi']),
    ('bagel', ['bagel']),
    ('black tumbler', ['owala', 'water bottle', 'black bottle', 'tumbler']),
    ('cup', ['cup']),
    ('banana', []),
]
WORLD_CLASSES = ['can', 'bagel', 'cup', 'bottle', 'tumbler', 'banana', 'laptop', 'keyboard', 'plate']


def iou(a, b):
    inter = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.


def captures(limit):
    frames = []
    for result_path in sorted(CAPTURES.glob('track-*/result.json'), key=os.path.getmtime, reverse=True):
        result = json.loads(result_path.read_text(encoding='utf-8'))
        controls = (result.get('screenModel') or {}).get('controls') or []
        if not result.get('provider', {}).get('ok') or not controls:
            continue
        labels = ' '.join(c.get('label', '').lower() for c in controls)
        if not any(word in labels for word in ('red bull', 'pepsi', 'bagel', 'owala', 'cup')):
            continue
        image = Image.open(result_path.with_name('captured.png')).convert('RGB')
        frames.append((result_path.parent.name[6:14], image, controls, result.get('timingMs', {}).get('provider')))
        if len(frames) >= limit:
            break
    return frames


def references(controls, keys, size):
    w, h = size
    out = []
    for c in controls:
        label = c.get('label', '').lower()
        region = c.get('region')
        if region and any(k in label for k in keys):
            out.append((label, (region['x'] * w, region['y'] * h, (region['x'] + region['width']) * w,
                                (region['y'] + region['height']) * h)))
    return out


class Detector:
    def __init__(self, name, path, classes=None, conf=.25):
        from ultralytics import YOLO
        process = psutil.Process()
        before = process.memory_info().rss
        started = time.perf_counter()
        self.model = YOLO(str(path))
        if classes:
            self.model.set_classes(classes)
        self.model.predict(np.zeros((480, 640, 3), np.uint8), imgsz=640, conf=conf, verbose=False, device='cpu')
        self.cold_s = time.perf_counter() - started
        self.rss_mb = (process.memory_info().rss - before) / 2**20
        self.name, self.conf = name, conf

    def detect(self, image):
        started = time.perf_counter()
        result = self.model.predict(np.array(image)[..., ::-1], imgsz=640, conf=self.conf, verbose=False,
                                    device='cpu')[0]
        ms = (time.perf_counter() - started) * 1000
        boxes = []
        if result.boxes is not None:
            for xyxy, score, cls in zip(result.boxes.xyxy.cpu().numpy(), result.boxes.conf.cpu().numpy(),
                                        result.boxes.cls.cpu().numpy()):
                boxes.append((self.model.names[int(cls)], float(score), tuple(map(float, xyxy))))
        return boxes, ms


def evaluate(detector, frames):
    rows, latencies = [], []
    for frame_id, image, controls, _ in frames:
        boxes, ms = detector.detect(image)
        latencies.append(ms)
        for query, keys in CASES:
            refs = references(controls, keys, image.size)
            best = max((iou(b[2], r[1]), b[0], b[1]) for b in boxes for r in refs) if refs and boxes else (0., None, None)
            words = set(query.lower().split())
            named = [b for b in boxes if words & set(b[0].lower().split())]
            rows.append(dict(frame=frame_id, query=query, present=bool(refs), referenceCount=len(refs),
                             localized=best[0] >= .5, bestIoU=round(best[0], 2), bestClass=best[1],
                             classMatches=len(named), classMatchCorrect=sum(
                                 1 for b in named if refs and max(iou(b[2], r[1]) for r in refs) >= .5)))
    return rows, latencies


def summary(name, detector, rows, latencies):
    out = dict(detector=name, coldInitS=round(detector.cold_s, 2), rssIncreaseMb=round(detector.rss_mb),
               warmMsMedian=round(statistics.median(latencies[1:] or latencies), 1),
               warmMsMax=round(max(latencies[1:] or latencies), 1), cases={})
    for query, _ in CASES:
        r = [x for x in rows if x['query'] == query]
        present = [x for x in r if x['present']]
        out['cases'][query] = dict(
            framesWithObject=len(present),
            localizedIoU50=sum(x['localized'] for x in present),
            correctClassAndBox=sum(x['classMatchCorrect'] > 0 for x in present),
            falseClassMatches=sum(x['classMatches'] - x['classMatchCorrect'] for x in r),
            absentFrames=len(r) - len(present))
    return out


def sheet(detectors, frames, path):
    tiles = []
    for frame_id, image, controls, _ in frames[:4]:
        for detector in detectors:
            boxes, _ = detector.detect(image)
            view = image.copy()
            draw = ImageDraw.Draw(view)
            for name, score, (a, b, c, d) in boxes:
                draw.rectangle([a, b, c, d], outline='lime', width=6)
                draw.text((a + 4, b + 4), f'{name} {score:.2f}', fill='yellow')
            draw.text((12, 12), f'{detector.name} | {frame_id}', fill='red')
            view.thumbnail((480, 360))
            tiles.append(view)
    columns = len(detectors)
    canvas = Image.new('RGB', (480 * columns, 360 * ((len(tiles) + columns - 1) // columns)))
    for i, tile in enumerate(tiles):
        canvas.paste(tile, (480 * (i % columns), 360 * (i // columns)))
    canvas.save(path)


HYBRID_REQUESTS = [
    ('Find the Red Bull can', ['red bull']),
    ('Find the black Owala tumbler', ['owala', 'water bottle', 'black bottle']),
    ('Find the bagel', ['bagel']),
    ('Find the cup beside the laptop', ['cup']),
    ('Find the laptop', ['laptop']),
    ('Find the banana', []),
]


def hybrid_decisions(frames, out):
    """HYBRID decision per request with the real local detector; no Gemini call. Lock timing is local only."""
    from .hybrid import local_choice, phrases
    from .local_detector import LocalDetector
    from .object_lock import ObjectLockTracker
    from .hybrid import region
    from .visual_tracker import working_image
    detector = LocalDetector(background=False)
    rows = []
    for frame_id, image, controls, gemini_ms in frames:
        for request, keys in HYBRID_REQUESTS:
            started = time.perf_counter()
            candidates, local_ms = detector.detect(image, phrases(request))
            chosen, reason = local_choice(request, candidates)
            refs = references(controls, keys, image.size)
            correct = None
            lock_s = None
            if chosen is not None:
                correct = bool(refs) and max(iou(chosen['box'], r[1]) for r in refs) >= .5
                clock = [0.]
                tracker = ObjectLockTracker(image, region(chosen['box'], image.size), clock=lambda: clock[0])
                current = working_image(image, 640)
                for _ in range(3):
                    clock[0] += .07
                    if tracker.locate(current).state == 'LOCKED':
                        lock_s = time.perf_counter() - started
                        break
            rows.append(dict(frame=frame_id, request=request, objectPresent=bool(refs), candidates=len(candidates),
                             decision='LOCAL' if chosen is not None else 'GEMINI', reason=reason,
                             localChoiceCorrect=correct, localMs=round(local_ms), localLockS=lock_s and round(lock_s, 2),
                             savedGeminiOnlyS=gemini_ms and round(gemini_ms / 1000, 1)))
    (out / 'hybrid-decisions.json').write_text(json.dumps(rows, indent=1))
    return rows


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--frames', type=int, default=12)
    p.add_argument('--out', type=Path, default=BACKEND / '.runtime/detector-bench')
    p.add_argument('--skip-world', action='store_true')
    p.add_argument('--hybrid', action='store_true', help='Only report HYBRID decisions with the local detector')
    a = p.parse_args(argv)
    import torch
    torch.set_num_threads(max(1, (os.cpu_count() or 4) // 2))
    frames = captures(a.frames)
    a.out.mkdir(parents=True, exist_ok=True)
    if a.hybrid:
        print(json.dumps(hybrid_decisions(frames, a.out), indent=1))
        return
    gemini = [f[3] / 1000 for f in frames if f[3]]
    report = dict(frames=[f[0] for f in frames],
                  geminiOnlySavedLatencyS=dict(median=round(statistics.median(gemini), 1), min=round(min(gemini), 1),
                                               max=round(max(gemini), 1)) if gemini else None, detectors=[])
    detectors = [Detector('YOLOE-26n-seg-pf', MODELS / 'yoloe-26n-seg-pf.pt', conf=.15),
                 Detector('YOLO11s-COCO (existing)', COCO, conf=.2)]
    if not a.skip_world:
        detectors.insert(1, Detector('YOLO-World-v2-s', MODELS / 'yolov8s-worldv2.pt', WORLD_CLASSES, conf=.1))
    for detector in detectors:
        rows, latencies = evaluate(detector, frames)
        report['detectors'].append(summary(detector.name, detector, rows, latencies))
        (a.out / f'{detector.name.split()[0]}-rows.json').write_text(json.dumps(rows, indent=1))
    sheet(detectors, frames, a.out / 'comparison.jpg')
    (a.out / 'report.json').write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == '__main__':
    main()
