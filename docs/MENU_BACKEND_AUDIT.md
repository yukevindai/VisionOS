# Reflex backend capability audit — menu, kiosk and elevator

Audit date: October 24, 2026. This audits the existing baseline before the isolated Menu Reader
MVP. It is a source/evidence review, not a new device test or provider benchmark. No active
tracking, depth, audio or provider source was edited and no new API calls were made.

## Classification and scope

The requested categories need a scope distinction: a real Gemini API call on a saved image is
**Working only on saved images/tests**, not proof that the installed Swift app works on a
physical elevator/kiosk. Source wiring alone is **Implemented but unverified**. The native
audit in [MENU_NATIVE_AUDIT.md](MENU_NATIVE_AUDIT.md) covers production composition and audio.

| Capability | Classification of existing capability | Concrete evidence and practical limit |
| --- | --- | --- |
| Recognize elevator labels and identify requested floor | **Working only on saved images/tests**, including real API responses | `backend/r3_vision/gemini.py:31` `PROMPT` lists physical interactive controls; `pipeline.py:47` `_element` and `:64` `build_screen_model` preserve reported labels/regions; `selector.py:72` `select` matches the requested floor. `backend/tests/test_r3_vision.py:124` `test_find_floor_5`; Swift `TargetSelectorTests.swift:52` `testFindFloor5`. Real saved floor-5 examples are listed below. |
| Repeated labels / ambiguous floor choice | **Working only on saved images/tests** | `selector.py:46` `match` requires requested numbers to occur in the label; `select` returns `AMBIGUOUS` for tied best matches instead of picking a position. Tests: `test_unclear_and_duplicate_targets` (`test_r3_vision.py:142`); Swift `testNumberMustBeOnTheLabel` (`:78`), `testDuplicateLabelsAreAmbiguous` (`:100`), `testDuplicateWithUnlocatableTwinIsStillAmbiguous` (`:110`). This does not establish accuracy on repeated real panels. |
| Distinguish button surface from adjacent plate/braille text | **Working only on saved images/tests** | The prompt excludes signs/printed plates as standalone targets and asks for the pressable surface. `select` prefers a typed control over an untyped same-label plate. `test_button_outranks_its_printed_label` (`test_r3_vision.py:149`); Swift `testButtonOutranksItsPrintedLabel` (`TargetSelectorTests.swift:222`). |
| Handle glare, small text and unusual layouts reliably | **Implemented but unverified** for degraded-input reporting; real robustness is unproven | Prompt supports legibility, availability and unavailable reasons; `pipeline._element` drops an unreadable label to empty, and `selector.select` refuses partial/unreadable labels. `test_unclear_and_duplicate_targets` and `test_kiosk_partial_view` (`:182`) are simulated. There is no measured diverse real-panel glare/OCR accuracy evaluation. |
| Identify a visible kiosk menu item | **Working only on saved images/tests**, including some real API responses | `PROMPT` includes touchscreen controls/menu items; schema `gemini.py:91` `RESPONSE_SCHEMA` has label/type/box plus optional price/section. `test_kiosk_partial_view` recognizes Big Mac/$5.99 and refuses ambiguous fries. Swift `testFindTheBigMacPrefersExactLabelOverLongerItem` (`TargetSelectorTests.swift:62`). Local real-provider captures selected Cheese, Corn Cup and ChocoTop; they are not physical kiosk acceptance tests. |
| Understand a whole visible kiosk screen | **Partially implemented** | `pipeline.read_screen` (`pipeline.py:109`) returns an inventory of reported controls and section strings rather than just one selected item. `MAX_ELEMENTS=200`/`MAX_TEXT=120` bound the parser. Regions support layout inspection, but there is no full screen graph, description/option relationships, reliable completeness proof or all-text OCR transcript. |
| Read a printed restaurant menu | **Missing** as a reliable full-menu reader | `wallMenu` is an enum value, not an implementation. The existing prompt explicitly requests only elements a finger presses/taps and excludes signs/headings. Printed menu lines/descriptions are not interactive controls. Reusing this prompt as a document reader would omit intended information. |
| Dish names, prices and categories | **Partially implemented** for kiosk controls | Schema has `label`, optional `price` and `section`; `pipeline._element` only whitelists those fields. These can populate kiosk item summaries. Price association is whatever Gemini reported; there is no dedicated OCR/layout-to-item price association algorithm or measured association benchmark. |
| Descriptions, size/option variations, explicit allergens/ingredients | **Missing** as structured menu data | Neither `RESPONSE_SCHEMA`, `pipeline._element`, nor Swift `InteractiveElement` has description/ingredient/allergen/variant fields. The parser would discard unrecognized model fields. There is no explicit-allergen QA policy to reuse. An absent field never proves an allergen is absent. |
| Ask natural-language menu questions / cheapest item | **Missing** in the production reader | `selector.match/select` performs label-token matching, not questions or price comparisons. `target_query.py:26` `prompt` and `:39` `run` ask only FOUND/NOT_FOUND/AMBIGUOUS for one physical object. Native `R3TargetEvidenceClient.answerMenu` (`ios/App/Integration/R3TargetEvidenceClient.swift:63`) throws `unsupportedTask`. |
| Menu reading adapter | **Partially implemented** | `R3TargetEvidenceClient.readMenu` (`:58`) maps a screen through `R3TargetMapping.menu` (`R3TargetMapping.swift:75`): clear control labels/prices are grouped by section and partial/unreadable flags retained. This is a limited mapping, not printed-document extraction. Production `OnDeviceTargetEvidenceClient` has no fallback in `AppDependencies.nativeIntegration`; see native audit. |
| Multi-photo/menu-page merging, deduplication, scan coverage | **Missing** | Existing `ScreenModel` represents one `frameId`/capture. No baseline document/session menu store, page merge, stable item identity, duplicate conflict resolution or user-confirmed complete-scan state exists. `availability=partial` is useful per-image uncertainty, not multi-page coverage. |
| Updated kiosk screens and multi-step interaction | **Partially implemented** | R2 Tasks has kiosk/menu state/effects (`TaskTypes.swift:182`, `:227`) and coordinator dispatch (`SessionCoordinator.swift:639`), but the cloud-to-physical route lacks qualified evidence and the baseline has no implemented visual result verifier: `R3TargetEvidenceClient.verify` (`:67`) returns `.unavailable`. State-machine tests are not observed real kiosk transitions. |
| Spoken conversational input/output, stop/barge-in | See **native audit** | Backend `audio_guidance.py:19` has a fixed short guidance vocabulary, not a conversational menu speaker. `make_clips` (`:201`) uses offline Windows SAPI; `ClipPlayer.play/stop` (`:361`, `:366`) can replace/stop clips. This does not implement menu dictation, microphone STT or dialogue state. Native speech/UI paths are separately audited. |
| Read-image capability accessible to Swift | **Implemented but unverified** on actual iPhone | Authenticated `vision:readScreen` -> private Python service -> Gemini -> `Targets.ScreenModel`; exact frame/request/capture correlation is implemented. Separate `TargetReaderModel` selects in a snapshot and expressly says no physical guidance (`TargetReaderModel.swift:251`). Device installation and hosted bridge have not been validated; see recorded native validation. |

## Actual local evidence: keep provenance distinct

These ignored local artifacts exist on the implementation host; they are not portable committed
fixtures and should not be copied into source without explicit review:

| Artifact | Recorded observation | What it does NOT establish |
| --- | --- | --- |
| `backend/recordings/r3-live/capture-f943138a2b9d49e98c543f3e0d36009e/result.json:204` | REAL_API, elevator panel, SELECTED `5`; provider 14681.5ms (`:247`). | A physical iPhone-app elevator task, button press or verified guidance. |
| `backend/recordings/r3-tracking/track-7b2c3133984f4007b85485c1046abe72/result.json:156` | REAL_API, SELECTED `5`; provider 22795.4ms (`:199`). | Continuous tracking accuracy or physical panel acceptance. |
| `backend/recordings/r3-tracking/track-8c4fb363f87a48e0bada1aab0f63acdb/result.json` | REAL_API, SELECTED `5`; provider 30199.4ms. | A representative latency distribution. |
| `backend/recordings/r3-live/capture-d38e32c714284c9b876650eaae3dfc36/result.json:185` | REAL_API, ordering kiosk, SELECTED `Corn Cup`; provider 14829.4ms (`:228`). | A physical kiosk ordering flow or correct touch/price pairing. |
| `backend/recordings/r3-live/capture-4cb257602d0e4099bb949baae4423933/result.json` | REAL_API, ordering kiosk, SELECTED `Cheese`; provider 23718.8ms. | Complete menu extraction or scene/question accuracy. |
| `ios/Validation/Evidence/2026-10-24-native-r1-r3/gemini-3.8-low-synthetic-panel.json` | Real provider, generated two-button input, selected `2`; validation report records 2.34s total. | Real menu/elevator imagery or hosted/iPhone network behavior. |

`backend/eval/photos` contains `elevator-a.jpg`, `kiosk-a.jpg`, `kiosk-b.jpg`, `remote.png`.
The local draft `backend/eval/manifest.json:2` explicitly says kiosk-a/b are **one UI design
mock-up in two states**, not photos of physical kiosks, and ground-truth boxes/user review are
missing. Its expected Alarm selection is semantic test data, not permission to guide to an alarm.
`backend/eval/evaluate.py:40` `score_case` can compute status/label correctness and IoU only when
reviewed expectations/boxes exist. No `backend/eval/results` directory was found at audit time.
There is consequently no defensible full-menu OCR/name-price accuracy benchmark in this baseline.

The public [native validation record](../ios/Validation/2026-10-24-native-r1-r3.md) expressly says
the app was **not installed/tested on a physical iPhone**, describes the DDI/toolchain blocker,
and separates simulator/native build checks from a single live provider check on synthetic art.

## Backend-to-iOS boundary and limits

- `backend/convex/vision.ts:8` `readScreen` authenticates with `api.users.me` then validates and
  calls `readVisionScreen`. `backend/convex/lib/visionService.ts:18` validates image/request/frame
  fields; `:44` validates response correlation; `:71` bounds the HTTPS request to six seconds.
- `backend/r3_vision/server.py:62` `Handler.do_POST` accepts only private `/v1/read-screen`,
  authenticates the service token before provider work, and returns request ID plus ScreenModel.
  It calls `pipeline.read_screen` (`pipeline.py:109`); `pipeline.run` (`:139`) adds Python
  selection only for CLI/evaluation. Native selection uses the Swift reference selector.
- Native/private image payloads are capped at 512KiB; Python image preparation handles EXIF
  and downsizes provider images to a 1536px long edge (`image_prep.py:14`, `:32`). That is a
  practical small-text limitation for full menus. A new native high-resolution OCR path should
  operate before this upload/downscale, using R1's existing capture owner.
- Tests: `test_vision_service.py:56` auth-before-provider; `:62` correlation; `:72` removed legacy
  route; `:75` invalid bounds; `:112` five-second deadline. `backend/tests/vision.test.ts` covers
  authenticated bridge/response validation. These are simulated/local tests, not deployment.

## Safety facts and extension recommendation

Bare semantic `TargetSelector` does **not** classify emergency/payment controls. Safe physical
conversion is a separate boundary: `R3TargetMapping.target` (`R3TargetMapping.swift:97`) requires
finite physical confidence and explicit `.ordinary` safety. Backend `pipeline._element` omits
both confidence and safety, so current Gemini output intentionally cannot satisfy that physical
adapter. `TargetHandoff.prepare` (`TargetHandoff.swift:37`) also blocks unsafe/emergency/payment
terms/screens. Do not add made-up confidence/safety to make a cloud target guide a hand.

Extend the existing label matching, per-frame metadata, partial-view handling and kiosk task
state rather than rebuilding them. Add a **separate grounded menu-document schema and OCR/layout
pipeline**, with exact quoted evidence, explicit uncertain associations and session storage.
Menu QA must reject ungrounded ingredient/allergen conclusions: “not stated” is not “allergen
free.” Preserve the existing backend auth/frame correlations and R1's sole capture/output owner.
Do not route menu speech through the short fixed-word hand-guidance cue player.
