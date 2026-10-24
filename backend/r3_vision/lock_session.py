"""TrackingSession variant for ObjectLockTracker: SEARCHING, IDENTIFYING, LOCKED, UNCERTAIN, TARGET LOST, REACQUIRING.

Gemini identifies once; local tracking keeps searching while any later re-identification runs.
A lock is never restored from an old Gemini box: only a current-frame match confirmed on the next frame.
"""
from .object_lock import LOCKED, LOST, REACQUIRING, UNCERTAIN, LockMatch, ObjectLockTracker
from .tracking_session import TrackingSession


class LockSession(TrackingSession):
    def __init__(self, budget=None, reacquire_after=8., reference_ttl=600., tracker_factory=ObjectLockTracker,
                 max_gap_s=1.5):
        super().__init__(budget, reacquire_after, reference_ttl, tracker_factory=tracker_factory)
        self.max_gap_s = max_gap_s
        self.last_match = LockMatch(None, 'Not acquired')

    def find(self, query):
        self.needs_clarification = False
        super().find(query)

    def job(self, image, received, now):
        if getattr(self, 'needs_clarification', False):
            return None
        tracker = self.tracker
        job = super().job(image, received, now)
        if job is not None and tracker is not None:
            self.tracker = tracker
            self.detail = 'Gemini re-identifying; local search continues; no box until a current match'
        return job

    def complete(self, job, result, now, prepared_tracker=None):
        if self.inflight is job and self.state in (LOCKED, UNCERTAIN) and job.generation == self.generation:
            self.inflight = None
            selection = result.get('selection', {})
            latency = result.get('timingMs', {}).get('provider', 0) / 1000
            self.last_identification = f"{selection.get('status')} ({latency:.1f}s), ignored: already locked locally"
            return
        current = self.inflight is job and job.generation == self.generation
        super().complete(job, result, now, prepared_tracker)
        selection = result.get('selection', {})
        if current and selection.get('status') == 'NOT_FOUND' and result.get('provider', {}).get('ok'):
            seen = [c.get('label', '') for c in (result.get('screenModel') or {}).get('controls', []) if c.get('label')]
            if seen:
                self.detail += ' | Gemini saw: ' + ', '.join(seen[:8]) + ' | try one of these names'
        if current and selection.get('status') == 'AMBIGUOUS':
            labels = selection.get('candidateLabels') or []
            self.needs_clarification = True
            self.state = 'AMBIGUOUS'
            self.detail = (f"AMBIGUOUS: {len(labels) or 'several'} matches"
                           + (': ' + ', '.join(repr(l) for l in labels) if labels else '')
                           + ' | type a more specific request (for example colour or size) and click Find & Track')

    def update(self, image, received, now):
        self.last_match = LockMatch(None, 'No verified box')
        if not 0 <= now - received <= .3:
            self.state, self.detail = 'TARGET LOST', 'No fresh frame (local age >300ms or invalid clock)'
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
            self.detail = f'{self.label} | {match.state} | {match.reason}'
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
