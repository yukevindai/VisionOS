"""Fingertip-to-target 3D offset from PathOn LiDAR, for the 2D preview. Display only; not contact detection.

Reuses the project's own DeskGuide-Laptop modules (MIT, ios/Packages/Perception/Prototypes/DeskGuide-Laptop):
stream.prepare for colour/depth/confidence/intrinsics alignment, geometry.surface for the target's front-surface
depth, and metric_hand.HandDepthFusion as an independent fingertip estimate check. A palm-fused fingertip is
explicitly ESTIMATED; direct tip support, agreement and calibration are required by the directional adapter.
Coordinates are camera-optical metres of the displayed (rotated) frame: x right, y down, z forward;
delta = target - fingertip. The target point is the visible surface point of the object nearest the fingertip
(LiDAR samples of the object's front surface, hand pixels excluded), backprojected with X=(u-cx)Z/fx, Y=(v-cy)Z/fy.
Confidence levels use the share of ARKit high-confidence (2) depth samples actually used.
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
    target_point: str = ''
    target_confidence: str = 'UNAVAILABLE'
    fingertip_confidence: str = 'UNAVAILABLE'
    tip_check: str = ''
    fingertip_estimated: bool = False
    depth_disagreement_m: float | None = None
    received_monotonic: float | None = None

    @property
    def measured(self):
        return self.status == 'LIDAR' and not self.fingertip_estimated

    def text(self):
        if self.delta_m is None:
            return f'3D: {self.detail}'
        if self.status == 'UNRELIABLE':
            return f'3D UNRELIABLE: {self.tip_check}; physical instructions suppressed'
        prefix = '3D LiDAR' if self.measured else '3D ≈ SIZE ESTIMATE (not LiDAR)'
        approx = '' if self.measured else '≈'
        parts = []
        for (axis, positive, negative), value in zip(AXES, self.delta_m):
            word = 'ALIGNED' if abs(value) < TOLERANCE_M else (positive if value > 0 else negative)
            parts.append(f'{axis} {word} {approx}{abs(value) * 100:.0f}cm')
        return f'{prefix}: {approx}{self.distance_m * 100:.1f} cm | ' + ' | '.join(parts) + ' | not contact detection'

    def record(self):
        return dict(status=self.status, fingertipM=self.fingertip_m, targetM=self.target_m, deltaM=self.delta_m,
                    distanceM=self.distance_m, fingertipSource=self.fingertip_source, detail=self.detail,
                    targetPoint=self.target_point, targetConfidence=self.target_confidence,
                    fingertipConfidence=self.fingertip_confidence, tipCheck=self.tip_check,
                    fingertipEstimated=self.fingertip_estimated, depthDisagreementM=self.depth_disagreement_m,
                    receivedMonotonic=self.received_monotonic)


def level(high_share):
    if high_share is None:
        return 'UNAVAILABLE'
    return 'HIGH' if high_share >= .7 else 'MEDIUM' if high_share >= .4 else 'LOW'


def nearest_surface(frame, depth, box, front_z, finger):
    """Visible front-surface LiDAR points of the object (hand already removed from depth); nearest to finger."""
    x0, y0, x1, y1 = box
    mx, my = .1 * (x1 - x0), .1 * (y1 - y0)
    a, b, c, d = int(x0 + mx), int(y0 + my), int(x1 - mx), int(y1 - my)
    region = depth[max(0, b):d:3, max(0, a):c:3]
    if region.size == 0:
        return None, None
    vs, us = np.mgrid[max(0, b):d:3, max(0, a):c:3]
    keep = np.isfinite(region) & (region > .12) & (np.abs(region - front_z) <= .04)
    if keep.sum() < 5:
        return None, None
    z, u, v = region[keep], us[keep], vs[keep]
    K = frame.K
    points = np.stack([(u - K[0, 2]) * z / K[0, 0], (v - K[1, 2]) * z / K[1, 1], z], axis=1)
    share = None
    if frame.confidence is not None:
        share = float(np.mean(frame.confidence[v, u] >= 2))
    if finger is None:
        return points.mean(axis=0), share
    return points[np.argmin(np.linalg.norm(points - np.asarray(finger, float), axis=1))], share


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
        target_mask = np.zeros_like(mask)
        cv2.fillConvexPoly(target_mask, np.asarray(polygon, np.int32), 1)
        depth[target_mask == 0] = 0.
        surface = self.geometry.surface(replace(frame, depth=depth), (x0, y0, x1, y1))
        measured, estimated, reason = self.fusion.resolve(frame, points, t)
        if surface is None:
            return Offset('UNAVAILABLE', detail='can depth unavailable (hidden by hand or no supported LiDAR)')
        if measured is not None:
            # Palm depth plus nominal bone geometry is still an estimated fingertip.
            finger, status, source = measured.xyz, 'ESTIMATED', measured.source
        elif estimated is not None:
            finger, status, source = estimated, 'ESTIMATED', 'hand size/shape estimate'
        else:
            finger, status, source = None, 'UNAVAILABLE', ''
        point, share = nearest_surface(frame, depth, (x0, y0, x1, y1), float(surface[1].xyz[2]), finger)
        target, kind = (point, 'nearest visible surface point') if point is not None else (surface[1].xyz, 'box centre on front surface')
        target_level = level(share)
        diagnostics = self.fusion.diagnostics or {}
        palm = [c for c in diagnostics.get('confidence_values') or [] if c >= 1]
        finger_level = level(float(np.mean(np.asarray(palm) >= 2)) if palm else None) if measured is not None else (
            'ESTIMATED' if status == 'ESTIMATED' else 'UNAVAILABLE')
        if finger is None:
            return Offset('UNAVAILABLE', target_m=tuple(map(float, target)), detail=f'fingertip depth unavailable ({reason})',
                          target_point=kind, target_confidence=target_level)
        tip = self.geometry.fingertip_depth(frame, points[8])
        disagreement = None if tip is None else abs(float(tip.xyz[2]) - float(finger[2]))
        check = ('no direct fingertip depth' if tip is None else
                 f'direct tip depth agrees ({disagreement * 100:.1f} cm)'
                 if disagreement <= .03 else
                 f'direct tip depth differs by {disagreement * 100:.1f} cm')
        estimated_tip = True
        if disagreement is not None and disagreement > .03:
            status = 'UNRELIABLE'
        elif tip is not None and measured is not None and frame.confidence is not None:
            # Use the direct, center-supported sample only after the independent check agrees.
            dh, dw = frame.depth.shape
            h, w = frame.color.shape[:2]
            u, v = np.floor(points[8] * [dw / w, dh / h]).astype(int)
            if frame.confidence[v, u] >= 2:
                finger, status, source = tip.xyz, 'LIDAR', tip.source
                finger_level, estimated_tip = 'HIGH', False
                point, share = nearest_surface(frame, depth, (x0, y0, x1, y1), float(surface[1].xyz[2]), finger)
                target, kind = (point, 'nearest visible surface point') if point is not None else (surface[1].xyz, kind)
                target_level = level(share)
        delta = np.asarray(target, float) - np.asarray(finger, float)
        if not np.isfinite(delta).all():
            return Offset('UNAVAILABLE', detail='invalid 3D geometry')
        return Offset(status, tuple(map(float, finger)), tuple(map(float, target)), tuple(map(float, delta)),
                      float(math.sqrt(float(delta @ delta))), source, reason, kind, target_level, finger_level, check,
                      estimated_tip, disagreement, t)
