# R2 conversational speech and UI follow-up — October 24, 2026

R3 has returned to the teammate. This change is limited to native iPhone integration,
UI/test repairs and documentation. No backend, provider environment, deployment,
shared target schema or teammate module was changed.

## Speech behavior

- Native composition supplies `ConvexSpeechClient` to the existing shared audio
  coordinator when public service configuration is valid. It uses stored Auth0
  credentials, rechecking account identity around the request, and never opens a
  login prompt while guiding. Missing configuration or sign-in uses local speech.
- Only priority 4 calls `speech:synthesize`. The original scheduler still owns
  playback and safety preemption; priorities 0–3 remain local.
- The request gate enforces a five-second total deadline, independent of a
  provider's cancellation cooperation. Provider errors, `SIMULATED` replies,
  malformed/mismatched responses, invalid audio, and decode/playback failure use
  the original local utterance. Text exceeding 300 UTF-16 units stays local.
- Stop, cancellation, session/generation changes, microphone mute, teardown,
  higher-priority output and replacement speech invalidate pending responses.
  New eligible owner speech cancels pending synthesis before its transcription
  debounce, preventing a reply from hiding the owner's new utterance.
  Cancelling a clip also caps residual echo suppression at 500 ms rather than
  retaining that clip's original end time.
- No provider key is present in the phone. R3 must deploy the authenticated action
  and configure the server. This is a bounded TTS demo path, not the planned
  streaming STT/Claude/LiveKit worker.

## CI findings and repairs

The reported seven failures in GitHub iOS job `120947724037` (run `40119384455`)
were four UI test cases and three assertions in one lifecycle test.

- The reconnect test assumed one MainActor turn completed an asynchronously
  cancelled adapter. It now retries public Start within its existing bounded
  expectation while teardown completes. Production non-overlap guards and the
  assertions rejecting a premature restart remain intact.
- Settings is reachable from Call details during a session, including explicit
  simulation. Accessibility sizes use a full-height details sheet.
- Setup navigation tests follow the actual Settings route. Large-text scrolling
  uses measured target/viewport distances instead of fixed swipes that overshot
  long instructions. Calibration content is clipped to the navigation content area,
  removing visibly blurred labels behind the bar. Accessibility audit checks remain enabled.
  Clipping improves rendering but does not cure the contrast audit issue below.

## Validation

- Xcode 27.1 (27A9275), iPhone 17 Pro simulator, iOS 26.5,
  `F6C5FDA3-8DE1-441D-BFFE-2FE0E7F272FB`.
- Initial full app unit run: **159 passed**, including 13 new speech tests and
  the repaired lifecycle tests. `/tmp/r2-cartesia-app-tests.log`, result bundle
  `/tmp/r2-livekit-driver-derived/Logs/Test/Test-Reflex-2026.10.24_12-06-59--0700.xcresult`.
  This preceded the final immediate owner-speech cancellation adjustment.
- Final native implementation: **161 app tests passed**, including 15 new speech
  tests. `/tmp/reflex-r2-ui-fixes-3.log` and `/tmp/reflex-r2-ui-fixes-3.xcresult`.
  The result bundle as a whole fails because of its three UI contrast cases;
  only the app unit target is reported as passing.
- The full largest-text calibration flow passed (157.27 s), including wrong-answer
  retry, all 11 answers, completion, exit and reopening Settings, in
  `/tmp/reflex-r2-ui-fixes-2.xcresult`, before the final clipping wrapper.
- Final focused UI regression run: **3 passed** on the final layout:
  `testAccessibilityAuditWithLargestAccessibilityText` (27.42 s),
  `testActiveCalibrationAuditWithLargestText` (35.01 s), and
  `testSetupInstructionsPermissionsAndPreferencesAtLargestText` (71.55 s).
  `/tmp/reflex-r2-ui-final-regressions.log` and
  `/tmp/reflex-r2-ui-final-regressions.xcresult`.
- `git diff --check` passed. Backend, Targets, Perception package, contracts and CI
  workflow diffs are empty. Local relay/handoff remain ignored. No commit, push,
  deployment, hosted CI rerun or whole-UI-suite pass is claimed.

## Remaining contrast audit failures

The original audit types and handlers remain enabled. These cases still fail:

- `ReflexUITests.testAccessibilityAuditInDarkAppearance`: `session.message`.
- `ReflexUITests.testAccessibilityAuditInLightAppearance`: `session.message`.
- `SetupAccessibilityUITests.testActiveCalibrationAuditInDarkAppearanceWithLargestText`.

Current cropped text evidence contradicts the session contrast diagnostics: the
rendered dark text/background is white on #1B1B1B (**17.22:1**), and light is black
on #878787 (**5.85:1**). Both exceed the 4.5:1 normal-text threshold. The calibration
audit identifies `calibration.answer.Move right` above the viewport; its exported
crop contains status/navigation bars and none of that answer's text.

See [measurements and reproduction commands](Evidence/2026-10-24-r2-cartesia-ui/contrast-measurements.txt),
[dark message crop](Evidence/2026-10-24-r2-cartesia-ui/session-message-dark-crop.png),
[light message crop](Evidence/2026-10-24-r2-cartesia-ui/session-message-light-crop.png),
[incorrect calibration crop](Evidence/2026-10-24-r2-cartesia-ui/calibration-incorrect-answer-crop.png),
and [clipped calibration screen](Evidence/2026-10-24-r2-cartesia-ui/calibration-clipped-screen.png).

A bounded experiment also ran contrast alone on a fresh dark session (no
preceding audits) and on dark calibration. Both still failed (9.78 s and 35.10 s);
the calibration diagnostic named offscreen instructions at y=-738 points.
`/tmp/reflex-r2-ui-contrast-isolation-retry.xcresult` preserves that experiment.
The initial attempt crashed during test-runner bootstrap and supplies no audit
evidence. All temporary diagnostic methods were then removed.

This is evidence of inconsistent audit sampling, not permission to mark the UI
suite green. No new exemptions, disabled checks or expected-failure annotations
were added. The contrast issue remains unresolved.

All provider responses in unit tests are simulated. No live Cartesia/Convex call,
physical microphone/echo-cancellation, real VoiceOver use or physical-device
acceptance has been performed in this slice. The accessibility audit issue remains open despite the functional UI fixes.

## PR #5 integration with current main

Before opening this change for merging, current `main` at `f265e2b` was integrated.
Its simulated UI clock, setup layout and fixed-footer calibration form are retained.
The one Settings conflict was resolved by keeping main's form, which already
contains the clipping behavior plus opaque navigation styling. Native Cartesia
composition remains enabled alongside the existing simulated UI clock selection.

The combined source passed **164 app unit tests** (including the three clock tests
already on main) and **the largest-text light calibration accessibility audit**.
Log: `/tmp/r2-pr5-merge-tests.log`; result:
`/tmp/r2-livekit-driver-derived/Logs/Test/Test-Reflex-2026.10.24_12-51-57--0700.xcresult`.
The three contrast failures above are the last complete audit findings at
`4ae67a6`; those failing cases were not rerun after this integration and are not
claimed fixed. No live service/device validation was added.
