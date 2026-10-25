# iPhone menu OCR: compile and connection checklist

Reviewed October 24, 2026. This is a read-only integration handoff. **No Swift file, package
manifest, Xcode project, capture/audio implementation or existing selection test was edited.**
The proof remains outside all build targets and has not been compiled or run on a Mac/iPhone.
The Windows menu experiment is not evidence of native OCR accuracy, latency or device acceptance.

## 1. Compile the existing proof before connecting anything

From the repository root on a Mac with full Xcode selected:

```bash
xcode-select -p
xcrun --find swiftc
xcrun --sdk iphoneos --show-sdk-path

# Mac SDK/type/API check of the isolated source; not an iOS app build.
xcrun swiftc -typecheck \
  ios/Packages/Targets/Prototypes/MenuOCRProof.swift \
  -framework Vision -framework ImageIO -framework CoreGraphics

# Check the same source against the actual iPhoneOS SDK/minimum deployment target.
menu_sdk="$(xcrun --sdk iphoneos --show-sdk-path)"
xcrun --sdk iphoneos swiftc -typecheck \
  -sdk "$menu_sdk" -target arm64-apple-ios17.0 \
  ios/Packages/Targets/Prototypes/MenuOCRProof.swift \
  -framework Vision -framework ImageIO -framework CoreGraphics
```

Record the Xcode/SDK versions and actual diagnostics. These commands are instructions, not
reported passes. Fix any compiler/API/concurrency issues in the teammate's integration branch
before claiming the proof works. The proof's declared API availability is iOS 13/macOS 10.15;
the actual Reflex app and Targets package currently require iOS 17.

## 2. Put one reviewed source into the app target

`MenuOCRProof.swift` currently resides in `ios/Packages/Targets/Prototypes/`, outside `Sources/`.
`ios/Packages/Targets/Package.swift` does not include it. A passing `swift test` of Targets will
therefore **not** compile or test this proof. `ios/Scripts/generate_project.py` scans `App`,
`Tests/ReflexAppTests` and `UITests`; it does not discover the prototype directory.

The smallest owner-side route is to promote the reviewed source to
`ios/App/Targets/MenuOCRProof.swift` and add a small `MenuDocumentAdapter.swift` alongside it,
then regenerate the project through its R2 owner. Ensure exactly one definition is compiled;
do not also add the prototype to the same Sources build phase. No new Swift package, camera
engine or replacement task engine is required for this slice.

New isolated app tests should go under `ios/Tests/ReflexAppTests/` (for example
`MenuOCRTests.swift` and `MenuDocumentAdapterTests.swift`). Once the owner has made those target
changes and completed the repository's existing model/config setup:

```bash
python3 ios/Scripts/generate_project.py
xcodebuild -project ios/Reflex.xcodeproj -scheme Reflex \
  -configuration Debug -sdk iphoneos -destination 'generic/platform=iOS' \
  CODE_SIGNING_ALLOWED=NO build
```

This is an unsigned compile check, not installation. Use the actual developer team and connected
iPhone in Xcode for a signed device run; never invent a team ID or device UDID. The recorded
CoreDevice/DDI installation issue in `ios/Validation/2026-10-24-native-r1-r3.md` needs resolving
on that Mac/phone pair before claiming physical-device success.

## 3. Receive a snapshot from the existing owner

`NativePerceptionGuidanceClient.start` creates the single ARSession. Its internal
`NativePerceptionProcessor.observe` already has `ARFrame.capturedImage`, a frame UUID,
`ARFrame.timestamp` converted to monotonic milliseconds, rotation and geometry ID.
`FrameSnapshot` retains context/frame/time/geometry/transform, preview and encoded JPEG.
Do not open VisionKit DataScanner, a second AVCaptureSession or another ARSession while native
assistance owns the camera.

The owner should expose a bounded one-shot full-resolution CGImage/pixel-buffer snapshot to the
new menu adapter. Avoid blocking the AR delivery queue to perform OCR, and do not perform OCR on
every camera frame. Use a separate bounded worker/task with cancellation and at most one active
menu extraction. This is a capture-owner change, not something to patch into Claude's Windows
PathOn/ALL OBJECTS/hand/LiDAR loop.

Call `MenuOCRProof.recognize(image:orientation:frameId:capturedAtMonotonicMs:accuracy:recognitionLanguages:coverage:)`
with the **actual** pixels and metadata. Use `NativePerceptionGeometry.imageOrientation(rotation)`
for raw AR pixels through its owner. If pixels were already physically oriented, pass `.up`;
never apply EXIF/rotation twice. Do not substitute Date/UTC for the local monotonic capture time.

The existing `TargetReaderImageEncoder` caps uploads at 512 KiB and may reduce resolution. Its
JPEG/preview is useful for UI/service transport but should not become the only source for tiny
menu text. The isolated proof records identity crop relative to its supplied image. If the owner
supplies a crop, retain the parent-frame crop/resize transform separately rather than pretending
its boxes refer to the full original camera frame.

## 4. Preserve the proof's output and map it into menu evidence

| Proof field | Adapter responsibility |
| --- | --- |
| `pageId`, `frameId`, `capturedAtMonotonicMs` | Retain as historical capture identity/time; never refresh it for a follow-up question. Also retain session/generation and owner geometry ID in the app adapter. |
| `image.originalSize`, `width`, `height`, `exifOrientation`, `orientationApplied`, `cropNormalized` | Keep the raw/oriented/crop distinction. Vision boxes are converted once from bottom-left to upright top-left; `words[].box` is upright pixel x/y/width/height. |
| `lines[].text`, `words[].text` | Preserve exact recognized text. No spelling, price or ingredient completion. `usesLanguageCorrection=false` does not make recognition error-free. |
| `lines[].candidateConfidence`, `words[].lineCandidateConfidence` | Actual recognized-line candidate confidence, **not** word/price accuracy and not calibrated physical confidence. `words[].confidence` is absent/null intentionally. |
| `warnings` and missing word boxes | Full line text remains available even when a token cannot be located. Keep that text as unresolved evidence; do not silently discard it because the spatial token list is incomplete. |
| `coverage` | Only unknown/partial is supported. No image proves all pages/regions of the menu were captured. |
| `timing.engineMs` | OCR engine timing only. Measure capture, queue, layout, first readable result and speech onset separately. |

The Python `menu_reader.layout.document` accepts the `image.width/height` and word-box shape.
For an on-device product, port or bridge the small layout/grounding layer; do not assume the
Python CLI runs in Swift or that its current JSON automatically extends the existing control-only
`Targets.ScreenModel`. Keep a dedicated MenuDocument/evidence store alongside it. Price/description
relationships, currency, source IDs, uncertainties and review state must survive any mapping.
Current `Tasks.MenuContent` string sections cannot carry all of that information by themselves.

## 5. Cache first; adapt existing speech at conversation priority

The production composition currently gives normal assistance `OnDeviceTargetEvidenceClient`
without a Gemini fallback (`AppDependencies.nativeIntegration`). The separate snapshot-reader
UI does not fix that routing. `R3TargetEvidenceClient.answerMenu` explicitly throws
`unsupportedTask`; an iOS owner must connect menu intents and a real cached answer provider.
Do not use simulator-only `SessionCoordinator.submitFixtureText` as evidence of production voice
support. Follow-up questions should read the cached MenuDocument and make zero new OCR/provider
requests for supported price/section questions. Camera motion after capture must not erase it.

`SpeechSchedulerClient` in `App/Integration/AdapterProtocols.swift` exposes
`speak(_:priority:context:)`, `cancel(context:)` and `localStop(context:)`; the live implementation
is `NativeAudioCoordinator`. Convert organized reading/Q&A requests to **priority 4** using the
current SessionContext. Menu dialogue must not masquerade as priority-2 physical guidance.
Retain the menu reading generation/event ID at the adapter so canceled replies cannot speak later.

Important queue detail: `NativeAudioCoordinator.speak` cancels active output when a new utterance
starts. **Do not loop over all chunks and call speak immediately**; only the last may remain.
The current protocol has no public completion event. The audio owner must expose a small actual
finish/cancel acknowledgement or queue bridge to release one chunk at a time; do not advance a
reading cursor by a timer, caption callback or guessed speech duration. Do not replace the existing
AVSpeechSynthesizer delegate. Stop/pause must invalidate pending work and cancel through the owner.
General question barge-in remains incomplete because normal transcripts are filtered while speaking.

The isolated Python speech adapter provides request preparation/injected-scheduler tests, not a
deployed bridge into this Swift process. Do not import or rewrite Claude's short cue/audio engine.
Only explicitly confirmed order text may be offered for staff-directed speech; never place an
order, tap purchase/payment controls or dispatch staff speech automatically.

## 6. Required focused validation before a device claim

Use the same real menu photos and reviewed subset of text/price associations as the Windows
comparison. Run `.fast` and `.accurate` separately. Record device/OS/SDK, original dimensions,
OCR time, first-readable/structured time, exact text errors, missing lines, wrong price pairs and
memory. A low CER on large text does not prove small-text coverage or completeness.

Test all eight EXIF values, mirrored/90-degree cases, glare, blur, small text, two columns,
detached prices, repeated similar names with different prices, partial captures and multiple pages.
Verify source-box overlays on the visually upright image. Verify unlocated lines remain readable
as uncertain text, currencies are not guessed, and absent allergen text causes abstention.
Test cached questions after the camera turns away, cancellation/session changes, Stop during an
utterance, pause/resume/skip, and priority preemption. Never use cached text boxes for hand movement;
physical guidance retains R1's fresh calibrated evidence and 300ms rule.

After app membership/adapters/tests exist, choose an installed simulator UDID using
`xcrun simctl list devices available`, then run only the focused tests using that actual value:

```bash
xcodebuild -project ios/Reflex.xcodeproj -scheme Reflex \
  -destination "platform=iOS Simulator,id=$IOS_SIMULATOR_UDID" \
  -only-testing:ReflexAppTests/MenuOCRTests \
  -only-testing:ReflexAppTests/MenuDocumentAdapterTests \
  CODE_SIGNING_ALLOWED=NO test
```

The named menu tests are **to be added by the iOS owner**, not tests already present/passed.
Simulator execution cannot establish camera OCR/glare, real microphone/echo, VoiceOver or device
acceptance. Do a signed physical-iPhone run and record the observed result separately.

## 7. Reuse and preserve kiosk/elevator functionality

Keep `Targets.TargetSelector.select`, `Targets.ScreenModel`/normalized region conventions and
`R3TargetMapping` frame/crop validation. Menu OCR text can inform a screen inventory, but a dish
document is not a physical Target. Preserve `R3TargetMapping.target` requirements for clear labels,
finite confidence and explicit ordinary safety; never invent those fields from OCR confidence.

Keep `TaskEngine` kiosk/menu state and the existing screen-change invalidation/payment boundary.
Useful current regression names (not rerun for this handoff):

- `TargetsTests/TargetSelectorTests.testFindFloor5`, `testNumberMustBeOnTheLabel`,
  `testDuplicateLabelsAreAmbiguous`, `testDuplicateWithUnlocatableTwinIsStillAmbiguous`,
  `testFindTheBigMacPrefersExactLabelOverLongerItem`, `testInvalidBoundingBoxesAreNeverSelected`.
- `TasksTests/KioskAndMenuTests.testCanonicalSelectionStillStopsBeforePayment`,
  `testKioskTapInvalidatesCoordinatesAndRereadsBeforeNextChoice`,
  `testScreenChangeMidGuidanceStopsAndRejectsOldCoordinates`,
  `testDuplicateLabelsRequireClarificationAndModelCannotChoose`.
- `ReflexAppTests/R3TargetEvidenceTests.testRecognitionAcceptsMissingConfidenceButGuidanceDoesNot`,
  `testConfidentOrdinaryTargetPreservesFrameAndMapsCrop`,
  `testPaymentScreenNeedsNoInventedConfidenceAndHasNoControls`.
- `ReflexAppTests/TargetReaderTests.testSnapshotUsesCanonicalSelectorAndCaptureClock`,
  `testStaleFramesAndSimulatedRepliesCannotBecomeLiveRecognition`,
  `testImageEncoderAppliesRotationOnceAndBoundsUpload`.

No existing selection implementation or fixture was changed in this slice. That is preservation
by edit scope, not a fresh regression/device pass. Full native menu narration, general Q&A and
physical kiosk/elevator acceptance remain unverified or incomplete until the above wiring and
focused/device tests are actually completed by their owners.
