"""Target-independent hand readiness. This does not validate depth or authorize motion."""
import math
import threading
from types import SimpleNamespace


class HandSetup:
    def __init__(self):
        self.status = 'WAITING FOR HAND'
        self.pending = 'Hand setup: show your hand to the camera and hold it steady. No object is needed yet.'
        self.cancelled = threading.Event()
        self.first = self.last = None
        self.identity = self.anchor = None
        self.samples = 0
        self.announced_ready = False
        self.ready = False
        self.speech_context = None

    def is_current(self):
        return not self.cancelled.is_set()

    def cancel(self):
        self.cancelled.set()
        if self.speech_context is not None:
            self.speech_context.cancelled.set()
        self.pending = None

    def expire(self, now):
        if self.last is not None and now - self.last > .3:
            self.observe(None, None, now)
            return True
        return False

    def fail(self):
        self.ready = False
        self.status = 'HAND SETUP UNAVAILABLE'
        self.pending = 'Hand tracking could not start. Check the hand tracking dependencies and model shown in the terminal.'

    def observe(self, stamp, result, now):
        primary = result.primary if result is not None else None
        valid = (result is not None and result.status == 'TRACKING' and primary is not None
                 and primary.index_tip_in_frame and 0 <= now - stamp.received_monotonic_s <= .3)
        if not valid:
            if self.ready:
                self.pending = None
                if self.speech_context is not None:
                    self.speech_context.cancelled.set()
            self.ready = False
            self.first = self.last = None
            self.samples = 0
            self.status = 'WAITING FOR HAND'
            return
        point = tuple(map(float, primary.index_tip_norm))
        if not all(math.isfinite(x) and 0 <= x <= 1 for x in point):
            self.observe(stamp, None, now)
            return
        identity = (stamp.stream_id, stamp.connection_epoch, stamp.image_width,
                    stamp.image_height, stamp.rotation_clockwise, stamp.mirrored, primary.track_id)
        received = stamp.received_monotonic_s
        restart = (identity != self.identity or self.last is None or received - self.last > .3
                   or received < self.last or math.dist(point, self.anchor or point) > .05)
        if restart:
            if self.ready:
                self.pending = None
                if self.speech_context is not None:
                    self.speech_context.cancelled.set()
            self.identity, self.anchor = identity, point
            self.first, self.samples = received, 0
            self.ready = False
        if self.last != received or restart:
            self.samples += 1
        self.last = received
        self.ready = self.samples >= 3 and received - self.first >= .5
        self.status = 'HAND READY (2D; depth calibration separate)' if self.ready else 'CHECKING HAND: hold steady'
        if self.ready and not self.announced_ready:
            self.pending = 'Hand tracking is ready. You can now ask me to find an object. Physical depth calibration is separate.'

    def speak(self, player, *, priority_wrapper=False):
        if self.cancelled.is_set() or not self.pending or player.speaking():
            return
        # Retry only until the sole player is initialized/available; no audio queue.
        context = SimpleNamespace(cancelled=threading.Event())
        context.is_current = lambda: self.is_current() and not context.cancelled.is_set()
        kwargs = {'context': context} if priority_wrapper else {'is_current': context.is_current}
        if player.say(self.pending, name='HAND_SETUP', **kwargs):
            self.speech_context = context
            if self.ready:
                self.announced_ready = True
            self.pending = None
