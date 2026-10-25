import glob
import unittest
from types import SimpleNamespace

import numpy as np
from PIL import Image

from r3_vision.lock_session import LockSession
from r3_vision.object_lock import LOCKED, LockMatch
from r3_vision.spatial import Offset, SpatialOffset, _deskguide
from r3_vision.tracking_session import RequestBudget

RGBD = sorted(glob.glob('C:/Users/ngkho/OneDrive/Desktop/visionOS/DeskGuide-Laptop/logs/identical-input-hand-visible/*.npz'))
CAN = (.419, .406, .147, .104)


def simulated_frame(depth_m=.5, size=(320, 240)):
    _, _, stream = _deskguide()
    w, h = size
    K = np.array([[300., 0, w / 2], [0, 300., h / 2], [0, 0, 1]])
    return stream.Frame(1, 1., 1., np.zeros((h, w, 3), np.uint8), np.full((h, w), depth_m, np.float32), K, None,
                        np.full((h, w), 2, np.uint8))


def box(x0, y0, x1, y1):
    return np.float32([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])


def hand(offset=(0, 0)):
    base = np.array([[200, 220], [185, 205], [175, 190], [168, 176], [160, 165], [190, 180], [188, 160], [187, 148],
                     [186, 137], [200, 178], [200, 156], [200, 143], [200, 131], [210, 180], [212, 160], [213, 148],
                     [214, 138], [220, 185], [224, 170], [226, 160], [228, 152]], float)
    return base + offset


class OffsetTextTests(unittest.TestCase):
    def test_lidar_and_estimate_are_labelled_differently(self):
        lidar = Offset('LIDAR', delta_m=(.03, -.01, .12), distance_m=.124)
        text = lidar.text()
        self.assertTrue(text.startswith('3D LiDAR: 12.4 cm'))
        self.assertIn('SIDE RIGHT 3cm', text)
        self.assertIn('HEIGHT ALIGNED', text)
        self.assertIn('REACH FORWARD 12cm', text)
        self.assertIn('not contact detection', text)
        estimate = Offset('ESTIMATED', delta_m=(.03, -.01, .12), distance_m=.124).text()
        self.assertIn('SIZE ESTIMATE (not LiDAR)', estimate)
        self.assertIn('≈', estimate)
        self.assertNotIn('LiDAR:', estimate)
        self.assertNotIn('touch', Offset('LIDAR', delta_m=(0, 0, 0), distance_m=0.).text().lower().replace('not contact', ''))

    def test_unavailable_has_no_numbers(self):
        self.assertEqual(Offset('UNAVAILABLE', detail='no synchronized LiDAR depth for this frame').text(),
                         '3D: no synchronized LiDAR depth for this frame')


class SpatialOffsetTests(unittest.TestCase):
    def test_missing_depth_frame_is_unavailable(self):
        self.assertEqual(SpatialOffset().measure(None, box(10, 10, 50, 80), hand(), 1.).status, 'UNAVAILABLE')

    def test_misaligned_depth_is_refused(self):
        spatial = SpatialOffset()
        self.assertIsNone(spatial.aligned(simulated_frame(size=(320, 240)), (300, 240), 0))
        self.assertIsNotNone(spatial.aligned(simulated_frame(size=(320, 240)), (320, 240), 0))

    def test_hand_over_can_does_not_become_can_depth(self):
        import cv2
        spatial = SpatialOffset()
        frame = simulated_frame(.6)
        mask = np.zeros(frame.depth.shape, np.uint8)
        cv2.fillConvexPoly(mask, cv2.convexHull(hand().astype(np.float32)).astype(np.int32), 1)
        frame.depth[cv2.dilate(mask, np.ones((9, 9), np.uint8)) > 0] = .3
        offset = spatial.measure(frame, box(150, 120, 240, 230), hand(), 1.)
        self.assertTrue(offset.target_m is None or offset.target_m[2] > .5, offset)


@unittest.skipUnless(RGBD, 'recorded PathOn RGB-D frames unavailable')
class RecordedRgbdTests(unittest.TestCase):
    def test_real_rgbd_fingertip_and_can_distance(self):
        import cv2
        from r3_vision.frame_packet import make_packet
        from r3_vision.hand_bridge import HandBridge
        _, _, stream = _deskguide()
        spatial, bridge = SpatialOffset(), HandBridge()
        distances = []
        try:
            for i, path in enumerate(RGBD[:6]):
                d = np.load(path)
                frame = stream.Frame(i, float(d['timestamp']), 100. + i, d['color'], d['depth'], d['K'], d['pose'],
                                     d['confidence'])
                image = Image.fromarray(cv2.cvtColor(d['color'], cv2.COLOR_BGR2RGB))
                packet = make_packet(image, frame.received, {'sourceFrameID': i, 'connectionEpoch': 1}, 's', 0, 640)
                w, h = packet.image.size
                x, y, bw, bh = CAN
                primary = bridge.process(packet).result.primary
                self.assertIsNotNone(primary)
                offset = spatial.measure(spatial.aligned(frame, packet.image.size, 0),
                                         box(x * w, y * h, (x + bw) * w, (y + bh) * h), primary.landmarks_px, frame.received)
                # Palm LiDAR + hand geometry is reported as ESTIMATED unless a direct fingertip depth agrees;
                # an estimate must never count as a measurement.
                self.assertIn(offset.status, ('LIDAR', 'ESTIMATED'), offset.detail)
                if offset.status == 'ESTIMATED':
                    self.assertFalse(offset.measured)
                distances.append(offset.distance_m)
                self.assertTrue(.3 < offset.target_m[2] < 1. and .2 < offset.fingertip_m[2] < offset.target_m[2])
        finally:
            bridge.close()
        self.assertLess(max(distances) - min(distances), .05)


class GapToleranceTests(unittest.TestCase):
    def session(self, n):
        tracker = SimpleNamespace(resets=0, locate=lambda image: LockMatch(box(1, 1, 20, 30), 'ok', LOCKED))

        def reset():
            tracker.resets += 1
        tracker.reset_temporal = reset
        s = LockSession(RequestBudget(0, 60, 100))
        s.find('Find the Red Bull can')
        s.tracker, s.label, s.reference_at = tracker, 'Red Bull can', 0.
        return s, tracker

    def test_short_stream_pause_keeps_the_lock(self):
        s, tracker = self.session(0)
        image = Image.new('RGB', (64, 96))
        s.update(image, 1., 1.)
        match = s.update(image, 1.8, 1.8)
        self.assertEqual((tracker.resets, s.state), (0, LOCKED))
        self.assertIsNotNone(match.polygon)
        s.update(image, 3.6, 3.6)
        self.assertEqual(tracker.resets, 1)


class SnapshotCacheTests(unittest.TestCase):
    def test_same_pathon_frame_is_converted_once(self):
        from r3_vision.live_camera import Source
        source = Source.__new__(Source)
        frame = SimpleNamespace(color=np.zeros((48, 64, 3), np.uint8), received=1., id=7, timestamp=2., epoch=1)
        source.phone = SimpleNamespace(latest=frame)
        first = source.snapshot()
        self.assertIs(source.snapshot(), first)
        self.assertIs(source.last_rgbd, frame)
        source.phone.latest = SimpleNamespace(**{**frame.__dict__, 'id': 8, 'received': 1.1})
        self.assertIsNot(source.snapshot(), first)


if __name__ == '__main__':
    unittest.main()
