"""Pretrained-model tests are offline and skip explicitly if the pinned asset is absent.
All generated camera motion and provider replies are SIMULATED; no API or sensor calls.
"""
import io
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r3_vision.frame_history import FrameHistory
from r3_vision.hybrid_tracker import HybridTracker
from r3_vision.tracker_models import MODEL_PATH, ensure_model
from test_live_tracking import panel, REGION, warp


class FrameHistoryTests(unittest.TestCase):
    def test_bounded_memory_epoch_and_timestamp_selection(self):
        history = FrameHistory(seconds=1, max_bytes=100000, max_frames=3)
        for t in [1, 1.1, 1.2, 1.3, 3]:
            history.append(panel(), t, 'SIMULATED-epoch')
        frames = history.after(0, 'SIMULATED-epoch')
        self.assertEqual([f.received for f in frames], [3])
        self.assertLessEqual(history.bytes, history.max_bytes)
        self.assertFalse(history.after(0, 'other-epoch'))
        self.assertEqual(frames[0].image().size, (640, 480))

    def test_size_and_count_caps_hold(self):
        history = FrameHistory(max_bytes=40000, max_frames=2)
        for i in range(20):
            history.append(Image.new('RGB', (100, 100)), float(i), 1)
        self.assertLessEqual(len(history.frames), 2)
        self.assertLessEqual(history.bytes, 40000)


@unittest.skipUnless(MODEL_PATH.exists() and hasattr(cv2, 'TrackerVit_create'),
                     'Pinned ViTTrack model/OpenCV unavailable; run python -m r3_vision.tracker_models')
class HybridTrackerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cv2.setNumThreads(1)
        ensure_model()  # Also enforces pinned checksum; never downloads from tests.

    def test_native_tracks_camera_motion_using_pretrained_model(self):
        tracker = HybridTracker(panel(), REGION)
        tracker.seed_reference()
        for shift in [0, 2, 4, 6, 8, 10, 15, 20]:
            current, H = warp(panel(), shift, shift)
            match = tracker.locate(current, recovery=False)
            self.assertIsNotNone(match.polygon, (shift, match.reason, tracker.last_score))
            expected = cv2.perspectiveTransform(tracker.reference.corners[None], H)[0]
            self.assertLess(np.max(np.linalg.norm(match.polygon-expected, axis=1)), 8)
            self.assertEqual(tracker.last_method, 'ViTTrack')
            self.assertGreaterEqual(tracker.last_score, .5)

    def test_occlusion_and_scene_change_hide_native_box(self):
        for current in [Image.new('RGB', (640, 480)), panel(945)]:
            tracker = HybridTracker(panel(), REGION)
            tracker.seed_reference()
            self.assertIsNone(tracker.locate(current, recovery=False).polygon)
            self.assertIsNone(tracker.native)

    def test_partial_occlusion_target_leaving_and_size_change_hide(self):
        image = np.array(panel())
        image[218:246, 298:324] = 100
        for current in [Image.fromarray(image), warp(panel(), 400, 0)[0], Image.new('RGB', (480, 640))]:
            tracker = HybridTracker(panel(), REGION)
            tracker.seed_reference()
            self.assertIsNone(tracker.locate(current, recovery=False).polygon)

    def test_low_learned_score_never_releases_prediction(self):
        tracker = HybridTracker(panel(), REGION)
        tracker.native_size = (640, 480)
        class LowScore:
            def update(self, image):
                return True, (280, 200, 72, 64)
            def getTrackingScore(self):
                return .1
        tracker.native = LowScore()
        self.assertIsNone(tracker.locate(panel(), recovery=False).polygon)

    def test_sift_reacquires_after_large_motion_and_rotation(self):
        tracker = HybridTracker(panel(), REGION)
        current, H = warp(panel(), 60, 30, 12)
        match = tracker.locate(current)
        self.assertIsNotNone(match.polygon, match.reason)
        self.assertEqual(tracker.last_method, 'SIFT reacquisition')
        self.assertIsNotNone(tracker.native)

    def test_delayed_response_replays_intervening_motion_without_old_box(self):
        history = FrameHistory()
        now = time.monotonic()
        received = now-.3
        for i in range(1, 9):
            history.append(warp(panel(), i*2, i)[0], received+i*.03, 1)
        tracker = HybridTracker(panel(), REGION)
        with patch('r3_vision.hybrid_tracker.time.monotonic', return_value=now+.05):
            ok, count = tracker.prime(history, received, 1)
        self.assertTrue(ok)
        self.assertGreaterEqual(count, 2)
        self.assertLessEqual(count, 8)  # Catch-up decimates to measured <=5 Hz endpoints.
        match = tracker.locate(warp(panel(), 18, 9)[0], recovery=False)
        self.assertIsNotNone(match.polygon, match.reason)
        self.assertGreater(float(match.polygon[:, 0].mean()), 330)

    def test_replay_gaps_and_occlusion_invalidate_temporal_state(self):
        for occlude in [False, True]:
            history = FrameHistory()
            received = time.monotonic()-2
            history.append(Image.new('RGB', (640, 480)) if occlude else panel(),
                           received+.1 if occlude else received+1, 1)
            tracker = HybridTracker(panel(), REGION)
            ok, _ = tracker.prime(history, received, 1)
            self.assertFalse(ok)
            self.assertIsNone(tracker.native)

    def test_high_learned_score_cannot_jump_to_neighboring_control(self):
        tracker = HybridTracker(panel(), REGION)
        tracker.seed_reference()
        class WrongBox:
            def update(self, image):
                return True, (420, 200, 72, 64)
            def getTrackingScore(self):
                return .99
        tracker.native = WrongBox()
        self.assertIsNone(tracker.locate(panel(), recovery=False).polygon)

    def test_async_live_worker_with_vit_default_path(self):
        from r3_vision.live_tracking import TrackingWorker as RealWorker
        from test_live_tracking import WorkerIntegrationTests
        def create(args, client):
            args.tracker = 'vit'
            return RealWorker(args, client)
        with patch('r3_vision.live_tracking.TrackingWorker', create):
            WorkerIntegrationTests('test_async_delayed_cloud_keeps_preview_live_then_reacquires_and_stops_on_occlusion').test_async_delayed_cloud_keeps_preview_live_then_reacquires_and_stops_on_occlusion()


if __name__ == '__main__':
    unittest.main()
