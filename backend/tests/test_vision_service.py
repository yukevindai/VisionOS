"""SIMULATED transport and localhost HTTP tests; never calls Gemini."""
from __future__ import annotations
import base64
import http.client
import io
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from PIL import Image
from r3_vision import gemini, server


def request_payload():
    image = io.BytesIO()
    Image.new("RGB", (8, 8)).save(image, format="JPEG")
    return {"imageBase64": base64.b64encode(image.getvalue()).decode(), "frameId": "native-1",
            "capturedAtMs": 1_800_000_000_000, "requestId": "request-1"}


class HTTPBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = []
        def transport(*args):
            cls.calls.append(args)
            return 200, json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps({
                "panelType": "controlPanel", "availability": "available", "isPaymentScreen": False,
                "sections": [], "elements": []})}]}}]}).encode()
        class TestHandler(server.Handler):
            service_token = "SIMULATED-server-token-00000000000"
            # live=True exercises the production wire shape using this fake transport.
            client = gemini.GeminiClient(api_key="SIMULATED", model="SIMULATED", transport=transport)
        cls.handler = TestHandler
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()

    def send(self, payload, path="/v1/read-screen", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=3)
        values = {"Authorization": f"Bearer {self.handler.service_token}", "Content-Type": "application/json"}
        values.update(headers or {})
        connection.request("POST", path, json.dumps(payload).encode(), values)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_authentication_precedes_provider_work(self):
        before = len(self.calls)
        for token in ("", "Bearer wrong"):
            self.assertEqual(self.send(request_payload(), headers={"Authorization": token})[0], 401)
        self.assertEqual(len(self.calls), before)

    def test_exact_correlation_without_duplicate_selection(self):
        request = request_payload()
        status, result = self.send(request)
        self.assertEqual(status, 200)
        self.assertEqual(set(result), {"requestId", "screenModel"})
        self.assertEqual(result["requestId"], request["requestId"])
        self.assertEqual(result["screenModel"]["frameId"], request["frameId"])
        self.assertEqual(result["screenModel"]["capturedAtMs"], request["capturedAtMs"])
        self.assertNotIn("SIMULATED-server-token", json.dumps(result))

    def test_legacy_unauthenticated_route_is_removed(self):
        self.assertEqual(self.send(request_payload(), path="/v1/select")[0], 404)

    def test_invalid_types_bounds_and_content_length_never_call_provider(self):
        before = len(self.calls)
        for change in ({"frameId": ""}, {"requestId": 123}, {"capturedAtMs": True}, {"capturedAtMs": -1},
                       {"imageBase64": "junk!"}, {"requestId": "x" * 129}, {"unexpected": True}):
            with self.subTest(change=change):
                self.assertEqual(self.send(dict(request_payload(), **change))[0], 400)
        self.assertEqual(self.send(request_payload(), headers={"Content-Length": "nope"})[0], 400)
        self.assertEqual(self.send(request_payload(), headers={"Content-Length": str(server.MAX_BODY_BYTES + 1)})[0], 413)
        self.assertEqual(self.send(request_payload(), headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(len(self.calls), before)

    def test_oversized_decoded_image_is_rejected(self):
        request = request_payload()
        request["imageBase64"] = base64.b64encode(b"x" * (server.MAX_IMAGE_BYTES + 1)).decode()
        with self.assertRaises(ValueError):
            server.parse_request(request)

    def test_missing_provider_config_returns_unavailable_model(self):
        with patch.object(self.handler, "client", gemini.GeminiClient(api_key="", model="configured")):
            status, result = self.send(request_payload())
        self.assertEqual(status, 200)
        self.assertEqual(result["screenModel"]["unavailableReason"], "providerUnavailable")


class MalformedProviderTests(unittest.TestCase):
    def test_malformed_envelopes_are_explicit_failures(self):
        for envelope in ([], None, {"candidates": "wrong"}, {"candidates": [None]},
                         {"candidates": [{"content": {"parts": [{"text": 42}]}}]},
                         {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]},
                         {"candidates": [{"content": []}]}, {"error": []}):
            with self.subTest(envelope=envelope):
                client = gemini.GeminiClient(api_key="SIMULATED", model="SIMULATED", live=False,
                    transport=lambda *args: (200, json.dumps(envelope).encode()))
                result = client.describe(b"SIMULATED")
                self.assertFalse(result.ok)
                self.assertIn(result.error["kind"], {"MALFORMED_RESPONSE", "NO_CANDIDATE"})

    def test_timeout_cannot_exceed_five_seconds(self):
        self.assertEqual(gemini.GeminiClient(timeout_s=100).timeout_s, 5)
        for timeout in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                gemini.GeminiClient(timeout_s=timeout)
