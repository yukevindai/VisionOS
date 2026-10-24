"""Offline tests. Every provider reply here is SIMULATED; no test calls the Gemini API.

    python -m unittest discover -s tests   (from backend/)
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from PIL import Image  # noqa: E402

from eval import evaluate  # noqa: E402
from r3_vision import gemini, image_prep, pipeline, selector  # noqa: E402

FIXTURES = BACKEND / "tests" / "fixtures"
SWIFT_FIXTURES = BACKEND.parent / "ios" / "Packages" / "Targets" / "Tests" / "TargetsTests" / "Fixtures"


def jpeg(width: int = 400, height: int = 200, orientation: int | None = None) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    buffer = io.BytesIO()
    exif = Image.Exif()
    if orientation:
        exif[image_prep.EXIF_ORIENTATION_TAG] = orientation
    image.save(buffer, format="JPEG", exif=exif.tobytes())
    return buffer.getvalue()


def replay_client(name: str) -> gemini.GeminiClient:
    envelope = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return gemini.GeminiClient(api_key="replay", model="replay", transport=gemini.replay_transport(envelope), live=False)


class SelectorConformanceTests(unittest.TestCase):
    """The Python port must reproduce the Swift package's example outputs."""

    def test_swift_example_files(self):
        files = sorted((SWIFT_FIXTURES / "examples").glob("*.json"))
        self.assertEqual(len(files), 4)
        for path in files:
            example = json.loads(path.read_text(encoding="utf-8"))
            model = json.loads((SWIFT_FIXTURES / f"{example['fixture']}.json").read_text(encoding="utf-8"))
            with self.subTest(path.name):
                self.assertEqual(selector.select(example["request"], model), example["expected"])


class ImagePrepTests(unittest.TestCase):
    def test_exif_rotation_is_applied_and_dimensions_are_upright(self):
        prepared = image_prep.prepare(jpeg(400, 200, orientation=6))  # 6 = rotate 90° clockwise to view
        self.assertEqual((prepared.width_px, prepared.height_px), (200, 400))
        self.assertEqual(prepared.exif_orientation, 6)
        self.assertEqual(Image.open(io.BytesIO(prepared.jpeg)).size, (200, 400))

    def test_large_image_is_downscaled_keeping_aspect(self):
        prepared = image_prep.prepare(jpeg(4000, 3000))
        self.assertEqual((prepared.width_px, prepared.height_px), (4000, 3000))
        self.assertEqual((prepared.sent_width_px, prepared.sent_height_px), (1536, 1152))

    def test_box_maps_back_to_the_original_upright_photo(self):
        # Upright scene 3000x2000 with a red block at normalized (0.1, 0.1, 0.2, 0.2), stored rotated with
        # EXIF orientation 6 (as phones save portrait shots) and downscaled before sending.
        from PIL import ImageChops, ImageDraw
        upright = Image.new("RGB", (3000, 2000), "white")
        ImageDraw.Draw(upright).rectangle([300, 200, 899, 599], fill="red")
        stored = upright.rotate(90, expand=True)  # exif_transpose of orientation 6 rotates it back
        exif = Image.Exif()
        exif[image_prep.EXIF_ORIENTATION_TAG] = 6
        buffer = io.BytesIO()
        stored.save(buffer, format="JPEG", exif=exif.tobytes(), quality=95)

        prepared = image_prep.prepare(buffer.getvalue())
        self.assertEqual((prepared.width_px, prepared.height_px), (3000, 2000))
        sent = Image.open(io.BytesIO(prepared.jpeg)).convert("RGB")
        self.assertEqual(sent.size, (1536, 1024))
        r, g, _ = sent.split()
        mask = ImageChops.multiply(r.point(lambda v: 255 if v > 200 else 0), g.point(lambda v: 255 if v < 80 else 0))
        left, top, right, bottom = mask.getbbox()
        # What a perfect detector would report on the sent image, in Gemini's 0-1000 box_2d convention:
        box_2d = [round(top / sent.height * 1000), round(left / sent.width * 1000),
                  round(bottom / sent.height * 1000), round(right / sent.width * 1000)]
        region = pipeline.box_2d_to_region(box_2d)
        for key, expected in (("x", 0.1), ("y", 0.1), ("width", 0.2), ("height", 0.2)):
            self.assertAlmostEqual(region[key], expected, delta=0.003)
        # ...and the same region lands on the block in the original-resolution upright photo.
        self.assertAlmostEqual(region["x"] * prepared.width_px, 300, delta=10)
        self.assertAlmostEqual(region["y"] * prepared.height_px, 200, delta=10)

    def test_unreadable_bytes(self):
        with self.assertRaises(ValueError):
            image_prep.prepare(b"not an image")


class ConversionTests(unittest.TestCase):
    def test_box_2d_conversion(self):
        self.assertEqual(pipeline.box_2d_to_region([500, 550, 580, 670]),
                         {"x": 0.55, "y": 0.5, "width": 0.12, "height": 0.08})
        self.assertIsNone(pipeline.box_2d_to_region([1, 2, 3]))
        self.assertIsNone(pipeline.box_2d_to_region("0,0,1,1"))
        self.assertIsNone(pipeline.box_2d_to_region([True, 0, 1, 1]))
        inverted = pipeline.box_2d_to_region([480, 670, 400, 550])  # kept as-is, so it is rejected
        self.assertFalse(selector.region_is_valid(inverted))


class SimulatedPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = pipeline.run(jpeg(), "Find floor 5", replay_client("SIMULATED_gemini_reply_elevator.json"))
        cls.model = cls.result["screenModel"]

    def test_replayed_reply_is_labelled_simulated(self):
        self.assertEqual(self.result["mode"], "SIMULATED")
        self.assertEqual(self.model["source"], "SIMULATED")
        self.assertTrue(self.model["frameId"].startswith("SIMULATED-"))
        self.assertFalse(self.model["evidence"]["live"])
        self.assertIn("SIMULATED", self.model["notes"])

    def test_find_floor_5(self):
        selection = self.result["selection"]
        self.assertEqual(selection["status"], "SELECTED")
        self.assertEqual(selection["target"]["label"], "5")
        self.assertEqual(selection["target"]["imageRegion"], {"x": 0.55, "y": 0.5, "width": 0.12, "height": 0.08})
        self.assertEqual(self.model["image"], {"widthPx": 400, "heightPx": 200, "exifOrientation": 1,
                                               "orientationApplied": True})

    def test_no_invented_confidence_labels_or_commands(self):
        for control in self.model["controls"]:
            self.assertNotIn("confidence", control)
            self.assertLessEqual(set(control), {"id", "label", "type", "legibility", "region", "price", "section"})
        unreadable = [c for c in self.model["controls"] if c["legibility"] == "unreadable"]
        self.assertEqual([c["label"] for c in unreadable], [""])
        text = json.dumps(self.result)
        for injected in ("vibrate", "press now", "command", "action"):
            self.assertNotIn(injected, text)

    def test_unclear_and_duplicate_targets(self):
        self.assertEqual(selector.select("Find floor 6", self.model)["reason"], "LOW_CONFIDENCE")  # partial label
        self.assertEqual(selector.select("Find floor 7", self.model)["reason"], "INVALID_REGION")  # inverted box
        self.assertEqual(selector.select("Find floor 8", self.model)["reason"], "NO_MATCH")  # unreadable, not guessed
        duplicate = selector.select("press open", self.model)
        self.assertEqual((duplicate["status"], duplicate["reason"]), ("AMBIGUOUS", "DUPLICATE_LABEL"))

    def test_button_outranks_its_printed_label(self):
        # A panel listing both the "5" plate (untyped) and the "5" button must point at the button.
        plate = {"id": "plate-5", "label": "5", "type": "other", "legibility": "clear",
                 "region": {"x": 0.25, "y": 0.48, "width": 0.08, "height": 0.06}}
        button = {"id": "button-5", "label": "5", "type": "button", "legibility": "clear",
                  "region": {"x": 0.37, "y": 0.47, "width": 0.09, "height": 0.07}}
        for controls in ([plate, button], [button, plate]):
            model = dict(self.model, controls=controls)
            selection = selector.select("Find floor 5", model)
            self.assertEqual(selection["target"]["targetId"], "button-5")
        # Two typed controls with the same label are still ambiguous.
        twin = dict(plate, type="button")
        self.assertEqual(selector.select("Find floor 5", dict(self.model, controls=[twin, button]))["status"],
                         "AMBIGUOUS")
        self.assertIn("braille", gemini.PROMPT)

    def test_frame_id_and_timestamp_are_preserved(self):
        result = pipeline.run(jpeg(), "Find floor 5", replay_client("SIMULATED_gemini_reply_elevator.json"),
                              frame_id="SIMULATED-phone-frame-42", captured_at_ms=1792800123456)
        for record in (result["screenModel"], result["selection"], result["selection"]["target"]):
            self.assertEqual(record["frameId"], "SIMULATED-phone-frame-42")
        self.assertEqual(result["screenModel"]["capturedAtMs"], 1792800123456)
        self.assertEqual(result["selection"]["target"]["capturedAtMs"], 1792800123456)
        # A replayed reply is always labelled, even when the caller's ID lacks the prefix.
        relabelled = pipeline.run(jpeg(), "Find floor 5", replay_client("SIMULATED_gemini_reply_elevator.json"),
                                  frame_id="phone-frame-43")
        self.assertEqual(relabelled["screenModel"]["frameId"], "SIMULATED-phone-frame-43")

    def test_output_contains_no_command_channel(self):
        self.assertEqual(set(self.result), {"mode", "screenModel", "selection", "provider", "timingMs"})
        self.assertLessEqual(set(self.result["selection"]),
                             {"status", "request", "frameId", "source", "candidateIds", "target", "reason", "errorCode"})

    def test_kiosk_partial_view(self):
        result = pipeline.run(jpeg(), "Find the Big Mac", replay_client("SIMULATED_gemini_reply_kiosk.json"))
        model = result["screenModel"]
        self.assertEqual(result["selection"]["target"]["label"], "Big Mac")
        self.assertEqual(model["controls"][0]["price"], "$5.99")
        self.assertEqual(model["unavailableReason"], "occluded")
        self.assertEqual(selector.select("Find the fries", model)["reason"], "MULTIPLE_MATCHES")
        self.assertEqual(selector.select("Find the milkshake", model)["reason"], "NO_MATCH_PARTIAL_VIEW")


class ProviderFailureTests(unittest.TestCase):
    def run_with(self, transport, api_key="test-key"):
        client = gemini.GeminiClient(api_key=api_key, model="test-model", transport=transport, live=False)
        return pipeline.run(jpeg(), "Find floor 5", client)

    def assert_unavailable(self, result, kind):
        """A provider failure must never look like a target that is absent from the image."""
        self.assertEqual(result["provider"]["error"]["kind"], kind)
        self.assertEqual(result["screenModel"]["availability"], "unavailable")
        self.assertEqual(result["screenModel"]["unavailableReason"], "providerUnavailable")
        self.assertEqual(result["selection"]["status"], "PROVIDER_UNAVAILABLE")
        self.assertEqual(result["selection"]["reason"], "PROVIDER_UNAVAILABLE")
        self.assertNotIn("target", result["selection"])

    def test_http_402_and_404_are_provider_unavailable_not_absent(self):
        for status, body in (
            (402, b'{"error":{"code":402,"status":"PAYMENT_REQUIRED","message":"billing required"}}'),
            (404, b'{"error":{"code":404,"status":"NOT_FOUND","message":"models/test-model is not found"}}'),
        ):
            calls = []
            def transport(url, headers, body_bytes, timeout, status=status, body=body):
                calls.append(url)
                return status, body
            result = self.run_with(transport)
            with self.subTest(status):
                self.assert_unavailable(result, "HTTP_ERROR")
                self.assertEqual(result["provider"]["httpStatus"], status)
                # Exactly one call, to the configured model: no retry or silent switch to another model.
                self.assertEqual(len(calls), 1)
                self.assertIn("/models/test-model:generateContent", calls[0])

    def test_missing_model_makes_no_call(self):
        calls = []
        client = gemini.GeminiClient(api_key="k", model="", transport=lambda *a: calls.append(a) or (200, b"{}"),
                                     live=False)
        self.assertFalse(client.configured)
        self.assert_unavailable(pipeline.run(jpeg(), "Find floor 5", client), "MISSING_MODEL")
        self.assertEqual(calls, [])

    def test_non_google_format_key_is_redacted_from_errors(self):
        secret = "teammate-secret-value-123"
        client = gemini.GeminiClient(api_key=secret, model="test-model", live=False,
                                     transport=lambda *a: (_ for _ in ()).throw(TimeoutError(f"echo {secret}")))
        self.assertNotIn(secret, json.dumps(pipeline.run(jpeg(), "Find floor 5", client)))
        client = gemini.GeminiClient(api_key=secret, model="test-model", live=False,
                                     transport=lambda *a: (400, json.dumps({"error": {"message": secret}}).encode()))
        self.assertNotIn(secret, json.dumps(pipeline.run(jpeg(), "Find floor 5", client)))

    def test_missing_key_makes_no_call(self):
        calls = []
        result = self.run_with(lambda *a: calls.append(a) or (200, b"{}"), api_key="")
        self.assertEqual(calls, [])
        self.assert_unavailable(result, "MISSING_API_KEY")

    def test_key_goes_in_header_not_url_or_body(self):
        seen = {}
        def transport(url, headers, body, timeout):
            seen.update(url=url, headers=headers, body=body, timeout=timeout)
            return 500, b'{"error":{"code":500,"status":"INTERNAL","message":"boom"}}'
        result = self.run_with(transport, api_key="AIzaSyTESTKEYTESTKEYTESTKEYTESTKEY123")
        self.assertEqual(seen["headers"]["x-goog-api-key"], "AIzaSyTESTKEYTESTKEYTESTKEYTESTKEY123")
        self.assertNotIn(b"AIza", seen["body"])
        self.assertNotIn("AIza", seen["url"])
        self.assertEqual(seen["timeout"], 5.0)
        self.assertNotIn("AIza", json.dumps(result))
        self.assertEqual(result["provider"]["httpStatus"], 500)

    def test_http_error_is_reported_verbatim(self):
        body = b'{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","message":"Quota exceeded"}}'
        result = self.run_with(lambda *a: (429, body))
        self.assert_unavailable(result, "HTTP_ERROR")
        self.assertEqual(result["provider"]["error"]["message"], "RESOURCE_EXHAUSTED: Quota exceeded")

    def test_timeout(self):
        def transport(*a):
            raise TimeoutError("timed out")
        result = self.run_with(transport)
        self.assert_unavailable(result, "PROVIDER_TIMEOUT")
        self.assertEqual(result["provider"]["error"]["errorCode"], "PROVIDER_TIMEOUT")
        self.assertEqual(result["selection"]["errorCode"], "PROVIDER_TIMEOUT")

    def test_blocked_and_malformed_replies(self):
        blocked = json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}).encode()
        self.assert_unavailable(self.run_with(lambda *a: (200, blocked)), "NO_CANDIDATE")
        prose = json.dumps({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "sorry"}]}}]})
        self.assert_unavailable(self.run_with(lambda *a: (200, prose.encode())), "MALFORMED_RESPONSE")
        self.assert_unavailable(self.run_with(lambda *a: (502, b"<html>bad gateway</html>")), "MALFORMED_RESPONSE")

    def test_redaction(self):
        self.assertEqual(gemini.redact("key AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ012 leaked"), "key <redacted-key> leaked")

    def test_invalid_image(self):
        client = gemini.GeminiClient(api_key="k", model="test-model", transport=lambda *a: self.fail("no call"), live=False)
        bad = pipeline.run(b"junk", "Find floor 5", client)
        self.assertEqual(bad["provider"]["error"]["kind"], "INVALID_IMAGE")
        self.assertEqual(bad["screenModel"]["unavailableReason"], "unreadable")
        self.assertEqual((bad["selection"]["status"], bad["selection"]["reason"]), ("NOT_FOUND", "SCREEN_UNAVAILABLE"))


class EvaluateTests(unittest.TestCase):
    def test_metrics_helpers(self):
        box = {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}
        self.assertAlmostEqual(evaluate.iou(box, box), 1.0)
        self.assertAlmostEqual(evaluate.iou(box, {"x": 0.2, "y": 0.1, "width": 0.2, "height": 0.2}), 1 / 3)
        self.assertEqual(evaluate.percentile([5, 1, 3, 2, 4], 50), 3)
        self.assertEqual(evaluate.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95), 10)

    def test_simulated_run_writes_simulated_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "photos").mkdir()
            (tmp / "photos" / "SIMULATED_gemini_reply_elevator.jpg").write_bytes(jpeg())
            manifest = {"photos": [{"image": "photos/SIMULATED_gemini_reply_elevator.jpg", "interface": "elevator",
                                    "requests": [
                {"request": "Find floor 5", "expected": {"status": "SELECTED", "label": "5",
                                                         "box": {"x": 0.55, "y": 0.5, "width": 0.12, "height": 0.08}}},
                {"request": "Find floor 42", "expected": {"status": "NOT_FOUND"}},
                {"request": "press open", "expected": {"status": "AMBIGUOUS"}}]}]}
            (tmp / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            out = tmp / "results"
            self.assertEqual(evaluate.main([str(tmp / "manifest.json"), "--replay-dir", str(FIXTURES),
                                            "--out-dir", str(out)]), 0)
            report = json.loads(next(out.glob("*-SIMULATED.json")).read_text(encoding="utf-8"))
            summary = report["summary"]
            self.assertEqual(summary["mode"], "SIMULATED")
            self.assertEqual(summary["recognitionAccuracy"], 1.0)
            self.assertEqual(summary["boxIoU"]["atLeast0.5"], 1)
            self.assertEqual(summary["missingAndAmbiguous"]["safe (NOT_FOUND or AMBIGUOUS)"], 2)
            self.assertTrue(list(out.glob("*-SIMULATED/*-overlay.jpg")))


if __name__ == "__main__":
    unittest.main()
