"""Simulated integration gates; no physical hardware acceptance."""
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from r3_vision.audio_guidance import Evidence, GuidanceDecider
from r3_vision.live_tracking import TrackingWorker, arguments

class HardwareGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.worker = TrackingWorker.__new__(TrackingWorker)
        self.worker.hardware = Mock()
        self.worker.hardware_lock = threading.RLock()
        self.worker.hardware_decider = GuidanceDecider()
        self.worker.hardware_armed = True
        self.worker.session = SimpleNamespace(state='LOCKED')
        self.worker.agents = None
        self.worker.stopped = threading.Event()
        self.evidence = Evidence(0, True, (60, 40, 80, 60), (10, 50), (100, 100))
    def update(self, age=0, hand=True):
        self.worker.hardware_update(self.evidence, time.monotonic() - age, hand)
        return self.worker.hardware.submit.call_args.args[0]
    def test_local_direction_and_alignment(self):
        self.assertEqual(self.update(), 'right')
        self.evidence.tip = (70, 50)
        self.assertIsNone(self.update())
    def test_stale_missing_hand_and_target_never_move(self):
        self.assertIsNone(self.update(age=.301))
        self.assertIsNone(self.update(hand=False))
        self.worker.session.state = 'UNCERTAIN'
        self.assertIsNone(self.update())
    def test_stop_blocks_inflight_frame_until_new_task(self):
        self.worker.hardware_gate()
        self.worker.hardware.stop.assert_called_once()
        self.assertIsNone(self.update())
        self.worker.hardware_gate(True)
        self.assertEqual(self.update(), 'right')
    def test_cancelled_agent_context_blocks_output(self):
        self.worker.agents = SimpleNamespace(context=SimpleNamespace(is_current=lambda: False))
        self.assertIsNone(self.update())
    def test_depth_does_not_control_servos(self):
        self.evidence.tip = (70, 50)
        self.evidence.offset = SimpleNamespace(measured=True, delta_m=(1, 0, 1))
        self.assertIsNone(self.update())
    def test_optional_flags(self):
        self.assertFalse(arguments(['--video', '0']).arduino_ble)
        args = arguments(['--video', '0', '--arduino-ble', '--arduino-address', 'test'])
        self.assertTrue(args.arduino_ble)
        self.assertEqual(args.arduino_address, 'test')
