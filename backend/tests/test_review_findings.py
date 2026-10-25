"""Regression tests for the PR #25 review findings. SIMULATED data; no models or network."""
import argparse
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from r3_vision.spatial import _deskguide, nearest_surface
from r3_vision.tracking_session import IdentifyJob


def frame(confidence):
    _, _, stream = _deskguide()
    h, w = confidence.shape
    K = np.array([[300., 0, w / 2], [0, 300., h / 2], [0, 0, 1]])
    return stream.Frame(1, 1., 1., np.zeros((h, w, 3), np.uint8), np.full((h, w), .6, np.float32), K, np.eye(4),
                        confidence)


class TargetPointConfidenceTests(unittest.TestCase):
    def test_low_confidence_sample_is_never_the_chosen_point(self):
        confidence = np.full((240, 320), 2, np.uint8)
        confidence[:, :150] = 0  # the side nearest the fingertip is unreliable
        f = frame(confidence)
        f.depth[:, :] = .6
        point, share = nearest_surface(f, f.depth, (100, 60, 220, 180), .6, (-.5, 0., .6))
        u = point[0] * 300 / point[2] + 160
        self.assertGreaterEqual(u, 150 - 1, 'chosen point must lie in high-confidence depth')
        self.assertGreater(share, .5)

    def test_no_high_confidence_sample_reports_low_share(self):
        f = frame(np.ones((240, 320), np.uint8))
        point, share = nearest_surface(f, f.depth, (100, 60, 220, 180), .6, None)
        self.assertEqual(share, 0.)


class TrackerRoutingTests(unittest.TestCase):
    def worker(self, tracker):
        from r3_vision.live_tracking import TrackingWorker
        w = TrackingWorker.__new__(TrackingWorker)
        w.args = argparse.Namespace(output=Path(tempfile.mkdtemp()), roi=None, host=None, tracker=tracker)
        w.responses, w.hybrid_note, w.factory = queue.Queue(), '', None
        w.all_objects = w.demo = None
        w.detector = SimpleNamespace(model=object(), status='READY')
        w.calls = []
        w._identify_hybrid = lambda *a, **k: w.calls.append('hybrid')
        w._gemini_result = lambda *a: w.calls.append('gemini') or {'selection': {'status': 'NOT_FOUND'}}
        return w

    def job(self):
        return IdentifyJob(1, 'track-SIMULATED', Image.new('RGB', (64, 48)), 'Find the laptop', 1., ('e', 0))

    def test_lock_tracker_uses_local_hybrid(self):
        w = self.worker('lock')
        w.identify(self.job(), Image.new('RGB', (64, 48)), {})
        self.assertEqual(w.calls, ['hybrid'])

    def test_orb_and_vit_keep_the_gemini_selection(self):
        for tracker in ('orb', 'vit'):
            w = self.worker(tracker)
            w.identify(self.job(), Image.new('RGB', (64, 48)), {})
            self.assertEqual(w.calls, ['gemini'], tracker)
            self.assertIn('--tracker lock', w.hybrid_note)


class VocabularyRaceTests(unittest.TestCase):
    def test_names_come_from_the_prediction_vocabulary(self):
        from r3_vision import local_detector
        d = local_detector.LocalDetector.__new__(local_detector.LocalDetector)
        d.imgsz, d.conf, d.lock = 640, .1, threading.Lock()
        d.vocabulary = None
        boxes = SimpleNamespace(xyxy=np.array([[0., 0, 9, 9], [1, 1, 5, 5]]), conf=np.array([.9, .8]),
                                cls=np.array([0., 99.]))
        for name in ('xyxy', 'conf', 'cls'):
            value = getattr(boxes, name)
            setattr(boxes, name, SimpleNamespace(cpu=lambda v=value: SimpleNamespace(numpy=lambda v=v: v)))

        class Model:
            names = ['wrong-from-other-thread']

            def predict(self, *a, **k):
                return [SimpleNamespace(boxes=boxes)]
        d.model = Model()
        d._set_classes = lambda model, vocabulary: None
        found, _ = d.detect(Image.new('RGB', (32, 32)), ['laptop'])
        self.assertEqual([c['names'] for c in found], [['laptop']])  # index 99 out of range is dropped


if __name__ == '__main__':
    unittest.main()
