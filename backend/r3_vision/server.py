"""Local HTTP endpoint so the phone never holds the Gemini key.

    python -m r3_vision.server            # listens on 127.0.0.1:8787

POST /v1/select  {"imageBase64": "...", "request": "Find floor 5", "frameId"?: "...", "capturedAtMs"?: 0}
                 ->  pipeline result JSON (frameId/capturedAtMs are passed through unchanged when given)
Binds to loopback only and has no authentication yet; do not expose it beyond this machine.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import gemini, pipeline
from .cli import BACKEND

MAX_BODY_BYTES = 15 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    client: gemini.GeminiClient

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        if self.path != "/v1/select":
            return self._reply(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY_BYTES:
            return self._reply(413 if length else 400, {"error": "body must be 1 byte to 15 MB"})
        try:
            payload = json.loads(self.rfile.read(length))
            image = base64.b64decode(payload["imageBase64"], validate=True)
            request = str(payload["request"])
            frame_id = payload.get("frameId")
            captured_at = payload.get("capturedAtMs")
            if frame_id is not None and (not isinstance(frame_id, str) or not frame_id.strip()):
                raise ValueError("frameId")
            if captured_at is not None and (type(captured_at) is not int or captured_at < 0):
                raise ValueError("capturedAtMs")
        except (ValueError, KeyError, TypeError, binascii.Error):
            return self._reply(400, {"error": "expected JSON {imageBase64, request, frameId?, capturedAtMs?}"})
        self._reply(200, pipeline.run(image, request, self.client, frame_id=frame_id, captured_at_ms=captured_at))


def main() -> None:
    gemini.load_dotenv(BACKEND / ".env")
    Handler.client = gemini.GeminiClient()
    port = int(os.environ.get("R3_PORT", "8787"))
    if not Handler.client.configured:
        print("warning: GEMINI_API_KEY or GEMINI_MODEL is not set; every request will return PROVIDER_UNAVAILABLE")
    print(f"R3 backend on http://127.0.0.1:{port}/v1/select (model {Handler.client.model})")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
