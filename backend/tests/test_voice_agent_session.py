"""SIMULATED transcript-to-coordinator session; no mic, camera or live providers."""
import collections
import queue
import threading
import time
from types import SimpleNamespace
import unittest

from r3_vision.orchestration import AgentResult, Coordinator
from r3_vision.voice_commands import VoiceCommands


class VoiceAgentSessionTests(unittest.TestCase):
    def test_wake_specialist_cancel_and_resume(self):
        voice = VoiceCommands.__new__(VoiceCommands)
        voice.commands = queue.Queue(maxsize=8)
        voice.transcript = collections.deque(maxlen=6)
        voice.transcript_confidence, voice.min_confidence = .35, .6
        voice.echo_gap_s, voice.quiet_until = .5, 0.
        voice.speaking = lambda: False
        voice.log = lambda *args, **kwargs: None
        voice.open_ended = True
        voice.listening, voice.listen_until, voice.idle_timeout_s = False, None, 30.
        voice.state_lock = threading.RLock()
        voice.wake_until, voice.last_heard = 0., ''
        calls, stops, spoken = [], [], []
        started, release, finished = threading.Event(), threading.Event(), threading.Event()

        def scene(query, context):
            calls.append(context)
            started.set()
            release.wait(2)
            finished.set()
            return AgentResult('scene-description', 'complete', 'SIMULATED scene.')

        router = SimpleNamespace(route=lambda *args: self.fail('Direct command called an LLM router'))
        coordinator = Coordinator(router, scene, scene, scene,
                                  lambda: stops.append(True), lambda *args: spoken.append(args))
        self.addCleanup(coordinator.close)
        self.addCleanup(release.set)

        def dispatch():
            while True:
                try:
                    command, argument, _ = voice.commands.get_nowait()
                except queue.Empty:
                    return
                if command in ('sleep', 'stop_tracking', 'quiet'):
                    coordinator.stop()
                elif command == 'describe':
                    coordinator.submit('Describe surroundings')
                elif command == 'request':
                    coordinator.submit(argument)

        self.assertEqual(voice.handle('Hey Vision, describe surroundings', .9, 10), 'request')
        dispatch()
        self.assertTrue(started.wait(1))
        self.assertTrue(calls[0].is_current())
        self.assertEqual(voice.handle('Thanks Vision', .9, 11), 'sleep')
        dispatch()
        self.assertFalse(calls[0].is_current())
        self.assertEqual(coordinator.snapshot()['status'], 'stopped')
        self.assertFalse(voice.listening)
        self.assertIsNone(voice.handle('Describe surroundings', .9, 12))
        dispatch()
        release.set()
        self.assertTrue(finished.wait(1))
        self.assertEqual(spoken, [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(voice.handle('Hey Vision, describe surroundings', .9, 13), 'request')
        dispatch()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and not spoken:
            threading.Event().wait(.005)
        self.assertEqual(len(calls), 2)
        self.assertEqual(spoken[0][0], 'SIMULATED scene.')
        self.assertTrue(voice.listening)
        self.assertGreaterEqual(len(stops), 3)
