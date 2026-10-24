# R2 optional wristbands and dispatch freshness

October 24, 2026. Native iPhone R2 continuation above `d8fbb8b1845977126b52818d44c4c2134449f951`. Changes remain local and uncommitted, alongside the earlier CI/accessibility work and prototype relocation. No teammate module or shared contract was changed in this continuation.

## Implemented

- `LiveIntegrationBundle` accepts optional `bands: LiveBandIntegrationBundle`. The band bundle groups H1 configuration, transport, generation seed, and synchronization installation. Without it, no BLE controller is constructed; band capabilities remain unavailable and calibration cannot start. R1/R3 adapters and their frame policy can be supplied independently. Simulation neither installs H1 synchronization nor uses its generation seed.
- Production composition accepts clock, permissions, and defaults injection for deterministic tests without system prompts or shared preference writes. Normal app defaults remain unchanged.
- Conversation frame forwarding checks original capture age, expiry, phase, and context both after buffering and immediately before the asynchronous send. A provider's longer expiry cannot extend R2's 300 ms capture-age limit. Dropped sends release the occupied slot; cancelled sends retain it until the provider returns.
- Task guidance dispatch rejects a clock older than the reducer's last accepted time. Regressing the clock cannot make previously stale physical evidence fresh.
- [Native adapter acceptance](ADAPTER_ACCEPTANCE.md) records the remaining capture-clock, geometry, depth provenance, range, completion, and local Stop cases. These are R2 projections and acceptance requirements, not assertions about unprovided teammate APIs.

## Validation

Environment: Xcode 26.6 (17F113), Swift 6.3.3 in Swift 5 language mode; iPhone 17 Pro simulator, iOS 26.5, UDID `F6C5FDA3-8DE1-441D-BFFE-2FE0E7F272FB`. Minimum app target remains iOS 17.

- Tasks: **41 passed**. The dispatch-clock regression failed before the guard was added, then passed with the full package suite.
- HapticLink: **40 passed**; package source was unchanged.
- App unit: **47 passed**: 27 session, 8 lifecycle, 7 composition, 5 band-service tests. Composition uses synthetic adapters, including a synthetic offline-Stop capability; it does not test speech recognition or actual hardware.
- UI: **11 passed**, including permission and accessibility cases. The existing narrow exception for Xcode's wholly offscreen `fixture.instructions` contrast sampling issue was exercised again; see [CI_ACCESSIBILITY.md](CI_ACCESSIBILITY.md). These are not wholly unfiltered audits.
- **139 total tests passed**. The `.xcresult` independently reports 58 app/UI passes, zero failures, and zero skips. Unsigned Release generic iPhone build passed; this is compilation, not a signed install or device run.
- Generated project is byte-identical on regeneration. Source hashes remained unchanged through final verification; whitespace and public Markdown link checks passed.

The two new frame regressions failed against the previous implementation, demonstrating that an aged queued frame and a frame expiring before task execution reached the provider. Both pass after the fix and also verify that fresh input can subsequently use the slot. An initial composition test build found a missing `@MainActor` annotation on the nested test harness; it was corrected before the passing app test run.

Temporary evidence:

```text
/tmp/reflex-r2-frame-regression-before.log
/tmp/reflex-r2-speech-only-app-tests.log       # initial harness compile failure
/tmp/reflex-r2-speech-only-tasks.log
/tmp/reflex-r2-speech-only-haptic.log
/tmp/reflex-r2-speech-only-tests.log
/tmp/reflex-r2-speech-only-tests.xcresult
/tmp/reflex-r2-speech-only-release.log
/tmp/reflex-r2-speech-only-source.sha256
```

Commands from repository root:

```sh
swift test --package-path ios/Packages/Tasks
swift test --package-path ios/Packages/HapticLink
xcodebuild -project ios/Reflex.xcodeproj -scheme Reflex \
  -destination 'platform=iOS Simulator,id=F6C5FDA3-8DE1-441D-BFFE-2FE0E7F272FB' \
  -parallel-testing-enabled NO -resultBundlePath /tmp/reflex-r2-speech-only-tests.xcresult \
  CODE_SIGNING_ALLOWED=NO test
xcodebuild -project ios/Reflex.xcodeproj -scheme Reflex -configuration Release \
  -sdk iphoneos -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO build
```

Use a new result-bundle path for a later run; Xcode will not overwrite the existing bundle.

## Remaining acceptance

Native R1/R3 implementations and confirmed H1 configuration are absent. Default live launch still reports unavailable integrations. No camera, microphone, offline recognizer, wristband dispatch/onset, signed install, real VoiceOver/audio coexistence, physical accuracy, or real 20-minute endurance was validated. The earlier virtual-clock stress case remains a fixture test. Hosted GitHub CI has not run.

Map actual R1/R3 interfaces using the acceptance document, begin with supported speech-only operation and working local Stop, then supply confirmed H1 dependencies when ready. Complete the [device checklist](DEVICE_ACCESSIBILITY.md) and applicable T01–T14 / R2-A–F checks with actual adapters and hardware. Do not relabel simulator or synthetic-adapter results as device acceptance.
