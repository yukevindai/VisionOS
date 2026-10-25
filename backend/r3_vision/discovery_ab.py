"""Bounded, offline diagnostic; real local CV, SIMULATED video motion and Gemini.

Run using the existing YOLO/MediaPipe venv: python -m r3_vision.discovery_ab
No API credentials, network requests, audio, GUI or phone sensor data are used.
"""
import json
import statistics
import threading
import time
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import psutil
from PIL import Image

from . import allobjects_replay as replay
from .local_detector import LocalDetector, WEIGHTS
from .tracker_models import ensure_model


def percentile(values, p):
    return round(float(np.percentile(values, p)), 2) if values else None


def main():
    import torch
    cv2.setNumThreads(1)
    torch.set_num_threads(4)
    if not WEIGHTS.exists():
        raise SystemExit('Cached YOLO weights required; this diagnostic will not download them.')
    ensure_model(download=False)
    base = np.asarray(Image.open(replay.CAPTURE / 'captured.png').convert('RGB'))
    result = json.loads((replay.CAPTURE / 'result.json').read_text(encoding='utf-8'))
    output = Path(__file__).resolve().parents[1] / '.runtime/discovery-ab' / str(time.time_ns())
    output.mkdir(parents=True)
    reports = []
    process = psutil.Process()
    for mode in ('allobjects', 'hybrid'):
        detector_calls, samples = [], []
        print(f'SIMULATED {mode}: loading detector before measurement', flush=True)
        detector = LocalDetector(background=False)
        if detector.model is None:
            raise SystemExit(f'Detector unavailable: {detector.status}; no valid A/B measurement.')
        original = detector.detect
        def detect(*args, **kwargs):
            detector_calls.append(time.monotonic())
            return original(*args, **kwargs)
        detector.detect = detect

        def sample(worker, seconds):
            # Identical short windows for both modes, excluding initialization.
            start = time.monotonic()
            cpu0 = process.cpu_times()
            fps, duration, discovery_threads = [], [], set()
            while time.monotonic() - start < 3:
                time.sleep(.1)
                fps.append(worker.fps)
                if worker.process_ms is not None:
                    duration.append(worker.process_ms)
                discovery_threads.update(t.name for t in threading.enumerate()
                                         if t.name in ('r3-discovery', 'r3-inventory-verify', 'r3-yoloe-pf'))
            end, cpu1 = time.monotonic(), process.cpu_times()
            report = dict(start=start, end=end, fps=round(statistics.mean(fps), 2),
                          processMsMedian=percentile(duration, 50),
                          cpuPercentOneCore=round(100 * ((cpu1.user + cpu1.system) -
                                                       (cpu0.user + cpu0.system)) / (end - start), 1),
                          detectorCalls=sum(start <= t <= end for t in detector_calls),
                          discoveryThreads=sorted(discovery_threads), state=worker.session.state)
            samples.append(report)
            return report

        print(f'SIMULATED {mode}: initialization, idle sample, one request, lock sample', flush=True)
        with patch('r3_vision.local_detector.LocalDetector', return_value=detector), \
                patch.object(replay, 'sample', sample):
            report = replay.run_mode(mode, ['Find the Red Bull can'], base, result, 2., 3., output,
                                     True, 12., ['--discovery-threads', '4'])
        rows = []
        for path in (output / mode).glob('session-*.jsonl'):
            rows.extend(json.loads(line) for line in path.read_text().splitlines())
        for window in samples:
            age = [r['frameAgeMs'] for r in rows if window['start'] <= r['t'] <= window['end']]
            window.update(frameAgeMsMedian=percentile(age, 50), frameAgeMsP95=percentile(age, 95),
                          loggedFrames=len(age))
        report['samples'] = samples
        report['totalDetectorCalls'] = len(detector_calls)
        report['detectorReady'] = detector.model is not None
        report['idleGeminiCalls'] = [c for c in report['geminiCalls'] if samples[0]['start'] <= c['at'] <= samples[0]['end']]
        reports.append(report)
        (output / 'report.json').write_text(json.dumps(dict(simulated=True, reports=reports), indent=2))
        print(json.dumps(report, indent=2), flush=True)
    print(f'Report: {output / "report.json"}', flush=True)


if __name__ == '__main__':
    main()
