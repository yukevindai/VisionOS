"""SIMULATED provider replies only; never reads a real key or calls a provider."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from r3_vision.gemini import GeminiClient, replay_transport


class CredentialRedactionTests(unittest.TestCase):
    key = "synthetic-new-key-format_0123456789-not-a-real-key"

    def run_client(self, transport):
        captured = io.StringIO()
        with redirect_stdout(captured), redirect_stderr(captured):
            result = GeminiClient(api_key=self.key, model="test-model", transport=transport, live=False).describe(b"image")
        serialized = json.dumps(asdict(result))
        self.assertNotIn(self.key, serialized)
        self.assertNotIn(self.key, captured.getvalue())
        return result, serialized

    def test_http_error_replaces_exact_configured_key(self):
        result, serialized = self.run_client(replay_transport({
            "error": {"status": "INVALID_ARGUMENT", "message": f"Rejected credential {self.key}"}
        }, status=400))
        self.assertFalse(result.ok)
        self.assertEqual(result.error["message"], "INVALID_ARGUMENT: Rejected credential <redacted-key>")

    def test_no_candidate_feedback_and_metadata_are_redacted(self):
        result, serialized = self.run_client(replay_transport({
            "promptFeedback": {"blockReason": f"blocked: {self.key}"},
            "responseId": self.key, "modelVersion": f"model/{self.key}",
            "usageMetadata": {self.key: [f"echo={self.key}"]}, "candidates": []
        }))
        self.assertEqual(result.error["kind"], "NO_CANDIDATE")
        self.assertEqual(result.error["message"], "blocked: <redacted-key>")
        self.assertEqual(result.response_id, "<redacted-key>")
        self.assertEqual(result.usage, {"<redacted-key>": ["echo=<redacted-key>"]})

    def test_malformed_body_redacts_before_diagnostic_truncation(self):
        # The credential crosses the old 300-byte cutoff; no partial credential may escape.
        body = ("x" * 290 + self.key + " bad json").encode()
        result, _ = self.run_client(lambda *_: (502, body))
        self.assertEqual(result.error["kind"], "MALFORMED_RESPONSE")
        self.assertNotIn(self.key[:10], result.error["message"])
        self.assertEqual(result.error["message"], ("x" * 290 + "<redacted-key> bad json")[:300])

    def test_network_error_replaces_exact_key(self):
        def failed(*_):
            raise urllib.error.URLError(f"upstream rejected {self.key}")
        result, _ = self.run_client(failed)
        self.assertEqual(result.error["message"], "upstream rejected <redacted-key>")

    def test_successful_reply_cannot_echo_key_through_nested_fields(self):
        description = {"panelType": "controlPanel", "availability": "available",
                       "isPaymentScreen": False, "sections": [self.key],
                       "elements": [{"label": self.key}]}
        result, _ = self.run_client(replay_transport({
            "candidates": [{"content": {"parts": [{"text": json.dumps(description)}]}}]
        }))
        self.assertTrue(result.ok)
        self.assertEqual(result.reply["sections"], ["<redacted-key>"])
        self.assertEqual(result.reply["elements"][0]["label"], "<redacted-key>")


class ThinkingConfigurationTests(unittest.TestCase):
    def test_supported_levels_serialize_without_changing_model_or_timeout(self):
        for level in ("low", "medium", "high"):
            calls = []
            def transport(url, headers, body, timeout):
                calls.append((url, json.loads(body), timeout))
                return 200, b'{"candidates":[]}'
            with patch.dict("os.environ", {"GEMINI_THINKING_LEVEL": level}):
                GeminiClient(api_key="synthetic-key", model="gemini-3.8-flash", timeout_s=30,
                             transport=transport, live=False).describe(b"image")
            self.assertEqual(len(calls), 1)
            self.assertIn("/models/gemini-3.8-flash:generateContent", calls[0][0])
            self.assertEqual(calls[0][1]["generationConfig"]["thinkingConfig"], {"thinkingLevel": level})
            self.assertEqual(calls[0][2], 5.0)

    def test_unset_level_omits_thinking_config(self):
        calls = []
        def transport(url, headers, body, timeout):
            calls.append(json.loads(body))
            return 200, b'{"candidates":[]}'
        with patch.dict("os.environ", {"GEMINI_THINKING_LEVEL": ""}):
            GeminiClient(api_key="synthetic-key", model="selected-model", transport=transport,
                         live=False).describe(b"image")
        self.assertNotIn("thinkingConfig", calls[0]["generationConfig"])

    def test_unsupported_level_fails_before_transport(self):
        for value in ("minimal", "auto", "LOW"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "must be low, medium or high"):
                GeminiClient(api_key="synthetic-key", model="selected-model", thinking_level=value,
                             transport=lambda *_: self.fail("invalid configuration made a call"), live=False)

    def test_explicit_empty_model_never_falls_back_to_environment(self):
        with patch.dict("os.environ", {"GEMINI_MODEL": "different-model", "GEMINI_THINKING_LEVEL": ""}):
            client = GeminiClient(api_key="synthetic-key", model="", live=False,
                                  transport=lambda *_: self.fail("missing explicit model made a call"))
            result = client.describe(b"image")
        self.assertFalse(client.configured)
        self.assertEqual(result.error["kind"], "MISSING_MODEL")


if __name__ == "__main__":
    unittest.main()
