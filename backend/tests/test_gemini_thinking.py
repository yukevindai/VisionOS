"""SIMULATED REST transport: verifies model/effort payload, never calls Gemini."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r3_vision.gemini import GeminiClient


class GeminiThinkingTests(unittest.TestCase):
    def test_38_high_uses_correct_rest_field_and_native_temperature(self):
        reply = {"panelType": "other", "availability": "available", "isPaymentScreen": False,
                 "sections": [], "elements": []}
        calls = []
        def transport(url, headers, body, timeout):
            calls.append((url, json.loads(body)))
            return 200, json.dumps({'candidates': [{'content': {'parts': [
                {'thought': True, 'text': 'SIMULATED hidden summary'}, {'text': json.dumps(reply)}]}}]}).encode()
        client = GeminiClient(api_key='SIMULATED-key', model='gemini-3.8-flash',
                              thinking_level='high', transport=transport, live=False)
        result = client.describe(b'SIMULATED image')
        self.assertTrue(result.ok)
        self.assertEqual(result.reply, reply)
        self.assertIn('/gemini-3.8-flash:generateContent', calls[0][0])
        config = calls[0][1]['generationConfig']
        self.assertEqual(config['thinkingConfig'], {'thinkingLevel': 'high'})
        self.assertNotIn('temperature', config)
        self.assertIn('responseSchema', config)
        self.assertNotIn('SIMULATED-key', json.dumps(calls))

    def test_invalid_effort_or_explicit_older_model_setting_makes_no_call(self):
        for level in ['ultra', 'minimal']:
            with self.assertRaises(ValueError):
                GeminiClient(api_key='SIMULATED', model='gemini-3.8-flash', thinking_level=level,
                             transport=lambda *a: self.fail('No transport call expected'), live=False)
        client = GeminiClient(api_key='SIMULATED', model='gemini-2.5-flash', thinking_level='high',
                              transport=lambda *a: self.fail('No transport call expected'), live=False)
        self.assertEqual(client.describe(b'').error['kind'], 'INVALID_THINKING_CONFIG')

    def test_env_level_applies_to_38_but_does_not_poison_replay(self):
        with patch.dict(os.environ, {'GEMINI_THINKING_LEVEL': 'high'}):
            self.assertEqual(GeminiClient(model='gemini-3.8-flash').thinking_level, 'high')
            self.assertEqual(GeminiClient(model='replay', live=False).thinking_level, '')

    def test_explicit_blank_model_does_not_silently_inherit_new_default(self):
        with patch.dict(os.environ, {'GEMINI_MODEL': 'gemini-3.8-flash'}):
            client = GeminiClient(api_key='SIMULATED', model='', live=False,
                                  transport=lambda *a: self.fail('Blank explicit model must not call provider'))
            self.assertEqual(client.describe(b'').error['kind'], 'MISSING_MODEL')


if __name__ == '__main__':
    unittest.main()
