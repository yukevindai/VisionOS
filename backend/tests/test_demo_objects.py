"""DEMO mode: request parsing, open-set identity decisions, and numeric / near-object speech. No models."""
import unittest
from types import SimpleNamespace

from r3_vision import demo_objects as demo
from r3_vision.audio_guidance import CM_PHRASES, AudioGuidance, Evidence, GuidanceDecider
from r3_vision.demo_objects import key_of_label, parse_request, resolve
from r3_vision.spatial import Offset


def proposal(box, **scores):
    base = {k: .7 for k in list(demo.OBJECTS) + [demo.OTHER]}
    base.update(scores)
    return dict(box=box, scores=base, names=['can'])


class RequestTests(unittest.TestCase):
    def test_six_objects_and_categories(self):
        self.assertEqual(parse_request('Find the Pepsi can')[0], ('pepsi',))
        self.assertEqual(parse_request('find the redbull')[0], ('redbull',))
        self.assertEqual(parse_request('Find the Coca-Cola can')[0], ('coke',))
        self.assertEqual(parse_request('Find the deck of cards')[0], ('cards',))
        self.assertEqual(parse_request('Find the white half bagel')[0], ('white_bagel',))
        self.assertEqual(parse_request('Find the brown bagel')[0], ('brown_bagel',))
        self.assertEqual(set(parse_request('Find the bagel')[0]), {'white_bagel', 'brown_bagel'})
        self.assertEqual(set(parse_request('Find the can')[0]), {'pepsi', 'redbull', 'coke'})
        self.assertIsNone(parse_request('Find my keys'))

    def test_recorded_label_mapping(self):
        self.assertEqual(key_of_label('seeded bagel half (bread)'), 'white_bagel')
        self.assertEqual(key_of_label('plain/blueberry bagel'), 'brown_bagel')
        self.assertEqual(key_of_label('Bicycle playing cards box'), 'cards')
        self.assertIsNone(key_of_label('bagel'))
        self.assertIsNone(key_of_label('blue can'))
        self.assertEqual(key_of_label("Rubik's cube (toy)"), demo.OTHER)


class ResolveTests(unittest.TestCase):
    def test_one_confident_match_locks_locally(self):
        found = [proposal((.1, .1, .2, .4), pepsi=1.35), proposal((.3, .1, .4, .4), redbull=1.33)]
        out = resolve(('pepsi',), 'Pepsi can', found)
        self.assertEqual((out['kind'], out['key'], out['box']), ('LOCAL', 'pepsi', (.1, .1, .2, .4)))

    def test_two_identical_matches_never_choose(self):
        found = [proposal((.1, .1, .2, .4), pepsi=1.35), proposal((.5, .1, .6, .4), pepsi=1.31)]
        out = resolve(('pepsi',), 'Pepsi can', found)
        self.assertIsNone(out['kind'])
        self.assertIn('Gemini must compare', out['reason'])

    def test_overlapping_boxes_of_one_object_are_one_candidate(self):
        found = [proposal((.1, .1, .2, .4), pepsi=1.35), proposal((.1, .1, .2, .25), pepsi=1.3)]
        self.assertEqual(resolve(('pepsi',), 'Pepsi can', found)['kind'], 'LOCAL')

    def test_unsure_or_absent_goes_to_gemini(self):
        unsure = [proposal((.1, .1, .2, .4), pepsi=1.12)]
        self.assertIsNone(resolve(('pepsi',), 'Pepsi can', unsure)['kind'])
        absent = [proposal((.1, .1, .2, .4), redbull=1.4)]
        out = resolve(('pepsi',), 'Pepsi can', absent)
        self.assertIsNone(out['kind'])
        other = [proposal((.1, .1, .2, .4), pepsi=1.25, other=1.3)]
        self.assertIsNone(resolve(('pepsi',), 'Pepsi can', other)['kind'])

    def test_confident_plus_unsure_lookalike_is_not_picked(self):
        found = [proposal((.1, .1, .2, .4), pepsi=1.35), proposal((.5, .1, .6, .4), pepsi=1.1)]
        self.assertIsNone(resolve(('pepsi',), 'Pepsi can', found)['kind'])

    def test_generic_bagel_with_both_bagels_is_ambiguous_locally(self):
        found = [proposal((.1, .5, .3, .8), white_bagel=1.4), proposal((.4, .5, .6, .8), brown_bagel=1.4)]
        out = resolve(('white_bagel', 'brown_bagel'), 'bagel', found)
        self.assertEqual(out['kind'], 'AMBIGUOUS')
        self.assertEqual(out['labels'], ['brown bagel', 'white half bagel'])
        self.assertEqual(resolve(('white_bagel',), 'white half bagel', found)['key'], 'white_bagel')

    def test_sift_supports_a_near_threshold_card_match(self):
        card = dict(proposal((.4, .3, .5, .5), cards=1.15), sift=dict(key='cards', inliers=40, box=(.4, .3, .5, .5)))
        self.assertEqual(resolve(('cards',), 'deck of cards', [card])['kind'], 'LOCAL')


class Recorder:
    def __init__(self):
        self.played = []

    def play(self, cue):
        self.played.append(cue)

    def stop(self):
        pass


def lidar(dx, dy, dz):
    return Offset('LIDAR', delta_m=(dx, dy, dz), distance_m=(dx * dx + dy * dy + dz * dz) ** .5)


class SpeechTests(unittest.TestCase):
    def ev(self, offset=None, tip=(100, 240)):
        return Evidence(.05, True, (300, 150, 360, 330), tip, (640, 480), offset)

    def test_numeric_cues_only_in_validated_3d(self):
        player = Recorder()
        audio = AudioGuidance(player, numeric=True, confirm_frames=1)
        audio.update(self.ev(lidar(-.08, 0, .3)), 1.)
        self.assertEqual(player.played, ['LEFT_8'])
        self.assertEqual(CM_PHRASES['LEFT_8'], 'Left, 8 centimeters.')
        audio.update(self.ev(lidar(-.081, 0, .3)), 1.5)  # same cue and distance: no repeat
        audio.update(self.ev(lidar(-.04, 0, .3)), 3.)  # moved 4 cm: updated distance
        self.assertEqual(player.played, ['LEFT_8', 'LEFT_4'])
        flat = AudioGuidance(Recorder(), numeric=True, confirm_frames=1)
        flat.update(self.ev(None), 1.)
        self.assertEqual(flat.player.played, ['RIGHT'])  # 2D: words only, never centimetres

    def test_near_object_cue_is_proximity_only(self):
        self.assertEqual(GuidanceDecider().decide(self.ev(lidar(.01, .01, .02)))[0], 'GRAB')
        self.assertNotEqual(GuidanceDecider().decide(self.ev(lidar(.01, .01, .04)))[0], 'GRAB')
        estimate = Offset('ESTIMATED', delta_m=(.01, .01, .04), distance_m=.04)
        self.assertIsNone(GuidanceDecider().decide(self.ev(estimate, tip=(330, 240)))[0])


class EstimatedForwardTests(unittest.TestCase):
    def ev(self, offset, tip=(330, 240)):
        return Evidence(.05, True, (300, 150, 360, 330), tip, (640, 480), offset)

    def estimate(self, dz, status='ESTIMATED', disagreement=None, confidence='MEDIUM'):
        return Offset(status, delta_m=(.01, 0., dz), distance_m=abs(dz), target_confidence=confidence,
                      depth_disagreement_m=disagreement)

    def test_large_estimated_gap_gives_word_only_forward_or_back(self):
        # Team decision (2026-10-25): word-only Forward/Back from a clearly large ESTIMATED gap, never centimetres.
        cue, mode, reason = GuidanceDecider().decide(self.ev(self.estimate(.10)))
        self.assertEqual((cue, mode), ('FORWARD', '2D+EST'))
        self.assertIn('not measured', reason)
        self.assertEqual(GuidanceDecider().decide(self.ev(self.estimate(-.09)))[0], 'BACKWARD')
        # Live case: direct tip depth disagrees by 3.3 cm, gap 10 cm -> sign still clear.
        self.assertEqual(GuidanceDecider().decide(self.ev(self.estimate(.10, 'UNRELIABLE', .033)))[0], 'FORWARD')

    def test_small_uncertain_or_low_confidence_gaps_stay_silent(self):
        self.assertIsNone(GuidanceDecider().decide(self.ev(self.estimate(.04)))[0])
        self.assertIsNone(GuidanceDecider().decide(self.ev(self.estimate(.10, 'UNRELIABLE', .072)))[0])
        self.assertIsNone(GuidanceDecider().decide(self.ev(self.estimate(.10, confidence='LOW')))[0])

    def test_grab_when_every_estimated_axis_is_within_3_cm(self):
        close = Offset('ESTIMATED', delta_m=(.01, -.02, .025), distance_m=.03, target_confidence='MEDIUM')
        cue, mode, reason = GuidanceDecider().decide(self.ev(close))
        self.assertEqual((cue, mode), ('GRAB', '2D+EST'))
        self.assertIn('estimated', reason)
        side_off = Offset('ESTIMATED', delta_m=(.05, 0., .02), distance_m=.05, target_confidence='MEDIUM')
        self.assertNotEqual(GuidanceDecider().decide(self.ev(side_off))[0], 'GRAB')
        doubtful = Offset('UNRELIABLE', delta_m=(.01, 0., .02), distance_m=.02, target_confidence='MEDIUM',
                          depth_disagreement_m=.04)
        self.assertNotEqual(GuidanceDecider().decide(self.ev(doubtful))[0], 'GRAB')
        self.assertNotEqual(GuidanceDecider().decide(self.ev(close, tip=(100, 240)))[0], 'GRAB')  # not over it

    def test_lateral_correction_comes_first_and_no_centimetres(self):
        self.assertEqual(GuidanceDecider().decide(self.ev(self.estimate(.10), tip=(100, 240)))[0], 'RIGHT')
        player = Recorder()
        audio = AudioGuidance(player, numeric=True, confirm_frames=1)
        audio.update(self.ev(self.estimate(.12)), 1.)
        self.assertEqual(player.played, ['FORWARD'])  # word only, never "Forward, 12 centimeters"


class SummaryTests(unittest.TestCase):
    def test_left_to_right_confident_objects_only(self):
        found = [proposal((.7, .1, .8, .4), pepsi=1.35), proposal((.05, .1, .15, .4), coke=1.4),
                 proposal((.45, .5, .55, .8), brown_bagel=1.3), proposal((.3, .1, .35, .3), redbull=1.1),
                 proposal((.7, .1, .8, .25), pepsi=1.3)]
        self.assertEqual(demo.summarize(found), [('coke', 'left'), ('brown_bagel', 'center'), ('pepsi', 'right')])
        self.assertEqual(demo.summarize([proposal((.1, .1, .2, .2), other=1.5)]), [])


class TrackingLostTests(unittest.TestCase):
    def test_hand_dropout_or_alignment_never_says_tracking_lost(self):
        player = Recorder()
        audio = AudioGuidance(player, confirm_frames=1, lost_on_invalid=False)
        audio.update(Evidence(.05, True, (300, 150, 360, 330), (100, 240), (640, 480), None), 1.)  # RIGHT
        for t in (1.5, 2., 3., 6.):
            audio.update(Evidence(.05, True, (300, 150, 360, 330), None, (640, 480), None), t)  # hand gone
            audio.tick(t)
        self.assertNotIn('TRACKING_LOST', player.played)
        self.assertTrue(audio.target_left_view(7.))
        self.assertFalse(audio.target_left_view(8.))  # at most once every 4 s
        self.assertEqual(player.played.count('TRACKING_LOST'), 1)

    def worker(self):
        import numpy as np
        from r3_vision.live_tracking import TrackingWorker
        w = TrackingWorker.__new__(TrackingWorker)
        w.audio = SimpleNamespace(calls=[], target_left_view=lambda now: w.audio.calls.append(now))
        w.session = SimpleNamespace(state='LOCKED', label='Pepsi can')
        box = lambda x0, y0, x1, y1: np.float32([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
        return w, box

    def test_lost_only_after_leaving_the_screen_for_1_5_s(self):
        w, box = self.worker()
        lost = SimpleNamespace(polygon=None, reason='Tracker confidence too low')
        self.assertFalse(w._watch_target_exit(SimpleNamespace(polygon=box(600, 100, 639, 300)), (640, 480), now=10.))
        w.session.state = 'TARGET LOST'
        self.assertFalse(w._watch_target_exit(lost, (640, 480), now=10.1))
        self.assertFalse(w._watch_target_exit(lost, (640, 480), now=11.))  # 0.9 s: still waiting
        self.assertTrue(w._watch_target_exit(lost, (640, 480), now=11.7))  # 1.6 s off screen
        self.assertEqual(len(w.audio.calls), 1)
        w.session.state = 'HIDDEN'
        w._watch_target_exit(SimpleNamespace(polygon=box(200, 100, 300, 300)), (640, 480), now=20.)
        w.session.state = 'TARGET LOST'
        w._watch_target_exit(SimpleNamespace(polygon=None, reason='Remembered location left the frame; target lost'),
                             (640, 480), now=20.1)
        self.assertTrue(w._watch_target_exit(lost, (640, 480), now=21.7))

    def test_coming_back_within_1_5_s_or_losing_a_centred_object_stays_silent(self):
        w, box = self.worker()
        w._watch_target_exit(SimpleNamespace(polygon=box(600, 100, 639, 300)), (640, 480), now=10.)
        w.session.state = 'TARGET LOST'
        w._watch_target_exit(SimpleNamespace(polygon=None, reason='x'), (640, 480), now=10.1)
        w.session.state = 'LOCKED'
        w._watch_target_exit(SimpleNamespace(polygon=box(580, 100, 630, 300)), (640, 480), now=11.)  # back
        w.session.state = 'LOCKED'
        self.assertFalse(w._watch_target_exit(SimpleNamespace(polygon=box(580, 100, 630, 300)), (640, 480), now=12.))
        w2, box2 = self.worker()
        w2._watch_target_exit(SimpleNamespace(polygon=box2(250, 150, 350, 330)), (640, 480), now=10.)
        w2.session.state = 'TARGET LOST'
        for t in (10.1, 11., 13.):
            self.assertFalse(w2._watch_target_exit(SimpleNamespace(polygon=None, reason='Evidence stayed weak'),
                                                   (640, 480), now=t))
        self.assertEqual(w.audio.calls + w2.audio.calls, [])


class ArgumentTests(unittest.TestCase):
    def test_demo_mode_argument(self):
        from r3_vision.live_tracking import arguments
        self.assertEqual(arguments(['--video', '0', '--detection', 'demo']).detection, 'demo')


if __name__ == '__main__':
    unittest.main()
