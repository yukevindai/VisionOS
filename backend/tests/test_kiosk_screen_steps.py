"""Kiosk step evidence (Step 6): screenKind, isWelcomeScreen, orderTotal, screenSignature.

All replies here are SIMULATED dictionaries in Gemini's reply shape. No test calls Gemini.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from r3_vision import gemini, pipeline  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SEQUENCE_FIXTURE = REPO / "fixtures" / "SIMULATED_kiosk_screen_sequence.json"


def item(label: str, box: list[int], price: str | None = None) -> dict:
    element = {"label": label, "legibility": "clear", "type": "screenControl", "box_2d": box}
    if price:
        element["price"] = price
    return element


def reply(kind: str | None, elements: list[dict], *, payment: bool = False, total: str | None = None,
          availability: str = "available") -> dict:
    value = {"panelType": "orderingKiosk", "availability": availability, "isPaymentScreen": payment,
             "sections": [], "elements": elements}
    if kind is not None:
        value["screenKind"] = kind
    if total is not None:
        value["orderTotal"] = total
    if availability != "available":
        value["unavailableReason"] = "occluded"
    return value


def model_for(value: dict | None, frame: str = "SIMULATED-frame") -> dict:
    result = gemini.ProviderResult(live=False, ok=value is not None, latency_ms=1.0, model="SIMULATED", reply=value,
                                   error=None if value is not None else {"kind": "PROVIDER_TIMEOUT", "status": None,
                                                                          "message": "SIMULATED"})
    return pipeline.build_screen_model(result, None, 0, frame)


MENU = [item("Burgers", [100, 50, 300, 450]), item("Drinks", [100, 500, 300, 950]), item("Desserts", [350, 50, 550, 450])]

# The sequence R2's task engine sees during T07: welcome → … → payment, then a reset back to welcome.
SEQUENCE = [
    ("welcome", reply("welcome", [item("Tap to start", [400, 200, 600, 800])])),
    ("menu", reply("menu", MENU)),
    ("itemOptions", reply("itemOptions", [item("Small", [200, 50, 400, 300], "$1.99"),
                                          item("Medium", [200, 350, 400, 600], "$2.49"),
                                          item("Large", [200, 650, 400, 950], "$2.99"),
                                          item("Add to order", [800, 300, 950, 700])])),
    ("upsell", reply("upsell", [item("Add fries for $1.99", [300, 100, 500, 900]), item("No thanks", [600, 300, 750, 700])])),
    ("cart", reply("cart", [item("Edit", [700, 50, 850, 300]), item("Checkout", [700, 650, 850, 950])], total="$7.48")),
    ("payment", reply("payment", [item("Insert or tap card", [300, 100, 700, 900])], payment=True, total="$7.48")),
    ("reset", reply("welcome", [item("Tap to start", [400, 200, 600, 800])])),
]


def build_sequence() -> list[dict]:
    return [{"step": step, "screenModel": model_for(value, f"SIMULATED-seq-{i:02d}")}
            for i, (step, value) in enumerate(SEQUENCE)]


class ScreenStepTests(unittest.TestCase):
    def test_welcome_screen_is_flagged_for_kiosk_reset(self):
        model = model_for(reply("welcome", [item("Tap to start", [400, 200, 600, 800])]))
        self.assertEqual(model["screenKind"], "welcome")
        self.assertTrue(model["isWelcomeScreen"])
        self.assertFalse(model["isPaymentScreen"])

    def test_payment_kind_alone_still_stops_at_payment(self):
        model = model_for(reply("payment", [item("Insert card", [300, 100, 700, 900])], payment=False))
        self.assertTrue(model["isPaymentScreen"])
        self.assertEqual(model_for(reply("cart", MENU, payment=True))["isPaymentScreen"], True)

    def test_total_is_copied_not_computed(self):
        self.assertEqual(model_for(reply("cart", MENU, total="  $7.48 ")).get("orderTotal"), "$7.48")
        self.assertNotIn("orderTotal", model_for(reply("cart", MENU)))

    def test_unknown_or_missing_kind_never_claims_welcome(self):
        for kind in (None, "Welcome!", "tap here to reset", 3):
            value = reply(None, MENU)
            if kind is not None:
                value["screenKind"] = kind
            model = model_for(value)
            self.assertEqual(model["screenKind"], "unknown")
            self.assertFalse(model["isWelcomeScreen"])

    def test_signature_ignores_camera_and_prices_but_not_content(self):
        base = model_for(reply("menu", MENU))["screenSignature"]
        moved = [dict(e, box_2d=[v + 20 for v in e["box_2d"]], price="$9.99") for e in reversed(MENU)]
        self.assertEqual(model_for(reply("menu", moved), "SIMULATED-other-frame")["screenSignature"], base)
        self.assertNotEqual(model_for(reply("menu", MENU[:2]))["screenSignature"], base)
        self.assertNotEqual(model_for(reply("cart", MENU))["screenSignature"], base)

    def test_partial_or_failed_reads_get_no_identity(self):
        self.assertNotIn("screenSignature", model_for(reply("menu", MENU, availability="partial")))
        failed = model_for(None)
        self.assertEqual((failed["screenKind"], failed["isWelcomeScreen"]), ("unknown", False))
        self.assertNotIn("screenSignature", failed)

    def test_new_fields_are_optional_in_the_gemini_schema(self):
        self.assertNotIn("screenKind", gemini.RESPONSE_SCHEMA["required"])
        self.assertNotIn("orderTotal", gemini.RESPONSE_SCHEMA["required"])
        for kind in gemini.SCREEN_KINDS:
            self.assertIn(f'"{kind}"', gemini.PROMPT)


class SequenceFixtureTests(unittest.TestCase):
    def test_sequence_covers_the_t07_path(self):
        models = {s["step"]: s["screenModel"] for s in build_sequence()}
        self.assertTrue(models["welcome"]["isWelcomeScreen"] and models["reset"]["isWelcomeScreen"])
        self.assertEqual(models["welcome"]["screenSignature"], models["reset"]["screenSignature"])
        self.assertTrue(models["payment"]["isPaymentScreen"])
        self.assertEqual(models["cart"]["orderTotal"], "$7.48")
        self.assertEqual(len({m["screenSignature"] for k, m in models.items() if k != "reset"}), 6)

    def test_shared_fixture_matches_the_pipeline(self):
        stored = json.loads(SEQUENCE_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(stored["sequence"], build_sequence(),
                         "Regenerate with: python tests/test_kiosk_screen_steps.py --write-fixture")


if __name__ == "__main__":
    if "--write-fixture" in sys.argv:
        SEQUENCE_FIXTURE.write_text(json.dumps({
            "_note": "SIMULATED: kiosk ScreenModels built by r3_vision.pipeline from hand-written Gemini-shaped "
                     "replies. Not from a real kiosk or the Gemini API. For R2 task-engine tests (T07 path).",
            "sequence": build_sequence(),
        }, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {SEQUENCE_FIXTURE}")
    else:
        unittest.main()
