# CI, accessibility and lifecycle validation

This checkpoint covers independent R2 work while R1/R3 implementations and H1's confirmed contract are unavailable. It preserves the native iPhone target and does not establish live integration or hardware acceptance.

## Environment and results

- Xcode 26.6 (17F113), Apple Swift 6.3.3, Swift 5 language mode; minimum iOS 17.
- iPhone 17 Pro simulator, iOS 26.5 (23F77).
- **129 tests passed:** Tasks 40, HapticLink 40, app unit 38 and UI 11. The final `.xcresult` reports 49 app/UI passes, zero failures and zero skips; both package logs report 40 passes. The stable CI script exited 0 and the unsigned Release iPhone build passed.
- Project generation consistency, workflow YAML parsing, Python syntax and shell syntax checks passed. `actionlint` was unavailable; its installation attempt failed, so no actionlint result is claimed.
- GitHub-hosted execution is unrun. The workflow must be published and complete on its hosted runner before reporting hosted CI success.

The final local command, from the repository root:

```sh
IOS_SIMULATOR_UDID=F6C5FDA3-8DE1-441D-BFFE-2FE0E7F272FB \
CI_ARTIFACT_DIR=/tmp/reflex-ci-verified \
bash ios/Scripts/ci_xcodebuild.sh
```

Local evidence is in `/tmp/reflex-ci-verified/`: separate package logs, simulator test log, `Reflex-tests.xcresult`, and unsigned Release build log. These temporary artifacts are not committed; this report preserves their conclusions. Use a fresh artifact directory for subsequent runs. The script refuses to overwrite an existing result bundle and propagates command/log-capture failures.

## Lifecycle coverage and fixes

The six new stress tests exercise adapters that deliberately ignore cancellation:

- 12,000 fixture frames over 20 **virtual** minutes leave only the newest pending frame behind one occupied send slot.
- 40 Stop/Resume cycles and 25 additional Start/End sessions do not accumulate concurrent frame sends.
- Cancelled target requests remain counted against the eight-operation limit until their providers return; capacity recovers afterward.
- Late calibration answers are rejected after reconnect, and calibration must be started again.
- End permits the coordinator to deallocate while a frame send or conversation startup is still held.

The coordinator now retains cancelled work in its capacity accounting, keys task work by full request context, and avoids retaining itself across the tested stalled adapter calls. The harness can accept alternate R2-facing adapters when real implementations arrive. It does not certify compatibility with absent teammate code.

This virtual-clock test is not a 20-minute wall-clock endurance run, memory profile, battery measurement, thermal test or physical sensor/audio/BLE test.

## Permission and accessibility coverage

Five UI cases cover both permissions denied, camera-only denial, microphone-only denial, camera restriction and initial permission request. The app explains the affected resource, distinguishes restrictions from denials, blocks assistance until ready, and offers the appropriate request or Settings action. Recovery in these tests is explicitly simulated; actual iOS permission prompts and Settings transitions remain unrun.

Three audit cases exercise Home and the scrolled simulated session in light, dark and largest accessibility text. They request contrast, element detection, hit region, description, clipping and trait checks. They also assert that Start/Pause/End are reachable and the simulated Send control fits above the fixed footer. Screenshots are attached to the result bundle and were exported locally to `/tmp/reflex-verified-attachments/`. The initial session screenshot is retained for inspection; the automated session audit runs after scrolling the input fully into view. These audits do not cover every settings, task or calibration state.

UI fixes include stronger primary-button contrast, shorter Home text that fits large type, explicit simulation labeling, readable fixture input text, and an opaque fixed controls footer physically outside the scrolling region. Debug appearance injection ensures the dark case actually renders dark; screenshots were inspected to verify this.

### One narrowly handled audit limitation

Xcode 26.6 reported contrast failure for `fixture.instructions` while its reported rectangle was entirely outside the application bounds (y=1482…2143 versus app y=0…874). The exported element screenshot was uniformly black and contained no text pixels. The full screen showed black instruction text on white; light/dark audits exercise those instructions onscreen. Evidence from the diagnostic run remains locally at `/tmp/reflex-accessibility-visible.xcresult` and `/tmp/reflex-visible-attachments/`.

The test handler records each issue and handles only **contrast + exact `fixture.instructions` identifier + nonempty rectangle wholly outside app bounds**. Visible or partly visible instances, other identifiers, and all other audit types still fail. This is an instrumentation exception, not an assertion that every offscreen element passed. Reevaluate it when updating Xcode. Apple describes reviewing and handling verified false positives in its [accessibility audit session](https://developer.apple.com/videos/play/wwdc2023/10035/?time=574); automated audits do not replace manual accessibility testing.

## Remaining acceptance

Debug permission scenarios and appearance overrides require both `--simulate` and `--uitesting`. The final Release binary was checked for `UITestPermissionClient`, `--ui-permission-scenario`, `--ui-appearance` and `setup.permission.simulatedRecovery`; all four DEBUG harness markers were absent.

Real iPhone VoiceOver, Increase Contrast, Reduce Motion, actual permission prompts, signed installation, live offline spoken Stop and hardware endurance are unrun. Follow [DEVICE_ACCESSIBILITY.md](DEVICE_ACCESSIBILITY.md), then the team T01–T14 and R2-A–F acceptance requirements once actual R1/R3/H1 integrations are available. No physical-device pass is claimed.
