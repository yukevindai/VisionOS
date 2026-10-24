"""Object lock for ordinary physical objects: ViTTrack per frame, object-only SIFT views for identity.

States reported by locate(): LOCKED, UNCERTAIN, HIDDEN, REACQUIRING, LOST. LOCKED and UNCERTAIN boxes come from
the current frame. HIDDEN is a remembered location after a confirmed lock is covered (by a hand or anything else):
the last box is carried with the camera's motion, estimated from background features (sparse optical flow and RANSAC,
the camera-motion compensation used by BoT-SORT), for at most remember_s. It is labelled as remembered, never treated
as a measurement, and ends when the background cannot be followed. Gemini's reference box is
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

LOCKED, UNCERTAIN, HIDDEN, REACQUIRING, LOST = 'LOCKED', 'UNCERTAIN', 'HIDDEN', 'REACQUIRING', 'LOST'


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
                 search_interval_s=.25, verify_interval_s=.5, min_inliers=8, search_edge=480,
                 view_interval_s=1., max_views=6, reference_window_s=2., remember_s=10., clock=time.monotonic):
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
        self.views = [] if first is None else [first]
        self.reference_rgb = rgb
        self.ref_box = (x0, y0, w, h)
        self.ref_hist = self._hist(rgb, (x0, y0, w, h))
        self.model_path = str(model_path or ensure_model())
        self.lock_score, self.weak_score, self.min_appearance = lock_score, weak_score, min_appearance
        self.uncertain_s, self.search_interval_s, self.verify_interval_s = uncertain_s, search_interval_s, verify_interval_s
        self.search_edge, self.view_interval_s, self.max_views = search_edge, view_interval_s, max_views
        self.clock = clock
        self.reference_until = clock() + reference_window_s
        self.remember_s = remember_s
        self.hidden = None
        self.last_good = None
        self.prev_gray = None
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

    def _pairs(self, view, points, desc, duplicates=None):
        knn = [p for p in self.matcher.knnMatch(view.desc, desc, k=2) if len(p) == 2]
        pairs = [m for m, n in knn if m.distance < .75 * n.distance]
        if duplicates is not None:
            spread = max(20., .5 * min(view.box[2], view.box[3]))
            tied = [(m, n) for m, n in knn if m.distance >= .75 * n.distance and n.distance < 1.1 * m.distance
                    and np.linalg.norm(points[m.trainIdx] - points[n.trainIdx]) > spread]
            if len(tied) >= 2 * self.min_inliers:
                src = np.float32([view.points[m.queryIdx] for m, _ in tied] * 2)
                dst = np.float32([points[m.trainIdx] for m, _ in tied] + [points[n.trainIdx] for _, n in tied])
                first = self._cluster(src, dst)
                if first is not None:
                    rest = ~first[1]
                    second = self._cluster(src[rest], dst[rest])
                    if second is not None:
                        a = dst[first[1]].mean(0)
                        b = dst[rest][second[1]].mean(0)
                        if np.linalg.norm(a - b) > spread:
                            duplicates.append(int(min(first[1].sum(), second[1].sum())))
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
        best, most, duplicates = None, 0, []
        for view in reversed(self.views):
            src, dst = self._pairs(view, points, desc, duplicates)
            most = max(most, len(src))
            found = self._cluster(src, dst)
            if found is not None and (best is None or found[1].sum() > best[3].sum()):
                best = (view, src, dst, found[1], found[0])
        repeated = max(duplicates, default=0)
        if repeated >= self.min_inliers and repeated >= (0 if best is None else .5 * best[3].sum()):
            return None, 'Two similar objects match; not choosing one'
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

    def _from_reference(self, rgb):
        """Textureless objects: run ViTTrack from Gemini's box on its own frame to the current frame."""
        height, width = rgb.shape[:2]
        rh, rw = self.reference_rgb.shape[:2]
        sx, sy = width / rw, height / rh
        x, y, w, h = self.ref_box
        box = (x * sx, y * sy, w * sx, h * sy)
        reference = self.reference_rgb if (rw, rh) == (width, height) else cv2.resize(
            self.reference_rgb, (width, height), interpolation=cv2.INTER_AREA)
        self._start(reference, box)
        ok, raw = self.native.update(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        score = float(self.native.getTrackingScore())
        found = tuple(map(float, raw))
        self.native = None
        if not ok or not np.isfinite(score) or score < self.lock_score:
            return None, f'Object not found from Gemini box (tracker score {score:.2f})'
        ratio = (found[2] * found[3]) / max(1., box[2] * box[3])
        jump = np.hypot(found[0] + found[2] / 2 - box[0] - box[2] / 2, found[1] + found[3] / 2 - box[1] - box[3] / 2)
        if not .4 <= ratio <= 2.5 or jump > max(60., max(box[2], box[3])):
            return None, 'Object moved too far since identification; waiting for re-identification'
        appearance = self._appearance(rgb, found)
        if appearance < self.min_appearance:
            return None, f'Colors differ from identified object ({appearance:.2f})'
        return (*found, 0, appearance), ''

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
        self.hidden = None
        self.native = None
        self.box = self.display = None
        self.uncertain_since = None
        self.strong_since = None
        self.state = REACQUIRING if self.state != LOST else LOST
        self.next_search = 0.

    def _lose(self, reason):
        remember = (self.remember_s > 0 and self.last_good is not None and self.box is not None
                    and self.state in (LOCKED, UNCERTAIN))
        box = self.box
        self.native = None
        self.box = self.display = None
        self.uncertain_since = None
        self.strong_since = None
        if remember:
            self.hidden = {'box': box, 'since': self.clock(), 'why': reason}
            self.state = HIDDEN
            self.next_search = 0.
            return LockMatch(_box_polygon(*box), f'Hidden ({reason}); remembered location, not live',
                             HIDDEN, score=self.last_score)
        self.state = LOST
        return LockMatch(None, reason, LOST, score=self.last_score)

    def _camera_motion(self, previous, gray, box):
        """Background motion between frames, excluding the remembered object's area. None if unreliable."""
        if previous is None or previous.shape != gray.shape:
            return None
        x, y, w, h = map(int, box)
        mask = np.full(gray.shape, 255, np.uint8)
        pad = round(.3 * max(w, h))
        mask[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad] = 0
        points = cv2.goodFeaturesToTrack(previous, maxCorners=250, qualityLevel=.01, minDistance=8, mask=mask)
        if points is None or len(points) < 20:
            return None
        moved, ok, _ = cv2.calcOpticalFlowPyrLK(previous, gray, points, None, winSize=(21, 21), maxLevel=3)
        if moved is None:
            return None
        good = ok.ravel() > 0
        if good.sum() < 20:
            return None
        A, inliers = cv2.estimateAffinePartial2D(points[good], moved[good], method=cv2.RANSAC,
                                                 ransacReprojThreshold=3.)
        if A is None or inliers is None or inliers.sum() < 15 or inliers.mean() < .5:
            return None
        return A

    def _reacquire_near(self, rgb, gray, box):
        """The SAME object reappearing near its remembered location: last confirmed appearance, then features."""
        x, y, w, h = box
        cx, cy = x + w / 2, y + h / 2
        reach = max(60., .8 * max(w, h))
        if self.last_good is not None:
            good_rgb, good_box = self.last_good
            if good_rgb.shape == rgb.shape:
                self._start(good_rgb, good_box)
                ok, raw = self.native.update(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
                score = float(self.native.getTrackingScore())
                self.native = None
                found = tuple(map(float, raw))
                if (ok and np.isfinite(score) and score >= self.lock_score
                        and np.hypot(found[0] + found[2] / 2 - cx, found[1] + found[3] / 2 - cy) <= reach
                        and self._appearance(rgb, found) >= self.min_appearance):
                    return found, 'last confirmed appearance'
        if self.views:
            found, _ = self._search(rgb, gray)
            if found is not None and np.hypot(found[0] + found[2] / 2 - cx, found[1] + found[3] / 2 - cy) <= reach:
                return found[:4], f'{found[4]} feature inliers'
        return None, ''

    def _hidden(self, rgb, gray, now):
        box = self.hidden['box']
        A = self._camera_motion(self.prev_gray, gray, box)
        if A is None:
            self.hidden = None
            self.state = LOST
            return LockMatch(None, 'Object hidden and camera motion could not be followed; target lost', LOST)
        x, y, w, h = box
        corners = cv2.transform(_box_polygon(x, y, w, h)[None], A)[0]
        nx, ny, nw, nh = cv2.boundingRect(corners)
        height, width = gray.shape
        inside = max(0, min(nx + nw, width) - max(nx, 0)) * max(0, min(ny + nh, height) - max(ny, 0))
        if inside < .5 * nw * nh:
            self.hidden = None
            self.state = LOST
            return LockMatch(None, 'Remembered location left the frame; target lost', LOST)
        if now - self.hidden['since'] > self.remember_s:
            self.hidden = None
            self.state = LOST
            return LockMatch(None, f'Hidden for over {self.remember_s:g} s; target lost', LOST)
        box = (float(nx), float(ny), float(nw), float(nh))
        self.hidden['box'] = box
        if now >= self.next_search:
            self.next_search = now + self.search_interval_s
            found, how = self._reacquire_near(rgb, gray, box)
            if found is not None:
                self.hidden = None
                self._start(rgb, found)
                self.state = REACQUIRING
                self.next_verify = now + self.verify_interval_s
                return LockMatch(None, f'Reappeared at remembered location ({how}); confirming on next frame',
                                 REACQUIRING, 1, self._appearance(rgb, found))
        return LockMatch(_box_polygon(*box), f"Hidden {now - self.hidden['since']:.1f}s; remembered location, not live",
                         HIDDEN)

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
            self.prev_gray = None
        rgb = np.array(image)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        try:
            return self._locate(rgb, gray, self.clock())
        finally:
            self.prev_gray = gray

    def _locate(self, rgb, gray, now):
        if self.native is None and self.hidden is not None:
            return self._hidden(rgb, gray, now)
        if self.native is None:
            self.last_score = None
            if now < self.next_search:
                return LockMatch(None, 'Searching for the identified object', self.state)
            self.next_search = now + self.search_interval_s
            found, reason = self._search(rgb, gray) if self.views else (None, 'Object has too few features to search')
            self.last_method = 'SIFT reacquisition'
            nothing_matched = reason.startswith(('Object not found', 'No features', 'Object has too few'))
            if found is None and nothing_matched and now <= self.reference_until:
                found, reference_reason = self._from_reference(rgb)
                if found is not None:
                    self.last_method = 'ViTTrack from Gemini box'
                else:
                    reason = f'{reason}; {reference_reason}'
            if found is None:
                self.state = LOST if self.state == LOST else REACQUIRING
                return LockMatch(None, reason, self.state)
            x, y, w, h, inliers, appearance = found
            self._start(rgb, (x, y, w, h))
            self.state = REACQUIRING
            self.last_appearance = appearance
            self.next_verify = now + self.verify_interval_s
            how = f'{inliers} feature inliers' if inliers else 'ViTTrack from Gemini box'
            return LockMatch(None, f'Candidate found ({how}); confirming on next frame',
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
            if strong:
                self.last_good = (rgb, box)
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
