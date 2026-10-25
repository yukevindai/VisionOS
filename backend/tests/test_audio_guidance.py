"""SIMULATED spatial coordinates; a recording player stands in for audio."""
import unittest

from r3_vision.audio_guidance import AudioGuidance, Evidence, GuidanceDecider
from r3_vision.spatial import Offset

SIZE = (640, 480)
BOX = (300, 150, 360, 330)


class Recorder:
    def __init__(self):
        self.played, self.stops = [], 0

    def play(self, cue):
        self.played.append(cue)

    def stop(self):
        self.stops += 1


def ev(tip=(100, 240), age=.05, locked=True, box=BOX, offset=None):
    return Evidence(age, locked, box, tip, SIZE, offset)


def lidar(dx, dy, dz):
    return Offset('LIDAR', delta_m=(dx, dy, dz), distance_m=(dx * dx + dy * dy + dz * dz) ** .5)


class DeciderTests(unittest.TestCase):
    def test_2d_directions_only(self):
        d = GuidanceDecider()
        self.assertEqual(d.decide(ev(tip=(100, 240)))[:2], ('RIGHT', '2D'))
        self.assertEqual(GuidanceDecider().decide(ev(tip=(500, 240)))[0], 'LEFT')
        self.assertEqual(GuidanceDecider().decide(ev(tip=(330, 50)))[0], 'DOWN')
        self.assertEqual(GuidanceDecider().decide(ev(tip=(330, 420)))[0], 'UP')
        cue, mode, reason = GuidanceDecider().decide(ev(tip=(330, 240)))
        self.assertIsNone(cue)
        self.assertIn('need LiDAR', reason)

    def test_estimated_depth_never_gives_3d_cues(self):
        estimate = Offset('ESTIMATED', delta_m=(0., 0., .02), distance_m=.02)
        cue, mode, _ = GuidanceDecider().decide(ev(tip=(330, 240), offset=estimate))
        # Estimated depth may only say Grab (all axes within 3 cm), labelled as an estimate, never as measured 3D.
        self.assertEqual((cue, mode), ('GRAB', '2D+EST'))
        cue, mode, _ = GuidanceDecider().decide(ev(tip=(330, 240), offset=Offset('ESTIMATED', delta_m=(0., 0., .045),
                                                                                    distance_m=.045)))
        self.assertIsNone(cue)
        self.assertEqual(mode, '2D')

    def test_3d_sequence_left_forward_closer_ready(self):
        d = GuidanceDecider()
        self.assertEqual(d.decide(ev(offset=lidar(-.12, 0, .30)))[:2], ('LEFT', '3D'))
        self.assertEqual(d.decide(ev(offset=lidar(-.01, 0, .30)))[0], 'FORWARD')
        self.assertEqual(d.decide(ev(offset=lidar(-.01, 0, .12)))[0], 'CLOSER')
        self.assertEqual(d.decide(ev(offset=lidar(.01, .01, .04)))[0], 'CLOSER')  # forward still 4 cm
        self.assertEqual(d.decide(ev(offset=lidar(.01, .02, .025)))[0], 'GRAB')  # every axis within 3 cm
        self.assertEqual(GuidanceDecider().decide(ev(offset=lidar(0, 0, -.2)))[0], 'BACKWARD')

    def test_hysteresis_keeps_axis_until_well_inside_tolerance(self):
        d = GuidanceDecider()
        self.assertEqual(d.decide(ev(offset=lidar(-.05, 0, .3)))[0], 'LEFT')
        self.assertEqual(d.decide(ev(offset=lidar(-.025, 0, .3)))[0], 'LEFT')
        self.assertEqual(d.decide(ev(offset=lidar(-.01, 0, .3)))[0], 'FORWARD')

    def test_every_safety_check_silences(self):
        for case in (ev(age=.31), ev(age=float('nan')), ev(locked=False), ev(box=None), ev(tip=None),
                     ev(tip=(700, 10)), ev(box=(300, 150, 700, 330))):
            self.assertIsNone(GuidanceDecider().decide(case)[0])


class SchedulerTests(unittest.TestCase):
    def test_speaks_on_change_and_repeats_sparingly(self):
        r = Recorder()
        g = AudioGuidance(r)
        for i in range(20):
            g.update(ev(), 10 + i * .1)
        self.assertEqual(r.played, ['RIGHT'])
        g.update(ev(), 12.6)
        self.assertEqual(r.played, ['RIGHT', 'RIGHT'])

    def test_direction_change_needs_two_frames(self):
        r = Recorder()
        g = AudioGuidance(r)
        g.update(ev(tip=(100, 240)), 1.)
        g.update(ev(tip=(500, 240)), 1.07)
        self.assertEqual(r.played, ['RIGHT'])
        g.update(ev(tip=(500, 240)), 1.14)
        self.assertEqual(r.played, ['RIGHT', 'LEFT'])

    def test_invalid_evidence_stops_after_grace_and_announces_lost_once(self):
        r = Recorder()
        g = AudioGuidance(r)
        g.update(ev(), 1.)
        g.update(ev(tip=None), 1.05)
        self.assertEqual(r.stops, 0, 'a one-frame dropout must not cut the word being spoken')
        g.update(ev(tip=None), 1.36)
        self.assertEqual(r.stops, 1)
        self.assertIsNone(g.current)
        self.assertNotIn('TRACKING_LOST', r.played)
        g.update(ev(tip=None), 1.7)
        g.update(ev(tip=None), 2.5)
        self.assertNotIn('TRACKING_LOST', r.played)  # 1.45 s: the 1.5 s wait has not passed
        g.update(ev(tip=None), 2.6)
        self.assertEqual(r.played.count('TRACKING_LOST'), 1)

    def test_brief_dropout_neither_cuts_nor_repeats_the_cue(self):
        r = Recorder()
        g = AudioGuidance(r)
        for i in range(30):
            g.update(ev(tip=None if i % 2 else (100, 240)), 1 + i * .07)
        self.assertEqual((r.played, r.stops), (['RIGHT'], 0))

    def test_no_new_cue_from_invalid_evidence_during_grace(self):
        r = Recorder()
        g = AudioGuidance(r)
        g.update(ev(tip=(100, 240)), 1.)
        g.update(ev(tip=(500, 240), age=.5), 1.07)
        g.update(ev(tip=(500, 240), age=.5), 1.14)
        self.assertEqual(r.played, ['RIGHT'])

    def test_single_frame_hand_dropouts_do_not_announce_lost(self):
        r = Recorder()
        g = AudioGuidance(r)
        for i in range(40):
            g.update(ev(tip=None if i % 2 else (100, 240)), 1 + i * .07)
        self.assertNotIn('TRACKING_LOST', r.played)

    def test_stream_stall_silences_without_new_frames(self):
        r = Recorder()
        g = AudioGuidance(r)
        g.update(ev(), 1.)
        g.tick(1.2)
        g.tick(1.35)
        self.assertEqual(r.stops, 0)
        g.tick(1.7)
        self.assertEqual(r.stops, 1)
        g.tick(2.)
        g.tick(2.4)
        self.assertEqual(r.played, ['RIGHT'])  # still within the 1.5 s wait
        g.tick(2.9)
        self.assertEqual(r.played, ['RIGHT', 'TRACKING_LOST'])


class PlayerMixingTests(unittest.TestCase):
    def test_persistent_stream_callback_plays_then_silences(self):
        import threading
        import numpy as np
        from r3_vision.audio_guidance import ClipPlayer
        player = ClipPlayer.__new__(ClipPlayer)
        player.buffer_lock = threading.Lock()
        player.buffer, player.position = np.ones(25, 'float32'), 0
        out = np.full((10, 1), 9., 'float32')
        for expected in (10, 10, 5):
            player._callback(out, 10, None, None)
            self.assertEqual(int(out.sum()), expected)
        self.assertIsNone(player.buffer)
        player._callback(out, 10, None, None)
        self.assertEqual(int(out.sum()), 0)


class CooldownTests(unittest.TestCase):
    def test_users_own_find_skips_the_cooldown_but_retries_do_not(self):
        from PIL import Image
        from r3_vision.lock_session import LockSession
        from r3_vision.tracking_session import RequestBudget
        s = LockSession(RequestBudget(20, 8, 20), reacquire_after=0)
        image = Image.new('RGB', (64, 48))
        s.find('Find the Red Bull can')
        self.assertIsNotNone(s.job(image, 10., 10.))
        s.inflight = None
        s.find('Find the Pepsi can')
        self.assertIsNotNone(s.job(image, 12., 12.), 'a new user request must not wait 20 s')
        s.inflight, s.tracker, s.lost_at = None, None, 0.
        self.assertIsNone(s.job(image, 14., 14.), 'automatic re-identification keeps the cooldown')


if __name__ == '__main__':
    unittest.main()
