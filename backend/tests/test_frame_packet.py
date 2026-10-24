"""SIMULATED shared-frame geometry, identity and clock compatibility checks."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r3_vision.frame_packet import make_packet, same_frame, raw_normalized_to_image


class FramePacketTests(unittest.TestCase):
    def packet(self, rotation=0):
        return make_packet(Image.new('RGB', (1920, 1440)), 10.,
            {'sourceFrameID': 42, 'connectionEpoch': 1, 'sensorTimestamp': 123.}, 'SIMULATED-stream', rotation)

    def test_canonical_resolution_rotation_and_source_preserved(self):
        packet = self.packet(90)
        self.assertEqual(packet.image.size, (720, 960))
        self.assertEqual((packet.stamp.raw_width, packet.stamp.raw_height), (1920, 1440))
        self.assertEqual(packet.stamp.received_monotonic_s, 10.)
        self.assertEqual(packet.stamp.sensor_timestamp, 123.)
        self.assertIsNone(packet.stamp.mirrored)
        self.assertEqual(raw_normalized_to_image(.2, .3, 90), (.7, .2))

    def test_join_requires_exact_frame_epoch_geometry_and_timestamp(self):
        stamp = self.packet().stamp
        self.assertTrue(same_frame(stamp, stamp))
        for different in [replace(stamp, frame_id='other'), replace(stamp, stream_id='other'),
                          replace(stamp, connection_epoch=2), replace(stamp, rotation_clockwise=90),
                          replace(stamp, received_monotonic_s=10.001), replace(stamp, image_width=640)]:
            self.assertFalse(same_frame(stamp, different))

    def test_video_fallback_identity_uses_local_receive_clock_not_sensor_clock(self):
        packet = make_packet(Image.new('RGB', (640, 480)), 1., {}, 'SIMULATED', 0)
        self.assertIsNone(packet.stamp.source_frame_id)
        self.assertIsNone(packet.stamp.sensor_timestamp)
        self.assertIn(float(1).hex(), packet.stamp.frame_id)


if __name__ == '__main__':
    unittest.main()
