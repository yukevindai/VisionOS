"""Simulated geometry/clock tests. No physical sensor or audible speech validation."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

from r3_vision.directional_distance import Calibration, DirectionalDistance, DistanceReadout, AxisDistance
from r3_vision.distance_speech import DistanceSpeech
from r3_vision.frame_packet import make_packet
from r3_vision.spatial import Offset, SpatialOffset, _deskguide


K = np.array([[300., 0, 160.], [0, 300., 120.], [0, 0, 1.]])
HOST, PORT, UTC = 'SIMULATED', 8888, 1000.


def packet(received=10.):
    return make_packet(Image.new('RGB', (320, 240)), received,
                       {'sourceFrameID': 1, 'connectionEpoch': 1, 'sensorTimestamp': 9.95}, 'SIMULATED', 0, 640)


def calibration(**changes):
    record = dict(schema=1, physicallyValidated=True, validationId='SIMULATED TEST ONLY',
                  validatedAtUnixS=UTC, coordinateSystem='display_rotated_camera_optical', mirrored=False,
                  host=HOST, port=PORT, rotationClockwise=0, rawSize=[320, 240], imageSize=[320, 240],
                  intrinsics=K.tolist(), depthAlignmentVerified=True,
                  sensorClock=dict(offsetToMonotonicS=0., uncertaintyS=.01, verifiedAtMonotonicS=10.,
                                   validForS=60., connectionEpoch=1),
                  axisChecks={a: dict(samples=3, maxErrorM=.01, positiveDirectionVerified=True) for a in 'xyz'})
    record.update(changes)
    return Calibration(record)


def offset(delta=(-.087, -.02, .072), **changes):
    finger = (.098, .049, .318)
    target = tuple(a + b for a, b in zip(finger, delta))
    result = Offset('LIDAR', finger, target, delta, float(np.linalg.norm(delta)), 'direct tip LiDAR',
                    target_confidence='HIGH', fingertip_confidence='HIGH', depth_disagreement_m=.01,
                    received_monotonic=10.)
    return replace(result, **changes)


def frame():
    return SimpleNamespace(received=10., K=K.copy())


class DistanceTests(unittest.TestCase):
    def controller(self, **kwargs):
        return DirectionalDistance(calibration(), HOST, PORT, **kwargs)

    def update(self, controller=None, value=None, **kwargs):
        data = dict(offset=offset() if value is None else value, packet=packet(), frame=frame(),
                    locked=True, hand_same_frame=True, now=10.1, utc=UTC)
        data.update(kwargs)
        return (controller or self.controller()).update(**data)

    def test_example_signed_deltas_without_claiming_physical_accuracy(self):
        r = self.update(self.controller(enter_m=.005, release_m=.01))
        self.assertEqual([a.direction for a in r.axes], ['LEFT', 'UP', 'FORWARD'])
        np.testing.assert_allclose([a.cm for a in r.axes], [8.7, 2., 7.2])
        self.assertEqual(r.axes[2].text, 'REACH FORWARD: 7.2 cm')
        np.testing.assert_allclose(r.delta_cm, [-8.7, -2., 7.2])

    def test_opposite_directions_and_euclidean_separate(self):
        r = self.update(value=offset((.1, .1, -.2)))
        self.assertEqual([a.direction for a in r.axes], ['RIGHT', 'DOWN', 'BACK'])
        self.assertAlmostEqual(r.euclidean_cm, np.sqrt(600))

    def test_old_aligned_deadband_does_not_flip_at_boundary(self):
        c = self.controller()
        for v, expected in ((.06, 'RIGHT'), (.024, 'ALIGNED'), (.04, 'ALIGNED'), (-.04, 'ALIGNED'), (-.06, 'LEFT')):
            r = self.update(c, offset((v, .1, .1)))
            self.assertEqual(r.axes[0].direction, expected)

    def test_missing_calibration_is_unreliable_and_only_raw_deltas_remain(self):
        r = self.update(DirectionalDistance())
        self.assertFalse(r.axes)
        self.assertEqual(r.status, 'UNRELIABLE')
        self.assertIsNotNone(r.delta_cm)

    def test_estimated_palm_fingertip_never_authorizes_instructions(self):
        r = self.update(value=offset(status='ESTIMATED', fingertip_estimated=True))
        self.assertFalse(r.axes)
        self.assertTrue(r.estimated)
        self.assertIn('ESTIMATED', r.reason)

    def test_six_point_nine_cm_disagreement_blocks_all_axes(self):
        r = self.update(value=offset(depth_disagreement_m=.069, tip_check='direct tip differs by 6.9 cm'))
        self.assertFalse(r.axes)
        self.assertIn('6.9', r.reason)

    def test_stale_future_invalid_clock_hand_target_and_frame_mismatch(self):
        cases = [dict(now=10.301), dict(now=9.9), dict(now=float('nan')), dict(locked=False),
                 dict(hand_same_frame=False), dict(frame=None), dict(frame=SimpleNamespace(received=9., K=K)),
                 dict(value=offset(received_monotonic=9.)), dict(value=offset(delta_m=(float('nan'), 0, .1)))]
        for changes in cases:
            with self.subTest(changes=changes):
                value = changes.pop('value', None)
                self.assertFalse(self.update(value=value, **changes).axes)

    def test_missing_confidence_or_crosscheck_blocks(self):
        for changes in (dict(target_confidence='LOW'), dict(fingertip_confidence='UNAVAILABLE'),
                        dict(depth_disagreement_m=None)):
            self.assertFalse(self.update(value=offset(**changes)).axes)

    def test_calibration_source_orientation_intrinsics_expiry_and_quality(self):
        for changes in (dict(host='OTHER'), dict(rotationClockwise=90), dict(mirrored=True),
                        dict(rawSize=[640, 480]), dict(intrinsics=np.eye(3).tolist()),
                        dict(validatedAtUnixS=UTC - 90000), dict(physicallyValidated=False),
                        dict(depthAlignmentVerified=False), dict(axisChecks={})):
            c = DirectionalDistance(calibration(**changes), HOST, PORT)
            self.assertFalse(self.update(c).axes)

    def test_local_freshness_does_not_authorize_old_or_unsynchronized_sensor_exposure(self):
        for clock in (None, {}, dict(offsetToMonotonicS=-1., uncertaintyS=.01, verifiedAtMonotonicS=10.,
                                      validForS=60., connectionEpoch=1),
                      dict(offsetToMonotonicS=0., uncertaintyS=.01, verifiedAtMonotonicS=-100.,
                           validForS=60., connectionEpoch=1)):
            c = DirectionalDistance(calibration(sensorClock=clock), HOST, PORT)
            self.assertFalse(self.update(c).axes)


class DepthProvenanceTests(unittest.TestCase):
    def measure(self, direct_z, fused_z=.318):
        from tests.test_spatial import simulated_frame, hand, box
        spatial = SpatialOffset()
        geometry, _, _ = _deskguide()
        measured = SimpleNamespace(xyz=np.array([.098, .049, fused_z]), source='palm LiDAR + hand geometry (fused estimate)')
        tip = None if direct_z is None else SimpleNamespace(xyz=np.array([.098, .049, direct_z]), source='direct LiDAR')
        spatial.fusion = SimpleNamespace(resolve=lambda *args: (measured, None, 'fused estimate'),
                                         diagnostics={'confidence_values': [2] * 11})
        with patch.object(geometry, 'fingertip_depth', return_value=tip):
            return spatial.measure(simulated_frame(.6), box(40, 40, 140, 130), hand(), 10.)

    def test_missing_direct_tip_is_estimated_even_with_palm_lidar(self):
        r = self.measure(None)
        self.assertEqual(r.status, 'ESTIMATED')
        self.assertFalse(r.measured)
        self.assertTrue(r.fingertip_estimated)

    def test_disagreement_is_rejected_numerically_not_just_printed(self):
        r = self.measure(.387)
        self.assertEqual(r.status, 'UNRELIABLE')
        self.assertAlmostEqual(r.depth_disagreement_m, .069)
        self.assertFalse(r.measured)

    def test_agreeing_high_confidence_direct_tip_is_used_instead_of_estimate(self):
        r = self.measure(.32)
        self.assertTrue(r.measured)
        self.assertEqual(r.fingertip_source, 'direct LiDAR')
        self.assertAlmostEqual(r.fingertip_m[2], .32)


class SpeechTests(unittest.TestCase):
    def readout(self, cm=9, direction='LEFT'):
        return DistanceReadout('VALIDATED CAMERA RELATIVE', 'SIMULATED', (AxisDistance('x', direction, cm),),
                               valid_until_monotonic=20.)

    def player(self):
        return SimpleNamespace(play=Mock(), stop=Mock(), refresh=Mock(), busy=False)

    def test_numeric_speech_and_no_constant_repetition(self):
        p = self.player()
        g = DistanceSpeech(p)
        g.update(self.readout(), 10., 9.9)
        g.update(self.readout(), 10.2, 10.1)
        self.assertEqual(p.play.call_count, 1)
        self.assertEqual(p.play.call_args.args[0], 'Left, 9 centimeters.')
        self.assertAlmostEqual(p.play.call_args.args[1], 10.2)
        self.assertNotIn('grab', g.current.lower())

    def test_no_overlap_refreshes_fresh_evidence(self):
        p = self.player()
        g = DistanceSpeech(p)
        g.update(self.readout(), 10., 9.9)
        p.busy = True
        g.update(self.readout(), 10.2, 10.1)
        self.assertEqual(p.play.call_count, 1)
        p.refresh.assert_called_once()

    def test_unreliable_or_stale_stops_immediately_and_stall_tick_stops(self):
        p = self.player()
        g = DistanceSpeech(p)
        g.update(self.readout(), 10., 9.9)
        g.update(DistanceReadout('UNRELIABLE', '6.9 cm disagreement'), 10.1, 10.)
        self.assertIsNone(g.current)
        self.assertEqual(p.stop.call_count, 1)
        g.update(self.readout(), 11., 10.)
        self.assertEqual(p.play.call_count, 1)
        g.tick(20.)
        self.assertIsNone(g.current)

    def test_direction_change_stops_old_speech_before_next(self):
        p = self.player()
        g = DistanceSpeech(p)
        g.update(self.readout(), 10., 9.9)
        p.busy = True
        g.update(self.readout(direction='RIGHT'), 10.2, 10.1)
        p.stop.assert_called_once()
        self.assertEqual(p.play.call_count, 1)


class WorkerSafetyIntegrationTests(unittest.TestCase):
    def test_live_loop_keeps_existing_audio_silent_without_validated_geometry(self):
        from tests.test_discovery_defaults import FreshSource
        from r3_vision.live_tracking import TrackingWorker, arguments
        player = SimpleNamespace(play=Mock(), stop=Mock(), close=Mock(), status='SIMULATED')
        with tempfile.TemporaryDirectory() as directory:
            args = arguments(['--video', 'SIMULATED', '--tracker', 'orb', '--detection', 'gemini',
                              '--no-hands', '--audio'])
            args.output = Path(directory)
            with patch('r3_vision.live_tracking.Source', FreshSource), \
                    patch('r3_vision.audio_guidance.ClipPlayer', return_value=player):
                worker = TrackingWorker(args, Mock())
                try:
                    deadline = time.monotonic() + 2
                    while worker.panel is None and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertIsNotNone(worker.panel)
                    self.assertEqual(worker.panel['distances'].status, 'UNRELIABLE')
                    self.assertIsNone(worker.audio.current)
                    # No guidance cue ever starts; reset no longer stops unrelated speech (scene descriptions).
                    player.play.assert_not_called()
                finally:
                    worker.close()


if __name__ == '__main__':
    unittest.main()
