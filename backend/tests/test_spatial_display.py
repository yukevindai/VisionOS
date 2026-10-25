"""SIMULATED geometry for the spatial panel, nearest-surface target, HIDDEN state and depth check."""
import time
import unittest
from types import SimpleNamespace

import numpy as np
from PIL import Image

from r3_vision.lock_session import LockSession
from r3_vision.object_lock import HIDDEN, LockMatch
from r3_vision.spatial import Offset, _deskguide, nearest_surface
from r3_vision.tracking_session import RequestBudget


def frame(depth_m=.6, size=(320, 240), confidence=2):
    _, _, stream = _deskguide()
    w, h = size
    K = np.array([[300., 0, w / 2], [0, 300., h / 2], [0, 0, 1]])
    return stream.Frame(7, 12.5, 1., np.zeros((h, w, 3), np.uint8), np.full((h, w), depth_m, np.float32), K,
                        np.eye(4), np.full((h, w), confidence, np.uint8))


class NearestSurfaceTests(unittest.TestCase):
    def test_target_is_the_visible_surface_point_nearest_the_fingertip(self):
        f = frame(.6)
        f.depth[:, :] = .6
        f.depth[:, 200:] = .9
        point, share = nearest_surface(f, f.depth, (100, 60, 220, 180), .6, (-.5, 0., .6))
        self.assertAlmostEqual(point[2], .6, places=3)
        self.assertLess(point[0], (100 + 12 - 160) * .6 / 300 + .01, 'nearest point is on the fingertip side')
        self.assertEqual(share, 1.)

    def test_too_few_surface_samples_returns_nothing(self):
        f = frame(0.)
        self.assertEqual(nearest_surface(f, f.depth, (100, 60, 220, 180), .6, None), (None, None))


class PanelTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from r3_vision.spatial_panel import SpatialPanel
        self.root = tk.Tk()
        self.root.withdraw()
        self.panel = SpatialPanel(self.root)

    def tearDown(self):
        self.root.destroy()

    def data(self, **changes):
        offset = Offset('LIDAR', (.005, .044, .433), (-.013, -.01, .595), (-.018, -.054, .162), .172,
                        'palm LiDAR + hand geometry', 'ok', 'nearest visible surface point', 'LOW', 'HIGH', 'no direct tip')
        data = dict(ticket=1, target='Red Bull can', tracking='LOCKED', received=time.monotonic(), fps=14.2,
                    offset=offset, alignment={'dx_px': 30., 'dy_px': -60., 'inside_box': False},
                    direction=('FORWARD', '3D'), audio={'enabled': True, 'last_cue': 'FORWARD', 'playback': 'playing FORWARD',
                                                        'suppressed': None}, depth_view=None)
        data.update(changes)
        return data

    def test_measured_values_are_large_and_in_centimetres(self):
        self.panel.update(self.data())
        self.assertEqual(self.panel.distance.cget('text'), '17.2 cm')
        self.assertEqual(self.panel.cells['FINGER', 3].cget('text'), '43.3')
        self.assertEqual(self.panel.cells['DIFF', 3].cget('text'), '+16.2')
        self.assertIn('finger HIGH / target LOW', self.panel.confidence.cget('text'))
        self.assertIn('FORWARD', self.panel.direction.cget('text'))
        self.assertIn('pixels, not cm', self.panel.pixels.cget('text'))
        self.assertEqual(int(self.root.tk.splitlist(self.panel.distance.cget('font'))[1]), -60)

    def test_no_measured_depth_says_3d_unavailable_and_no_centimetres(self):
        estimate = Offset('ESTIMATED', (.0, .0, .4), (.0, .0, .6), (.0, .0, .2), .2, 'hand size/shape estimate')
        self.panel.update(self.data(offset=estimate, direction=(None, '2D')))
        self.assertEqual(self.panel.distance.cget('text'), '3D UNAVAILABLE')
        self.assertEqual(self.panel.cells['FINGER', 1].cget('text'), '-')

    def test_stale_frame_clears_values_and_direction(self):
        self.panel.update(self.data(received=time.monotonic() - .5))
        self.assertEqual(self.panel.distance.cget('text'), 'FRAME STALE')
        self.assertEqual(self.panel.direction.cget('text'), 'DIRECTION: -')
        self.assertEqual(self.panel.cells['DIFF', 1].cget('text'), '-')


class DepthViewTests(unittest.TestCase):
    def test_depth_view_marks_invalid_samples_black(self):
        from r3_vision.spatial_panel import depth_view
        f = frame(.6, (320, 240))
        f.depth[:40] = 0
        image = np.asarray(depth_view(f, (100, 60, 220, 180), (50, 120)))
        self.assertEqual(image.shape[:2], (240, 320))
        self.assertEqual(int(image[5, 5].sum()), 0)
        self.assertGreater(int(image[200, 300].sum()), 0)


class HiddenStateTests(unittest.TestCase):
    def test_hidden_keeps_the_lock_without_gemini_retries(self):
        polygon = np.float32([[10, 10], [50, 10], [50, 80], [10, 80]])
        s = LockSession(RequestBudget(0, 60, 100), reacquire_after=0)
        s.find('Find the Red Bull can')
        s.tracker = SimpleNamespace(locate=lambda image: LockMatch(polygon, 'Hidden 1.0s', HIDDEN),
                                    reset_temporal=lambda: None)
        s.label, s.reference_at, s.user_request = 'Red Bull can', 0., False
        match = s.update(Image.new('RGB', (64, 96)), 1., 1.)
        self.assertEqual(s.state, 'HIDDEN')
        self.assertIsNotNone(match.polygon)
        self.assertIsNone(s.job(Image.new('RGB', (64, 96)), 1.1, 1.1))


class DepthCheckTests(unittest.TestCase):
    def test_reports_each_field_and_confidence_distribution(self):
        from r3_vision.depth_check import describe
        text = describe(frame())
        for field in ('RGB:         AVAILABLE', 'DEPTH:       AVAILABLE', 'CONFIDENCE:  AVAILABLE', '2:100%',
                      'INTRINSICS:  AVAILABLE', 'CAMERA POSE: AVAILABLE', 'FRAME ID: 7'):
            self.assertIn(field, text)
        f = frame()
        f.confidence, f.pose = None, None
        text = describe(f)
        self.assertIn('CONFIDENCE:  UNAVAILABLE', text)
        self.assertIn('CAMERA POSE: UNAVAILABLE', text)


if __name__ == '__main__':
    unittest.main()
