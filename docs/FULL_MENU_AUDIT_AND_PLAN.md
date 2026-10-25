# Reflex feature audit and Full Menu Reader checkpoint

October 24, 2026. Source audit + isolated Windows experiment. No native iPhone deployment,
physical kiosk/elevator test, live-user test or new cloud request was performed. No active
tracking/depth/audio/provider/app files were edited; Claude's existing work remains separate.

## What the current app actually supports

| Existing feature | Classification and evidence |
| --- | --- |
| Elevator labels/requested floor; kiosk item matching | **Working only on saved images/tests**, including real Gemini provider replies. `pipeline.read_screen`, `selector.select`, Swift `TargetSelector.select` and fixture tests exist. Saved floor-5/kiosk calls are not physical iPhone-app acceptance. |
| Separate native Gemini snapshot reader | **Implemented but unverified** on device: `TargetReaderModel.captureAndRead` -> authenticated `vision:readScreen` -> private Python service -> canonical Swift selection. It explicitly does not guide a hand. |
| Whole visible kiosk screen | **Partially implemented**: inventory of controls, sections, prices and payment state. No comprehensive document/layout graph, item descriptions/variants/allergens, or proven screen completeness. |
| Normal native menu/elevator/kiosk assistance | **Partially implemented** state machines/adapters. Production `AppDependencies.nativeIntegration` supplies an object-only client with no Gemini fallback; normal voice routing does not activate these flows. |
| Full printed-menu extraction | **Missing** in the existing production app. Current Gemini control prompt explicitly excludes noninteractive headings/signs. A `wallMenu` enum or menu adapter is not document OCR. |
| Natural menu Q&A and cheapest-item reasoning | **Missing** in the production reader: `R3TargetEvidenceClient.answerMenu` throws `unsupportedTask`. Selector token matching is not conversational understanding. |
| Speech recognition and speech output | **Implemented but unverified** on actual iPhone: `NativeAudioCoordinator` uses Apple speech recognition and scheduled AVSpeechSynthesizer output. Reader results are not automatically narrated. |
| Stop and interruptions | **Partially implemented**: spoken Stop is checked before echo filtering and cancels output. Other speech is discarded while speaking; arbitrary-question barge-in and a full-menu pause/resume/skip cursor are missing in production. |
| Repeated labels, partial legibility, unsafe actions | **Working only on saved images/tests** for selector and task safeguards; glare and real layouts remain unproven. Physical mapping requires measured confidence/ordinary safety that current Gemini output does not provide. |

No requested end-to-end native menu/kiosk/elevator feature can be classified **Working and
live-tested** from the available evidence. Windows phone streaming/provider snapshots are a
different scope. Native validation records explicitly say physical device install/test was not
completed; simulator tests and synthetic-art provider calls are not a substitute.

Detailed file/function/test citations and validation limits:

- [Native audit](MENU_NATIVE_AUDIT.md).
- [Backend audit](MENU_BACKEND_AUDIT.md).
- [Technology/product research with primary sources](MENU_TECHNOLOGY_RESEARCH.md).

## Small independent deliverable now available

The [saved-photo menu reader](../backend/menu_reader/README.md) is runnable and does not import
Claude's live modules. It uses installed Windows OCR, then conservative geometry-based parsing:

`saved image -> EXIF correction -> original-resolution OCR/tiles -> exact words+boxes ->
sections/items/same-row prices -> source-backed menu cache -> local questions/reading chunks`.

It retains exact wording, descriptions, explicit notes, monetary strings, sources and uncertainty.
It supports simple sections, one/two-column layouts, exact duplicate capture merging, similar
names with different prices, partial coverage warnings and a limited local question set.
Multiple prices/unclear relationships remain unstructured rather than guessed. Ingredient and
allergen questions quote explicit associated evidence or abstain. Lowest-price output is labeled
provisional until the user reviews facts; unknown/mixed currencies are not compared.

The session keeps the menu after the camera is gone. Reading commands test chunk pause/resume/
skip/stop. Drafted order text requires confirmation and is never automatically spoken to staff
or submitted. **This MVP has typed interaction and text output, not a completed voice assistant.**

From `VisionOS/backend`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-menu-reader.ps1
```

For a real saved photograph, add `-ImagePath "C:\photos\menu.jpg"`. Default demo artwork is
explicitly SIMULATED. No API key, new model installation or PathOn connection is needed.

## Measured benchmark

Actual Windows.Media.Ocr, English en-US, on generated artwork. One run per image, no camera.
“First text” includes image preparation/PowerShell startup; OCR itself does not stream lines.

| Controlled case | OCR engine ms | First text ms | Structured ms | Exact name-price pairs | CER |
| --- | ---: | ---: | ---: | ---: | ---: |
| Printed menu | 29.80 | 941.53 | 942.75 | 3/3 | 0% |
| Two columns | 27.82 | 513.03 | 514.24 | 5/5 | 0% |
| Kiosk artwork | 32.65 | 516.43 | 517.13 | 2/2 | 0% |
| Elevator artwork | 14.50 | 520.26 | 521.99 | N/A | 0% |
| Small text | 18.38 | 505.32 | 507.96 | 3/3 | **36.95%** |
| Partial capture | 13.15 | 492.62 | 494.28 | 2/2 | 0% |
| Similar names/prices | 15.35 | 477.77 | 479.80 | 2/2 | 0% |

Across this deliberately small set: **17/17 exact name-price pairs and section assignments**,
no unexpected/missing priced entries, **7/7 scoped QA checks**, QA <=0.943ms. This is not a
representative real-menu accuracy score. Small-text OCR is substantially wrong despite matching
the three item/price pairs, demonstrating why those metrics cannot imply full text correctness.
Sampled OCR child-process peak RSS was **105.94–110.05 MiB**, CPU time **0.406–0.844s**. This
is not total-system memory/battery use. Process startup dominates these desktop timings.

Evidence: ignored `backend/.runtime/menu-eval/benchmark.json`, per-case `ocr.json`/`menu.json`,
generated images and `truth.json`. Reproduce with `python -m menu_reader.evaluate` from backend.
Portable measurements and QA outcomes: [MENU_EVALUATION_2026-10-24.json](MENU_EVALUATION_2026-10-24.json).
19 focused parser/QA/grounding tests and a saved-OCR interactive session passed. No full shared
regression suite was run for this independent slice.

A matched full-image Gemini benchmark adapter is present but **NOT RUN**: required key/model
configuration was unavailable in this process; no request was made. Prior recorded Gemini calls
on other images range roughly 14–30 seconds, and a native-validation synthetic-panel low-effort
call was 2.34 seconds. These are different tasks/inputs and cannot establish a measured speedup
ratio against the menu OCR results. PaddleOCR and native Vision were researched, not benchmarked
here. Do not substitute published component timings for Reflex end-to-end latency.

## Proposed everyday menu flow

On R1's existing camera stream, use local text detection and menu-like layout/price evidence
to propose menu mode; no standard YOLO class is a substitute for reading. A deliberate “read
the menu” request triggers a high-resolution snapshot and first OCR result, not a 15-second
cloud image loop. Summarize recognized sections first and offer a section or all captured text.
If text is tiny/clipped, explain the specific uncertainty and request a closer/overlapping
capture. Retain image/page/crop/orientation IDs and all original spans as each page arrives.

Deduplicate exact overlapping facts, flag conflicts, and preserve incomplete/unreadable regions.
No snapshot proves all menu pages were seen. Stop repeated narration using a menu-document
version/content hash; keep its facts available after the camera moves away. Complex layout or
arbitrary questions can use Gemini on that cached OCR evidence, with returned field/span IDs
checked against the actual capture. Unmatched names, ingredients, prices and source IDs are
rejected. Price arithmetic remains local. “Not stated” never becomes allergen-free.

Feed section/item chunks through R1's existing conversation-priority scheduler. A reading
cursor advances on completed/canceled utterance events and explicit commands, allowing Stop,
pause/resume/skip and follow-up questions without restarting the menu. Hazards/physical cues
retain higher priority. Only a user-confirmed order draft may become a speech request to staff;
there is no automatic order/payment/tap. If a kiosk control must be touched, re-enter the existing
R2/R1 task pipeline and reacquire fresh calibrated evidence; cached menu coordinates authorize
no physical movement.

## Best technology to reuse

1. **iPhone: Apple Vision OCR first**, on R1-owned full-resolution snapshots. Use a fast preview
   pass and accurate refinement selectively; retain boxes, raw text, actual confidence and
   orientation. VisionKit is useful for a scanner product but must not create a competing camera
   session in the active ARKit call.
2. **Windows prototype: installed local OCR** now; benchmark PaddleOCR mobile/RapidOCR in an
   isolated environment if this host adapter is insufficient. Windows OCR's documented desktop
   deployment support requires MSIX identity despite the measured unpackaged-host success.
3. **Layout and grounded menu state before cloud conversation.** Compare decimal prices locally;
   treat same-name/different-price entries separately; keep unresolved variants and coverage.
   Use Gemini on OCR text for difficult relationships/questions with strict source validation,
   or a high-resolution image only when text/layout needs it. YOLO is a locator, not an OCR engine.
4. **Reuse Apple STT + existing R1 speech scheduler** for the demo. Feed priority-4 menu chunks,
   maintain a reading cursor, cancel on Stop, and add deliberate question interruption handling.
   Gemini Live supports real-time conversation/interruption but requires a separate streaming
   integration, grounded menu-context synchronization, audio lifecycle and access controls.

Seeing AI already supports scanned-document/menu questions; Lookout, Be My Eyes and accessible
menu/kiosk tools also address reading/access. Reflex's opportunity is a grounded, low-latency,
explicitly incomplete menu workflow integrated with carefully validated physical assistance,
not a claim that reading menus or asking about them is novel. See the research comparison.

## Native integration plan and ranked demo blockers

1. **Make native OCR/menu capture runnable on a physical iPhone.** The separate uncompiled
   [Swift Vision proof](../ios/Packages/Targets/Prototypes/MenuOCRProof.swift) takes owner-supplied
   CGImage/frame/orientation/time metadata and emits compatible pixel word boxes, exact lines
   and real candidate confidence. It opens no camera or audio engine. Its [README](../ios/Packages/Targets/Prototypes/README.md)
   contains Mac typecheck/EXIF/device steps. Fix the recorded Xcode/DDI/device-install issue;
   Windows cannot validate this proof or hardware speech/camera behavior.
2. **Add a grounded MenuDocument/evidence adapter in production.** Keep raw OCR spans plus
   descriptions/prices/options/allergen quotations and provenance. Existing `MenuContent` string
   sections alone are insufficient. Implement `answerMenu` and route “read the menu” into it;
   do not confuse simulator fixture commands with real production voice intents.
3. **Build capture coverage and uncertainty controls.** A visible screen/rectangle is not a complete
   menu. Use high-resolution snapshots for small text, request closer/overlapping captures,
   retain page identities, deduplicate exact facts and flag conflicting names/prices/titles.
   Ask for explicit coverage confirmation; never automatically declare all pages scanned.
4. **Connect menu conversation to native reading/interruptions.** Keep historical menu facts after
   camera movement, avoid repeated narration, add pause/resume/skip and controlled barge-in.
   Draft/speak the user's order only with explicit direction/confirmation; never order or pay.
5. **Extend elevator/kiosk support instead of rebuilding.** Reuse `TargetSelector`, normalized
   boxes/frame correlation, R2 kiosk screen-change/payment state and ambiguity rules. Add local
   OCR/button association plus Gemini fallback. Test repeated labels, glare, panels, screen
   transitions and partial visibility with reviewed ground truth. A 2D OCR/AI box cannot guide
   a hand until R1 independently validates current calibrated camera/depth/hand evidence.

No native/app/shared-contract changes were made. Remaining limits: real restaurant photos with
human-reviewed names/prices/allergens, large/stylized/distant boards, complex variants, reliable
missed-text detection, Apple/Paddle matched benchmarks, general grounded QA, speech integration
and real-device accessibility acceptance. Cached menu text is historical conversational evidence;
it must never be relabeled as fresh <=300ms physical-guidance evidence.
