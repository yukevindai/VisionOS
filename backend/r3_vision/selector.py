"""Python port of ios/Packages/Targets TargetSelector.swift.

The Swift selector is the reference. This port exists so the backend can evaluate photos on machines
without Swift. tests/test_selector_conformance.py runs the Swift package's example files against it;
change both together.
"""
from __future__ import annotations

import math
import re
import unicodedata

FILLER_WORDS = frozenset(
    "a an the please can could you i me my want would like need to for find locate where is show help "
    "press push tap touch hit select choose pick get order go take button item option key control".split()
)
NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
    "nineteen": "19", "twenty": "20", "first": "1", "second": "2", "third": "3", "fourth": "4",
    "fifth": "5", "sixth": "6", "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10",
}
MATCH_RANK = {"exact": 0, "labelInRequest": 1, "requestInLabel": 2}
SUPPORTED_COORDINATES = "normalizedImageTopLeft"


def _normalize_number(word: str) -> str:
    if word in NUMBER_WORDS:
        return NUMBER_WORDS[word]
    for suffix in ("st", "nd", "rd", "th"):
        if word.endswith(suffix) and word[:-2].isdigit():
            return word[:-2]
    return word


def tokens(text: str) -> list[str]:
    folded = "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)).lower()
    return [_normalize_number(w) for w in re.split(r"[^\w]+|_", folded) if w]


def content_tokens(text: str) -> list[str]:
    return [w for w in tokens(text) if w not in FILLER_WORDS]


def match(wanted: list[str], label: str) -> str | None:
    words = tokens(label)
    if not words:
        return None
    content = [w for w in words if w not in FILLER_WORDS]
    label_set, request_set = set(content or words), set(wanted)
    if label_set == request_set:
        return "exact"
    numbers = {w for w in request_set if w.isdigit()}
    if label_set <= request_set and numbers <= label_set:
        return "labelInRequest"
    if request_set <= label_set:
        return "requestInLabel"
    return None


def region_is_valid(region: dict | None) -> bool:
    if not isinstance(region, dict):
        return False
    try:
        x, y, w, h = (float(region[k]) for k in ("x", "y", "width", "height"))
    except (KeyError, TypeError, ValueError):
        return False
    return all(map(math.isfinite, (x, y, w, h))) and x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= 1 and y + h <= 1


def select(request: str, model: dict, minimum_confidence: float = 0.6) -> dict:
    base = {"request": request, "frameId": model.get("frameId", ""), "source": model.get("source", "unknown"),
            "candidateIds": []}

    def not_found(reason: str, candidates: list[str] | None = None) -> dict:
        result = dict(base, status="NOT_FOUND", reason=reason, candidateIds=candidates or [])
        if reason == "INVALID_REGION":
            result["errorCode"] = "LOW_CONFIDENCE"
        return result

    def ambiguous(reason: str, candidates: list[str]) -> dict:
        return dict(base, status="AMBIGUOUS", reason=reason, candidateIds=candidates,
                    errorCode="LOW_CONFIDENCE" if reason == "LOW_CONFIDENCE" else "LABEL_AMBIGUOUS")

    wanted = content_tokens(request)
    if not wanted:
        return not_found("EMPTY_REQUEST")
    controls = model.get("controls", [])
    ids = [c.get("id", "") for c in controls]
    if not model.get("frameId") or "" in ids or len(set(ids)) != len(ids):
        return not_found("MALFORMED_MODEL")
    if model.get("availability") not in ("available", "partial"):
        if model.get("unavailableReason") == "providerUnavailable":
            # Nothing was read, so nothing can be said about the target; never report it as absent.
            return dict(base, status="PROVIDER_UNAVAILABLE", reason="PROVIDER_UNAVAILABLE")
        return not_found("SCREEN_UNAVAILABLE")
    if model.get("coordinateSystem") != SUPPORTED_COORDINATES:
        return not_found("UNSUPPORTED_COORDINATES")

    matches = [(c, kind) for c in controls if (kind := match(wanted, c.get("label", "")))]
    if not matches:
        return not_found("NO_MATCH_PARTIAL_VIEW" if model.get("availability") == "partial" else "NO_MATCH")
    best = min((kind for _, kind in matches), key=MATCH_RANK.__getitem__)
    top = [c for c, kind in matches if kind == best]
    # A typed control outranks an untyped element with the same text (e.g. a button and its printed plate).
    typed = [c for c in top if c.get("type", "other") != "other"]
    top = typed or top
    candidates = [c["id"] for c in top]
    if len(top) > 1:
        same = len({tuple(tokens(c.get("label", ""))) for c in top}) == 1
        return ambiguous("DUPLICATE_LABEL" if same else "MULTIPLE_MATCHES", candidates)
    element = top[0]
    if not region_is_valid(element.get("region")):
        return not_found("INVALID_REGION", candidates)
    confidence = element.get("confidence")
    if confidence is not None and not confidence >= minimum_confidence:
        return ambiguous("LOW_CONFIDENCE", candidates)
    if element.get("legibility") not in (None, "clear"):
        return ambiguous("LOW_CONFIDENCE", candidates)

    target = {
        "targetId": element["id"], "label": element["label"], "elementType": element.get("type", "other"),
        "imageRegion": element["region"], "coordinateSystem": model["coordinateSystem"],
        "frameId": model["frameId"], "capturedAtMs": model.get("capturedAtMs"),
        "panelType": model.get("panelType", "unknown"), "source": model.get("source", "unknown"), "match": best,
    }
    if confidence is not None:
        target["confidence"] = confidence
    return dict(base, status="SELECTED", target=target)
