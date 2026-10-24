"""Testable identification/tracking state and global API budget. No UI or sensor ownership."""
from collections import deque
from dataclasses import dataclass
import math
import uuid

from PIL import Image

from .visual_tracker import ReferenceTracker, Match


class RequestBudget:
    def __init__(self, cooldown=20., per_minute=3, total=20):
        if not math.isfinite(cooldown) or cooldown < 0 or per_minute < 1 or total < 1:
            raise ValueError('Invalid API request budget')
        self.cooldown, self.per_minute, self.limit = cooldown, per_minute, total
        self.times = deque()
        self.total = 0
        self.last = None

    def take(self, now):
        while self.times and now-self.times[0] >= 60:
            self.times.popleft()
        if self.total >= self.limit:
            return False, 'Session API request limit reached; restart explicitly to reset budget'
        if self.last is not None and now-self.last < self.cooldown:
            return False, f'API cooldown: {self.cooldown-(now-self.last):.1f}s'
        if len(self.times) >= self.per_minute:
            return False, 'API requests/minute limit reached'
        self.times.append(now)
        self.last = now
        self.total += 1
        return True, ''


@dataclass
class IdentifyJob:
    generation: int
    frame_id: str
    image: Image.Image
    query: str
    received: float
    epoch: object


class TrackingSession:
    def __init__(self, budget=None, reacquire_after=2., reference_ttl=60., tracker_factory=ReferenceTracker):
        self.budget = budget or RequestBudget()
        self.reacquire_after, self.reference_ttl = reacquire_after, reference_ttl
        self.tracker_factory = tracker_factory
        self.generation = 0
        self.query = ''
        self.active = False
        self.inflight = None
        self.tracker = None
        self.state = 'SEARCHING'
        self.detail = 'Enter a target and click Find & Track'
        self.label = ''
        self.mode = ''
        self.epoch = None
        self.reference_at = 0
        self.lost_at = None
        self.hits = 0
        self.last_match = Match(None, 'Not acquired')
        self.last_received = None
        self.last_identification = ''
        self.last_loss = ''
        self.identify_started = 0.

    def describe(self, now):
        parts = [self.detail]
        if self.state == 'IDENTIFYING':
            parts.append(f'waiting {max(0, now-self.identify_started):.0f}s')
        if self.last_identification:
            parts.append('Last Gemini: '+self.last_identification)
        if self.last_loss:
            parts.append('Last tracking loss: '+self.last_loss)
        return ' | '.join(parts)

    def find(self, query):
        self.generation += 1
        self.query = query.strip()
        self.active = bool(self.query)
        self.tracker = None
        self.hits = 0
        self.label = ''
        self.mode = ''
        self.lost_at = None
        self.last_match = Match(None, 'New target request')
        self.last_received = None
        self.last_identification = ''
        self.last_loss = ''
        self.state = 'SEARCHING'
        self.detail = 'Waiting for a fresh frame' if self.active else 'Enter a nonempty target request'
        # Keep inflight occupied until the old job finishes. Its generation cannot be reused.

    def stop(self):
        self.find('')
        self.detail = 'Stopped; no tracking box'

    def source_epoch(self, epoch):
        if self.epoch is not None and epoch != self.epoch:
            self.generation += 1
            self.tracker = None
            self.hits = 0
            self.state = 'TARGET LOST'
            self.detail = 'Camera reconnected or geometry changed; re-identification required'
            self.last_match = Match(None, self.detail)
        self.epoch = epoch

    def job(self, image, received, now):
        if not self.active or self.inflight is not None or not 0 <= now-received <= .3:
            return None
        if self.tracker is not None and self.lost_at is None:
            return None
        if self.tracker is not None and self.hits > 0:
            # Let a promising local reacquisition confirm on the next distinct frame.
            return None
        if self.lost_at is not None and now-self.lost_at < self.reacquire_after:
            return None
        allowed, reason = self.budget.take(now)
        if not allowed:
            self.detail = self.detail.split(' | API:')[0] + ' | API: ' + reason
            return None
        job = IdentifyJob(self.generation, 'track-'+uuid.uuid4().hex, image.copy(), self.query, received, self.epoch)
        self.inflight = job
        self.identify_started = now
        self.tracker = None
        self.hits = 0
        self.state = 'IDENTIFYING'
        self.detail = 'Gemini processing reference; no box until current-frame verification'
        return job

    def complete(self, job, result, now, prepared_tracker=None):
        if self.inflight is not job:
            return
        self.inflight = None
        if job.generation != self.generation or job.epoch != self.epoch or not self.active:
            return
        self.tracker = None
        selection = result['selection']
        error = result.get('provider', {}).get('error') or {}
        self.mode = result.get('mode', '')
        latency = result.get('timingMs', {}).get('provider', 0)/1000
        self.last_identification = f"{error.get('kind') or selection.get('status')} ({latency:.1f}s)"
        if not result.get('provider', {}).get('ok'):
            self.state = 'ERROR'
            self.detail = f"{error.get('kind', 'PROVIDER_UNAVAILABLE')} | Gemini {latency:.2f}s"
            self.lost_at = now
            return
        target = selection.get('target')
        if selection.get('status') != 'SELECTED' or not target:
            self.state = 'TARGET LOST'
            self.detail = f"{selection.get('status')} / {selection.get('reason')} | Gemini {latency:.2f}s"
            self.lost_at = now
            return
        model = result['screenModel']
        expected_frame = job.frame_id if self.mode == 'REAL_API' else 'SIMULATED-'+job.frame_id
        if (model.get('frameId') != expected_frame or target.get('frameId') != expected_frame or
            model.get('coordinateSystem') != 'normalizedImageTopLeft' or
            model.get('image', {}).get('widthPx') != job.image.width or
            model.get('image', {}).get('heightPx') != job.image.height):
            self.state, self.detail = 'ERROR', 'Gemini reference-frame/geometry mismatch'
            self.lost_at = now
            return
        try:
            self.tracker = prepared_tracker or self.tracker_factory(job.image, target['imageRegion'])
        except (ValueError, KeyError):
            self.state, self.detail = 'TARGET LOST', 'Reference target/context has insufficient usable detail; move closer'
            self.lost_at = now
            return
        self.reference_at = now
        self.label = target['label']
        self.state, self.detail = 'REACQUIRING', f'{self.label} | Gemini {latency:.2f}s; matching old reference to current pixels'
        self.lost_at = now
        self.hits = 0

    def update(self, image, received, now):
        self.last_match = Match(None, 'No verified box')
        if not 0 <= now-received <= .3:
            self.state, self.detail = 'TARGET LOST', 'No fresh frame (local age >300ms or invalid clock)'
            self.hits = 0
            if hasattr(self.tracker, 'reset_temporal'):
                self.tracker.reset_temporal()
            if self.lost_at is None:
                self.lost_at = now
            return self.last_match
        if self.last_received is not None and received <= self.last_received:
            self.hits = 0
            self.state, self.detail = 'TARGET LOST', 'Repeated or out-of-order frame cannot confirm tracking'
            return self.last_match
        if self.last_received is not None and received-self.last_received > .5 and hasattr(self.tracker, 'reset_temporal'):
            self.tracker.reset_temporal()
        self.last_received = received
        if self.tracker is None:
            return self.last_match
        if now-self.reference_at > self.reference_ttl:
            self.tracker = None
            self.state, self.detail = 'TARGET LOST', 'Reference expired; re-identification required'
            self.lost_at = now
            self.hits = 0
            return self.last_match
        match = self.tracker.locate(image)
        if match.polygon is None:
            self.hits = 0
            self.state, self.detail = 'TARGET LOST', match.reason
            self.last_loss = match.reason
            if self.lost_at is None:
                self.lost_at = now
            return match
        self.hits += 1
        if self.hits < 2:
            self.state, self.detail = 'REACQUIRING', 'First geometric/appearance match; confirming next frame'
            return self.last_match
        self.state = 'TRACKING'
        method = getattr(self.tracker, 'last_method', 'ORB reference match')
        score = getattr(self.tracker, 'last_score', None)
        score_text = f' | tracker score {score:.2f}' if score is not None else ''
        self.detail = f'{self.label} | {method}{score_text} | {match.inliers} inliers | appearance correlation {match.correlation:.2f} (scores are not identity probabilities)'
        self.lost_at = None
        self.last_match = match
        return match
