"""SIMULATED worker/audio checks; no microphone, provider or hardware acceptance."""
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r3_vision.live_tracking import TrackingWorker, arguments


class NoFrameSource:
    status = 'SIMULATED: no camera frame'

    def __init__(self, args):
        pass

    def snapshot(self):
        return None

    def close(self):
        pass


class FindAcknowledgmentTests(unittest.TestCase):
    def test_find_acknowledgment_follows_reset_and_stop_invalidates_it_without_guidance(self):
        trace = []
        spoken = threading.Event()
        player, client = Mock(), Mock()
        player.stop.side_effect = lambda: trace.append('player.stop')

        def say(text, **kwargs):
            if player.say.call_count == 1:
                trace.append('synthesis.busy')
                return False
            trace.append(('say', text, kwargs))
            spoken.set()
            return True

        player.say.side_effect = say
        with tempfile.TemporaryDirectory() as directory, \
                patch('r3_vision.live_tracking.Source', NoFrameSource), \
                patch('r3_vision.audio_guidance.ClipPlayer', return_value=player), \
                patch('r3_vision.cartesia_speech.CartesiaSpeech.from_env', return_value=None):
            args = arguments(['--video', 'SIMULATED', '--detection', 'gemini',
                              '--audio', '--no-hands', '--tracker', 'orb'])
            args.output = Path(directory)
            worker = TrackingWorker(args, client)
            original_reset = worker.audio.reset

            def reset():
                trace.append('audio.reset')
                original_reset()

            worker.audio.reset = reset
            try:
                worker.commands.put((1, 'Find the can'))
                self.assertTrue(spoken.wait(2), 'worker did not acknowledge the queued Find')
                speech_index = next(i for i, event in enumerate(trace) if isinstance(event, tuple))
                preceding = trace[:speech_index]
                self.assertIn('player.stop', preceding)
                self.assertIn('audio.reset', preceding)
                self.assertLess(preceding.index('player.stop'), max(i for i, event in enumerate(preceding)
                                                                      if event == 'audio.reset'))
                _, text, kwargs = trace[speech_index]
                self.assertIn('Looking for can', text)
                self.assertIn('synthesis.busy', preceding)
                self.assertEqual(kwargs['name'], 'FINDING')
                eligible = kwargs['is_current']
                self.assertTrue(eligible())

                # No current image exists: acknowledgment must not select a target,
                # call a provider, or release a movement cue.
                self.assertIsNone(worker.latest_frame)
                self.assertIsNone(worker.session.tracker)
                client.assert_not_called()
                self.assertEqual(client.mock_calls, [])
                player.play.assert_not_called()

                worker.commands.put((2, ''))
                deadline = time.monotonic() + 2
                while eligible() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertFalse(eligible(), 'Stop must invalidate delayed synthesis from the Find')
                self.assertEqual(player.say.call_count, 2, 'Stop must not create another Find acknowledgment')
                player.play.assert_not_called()
            finally:
                worker.close()


if __name__ == '__main__':
    unittest.main()
