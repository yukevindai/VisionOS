"""Fingertip-to-target 3D offset from PathOn LiDAR, for the 2D preview. Display only; not contact detection.

Reuses the project's own DeskGuide-Laptop modules (MIT, ios/Packages/Perception/Prototypes/DeskGuide-Laptop):
stream.prepare for colour/depth/confidence/intrinsics alignment, geometry.surface for the target's front-surface
depth, and metric_hand.HandDepthFusion for the LiDAR-checked fingertip. No Proprio source is used.
Coordinates are camera-optical metres: x right, y down, z forward; delta = target - fingertip.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import sys

import numpy as np

PROTOTYPE = Path(__file__).resolve().parents[2] / 'ios/Packages/Perception/Prototypes/DeskGuide-Laptop'
TOLERANCE_M = .025
AXES = (('SIDE', 'RIGHT', 'LEFT'), ('HEIGHT', 'DOWN', 'UP'), ('REACH', 'FORWARD', 'BACK'))


def _deskguide():
    if str(PROTOTYPE) not in sys.path:
        sys.path.insert(0, str(PROTOTYPE))
    from deskguide import geometry, metric_hand, stream
    return geometry, metric_hand, stream


@dataclass(frozen=True)
class Offset:
    status: str
    fingertip_m: tuple | None = None
    target_m: tuple | None = None
    delta_m: tuple | None = None
    distance_m: float | None = None
    fingertip_source: str = ''
    detail: str = ''

    @property
    def measured(self):
        return self.status == 'LIDAR'

    def text(self):
        if self.delta_m is None:
            return f'3D: {self.detail}'
        prefix = '3D LiDAR' if self.measured else '3D ≈ SIZE ESTIMATE (not LiDAR)'
        approx = '' if self.measured else '≈'
        parts = []
        for (axis, positive, negative), value in zip(AXES, self.delta_m):
            word = 'ALIGNED' if abs(value) < TOLERANCE_M else (positive if value > 0 else negative)
            parts.append(f'{axis} {word} {approx}{abs(value) * 100:.0f}cm')
        return f'{prefix}: {approx}{self.distance_m * 100:.1f} cm | ' + ' | '.join(parts) + ' | not contact detection'

    def record(self):
        return dict(status=self.status, fingertipM=self.fingertip_m, targetM=self.target_m, deltaM=self.delta_m,
                    distanceM=self.distance_m, fingertipSource=self.fingertip_source, detail=self.detail)


class SpatialOffset:
    def __init__(self):
        self.geometry, metric_hand, self.stream = _deskguide()
        self.fusion = metric_hand.HandDepthFusion()

    def reset(self):
        _, metric_hand, _ = _deskguide()
        self.fusion = metric_hand.HandDepthFusion()

    def aligned(self, rgbd, size, rotation):
        if rgbd is None or getattr(rgbd, 'depth', None) is None or getattr(rgbd, 'K', None) is None:
            return None
        frame = self.stream.prepare(rgbd, max(size), rotation)
        return frame if (frame.color.shape[1], frame.color.shape[0]) == tuple(size) else None

    def measure(self, frame, polygon, landmarks, t):
        if frame is None:
            return Offset('UNAVAILABLE', detail='no synchronized LiDAR depth for this frame')
        import cv2
        from dataclasses import replace
        points = np.asarray(landmarks, float)
        x0, y0 = np.asarray(polygon, float).min(0)
        x1, y1 = np.asarray(polygon, float).max(0)
        hull = cv2.convexHull(points.astype(np.float32))
        center = hull.reshape(-1, 2).mean(0)
        grown = np.int32(center + 1.25 * (hull.reshape(-1, 2) - center))
        depth = frame.depth.copy()
        mask = np.zeros(frame.color.shape[:2], np.uint8)
        cv2.fillConvexPoly(mask, grown, 1)
        if mask.shape != depth.shape:
            mask = cv2.resize(mask, (depth.shape[1], depth.shape[0]), interpolation=cv2.INTER_NEAREST)
        depth[mask > 0] = 0.
        surface = self.geometry.surface(replace(frame, depth=depth), (x0, y0, x1, y1))
        measured, estimated, reason = self.fusion.resolve(frame, points, t)
        if surface is None:
            return Offset('UNAVAILABLE', detail='can depth unavailable (hidden by hand or no supported LiDAR)')
        target = surface[1].xyz
        if measured is not None:
            finger, status, source = measured.xyz, 'LIDAR', measured.source
        elif estimated is not None:
            finger, status, source = estimated, 'ESTIMATED', 'hand size/shape estimate'
        else:
            return Offset('UNAVAILABLE', target_m=tuple(map(float, target)), detail=f'fingertip depth unavailable ({reason})')
        delta = np.asarray(target, float) - np.asarray(finger, float)
        if not np.isfinite(delta).all():
            return Offset('UNAVAILABLE', detail='invalid 3D geometry')
        return Offset(status, tuple(map(float, finger)), tuple(map(float, target)), tuple(map(float, delta)),
                      float(math.sqrt(float(delta @ delta))), source, reason)
