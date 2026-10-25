"""TrackingSession variant for ObjectLockTracker: SEARCHING, IDENTIFYING, LOCKED, UNCERTAIN, TARGET LOST, REACQUIRING.

Gemini identifies once; local tracking keeps searching while any later re-identification runs.
A lock is never restored from an old Gemini box: only a current-frame match confirmed on the next frame.
"""
import time

from .object_lock import HIDDEN, LOCKED, LOST, REACQUIRING, UNCERTAIN, LockMatch, ObjectLockTracker
from .tracking_session import TrackingSession


class LockSession(TrackingSession):
    def __init__(self, budget=None, reacquire_after=8., reference_ttl=600., tracker_factory=ObjectLockTracker,
                 max_gap_s=1.5):
        super().__init__(budget, reacquire_after, reference_ttl, tracker_factory=tracker_factory)
        self.max_gap_s = max_gap_s
        self.last_match = LockMatch(None, 'Not acquired')

    def find(self, query):
        self.needs_clarification = False
        self.find_at = time.monotonic()
        self.time_to_lock = None
        self.source = ''
        self.user_request = bool(query.strip())
        super().find(query)

    def job(self, image, received, now):
        if getattr(self, 'needs_clarification', False):
            return None
        tracker = self.tracker
        last = self.budget.last
        if getattr(self, 'user_request', False):
            # The user's own Find is answered at once; the cooldown only paces automatic re-identification.
            self.budget.last = None
        job = super().job(image, received, now)
        if job is None:
            self.budget.last = last if self.budget.last is None else self.budget.last
        else:
            self.user_request = False
        if job is not None and tracker is not None:
            self.tracker = tracker
            self.detail = 'Gemini re-identifying; local search continues; no box until a current match'
        return job

    def complete(self, job, result, now, prepared_tracker=None):
        if self.inflight is not job:
            return
        if result.get('mode') == 'HYBRID_VERIFY':
            self.inflight = None
            if job.generation != self.generation or job.epoch != self.epoch or not self.active:
                return
            seconds = result.get('timingMs', {}).get('provider', 0) / 1000
            if result.get('agrees') is True:
                self.last_identification += f' | Gemini confirmed ({seconds:.1f}s)'
            elif result.get('agrees') is None:
                self.last_identification += ' | Gemini check unavailable; local lock unconfirmed'
            else:
                self.tracker = None
                self.needs_clarification = True
                self.state = 'UNCERTAIN'
                self.lost_at, self.hits = now, 0
                self.detail = (f"UNCERTAIN: local detector and Gemini disagree ({result.get('detail')}); box removed"
                               ' | make the request more specific and click Find & Track')
            return
        if self.inflight is job and self.state in (LOCKED, UNCERTAIN, HIDDEN) and job.generation == self.generation:
            if result.get('mode') != 'HYBRID' or result.get('providerPending') is False:
                self.inflight = None
            selection = result.get('selection', {})
            latency = result.get('timingMs', {}).get('provider', 0) / 1000
            self.last_identification = f"{selection.get('status')} ({latency:.1f}s), ignored: already locked locally"
            return
        current = self.inflight is job and job.generation == self.generation
        self.source = result.get('detectionSource') or 'GEMINI'
        if result.get('mode') == 'HYBRID':
            self._complete_hybrid(job, result, now)
        else:
            super().complete(job, result, now, prepared_tracker)
        selection = result.get('selection', {})
        if current and selection.get('status') == 'NOT_FOUND' and result.get('provider', {}).get('ok'):
            seen = [c.get('label', '') for c in (result.get('screenModel') or {}).get('controls', []) if c.get('label')]
            if seen:
                who = 'Gemini' if result.get('mode') != 'HYBRID' else 'Local detector'
                self.detail += f' | {who} saw: ' + ', '.join(seen[:8]) + ' | try one of these names'
        if current and selection.get('status') == 'AMBIGUOUS':
            labels = selection.get('candidateLabels') or []
            self.needs_clarification = True
            self.state = 'AMBIGUOUS'
            self.detail = (f"AMBIGUOUS: {len(labels) or 'several'} matches"
                           + (': ' + ', '.join(repr(l) for l in labels) if labels else '')
                           + ' | type a more specific request (for example colour or size) and click Find & Track')

    def _complete_hybrid(self, job, result, now):
        # Early local candidates retain the slot until Gemini verification finishes.
        # Inventory/demo results explicitly mark requests with no provider work as terminal.
        pending = result.get('providerPending') is not False
        if not pending:
            self.inflight = None
        if job.generation != self.generation or job.epoch != self.epoch or not self.active:
            return
        self.tracker = None
        self.mode = 'HYBRID'
        selection = result.get('selection', {})
        timing = result.get('timingMs', {})
        gemini_s = timing.get('provider', 0) / 1000
        self.last_identification = (f"{self.source} {selection.get('status')} | local {timing.get('local', 0):.0f} ms"
                                    + (f' | Gemini {gemini_s:.1f}s' if gemini_s else
                                       ' | waiting for Gemini check' if pending else ' | no Gemini call'))
        target = selection.get('target')
        if selection.get('status') != 'SELECTED' or not target:
            self.state = 'TARGET LOST'
            self.detail = f"{selection.get('status')} / {selection.get('reason')}"
            self.lost_at = now
            return
        model = result.get('screenModel', {})
        if (model.get('frameId') != job.frame_id or target.get('frameId') != job.frame_id
                or model.get('image', {}).get('widthPx') != job.image.width
                or model.get('image', {}).get('heightPx') != job.image.height):
            self.state, self.detail = 'ERROR', 'Hybrid reference-frame/geometry mismatch'
            self.lost_at = now
            return
        try:
            self.tracker = self.tracker_factory(job.image, target['imageRegion'])
        except (ValueError, KeyError):
            self.state, self.detail = 'TARGET LOST', 'Selected box has too little usable detail; move closer'
            self.lost_at = now
            return
        self.reference_at = now
        self.label = target['label']
        self.state = 'REACQUIRING'
        self.detail = f"{self.label} | {self.source}: {selection.get('reason')} | matching to the current frame"
        self.lost_at, self.hits = now, 0

    def update(self, image, received, now):
        self.last_match = LockMatch(None, 'No verified box')
        if not 0 <= now - received <= self.max_gap_s:
            self.state, self.detail = 'TARGET LOST', f'No new frame for over {self.max_gap_s:g} s (or invalid clock)'
            self.hits = 0
            if self.tracker is not None:
                self.tracker.reset_temporal()
            if self.lost_at is None:
                self.lost_at = now
            return self.last_match
        if self.last_received is not None and received <= self.last_received:
            self.hits = 0
            self.state, self.detail = 'TARGET LOST', 'Repeated or out-of-order frame cannot confirm tracking'
            return self.last_match
        if (self.last_received is not None and received - self.last_received > self.max_gap_s
                and self.tracker is not None):
            self.tracker.reset_temporal()
        self.last_received = received
        if self.tracker is None:
            return self.last_match
        if self.state not in (LOCKED, UNCERTAIN) and now - self.reference_at > self.reference_ttl:
            self.tracker = None
            self.state, self.detail = 'TARGET LOST', 'Reference expired; re-identification required'
            self.lost_at, self.hits = now, 0
            return self.last_match
        match = self.tracker.locate(image)
        if match.polygon is not None and match.state in (LOCKED, UNCERTAIN):
            self.state = match.state
            self.lost_at = None
            self.hits = 2
            if match.state == LOCKED and self.time_to_lock is None and getattr(self, 'find_at', None) is not None:
                self.time_to_lock = time.monotonic() - self.find_at
            self.detail = f'{self.label} | {match.state} | {match.reason}'
            self.last_match = match
            return match
        if match.polygon is not None and match.state == HIDDEN:
            self.state = HIDDEN
            self.lost_at = None
            self.hits = 2
            self.detail = f'{self.label} | HIDDEN | {match.reason}'
            self.last_match = match
            return match
        if match.state == REACQUIRING:
            self.state = 'REACQUIRING'
            self.hits = 1 if match.inliers else 0
        else:
            self.state = 'TARGET LOST'
            self.hits = 0
            self.last_loss = match.reason
        self.detail = f'{self.label} | {self.state} | {match.reason}'
        if self.lost_at is None:
            self.lost_at = now
        return match
