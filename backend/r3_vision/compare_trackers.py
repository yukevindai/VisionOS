"""Offline saved-capture comparison; no camera, credentials or API calls.

Pairwise acceptance is NOT semantic ground truth. The motion sequence is SIMULATED.
"""
import argparse
import json
from pathlib import Path
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .hybrid_tracker import HybridTracker
from .visual_tracker import ReferenceTracker


def run(captures, output):
    cv2.setNumThreads(1)
    records = []
    for directory in sorted(captures.iterdir()):
        path = directory/'result.json'
        if not path.exists():
            continue
        result = json.loads(path.read_text(encoding='utf-8'))
        target = result['selection'].get('target')
        if result['selection']['status'] == 'SELECTED' and target:
            with Image.open(directory/'captured.png') as source:
                records.append((directory.name, target, source.convert('RGB')))
    if not records:
        raise ValueError('No selected reference captures found')
    comparisons = []
    for name, target, image in records:
        for feature in ['orb', 'sift']:
            tracker = ReferenceTracker(image, target['imageRegion'], features=feature)
            for other, other_target, current in records:
                if other_target['label'] != target['label']:
                    continue
                started = time.perf_counter()
                match = tracker.locate(current)
                comparisons.append(dict(reference=name, current=other, method=feature,
                    accepted=match.polygon is not None, reason=match.reason, inliers=match.inliers,
                    correlation=match.correlation, elapsedMs=round((time.perf_counter()-started)*1000, 2)))
    name, target, source = records[0]
    tracker = HybridTracker(source, target['imageRegion'])
    tracker.seed_reference()
    image = tracker.reference.image
    motion = []
    annotated = None
    for shift in range(0, 42, 2):
        H = np.float32([[1, 0, shift], [0, 1, shift/2]])
        current = Image.fromarray(cv2.warpAffine(np.array(image), H, image.size))
        started = time.perf_counter()
        match = tracker.locate(current, recovery=False)
        elapsed = (time.perf_counter()-started)*1000
        expected = tracker.reference.corners+np.float32([shift, shift/2])
        error = float(np.max(np.linalg.norm(expected-match.polygon, axis=1))) if match.polygon is not None else None
        motion.append(dict(simulatedDx=shift, simulatedDy=shift/2, accepted=match.polygon is not None,
                           maxCornerErrorPx=error, elapsedMs=round(elapsed, 2)))
        if match.polygon is not None:
            annotated = current.copy()
            points = [tuple(map(float, p)) for p in match.polygon]
            ImageDraw.Draw(annotated).line(points+[points[0]], fill='red', width=3)
    summary = dict(testKind='OFFLINE_REAL_CAPTURE_WITH_SIMULATED_MOTION_NOT_LIVE',
        motionReference=name, semanticGroundTruthAvailable=False,
        pairwise={f: dict(accepted=sum(r['accepted'] for r in comparisons if r['method']==f),
                         attempts=sum(r['method']==f for r in comparisons)) for f in ['orb', 'sift']},
        native=dict(accepted=sum(r['accepted'] for r in motion), attempts=len(motion),
                    medianMsExcludingWarmup=round(float(np.median([r['elapsedMs'] for r in motion[1:]])), 2),
                    maxAcceptedCornerErrorPx=max((r['maxCornerErrorPx'] for r in motion if r['accepted']), default=None)),
        pairwiseDetails=comparisons, motionDetails=motion)
    output.mkdir(parents=True, exist_ok=True)
    (output/'comparison.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    if annotated is not None:
        ImageDraw.Draw(annotated).rectangle((0, 0, annotated.width, 28), fill='black')
        ImageDraw.Draw(annotated).text((8, 8), 'OFFLINE saved phone image + SIMULATED motion; NOT live validation', fill='white')
        annotated.save(output/'offline-motion-example.png')
    print(json.dumps({k:v for k,v in summary.items() if k not in ['pairwiseDetails', 'motionDetails']}, indent=2))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--captures', type=Path, default=Path(__file__).resolve().parents[1]/'recordings/r3-tracking')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1]/'.runtime/tracker-comparison')
    args = parser.parse_args()
    run(args.captures, args.output)
