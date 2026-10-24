"""SIMULATED detector; tests the adapter without editing/loading Claude's real detector."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hand_tracking import HandTracker, HandFrame, TRACKING
from r3_vision.frame_packet import make_packet
from r3_vision.hand_bridge import HandBridge


class FakeDetector:
    def __init__(self):
        self.calls = []
    def detect(self, rgb, timestamp_ms):
        self.calls.append((rgb.copy(), timestamp_ms))
        if len(self.calls) > 1:
            return []
        points = np.float64([[150+i%5*5, 100+i//5*5] for i in range(21)])
        return [dict(points=points, label='Right', score=.9)]
    def close(self):
        pass


class HandBridgeTests(unittest.TestCase):
    def packet(self, rotation=0, received=1.):
        return make_packet(Image.new('RGB', (640, 480)), received,
                           {'sourceFrameID': 1, 'connectionEpoch': 1}, 'SIMULATED', rotation)

    def test_same_exact_rgb_frame_stamp_timestamp_and_geometry(self):
        detector = FakeDetector()
        bridge = HandBridge(HandTracker(detector=detector))
        packet = self.packet(90)
        hand = bridge.process(packet)
        self.assertEqual(hand.stamp, packet.stamp)
        self.assertEqual(hand.result.image_size, packet.image.size)
        self.assertEqual(hand.result.capture_timestamp_s, packet.stamp.received_monotonic_s)
        np.testing.assert_array_equal(detector.calls[0][0], np.array(packet.image))
        self.assertEqual(detector.calls[0][1], 1000)
        rendered = bridge.draw(packet.image, packet, hand, 1.1)
        self.assertNotEqual(rendered.tobytes(), packet.image.tobytes())
        bridge.close()

    def test_stale_mismatched_epoch_and_timestamp_never_overlay(self):
        bridge = HandBridge(HandTracker(detector=FakeDetector()))
        packet = self.packet()
        hand = bridge.process(packet)
        for other in [replace(packet, stamp=replace(packet.stamp, connection_epoch=2)),
                      replace(packet, stamp=replace(packet.stamp, received_monotonic_s=1.001)),
                      replace(packet, stamp=replace(packet.stamp, rotation_clockwise=90))]:
            self.assertEqual(bridge.draw(other.image, other, hand, 1.1).tobytes(), other.image.tobytes())
        self.assertEqual(bridge.draw(packet.image, packet, hand, 1.301).tobytes(), packet.image.tobytes())
        bridge.close()

    def test_no_hand_never_reuses_previous_landmarks(self):
        bridge = HandBridge(HandTracker(detector=FakeDetector()))
        packet = self.packet()
        self.assertTrue(bridge.process(packet).result.hands)
        next_packet = self.packet(received=1.2)
        result = bridge.process(next_packet)
        self.assertEqual(result.result.status, 'NO_HAND')
        self.assertEqual(bridge.draw(next_packet.image, next_packet, result, 1.25).tobytes(), next_packet.image.tobytes())
        bridge.close()

    def test_invalid_hand_result_association_is_rejected(self):
        class BadTracker:
            def reset(self):
                pass
            def process(self, image, timestamp, **kwargs):
                return HandFrame(TRACKING, 'wrong-frame', timestamp, image.size, (), 0)
        with self.assertRaises(ValueError):
            HandBridge(BadTracker()).process(self.packet())


if __name__ == '__main__':
    unittest.main()
