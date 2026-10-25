"""SIMULATED hand observations/audio; not a camera, depth or speaker acceptance test."""
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r3_vision.hand_setup import HandSetup


def observation(t, *, identity=1, point=(.5, .5), epoch=1):
    stamp = SimpleNamespace(received_monotonic_s=t, stream_id='simulated', connection_epoch=epoch,
                            image_width=640, image_height=480, rotation_clockwise=0, mirrored=None)
    hand = SimpleNamespace(track_id=identity, index_tip_norm=point, index_tip_in_frame=True)
    return stamp, SimpleNamespace(status='TRACKING', primary=hand)


class HandSetupTests(unittest.TestCase):
    def observe(self, setup, t, **kwargs):
        setup.observe(*observation(t, **kwargs), t)

    def ready(self, setup):
        for t in [1., 1.25, 1.5]:
            self.observe(setup, t)
        self.assertTrue(setup.ready)

    def test_stable_hand_is_ready_without_target_or_depth(self):
        setup = HandSetup()
        self.ready(setup)
        self.assertIn('depth calibration separate', setup.status)
        self.assertIn('find an object', setup.pending)

    def test_repeat_buffer_and_stale_frames_never_complete_setup(self):
        setup = HandSetup()
        for now in [1., 1.1, 1.2, 1.5, 2.]:
            setup.observe(*observation(1.), now)
        self.assertFalse(setup.ready)
        self.assertEqual(setup.status, 'WAITING FOR HAND')

    def test_moving_hand_changed_identity_and_epoch_restart(self):
        for change in [{'point': (.8, .5)}, {'identity': 2}, {'epoch': 2}]:
            setup = HandSetup()
            self.observe(setup, 1.)
            self.observe(setup, 1.25)
            self.observe(setup, 1.5, **change)
            self.assertFalse(setup.ready)

    def test_lost_hand_revokes_readiness_and_pending_acknowledgment(self):
        setup = HandSetup()
        self.ready(setup)
        setup.observe(None, None, 1.6)
        self.assertFalse(setup.ready)
        self.assertIsNone(setup.pending)

    def test_camera_gap_revokes_readiness(self):
        setup = HandSetup()
        self.ready(setup)
        setup.expire(2.)
        self.assertFalse(setup.ready)

    def test_startup_speech_retries_until_player_ready(self):
        setup = HandSetup()
        player = Mock()
        player.speaking.return_value = False
        player.say.side_effect = [False, True]
        setup.speak(player)
        self.assertIsNotNone(setup.pending)
        setup.speak(player)
        self.assertIsNone(setup.pending)
        setup.speak(player)
        self.assertEqual(player.say.call_count, 2)

    def test_stop_cancels_synthesis_and_prevents_retry(self):
        setup = HandSetup()
        player = Mock()
        player.speaking.return_value = False
        player.say.return_value = True
        setup.speak(player)
        eligible = player.say.call_args.kwargs['is_current']
        self.assertTrue(eligible())
        setup.cancel()
        self.assertFalse(eligible())
        setup.speak(player)
        self.assertEqual(player.say.call_count, 1)

    def test_lost_hand_cancels_priority_wrapped_acknowledgment(self):
        setup = HandSetup()
        self.ready(setup)
        player = Mock()
        player.speaking.return_value = False
        player.say.return_value = True
        setup.speak(player, priority_wrapper=True)
        context = player.say.call_args.kwargs['context']
        self.assertTrue(context.is_current())
        setup.expire(2.)
        self.assertTrue(context.cancelled.is_set())

    def test_failure_has_actionable_speech_without_ready_claim(self):
        setup = HandSetup()
        setup.fail()
        self.assertFalse(setup.ready)
        self.assertIn('dependencies and model', setup.pending)


class HandSetupWorkerTests(unittest.TestCase):
    def test_idle_worker_speaks_and_checks_hand_before_any_target_request(self):
        from PIL import Image
        from r3_vision.live_tracking import TrackingWorker, arguments
        from r3_vision.hand_bridge import HandOverlay

        class FreshSource:
            status = 'SIMULATED source'
            connected = True

            def __init__(self, args):
                pass

            def snapshot(self):
                return (Image.new('RGB', (640, 480)), time.monotonic(), {'connectionEpoch': 1}) if self.connected else None

            def close(self):
                pass

        class FakeBridge:
            def process(self, packet):
                _, result = observation(packet.stamp.received_monotonic_s)
                result.primary.index_tip_px = (320, 240)
                result.hands = (result.primary,)
                return HandOverlay(packet.stamp, result)

            def draw(self, image, *args):
                return image

            def close(self):
                pass

        player, client = Mock(), Mock()
        player.speaking.return_value = False
        player.say.return_value = True
        with tempfile.TemporaryDirectory() as directory, \
                patch('r3_vision.live_tracking.Source', FreshSource), \
                patch('r3_vision.hand_bridge.HandBridge', FakeBridge), \
                patch('r3_vision.audio_guidance.ClipPlayer', return_value=player), \
                patch('r3_vision.cartesia_speech.CartesiaSpeech.from_env', return_value=None):
            args = arguments(['--video', 'SIMULATED', '--detection', 'gemini', '--audio', '--tracker', 'orb'])
            args.output = Path(directory)
            worker = TrackingWorker(args, client)
            try:
                deadline = time.monotonic() + 3
                while not worker.hand_setup.announced_ready and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(worker.hand_setup.ready, worker.hand_status)
                spoken = [call.args[0] for call in player.say.call_args_list]
                self.assertTrue(any('show your hand' in text for text in spoken), spoken)
                self.assertTrue(any('Hand tracking is ready' in text for text in spoken), spoken)
                self.assertIn('HAND READY', worker.hand_status)
                self.assertEqual(client.mock_calls, [])
                self.assertIsNone(worker.session.tracker)
                worker.source.connected = False
                deadline = time.monotonic() + 2
                while worker.hand_setup.ready and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertFalse(worker.hand_setup.ready)
                worker.commands.put((1, ''))
                deadline = time.monotonic() + 2
                while not worker.hand_setup.cancelled.is_set() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(worker.hand_setup.cancelled.is_set())
            finally:
                worker.close()


if __name__ == '__main__':
    unittest.main()
