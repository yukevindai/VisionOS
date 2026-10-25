"""ALL OBJECTS mode: continuous local object inventory, asynchronous Gemini verification, inventory-first selection.

Discovery runs in a background thread at a reduced, adaptive rate on the latest working frame (YOLO-World v2-s with a
general everyday vocabulary, or optionally YOLOE-26n prompt-free). Detections are associated across passes by IoU,
then by centre distance, after compensating background camera motion (LK + RANSAC affine, as in object_lock's
HIDDEN mode), so each object keeps a temporary ID. Ultralytics' ByteTrack/BoT-SORT were not used: they assume
consecutive frames from one fixed predictor, while discovery skips frames and the shared YOLO-World predictor switches
vocabulary for HYBRID requests.

Gemini is never called per frame. At most one inventory call is in flight, only when stable objects are new or changed,
no more often than --verify-interval, through the session's RequestBudget with requests held back for the user. Gemini
labels are matched to the detections of the SAME frame by box overlap; matched objects become VERIFIED. Detector class
names are never brand evidence, and YOLOE prompt-free labels (measured wrong: "tequila" for cans) are shown as hints only.

A request first searches the inventory. It is answered locally only for (a) one VERIFIED object whose Gemini label
matches the request through the shared selector, with every similar-looking present object also verified, or (b) a
unique plainly named generic YOLO-World class (hybrid.local_choice), which then gets the usual Gemini check. The box
handed to the lock is detected again on the request's own frame. Anything else falls back to the HYBRID request flow.
Inventory boxes are a debug overlay only; they never drive guidance, which still comes from the lock alone.
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

os.environ.setdefault('YOLO_AUTOINSTALL', 'false')

import numpy as np
from PIL import ImageDraw

from . import gemini, hybrid, object_select, selector
from .local_detector import MODELS, merge

YOLOE_PF = MODELS / 'yoloe-26n-seg-pf.pt'
# General everyday objects a person might reach for. People are left out on purpose; hands are tracked separately.
VOCABULARY = ('cup', 'mug', 'bottle', 'water bottle', 'can', 'glass', 'bowl', 'plate', 'fork', 'spoon', 'knife',
              'phone', 'laptop', 'keyboard', 'computer mouse', 'remote control', 'book', 'notebook', 'pen',
              'scissors', 'keys', 'wallet', 'box', 'bag', 'backpack', 'headphones', 'charger', 'glasses', 'watch',
              'toy', 'cube', 'playing cards', 'bagel', 'bread', 'banana', 'apple', 'orange', 'sandwich', 'snack bag',
              'tissue box', 'lamp', 'plant', 'clock', 'pillow', 'tumbler', 'jar', 'towel', 'shoe', 'hat')
VERIFY_PROMPT = gemini.OBJECT_PROMPT + """
- In the same parentheses, after the category, add up to two other common names people use for exactly this kind of
  object, separated by commas, for example "black Owala water bottle (drink bottle, tumbler)". Only names that truly
  fit this object; never a brand you cannot read."""
VERIFIED, UNVERIFIED = 'VERIFIED', 'UNVERIFIED'
GATE_IOU = .3


def iou(a, b):
    return hybrid.iou(a, b)


def normalized(box, size):
    w, h = size
    x0, y0, x1, y1 = box
    return (min(max(x0 / w, 0.), 1.), min(max(y0 / h, 0.), 1.), min(max(x1 / w, 0.), 1.), min(max(y1 / h, 0.), 1.))


def small_gray(image, width=320):
    import cv2
    rgb = np.asarray(image.convert('RGB'))
    h, w = rgb.shape[:2]
    return cv2.resize(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), (width, max(1, round(h * width / w))),
                      interpolation=cv2.INTER_AREA)


def camera_motion(previous, gray):
    """Background affine between two small grey frames (moving hands are RANSAC outliers). None if unreliable."""
    import cv2
    if previous is None or previous.shape != gray.shape:
        return None
    points = cv2.goodFeaturesToTrack(previous, maxCorners=200, qualityLevel=.01, minDistance=6)
    if points is None or len(points) < 20:
        return None
    moved, ok, _ = cv2.calcOpticalFlowPyrLK(previous, gray, points, None, winSize=(21, 21), maxLevel=3)
    if moved is None or (ok.ravel() > 0).sum() < 20:
        return None
    good = ok.ravel() > 0
    A, inliers = cv2.estimateAffinePartial2D(points[good], moved[good], method=cv2.RANSAC, ransacReprojThreshold=2.)
    if A is None or inliers is None or inliers.sum() < 15 or inliers.mean() < .5:
        return None
    return A


def warp_box(box, A, shape):
    """Normalized box moved by an affine expressed in the pixels of a grey frame of `shape` (h, w)."""
    h, w = shape
    x0, y0, x1, y1 = box
    corners = np.float32([[x0 * w, y0 * h], [x1 * w, y0 * h], [x1 * w, y1 * h], [x0 * w, y1 * h]])
    moved = corners @ np.float32(A)[:, :2].T + np.float32(A)[:, 2]
    (mx0, my0), (mx1, my1) = moved.min(0), moved.max(0)
    return (float(mx0 / w), float(my0 / h), float(mx1 / w), float(my1 / h))


@dataclass
class Track:
    id: int
    box: tuple  # normalized x0, y0, x1, y1 on the frame it was last detected in
    names: list
    score: float
    first_seen: float
    last_seen: float
    hits: int = 1
    misses: int = 0  # consecutive discovery passes that did not see it
    version: int = 0  # bumped when the object may have changed identity; verification is per version
    status: str = UNVERIFIED
    label: str = ''  # Gemini's label, only when VERIFIED
    legibility: str = ''
    note: str = ''
    checked_version: int = -1  # last version Gemini was asked about
    checking: bool = False
    name_counts: Counter = field(default_factory=Counter)

    @property
    def detector_label(self):
        return self.name_counts.most_common(1)[0][0] if self.name_counts else '/'.join(self.names)

    def shown_label(self):
        return self.label if self.status == VERIFIED else self.detector_label

    def shown_status(self):
        if self.status == VERIFIED:
            return VERIFIED
        if self.checking:
            return 'UNVERIFIED (checking)'
        return f'UNVERIFIED ({self.note})' if self.note else UNVERIFIED


class Inventory:
    """Pure association / verification / lookup logic. Thread-safe; no models, threads or API calls."""
    def __init__(self, max_missing_s=4., present_s=1.5, stable_hits=2, labels_trusted=True):
        self.tracks = {}
        self.next_id = 1
        self.max_missing_s, self.present_s, self.stable_hits = max_missing_s, present_s, stable_hits
        self.labels_trusted = labels_trusted
        self.last_pass = None
        self.gemini_only = []
        self.lock = threading.RLock()

    def _pairs(self, candidates, boxes):
        """Greedy one-to-one association. Returns {candidate index: (track id, how)} and the set of crowded tracks."""
        scored, crowded = [], set()
        for tid, box in boxes.items():
            overlaps = sorted((iou(box, c['nbox']), i) for i, c in enumerate(candidates))
            good = [o for o in overlaps if o[0] >= GATE_IOU]
            if len(good) > 1 and good[-2][0] > .5 * good[-1][0]:
                crowded.add(tid)
            for i, c in enumerate(candidates):
                overlap = iou(box, c['nbox'])
                if overlap >= GATE_IOU:
                    scored.append((1 + overlap, tid, i, 'iou'))
                    continue
                bw, bh = box[2] - box[0], box[3] - box[1]
                cw, ch = c['nbox'][2] - c['nbox'][0], c['nbox'][3] - c['nbox'][1]
                gate = .6 * max(bw, bh, 1e-6)
                distance = np.hypot((box[0] + box[2] - c['nbox'][0] - c['nbox'][2]) / 2,
                                    (box[1] + box[3] - c['nbox'][1] - c['nbox'][3]) / 2)
                ratio = (cw * ch) / max(bw * bh, 1e-9)
                if distance < gate and .5 <= ratio <= 2:
                    scored.append((1 - distance / gate, tid, i, 'centre'))
        pairs, used_t, used_c = {}, set(), set()
        for _, tid, i, how in sorted(scored, reverse=True):
            if tid not in used_t and i not in used_c:
                pairs[i] = (tid, how)
                used_t.add(tid)
                used_c.add(i)
        return pairs, crowded

    @staticmethod
    def _prepare(candidates, size):
        return [dict(c, nbox=normalized(c['box'], size)) for c in candidates]

    def _moved_boxes(self, motion, shape):
        if motion is None:
            return {tid: t.box for tid, t in self.tracks.items()}
        return {tid: warp_box(t.box, motion, shape) for tid, t in self.tracks.items()}

    def update(self, candidates, received, size, motion=None, shape=None):
        """One discovery pass. candidates: merged detector output in pixels of an image of `size`."""
        with self.lock:
            if self.last_pass is not None and received <= self.last_pass:
                return {}
            prepared = self._prepare(candidates, size)
            boxes = self._moved_boxes(motion, shape)
            for tid, box in boxes.items():
                self.tracks[tid].box = box
            pairs, crowded = self._pairs(prepared, boxes)
            assigned = {}
            for i, c in enumerate(prepared):
                if i in pairs:
                    tid, how = pairs[i]
                    t = self.tracks[tid]
                    # Missed by real passes, not merely a pause in discovery.
                    gone = t.misses >= 2 and received - t.last_seen > self.present_s
                    if how != 'iou' or tid in crowded or gone:
                        # A jump, a crowded match or a long absence (it may have been swapped while hidden) can
                        # change identity: the old verification no longer holds.
                        t.version += 1
                        if t.status == VERIFIED:
                            t.status, t.label, t.note = UNVERIFIED, '', ('reappeared; re-check' if gone
                                                                         else 'moved or crowded; re-check')
                    t.box, t.names, t.score, t.last_seen = c['nbox'], list(c['names']), c['score'], received
                    t.hits += 1
                    t.misses = 0
                else:
                    t = Track(self.next_id, c['nbox'], list(c['names']), c['score'], received, received)
                    self.tracks[t.id] = t
                    self.next_id += 1
                t.name_counts.update(c['names'])
                assigned[i] = t.id
            for tid in set(self.tracks) - set(assigned.values()):
                self.tracks[tid].misses += 1
            for tid in [tid for tid, t in self.tracks.items() if received - t.last_seen > self.max_missing_s]:
                del self.tracks[tid]
            self.last_pass = received
            return assigned

    def match(self, candidates, size, motion=None, shape=None):
        """Read-only association of a detection on another frame (the request frame). {candidate index: track id}."""
        with self.lock:
            prepared = self._prepare(candidates, size)
            pairs, crowded = self._pairs(prepared, self._moved_boxes(motion, shape))
            return {i: tid for i, (tid, how) in pairs.items() if how == 'iou' and tid not in crowded}

    def present(self):
        with self.lock:
            if self.last_pass is None:
                return []
            return [t for t in self.tracks.values() if self.last_pass - t.last_seen <= self.present_s]

    def visible(self):
        """Objects detected in the latest pass."""
        with self.lock:
            return [t for t in self.tracks.values() if t.last_seen == self.last_pass]

    def needs_check(self, ids):
        with self.lock:
            return [self.tracks[i] for i in ids if i in self.tracks and self.tracks[i].hits >= self.stable_hits
                    and self.tracks[i].status != VERIFIED and self.tracks[i].checked_version != self.tracks[i].version
                    and not self.tracks[i].checking]

    def mark_checking(self, snapshot):
        with self.lock:
            for tid, (_, version) in snapshot.items():
                if tid in self.tracks and self.tracks[tid].version == version:
                    self.tracks[tid].checking = True

    def apply_verification(self, snapshot, result, now=None):
        """snapshot: {track id: (normalized box, version)} detected on the frame Gemini saw. Returns counts."""
        with self.lock:
            for tid in snapshot:
                if tid in self.tracks:
                    self.tracks[tid].checking = False
            if not result.get('provider', {}).get('ok'):
                return dict(verified=0, unconfirmed=0, error=True)
            controls = [c for c in (result.get('screenModel') or {}).get('controls', [])
                        if c.get('label') and selector.region_is_valid(c.get('region'))]
            scored = []
            for tid, (box, _) in snapshot.items():
                for ci, control in enumerate(controls):
                    overlap = iou(box, hybrid.pixels(control['region'], (1, 1)))
                    if overlap >= hybrid.AGREE_IOU:
                        scored.append((overlap, tid, ci))
            matched, used = {}, set()
            for overlap, tid, ci in sorted(scored, reverse=True):
                if tid not in matched and ci not in used:
                    matched[tid] = ci
                    used.add(ci)
            verified = unconfirmed = 0
            for tid, (_, version) in snapshot.items():
                t = self.tracks.get(tid)
                if t is None or t.version != version:
                    continue  # changed while Gemini was looking; checked again later
                t.checked_version = version
                if tid in matched:
                    control = controls[matched[tid]]
                    t.status, t.label, t.legibility, t.note = VERIFIED, control['label'], control.get('legibility', ''), ''
                    verified += 1
                else:
                    t.note = 'Gemini did not confirm'
                    unconfirmed += 1
            self.gemini_only = [c['label'] for ci, c in enumerate(controls) if ci not in used]
            return dict(verified=verified, unconfirmed=unconfirmed, error=False)

    def lookup(self, request):
        """(kind, track, reason). kind 'VERIFIED' or 'GENERIC'; None means: use the HYBRID request flow."""
        with self.lock:
            present = self.present()
            if not present:
                return None, None, 'inventory empty'
            verified = [t for t in present if t.status == VERIFIED]
            model = {'frameId': 'inventory', 'coordinateSystem': selector.SUPPORTED_COORDINATES,
                     'availability': 'available',
                     'controls': [{'id': f'obj-{t.id}', 'label': t.label, 'legibility': t.legibility or 'clear',
                                   'type': 'other', 'region': hybrid.region(t.box, (1, 1))} for t in verified]}
            selection = object_select.select(request, model) if verified else {'status': 'NOT_FOUND'}
            if selection.get('status') == 'SELECTED':
                t = next(t for t in verified if f'obj-{t.id}' == selection['target']['targetId'])
                # Similar objects Gemini has not yet seen in their current form; one it checked and did not list
                # (not a separate object to Gemini) has already been told apart.
                similar = [u for u in present if u.status != VERIFIED and u.checked_version != u.version
                           and set(u.name_counts) & set(t.name_counts)]
                if similar:
                    return None, t, (f"#{t.id} verified, but similar unverified object(s) "
                                     f"{', '.join('#' + str(u.id) for u in similar)}; Gemini must compare them")
                return 'VERIFIED', t, f'#{t.id} verified as "{t.label}"'
            if selection.get('status') == 'AMBIGUOUS':
                return None, None, f"{len(selection.get('candidateIds') or [])} verified objects match"
            if not self.labels_trusted:
                return None, None, 'no verified match (prompt-free labels are never trusted)'
            candidates = [dict(id=t.id, names=list(t.names), score=t.score, box=t.box) for t in present]
            chosen, reason = hybrid.local_choice(request, candidates)
            if chosen is not None:
                return 'GENERIC', self.tracks[chosen['id']], f"#{chosen['id']} {reason}"
            return None, None, f'no verified match; {reason}'


class PromptFreeDetector:
    """YOLOE-26n prompt-free (AGPL-3.0, 4585 built-in classes). Same detect() shape as LocalDetector; labels are hints."""
    def __init__(self, weights=YOLOE_PF, conf=.2, imgsz=640, background=True):
        self.weights, self.conf, self.imgsz = Path(weights), conf, imgsz
        self.model = None
        self.error = ''
        self.load_s = None
        self.ready = threading.Event()
        self.lock = threading.Lock()
        if background:
            threading.Thread(target=self._load, daemon=True, name='r3-yoloe-pf').start()
        else:
            self._load()

    def _load(self):
        started = time.perf_counter()
        try:
            if not self.weights.exists():
                raise FileNotFoundError(self.weights.name)
            from ultralytics import YOLO
            model = YOLO(str(self.weights))
            model.predict(np.zeros((480, 640, 3), np.uint8), imgsz=self.imgsz, conf=self.conf, verbose=False,
                          device='cpu')
            self.model = model
            self.load_s = time.perf_counter() - started
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'[:200]
        finally:
            self.ready.set()

    @property
    def status(self):
        if not self.ready.is_set():
            return 'LOADING'
        return f'READY ({self.load_s:.1f}s load)' if self.model is not None else f'UNAVAILABLE ({self.error})'

    def detect(self, image, phrases=()):
        if self.model is None:
            return None, 0.
        rgb = np.asarray(image.convert('RGB'))
        with self.lock:
            started = time.perf_counter()
            result = self.model.predict(rgb[..., ::-1], imgsz=self.imgsz, conf=self.conf, verbose=False,
                                        device='cpu')[0]
            ms = (time.perf_counter() - started) * 1000
        found = []
        if result.boxes is not None:
            for xyxy, score, cls in zip(result.boxes.xyxy.cpu().numpy(), result.boxes.conf.cpu().numpy(),
                                        result.boxes.cls.cpu().numpy()):
                found.append(dict(name=result.names[int(cls)], score=float(score), box=tuple(map(float, xyxy))))
        return merge(found), ms


class AllObjects:
    """Runtime owner: discovery thread, verification scheduling (called from the tracking loop) and request choice."""
    def __init__(self, args, detector, client, budget, output, stream_id, health=lambda: None, log=print,
                 start=True):
        self.args, self.client, self.budget, self.output, self.stream_id = args, client, budget, Path(output), stream_id
        self.source_name = getattr(args, 'discovery_source', 'world')
        self.detector = PromptFreeDetector() if self.source_name == 'yoloe' else detector
        self.inventory = Inventory(labels_trusted=self.source_name != 'yoloe')
        self.interval = float(getattr(args, 'discovery_interval', 1.))
        self.conf = float(getattr(args, 'discovery_conf', .25))
        self.verify_interval = float(getattr(args, 'verify_interval', 15.))
        self.reserve = int(getattr(args, 'verify_reserve', 5))
        self.busy_ms = float(getattr(args, 'discovery_busy_ms', 120.))
        self.duty = .35  # discovery may use at most about this share of wall time
        self.health, self.log = health, log
        self.frame = None
        self.prev = None  # (received, grey) of the latest discovery pass
        self.snapshot = None  # latest pass: received, raw, info, {track id: (box, version)}
        self.verifying = False
        self.last_verify = None
        self.verify_note = 'waiting for stable objects'
        self.passes = 0
        self.last_ms = None
        self.skips = Counter()
        self.pass_times = []
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name='r3-discovery')
        if start:
            self.thread.start()

    # ---- discovery (background thread) -------------------------------------------------------------------------
    def offer(self, packet, raw, info):
        self.frame = (packet, raw, info)

    def _skip(self, why):
        self.skips[why] += 1

    def _run(self):
        threads = int(getattr(self.args, 'discovery_threads', 0) or 0)
        if threads > 0:
            try:
                import torch
                # Process-wide: leaves cores for the camera loop, MediaPipe and ViTTrack.
                torch.set_num_threads(threads)
            except ImportError:
                pass
        wait = self.interval
        while not self.stopped.wait(wait):
            wait = self.interval
            try:
                wait = self.discover_once()
            except Exception as exc:
                self._skip(f'error {type(exc).__name__}')
                wait = 1.

    def discover_once(self, now=None):
        """One adaptive discovery step. Returns the wait before the next one."""
        item = self.frame
        if item is None or self.detector is None or getattr(self.detector, 'model', None) is None:
            return self.interval
        packet, raw, info = item
        received = packet.stamp.received_monotonic_s
        now = time.monotonic() if now is None else now
        if (self.prev is not None and received <= self.prev[0]) or not 0 <= now - received <= .3:
            self._skip('no fresh frame')
            return min(self.interval, .1)
        if self.detector.lock.locked():
            self._skip('detector busy with a request')
            return self.interval / 2
        health = self.health()
        if health is not None and health > self.busy_ms:
            # The tracking loop is slow (CPU busy): camera, hands and audio come first.
            self._skip('tracking loop busy')
            return min(4., self.interval * 2)
        candidates, ms = self.detector.detect(packet.image, VOCABULARY)
        candidates = [c for c in candidates or [] if c['score'] >= self.conf]
        gray = small_gray(packet.image)
        motion = camera_motion(self.prev[1] if self.prev else None, gray)
        self.inventory.update(candidates, received, packet.image.size, motion, gray.shape)
        self.prev = (received, gray)
        with self.inventory.lock:
            boxes = {t.id: (t.box, t.version) for t in self.inventory.visible()}
        self.snapshot = dict(received=received, raw=raw, info=dict(info), boxes=boxes)
        self.passes += 1
        self.last_ms = ms
        self.pass_times = [t for t in self.pass_times if now - t < 10] + [now]
        return max(self.interval, ms / 1000 * (1 / self.duty - 1))

    @property
    def rate(self):
        times = self.pass_times
        return (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 and times[-1] > times[0] else 0.

    # ---- Gemini verification (scheduled from the tracking loop, so the budget stays single-threaded) ----------
    def maybe_verify(self, now, session, start=None):
        if self.verifying or self.client is None:
            return False
        if self.last_verify is not None and now - self.last_verify < self.verify_interval:
            return False
        if session is not None and session.inflight is not None:
            self.verify_note = 'waiting: user request in progress'
            return False
        snapshot = self.snapshot
        if snapshot is None or now - snapshot['received'] > 1.:
            return False
        pending = self.inventory.needs_check(snapshot['boxes'])
        if not pending:
            self.verify_note = 'idle: nothing new or changed'
            return False
        budget = self.budget
        while budget.times and now - budget.times[0] >= 60:
            budget.times.popleft()
        if budget.total >= budget.limit - self.reserve:
            self.verify_note = f'paused: last {self.reserve} API requests kept for Find'
            return False
        if len(budget.times) >= budget.per_minute - 1:
            self.verify_note = 'paused: per-minute API limit'
            return False
        allowed, reason = budget.take(now)
        if not allowed:
            self.verify_note = f'paused: {reason}'
            return False
        self.last_verify = now
        self.verifying = True
        self.inventory.mark_checking(snapshot['boxes'])
        self.verify_note = f'Gemini checking {len(pending)} new/changed object(s)'
        (start or (lambda: threading.Thread(target=self._verify, args=(snapshot,), daemon=True,
                                            name='r3-inventory-verify').start()))()
        return True

    def _verify(self, snapshot):
        from .live_camera import process_capture
        started = time.monotonic()
        try:
            client = copy.copy(self.client)
            client.prompt = VERIFY_PROMPT
            frame_id = 'inventory-' + uuid.uuid4().hex
            directory = self.output / frame_id
            metadata = dict(snapshot['info'], frameID=frame_id, snapshotUTCms=int(time.time() * 1000),
                            receivedMonotonic=snapshot['received'], snapshotMonotonic=time.monotonic(),
                            desktopROI=None, sourceKind='REC3D' if getattr(self.args, 'host', None) else 'video',
                            purpose='ALL OBJECTS inventory verification')
            process_capture(snapshot['raw'], getattr(self.args, 'rotation', 0), 'all visible objects', metadata,
                            client, directory, fast=True)
            result = json.loads((directory / 'result.json').read_text(encoding='utf-8'))
        except Exception as exc:
            result = {'provider': {'ok': False, 'error': {'kind': f'LOCAL_PROCESSING_ERROR_{type(exc).__name__}'}}}
        counts = self.inventory.apply_verification(snapshot['boxes'], result)
        seconds = time.monotonic() - started
        self.verify_note = ('Gemini check failed: ' + str((result.get('provider', {}).get('error') or {}).get('kind'))
                            if counts['error'] else
                            f"last check {seconds:.1f}s: {counts['verified']} verified, {counts['unconfirmed']} not confirmed")
        self.log('INVENTORY_VERIFIED', seconds=round(seconds, 2), objects=len(snapshot['boxes']), **counts)
        self.verifying = False

    # ---- request ---------------------------------------------------------------------------------------------
    def choose(self, job):
        """Inventory answer for a request on job.image. dict(kind, track, candidate, candidates, ms, reason)."""
        from .visual_tracker import working_image
        kind, track, reason = self.inventory.lookup(job.query)
        out = dict(kind=None, track=track, candidate=None, candidates=[], ms=0., reason=reason)
        if kind is None or self.detector is None or getattr(self.detector, 'model', None) is None:
            return out
        image = working_image(job.image, 640)
        candidates, ms = self.detector.detect(image, VOCABULARY)
        candidates = [c for c in candidates or [] if c['score'] >= self.conf]
        gray = small_gray(image)
        prev = self.prev
        motion = camera_motion(prev[1], gray) if prev is not None and job.received != prev[0] else None
        ids = self.inventory.match(candidates, image.size, motion, gray.shape)
        sx, sy = job.image.width / image.width, job.image.height / image.height
        # Boxes in the pixels of job.image (the request frame), named with the inventory IDs.
        current = [dict(c, id=ids.get(i, -1 - i), box=(c['box'][0] * sx, c['box'][1] * sy, c['box'][2] * sx,
                                                         c['box'][3] * sy)) for i, c in enumerate(candidates)]
        out.update(candidates=current, ms=ms)
        chosen = next((c for c in current if c['id'] == track.id), None)
        if chosen is None:
            out['reason'] = f'#{track.id} not detected again on the request frame'
            return out
        if kind == VERIFIED and track.status != VERIFIED:
            out['reason'] = f'#{track.id} verification no longer holds'
            return out
        if kind == 'GENERIC':
            again, why = hybrid.local_choice(job.query, current)
            if again is None or again['id'] != track.id:
                out['reason'] = f'#{track.id} no longer unique on the request frame ({why})'
                return out
        out.update(kind=kind, candidate=chosen)
        return out

    # ---- display -----------------------------------------------------------------------------------------------
    def draw(self, display, now):
        """Thin boxes for objects of the latest pass; dashed with their age once older than 300 ms (not live)."""
        last = self.inventory.last_pass
        if last is None:
            return
        age = now - last
        draw = ImageDraw.Draw(display)
        w, h = display.size
        for t in self.inventory.visible():
            color = (0, 200, 255) if t.status == VERIFIED else (235, 235, 235)
            x0, y0, x1, y1 = t.box[0] * w, t.box[1] * h, t.box[2] * w, t.box[3] * h
            corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            if age <= .3:
                draw.line(corners + corners[:1], fill=color, width=2)
            else:
                for a, b in zip(corners, corners[1:] + corners[:1]):
                    for k in range(0, 16, 2):
                        draw.line([(a[0] + (b[0] - a[0]) * k / 16, a[1] + (b[1] - a[1]) * k / 16),
                                   (a[0] + (b[0] - a[0]) * (k + 1) / 16, a[1] + (b[1] - a[1]) * (k + 1) / 16)],
                                  fill=color, width=1)
            caption = f'#{t.id} {t.shown_label()} {t.score:.2f} {t.shown_status()}'
            if age > .3:
                caption += f' ({age * 1000:.0f} ms old)'
            draw.rectangle((x0, max(0., y0 - 12), x0 + 6 * len(caption), max(0., y0 - 12) + 11), fill=(20, 20, 20))
            draw.text((x0 + 1, max(0., y0 - 12)), caption, fill=color)

    def summary(self):
        visible = self.inventory.visible()
        verified = sum(t.status == VERIFIED for t in visible)
        skips = ', '.join(f'{n} {why}' for why, n in self.skips.most_common(2))
        text = (f'ALL OBJECTS ({self.source_name} {getattr(self.detector, "status", "UNAVAILABLE")}): '
                f'{len(visible)} objects, {verified} verified | discovery {self.rate:.1f} Hz'
                + (f', {self.last_ms:.0f} ms' if self.last_ms is not None else '')
                + (f', skipped {skips}' if skips else '') + f' | verify: {self.verify_note}')
        if self.inventory.gemini_only:
            text += ' | Gemini also saw (no live box): ' + ', '.join(self.inventory.gemini_only[:5])
        return text

    def record(self):
        visible = self.inventory.visible()
        return dict(objects=len(visible), verified=sum(t.status == VERIFIED for t in visible), passes=self.passes,
                    rateHz=round(self.rate, 2), detectMs=None if self.last_ms is None else round(self.last_ms, 1),
                    skips=dict(self.skips))

    def close(self):
        self.stopped.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)


class SelectionLatency:
    """Request -> verified lock, for the per-request comparison of inventory selection with HYBRID / GEMINI_ONLY.

    A lock is verified when it comes from a VERIFIED inventory object or a Gemini choice; a local detector lock
    (YOLO or a generic inventory class) only once Gemini confirms it.
    """
    def __init__(self, path, detection, log=print):
        self.path, self.detection, self.log = Path(path), detection, log
        self.current = None

    def start(self, query, now):
        self.current = dict(request=query, detection=self.detection, source=None, choice=None, lockS=None,
                            confirmS=None, verifiedS=None, startedAt=now) if query.strip() else None

    def choice(self, text):
        if self.current is not None:
            self.current['choice'] = text

    def confirmed(self, now):
        if self.current is not None and self.current['confirmS'] is None:
            self.current['confirmS'] = round(now - self.current['startedAt'], 3)

    def update(self, session):
        c = self.current
        if c is None or c['verifiedS'] is not None or getattr(session, 'time_to_lock', None) is None:
            return None
        c['lockS'] = round(session.time_to_lock, 3)
        c['source'] = session.source
        local = not (session.source.startswith('GEMINI') or 'VERIFIED' in session.source)
        if local and c['confirmS'] is None:
            return None
        c['verifiedS'] = max(c['lockS'], c['confirmS'] or 0.)
        record = {k: v for k, v in c.items() if k != 'startedAt'}
        self.log('SELECTION', json.dumps(record))
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open('a', encoding='utf-8') as out:
                out.write(json.dumps(dict(record, t=round(time.monotonic(), 3))) + '\n')
        except OSError:
            pass
        return record
