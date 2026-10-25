"""One priority gate for existing laptop output. No model text becomes a movement cue.

The existing ClipPlayer remains the sole audio device owner. No speech backlog is
kept: interrupted or rejected conversation must be requested again.
"""
from __future__ import annotations

import threading


class PriorityAudio:
    def __init__(self, player):
        self.player = player
        self.lock = threading.RLock()
        self.priority = 4
        self.generation = 0
        self.guidance_enabled = True
        self.guidance_cancelled = None

    def __getattr__(self, key):
        return getattr(self.player, key)

    @property
    def muted(self):
        return getattr(self.player, 'muted', False)

    @muted.setter
    def muted(self, value):
        self.player.muted = value
        if value:
            self.interrupt_all()

    def enable_guidance(self, enabled, context=None):
        with self.lock:
            self.guidance_cancelled = getattr(context, 'cancelled', None)
            self.guidance_enabled = bool(enabled)
            if not enabled:
                self.stop()

    def play(self, cue):
        from .audio_guidance import PHRASES, CM_PHRASES
        priority = 1 if cue == 'TRACKING_LOST' else 2 if cue in PHRASES or cue in CM_PHRASES else 4
        with self.lock:
            if self.priority == 1 and not self.player.speaking() and getattr(self.player, 'wanted', None) is None:
                self.priority = 4
            if priority < 4 and (not self.guidance_enabled or
                                 (self.guidance_cancelled is not None and self.guidance_cancelled.is_set())):
                return False
            if priority > self.priority:
                return False
            self.generation += 1
            self.priority = priority
            self.player.play(cue)
            return True

    def say(self, text, name='SPEECH', context=None):
        if context is not None and not context.is_current():
            return False
        with self.lock:
            if self.priority < 4:
                # A loss announcement may finish, but an active guidance session
                # keeps conversation suppressed until its owner stops guidance.
                if self.priority == 1 and not self.player.speaking():
                    self.priority = 4
                else:
                    return False
            generation = self.generation

            def eligible():
                cancelled = getattr(context, 'cancelled', None)
                return (generation == self.generation and self.priority == 4
                        and not (cancelled is not None and cancelled.is_set()))

            return self.player.say(text, name=name, is_current=eligible)

    def play_parts(self, cues, name='SUMMARY'):
        with self.lock:
            if self.priority < 4:
                return False
            self.generation += 1
            return self.player.play_parts(cues, name=name)

    def stop(self):
        """Guidance reset must not cut an unrelated conversation every frame."""
        with self.lock:
            if self.priority < 4:
                self.generation += 1
                self.player.stop()
                self.priority = 4

    def interrupt_all(self):
        with self.lock:
            self.generation += 1
            self.priority = 4
            self.player.stop()

    def close(self):
        self.interrupt_all()
        self.player.close()
