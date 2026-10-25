import glob
import json
import unittest

import numpy as np
from PIL import Image

from r3_vision import gemini, lock_replay
from r3_vision.alignment import fingertip_alignment
from r3_vision.lock_session import LockSession
from r3_vision.object_lock import HIDDEN, LOCKED, LOST, REACQUIRING, UNCERTAIN, LockMatch, ObjectLockTracker
from r3_vision.tracker_models import MODEL_PATH
from r3_vision.tracking_session import IdentifyJob, RequestBudget

FRAMES = sorted(glob.glob(str(lock_replay.DEFAULT_FRAMES / '*.npz')))


class FakeTracker:
    def __init__(self, script):
        self.script = list(script)
        self.resets = 0

    def locate(self, image):
        return self.script.pop(0)

    def reset_temporal(self):
        self.resets += 1


def box():
    return np.float32([[10, 10], [50, 10], [50, 80], [10, 80]])


class LockSessionTests(unittest.TestCase):
    def session(self, script):
        s = LockSession(RequestBudget(0, 60, 100), reacquire_after=5)
        s.find('Find the Red Bull can')
        s.tracker, s.label, s.reference_at = FakeTracker(script), 'Red Bull can', 0.
        return s

    def test_states_follow_current_tracker_evidence(self):
        s = self.session([LockMatch(None, 'Candidate found', REACQUIRING, inliers=40),
                          LockMatch(box(), 'ok', LOCKED), LockMatch(box(), 'weak', UNCERTAIN),
                          LockMatch(None, 'gone', LOST)])
        image = Image.new('RGB', (64, 96))
        states, shown = [], []
        for i in range(4):
            match = s.update(image, 1 + i * .05, 1 + i * .05)
            states.append(s.state)
            shown.append(match.polygon is not None and s.state in (LOCKED, UNCERTAIN))
        self.assertEqual(states, ['REACQUIRING', 'LOCKED', 'UNCERTAIN', 'TARGET LOST'])
        self.assertEqual(shown, [False, True, True, False])

    def test_long_stall_hides_box_and_resets_temporal_tracking(self):
        s = self.session([])
        match = s.update(Image.new('RGB', (64, 96)), 1., 2.6)
        self.assertIsNone(match.polygon)
        self.assertEqual((s.state, s.tracker.resets), ('TARGET LOST', 1))

    def test_late_frame_within_grace_keeps_tracking(self):
        s = self.session([LockMatch(box(), 'ok', LOCKED)])
        s.update(Image.new('RGB', (64, 96)), 1., 1.5)
        self.assertEqual((s.state, s.tracker.resets), (LOCKED, 0))

    def test_late_gemini_result_cannot_replace_an_active_lock(self):
        s = self.session([LockMatch(box(), 'ok', LOCKED)])
        s.state, s.lost_at = 'TARGET LOST', -10.
        job = s.job(Image.new('RGB', (64, 96)), 1., 1.)
        self.assertIsNotNone(job)
        self.assertIsNotNone(s.tracker, 'local search must keep running during re-identification')
        tracker = s.tracker
        s.update(Image.new('RGB', (64, 96)), 1.05, 1.05)
        self.assertEqual(s.state, LOCKED)
        s.complete(job, {'selection': {'status': 'SELECTED'}, 'timingMs': {'provider': 3000}}, 1.1)
        self.assertIs(s.tracker, tracker)
        self.assertIsNone(s.inflight)
        self.assertIn('ignored', s.last_identification)

    def test_no_gemini_call_while_locked(self):
        s = self.session([LockMatch(box(), 'ok', LOCKED)])
        s.update(Image.new('RGB', (64, 96)), 1., 1.)
        self.assertIsNone(s.job(Image.new('RGB', (64, 96)), 1., 1.))
        self.assertEqual(s.budget.total, 0)


class RememberedGeometryTests(unittest.TestCase):
    def tracker(self, box=(100.25, 90.5, 40.5, 60.75)):
        tracker = ObjectLockTracker.__new__(ObjectLockTracker)
        tracker.hidden = {'box': box, 'since': 0.}
        tracker.state, tracker.remember_s = HIDDEN, 10.
        tracker.prev_gray = None
        tracker.next_search = float('inf')
        tracker._camera_motion = lambda previous, gray, area: np.float32([[1, 0, 0], [0, 1, 0]])
        return tracker

    def test_stationary_remembered_box_keeps_fractional_bounds(self):
        tracker = self.tracker()
        original = tracker.hidden['box']
        for frame in range(200):
            match = tracker._hidden(np.zeros((480, 640, 3), np.uint8), np.zeros((480, 640), np.uint8), frame / 30.)
            self.assertEqual(match.state, HIDDEN)
            np.testing.assert_allclose(tracker.hidden['box'], original, atol=1e-5)

    def test_subpixel_motion_accumulates_without_rounding(self):
        tracker = self.tracker()
        tracker._camera_motion = lambda previous, gray, area: np.float32([[1, 0, .25], [0, 1, -.125]])
        for frame in range(100):
            tracker._hidden(np.zeros((480, 640, 3), np.uint8), np.zeros((480, 640), np.uint8), frame / 30.)
        np.testing.assert_allclose(tracker.hidden['box'], (125.25, 78., 40.5, 60.75), atol=1e-5)

    def test_rotation_then_inverse_restores_original_geometry(self):
        import cv2
        tracker = self.tracker()
        original = tracker.hidden['box']
        forward = cv2.getRotationMatrix2D((120, 120), 20, 1)
        transforms = iter([forward, cv2.invertAffineTransform(forward)])
        tracker._camera_motion = lambda previous, gray, area: next(transforms)
        for now in (1., 2.):
            tracker._hidden(np.zeros((480, 640, 3), np.uint8), np.zeros((480, 640), np.uint8), now)
        np.testing.assert_allclose(tracker.hidden['box'], original, atol=1e-4)


class AlignmentTests(unittest.TestCase):
    def test_inside_and_outside_box(self):
        inside = fingertip_alignment(box(), (30, 40), (100, 100))
        self.assertTrue(inside['inside_box'])
        self.assertEqual(inside['edge_gap_px'], 0)
        outside = fingertip_alignment(box(), (80, 45), (100, 100))
        self.assertFalse(outside['inside_box'])
        self.assertAlmostEqual(outside['edge_gap_px'], 30)
        self.assertAlmostEqual(outside['dx_px'], -50)


class GeminiObjectPromptTests(unittest.TestCase):
    def test_object_prompt_is_sent_and_red_bull_label_selects(self):
        sent = []
        reply = {'panelType': 'other', 'availability': 'available', 'isPaymentScreen': False, 'sections': [],
                 'elements': [{'label': 'Red Bull can', 'legibility': 'clear', 'type': 'other',
                               'box_2d': [200, 450, 540, 690]}]}
        envelope = {'candidates': [{'content': {'parts': [{'text': json.dumps(reply)}]}}]}

        def transport(url, headers, body, timeout):
            sent.append(json.loads(body))
            return 200, json.dumps(envelope).encode()
        client = gemini.GeminiClient(api_key='k', model='gemini-3.8-flash', transport=transport, live=False,
                                     thinking_level='low')
        client.prompt = gemini.OBJECT_PROMPT
        from r3_vision import pipeline
        import io
        buffer = io.BytesIO()
        Image.new('RGB', (720, 960), (90, 90, 90)).save(buffer, 'PNG')
        result = pipeline.run(buffer.getvalue(), 'Find the Red Bull can', client, frame_id='f')
        self.assertIn('everyday physical objects', sent[0]['contents'][0]['parts'][0]['text'])
        self.assertEqual(sent[0]['generationConfig']['thinkingConfig'], {'thinkingLevel': 'low'})
        self.assertEqual(result['selection']['status'], 'SELECTED')
        self.assertEqual(result['selection']['target']['label'], 'Red Bull can')

    def test_two_cans_are_ambiguous(self):
        from r3_vision import selector
        model = {'frameId': 'f', 'availability': 'available', 'coordinateSystem': 'normalizedImageTopLeft',
                 'controls': [{'id': 'a', 'label': 'Red Bull can', 'legibility': 'clear', 'type': 'other',
                               'region': {'x': .1, 'y': .1, 'width': .2, 'height': .3}},
                              {'id': 'b', 'label': 'Red Bull can', 'legibility': 'clear', 'type': 'other',
                               'region': {'x': .6, 'y': .1, 'width': .2, 'height': .3}}]}
        self.assertEqual(selector.select('Find the Red Bull can', model)['status'], 'AMBIGUOUS')

    def test_default_panel_prompt_unchanged(self):
        self.assertIs(gemini.GeminiClient(api_key='k', model='m', live=False).prompt, gemini.PROMPT)


class LiveArgumentTests(unittest.TestCase):
    def test_red_bull_defaults(self):
        from r3_vision.live_tracking import arguments
        args = arguments(['--host', '10.0.0.2', '--port', '8888'])
        self.assertEqual((args.tracker, args.target_kind, args.thinking, args.request, args.reidentify_after),
                         ('lock', 'object', 'low', 'Find the Red Bull can', 5.))
        self.assertEqual(arguments(['--host', 'h', '--port', '1', '--tracker', 'vit']).reidentify_after, 2.)


@unittest.skipUnless(FRAMES and MODEL_PATH.exists(), 'recorded can frames or ViTTrack model unavailable')
class RecordedCanLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.timings = lock_replay.run(lambda r, g, c: ObjectLockTracker(r, g, clock=c))
        cls.summary = lock_replay.summarize('lock', cls.rows, cls.timings)

    def test_follows_can_through_pan_zoom_rotation(self):
        for phase in ('pan', 'zoom', 'rotate'):
            p = self.summary['phases'][phase]
            self.assertGreaterEqual(p['correct'], p['frames'] - 1, phase)
            self.assertEqual(p['wrong'], 0, phase)

    def test_covered_target_keeps_a_remembered_box_that_follows_the_camera(self):
        for phase in ('full cover', 'covered pan'):
            p = self.summary['phases'][phase]
            self.assertEqual(p['shown'], 0, f'{phase}: no live box on a covered target')
            self.assertGreaterEqual(p['remembered'], p['frames'] - 1, phase)
            self.assertGreaterEqual(p['correct'], p['frames'] - 1, f'{phase}: remembered box must stay on the object')
            self.assertEqual(p['wrong'], 0, phase)

    def test_never_shows_box_on_hidden_target(self):
        self.assertEqual(self.summary['boxesOnHiddenTarget'], 0)
        for phase in ('full cover', 'point away'):
            self.assertEqual(self.summary['phases'][phase]['shown'], 0, phase)

    def test_reacquires_after_return(self):
        p = self.summary['phases']['return']
        self.assertGreaterEqual(p['correct'], p['frames'] - 3)
        self.assertEqual(p['wrong'], 0)

    def test_box_only_with_lock_states(self):
        for r in self.rows:
            if r['boxShown']:
                self.assertIn(r['state'], (LOCKED, UNCERTAIN, 'HIDDEN'))
        self.assertEqual(self.summary['phases']['point away']['remembered'], 0,
                         'pointing away must end the remembered location')

    def test_two_identical_cans_are_not_chosen_between(self):
        frame = np.load(FRAMES[0])['rgb']
        h, w = frame.shape[:2]
        region = lock_replay.DEFAULT_REGION
        x0, y0 = int(region['x'] * w), int(region['y'] * h)
        x1, y1 = int((region['x'] + region['width']) * w), int((region['y'] + region['height']) * h)
        tracker = ObjectLockTracker(Image.fromarray(frame), region, clock=lambda: 0.)
        doubled = frame.copy()
        can = frame[y0:y1, x0:x1]
        doubled[y0:y1, 10:10 + can.shape[1]] = can
        match = tracker.locate(Image.fromarray(doubled))
        self.assertIsNone(match.polygon)
        self.assertNotEqual(match.state, LOCKED)
        self.assertIn('Two similar objects', match.reason)

    def locked_tracker(self):
        frame = np.load(FRAMES[0])['rgb']
        clock = [0.]
        tracker = ObjectLockTracker(Image.fromarray(frame), lock_replay.DEFAULT_REGION, clock=lambda: clock[0])
        return tracker, Image.fromarray(frame), clock

    def test_failed_feature_checks_alone_do_not_drop_a_strong_lock(self):
        tracker, image, clock = self.locked_tracker()
        tracker._verify = lambda gray, box: False
        states = []
        for _ in range(30):
            clock[0] += .1
            states.append(tracker.locate(image).state)
        self.assertEqual(states[1:], [LOCKED] * 29)
        self.assertGreaterEqual(tracker.verify_failures, 4)

    def test_learns_extra_views_while_strongly_locked(self):
        tracker, image, clock = self.locked_tracker()
        for _ in range(40):
            clock[0] += .1
            tracker.locate(image)
        self.assertGreater(len(tracker.views), 1)
        self.assertLessEqual(len(tracker.views), tracker.max_views)

    def test_jump_to_another_place_is_refused(self):
        frame = np.load(FRAMES[0])['rgb']
        tracker = ObjectLockTracker(Image.fromarray(frame), lock_replay.DEFAULT_REGION, clock=lambda: 0.)
        tracker.box = (325., 200., 170., 320.)
        self.assertTrue(tracker._plausible((335., 205., 168., 318.)))
        self.assertFalse(tracker._plausible((20., 600., 170., 320.)))
        self.assertFalse(tracker._plausible((325., 200., 60., 110.)))


RED_BULL = sorted(glob.glob(str(lock_replay.DEFAULT_FRAMES.parents[3] / 'VisionOS/backend/recordings/r3-tracking/track-*/result.json')))


def red_bull_capture():
    from pathlib import Path
    for path in RED_BULL:
        result = json.loads(Path(path).read_text(encoding='utf-8'))
        target = result['selection'].get('target') or {}
        if result['selection']['status'] == 'SELECTED' and 'red bull' in target.get('label', '').lower():
            return Image.open(Path(path).with_name('captured.png')).convert('RGB'), target['imageRegion']
    return None


@unittest.skipUnless(MODEL_PATH.exists() and red_bull_capture(), 'no local live Red Bull capture with a Gemini box')
class LiveRedBullCaptureTests(unittest.TestCase):
    def test_real_red_bull_capture_with_simulated_camera_motion(self):
        import cv2
        image, region = red_bull_capture()
        clock = [0.]
        tracker = ObjectLockTracker(image, region, clock=lambda: clock[0])
        rgb = np.array(tracker_image := image.resize(tracker.size))
        x, y, w, h = tracker.ref_box
        correct = shown = 0
        steps = [(s[1], s[2], s[3], s[4]) for s in lock_replay.trajectory() if s[0] in ('pan', 'zoom', 'rotate')]
        for dx, dy, scale, angle in steps:
            clock[0] += 1 / 15
            frame, corners = lock_replay.warp(rgb, dx, dy, scale, angle, (x, y, w, h))
            match = tracker.locate(Image.fromarray(frame))
            if match.polygon is not None:
                shown += 1
                truth = (*corners.min(0), *corners.max(0))
                correct += lock_replay.iou((*match.polygon.min(0), *match.polygon.max(0)), truth) >= .5
        self.assertGreaterEqual(correct, len(steps) - 2)
        self.assertEqual(correct, shown)


if __name__ == '__main__':
    unittest.main()
