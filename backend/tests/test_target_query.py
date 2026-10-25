"""SIMULATED Gemini replies for the target-specific query; no network."""
import io
import json
import unittest

from PIL import Image

from r3_vision import gemini, target_query


def jpeg():
    buffer = io.BytesIO()
    Image.new('RGB', (640, 480), (90, 90, 90)).save(buffer, 'JPEG')
    return buffer.getvalue()


def run(reply, request='Find the black Owala tumbler'):
    sent = []

    def transport(url, headers, body, timeout):
        sent.append(json.loads(body))
        return 200, json.dumps({'candidates': [{'content': {'parts': [{'text': json.dumps(reply)}]}}]}).encode()
    client = gemini.GeminiClient(api_key='SIMULATED-test-api-key', model='gemini-3.5-flash-lite', transport=transport, live=False,
                                 thinking_level='low')
    client.media_resolution = 'medium'
    return target_query.run(jpeg(), request, client, frame_id='f1'), sent


class TargetQueryTests(unittest.TestCase):
    def test_malformed_target_replies_fail_without_selection(self):
        for reply in ({'status': 'FOUND', 'label': '', 'box_2d': [1, 2, 3, 4]},
                      {'status': 'AMBIGUOUS', 'label': '', 'candidates': []},
                      {'status': 'UNKNOWN', 'label': ''},
                      {'status': 'NOT_FOUND', 'label': 4}):
            with self.subTest(reply=reply):
                result, _ = run(reply)
                self.assertEqual(result['selection']['status'], 'PROVIDER_UNAVAILABLE')
                self.assertEqual(result['provider']['error']['kind'], 'MALFORMED_RESPONSE')

    def test_found_becomes_a_selected_target_on_the_same_frame(self):
        result, sent = run({'status': 'FOUND', 'label': 'black Owala water bottle', 'box_2d': [190, 386, 672, 532]})
        selection = result['selection']
        self.assertEqual(selection['status'], 'SELECTED')
        self.assertEqual(selection['target']['frameId'], result['screenModel']['frameId'])
        self.assertEqual(selection['target']['imageRegion'], {'x': .386, 'y': .19, 'width': .146, 'height': .482})
        config = sent[0]['generationConfig']
        self.assertEqual(config['responseSchema'], target_query.TARGET_SCHEMA)
        self.assertEqual(config['thinkingConfig'], {'thinkingLevel': 'low'})
        self.assertEqual(config['mediaResolution'], 'MEDIA_RESOLUTION_MEDIUM')
        self.assertIn('black Owala tumbler', sent[0]['contents'][0]['parts'][0]['text'])

    def test_ambiguous_lists_candidates_and_does_not_select(self):
        result, _ = run({'status': 'AMBIGUOUS', 'label': '', 'candidates': [
            {'label': 'brown bagel', 'box_2d': [400, 400, 600, 650]},
            {'label': 'seeded bagel', 'box_2d': [350, 300, 500, 420]}]}, 'Find the bagel')
        selection = result['selection']
        self.assertEqual(selection['status'], 'AMBIGUOUS')
        self.assertEqual(selection['candidateLabels'], ['brown bagel', 'seeded bagel'])
        self.assertNotIn('target', selection)

    def test_not_found_and_invalid_boxes_never_select(self):
        self.assertEqual(run({'status': 'NOT_FOUND', 'label': ''}, 'Find the banana')[0]['selection']['status'],
                         'NOT_FOUND')
        bad = run({'status': 'FOUND', 'label': 'cup', 'box_2d': [700, 500, 300, 900]})[0]['selection']
        self.assertEqual((bad['status'], bad['reason']), ('NOT_FOUND', 'INVALID_REGION'))

    def test_provider_failure_is_unavailable(self):
        def transport(url, headers, body, timeout):
            return 500, b'{"error": {"status": "INTERNAL", "message": "x"}}'
        client = gemini.GeminiClient(api_key='SIMULATED-test-api-key', model='gemini-3.8-flash', transport=transport, live=False)
        result = target_query.run(jpeg(), 'Find the cup', client, frame_id='f1')
        self.assertEqual(result['selection']['status'], 'PROVIDER_UNAVAILABLE')

    def test_inventory_prompt_no_longer_caps_objects(self):
        self.assertNotIn('at most 8', gemini.object_prompt('Find the bagel'))


if __name__ == '__main__':
    unittest.main()
