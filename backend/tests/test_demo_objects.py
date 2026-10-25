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
        self.assertEqual(GuidanceDecider().decide(self.ev(lidar(.01, .01, .04)))[0], 'NEAR_OBJECT')
        estimate = Offset('ESTIMATED', delta_m=(.01, .01, .04), distance_m=.04)
        self.assertNotEqual(GuidanceDecider().decide(self.ev(estimate, tip=(330, 240)))[0], 'NEAR_OBJECT')


class EstimatedForwardTests(unittest.TestCase):
    def ev(self, offset, tip=(330, 240)):
        return Evidence(.05, True, (300, 150, 360, 330), tip, (640, 480), offset)

    def estimate(self, dz, status='ESTIMATED', disagreement=None, confidence='MEDIUM'):
        return Offset(status, delta_m=(.01, 0., dz), distance_m=abs(dz), target_confidence=confidence,
                      depth_disagreement_m=disagreement)

    def test_large_estimated_or_unreliable_gap_never_authorizes_depth_motion(self):
        for offset in (self.estimate(.10), self.estimate(-.09),
                       self.estimate(.10, 'UNRELIABLE', .033)):
            with self.subTest(offset=offset):
                cue, mode, reason = GuidanceDecider().decide(self.ev(offset))
                self.assertEqual((cue, mode), (None, '2D'))
                self.assertIn('need LiDAR depth', reason)

    def test_small_uncertain_or_low_confidence_gaps_stay_silent(self):
        self.assertIsNone(GuidanceDecider().decide(self.ev(self.estimate(.04)))[0])
        self.assertIsNone(GuidanceDecider().decide(self.ev(self.estimate(.10, 'UNRELIABLE', .072)))[0])
        self.assertIsNone(GuidanceDecider().decide(self.ev(self.estimate(.10, confidence='LOW')))[0])

    def test_lateral_correction_comes_first_and_no_centimetres(self):
        self.assertEqual(GuidanceDecider().decide(self.ev(self.estimate(.10), tip=(100, 240)))[0], 'RIGHT')
        player = Recorder()
        audio = AudioGuidance(player, numeric=True, confirm_frames=1)
        audio.update(self.ev(self.estimate(.12)), 1.)
        self.assertEqual(player.played, [])


class SummaryTests(unittest.TestCase):
    def test_left_to_right_confident_objects_only(self):
        found = [proposal((.7, .1, .8, .4), pepsi=1.35), proposal((.05, .1, .15, .4), coke=1.4),
                 proposal((.45, .5, .55, .8), brown_bagel=1.3), proposal((.3, .1, .35, .3), redbull=1.1),
                 proposal((.7, .1, .8, .25), pepsi=1.3)]
        self.assertEqual(demo.summarize(found), [('coke', 'left'), ('brown_bagel', 'center'), ('pepsi', 'right')])
        self.assertEqual(demo.summarize([proposal((.1, .1, .2, .2), other=1.5)]), [])


class ArgumentTests(unittest.TestCase):
    def test_demo_mode_argument(self):
        from r3_vision.live_tracking import arguments
        self.assertEqual(arguments(['--video', '0', '--detection', 'demo']).detection, 'demo')


if __name__ == '__main__':
    unittest.main()
