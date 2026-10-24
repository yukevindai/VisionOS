# Targets (R3)

Foundation-only Swift 5.9 package, iOS 17 / macOS 13. R3 Step 1: a device-independent `ScreenModel` and a `TargetSelector` that maps a user's words to one control. It knows no device, brand, layout or button position. It calls no provider (Gemini, Convex, Cartesia), opens no camera, and plays no audio. It also sends no guidance or haptic command.

```swift
let model = try JSONDecoder().decode(ScreenModel.self, from: data)
let result = TargetSelector().select("Find floor 5", in: model)
// result.status: SELECTED (result.target), NOT_FOUND (result.reason) or AMBIGUOUS (result.candidateIds)
```

## Safety boundary

A `TargetSelection` is a proposal. R2's task engine validates it against current evidence. R1 decides whether and how to guide. Nothing in this package can move a hand, buzz a band, confirm a press, or authorize payment (spec S4, S6). `target.imageRegion` is historical metadata for `frameId`. R1 must re-track it before guiding. Every mock observation uses `source: "SIMULATED"` and a `SIMULATED` frame ID.

## Selection rules

1. Words are lower-cased and diacritic-folded. Number words and ordinals become digits ("fifth" → `5`). Request filler words ("find", "the", "press", "button", ...) are dropped.
2. A label matches in rank order. **exact** means the same words. **labelInRequest** means the label's words are all in the request ("floor 5" → `5`), and any number in the request must also be on the label. **requestInLabel** means the request's words are all in the label ("fries" → "Small Fries").
3. Only the best-ranked tier counts. More than one control in that tier is `AMBIGUOUS`: `DUPLICATE_LABEL` when the labels are identical, otherwise `MULTIPLE_MATCHES`. Controls without a usable region still count, so an unlocatable twin is never silently dropped.
4. A single match with an invalid or missing box is `NOT_FOUND` / `INVALID_REGION`. Valid boxes are finite, non-empty and inside 0...1, the same rule as R2's `ImageRegion.isValid`. A single match below `minimumConfidence` (default 0.6), or with a `legibility` other than `clear`, is `AMBIGUOUS` / `LOW_CONFIDENCE`.
5. These cases return `NOT_FOUND`: an `unavailable` screen, an unsupported coordinate system, an empty or duplicate control ID, or a request with no content words. A `partial` screen with no match reports `NO_MATCH_PARTIAL_VIEW`, so the user can be asked to reposition.

## Fixtures and examples (SIMULATED)

- `Tests/TargetsTests/Fixtures/SIMULATED_elevator_panel.json`: generic elevator panel (B, 1–7, Open, Close, Alarm)
- `Tests/TargetsTests/Fixtures/SIMULATED_ordering_kiosk.json`: generic ordering-kiosk menu screen (items with prices, View Order, Checkout)
- `Tests/TargetsTests/Fixtures/examples/*.json`: example request + expected `TargetSelection` output. The tests run these files.
- `Tests/TargetsTests/Fixtures/SIMULATED_gemini_pipeline_elevator.json`: written by `backend/r3_vision` from a hand-written Gemini-shaped reply (not a live API result). It contains deliberate defects (partial legibility, inverted box, unreadable label, duplicate label).

The fixtures are hand-written, not captured. Move them to the shared `fixtures/` directory only after the team agrees on them as cross-team inputs.

## Tests

```sh
swift test --package-path ios/Packages/Targets
```

## Proposed shared-contract changes (not applied)

The shared v3 `ScreenModel` is `frameId, controls[{id,label,price?,region}], sections[], isPaymentScreen`. This package keeps those keys and adds R3-local fields. They become shared only once the owners agree:

- Selection status `PROVIDER_UNAVAILABLE` (provider failed; says nothing about the target). Typed controls outrank an untyped element with the same label.
- `capturedAtMs` (UTC ms), `panelType`, `availability` + `unavailableReason`, `coordinateSystem`, `source`, `notes`, `image` (upright `widthPx`, `heightPx`, `exifOrientation`, `orientationApplied`), `evidence` (`provider`, `model`, `live`, `latencyMs`, `modelVersion`, `responseId`)
- `controls[].type`, `controls[].confidence`, `controls[].section`, `controls[].legibility`

Example: a control `{"id":"el-5","label":"5","type":"button","confidence":0.91,"region":{"x":0.55,"y":0.5,"width":0.12,"height":0.08}}` in a model with `"coordinateSystem":"normalizedImageTopLeft","source":"SIMULATED"`.

R2 integration is not wired. R2 owns `ios/Scripts/ci_xcodebuild.sh`, so it does not run this suite yet. Proposed line: `run_logged targets-package-tests swift test --package-path ios/Packages/Targets`. A mapping from `SelectedTarget` to R2's `ResolvedTarget` is likewise left to the R2 adapter.
