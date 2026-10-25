"""Live PathOn REC3D field check: what each received packet actually contains. No models, no Gemini.

    python -m r3_vision.depth_check --host 192.168.137.223 --port 8888 --seconds 5
"""
from __future__ import annotations

import argparse
import time

import numpy as np

from .spatial import _deskguide


def describe(frame):
    depth = frame.depth
    valid = depth[np.isfinite(depth) & (depth > 0)] if depth is not None else np.array([])
    lines = [f"RGB:         {'AVAILABLE' if frame.color is not None else 'UNAVAILABLE'}"
             + (f'  {frame.color.shape[1]}x{frame.color.shape[0]}' if frame.color is not None else ''),
             f"DEPTH:       {'AVAILABLE' if depth is not None and valid.size else 'UNAVAILABLE'}"
             + (f'  {depth.shape[1]}x{depth.shape[0]} {depth.dtype}, metres, valid {valid.size / depth.size:.0%}, '
                f'range {valid.min():.2f}-{valid.max():.2f} m, median {np.median(valid):.2f} m' if valid.size else '')]
    if frame.confidence is not None:
        values, counts = np.unique(frame.confidence, return_counts=True)
        share = ', '.join(f'{int(v)}:{c / counts.sum():.0%}' for v, c in zip(values, counts))
        lines.append(f'CONFIDENCE:  AVAILABLE  (0 low, 1 medium, 2 high) {share}')
    else:
        lines.append('CONFIDENCE:  UNAVAILABLE (REC3D v1 packet)')
    K = frame.K
    lines.append(f"INTRINSICS:  {'AVAILABLE' if K is not None else 'UNAVAILABLE'}"
                 + (f'  fx {K[0, 0]:.1f} fy {K[1, 1]:.1f} cx {K[0, 2]:.1f} cy {K[1, 2]:.1f} (RGB pixels)' if K is not None else ''))
    lines.append(f"CAMERA POSE: {'AVAILABLE' if frame.pose is not None else 'UNAVAILABLE (missing or invalid rotation)'}")
    lines.append(f'TIMESTAMP:   {frame.timestamp:.3f} s (sensor clock)   FRAME ID: {frame.id}   '
                 f'decode {frame.decode_ms:.0f} ms')
    return '\n'.join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--host', required=True)
    p.add_argument('--port', type=int, default=8888)
    p.add_argument('--seconds', type=float, default=5)
    a = p.parse_args(argv)
    _, _, stream = _deskguide()
    phone = stream.Phone(a.host, a.port)
    phone.start()
    seen, last = 0, None
    deadline = time.monotonic() + a.seconds
    try:
        while time.monotonic() < deadline:
            frame = phone.latest
            if frame is not None and frame is not last:
                seen += 1
                last = frame
                if seen == 1 or seen % 15 == 0:
                    print(f'--- packet {seen} ---\n{describe(frame)}', flush=True)
            time.sleep(.01)
    finally:
        phone.close()
    print(f'{seen} packets in {a.seconds:g} s ({seen / a.seconds:.1f} per second processed here); status: {phone.status}')
    return 0 if seen else 2


if __name__ == '__main__':
    raise SystemExit(main())
