"""SIMULATED scenes/provider replies only; no phone or live API is tested."""
import io
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r3_vision import gemini, pipeline
from r3_vision.tracking_session import TrackingSession, RequestBudget
from r3_vision.visual_tracker import ReferenceTracker

REGION = dict(x=280/640, y=200/480, width=72/640, height=64/480)


def panel(seed=3):
    rng = np.random.default_rng(seed)
    # Uniquely textured planar panel, with arbitrary labeled controls. No detector classes.
    gray = rng.integers(50, 205, (480, 640), dtype=np.uint8)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    for i in range(160):
        x, y = rng.integers(10, 620), rng.integers(10, 460)
        cv2.circle(gray, (int(x), int(y)), 4, int(rng.integers(0, 256)), 2)
    cv2.rectangle(gray, (280, 200), (351, 263), 220, 2)
    cv2.putText(gray, '5', (295, 247), cv2.FONT_HERSHEY_SIMPLEX, 1.6, 0, 3)
    return Image.fromarray(cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB))


def warp(image, dx=45, dy=20, angle=0):
    H = np.eye(3)
    H[:2] = cv2.getRotationMatrix2D((320, 240), angle, 1)
    H[0, 2] += dx
    H[1, 2] += dy
    return Image.fromarray(cv2.warpPerspective(np.array(image), H, image.size)), H


def selected(job):
    frame = 'SIMULATED-'+job.frame_id
    return dict(mode='SIMULATED', provider={'ok': True, 'error': None}, timingMs={'provider': 19000},
        screenModel={'frameId': frame, 'image': {'widthPx': job.image.width, 'heightPx': job.image.height},
                     'coordinateSystem': 'normalizedImageTopLeft'},
        selection={'status': 'SELECTED', 'target': {'frameId': frame, 'label': '5', 'imageRegion': REGION}})


class VisualTrackerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cv2.setNumThreads(1)
        cls.reference = panel()

    def tracker(self):
        return ReferenceTracker(self.reference, REGION)

    def test_camera_translation_follows_current_not_old_box(self):
        current, H = warp(self.reference, 75, 30)
        tracker = self.tracker()
        match = tracker.locate(current)
        self.assertIsNotNone(match.polygon, match.reason)
        expected = cv2.perspectiveTransform(tracker.corners[None], H)[0]
        self.assertLess(np.max(np.linalg.norm(expected-match.polygon, axis=1)), 3)
        self.assertGreater(float(match.polygon[:, 0].mean()), float(tracker.corners[:, 0].mean())+65)

    def test_motion_sequence_has_no_incremental_drift(self):
        tracker = self.tracker()
        for dx, dy, angle in [(0, 0, 0), (15, 5, 2), (40, 20, 5), (60, 30, 8), (0, 0, 0)]:
            current, H = warp(self.reference, dx, dy, angle)
            match = tracker.locate(current)
            self.assertIsNotNone(match.polygon, (dx, angle, match.reason))
            expected = cv2.perspectiveTransform(tracker.corners[None], H)[0]
            self.assertLess(np.max(np.linalg.norm(expected-match.polygon, axis=1)), 3)

    def test_rotation_and_portrait_frames(self):
        tracker = self.tracker()
        for angle in [-15, 15]:
            current, _ = warp(self.reference, 0, 0, angle)
            match = tracker.locate(current)
            self.assertIsNotNone(match.polygon, match.reason)
        current = self.reference.transpose(Image.Transpose.ROTATE_270)
        match = tracker.locate(current)
        self.assertIsNotNone(match.polygon, match.reason)

    def test_occluded_target_rejected_even_when_panel_context_matches(self):
        image = np.array(self.reference)
        image[198:266, 278:354] = 90
        match = self.tracker().locate(Image.fromarray(image))
        self.assertIsNone(match.polygon)
        self.assertIn('Target', match.reason)

    def test_partial_label_occlusion_hides_box(self):
        image = np.array(self.reference)
        image[218:246, 298:324] = 110
        self.assertIsNone(self.tracker().locate(Image.fromarray(image)).polygon)

    def test_target_leaving_frame(self):
        current, _ = warp(self.reference, 400, 0)
        self.assertIsNone(self.tracker().locate(current).polygon)

    def test_perspective_camera_motion(self):
        H = np.float64([[1.02, .02, 20], [.01, 1., 10], [.00004, -.00002, 1]])
        current = Image.fromarray(cv2.warpPerspective(np.array(self.reference), H, self.reference.size))
        tracker = self.tracker()
        match = tracker.locate(current)
        self.assertIsNotNone(match.polygon, match.reason)
        expected = cv2.perspectiveTransform(tracker.corners[None], H)[0]
        self.assertLess(np.max(np.linalg.norm(expected-match.polygon, axis=1)), 3)

    def test_unrelated_scene_and_false_feature_matches(self):
        tracker = self.tracker()
        self.assertIsNone(tracker.locate(panel(seed=928)).polygon)
        self.assertIsNone(tracker.locate(Image.new('RGB', (640, 480), 'gray')).polygon)
        checker = np.tile(np.array([[0, 255], [255, 0]], dtype=np.uint8), (240, 320))
        self.assertIsNone(tracker.locate(Image.fromarray(checker).convert('RGB')).polygon)

    def test_changed_button_identity_does_not_pass_context_only(self):
        image = np.array(self.reference)
        # Entire target replaced but surrounding panel remains exactly the same.
        image[201:263, 281:351] = np.array(panel(555))[201:263, 281:351]
        self.assertIsNone(self.tracker().locate(Image.fromarray(image)).polygon)


class TrackingSessionTests(unittest.TestCase):
    def identified(self):
        s = TrackingSession()
        s.find('Find floor 5')
        s.source_epoch(1)
        job = s.job(panel(), 1., 1.)
        self.assertIsNotNone(job)
        self.assertEqual(s.state, 'IDENTIFYING')
        s.complete(job, selected(job), 20.)
        self.assertEqual(s.state, 'REACQUIRING')
        return s

    def test_delayed_gemini_reacquires_moved_current_frame(self):
        s = self.identified()
        current, _ = warp(panel(), 75, 30)
        self.assertIsNone(s.update(current, 20.1, 20.1).polygon)
        self.assertEqual(s.state, 'REACQUIRING')
        match = s.update(current, 20.2, 20.2)
        self.assertEqual(s.state, 'TRACKING')
        self.assertGreater(float(match.polygon[:, 0].mean()), 380)

    def test_lost_frame_immediately_hides_then_requires_two_new_matches(self):
        s = self.identified()
        s.update(panel(), 20.1, 20.1)
        self.assertIsNotNone(s.update(panel(), 20.2, 20.2).polygon)
        self.assertIsNone(s.update(Image.new('RGB', (640, 480)), 20.3, 20.3).polygon)
        self.assertEqual(s.state, 'TARGET LOST')
        self.assertIsNone(s.update(panel(), 20.4, 20.4).polygon)
        self.assertIsNotNone(s.update(panel(), 20.5, 20.5).polygon)
        self.assertIsNone(s.update(panel(), 20.5, 21.).polygon)
        self.assertEqual(s.state, 'TARGET LOST')

    def test_changed_request_discards_late_response_without_extra_concurrent_call(self):
        s = TrackingSession()
        s.find('5')
        job = s.job(panel(), 1, 1)
        s.find('6')
        self.assertIsNone(s.job(panel(), 30, 30))
        s.complete(job, selected(job), 30)
        self.assertIsNone(s.tracker)
        new_job = s.job(panel(), 31, 31)
        self.assertEqual(new_job.query, '6')
        self.assertEqual(s.budget.total, 2)

    def test_repeated_frame_cannot_confirm_acquisition_or_keep_box(self):
        s = self.identified()
        s.update(panel(), 20.1, 20.1)
        self.assertIsNone(s.update(panel(), 20.1, 20.2).polygon)
        self.assertEqual(s.state, 'TARGET LOST')
        self.assertIsNone(s.update(panel(), 20.3, 20.3).polygon)
        self.assertIsNotNone(s.update(panel(), 20.4, 20.4).polygon)

    def test_promising_local_reacquisition_does_not_waste_cloud_request(self):
        s = self.identified()
        s.update(Image.new('RGB', (640, 480)), 21., 21.)
        s.update(panel(), 30., 30.)
        self.assertEqual(s.state, 'REACQUIRING')
        self.assertIsNone(s.job(panel(), 30., 30.))
        self.assertIsNotNone(s.update(panel(), 30.1, 30.1).polygon)
        self.assertEqual(s.budget.total, 1)

    def test_source_reconnect_invalidates_reference_and_pending_reply(self):
        s = self.identified()
        s.source_epoch(2)
        self.assertIsNone(s.tracker)
        self.assertEqual(s.state, 'TARGET LOST')
        job = s.job(panel(), 30, 30)
        s.source_epoch(3)
        s.complete(job, selected(job), 40)
        self.assertIsNone(s.tracker)

    def test_cloud_timeouts_and_api_failure_are_errors_with_no_tracker(self):
        for kind in ['timeout', 'http']:
            s = TrackingSession()
            s.find('5')
            job = s.job(panel(), 1, 1)
            def transport(*args):
                if kind == 'timeout':
                    raise TimeoutError()
                return 403, b'{"error":{"status":"PERMISSION_DENIED","message":"SIMULATED"}}'
            client = gemini.GeminiClient(api_key='SIMULATED-key', model='SIMULATED-model', transport=transport)
            data = io.BytesIO()
            job.image.save(data, format='PNG')
            result = pipeline.run(data.getvalue(), '5', client, frame_id=job.frame_id)
            s.complete(job, result, 20)
            self.assertEqual(s.state, 'ERROR')
            self.assertIsNone(s.tracker)
            self.assertIn('PROVIDER_TIMEOUT' if kind == 'timeout' else 'HTTP_ERROR', s.detail)

    def test_ambiguous_not_found_and_frame_mismatch_do_not_track(self):
        for status in ['AMBIGUOUS', 'NOT_FOUND', 'PROVIDER_UNAVAILABLE', 'SELECTED']:
            s = TrackingSession()
            s.find('5')
            job = s.job(panel(), 1, 1)
            result = selected(job)
            result['selection']['status'] = status
            result['selection']['target']['frameId'] = 'wrong'
            s.complete(job, result, 20)
            self.assertIsNone(s.tracker)
            self.assertNotEqual(s.state, 'TRACKING')

    def test_cooldown_rolling_rate_and_total_caps_survive_target_changes(self):
        budget = RequestBudget(cooldown=5, per_minute=2, total=3)
        self.assertTrue(budget.take(0)[0])
        self.assertFalse(budget.take(1)[0])
        self.assertTrue(budget.take(6)[0])
        self.assertFalse(budget.take(12)[0])
        self.assertTrue(budget.take(61)[0])
        self.assertFalse(budget.take(125)[0])
        s = TrackingSession(budget)
        s.find('5')
        self.assertIsNone(s.job(panel(), 200, 200))
        s.find('6')
        self.assertIsNone(s.job(panel(), 201, 201))

    def test_scene_change_reidentification_and_reference_expiry(self):
        s = self.identified()
        s.update(panel(928), 20.1, 20.1)
        self.assertIsNone(s.job(panel(928), 20.2, 20.2))
        self.assertIsNotNone(s.job(panel(928), 23, 23))
        s = self.identified()
        self.assertIsNone(s.update(panel(), 81, 81).polygon)
        self.assertIsNone(s.tracker)
        self.assertEqual(s.state, 'TARGET LOST')


class WorkerIntegrationTests(unittest.TestCase):
    def test_async_delayed_cloud_keeps_preview_live_then_reacquires_and_stops_on_occlusion(self):
        import argparse
        import json
        import tempfile
        import threading
        import time
        from unittest.mock import patch
        from r3_vision.live_tracking import TrackingWorker

        reference = panel()
        moved, _ = warp(reference, 75, 30)
        pending = threading.Event()
        release = threading.Event()
        occluded = threading.Event()
        entered = threading.Event()
        body = {'panelType': 'elevatorPanel', 'availability': 'available', 'sections': [],
                'isPaymentScreen': False, 'elements': [{'label': '5', 'legibility': 'clear',
                    'type': 'button', 'box_2d': [417, 438, 550, 550]}]}
        envelope = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(body)}]}}]}
        replay = gemini.replay_transport(envelope)

        def transport(*args):
            pending.set()
            entered.set()
            if not release.wait(5):
                raise TimeoutError()
            return replay(*args)

        class FakeSource:
            status = 'SIMULATED source'
            def __init__(self, args):
                pass
            def snapshot(self):
                image = Image.new('RGB', reference.size) if occluded.is_set() else moved if pending.is_set() else reference
                return image.copy(), time.monotonic(), {'connectionEpoch': 1}
            def close(self):
                pass

        def wait_for(worker, condition, timeout=5):
            deadline = time.monotonic()+timeout
            while time.monotonic() < deadline:
                item = worker.output
                if item and condition(item):
                    return item
                time.sleep(.02)
            self.fail(f'SIMULATED worker timed out; last output: {worker.output[3:5] if worker.output else None}')

        client = gemini.GeminiClient(api_key='SIMULATED', model='replay', transport=transport, live=False)
        with tempfile.TemporaryDirectory() as tmp, patch('r3_vision.live_tracking.Source', FakeSource):
            args = argparse.Namespace(host=None, video='SIMULATED', roi=None, rotation=0,
                cooldown=20, per_minute=3, max_requests=3, reidentify_after=2, reference_ttl=60,
                output=Path(tmp))
            worker = TrackingWorker(args, client)
            try:
                worker.commands.put((1, 'Find floor 5'))
                self.assertTrue(entered.wait(3))
                first = wait_for(worker, lambda item: item[0] == 1 and item[3] == 'IDENTIFYING')
                second = wait_for(worker, lambda item: item[0] == 1 and item[2] > first[2])
                self.assertEqual(second[3], 'IDENTIFYING')
                release.set()
                tracked = wait_for(worker, lambda item: item[3] == 'TRACKING')
                self.assertEqual(tracked[5], 'SIMULATED')
                self.assertEqual(tracked[6], 1)
                # New location is x~355..427, y~230..294; old x~280..352 is never reused.
                pixels = np.array(tracked[1])
                red = (pixels[:, :, 0] == 255) & (pixels[:, :, 1] == 0) & (pixels[:, :, 2] == 0)
                ys, xs = np.where(red)
                self.assertGreater(xs.min(), 350)
                occluded.set()
                lost = wait_for(worker, lambda item: item[3] == 'TARGET LOST' and item[2] > tracked[2])
                self.assertIsNotNone(lost[1])  # still live video, with no tracking overlay
                self.assertFalse(np.any((np.array(lost[1])[:, :, 0] == 255)))
            finally:
                release.set()
                worker.close()


if __name__ == '__main__':
    unittest.main()
