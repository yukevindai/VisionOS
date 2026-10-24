# Reflex for iPhone

Reflex is a native SwiftUI iPhone app with a minimum deployment target of iOS 17. The project uses Swift 5 language mode and was inspected with Xcode 26.6 (17F113) and Apple Swift 6.3.3. VS Code works for editing; Xcode is required to build, run, sign, and test the iOS app.

## Open and run

1. Open `ios/Reflex.xcodeproj` directly in Xcode. No project generator needs to be installed.
2. Select the shared `Reflex` scheme and an iPhone simulator or connected iPhone.
3. For a physical device, open the Reflex target's **Signing & Capabilities**, enable automatic signing, and select your development team. The project does not include a team ID.
4. Build and run. Live integrations are not supplied in this checkout, so the normal app launch does not silently substitute simulation if an integration is unavailable.

The sample bundle identifier is `com.example.reflex`. To use a different one, copy `ios/Config/Local.example.xcconfig` to `ios/Config/Local.xcconfig` and change `REFLEX_BUNDLE_ID`. The local file is ignored by git. The project has camera, microphone, and Bluetooth permission descriptions; no provider credentials or real H1 Bluetooth UUIDs are bundled.

To refresh Xcode target membership after adding or removing Swift files under `ios/App`, `ios/Tests/ReflexAppTests`, or `ios/UITests`, run:

```sh
python3 ios/Scripts/generate_project.py
```

The generated `.xcodeproj` is included with the source and opens without running that script on a fresh clone. The script uses only Python's standard library.

## Explicit simulation

Simulation is off by default. Turn it on from **Settings → Use simulated assistance**, or launch the app with `--simulate`. The home and active-session views show a persistent **SIMULATED** label. Fixtures use sample scene/task events and typed requests; they do not connect to the camera, microphone, or wristbands. Inside a simulated session, enter a request in the simulated voice input field and submit it to exercise a fixture flow. This typed path is for fixture development; live assistance requires real speech and perception adapters through the composition seam below.

For UI automation, launch with both `--simulate` and `--uitesting`. The latter uses and clears a separate `Reflex.UITests` preferences domain and disables fixture speech output so the simulator does not speak during tests. UI tests set these arguments themselves.

Debug UI automation can also add `--ui-permission-scenario` followed by `denied`, `camera-denied`, `microphone-denied`, `restricted`, or `requestable`. These scenarios enforce the app's permission gate using simulated authorization, without opening the camera/microphone or showing system prompts. Recovery is an explicitly labeled simulated Settings action. These injections require both simulation/test flags and are excluded from Release builds.

## Tests and build checks

Run each local package suite from the repository root:

```sh
swift test --package-path ios/Packages/Tasks
swift test --package-path ios/Packages/HapticLink
```

The shared `Reflex` scheme includes the app unit-test target and the UI-test target. To run both against the iPhone 17 Pro simulator recorded for this checkout, with parallel test runners disabled:

```sh
xcodebuild \
  -project ios/Reflex.xcodeproj \
  -scheme Reflex \
  -destination 'platform=iOS Simulator,id=F6C5FDA3-8DE1-441D-BFFE-2FE0E7F272FB' \
  -parallel-testing-enabled NO \
  test
```

If that simulator is not installed, use `xcrun simctl list devices available` and replace the ID with an available iPhone simulator. A generic unsigned simulator build is:

```sh
xcodebuild \
  -project ios/Reflex.xcodeproj \
  -scheme Reflex \
  -configuration Debug \
  -sdk iphonesimulator \
  -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO \
  build
```

The project and scheme were accepted by `xcodebuild -list` with both local packages resolved. The latest continuation ran **148 tests: 147 passed and 1 failed** (44 Tasks, 44 HapticLink, 49 app unit passes; 10 UI passes and one largest-text contrast audit failure). The unsigned Release iPhone build passed. The contrast failure remains unresolved; safety fixes, regression evidence and reverted layout probes are recorded in [the Bluetooth safety validation record](Validation/2026-10-24-bluetooth-safety.md), following [the lifecycle safety checkpoint](Validation/2026-10-24-lifecycle-safety.md). The existing narrow offscreen audit exception was not broadened.

The earlier 139-test passing checkpoint remains historical: see [optional-band validation](Validation/2026-10-24-speech-only.md), [CI/accessibility validation](Validation/CI_ACCESSIBILITY.md), [the earlier continuation](Validation/2026-10-23-r2-continuation.md), and [initial validation](Validation/2026-10-23.md). Physical camera, microphone, Bluetooth, wristband timing, and H1 failsafe checks remain unrun; simulator results do not establish hardware behavior.

## Continuous integration

[The iOS workflow](../.github/workflows/ios-ci.yml) checks the generated Xcode project, both Swift packages, simulator app/UI tests, and the unsigned Release iPhone build on pushes and pull requests. It uses read-only repository access, cancels superseded runs, and uploads logs plus the `.xcresult` even if a test fails. The first [hosted run](https://github.com/yukevindai/VisionOS/actions/runs/40076838872) completed successfully for published baseline `a1a6637`. It does not cover the newer local continuation or resolve the local largest-text audit failure.

The workflow selects Xcode 26.6 on `macos-26` and discovers an installed iPhone simulator. This matches the [official runner image inventory](https://github.com/actions/runner-images/blob/main/images/macos/macos-26-arm64-Readme.md). Official [checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1) and [upload-artifact v7.0.2](https://github.com/actions/upload-artifact/releases/tag/v7.0.2) actions are pinned to commit SHAs. If that runner image changes its installed Xcode versions, update the explicit selection after checking the inventory.

Run the same test/build script locally:

```sh
python3 ios/Scripts/select_simulator.py --boot
# Copy the selected device's UDID from the output:
IOS_SIMULATOR_UDID='<selected-udid>' bash ios/Scripts/ci_xcodebuild.sh
```

The script creates a fresh temporary artifact directory unless `CI_ARTIFACT_DIR` is provided. It refuses to overwrite an existing result bundle. After adding/removing Swift files, regenerate with `python3 ios/Scripts/generate_project.py` and include the resulting project change; CI rejects missing target membership.

## Adapter and lifecycle verification

`Tests/ReflexAppTests/AdapterCompatibilityHarness.swift` provides test-only recording speech and controllable conversation/evidence clients. Tests deliberately hold callbacks after cancellation and check latest-frame delivery, capacity recovery, scoped calibration answers, and release of the session coordinator. The composition accepts alternate R2-facing adapters for future integration tests; it does not establish compatibility with unpushed teammate code.

The coordinator retains a single occupied frame slot until its adapter returns, even after Stop. Only the newest current frame waits behind it. Cancelled task requests remain counted against the eight-operation limit until they return, using the full request context to keep sessions separate. Startup and conversation recovery also share one occupied attempt slot until the adapter actually returns. A stalled cancelled attempt blocks a new Start with an explanation and leaves recovery paused; after it returns, the user can explicitly Start again. Providers should still implement cancellation and bound their internal queues; R2 cannot force a third-party operation to finish.

The virtual-clock stress case produces 12,000 fixture frames over 20 simulated minutes, alongside repeated Stop/Resume and Start/End cycles. It checks scheduling and work bounds, not wall-clock memory, battery, thermal behavior or physical endurance. Use the [iPhone accessibility checklist](Validation/DEVICE_ACCESSIBILITY.md) for the remaining manual checks.

## Connect live adapters

The latest teammate update adds a Python reaching research prototype. See the [R2 integration review](Validation/TEAM_SYNC_REVIEW.md) for reusable evidence, unsupported mappings, and the next R2 tasks. It does not yet provide native R1/R3 packages or confirmed H1 configuration.

`AppDependencies` accepts an optional `LiveIntegrationBundle`. Supply the confirmed R1/R3 adapters and frame policy there; wristbands are optional:

- `PerceptionGuidanceClient` for R1-owned camera/session frames, tracking, guidance, and scheduled haptic releases; `FrameDeliveryPolicy` from R3 for its supported image size/rate limits.
- `SpeechSchedulerClient` for R1-owned app speech and priority-zero local Stop.
- `ConversationClient` and `TargetEvidenceClient` for R3 conversation, intent/target proposals, evidence, and verification.
- Optional `LiveBandIntegrationBundle` in `bands`, containing `HapticLinkConfiguration`, `BandTransport`, `generationSeed`, and `configureBandSynchronization` for H1's confirmed BLE contract and actual generation handshake.

Omit `bands` until H1's dependencies are ready. The app then constructs no BLE controller, reports both wristbands unavailable, and allows conversation through the supplied R1/R3 adapters. Physical speech-only guidance still requires R1 to report both supported speech-only guidance and working offline spoken Stop. Band calibration remains unavailable. A generation starting at zero in this composition is an app task counter only; it is never sent to firmware as a guessed synchronization value.

The user has confirmed the planned hardware connection will use Bluetooth; BLE/GATT compatibility and firmware details are still pending. Follow the [Bluetooth integration checklist](Validation/BLUETOOTH_INTEGRATION.md) when H1 supplies its confirmed contract. The existing CoreBluetooth implementation does not establish compatibility with an unspecified board.

See [native adapter acceptance](Validation/ADAPTER_ACCEPTANCE.md) for clock, geometry, depth provenance, completion, and offline Stop cases to verify when the real adapters arrive. Existing R2 tests use synthetic adapters and do not establish native compatibility.

The current unavailable adapters fail clearly; they do not switch the app into simulation. H1 UUIDs, protocol version, pattern envelopes, identity verification, and generation reset/handshake policy are still external configuration and must come from H1. Do not invent values or treat a Bluetooth connection as proof that generations are synchronized. Keep provider endpoints and credentials in R3's approved integration/backend; do not put secrets in this project or local xcconfig. Adapter names are R2 projections and need mapping to the teammates' real APIs before calling integration complete.

The package boundaries are:

```text
Reflex app
├── Tasks       deterministic task state/effect engine (Foundation)
└── HapticLink  wristband protocol, transport, and safety gates
```

`Tasks` does not access SwiftUI, BLE, camera, speech, or providers. `HapticLink` is the only app component that writes wristband command bytes, and ordinary haptic releases still require R1's scheduler approval. See [Tasks/README.md](Packages/Tasks/README.md) and [HapticLink/README.md](Packages/HapticLink/README.md) for package contracts, validation, and unresolved hardware assumptions.

## Repository layout

The team specifications live in `docs/` at the repository root. See the [R2 implementation specification](../docs/R2_IOS_IMPLEMENTATION_SPEC.md) and [team build specification](../docs/Cal_Hacks_13_Build_Spec_v3.md). The relay and handoff documents are local-only files excluded from Git; they are not prerequisites for building a fresh clone.
