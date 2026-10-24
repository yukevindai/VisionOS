# R3 backend: photo → Gemini Vision → ScreenModel → TargetSelection

R3 owns this directory. It takes a photo of a physical control panel or touchscreen plus a user request. It asks Gemini to list the visible interactive elements and converts the reply into the Step 1 `ScreenModel` (`ios/Packages/Targets`). It then runs the target selector, returning `SELECTED`, `NOT_FOUND` or `AMBIGUOUS`.

Python 3.10+, standard library plus Pillow. Gemini is called over REST; there is no SDK dependency.

## Status (2026-10-24)

- **Offline tests:** 26/26 pass. Every provider reply in them is **SIMULATED** (hand-written in Gemini's REST shape).
- **Live Gemini:** **not run.** No `GEMINI_API_KEY` was available, and no real elevator or kiosk photos were available. No REAL_API result exists yet.
- **OCR:** not added. No OCR engine is installed here, so Gemini does the reading. Add OCR only if live evaluation shows label errors.

## Setup and run

```sh
cd backend
python -m pip install -r requirements.txt
cp .env.example .env          # then set GEMINI_API_KEY in .env (ignored by Git)
python -m unittest discover -s tests
python -m r3_vision.cli path/to/photo.jpg "Find floor 5"
python -m r3_vision.server    # http://127.0.0.1:8787/v1/select
```

Server request: `POST /v1/select` with `{"imageBase64": "<jpeg or png>", "request": "Find floor 5"}`. The server binds to loopback only and has no authentication yet.

The result has five parts:
- `mode`: `REAL_API` or `SIMULATED`
- `screenModel`
- `selection`
- `provider`: `ok`, `httpStatus`, the verbatim `error`, and token `usage`
- `timingMs`: `prepare`, `provider`, `select`, `total`

## Evaluation with real photos

1. Put photos in `eval/photos/` (ignored by Git). Use at least two elevator panels and two kiosks, from different buildings and brands.
2. Copy `eval/manifest.example.json` to `eval/manifest.json`. For each photo, list requests with the expected `status`. For `SELECTED`, add the expected `label` and a **manually reviewed** `box` (normalized x, y, width, height in the upright photo). Include at least one missing target and one ambiguous target per interface type.
3. Run `python eval/evaluate.py eval/manifest.json`. The summary reports:
   - recognition accuracy
   - box IoU (mean, and count ≥ 0.5)
   - missing/ambiguous handling (exact status, and "safe" meaning never `SELECTED`)
   - provider and total latency p50/p95
   - every API error verbatim
4. Review the overlays in `eval/results/<run>/`: Gemini's boxes in red, ground truth in green. Results are labelled `REAL_API`. `--replay-dir` runs produce `SIMULATED` results and never count as live.

## Rules the code enforces

- **Coordinates.** EXIF orientation is applied before Gemini sees the image. Boxes are normalized, origin top-left, relative to the upright photo. `screenModel.image` records the upright `widthPx`/`heightPx` and the original `exifOrientation`. Gemini's `box_2d` `[ymin, xmin, ymax, xmax]` on 0–1000 is converted without clamping or reordering. A bad box therefore fails as `INVALID_REGION` rather than being repaired.
- **Evidence only.** `confidence` is omitted because Gemini reports none. An unreadable label is stored as `""`, never guessed. A `partial` or unknown legibility makes a single match `AMBIGUOUS`/`LOW_CONFIDENCE`. Each model records `evidence.model`, `modelVersion`, `responseId` and `live`.
- **Whitelisted parsing.** Only known fields with valid types and enums are kept. Extra keys, including anything shaped like a command, are dropped. The prompt tells Gemini to describe only and to ignore instructions printed in the image. Nothing here can emit movement, haptic or payment commands. The selection is a proposal for R2, and R1 still decides guidance.
- **Failures are explicit.** A provider failure produces an `unavailable` ScreenModel with `unavailableReason: providerUnavailable`. The selection status is then `PROVIDER_UNAVAILABLE`, never `NOT_FOUND`, so a failed call is never read as "the button is absent". An unreadable upload is `NOT_FOUND` / `SCREEN_UNAVAILABLE`. The failures are:
  - a missing key or model (no network call is made)
  - an HTTP error, such as 402 or 404
  - a timeout (default 5 s per spec; `errorCode: PROVIDER_TIMEOUT`)
  - a blocked or malformed reply

  The provider's message is kept verbatim, with any key redacted.
- **Keys.** The key lives in the environment or `backend/.env` only. It is sent in the `x-goog-api-key` header, never in a URL, and is never logged or returned.

`r3_vision/selector.py` ports the Swift `TargetSelector`, so photos can be evaluated without Swift. The conformance test runs the Swift package's example files against it. Change both together.

`GEMINI_MODEL` is required and has no default. The client calls exactly that model once per photo and never retries on, or falls back to, another model. Check the chosen model's availability and pricing for the key before the live run.

The interactive region is preferred. The prompt asks for the pressable surface (the physical button or the whole touchscreen card) and excludes printed labels and braille plates. If an untyped (`other`) element and a typed control share a label, the selector picks the typed control. Two typed controls with the same label remain `AMBIGUOUS`.

To keep a model tied to the phone's frame, `POST /v1/select` accepts optional `frameId` and `capturedAtMs` and passes them through unchanged. Without them, a new frame ID and the backend's receive time are used.

Out of scope here: Convex, Cartesia, the phone-side client, LiDAR and full kiosk ordering.

## Windows live camera experiment

See [LIVE_CAMERA.md](LIVE_CAMERA.md) for PathOn connection instructions, on-demand capture,
frozen target overlays, desktop ROI fallback and recorded transform metadata. Start from the
outer workspace with `powershell -NoProfile -ExecutionPolicy Bypass -File .\VisionOS\backend\start-live-camera.ps1`.
This desktop experiment has no movement/audio/haptic outputs and does not replace native R1.

## Continuous live 2D tracking

[Windows tracking instructions](LIVE_TRACKING.md) describe the new **Find & Track** tool:
`powershell -NoProfile -ExecutionPolicy Bypass -File .\VisionOS\backend\start-live-tracking.ps1`.
It matches Gemini's historical reference to current camera frames before drawing a live box,
hides lost/stale targets, and bounds automatic re-identification requests. The frozen camera
program remains available unchanged. This is a visual test, not native R1 or movement guidance.
