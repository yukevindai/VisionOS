"""photo + request -> Gemini -> ScreenModel (Step 1 schema) -> TargetSelection.

Only whitelisted, type-checked fields from Gemini's reply reach the ScreenModel. Confidence is omitted
because Gemini reports none; labels are never filled in. Provider failures become an `unavailable`
ScreenModel, which the selector reports as NOT_FOUND / SCREEN_UNAVAILABLE.
"""
from __future__ import annotations

import hashlib
import time
import math
import uuid

from . import gemini, image_prep, selector

MAX_ELEMENTS = 200
MAX_TEXT = 120


def _now_ms() -> int:
    return int(time.time() * 1000)


def _text(value) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join("".join(ch for ch in value if ch.isprintable()).split())
    return cleaned[:MAX_TEXT] or None


def _enum(value, allowed: list[str], fallback: str) -> str:
    return value if isinstance(value, str) and value in allowed else fallback


def box_2d_to_region(box) -> dict | None:
    """Gemini [ymin, xmin, ymax, xmax] on 0-1000 -> normalized top-left x/y/width/height.

    Out-of-range or inverted boxes are converted as-is (not clamped or reordered) so the selector
    rejects them as INVALID_REGION instead of guiding toward a repaired guess.
    """
    if not isinstance(box, list) or len(box) != 4 or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in box):
        return None
    ymin, xmin, ymax, xmax = (v / 1000 for v in box)
    return {"x": round(xmin, 4), "y": round(ymin, 4), "width": round(xmax - xmin, 4), "height": round(ymax - ymin, 4)}


def _element(index: int, raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    legibility = _enum(raw.get("legibility"), gemini.LEGIBILITY, "partial")
    label = "" if legibility == "unreadable" else (_text(raw.get("label")) or "")
    element = {"id": f"el-{index:03d}", "label": label,
               "type": _enum(raw.get("type"), gemini.ELEMENT_TYPES, "other"),
               "legibility": legibility if label else "unreadable"}
    region = box_2d_to_region(raw.get("box_2d"))
    if region is not None:
        element["region"] = region
    for key in ("price", "section"):
        if (value := _text(raw.get(key))) is not None:
            element[key] = value
    return element


def build_screen_model(result: gemini.ProviderResult, image: image_prep.PreparedImage | None,
                       captured_at_ms: int, frame_id: str | None = None) -> dict:
    source = "visionModel" if result.live else "SIMULATED"
    frame_id = frame_id or f"frame-{uuid.uuid4().hex[:12]}"
    if not result.live and not frame_id.startswith("SIMULATED"):
        frame_id = f"SIMULATED-{frame_id}"
    model = {
        "schemaVersion": 3, "frameId": frame_id,
        # The caller's capture time when supplied; otherwise when the backend received the photo.
        "capturedAtMs": captured_at_ms,
        "coordinateSystem": selector.SUPPORTED_COORDINATES, "source": source,
        "evidence": {"provider": "gemini", "model": result.model, "live": result.live,
                     "latencyMs": round(result.latency_ms, 1)},
    }
    if image is not None:
        model["image"] = image.info()
    for key, value in (("modelVersion", result.model_version), ("responseId", result.response_id)):
        if value:
            model["evidence"][key] = value
    if not result.live:
        model["notes"] = "SIMULATED: reply did not come from the live Gemini API."

    reply = result.reply if result.ok else None
    if reply is None:
        # A bad upload is an unreadable image; every other failure means the provider gave no answer.
        invalid_image = (result.error or {}).get("kind") == "INVALID_IMAGE"
        model.update(panelType="unknown", availability="unavailable",
                     unavailableReason="unreadable" if invalid_image else "providerUnavailable",
                     controls=[], sections=[], isPaymentScreen=False, screenKind="unknown", isWelcomeScreen=False)
        return model
    elements = reply.get("elements") if isinstance(reply.get("elements"), list) else []
    controls = [e for i, raw in enumerate(elements[:MAX_ELEMENTS]) if (e := _element(i + 1, raw))]
    availability = _enum(reply.get("availability"), gemini.AVAILABILITY, "unavailable")
    model.update(
        panelType=_enum(reply.get("panelType"), gemini.PANEL_TYPES, "unknown"),
        availability=availability, controls=controls,
        sections=[s for s in map(_text, reply.get("sections") or []) if s][:50]
        if isinstance(reply.get("sections"), list) else [],
        isPaymentScreen=reply.get("isPaymentScreen") is True,
    )
    if availability != "available":
        model["unavailableReason"] = _enum(reply.get("unavailableReason"), gemini.UNAVAILABLE_REASONS, "unknown")
    _add_screen_step(model, reply)
    return model


def screen_signature(panel_type: str, screen_kind: str, controls: list[dict]) -> str:
    """Stable id for "the same screen" across frames: kind plus the sorted set of clearly read labels.

    Camera position, prices and box coordinates do not change it, so R2 can tell a re-read of the same
    screen from a real screen change without comparing frame ids (which differ on every frame).
    """
    labels = sorted({c["label"].casefold() for c in controls if c.get("legibility") == "clear" and c.get("label")})
    digest = hashlib.sha256("\n".join([panel_type, screen_kind, *labels]).encode("utf-8")).hexdigest()
    return f"scr-{digest[:16]}"


def _add_screen_step(model: dict, reply: dict) -> None:
    """Kiosk step evidence for R2's task engine: welcome (reset), payment boundary, total, screen identity."""
    kind = _enum(reply.get("screenKind"), gemini.SCREEN_KINDS, "unknown")
    model["screenKind"] = kind
    model["isWelcomeScreen"] = kind == "welcome"
    # Either signal is enough to stop: a missed payment screen is worse than an early handoff (spec S7).
    model["isPaymentScreen"] = model["isPaymentScreen"] or kind == "payment"
    if (total := _text(reply.get("orderTotal"))) is not None:
        model["orderTotal"] = total
    # Only a fully visible screen gets an identity; a partial view would look like a different screen.
    if model["availability"] == "available" and model["controls"]:
        model["screenSignature"] = screen_signature(model["panelType"], kind, model["controls"])


def read_screen(image_bytes: bytes, client: gemini.GeminiClient, frame_id: str | None = None,
                captured_at_ms: int | None = None) -> dict:
    """Read semantic evidence only; native callers select with the Swift reference implementation."""
    started = time.perf_counter()
    received_at = captured_at_ms if captured_at_ms is not None else _now_ms()
    try:
        image = image_prep.prepare(image_bytes)
    except ValueError as error:
        image = None
        provider = gemini.ProviderResult(client.live, False, 0.0, client.model,
                                         error={"kind": "INVALID_IMAGE", "status": None, "message": str(error)})
    prepared = time.perf_counter()
    if image is not None:
        provider = client.describe(image.jpeg)
    described = time.perf_counter()
    screen_model = build_screen_model(provider, image, received_at, frame_id)
    error = dict(provider.error) if provider.error else None
    if error and error["kind"] == "PROVIDER_TIMEOUT":
        error["errorCode"] = "PROVIDER_TIMEOUT"
    return {
        "mode": "REAL_API" if provider.live else "SIMULATED",
        "screenModel": screen_model,
        "provider": {"ok": provider.ok, "httpStatus": provider.http_status, "error": error,
                     "usage": provider.usage},
        "timingMs": {"prepare": round((prepared - started) * 1000, 1),
                     "provider": round((described - prepared) * 1000, 1),
                     "total": round((described - started) * 1000, 1)},
    }


def run(image_bytes: bytes, request: str, client: gemini.GeminiClient, frame_id: str | None = None,
        captured_at_ms: int | None = None) -> dict:
    """CLI/evaluation path, including the portable Python selector."""
    result = read_screen(image_bytes, client, frame_id, captured_at_ms)
    started = time.perf_counter()
    selection = selector.select(request, result["screenModel"])
    elapsed = (time.perf_counter() - started) * 1000
    error = result["provider"]["error"]
    if error and error["kind"] == "PROVIDER_TIMEOUT":
        selection["errorCode"] = "PROVIDER_TIMEOUT"
    result["selection"] = selection
    result["timingMs"]["select"] = round(elapsed, 1)
    result["timingMs"]["total"] = round(result["timingMs"]["total"] + elapsed, 1)
    return result
