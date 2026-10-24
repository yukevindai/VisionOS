"""Object lock for ordinary physical objects: ViTTrack per frame, object-only SIFT views for identity.

States reported by locate(): LOCKED, UNCERTAIN, REACQUIRING, LOST. A box is returned only for
LOCKED and UNCERTAIN, and only when it comes from the current frame. Gemini's reference box is
used once to describe the object's appearance; it is never drawn on a later frame. While the lock
is strong, extra views of the same object are kept so it can be recognized from new angles.
"""
from __future__ import annotations

from dataclasses import dataclass
import time

import cv2
import numpy as np
from PIL import Image

from .live_camera import pixel_box
from .tracker_models import ensure_model
from .visual_tracker import working_image

LOCKED, UNCERTAIN, REACQUIRING, LOST = 'LOCKED', 'UNCERTAIN', 'REACQUIRING', 'LOST'


@dataclass
class LockMatch:
    polygon: np.ndarray | None
    reason: str
    state: str = LOST
    inliers: int = 0
    correlation: float = 0.0
    score: float | None = None


@dataclass
class View:
    points: np.ndarray
    desc: np.ndarray
    box: tuple


def _box_polygon(x, y, w, h):
    return np.float32([[x, y], [x + w - 1, y], [x + w - 1, y + h - 1], [x, y + h - 1]])


class ObjectLockTracker:
    def __init__(self, reference: Image.Image, region: dict, model_path=None, *,
                 lock_score=.45, weak_score=.25, min_appearance=.45, uncertain_s=.5,
                 search_interval_s=.25, verify_interval_s=.5, min_inliers=8, search_edge=640,
                 view_interval_s=1., max_views=6, clock=time.monotonic):
        image = working_image(reference)
        x0, y0, x1, y1 = pixel_box(region, image.size)
        w, h = x1 - x0 + 1, y1 - y0 + 1
        if min(w, h) < 16:
            raise ValueError('Target too small to lock; move closer')
        self.size = image.size
        rgb = np.array(image)
        self.sift = cv2.SIFT_create(nfeatures=1500, contrastThreshold=.02)
        self.matcher = cv2.BFMatcher(cv2.NORM_L2)
        self.min_inliers = min_inliers
        first = self._view(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), (x0, y0, w, h))
        if first is None:
            raise ValueError('Target has too little visible detail to verify; move closer or improve lighting')
        self.views = [first]
        self.ref_box = (x0, y0, w, h)
        self.ref_hist = self._hist(rgb, (x0, y0, w, h))
        self.model_path = str(model_path or ensure_model())
        self.lock_score, self.weak_score, self.min_appearance = lock_score, weak_score, min_appearance
        self.uncertain_s, self.search_interval_s, self.verify_interval_s = uncertain_s, search_interval_s, verify_interval_s
        self.search_edge, self.view_interval_s, self.max_views = search_edge, view_interval_s, max_views
        self.clock = clock
        self.native = None
        self.box = None
        self.display = None
        self.state = REACQUIRING
        self.uncertain_since = None
        self.weak_frames = 0
        self.strong_since = None
        self.next_search = 0.
        self.next_verify = 0.
        self.next_view = 0.
        self.verify_failures = 0
        self.last_score = None
        self.last_method = ''
        self.last_appearance = 0.

    @property
    def keyframes(self):
        return self.views

    def _view(self, gray, box):
        x, y, w, h = map(int, box)
        mx, my = max(1, round(w * .06)), max(1, round(h * .04))
        a, b = max(0, x + mx), max(0, y + my)
        crop = gray[b:y + h - my, a:x + w - mx]
        if min(crop.shape[:2]) < 12:
            return None
        keys, desc = self.sift.detectAndCompute(crop, None)
        if desc is None or len(keys) < self.min_inliers:
            return None
        return View(np.float32([k.pt for k in keys]) + np.float32([a, b]), desc, (x, y, w, h))

    @staticmethod
    def _hist(rgb, box):
        x, y, w, h = map(int, box)
        patch = rgb[max(0, y):y + h, max(0, x):x + w]
        if patch.size == 0:
            return None
        hsv = cv2.cvtColor(patch, cv2.COLOR_RGB2HSV)
        hist = cv2.calcHist([hsv], [0, 1, 2], None, [16, 6, 6], [0, 180, 0, 256, 0, 256])
        return cv2.normalize(hist, hist).flatten()

    def _appearance(self, rgb, box):
        hist = self._hist(rgb, box)
        if hist is None:
            return 0.
        return float(1 - cv2.compareHist(self.ref_hist, hist, cv2.HISTCMP_BHATTACHARYYA))

    def _features(self, gray, origin=(0, 0), scale=1.):
        keys, desc = self.sift.detectAndCompute(gray, None)
        if desc is None or len(keys) < 2:
            return None, None
        return np.float32([k.pt for k in keys]) / scale + np.float32(origin), desc

    def _pairs(self, view, points, desc):
        pairs = [m for m, n in (p for p in self.matcher.knnMatch(view.desc, desc, k=2) if len(p) == 2)
                 if m.distance < .75 * n.distance]
        src = np.float32([view.points[m.queryIdx] for m in pairs]).reshape(-1, 2)
        dst = np.float32([points[m.trainIdx] for m in pairs]).reshape(-1, 2)
        return src, dst

    def _cluster(self, src, dst, least=None):
        least = self.min_inliers if least is None else least
        if len(src) < least:
            return None
        A, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.,
                                                 maxIters=2000, confidence=.995)
        if A is None or inliers is None or not np.isfinite(A).all():
            return None
        good = inliers.ravel().astype(bool)
        return (A, good) if good.sum() >= least else None

    def _search(self, rgb, gray):
        scale = min(1., self.search_edge / max(gray.shape))
        small = gray if scale == 1 else cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        points, desc = self._features(small, scale=scale)
        if desc is None:
            return None, 'No features in current frame'
        best, most = None, 0
        for view in reversed(self.views):
            src, dst = self._pairs(view, points, desc)
            most = max(most, len(src))
            found = self._cluster(src, dst)
            if found is not None and (best is None or found[1].sum() > best[3].sum()):
                best = (view, src, dst, found[1], found[0])
        if best is None:
            return None, f'Object not found in current frame ({most} feature matches)'
        view, src, dst, good, A = best
        corners = cv2.transform(_box_polygon(*view.box)[None], A)[0]
        bx, by, bw, bh = cv2.boundingRect(corners)
        rest = ~good
        rest &= ~((dst[:, 0] >= bx) & (dst[:, 0] < bx + bw) & (dst[:, 1] >= by) & (dst[:, 1] < by + bh))
        second = self._cluster(src[rest], dst[rest])
        if second is not None and second[1].sum() >= max(self.min_inliers, .5 * good.sum()):
            return None, 'Two similar objects match; not choosing one'
        size = float(np.sqrt(abs(np.linalg.det(A[:, :2]))))
        if not .25 <= size <= 4:
            return None, 'Implausible size change'
        height, width = gray.shape
        bx, by = max(0, bx), max(0, by)
        bw, bh = min(width, bx + bw) - bx, min(height, by + bh) - by
        if min(bw, bh) < 12:
            return None, 'Matched object mostly outside frame'
        appearance = self._appearance(rgb, (bx, by, bw, bh))
        if appearance < self.min_appearance:
            return None, f'Feature match but colors differ ({appearance:.2f})'
        return (bx, by, bw, bh, int(good.sum()), appearance), ''

    def _verify(self, gray, box):
        x, y, w, h = map(int, box)
        pad = round(.15 * max(w, h))
        a, b = max(0, x - pad), max(0, y - pad)
        crop = gray[b:y + h + pad, a:x + w + pad]
        if min(crop.shape) < 16:
            return False
        points, desc = self._features(crop, origin=(a, b))
        if desc is None:
            return False
        least = max(4, self.min_inliers // 2)
        return any(self._cluster(*self._pairs(view, points, desc), least=least) is not None for view in self.views)

    def _start(self, rgb, box):
        params = cv2.TrackerVit_Params()
        params.net = self.model_path
        params.tracking_score_threshold = self.weak_score
        params.backend = cv2.dnn.DNN_BACKEND_OPENCV
        params.target = cv2.dnn.DNN_TARGET_CPU
        self.native = cv2.TrackerVit_create(params)
        self.native.init(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), tuple(map(int, box)))
        self.box = tuple(map(float, box))
        self.display = np.array(self.box)
        self.verify_failures = 0
        self.weak_frames = 0
        self.strong_since = None

    def reset_temporal(self):
        self.native = None
        self.box = self.display = None
        self.uncertain_since = None
        self.strong_since = None
        self.state = REACQUIRING if self.state != LOST else LOST
        self.next_search = 0.

    def _lose(self, reason):
        self.native = None
        self.box = self.display = None
        self.uncertain_since = None
        self.strong_since = None
        self.state = LOST
        return LockMatch(None, reason, LOST, score=self.last_score)

    def _plausible(self, box):
        x, y, w, h = box
        px, py, pw, ph = self.box
        if not np.isfinite(box).all() or min(w, h) < 8:
            return False
        ratio = (w * h) / max(1., pw * ph)
        jump = np.hypot((x + w / 2) - (px + pw / 2), (y + h / 2) - (py + ph / 2))
        return .55 <= ratio <= 1.8 and jump <= max(40., .6 * max(pw, ph))

    def _learn_view(self, gray, box, now):
        if now < self.next_view or self.strong_since is None or now - self.strong_since < self.view_interval_s:
            return
        self.next_view = now + self.view_interval_s
        view = self._view(gray, box)
        if view is not None:
            self.views.append(view)
            if len(self.views) > self.max_views:
                del self.views[1]

    def locate(self, current: Image.Image) -> LockMatch:
        image = working_image(current)
        if image.size != self.size:
            self.size = image.size
            self.reset_temporal()
        rgb = np.array(image)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        now = self.clock()
        if self.native is None:
            self.last_score = None
            if now < self.next_search:
                return LockMatch(None, 'Searching for the identified object', self.state)
            self.next_search = now + self.search_interval_s
            found, reason = self._search(rgb, gray)
            self.last_method = 'SIFT reacquisition'
            if found is None:
                self.state = LOST if self.state == LOST else REACQUIRING
                return LockMatch(None, reason, self.state)
            x, y, w, h, inliers, appearance = found
            self._start(rgb, (x, y, w, h))
            self.state = REACQUIRING
            self.last_appearance = appearance
            self.next_verify = now + self.verify_interval_s
            return LockMatch(None, f'Candidate found ({inliers} feature inliers); confirming on next frame',
                             REACQUIRING, inliers, appearance)
        ok, raw = self.native.update(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        score = float(self.native.getTrackingScore())
        self.last_score = score
        self.last_method = 'ViTTrack'
        box = tuple(map(float, raw))
        if not ok or not np.isfinite(score) or score < self.weak_score:
            return self._lose(f'Tracker confidence too low ({score:.2f})')
        if not self._plausible(box):
            return self._lose('Implausible jump or size change; refusing to follow')
        appearance = self._appearance(rgb, box)
        self.last_appearance = appearance
        if appearance < .6 * self.min_appearance:
            return self._lose(f'Colors no longer match the identified object ({appearance:.2f})')
        if now >= self.next_verify:
            self.next_verify = now + self.verify_interval_s
            self.verify_failures = 0 if self._verify(gray, box) else self.verify_failures + 1
        unconfirmed = self.verify_failures >= 4 and score < .6
        strong = score >= self.lock_score and appearance >= self.min_appearance and not unconfirmed
        self.weak_frames = 0 if strong else self.weak_frames + 1
        self.box = box
        if strong or (self.state == LOCKED and self.weak_frames < 2):
            self.uncertain_since = None
            if strong and self.strong_since is None:
                self.strong_since = now
            self.state = LOCKED
            self.display = .4 * self.display + .6 * np.array(box)
            if strong and score >= .6 and appearance >= .7 and self.verify_failures <= 1:
                self._learn_view(gray, box, now)
        else:
            self.strong_since = None
            if self.uncertain_since is None:
                self.uncertain_since = now
            if now - self.uncertain_since > self.uncertain_s:
                return self._lose('Evidence stayed weak; target lost')
            self.state = UNCERTAIN
            self.display = np.array(box)
        reason = (f'score {score:.2f} | colors {appearance:.2f} | views {len(self.views)}'
                  + ('' if self.verify_failures == 0 else f' | feature check failed x{self.verify_failures}'))
        return LockMatch(_box_polygon(*self.display), reason, self.state, correlation=appearance, score=score)
