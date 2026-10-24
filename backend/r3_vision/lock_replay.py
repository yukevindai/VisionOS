"""Offline lock evaluation: real recorded frames with SIMULATED camera motion. No Gemini calls.

Scores a tracker against the known transformed target box for pan, zoom, rotation, partial cover,
pointing away and return. This is evidence for tracker behavior, not a live-camera acceptance test.
"""
from __future__ import annotations

import argparse
import glob
import json
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

DEFAULT_FRAMES = Path(__file__).resolve().parents[3] / 'DeskGuide-Laptop/logs/comparison-live-hand-before/baseline'
DEFAULT_REGION = {'x': .451, 'y': .208, 'width': .236, 'height': .333}


def trajectory():
    steps = []
    for t in np.linspace(0, 1, 16):
        steps.append(('pan', 150 * np.sin(2 * np.pi * t), 0, 1, 0, None))
    for s in list(np.linspace(1, 1.45, 8)) + list(np.linspace(1.45, .7, 12)) + list(np.linspace(.7, 1, 6)):
        steps.append(('zoom', 0, 0, float(s), 0, None))
    for a in list(np.linspace(0, 8, 5)) + list(np.linspace(8, -8, 8)) + list(np.linspace(-8, 0, 5)):
        steps.append(('rotate', 0, 0, 1, float(a), None))
    steps += [('partial cover', 0, 0, 1, 0, 'partial')] * 6
    steps += [('full cover', 0, 0, 1, 0, 'full')] * 6
    steps += [('point away', 700, 0, 1, 0, None)] * 10
    steps += [('return', 0, 0, 1, 0, None)] * 10
    return steps


def warp(image, dx, dy, scale, angle, box):
    h, w = image.shape[:2]
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    A = cv2.getRotationMatrix2D((cx, cy), angle, scale)
    A[:, 2] += (dx, dy)
    out = cv2.warpAffine(image, A, (w, h), borderMode=cv2.BORDER_CONSTANT, borderValue=(40, 40, 40))
    x, y, bw, bh = box
    corners = np.float32([[x, y], [x + bw, y], [x + bw, y + bh], [x, y + bh]])
    moved = cv2.transform(corners[None], A)[0]
    return out, moved


def iou(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    inter = max(0, min(ax1, bx1) - max(ax0, bx0)) * max(0, min(ay1, by1) - max(ay0, by0))
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / union if union > 0 else 0.


def run(factory, frames_dir=DEFAULT_FRAMES, region=DEFAULT_REGION, dt=1 / 15, save=None):
    files = sorted(glob.glob(str(Path(frames_dir) / '*.npz')))
    frames = [np.load(f)['rgb'] for f in files]
    h, w = frames[0].shape[:2]
    box = (region['x'] * w, region['y'] * h, region['width'] * w, region['height'] * h)
    clock = [100.]
    tracker = factory(Image.fromarray(frames[0]), region, lambda: clock[0])
    rows, timings = [], []
    for i, (phase, dx, dy, scale, angle, cover) in enumerate(trajectory()):
        clock[0] += dt
        image, corners = warp(frames[(i + 1) % len(frames)], dx, dy, scale, angle, box)
        truth = (*corners.min(0), *corners.max(0))
        visible = truth[0] >= 0 and truth[1] >= 0 and truth[2] <= w and truth[3] <= h
        if cover:
            x0, y0, x1, y1 = map(int, truth)
            bottom = y1 if cover == 'full' else (y0 + y1) // 2
            image = image.copy()
            image[max(0, y0 - 10):bottom + 10, max(0, x0 - 10):x1 + 10] = (190, 140, 110)
            visible = visible and cover != 'full'
        started = time.perf_counter()
        match = tracker.locate(Image.fromarray(image))
        timings.append((time.perf_counter() - started) * 1000)
        shown = match.polygon is not None
        overlap = iou((*match.polygon.min(0), *match.polygon.max(0)), truth) if shown else None
        rows.append(dict(frame=i, phase=phase, state=getattr(match, 'state', 'BOX' if shown else 'NO BOX'),
                         targetVisible=bool(visible), boxShown=shown,
                         iou=None if overlap is None else round(float(overlap), 3), reason=match.reason))
        if save is not None:
            save.mkdir(parents=True, exist_ok=True)
            view = image.copy()
            if shown:
                cv2.polylines(view, [np.int32(match.polygon)], True, (0, 255, 0), 4)
            cv2.putText(view, f'{i} {phase} {rows[-1]["state"]}', (10, 30), 0, .8, (255, 0, 0), 2)
            Image.fromarray(view).save(save / f'{i:03d}.jpg')
    return rows, timings


def summarize(name, rows, timings):
    phases = {}
    for r in rows:
        p = phases.setdefault(r['phase'], dict(frames=0, shown=0, correct=0, wrong=0))
        p['frames'] += 1
        p['shown'] += r['boxShown']
        p['correct'] += bool(r['boxShown'] and r['iou'] is not None and r['iou'] >= .5)
        p['wrong'] += bool(r['boxShown'] and (not r['targetVisible'] or (r['iou'] or 0) < .3))
    measured = np.array(timings[1:])
    return dict(tracker=name, phases=phases,
                boxesOnHiddenTarget=sum(r['boxShown'] and not r['targetVisible'] for r in rows),
                flickerTransitions=sum(a['boxShown'] != b['boxShown'] for a, b in zip(rows, rows[1:])
                                       if a['targetVisible'] and b['targetVisible']),
                msMean=round(float(measured.mean()), 1), msP95=round(float(np.percentile(measured, 95)), 1))


def factories():
    from .hybrid_tracker import HybridTracker
    from .object_lock import ObjectLockTracker

    def hybrid(reference, region, clock):
        tracker = HybridTracker(reference, region)
        tracker.seed_reference()
        return tracker

    return {'lock': lambda reference, region, clock: ObjectLockTracker(reference, region, clock=clock),
            'vit-hybrid (previous)': hybrid}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--frames', type=Path, default=DEFAULT_FRAMES)
    p.add_argument('--save', type=Path)
    a = p.parse_args(argv)
    for name, factory in factories().items():
        rows, timings = run(factory, a.frames, save=a.save / name.split()[0] if a.save else None)
        print(json.dumps(summarize(name, rows, timings), indent=1))


if __name__ == '__main__':
    main()
