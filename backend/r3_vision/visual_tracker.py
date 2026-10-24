"""Conservative planar reference-to-current matching for a 2D visual experiment.

Never integrates incremental transforms or extrapolates a lost target. No guidance API.
"""
from dataclasses import dataclass
import math

import cv2
import numpy as np
from PIL import Image

from .live_camera import pixel_box


def thumbnail_size(width, height, max_edge=960):
    """Pillow Image.thumbnail's output size, so fast resizing keeps the established geometry."""
    if max_edge >= width and max_edge >= height:
        return width, height
    aspect = width / height
    x = y = max_edge

    def round_aspect(number, key):
        return max(min(math.floor(number), math.ceil(number), key=key), 1)
    if x / y >= aspect:
        x = round_aspect(y * aspect, key=lambda n: abs(aspect - n / y))
    else:
        y = round_aspect(x / aspect, key=lambda n: 0 if n == 0 else abs(aspect - x / n))
    return x, y


def working_image(image: Image.Image, max_edge=960) -> Image.Image:
    size = thumbnail_size(*image.size, max_edge)
    if size == image.size:
        return image.convert('RGB') if image.mode != 'RGB' else image.copy()
    rgb = np.asarray(image.convert('RGB') if image.mode != 'RGB' else image)
    return Image.fromarray(cv2.resize(rgb, size, interpolation=cv2.INTER_AREA))


@dataclass
class Match:
    polygon: np.ndarray | None
    reason: str
    inliers: int = 0
    correlation: float = 0.0


class ReferenceTracker:
    """Immutable selected reference plus surrounding context; match anew on every frame."""
    def __init__(self, reference: Image.Image, region: dict, *, features='orb'):
        self.image = working_image(reference)
        self.gray = cv2.cvtColor(np.array(self.image), cv2.COLOR_RGB2GRAY)
        self.box = pixel_box(region, self.image.size)
        x, y, right, bottom = self.box
        self.corners = np.float32([[x, y], [right, y], [right, bottom], [x, bottom]])
        if min(right-x, bottom-y) < 12:
            raise ValueError('Target too small for local verification; move closer')
        self.patch = self.gray[y:bottom+1, x:right+1]
        if self.patch.std() < 8:
            raise ValueError('Target lacks visible detail; cannot verify identity locally')
        pad = max(90, 3 * max(right-x, bottom-y))
        self.context = (max(0, x-pad), max(0, y-pad), min(self.gray.shape[1], right+pad), min(self.gray.shape[0], bottom+pad))
        mask = np.zeros_like(self.gray)
        a, b, c, d = self.context
        mask[b:d, a:c] = 255
        self.features = features
        self.orb = (cv2.SIFT_create(nfeatures=1800, contrastThreshold=.02) if features == 'sift'
                    else cv2.ORB_create(nfeatures=1800, edgeThreshold=8, fastThreshold=12))
        self.keys, self.desc = self.orb.detectAndCompute(self.gray, mask)
        if self.desc is None or len(self.keys) < 16:
            raise ValueError('Insufficient reference context features; move closer or improve lighting')
        self.matcher = cv2.BFMatcher(cv2.NORM_L2 if features == 'sift' else cv2.NORM_HAMMING)

    def _ratio(self, pairs):
        return {m.queryIdx: m.trainIdx for pair in pairs if len(pair) == 2
                for m, n in [pair] if m.distance < (.75 if self.features == 'sift' else .70)*n.distance
                and (self.features == 'sift' or m.distance <= 64)}

    def locate(self, current: Image.Image) -> Match:
        current = working_image(current)
        gray = cv2.cvtColor(np.array(current), cv2.COLOR_RGB2GRAY)
        keys, desc = self.orb.detectAndCompute(gray, None)
        if desc is None or len(keys) < 16:
            return Match(None, 'No current-frame features')
        forward = self._ratio(self.matcher.knnMatch(self.desc, desc, k=2))
        backward = self._ratio(self.matcher.knnMatch(desc, self.desc, k=2))
        pairs = [(a, b) for a, b in forward.items() if backward.get(b) == a]
        if len(pairs) < 12:
            return Match(None, 'Too few unique mutual feature matches')
        src = np.float32([self.keys[a].pt for a, b in pairs])
        dst = np.float32([keys[b].pt for a, b in pairs])
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 2.5, maxIters=1500, confidence=.995)
        if H is None or mask is None or not np.isfinite(H).all():
            return Match(None, 'No consistent panel geometry')
        good = mask.ravel().astype(bool)
        count = int(good.sum())
        if count < 12 or count/len(pairs) < .65:
            return Match(None, 'Weak geometric consensus', count)
        projected = cv2.perspectiveTransform(src[good, None], H)[:, 0]
        if np.median(np.linalg.norm(projected-dst[good], axis=1)) > 1.8:
            return Match(None, 'High reprojection error', count)
        # Require support around the target, rather than extrapolating from a remote panel corner.
        hull = cv2.convexHull(src[good])
        center = tuple(map(float, self.corners.mean(axis=0)))
        if cv2.pointPolygonTest(hull, center, True) < 0:
            return Match(None, 'Target outside matched context support', count)
        if cv2.contourArea(hull) < 2*cv2.contourArea(self.corners):
            return Match(None, 'Feature support too concentrated', count)
        polygon = cv2.perspectiveTransform(self.corners[None], H)[0]
        h, w = gray.shape
        if (not np.isfinite(polygon).all() or not cv2.isContourConvex(polygon) or
            cv2.contourArea(polygon, oriented=True) <= 0 or
            np.any(polygon < 0) or np.any(polygon[:, 0] >= w) or np.any(polygon[:, 1] >= h)):
            return Match(None, 'Target outside frame or invalid projection', count)
        area_ratio = cv2.contourArea(polygon)/cv2.contourArea(self.corners)
        edges = np.linalg.norm(polygon-np.roll(polygon, 1, axis=0), axis=1)
        if not .2 <= area_ratio <= 5 or edges.min() < 10 or edges.max()/edges.min() > 8:
            return Match(None, 'Implausible scale/perspective', count)
        # Warp the CURRENT pixels back to reference coordinates. Context alone is insufficient:
        # an occluded or changed button must fail even when its surrounding panel still matches.
        try:
            aligned = cv2.warpPerspective(gray, np.linalg.inv(H), (self.gray.shape[1], self.gray.shape[0]))
        except (np.linalg.LinAlgError, cv2.error):
            return Match(None, 'Singular alignment', count)
        x, y, right, bottom = self.box
        patch = aligned[y:bottom+1, x:right+1]
        a, b = self.patch.astype(np.float32), patch.astype(np.float32)
        if b.std() < 8:
            return Match(None, 'Target obscured or unreadable', count)
        correlation = float(cv2.matchTemplate(b, a, cv2.TM_CCOEFF_NORMED)[0, 0])
        # Check local tiles too: a small occluder must not hide behind whole-patch correlation.
        bad, informative = 0, 0
        for rows_a, rows_b in zip(np.array_split(a, 3), np.array_split(b, 3)):
            for tile_a, tile_b in zip(np.array_split(rows_a, 3, axis=1), np.array_split(rows_b, 3, axis=1)):
                if tile_a.std() >= 8:
                    informative += 1
                    score = float(cv2.matchTemplate(tile_b, tile_a, cv2.TM_CCOEFF_NORMED)[0, 0]) if tile_b.std() >= 5 else 0
                    bad += score < .65
        if correlation < .80 or informative < 2 or bad > 0:
            return Match(None, 'Target appearance changed/occluded', count, correlation)
        return Match(polygon, 'Reference geometry and target appearance verified', count, correlation)
