"""Camera-relative metric axis readout; never infer centimetres from pixel differences.

Reuses DeskGuide backprojection/surface measurements and Proprio's target-minus-hand
sign convention and aligned/release deadband. Calibration is an external physical test
record, not learned hand size, model confidence, or a user/body coordinate transform.
"""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import time

import numpy as np


@dataclass(frozen=True)
class Calibration:
    record: dict

    @classmethod
    def load(cls, path):
        if path is None:
            return None
        record = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(record, dict):
            raise ValueError('Calibration must be a physical validation record')
        return cls(record)

    def reason(self, packet, frame, host, port, utc=None, now=None):
        r, s = self.record, packet.stamp
        try:
            utc = time.time() if utc is None else utc
            if r.get('schema') != 1 or r.get('physicallyValidated') is not True or not r.get('validationId'):
                return 'physical calibration has not been validated'
            if not 0 <= utc - float(r['validatedAtUnixS']) <= 86400:
                return 'physical calibration expired (24 hour limit)'
            if r.get('coordinateSystem') != 'display_rotated_camera_optical' or r.get('mirrored') is not False:
                return 'camera axes or source mirroring unverified'
            if s.mirrored is True:
                return 'live source is mirrored'
            if (r['host'], r['port'], r['rotationClockwise'], tuple(r['rawSize']), tuple(r['imageSize'])) != (
                    host, port, s.rotation_clockwise, (s.raw_width, s.raw_height), (s.image_width, s.image_height)):
                return 'camera source, rotation or image dimensions changed'
            checks = r['axisChecks']
            if set(checks) != {'x', 'y', 'z'}:
                return 'physical checks required on all three axes'
            for check in checks.values():
                error = float(check['maxErrorM'])
                if check['positiveDirectionVerified'] is not True or check['samples'] < 3 or not 0 <= error <= .02:
                    return 'physical axis checks exceed the 2 cm acceptance limit'
            expected = np.asarray(r['intrinsics'], float)
            if (frame is None or expected.shape != (3, 3) or not np.isfinite(expected).all()
                    or expected[0, 0] <= 0 or expected[1, 1] <= 0 or abs(np.linalg.det(expected)) < 1e-9
                    or not np.allclose(expected, frame.K, rtol=.01, atol=.01)):
                return 'camera/depth intrinsics differ from the calibration record'
            if r.get('depthAlignmentVerified') is not True:
                return 'RGB/depth alignment not physically checked'
            try:
                self.evidence_deadline(packet, time.monotonic() if now is None else now)
            except (KeyError, TypeError, ValueError, OverflowError):
                return 'sensor clock mapping unavailable, expired or exposure older than 300 ms'
        except (KeyError, TypeError, ValueError, OverflowError):
            return 'incomplete or invalid calibration record'
        return None

    def evidence_deadline(self, packet, now):
        """Require an externally verified phone-to-desktop clock mapping; never derive one from arrival time."""
        s, c = packet.stamp, self.record['sensorClock']
        offset, uncertainty, verified, lifetime = (float(c[k]) for k in
            ('offsetToMonotonicS', 'uncertaintyS', 'verifiedAtMonotonicS', 'validForS'))
        values = (offset, uncertainty, verified, lifetime, s.sensor_timestamp)
        if (not all(v is not None and math.isfinite(v) for v in values) or not 0 <= uncertainty <= .03
                or not 0 < lifetime <= 60 or not 0 <= now - verified <= lifetime
                or c['connectionEpoch'] != s.connection_epoch):
            raise ValueError('sensor clock mapping missing, expired or mismatched')
        capture = s.sensor_timestamp + offset
        if not 0 <= now - capture or capture - s.received_monotonic_s > uncertainty:
            raise ValueError('sensor clock mapping is inconsistent')
        deadline = min(capture + .3 - uncertainty, s.received_monotonic_s + .3, verified + lifetime)
        if now > deadline:
            raise ValueError('sensor exposure evidence exceeds 300 ms including clock uncertainty')
        return deadline


@dataclass(frozen=True)
class AxisDistance:
    axis: str
    direction: str
    cm: float

    @property
    def text(self):
        prefix = 'REACH' if self.direction == 'FORWARD' else 'MOVE'
        return (f'{self.axis.upper()} ALIGNED: {self.cm:.1f} cm' if self.direction == 'ALIGNED'
                else f'{prefix} {self.direction}: {self.cm:.1f} cm')

    @property
    def speech(self):
        return f'{self.direction.capitalize()}, {self.cm:.0f} centimeters.'


@dataclass(frozen=True)
class DistanceReadout:
    status: str
    reason: str
    axes: tuple = ()
    estimated: bool = False
    delta_cm: tuple | None = None
    euclidean_cm: float | None = None
    valid_until_monotonic: float | None = None


class DirectionalDistance:
    def __init__(self, calibration=None, host=None, port=None, enter_m=.025, release_m=.05):
        if not 0 < enter_m < release_m:
            raise ValueError('Aligned/release thresholds must form a positive deadband')
        self.calibration, self.host, self.port = calibration, host, port
        self.enter_m, self.release_m = enter_m, release_m
        self.aligned = {}
        self.last_frame = None

    def update(self, offset, packet, frame, locked, hand_same_frame, now, utc=None):
        s = packet.stamp if packet is not None else None
        estimated = bool(offset is not None and (getattr(offset, 'fingertip_estimated', False)
                                                 or offset.status == 'ESTIMATED'))
        delta = None if offset is None or offset.delta_m is None else tuple(v * 100 for v in offset.delta_m)
        distance = None if offset is None or offset.distance_m is None else offset.distance_m * 100
        reason = None
        if s is None or not math.isfinite(now) or not 0 <= now - s.received_monotonic_s <= .3:
            reason = 'no fresh synchronized frame (300 ms limit)'
            delta = distance = None
        elif not locked or not hand_same_frame:
            reason = 'target or hand not locked in this frame'
            delta = distance = None
        elif (frame is None or frame.received != s.received_monotonic_s or offset is None
              or offset.received_monotonic != s.received_monotonic_s):
            reason = 'missing or mismatched RGB/hand/depth measurements'
            delta = distance = None
        elif offset.status == 'UNRELIABLE' or (offset.depth_disagreement_m is not None
                                              and (not math.isfinite(offset.depth_disagreement_m)
                                                   or not 0 <= offset.depth_disagreement_m <= .03)):
            reason = offset.tip_check or 'direct fingertip depth disagrees with hand geometry'
        elif (delta is None or len(delta) != 3 or not all(math.isfinite(v) for v in delta)
              or distance is None or not math.isfinite(distance) or distance < 0):
            reason = 'invalid metric geometry'
        elif estimated or not offset.measured:
            reason = 'fingertip depth ESTIMATED; physical instructions suppressed'
        elif offset.fingertip_confidence != 'HIGH' or offset.target_confidence != 'HIGH':
            reason = 'fingertip or target surface confidence insufficient'
        elif offset.depth_disagreement_m is None:
            reason = 'no independent fingertip-depth cross-check'
        elif self.calibration is None:
            reason = 'camera/depth calibration and mirroring not physically validated'
        else:
            reason = self.calibration.reason(packet, frame, self.host, self.port, utc, now)
        if reason:
            self.aligned.clear()
            self.last_frame = None
            return DistanceReadout('UNRELIABLE', reason, estimated=estimated, delta_cm=delta, euclidean_cm=distance)
        epoch = (s.stream_id, s.connection_epoch, s.rotation_clockwise, s.image_width, s.image_height)
        if self.last_frame is not None and self.last_frame != epoch:
            self.aligned.clear()
        self.last_frame = epoch
        axes = []
        for axis, value, positive, negative in zip('xyz', offset.delta_m, ('RIGHT', 'DOWN', 'FORWARD'), ('LEFT', 'UP', 'BACK')):
            threshold = self.release_m if self.aligned.get(axis, False) else self.enter_m
            self.aligned[axis] = abs(value) <= threshold
            direction = 'ALIGNED' if self.aligned[axis] else (positive if value > 0 else negative)
            axes.append(AxisDistance(axis, direction, abs(value) * 100))
        return DistanceReadout('VALIDATED CAMERA RELATIVE', 'camera-relative only; no contact inference', tuple(axes),
                               False, delta, distance, self.calibration.evidence_deadline(packet, now))
