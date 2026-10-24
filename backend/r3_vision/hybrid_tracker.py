"""OpenCV pretrained ViTTrack for temporal tracking, SIFT for wide-baseline recovery.

Independent adapter using published OpenCV APIs; no third-party source code is bundled.
See TRACKING_RESEARCH.md for upstream implementations, license, and benchmark limitations.
"""
import time

import cv2
import numpy as np
from PIL import Image

from .tracker_models import ensure_model
from .visual_tracker import ReferenceTracker, Match, working_image


class HybridTracker:
    def __init__(self, reference, region, model_path=None):
        self.reference = ReferenceTracker(reference, region, features='sift')
        self.model_path = str(model_path or ensure_model())
        self.native = None
        self.native_size = None
        self.last_score = None
        self.last_method = ''
        self.next_recovery = 0.
        self.last_failure = 'Not yet located'
        self.flow_gray = None
        self.flow_points = None

    def _init_native(self, image, box):
        params = cv2.TrackerVit_Params()
        params.net = self.model_path
        params.tracking_score_threshold = .5
        params.backend = cv2.dnn.DNN_BACKEND_OPENCV
        params.target = cv2.dnn.DNN_TARGET_CPU
        self.native = cv2.TrackerVit_create(params)
        self.native.init(cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR), tuple(map(int, box)))
        self.native_size = image.size
        self._seed_context(image, box)

    def _seed_context(self, image, box):
        gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
        x, y, w, h = map(int, box)
        self.flow_box = (x, y, w, h)
        pad = max(60, 2*max(w, h))
        a, b, c, d = max(0, x-pad), max(0, y-pad), min(gray.shape[1], x+w+pad), min(gray.shape[0], y+h+pad)
        self.flow_roi = a, b, c, d
        self.flow_gray = gray[b:d, a:c].copy()
        self.flow_points = cv2.goodFeaturesToTrack(self.flow_gray, maxCorners=160, qualityLevel=.01,
                                                 minDistance=5, blockSize=7)

    def _context_follows(self, gray, box):
        """Forward/backward LK rejects scene changes even if a similar control remains."""
        a, b, c, d = self.flow_roi
        gray = gray[b:d, a:c]
        if self.flow_points is None or len(self.flow_points) < 12 or gray.shape != self.flow_gray.shape:
            return False
        settings = dict(winSize=(21, 21), maxLevel=3,
                        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, .02))
        forward, ok, error = cv2.calcOpticalFlowPyrLK(self.flow_gray, gray, self.flow_points, None, **settings)
        if forward is None:
            return False
        backward, back_ok, _ = cv2.calcOpticalFlowPyrLK(gray, self.flow_gray, forward, None, **settings)
        if backward is None:
            return False
        good = (ok.ravel() > 0) & (back_ok.ravel() > 0)
        good &= np.linalg.norm(backward-self.flow_points, axis=2).ravel() < 1.
        good &= error.ravel() < 25
        if good.sum() < 12 or good.mean() < .6:
            return False
        H, inliers = cv2.findHomography(self.flow_points[good], forward[good], cv2.RANSAC, 2.)
        if not (H is not None and np.isfinite(H).all() and inliers is not None
                and inliers.sum() >= 12 and inliers.mean() >= .65):
            return False
        x, y, w, h = self.flow_box
        expected = cv2.perspectiveTransform(np.float32([[[x+w/2-a, y+h/2-b]]]), H)[0, 0]+[a, b]
        bx, by, bw, bh = map(float, box)
        # A good learned score cannot jump the box to a neighboring, similar button.
        return np.linalg.norm(expected-np.float32([bx+bw/2, by+bh/2])) <= max(8., min(bw, bh)*.35)

    def seed_reference(self):
        """Only used before buffered measured-frame replay, never directly overlaid on live."""
        x, y, r, b = self.reference.box
        self._init_native(self.reference.image, (x, y, r-x+1, b-y+1))

    def reset_temporal(self):
        self.native = None
        self.next_recovery = 0.

    def _refine(self, gray, box):
        """Verify immutable target appearance near learned prediction; no template updates.

        Multiscale local matching refines the learned coarse box. A competing remote peak
        is rejected, and per-tile checks prevent context-only tracking over an occluder.
        """
        x, y, w, h = map(float, box)
        if min(w, h) < 10 or not np.isfinite([x, y, w, h]).all():
            return None
        height, width = gray.shape
        if x < 0 or y < 0 or x+w > width or y+h > height:
            return None
        a, b = max(0, int(x-w*.4)), max(0, int(y-h*.4))
        c, d = min(width, int(x+w*1.4+1)), min(height, int(y+h*1.4+1))
        search = gray[b:d, a:c]
        template = self.reference.patch
        best = None
        scale = np.sqrt((w*h)/(template.shape[1]*template.shape[0]))
        sizes = {(template.shape[1], template.shape[0]), (round(w), round(h))}
        sizes.update((round(template.shape[1]*scale*f), round(template.shape[0]*scale*f))
                     for f in [.85, .90, .95, 1., 1.05, 1.10, 1.15])
        for tw, th in sorted(sizes):
            if min(tw, th) < 12 or tw > search.shape[1] or th > search.shape[0]:
                continue
            patch = cv2.resize(template, (tw, th), interpolation=cv2.INTER_AREA)
            response = cv2.matchTemplate(search, patch, cv2.TM_CCOEFF_NORMED)
            _, score, _, (px, py) = cv2.minMaxLoc(response)
            # Separate matches for repeated controls must not be silently resolved by search order.
            other = response.copy()
            r = max(3, min(tw, th)//3)
            other[max(0, py-r):py+r+1, max(0, px-r):px+r+1] = -1
            if other.max(initial=-1) > score-.04:
                continue
            if best is None or score > best[0]:
                best = score, a+px, b+py, tw, th, patch
        if best is None or best[0] < .80:
            return None
        score, x, y, w, h, expected = best
        actual = gray[y:y+h, x:x+w]
        if actual.std() < 8:
            return None
        informative = 0
        for e_rows, a_rows in zip(np.array_split(expected, 3), np.array_split(actual, 3)):
            for e, a in zip(np.array_split(e_rows, 3, axis=1), np.array_split(a_rows, 3, axis=1)):
                if e.std() >= 8:
                    informative += 1
                    if a.std() < 5 or float(cv2.matchTemplate(a, e, cv2.TM_CCOEFF_NORMED)[0, 0]) < .65:
                        return None
        if informative < 2:
            return None
        return Match(np.float32([[x, y], [x+w-1, y], [x+w-1, y+h-1], [x, y+h-1]]),
                     'ViTTrack + local target appearance verified', correlation=score)

    def locate(self, current: Image.Image, *, recovery=True):
        image = working_image(current)
        if self.native is not None and self.native_size == image.size:
            bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
            ok, box = self.native.update(bgr)
            score = float(self.native.getTrackingScore())
            self.last_score = score
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            match = self._refine(gray, box) if ok and np.isfinite(score) and score >= .5 and self._context_follows(gray, box) else None
            if match is not None:
                self.last_method = 'ViTTrack'
                self._seed_context(image, cv2.boundingRect(match.polygon))
                return match
            self.native = None
            self.last_failure = 'ViTTrack confidence/target appearance lost'
        if not recovery:
            return Match(None, self.last_failure)
        now = time.monotonic()
        if now < self.next_recovery:
            return Match(None, self.last_failure)
        self.next_recovery = now+.6
        self.last_score = None
        match = self.reference.locate(image)
        self.last_method = 'SIFT reacquisition'
        if match.polygon is not None:
            x, y, w, h = cv2.boundingRect(match.polygon)
            self._init_native(image, (x, y, w, h))
            return match
        self.last_failure = match.reason
        return match

    def prime(self, history, received, epoch, max_work_s=8.):
        """Catch up on bounded intervening frames in the CLOUD thread, not the UI/CV loop.

        Any history gap, lost visibility, size change or missed deadline aborts temporal reuse.
        The normal SIFT recovery path must then locate the target in the current image.
        """
        self.seed_reference()
        start = time.monotonic()
        cursor = received
        count = 0
        while time.monotonic()-start < max_work_s:
            frames = history.after(cursor, epoch)
            if not frames:
                if 0 <= time.monotonic()-cursor <= .3 and count > 0:
                    return True, count
                break
            for frame in frames:
                # Process actual measured endpoints at <=5 Hz while catching up. Bound every
                # step's gap below; a missed interval still invalidates temporal reuse.
                if frame.received-cursor < .18 and frame is not frames[-1]:
                    continue
                if frame.received-cursor > .5 or time.monotonic()-start >= max_work_s:
                    self.native = None
                    return False, count
                match = self.locate(frame.image(), recovery=False)
                if match.polygon is None:
                    self.native = None
                    return False, count
                cursor = frame.received
                count += 1
        self.native = None
        return False, count
