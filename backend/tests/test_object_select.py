"""SIMULATED screen models in the object prompt's label style; no Gemini calls."""
import unittest

from PIL import Image

from r3_vision import gemini, object_select
from r3_vision.lock_session import LockSession
from r3_vision.tracking_session import IdentifyJob, RequestBudget


def model(*labels):
    controls = [{'id': f'el-{i:03d}', 'label': label, 'legibility': 'clear', 'type': 'other',
                 'region': {'x': .1 + .2 * (i % 4), 'y': .2, 'width': .15, 'height': .2}}
                for i, label in enumerate(labels, 1)]
    return {'frameId': 'SIMULATED-f', 'availability': 'available', 'coordinateSystem': 'normalizedImageTopLeft',
            'controls': controls}


class ObjectSelectTests(unittest.TestCase):
    def test_joined_brand_words_match(self):
        result = object_select.select('Find the redbull can', model('Diet Pepsi can (drink)', 'Red Bull can (drink)'))
        self.assertEqual((result['status'], result['target']['label']), ('SELECTED', 'Red Bull can (drink)'))
        self.assertEqual(result['request'], 'Find the redbull can')

    def test_two_bagels_are_ambiguous_with_their_labels(self):
        m = model('large brown bagel (bread)', 'small seeded bagel (bread)', 'Red Bull can (drink)')
        result = object_select.select('Find the bagel', m)
        self.assertEqual(result['status'], 'AMBIGUOUS')
        self.assertEqual(object_select.candidates(result, m), ['large brown bagel (bread)', 'small seeded bagel (bread)'])

    def test_specific_request_selects_one_bagel(self):
        m = model('large brown bagel (bread)', 'small seeded bagel (bread)')
        result = object_select.select('Find the brown bagel', m)
        self.assertEqual((result['status'], result['target']['label']), ('SELECTED', 'large brown bagel (bread)'))

    def test_general_category_matches_only_when_unique(self):
        self.assertEqual(object_select.select('Find the bread', model('bagel (bread)', 'mug (cup)'))['status'], 'SELECTED')
        self.assertEqual(object_select.select('Find the bread', model('large brown bagel (bread)',
                                                                      'small seeded bagel (bread)'))['status'], 'AMBIGUOUS')

    def test_absent_object_is_not_found(self):
        result = object_select.select('Find the banana', model('bagel (bread)', 'Red Bull can (drink)'))
        self.assertEqual((result['status'], result['reason']), ('NOT_FOUND', 'NO_MATCH'))

    def test_short_fragments_do_not_join_match(self):
        self.assertEqual(object_select.select('Find the red', model('Red Bull can (drink)'))['status'], 'SELECTED')
        self.assertEqual(object_select.select('Find the ed', model('Red Bull can (drink)'))['status'], 'NOT_FOUND')

    def test_prompt_asks_for_distinct_labels_and_categories(self):
        self.assertIn('make every one of their labels different', gemini.OBJECT_PROMPT)
        self.assertIn('general category in\n  parentheses', gemini.OBJECT_PROMPT)


class AmbiguousSessionTests(unittest.TestCase):
    def test_ambiguity_waits_for_a_more_specific_request(self):
        s = LockSession(RequestBudget(0, 60, 100), reacquire_after=0)
        s.find('Find the bagel')
        image = Image.new('RGB', (64, 48))
        job = s.job(image, 1., 1.)
        result = {'provider': {'ok': True}, 'mode': 'SIMULATED', 'timingMs': {'provider': 5000},
                  'selection': {'status': 'AMBIGUOUS', 'reason': 'MULTIPLE_MATCHES',
                                'candidateLabels': ['large brown bagel (bread)', 'small seeded bagel (bread)']}}
        s.complete(job, result, 2.)
        self.assertEqual(s.state, 'AMBIGUOUS')
        self.assertIn('large brown bagel', s.detail)
        self.assertIsNone(s.job(image, 10., 10.), 'no repeated Gemini calls for the same ambiguous request')
        s.find('Find the brown bagel')
        self.assertIsNotNone(s.job(image, 11., 11.))


if __name__ == '__main__':
    unittest.main()
