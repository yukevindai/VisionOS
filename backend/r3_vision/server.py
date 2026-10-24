"""Private vision service behind an HTTPS reverse proxy.

Only Convex calls this endpoint; the service token and Gemini key never reach clients.
POST /v1/read-screen {imageBase64, frameId, capturedAtMs, requestId}
"""
from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import gemini, pipeline
from .cli import BACKEND

MAX_IMAGE_BYTES = 512 * 1024
MAX_BODY_BYTES = 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 2048
MAX_RESPONSE_BYTES = 256 * 1024
MAX_SAFE_INTEGER = 9_007_199_254_740_991


def parse_request(payload: object) -> tuple[bytes, str, int, str]:
    if not isinstance(payload, dict) or set(payload) != {"imageBase64", "frameId", "capturedAtMs", "requestId"}:
        raise ValueError("invalid request fields")
    for key in ("frameId", "requestId"):
        value = payload[key]
        if not isinstance(value, str) or not 1 <= len(value) <= 128 or not value.strip() or not value.isprintable():
            raise ValueError("invalid correlation identifier")
    captured_at = payload["capturedAtMs"]
    if type(captured_at) is not int or not 0 <= captured_at <= MAX_SAFE_INTEGER:
        raise ValueError("invalid capture timestamp")
    encoded = payload["imageBase64"]
    if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
        raise ValueError("invalid image")
    image = base64.b64decode(encoded, validate=True)
    if not image or len(image) > MAX_IMAGE_BYTES or base64.b64encode(image).decode("ascii") != encoded:
        raise ValueError("invalid image")
    return image, payload["frameId"], captured_at, payload["requestId"]


class Handler(BaseHTTPRequestHandler):
    client: gemini.GeminiClient
    service_token: str

    def log_message(self, format: str, *args) -> None:
        # Never log requests, image data, query strings or credentials.
        pass

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, allow_nan=False).encode("utf-8")
        if len(body) > MAX_RESPONSE_BYTES:
            status, body = 502, b'{"error":"VISION_RESPONSE_TOO_LARGE"}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/read-screen":
            return self._reply(404, {"error": "not found"})
        expected = f"Bearer {self.service_token}".encode("utf-8")
        actual = (self.headers.get("Authorization") or "").encode("utf-8")
        if not self.service_token or not hmac.compare_digest(actual, expected):
            return self._reply(401, {"error": "unauthorized"})
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            return self._reply(415, {"error": "expected application/json"})
        raw_length = self.headers.get("Content-Length", "")
        if len(raw_length) > 10 or not raw_length.isascii() or not raw_length.isdigit():
            return self._reply(400, {"error": "invalid content length"})
        length = int(raw_length)
        if not 0 < length <= MAX_BODY_BYTES:
            return self._reply(413, {"error": "body too large or empty"})
        try:
            self.connection.settimeout(6)
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError("incomplete request")
            image, frame_id, captured_at, request_id = parse_request(json.loads(data))
        except (ValueError, UnicodeError, binascii.Error, TimeoutError):
            return self._reply(400, {"error": "invalid vision request"})
        result = pipeline.read_screen(image, self.client, frame_id, captured_at)
        self._reply(200, {"requestId": request_id, "screenModel": result["screenModel"]})


def main() -> None:
    gemini.load_dotenv(BACKEND / ".env")
    token = os.environ.get("R3_VISION_SERVICE_TOKEN", "")
    if len(token) < 32 or not token.isascii() or not token.isprintable() or token.strip() != token:
        raise SystemExit("Set R3_VISION_SERVICE_TOKEN to a server-only random token of at least 32 printable ASCII characters")
    Handler.service_token = token
    Handler.client = gemini.GeminiClient()
    port = int(os.environ.get("R3_PORT", "8787"))
    if not Handler.client.configured:
        print("warning: GEMINI_API_KEY or GEMINI_MODEL is not set; reads will report provider unavailable")
    print(f"Private R3 service on http://127.0.0.1:{port}/v1/read-screen; expose only through an HTTPS reverse proxy")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
