"""Isolated numeric speech adapter for the audio owner's existing output path.

Player contract: busy, play(text, expires_monotonic), refresh(expires_monotonic),
stop(). The player must recheck expiry after async synthesis and during playback.
This adapter does not replace or open any audio device in the live preview.
"""
import math


class DistanceSpeech:
    def __init__(self, player, log=None, repeat_s=2.5, change_gap_s=1., distance_step_cm=2.):
        self.player, self.log = player, log or (lambda *args, **kwargs: None)
        self.repeat_s, self.change_gap_s, self.distance_step_cm = repeat_s, change_gap_s, distance_step_cm
        self.current, self.mode, self.reason = None, None, 'physical calibration required'
        self.spoken_at, self.last_valid_until = -math.inf, -math.inf
        self.last_axis = None
        self.last_cm = None
        self.next_axis = 0

    def reset(self):
        self.player.stop()
        self.current = self.last_axis = self.last_cm = None
        self.mode, self.reason = None, 'reset'
        self.next_axis = 0

    def update(self, readout, now, received):
        if (readout is None or readout.status != 'VALIDATED CAMERA RELATIVE' or readout.estimated
                or not readout.axes or not math.isfinite(now) or not 0 <= now - received <= .3
                or readout.valid_until_monotonic is None or now > readout.valid_until_monotonic):
            self.reason = readout.reason if readout is not None else 'no validated distances'
            self.reset()
            self.reason = readout.reason if readout is not None else 'no validated distances'
            return
        self.last_valid_until = min(received + .3, readout.valid_until_monotonic)
        active = [a for a in readout.axes if a.direction != 'ALIGNED']
        self.mode, self.reason = '3D CAMERA RELATIVE', readout.reason
        if not active:
            self.player.stop()
            self.current = None
            self.reason = 'within axis tolerances; no contact or grasp claim'
            return
        old = next((a for a in active if (a.axis, a.direction) == self.last_axis), None)
        if self.player.busy:
            if old is None or abs(old.cm - self.last_cm) >= self.distance_step_cm:
                self.player.stop()
                self.current = None
            else:
                self.player.refresh(self.last_valid_until)
                return
        if now - self.spoken_at < self.change_gap_s:
            return
        if old is not None and abs(old.cm - self.last_cm) < self.distance_step_cm and now - self.spoken_at < self.repeat_s:
            return
        # Read all required axes in turn; forward never disappears behind a single dominant axis.
        a = active[self.next_axis % len(active)]
        self.next_axis += 1
        self.current, self.last_axis, self.last_cm = a.speech, (a.axis, a.direction), a.cm
        self.spoken_at = now
        self.player.play(a.speech, self.last_valid_until)
        self.log('CUE_COMPUTED', cue=a.speech, mode=self.mode, reason=self.reason,
                 frameAgeMs=round((now - received) * 1000))

    def tick(self, now):
        if now > self.last_valid_until:
            self.player.stop()
            self.current, self.mode, self.reason = None, None, 'no fresh validated frame (300 ms limit)'

    def status(self):
        return f'AUDIO {self.current}' if self.current else f'AUDIO silent ({self.reason})'
