from __future__ import annotations

import itertools
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

PROTOTYPE = Path(__file__).resolve().parents[2] / 'ios/Packages/Perception/Prototypes/DeskGuide-Laptop'

LANDMARK_NAMES = (
    'WRIST',
    'THUMB_CMC', 'THUMB_MCP', 'THUMB_IP', 'THUMB_TIP',
    'INDEX_MCP', 'INDEX_PIP', 'INDEX_DIP', 'INDEX_TIP',
    'MIDDLE_MCP', 'MIDDLE_PIP', 'MIDDLE_DIP', 'MIDDLE_TIP',
    'RING_MCP', 'RING_PIP', 'RING_DIP', 'RING_TIP',
    'PINKY_MCP', 'PINKY_PIP', 'PINKY_DIP', 'PINKY_TIP',
)
WRIST = 0
INDEX_TIP = 8
PALM = (0, 5, 9, 13, 17)
FINGERTIPS = {'THUMB': 4, 'INDEX': 8, 'MIDDLE': 12, 'RING': 16, 'PINKY': 20}
CONNECTIONS = ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
               (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16),
               (13, 17), (0, 17), (17, 18), (18, 19), (19, 20))
COORDINATE_SYSTEM = ('pixels of the supplied image; origin top-left; x right, y down; '
                     'normalized = pixels / (width, height)')

TRACKING = 'TRACKING'
NO_HAND = 'NO_HAND'
INVALID_INPUT = 'INVALID_INPUT'
STALE_INPUT = 'STALE_INPUT'


@dataclass(frozen=True)
class Hand:
    track_id: int
    identity: str
    mediapipe_handedness: str
    handedness_score: float
    landmarks_px: np.ndarray
    landmarks_norm: np.ndarray
    in_frame: np.ndarray

    @property
    def index_tip_px(self) -> np.ndarray:
        return self.landmarks_px[INDEX_TIP]

    @property
    def index_tip_norm(self) -> np.ndarray:
        return self.landmarks_norm[INDEX_TIP]

    @property
    def index_tip_in_frame(self) -> bool:
        return bool(self.in_frame[INDEX_TIP])

    @property
    def palm_center_px(self) -> np.ndarray:
        return self.landmarks_px[list(PALM)].mean(axis=0)

    @property
    def box_px(self) -> np.ndarray:
        return np.r_[self.landmarks_px.min(axis=0), self.landmarks_px.max(axis=0)]

    def to_dict(self) -> dict:
        return dict(
            trackID=self.track_id,
            identity=self.identity,
            mediapipeHandedness=self.mediapipe_handedness,
            handednessScore=self.handedness_score,
            indexTip=dict(px=self.index_tip_px.tolist(), norm=self.index_tip_norm.tolist(),
                          inFrame=self.index_tip_in_frame),
            palmCenterPx=self.palm_center_px.tolist(),
            boxPx=self.box_px.tolist(),
            landmarks=[dict(name=name, px=px.tolist(), norm=norm.tolist(), inFrame=bool(inside))
                       for name, px, norm, inside in zip(LANDMARK_NAMES, self.landmarks_px,
                                                         self.landmarks_norm, self.in_frame)],
        )


@dataclass(frozen=True)
class HandFrame:
    status: str
    frame_id: object
    capture_timestamp_s: float | None
    image_size: tuple[int, int] | None
    hands: tuple[Hand, ...]
    processing_ms: float
    detail: str = ''
    coordinate_system: str = COORDINATE_SYSTEM

    @property
    def primary(self) -> Hand | None:
        return min(self.hands, key=lambda hand: hand.track_id) if self.hands else None

    def to_dict(self) -> dict:
        return dict(
            status=self.status,
            frameID=self.frame_id,
            captureTimestampS=self.capture_timestamp_s,
            imageSize=None if self.image_size is None else list(self.image_size),
            coordinateSystem=self.coordinate_system,
            processingMs=self.processing_ms,
            detail=self.detail,
            primaryTrackID=None if self.primary is None else self.primary.track_id,
            hands=[hand.to_dict() for hand in self.hands],
        )


class MediaPipeHands:
    def __init__(self):
        if str(PROTOTYPE) not in sys.path:
            sys.path.insert(0, str(PROTOTYPE))
        from deskguide.hand_observations import HandDetector
        self.detector = HandDetector()

    def detect(self, rgb: np.ndarray, timestamp_ms: int) -> list[dict]:
        return self.detector.detect(np.ascontiguousarray(rgb[..., ::-1]), timestamp_ms=timestamp_ms)

    def close(self) -> None:
        self.detector.close()


def as_rgb(image, color_order: str = 'RGB') -> np.ndarray | None:
    if hasattr(image, 'convert') and hasattr(image, 'size'):
        array = np.asarray(image.convert('RGB'))
    else:
        array = np.asarray(image)
        if color_order not in ('RGB', 'BGR'):
            raise ValueError("color_order must be 'RGB' or 'BGR'")
        if array.ndim == 3 and array.shape[2] == 3 and color_order == 'BGR':
            array = array[..., ::-1]
    if array.ndim != 3 or array.shape[2] != 3 or array.dtype != np.uint8 or min(array.shape[:2]) < 16:
        return None
    return np.ascontiguousarray(array)


def upright_to_raw_norm(points, rotation: int) -> np.ndarray:
    p = np.asarray(points, float)
    x, y = p[..., 0], p[..., 1]
    mapped = {0: (x, y), 90: (y, 1 - x), 180: (1 - x, 1 - y), 270: (1 - y, x)}[rotation]
    return np.stack(mapped, axis=-1)


def raw_to_upright_norm(points, rotation: int) -> np.ndarray:
    p = np.asarray(points, float)
    x, y = p[..., 0], p[..., 1]
    mapped = {0: (x, y), 90: (1 - y, x), 180: (1 - x, 1 - y), 270: (y, 1 - x)}[rotation]
    return np.stack(mapped, axis=-1)


class HandTracker:
    def __init__(self, detector=None, identity_timeout_s: float = .5, max_wrist_jump: float = .25,
                 handedness_penalty: float = .1, ambiguity_margin: float = .05):
        self.detector = detector if detector is not None else MediaPipeHands()
        self.identity_timeout_s = identity_timeout_s
        self.max_wrist_jump = max_wrist_jump
        self.handedness_penalty = handedness_penalty
        self.ambiguity_margin = ambiguity_margin
        self.tracks: dict[int, tuple[np.ndarray, str, float]] = {}
        self.next_id = 1
        self.last_timestamp: float | None = None

    def reset(self) -> None:
        self.tracks.clear()
        self.last_timestamp = None

    def close(self) -> None:
        close = getattr(self.detector, 'close', None)
        if close:
            close()

    def process(self, image, capture_timestamp_s: float, frame_id=None, color_order: str = 'RGB') -> HandFrame:
        start = time.perf_counter()

        def empty(status, size=None, detail=''):
            return HandFrame(status, frame_id, capture_timestamp_s, size, (),
                             (time.perf_counter() - start) * 1000, detail)

        rgb = as_rgb(image, color_order)
        if rgb is None:
            return empty(INVALID_INPUT, detail='expected an RGB image (PIL or HxWx3 uint8, at least 16 px)')
        h, w = rgb.shape[:2]
        try:
            timestamp = float(capture_timestamp_s)
        except (TypeError, ValueError):
            timestamp = math.nan
        if not math.isfinite(timestamp):
            return empty(INVALID_INPUT, (w, h), 'capture timestamp must be a finite number of seconds')
        if self.last_timestamp is not None and timestamp <= self.last_timestamp:
            return empty(STALE_INPUT, (w, h), 'capture timestamp is not newer than the previous frame')
        self.last_timestamp = timestamp
        detections = []
        for found in self.detector.detect(rgb, int(round(timestamp * 1000))):
            points = np.asarray(found['points'], float)
            if points.shape == (21, 2) and np.isfinite(points).all():
                detections.append(found)
        hands = self._assign(detections, w, h, timestamp)
        self.tracks = {track_id: track for track_id, track in self.tracks.items()
                       if timestamp - track[2] <= self.identity_timeout_s}
        status = TRACKING if hands else NO_HAND
        return HandFrame(status, frame_id, timestamp, (w, h), tuple(sorted(hands, key=lambda hand: hand.track_id)),
                         (time.perf_counter() - start) * 1000)

    def _assign(self, detections, w, h, timestamp):
        scale = np.array([w, h], float)
        diagonal = math.hypot(w, h)
        live = [(track_id, track) for track_id, track in self.tracks.items()
                if timestamp - track[2] <= self.identity_timeout_s]

        def cost(found, track):
            distance = np.linalg.norm(np.asarray(found['points'], float)[WRIST] - track[0] * scale) / diagonal
            if distance > self.max_wrist_jump:
                return None
            return distance + (self.handedness_penalty if found.get('label') != track[1] else 0.)

        options = []
        slots = list(range(len(live))) + [None] * len(detections)
        for order in set(itertools.permutations(slots, len(detections))):
            total, valid = 0., True
            for found, slot in zip(detections, order):
                if slot is None:
                    total += self.max_wrist_jump + self.handedness_penalty
                    continue
                c = cost(found, live[slot][1])
                if c is None:
                    valid = False
                    break
                total += c
            if valid:
                options.append((total, order))
        options.sort(key=lambda item: item[0])
        best = options[0][1] if options else (None,) * len(detections)
        ambiguous = set()
        if len(options) > 1:
            for total, order in options[1:]:
                if total - options[0][0] >= self.ambiguity_margin:
                    break
                for index, (a, b) in enumerate(zip(best, order)):
                    if a != b:
                        ambiguous.add(index)
        hands = []
        for index, (found, slot) in enumerate(zip(detections, best)):
            points = np.asarray(found['points'], float)
            if slot is not None and index not in ambiguous:
                track_id, identity = live[slot][0], 'CONTINUED'
            else:
                if slot is not None:
                    self.tracks.pop(live[slot][0], None)
                track_id, identity = self.next_id, 'NEW_AMBIGUOUS' if index in ambiguous else 'NEW'
                self.next_id += 1
            self.tracks[track_id] = (points[WRIST] / scale, found.get('label'), timestamp)
            inside = (points[:, 0] >= 0) & (points[:, 0] < w) & (points[:, 1] >= 0) & (points[:, 1] < h)
            hands.append(Hand(track_id, identity, str(found.get('label')), float(found.get('score', math.nan)),
                              points, points / scale, inside))
        return hands
