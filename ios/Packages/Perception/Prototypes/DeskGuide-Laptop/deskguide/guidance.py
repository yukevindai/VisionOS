import time
from dataclasses import dataclass
import numpy as np

TOLERANCE_M = .025
HOLD_MS = 180
NAMES = (('RIGHT', 'LEFT'), ('DOWN', 'UP'), ('FORWARD', 'BACKWARD'))


@dataclass(frozen=True)
class GuidanceEvent:
    direction: str
    reason: str
    delta_m: tuple[float, float, float] | None = None
    distance_m: float | None = None
    expires_at: float | None = None
    sequence: int = 0


class GuidanceOutput:
    def __init__(self):
        self.axis = 0
        self.sequence = 0
        self.event = GuidanceEvent('STOP', 'unavailable')

    def update(self, delta=None, distance=None, deadline=None):
        self.sequence += 1
        if delta is None or distance is None:
            self.event = GuidanceEvent('STOP', 'unavailable', sequence=self.sequence)
            return
        delta = np.asarray(delta, float)
        now = time.monotonic()
        expires = now + HOLD_MS / 1000 if deadline is None else min(now + HOLD_MS / 1000, deadline)
        strongest = int(np.argmax(np.abs(delta)))
        if (self.event.direction == 'STOP' or abs(delta[self.axis]) < TOLERANCE_M
                or abs(delta[strongest]) > 1.25 * abs(delta[self.axis])):
            self.axis = strongest
        value = float(delta[self.axis])
        if abs(value) < TOLERANCE_M:
            direction, reason = 'STOP', 'within_tolerance'
        else:
            direction, reason = NAMES[self.axis][0 if value > 0 else 1], 'measured'
        self.event = GuidanceEvent(direction, reason, tuple(float(x) for x in delta),
                                   float(distance), expires, self.sequence)

    def current(self, now=None):
        event = self.event
        if event.expires_at is not None and (time.monotonic() if now is None else now) >= event.expires_at:
            return GuidanceEvent('STOP', 'expired', sequence=event.sequence)
        return event

    @property
    def status(self):
        return self.current().direction
