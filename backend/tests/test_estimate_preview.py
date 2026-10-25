"""Display-only Proprio-style signed metric differences; no guidance authorization."""
from dataclasses import replace
import unittest

from r3_vision.directional_distance import DistanceReadout
from r3_vision.spatial import Offset
from r3_vision.spatial_panel import estimate_preview


class EstimatePreviewTests(unittest.TestCase):
    def setUp(self):
        self.offset = Offset('ESTIMATED', fingertip_m=(.098, .049, .318), target_m=(.011, .029, .390),
                             delta_m=(-.087, -.020, .072), distance_m=.115,
                             fingertip_estimated=True, received_monotonic=10.)
        self.readout = DistanceReadout('UNRELIABLE', 'estimated fingertip', estimated=True,
                                       delta_cm=(-8.7, -2., 7.2))

    def test_old_example_is_visible_as_estimates_without_authorizing_axes(self):
        labels = estimate_preview(self.offset, self.readout, True, True)
        self.assertEqual(labels, dict(x='LEFT ≈8.7 cm', y='UP ≈2.0 cm', z='FORWARD ≈7.2 cm', qualifier='ESTIMATED'))
        self.assertEqual(self.readout.axes, ())
        self.assertNotIn('MOVE', ' '.join(labels.values()))

    def test_low_finger_confidence_can_be_observed_but_is_not_promoted_to_measured(self):
        value = replace(self.offset, fingertip_confidence='LOW', target_confidence='HIGH')
        self.assertIsNotNone(estimate_preview(value, self.readout, True, True))
        self.assertFalse(value.measured)

    def test_disagreement_stale_lost_missing_mismatched_or_invalid_data_hide_numbers(self):
        cases = [(self.offset, self.readout, False, True), (self.offset, self.readout, True, False),
                 (None, self.readout, True, True),
                 (replace(self.offset, status='UNRELIABLE', depth_disagreement_m=.069), self.readout, True, True),
                 (replace(self.offset, depth_disagreement_m=.069), self.readout, True, True),
                 (self.offset, replace(self.readout, delta_cm=None), True, True),
                 (self.offset, replace(self.readout, delta_cm=(1., 2., 3.)), True, True),
                 (replace(self.offset, target_m=(float('nan'), 0, 1)), self.readout, True, True)]
        for args in cases:
            with self.subTest(args=args):
                self.assertIsNone(estimate_preview(*args))

    def test_uncalibrated_direct_depth_is_still_labeled_unvalidated(self):
        direct = replace(self.offset, status='LIDAR', fingertip_estimated=False)
        self.assertEqual(estimate_preview(direct, self.readout, True, True)['qualifier'], 'UNVALIDATED')


if __name__ == '__main__':
    unittest.main()
