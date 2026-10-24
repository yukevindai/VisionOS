"""Evaluate the photo pipeline against manually reviewed ground truth.

    python eval/evaluate.py eval/manifest.json                 # REAL_API: live Gemini, needs GEMINI_API_KEY
    python eval/evaluate.py eval/manifest.json --replay-dir D  # SIMULATED: D/<image stem>.json replies

Each photo is sent to the provider once; every request listed for it is then run through the selector.
Writes eval/results/<timestamp>-<mode>.json plus overlay images for manual box review.
Ground-truth boxes are normalized x/y/width/height in the upright (EXIF-corrected) photo.
"""
from __future__ import annotations

import argparse
import math
import json
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from r3_vision import gemini, pipeline, selector  # noqa: E402

IOU_PASS = 0.5


def iou(a: dict, b: dict) -> float:
    ax2, ay2, bx2, by2 = a["x"] + a["width"], a["y"] + a["height"], b["x"] + b["width"], b["y"] + b["height"]
    w, h = max(0.0, min(ax2, bx2) - max(a["x"], b["x"])), max(0.0, min(ay2, by2) - max(a["y"], b["y"]))
    union = a["width"] * a["height"] + b["width"] * b["height"] - w * h
    return w * h / union if union > 0 else 0.0


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]  # nearest rank


def score_case(expected: dict, selection: dict) -> dict:
    status_ok = selection["status"] == expected["status"]
    case = {"expectedStatus": expected["status"], "actualStatus": selection["status"],
            "reason": selection.get("reason"), "statusCorrect": status_ok,
            "providerUnavailable": selection["status"] == "PROVIDER_UNAVAILABLE"}
    target = selection.get("target")
    if expected["status"] == "SELECTED":
        label_ok = bool(target) and selector.tokens(target["label"]) == selector.tokens(expected.get("label", ""))
        case.update(labelCorrect=label_ok, actualLabel=target["label"] if target else None)
        if target and expected.get("box"):
            case["iou"] = round(iou(target["imageRegion"], expected["box"]), 3)
    else:
        # A wrong SELECTED on a missing/ambiguous target is the unsafe failure.
        # A provider failure says nothing about the image, so it is neither safe nor correct here.
        case["safe"] = selection["status"] in ("NOT_FOUND", "AMBIGUOUS")
    case["correct"] = status_ok and case.get("labelCorrect", True)
    return case


def draw_overlay(image_path: Path, screen_model: dict, truths: list[dict], out: Path) -> None:
    from PIL import Image, ImageDraw, ImageOps
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    draw, (w, h) = ImageDraw.Draw(image), image.size

    def rect(r: dict, colour: str, width: int) -> None:
        draw.rectangle([r["x"] * w, r["y"] * h, (r["x"] + r["width"]) * w, (r["y"] + r["height"]) * h],
                       outline=colour, width=width)
    for c in screen_model["controls"]:
        if selector.region_is_valid(c.get("region")):
            rect(c["region"], "red", 3)
            draw.text((c["region"]["x"] * w + 4, c["region"]["y"] * h + 2), f'{c["id"]} {c["label"]}', fill="red")
    for t in truths:
        rect(t, "lime", 3)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, quality=85)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--replay-dir", type=Path, help="SIMULATED replies instead of live Gemini")
    parser.add_argument("--no-overlays", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=BACKEND / "eval" / "results")
    args = parser.parse_args(argv)
    gemini.load_dotenv(BACKEND / ".env")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    live = args.replay_dir is None
    if live and not gemini.GeminiClient().configured:
        print("GEMINI_API_KEY and GEMINI_MODEL must both be set. No REAL_API evaluation was run.", file=sys.stderr)
        return 3
    mode = "REAL_API" if live else "SIMULATED"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    results_dir = args.out_dir

    photos, cases, errors, provider_ms, total_ms = [], [], [], [], []
    for photo in manifest["photos"]:
        path = (args.manifest.parent / photo["image"]).resolve()
        if live:
            client = gemini.GeminiClient()
        else:
            envelope = json.loads((args.replay_dir / f"{path.stem}.json").read_text(encoding="utf-8"))
            client = gemini.GeminiClient(api_key="replay", model="replay", transport=gemini.replay_transport(envelope), live=False)
        requests = photo["requests"]
        first = pipeline.run(path.read_bytes(), requests[0]["request"] if requests else "", client)
        model = first["screenModel"]
        provider_ms.append(first["timingMs"]["provider"])
        total_ms.append(first["timingMs"]["total"])
        if first["provider"]["error"]:
            errors.append({"image": photo["image"], **first["provider"]["error"],
                           "httpStatus": first["provider"]["httpStatus"]})
        for item in requests:
            selection = selector.select(item["request"], model)
            cases.append({"image": photo["image"], "interface": photo.get("interface"),
                          "request": item["request"], **score_case(item["expected"], selection)})
        photos.append({"image": photo["image"], "interface": photo.get("interface"),
                       "elementCount": len(model["controls"]), "availability": model["availability"],
                       "timingMs": first["timingMs"], "screenModel": model})
        if not args.no_overlays and model["controls"]:
            truths = [r["expected"]["box"] for r in requests if r["expected"].get("box")]
            draw_overlay(path, model, truths, results_dir / f"{stamp}-{mode}" / f"{path.stem}-overlay.jpg")

    selected = [c for c in cases if c["expectedStatus"] == "SELECTED"]
    with_iou = [c["iou"] for c in selected if "iou" in c]
    negatives = [c for c in cases if c["expectedStatus"] != "SELECTED"]
    summary = {
        "mode": mode, "model": gemini.GeminiClient().model if live else "replay",
        "photos": len(photos), "cases": len(cases),
        "providerUnavailableCases": sum(c["providerUnavailable"] for c in cases),
        "recognitionAccuracy": round(sum(c["correct"] for c in cases) / len(cases), 3) if cases else None,
        "boxIoU": {"cases": len(with_iou), "mean": round(statistics.mean(with_iou), 3) if with_iou else None,
                   f"atLeast{IOU_PASS}": sum(v >= IOU_PASS for v in with_iou)},
        "missingAndAmbiguous": {"cases": len(negatives), "exactStatus": sum(c["statusCorrect"] for c in negatives),
                                "safe (NOT_FOUND or AMBIGUOUS)": sum(c["safe"] for c in negatives)},
        "latencyMs": {"providerP50": percentile(provider_ms, 50), "providerP95": percentile(provider_ms, 95),
                      "totalP50": percentile(total_ms, 50), "totalP95": percentile(total_ms, 95)},
        "apiErrors": errors,
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    out = results_dir / f"{stamp}-{mode}.json"
    out.write_text(json.dumps({"summary": summary, "cases": cases, "photos": photos}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
