"""SIMULATED REST responses, including model catalogs; no live model claims."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r3_vision.agent_providers import AnthropicRouter, GeminiProvider, ProviderError


class ProviderTests(unittest.TestCase):
    def test_anthropic_typed_tool_and_credentials_only_header(self):
        calls = []
        def send(url, headers, body=None, timeout=5):
            calls.append((url, headers, body, timeout))
            return {'content': [{'type': 'tool_use', 'name': 'describe_scene', 'input': {'query': 'What is visible?'}}]}
        router = AnthropicRouter('SIMULATED-key', 'claude-haiku-simulated', transport=send)
        route = router.route('What is visible?', [], {})
        self.assertEqual((route.action, route.query), ('describe', 'What is visible?'))
        url, headers, body, timeout = calls[0]
        self.assertEqual(headers['x-api-key'], 'SIMULATED-key')
        self.assertNotIn('SIMULATED-key', url + json.dumps(body))
        self.assertLessEqual(timeout, 5)

    def test_multiple_or_unknown_tools_rejected(self):
        for content in ([{'type': 'tool_use', 'name': 'move', 'input': {}}],
                        [{'type': 'tool_use', 'name': 'resolve_target', 'input': {'query': 'cup'}}] * 2,
                        [{'type': 'tool_use', 'name': 'resolve_target', 'input': {'query': 'cup', 'movement': 'left'}}]):
            with self.subTest(content=content):
                router = AnthropicRouter('SIMULATED', 'claude-haiku-simulated',
                    transport=lambda *a: {'content': content})
                with self.assertRaises(ProviderError):
                    router.route('find', [], {})

    def test_explicit_sonnet_escalation(self):
        models = []
        def send(url, headers, body, timeout):
            models.append(body['model'])
            name, args = ('escalate', {}) if len(models) == 1 else ('request_clarification', {'message': 'Which item?'})
            return {'content': [{'type': 'tool_use', 'name': name, 'input': args}]}
        router = AnthropicRouter('SIMULATED', 'claude-haiku-simulated', 'claude-sonnet-simulated', transport=send)
        self.assertEqual(router.route('complex', [], {}).action, 'clarify')
        self.assertEqual(models, ['claude-haiku-simulated', 'claude-sonnet-simulated'])

    def test_paginated_authenticated_catalog_and_missing_model(self):
        urls = []
        def send(url, headers, body=None, timeout=5):
            urls.append(url)
            return ({'data': [{'id': 'other'}], 'has_more': True, 'last_id': 'other'} if len(urls) == 1
                    else {'data': [{'id': 'claude-haiku-simulated'}], 'has_more': False})
        router = AnthropicRouter('SIMULATED', 'claude-haiku-simulated', transport=send)
        self.assertTrue(router.verify_models()['verified'])
        self.assertIn('after_id=other', urls[1])
        router.transport = lambda *a, **k: {'data': [], 'has_more': False}
        with self.assertRaises(ProviderError):
            router.verify_models()

    def test_gemini_evidence_and_json_response(self):
        calls = []
        def send(url, headers, body=None, timeout=5):
            calls.append((url, headers, body))
            return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': '{"text":"cup"}'}]}}]}
        provider = GeminiProvider('SIMULATED-key', 'gemini-flash-simulated', transport=send)
        answer = provider.generate('Evidence only', 'What?', [{'jpeg': b'simulated-jpeg', 'frame_id': 'f1', 'observed_at': 10}])
        self.assertEqual(answer, {'text': 'cup'})
        url, headers, body = calls[0]
        self.assertEqual(headers['x-goog-api-key'], 'SIMULATED-key')
        self.assertNotIn('SIMULATED-key', url + json.dumps(body))
        self.assertEqual(body['contents'][0]['parts'][2]['inlineData']['mimeType'], 'image/jpeg')

    def test_gemini_truncated_or_blocked_response_rejected(self):
        for result in ({'candidates': []}, {'candidates': [{'finishReason': 'MAX_TOKENS'}]},
                       {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': 'not JSON'}]}}]}):
            provider = GeminiProvider('SIMULATED', 'gemini-flash-simulated', transport=lambda *a: result)
            with self.assertRaises(ProviderError):
                provider.generate('Evidence only', 'What?', [{'jpeg': b'fake', 'frame_id': 'f', 'observed_at': 1}])

    def test_gemini_catalog_requires_generate_content(self):
        provider = GeminiProvider('SIMULATED', 'gemini-flash-simulated', transport=lambda *a, **k:
            {'models': [{'name': 'models/gemini-flash-simulated', 'supportedGenerationMethods': ['embedContent']}]})
        with self.assertRaises(ProviderError):
            provider.verify_models()


if __name__ == '__main__':
    unittest.main()
