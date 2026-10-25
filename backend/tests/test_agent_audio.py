"""Simulated output arbitration; no speaker, microphone or Windows API."""
import threading
import unittest
from r3_vision.agent_audio import PriorityAudio


class Player:
    def __init__(self):
        self.calls = []
        self.valid = None

    def play(self, cue): self.calls.append(('play', cue))
    def stop(self): self.calls.append(('stop',))
    def speaking(self): return False
    def say(self, text, **kwargs):
        self.valid = kwargs['is_current']
        self.calls.append(('say', text))
        return self.valid()


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.player = Player()
        self.audio = PriorityAudio(self.player)

    def test_guidance_preempts_conversation_and_blocks_overlap(self):
        self.assertTrue(self.audio.say('A chair is visible'))
        pending = self.player.valid
        self.audio.play('LEFT')
        self.assertFalse(pending())
        self.assertFalse(self.audio.say('A table is visible'))
        self.audio.stop()
        self.assertTrue(self.audio.say('A table is visible'))

    def test_idle_geometry_reset_keeps_conversation(self):
        self.audio.say('Scene description')
        self.audio.stop()
        self.assertEqual(self.player.calls, [('say', 'Scene description')])

    def test_stop_invalidates_synthesis_and_rejects_late_guidance(self):
        self.audio.say('Delayed response')
        pending = self.player.valid
        self.audio.enable_guidance(False)
        self.audio.interrupt_all()
        self.assertFalse(pending())
        self.assertFalse(self.audio.play('RIGHT'))
        self.assertEqual(self.player.calls[-1], ('stop',))

    def test_cancelled_context_cannot_publish_synthesized_audio(self):
        class Context:
            cancelled = threading.Event()
            def is_current(self): return not self.cancelled.is_set()
        context = Context()
        self.assertTrue(self.audio.say('Response', context=context))
        context.cancelled.set()
        self.assertFalse(self.player.valid())
        self.assertFalse(self.audio.say('Obsolete', context=context))


    def test_obsolete_context_cannot_reenable_guidance_after_stop(self):
        class Context:
            cancelled = threading.Event()
        context = Context()
        self.audio.enable_guidance(True, context=context)
        self.assertTrue(self.audio.play('LEFT'))
        context.cancelled.set()
        self.audio.interrupt_all()
        self.audio.enable_guidance(True, context=context)
        self.assertFalse(self.audio.play('RIGHT'))
        self.assertEqual(self.player.calls[-1], ('stop',))
