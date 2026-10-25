"""HYBRID must not instantiate or schedule the experimental inventory worker."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from r3_vision.live_tracking import TrackingWorker, arguments


class FreshSource:
    status = 'SIMULATED'

    def __init__(self, args):
        self.image = Image.new('RGB', (160, 120))

    def snapshot(self):
        return self.image, time.monotonic(), {'connectionEpoch': 1}

    def close(self):
        pass


class DiscoveryDefaultsTests(unittest.TestCase):
    def test_cli_defaults_to_hybrid_and_preserves_explicit_experiments(self):
        self.assertEqual(arguments(['--video', '0']).detection, 'hybrid')
        for mode in ('allobjects', 'gemini'):
            self.assertEqual(arguments(['--video', '0', '--detection', mode]).detection, mode)

    def test_launcher_default_and_explicit_allobjects_switch(self):
        launcher = (Path(__file__).resolve().parents[1] / 'start-live-tracking.ps1').read_text()
        self.assertIn("[string]$Detection = 'hybrid'", launcher)
        self.assertIn("if ($AllObjects) { $Detection = 'allobjects' }", launcher)

    def test_idle_hybrid_processes_frames_without_discovery_or_cloud_calls(self):
        detector = SimpleNamespace(model=object(), detect=Mock(), status='SIMULATED READY')
        client = Mock()
        with tempfile.TemporaryDirectory() as directory:
            args = arguments(['--video', 'SIMULATED', '--no-hands', '--no-audio', '--tracker', 'orb'])
            args.output = Path(directory)
            with patch('r3_vision.live_tracking.Source', FreshSource), \
                    patch('r3_vision.local_detector.LocalDetector', return_value=detector), \
                    patch('r3_vision.all_objects.AllObjects', side_effect=AssertionError('discovery constructed')):
                worker = TrackingWorker(args, client)
                try:
                    deadline = time.monotonic() + 2
                    while worker.latest_frame is None and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertIsNotNone(worker.latest_frame)
                    time.sleep(.15)
                    self.assertIsNone(worker.all_objects)
                    detector.detect.assert_not_called()
                    self.assertEqual(client.mock_calls, [])
                    forbidden = {'r3-discovery', 'r3-inventory-verify', 'r3-yoloe-pf'}
                    self.assertFalse(forbidden.intersection(t.name for t in threading.enumerate()))
                finally:
                    worker.close()


if __name__ == '__main__':
    unittest.main()
