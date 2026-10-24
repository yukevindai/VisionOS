from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .render import draw_hands
from .tracker import TRACKING, HandTracker


def recorded_frames(path: Path, video_fps: float):
    if path.is_dir():
        for index, file in enumerate(sorted(path.glob('*.npz'))):
            data = np.load(file)
            if 'color' in data.files:
                image = Image.fromarray(np.ascontiguousarray(data['color'][..., ::-1]))
            else:
                image = Image.fromarray(data['rgb'])
            stamp = float(data['timestamp']) if 'timestamp' in data.files else index / video_fps
            yield file.stem, image, stamp
        return
    import cv2
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise SystemExit('Cannot open the supplied video.')
    fps = capture.get(cv2.CAP_PROP_FPS) or video_fps
    index = 0
    try:
        while True:
            ok, bgr = capture.read()
            if not ok:
                return
            yield index, Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)), index / fps
            index += 1
    finally:
        capture.release()


def replay(path: Path, rotation: int = 0, output: Path | None = None, limit: int | None = None,
           video_fps: float = 30.) -> dict:
    tracker = HandTracker()
    timings, statuses, events = [], [], []
    try:
        started = time.perf_counter()
        for count, (frame_id, image, stamp) in enumerate(recorded_frames(path, video_fps)):
            if limit is not None and count >= limit:
                break
            if rotation:
                image = image.rotate(-rotation, expand=True)
            result = tracker.process(image, stamp, frame_id)
            timings.append(result.processing_ms)
            statuses.append(result.status)
            events += [dict(frame=str(frame_id), trackID=h.track_id, identity=h.identity)
                       for h in result.hands if h.identity != 'CONTINUED']
            if output is not None:
                output.mkdir(parents=True, exist_ok=True)
                draw_hands(image, result).save(output / f'{frame_id}.png')
        elapsed = time.perf_counter() - started
    finally:
        tracker.close()
    measured = np.array(timings[1:] or timings, float)
    return dict(
        source=str(path), rotation=rotation, frames=len(statuses),
        trackingFrames=statuses.count(TRACKING),
        identityEvents=events,
        processingMs=None if not len(measured) else dict(
            mean=float(measured.mean()), p95=float(np.percentile(measured, 95)), max=float(measured.max())),
        trackerFps=None if not len(measured) else float(1000 / measured.mean()),
        wallFpsIncludingDecode=float(len(statuses) / elapsed) if elapsed > 0 else None,
        note='First frame excluded from processing statistics (model warm-up). Laptop CPU, MediaPipe CPU delegate.',
    )


def main(argv=None):
    p = argparse.ArgumentParser(description='Replay recorded frames through the hand tracker.')
    p.add_argument('source', type=Path, help='Folder of .npz frames or a video file')
    p.add_argument('--rotation', type=int, choices=[0, 90, 180, 270], default=0)
    p.add_argument('--out', type=Path, help='Optional folder for rendered skeleton images')
    p.add_argument('--limit', type=int)
    a = p.parse_args(argv)
    print(json.dumps(replay(a.source, a.rotation, a.out, a.limit), indent=2))


if __name__ == '__main__':
    main()
