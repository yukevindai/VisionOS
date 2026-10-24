import glob
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from hand_tracking import (INDEX_TIP, INVALID_INPUT, LANDMARK_NAMES, NO_HAND, STALE_INPUT, TRACKING, HandTracker,
                           raw_to_upright_norm, upright_to_raw_norm)
from hand_tracking.render import draw_hands
from hand_tracking.tracker import PROTOTYPE

RECORDED = Path(__file__).resolve().parents[3] / 'DeskGuide-Laptop/logs/identical-input-hand-visible'
MODEL = PROTOTYPE / 'models/hand_landmarker.task'


def simulated_hand(wrist, label='Right', score=.9, spread=40.):
    wrist = np.asarray(wrist, float)
    offsets = np.array([[0, 0]] + [[(i % 4 - 1.5) * spread / 3, -spread * (1 + i // 4) / 2] for i in range(20)])
    return dict(points=wrist + offsets, label=label, score=score)


class SimulatedDetector:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def detect(self, rgb, timestamp_ms):
        self.calls.append((rgb.copy(), timestamp_ms))
        return self.script.pop(0) if self.script else []


def frame(w=320, h=240):
    return np.zeros((h, w, 3), np.uint8)


class SimulatedHandTracking(unittest.TestCase):
    def test_21_landmarks_index_tip_and_coordinate_system(self):
        tracker = HandTracker(SimulatedDetector([[simulated_hand((160, 200))]]))
        result = tracker.process(frame(), 1.0, frame_id='f1')
        self.assertEqual(result.status, TRACKING)
        hand = result.primary
        self.assertEqual(hand.landmarks_px.shape, (21, 2))
        self.assertEqual(len(LANDMARK_NAMES), 21)
        np.testing.assert_allclose(hand.index_tip_px, hand.landmarks_px[INDEX_TIP])
        np.testing.assert_allclose(hand.index_tip_norm, hand.index_tip_px / [320, 240])
        self.assertEqual(result.image_size, (320, 240))
        self.assertIn('origin top-left', result.coordinate_system)
        payload = result.to_dict()
        self.assertEqual(len(payload['hands'][0]['landmarks']), 21)
        self.assertEqual(payload['frameID'], 'f1')

    def test_missing_hand_clears_immediately(self):
        tracker = HandTracker(SimulatedDetector([[simulated_hand((160, 200))], []]))
        self.assertEqual(tracker.process(frame(), 1.0).status, TRACKING)
        result = tracker.process(frame(), 1.033)
        self.assertEqual(result.status, NO_HAND)
        self.assertEqual(result.hands, ())
        self.assertIsNone(result.primary)

    def test_reacquisition_keeps_identity_only_within_timeout(self):
        hand = [simulated_hand((160, 200))]
        tracker = HandTracker(SimulatedDetector([hand, [], hand, [], hand]))
        first = tracker.process(frame(), 1.0).primary.track_id
        tracker.process(frame(), 1.1)
        again = tracker.process(frame(), 1.3).primary
        self.assertEqual((again.track_id, again.identity), (first, 'CONTINUED'))
        tracker.process(frame(), 1.4)
        later = tracker.process(frame(), 2.5).primary
        self.assertNotEqual(later.track_id, first)
        self.assertEqual(later.identity, 'NEW')

    def test_two_hands_keep_identities_while_moving(self):
        script = [[simulated_hand((80 + 5 * i, 200), 'Left'), simulated_hand((240 - 5 * i, 200), 'Right')]
                  for i in range(6)]
        script[3].reverse()
        tracker = HandTracker(SimulatedDetector(script))
        ids = []
        for i in range(6):
            result = tracker.process(frame(), 1 + i / 30)
            ids.append({tuple(np.rint(h.landmarks_px[0]).astype(int)): h.track_id for h in result.hands})
        left = [d[(80 + 5 * i, 200)] for i, d in enumerate(ids)]
        right = [d[(240 - 5 * i, 200)] for i, d in enumerate(ids)]
        self.assertEqual(len(set(left)), 1)
        self.assertEqual(len(set(right)), 1)
        self.assertNotEqual(left[0], right[0])

    def test_crossing_hands_get_new_ids_instead_of_silent_swap(self):
        tracker = HandTracker(SimulatedDetector([
            [simulated_hand((150, 200), 'Right'), simulated_hand((170, 200), 'Right')],
            [simulated_hand((160, 200), 'Right'), simulated_hand((161, 200), 'Right')],
        ]))
        before = {h.track_id for h in tracker.process(frame(), 1.0).hands}
        after = tracker.process(frame(), 1.033).hands
        self.assertTrue(all(h.identity == 'NEW_AMBIGUOUS' for h in after))
        self.assertFalse(before & {h.track_id for h in after})

    def test_far_jump_is_a_new_hand(self):
        tracker = HandTracker(SimulatedDetector([[simulated_hand((20, 200))], [simulated_hand((300, 30))]]))
        first = tracker.process(frame(), 1.0).primary.track_id
        second = tracker.process(frame(), 1.033).primary
        self.assertNotEqual(second.track_id, first)

    def test_partially_outside_landmarks_are_flagged(self):
        tracker = HandTracker(SimulatedDetector([[simulated_hand((10, 30), spread=60)]]))
        hand = tracker.process(frame(), 1.0).primary
        self.assertFalse(hand.index_tip_in_frame)
        self.assertTrue(hand.in_frame[0])
        self.assertFalse(hand.in_frame.all())

    def test_invalid_or_stale_input_returns_no_hands(self):
        detector = SimulatedDetector([[simulated_hand((160, 200))]] * 3)
        tracker = HandTracker(detector)
        self.assertEqual(tracker.process(np.zeros((10, 10, 3), np.uint8), 1.0).status, INVALID_INPUT)
        self.assertEqual(tracker.process(frame(), float('nan')).status, INVALID_INPUT)
        self.assertEqual(tracker.process(frame(), 2.0).status, TRACKING)
        stale = tracker.process(frame(), 2.0)
        self.assertEqual((stale.status, stale.hands), (STALE_INPUT, ()))
        self.assertEqual(len(detector.calls), 1)

    def test_non_finite_detections_are_dropped(self):
        broken = simulated_hand((160, 200))
        broken['points'][3] = np.nan
        tracker = HandTracker(SimulatedDetector([[broken]]))
        self.assertEqual(tracker.process(frame(), 1.0).status, NO_HAND)

    def test_pil_rgb_and_bgr_arrays_reach_detector_as_rgb(self):
        rgb = frame()
        rgb[..., 0] = 200
        detector = SimulatedDetector([])
        tracker = HandTracker(detector)
        tracker.process(Image.fromarray(rgb), 1.0)
        tracker.process(rgb[..., ::-1].copy(), 1.1, color_order='BGR')
        tracker.process(rgb, 1.2)
        for seen, stamp in detector.calls:
            self.assertEqual(int(seen[0, 0, 0]), 200)
        self.assertEqual([c[1] for c in detector.calls], [1000, 1100, 1200])

    def test_rotation_mapping_round_trips_and_matches_capture_convention(self):
        corners = np.array([[0, 0], [1, 0], [1, 1], [0, 1], [.25, .75]])
        for rotation in (0, 90, 180, 270):
            np.testing.assert_allclose(raw_to_upright_norm(upright_to_raw_norm(corners, rotation), rotation), corners)
        np.testing.assert_allclose(upright_to_raw_norm([[.2, .7]], 90), [[.7, .8]])

    def test_rotation_mapping_matches_pil_upright_rotation(self):
        raw = np.zeros((40, 60, 3), np.uint8)
        raw[5, 50] = 255
        for rotation in (90, 180, 270):
            upright = np.asarray(Image.fromarray(raw).rotate(-rotation, expand=True))
            y, x = np.argwhere(upright[..., 0] == 255)[0]
            h, w = upright.shape[:2]
            mapped = upright_to_raw_norm([[(x + .5) / w, (y + .5) / h]], rotation)[0]
            np.testing.assert_allclose(mapped * [60, 40], [50.5, 5.5], atol=1e-6)

    def test_render_only_for_matching_current_frame(self):
        tracker = HandTracker(SimulatedDetector([[simulated_hand((160, 200))], []]))
        image = Image.fromarray(frame())
        result = tracker.process(image, 1.0, frame_id=7)
        drawn = np.asarray(draw_hands(image, result, frame_id=7))
        self.assertGreater(np.count_nonzero(drawn), 50)
        self.assertEqual(np.count_nonzero(np.asarray(image)), 0)
        self.assertEqual(np.count_nonzero(np.asarray(draw_hands(image, result, frame_id=8))), 0)
        empty = tracker.process(image, 1.1, frame_id=8)
        self.assertEqual(np.count_nonzero(np.asarray(draw_hands(image, empty, frame_id=8))), 0)


def fit(image, longest=960):
    scale = longest / max(image.size)
    return image if scale >= 1 else image.resize((round(image.width * scale), round(image.height * scale)),
                                                 Image.BILINEAR)


def recorded_images():
    frames = []
    for file in sorted(glob.glob(str(RECORDED / '*.npz'))):
        data = np.load(file)
        image = Image.fromarray(np.ascontiguousarray(data['color'][..., ::-1]))
        frames.append((fit(image), float(data['timestamp'])))
    return frames


@unittest.skipUnless(MODEL.exists() and RECORDED.exists(), 'local MediaPipe model or recorded iPhone frames unavailable')
class RecordedHandTracking(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames = recorded_images()

    def run_sequence(self, frames, rotation=0):
        tracker = HandTracker()
        try:
            results = []
            for i, (image, stamp) in enumerate(frames):
                results.append(tracker.process(image.rotate(-rotation, expand=True) if rotation else image, stamp, i))
            return results
        finally:
            tracker.close()

    def test_recorded_frames_track_21_landmarks_with_one_identity(self):
        results = self.run_sequence(self.frames)
        tracked = [r for r in results if r.status == TRACKING]
        self.assertGreaterEqual(len(tracked), len(results) - 1)
        for r in tracked:
            hand = r.primary
            self.assertEqual(hand.landmarks_px.shape, (21, 2))
            self.assertTrue(hand.index_tip_in_frame)
        for previous, current in zip(results, results[1:]):
            if previous.status == TRACKING and current.status == TRACKING:
                gap = current.capture_timestamp_s - previous.capture_timestamp_s
                same = current.primary.track_id == previous.primary.track_id
                self.assertEqual(same, gap <= .5, f'gap {gap:.3f}s')

    def test_rotated_camera_maps_back_to_same_fingertip(self):
        base = self.run_sequence(self.frames[:6])
        for rotation in (90, 270):
            rotated = self.run_sequence(self.frames[:6], rotation)
            for a, b in zip(base, rotated):
                if a.status != TRACKING or b.status != TRACKING:
                    continue
                w, h = a.image_size
                back = upright_to_raw_norm(b.primary.index_tip_norm, rotation) * [w, h]
                self.assertLess(np.linalg.norm(back - a.primary.index_tip_px) / np.hypot(w, h), .03)

    def test_disappearance_clears_and_reacquires(self):
        image, stamp = self.frames[0]
        blank = Image.new('RGB', image.size)
        sequence = [(image, stamp), (blank, stamp + .1), (blank, stamp + .2), (image, stamp + .3),
                    (blank, stamp + .4)] + [(blank, stamp + .5 + i * .1) for i in range(8)] + [(image, stamp + 1.5)]
        results = self.run_sequence(sequence)
        self.assertEqual(results[1].status, NO_HAND)
        self.assertEqual(results[3].status, TRACKING)
        self.assertEqual(results[3].primary.track_id, results[0].primary.track_id)
        self.assertEqual(results[-1].status, TRACKING)
        self.assertNotEqual(results[-1].primary.track_id, results[0].primary.track_id)

    def test_two_recorded_hands_keep_separate_identities(self):
        pairs = []
        for i, ((a, _), (b, _)) in enumerate(zip(self.frames[:6], self.frames[6:12])):
            canvas = Image.new('RGB', (a.width * 2, a.height))
            canvas.paste(a, (0, 0))
            canvas.paste(b, (a.width, 0))
            pairs.append((fit(canvas), 100 + i / 30))
        results = self.run_sequence(pairs)
        two = [r for r in results if len(r.hands) == 2]
        self.assertGreaterEqual(len(two), 4)
        sides = [{('L' if h.palm_center_px[0] < r.image_size[0] / 2 else 'R'): h.track_id for h in r.hands} for r in two]
        self.assertEqual(len({s['L'] for s in sides}), 1)
        self.assertEqual(len({s['R'] for s in sides}), 1)
        self.assertNotEqual(sides[0]['L'], sides[0]['R'])

    def test_occluded_frame_never_returns_previous_coordinates(self):
        image, stamp = self.frames[0]
        first = self.run_sequence([(image, stamp)])[0]
        x0, y0, x1, y1 = first.primary.box_px.astype(int)
        covered = image.copy()
        covered.paste((0, 0, 0), (x0, y0, x1, (y0 + y1) // 2))
        results = self.run_sequence([(image, stamp), (covered, stamp + .033)])
        hidden = results[1]
        self.assertIn(hidden.status, (TRACKING, NO_HAND))
        if hidden.status == TRACKING:
            self.assertEqual(hidden.primary.landmarks_px.shape, (21, 2))
            self.assertFalse(np.array_equal(hidden.primary.landmarks_px, results[0].primary.landmarks_px))


if __name__ == '__main__':
    unittest.main()
