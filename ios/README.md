# Reflex for iPhone

Reflex is a native SwiftUI iPhone app with a minimum deployment target of iOS 17. The project uses Swift 5 language mode and was inspected with Xcode 26.6 (17F113) and Apple Swift 6.3.3. VS Code works for editing; Xcode is required to build, run, sign, and test the iOS app.

## Open and run

1. On a fresh clone, prepare the native object model using the commands below, then open `ios/Reflex.xcodeproj` in Xcode. No external project generator needs to be installed.
2. Select the shared `Reflex` scheme and an iPhone simulator or connected iPhone.
3. For a physical device, open the Reflex target's **Signing & Capabilities**, enable automatic signing, and select your development team. The project does not include a team ID.
4. Build and run. Normal assistance now composes the native camera, hand/depth guidance, audio and local object-recognition adapters. The separate Gemini snapshot reader and friend-video demo require service configuration. None of these flows silently substitutes simulation.

The sample bundle identifier is `com.example.reflex`. To use a different one, copy `ios/Config/Local.example.xcconfig` to `ios/Config/Local.xcconfig` and change `REFLEX_BUNDLE_ID`. The local file is ignored by git. The project has camera, microphone, speech-recognition and Bluetooth permission descriptions; no provider credentials or real H1 Bluetooth UUIDs are bundled.

To refresh Xcode target membership after adding or removing Swift files under `ios/App`, `ios/Tests/ReflexAppTests`, or `ios/UITests`, run:

```sh
python3 ios/Scripts/generate_project.py
```

The generated `.xcodeproj` is included with the source and opens without running that script on a fresh clone. The script uses only Python's standard library.

## Prepare the native object model

The native object path uses the same hash-pinned YOLO11s weights as the R1 prototype, exported to Core ML with non-maximum suppression and the original COCO labels. The compiled binary is ignored by Git. A fresh clone must prepare it before running local object recognition or the bundled-model test.

From the repository root on macOS with Xcode selected and Python 3.12 installed:

```sh
mkdir -p .runtime
python3.12 -m venv .runtime/coreml-export
.runtime/coreml-export/bin/python -m pip install \
  numpy==1.26.4 ultralytics==8.4.183 torch==2.14.1 coremltools==9.0
.runtime/coreml-export/bin/python ios/Scripts/prepare_native_model.py
python3 ios/Scripts/generate_project.py
```

The preparation script downloads the original model named in the teammate's `models.lock.json`, verifies its SHA-256, exports it and invokes `xcrun coremlcompiler`. It writes `Resources/Models/yolo11s.mlmodelc` and records provenance in `Resources/Models/model-manifest.json`. Downloaded weights and exporter artifacts stay under ignored `.runtime/`. No Gemini key or account is needed for this local path.

Read [the model notice](Resources/Models/NOTICE.txt) and [AGPL-3.0 license](Resources/Models/ULTRALYTICS-AGPL-3.0.txt). The repository's MIT license does not relicense the Ultralytics model. Conversion and a synthetic model-loading test do not establish real-device recognition accuracy or latency.

## Conversational speech

Normal native assistance uses Cartesia for priority-4 speech through the authenticated
Convex `speech:synthesize` action when online configuration and an existing sign-in
are available. Sign in through the image reader or friend tools first. The phone
contains no Cartesia key; R3 owns the backend action and its server configuration.
This demo path does not implement the planned streaming STT/Claude/LiveKit worker.

The shared native scheduler retains all playback and priority authority. Stop,
hazards, lost-target and movement cues stay local. Missing configuration/sign-in,
provider errors, simulated responses, invalid audio, and the five-second total
deadline fall back to the original on-device utterance. Text beyond the backend's
300 UTF-16-unit limit stays local without truncation. Stop, context changes, mute,
interruptions, new owner speech and replacement utterances cancel pending synthesis;
late replies cannot restart it. Decode/playback failure also uses local speech.
Simulator tests use simulated HTTP replies and do not establish live provider,
microphone, echo-cancellation, or physical-device acceptance.

## Explicit simulation

Simulation is off by default. Turn it on from **Settings → Use simulated assistance**, or launch the app with `--simulate`. The home and active-session views show a persistent **SIMULATED** label. Fixtures use sample scene/task events and typed requests; they do not connect to the camera, microphone, or wristbands. Inside a simulated session, enter a request in the simulated voice input field and submit it to exercise a fixture flow. This typed path is for fixture development. Normal assistance uses the native adapters described below and requires their actual camera, microphone, speech-recognition and depth readiness.

For UI automation, launch with both `--simulate` and `--uitesting`. The latter uses and clears a separate `Reflex.UITests` preferences domain and disables fixture speech output so the simulator does not speak during tests. UI tests set these arguments themselves.

Debug UI automation can also add `--ui-permission-scenario` followed by `denied`, `camera-denied`, `microphone-denied`, `restricted`, or `requestable`. These scenarios enforce the app's permission gate using simulated authorization, without opening the camera/microphone or showing system prompts. Recovery is an explicitly labeled simulated Settings action. These injections require both simulation/test flags and are excluded from Release builds.

## Tests and build checks

Run each local package suite from the repository root:

```sh
swift test --package-path ios/Packages/Guidance
swift test --package-path ios/Packages/Targets
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

The following numbers are the earlier two-package R2 baseline, not validation of the new native R1/R3 integration. At that checkpoint, the project and scheme were accepted by `xcodebuild -list`. Its focused validation covered **169 unique cases: 164 passed and five strict contrast audits failed**. All 138 logic tests and 23 functional UI tests passed; three of eight audit tests passed. The unsigned Release iPhone build passed. The inherited session contrast report is a confirmed false positive with a complete black/white 21:1 crop; no reliable audit-harness fix was found. Four setup/calibration contrast reports also remain unsuppressed. See the [accessibility and simulated-flow validation record](Validation/2026-10-24-accessibility-flows.md), [standalone reproduction](AccessibilityRepro/README.md), and [setup/calibration evidence](Validation/Evidence/2026-10-24-setup-accessibility/README.md). The existing narrow offscreen audit exception was not broadened. Previous safety validation is recorded in [Bluetooth safety](Validation/2026-10-24-bluetooth-safety.md) and [lifecycle safety](Validation/2026-10-24-lifecycle-safety.md).

The earlier 139-test passing checkpoint remains historical: see [optional-band validation](Validation/2026-10-24-speech-only.md), [CI/accessibility validation](Validation/CI_ACCESSIBILITY.md), [the earlier continuation](Validation/2026-10-23-r2-continuation.md), and [initial validation](Validation/2026-10-23.md). Physical camera, microphone, Bluetooth, wristband timing, and H1 failsafe checks remain unrun; simulator results do not establish hardware behavior.

## Continuous integration

[The iOS workflow](../.github/workflows/ios-ci.yml) invokes the repository validation script for the generated Xcode project, local Swift packages, simulator app/UI tests, and the unsigned Release iPhone build on pushes and pull requests. It uses read-only repository access, cancels superseded runs, and uploads logs plus the `.xcresult` even if a test fails. The first [hosted run](https://github.com/yukevindai/VisionOS/actions/runs/40076838872) completed successfully for published baseline `a1a6637`. It does not cover the newer local continuation or resolve the local largest-text audit failure.

The workflow selects Xcode 26.6 on `macos-26` and discovers an installed iPhone simulator. This matches the [official runner image inventory](https://github.com/actions/runner-images/blob/main/images/macos/macos-26-arm64-Readme.md). Official [checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1) and [upload-artifact v7.0.2](https://github.com/actions/upload-artifact/releases/tag/v7.0.2) actions are pinned to commit SHAs. If that runner image changes its installed Xcode versions, update the explicit selection after checking the inventory.

The current local script runs Guidance, Targets, Tasks and HapticLink. Prepare the ignored native model before running it; a clean CI checkout needs the same preparation or a verified model cache. Historical hosted runs above predate this requirement.

Run the test/build script locally after model preparation:

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

## Native assistance and remaining integration boundaries

Normal assistance now uses `NativePerceptionGuidanceClient`, `NativeAudioCoordinator`, and `OnDeviceTargetEvidenceClient` through the existing `LiveIntegrationBundle`. This is a native iPhone path; the laptop Python application is not launched or embedded.

- One rear ARKit session supplies original frames, registered scene depth/confidence and tracking state. Apple Vision observes the selected hand and tracks a target from its exact source frame. Orientation or geometry changes invalidate the old target.
- The Foundation-only `Guidance` package ports the teammate's measured guidance controller and geometry functions. Its parity fixtures execute the unchanged Python reference and label their inputs **SIMULATED**. Fresh, measured target/fingertip geometry is required for a movement cue; unknown depth, loss, expiry or cancellation stops guidance. Alignment is not proof of a grasp.
- `NativeAudioCoordinator` owns the native microphone/speech path, local commands and scheduled app speech. Readiness requires the on-device recognition resources and offline Stop path; an unavailable resource produces an explicit failure. Local speech and target recognition do not require a cloud account.
- Local YOLO11s object recognition supports cup/can (the teammate's COCO `cup` class), bottle and bowl. Multiple matching objects, uncertain detections and unsupported attributes are rejected. Results retain the actual source-frame ID and require current native reacquisition. The adapter does not infer approach, free space, contents, successful contact or a completed grasp.

Physical guidance remains limited to the implemented hand-reaching path with usable depth. Button/kiosk interpretation, wall-menu conversation, door passage, navigation, Claude/Cartesia orchestration and the complete friend audio/click workflow must not be inferred from the local object demo. The separate Gemini snapshot reader below provides semantic reading, not permission to guide toward a control with unknown safety or confidence.

`LiveIntegrationBundle` remains injectable for tests and future agreed adapters. The main boundaries are `PerceptionGuidanceClient`, `SpeechSchedulerClient`, `ConversationClient`, `TargetEvidenceClient`, and `FrameTargetEvidenceClient` for correlated image delivery. Preserve R1 ownership of capture and output scheduling. An optional `LiveBandIntegrationBundle` supplies `HapticLinkConfiguration`, `BandTransport`, `generationSeed` and `configureBandSynchronization` only when H1 provides the actual BLE contract and handshake.

Bands are currently omitted, so no BLE controller is constructed and calibration remains unavailable. The user has confirmed Bluetooth as the planned hardware connection, but service UUIDs, firmware protocol, pattern envelopes, identity verification and generation synchronization still require H1's values. Follow the [Bluetooth integration checklist](Validation/BLUETOOTH_INTEGRATION.md); do not infer compatibility from a generic Bluetooth connection.

The package boundaries are:

```text
Reflex app
├── Guidance    measured geometry and hand-guidance policy (Foundation)
├── Targets     teammate ScreenModel and deterministic target selection
├── Tasks       deterministic task state/effect engine
└── HapticLink  wristband protocol, transport, and safety gates
```

`Tasks` consumes the teammate `Targets.BoundingBox` rather than maintaining a second normalized-box implementation. Its `TaskScreen` is the task-engine projection, distinct from the provider's `Targets.ScreenModel`. `HapticLink` remains the only component that writes wristband commands, and ordinary haptic releases require R1 scheduler approval. See the package READMEs for their specific contracts.

See [the native R1/R3 validation record](Validation/2026-10-24-native-r1-r3.md) for actual current results and remaining checks. No live-device pass is established here. The connected iPhone is on iOS 27.2 beta; the installed Xcode 26.6 developer disk image lacks the required device variant, blocking installation/debug validation in this environment. This tooling failure is separate from simulation and compilation. Camera orientation, depth accuracy, speech interruption, offline Stop, actual object detection and physical reaching still need observed device tests under [native adapter acceptance](Validation/ADAPTER_ACCEPTANCE.md).

## Role-based interface

Every app launch starts with **Blind / Not blind**. Blind opens a large **Call** action, which starts the existing native voice/object-assistance coordinator after any required first-use setup. There is no task grid or friend picker in that path. The live camera uses aspect-fit and semi-transparent native material islands; Stop and End call remain reachable, including with large text. Reduce Transparency switches the islands to opaque surfaces. Settings and separate camera tools remain outside the call.

Not blind opens authenticated shared sessions, invitation redemption, and a watch-only rear-camera viewer. It shows only server-authorized joinable sessions. It never opens this phone's camera or microphone. Missing cloud setup is explicit; hand landmarks, object labels, task state and distances remain unavailable until a real shared telemetry feed exists. Full call audio and automatic friend sharing from the normal Call are not wired yet. The existing owner-video tool provides the separate consented sharing path.

## Gemini snapshot reader

The separate **Blind → More tools → Read a camera image** flow captures an upright snapshot and sends it through the authenticated Convex `vision:readScreen` action to the private Python Gemini service. It reuses the teammate's image preparation, Gemini parser and `Targets` selector. The live HTTP path returns one correlated `ScreenModel`; the portable Python selector remains for CLI/evaluation. The old unauthenticated `/v1/select` HTTP route was removed.

This flow requires real Auth0/Convex configuration plus the private Gemini service URL/token and Gemini key/model, documented in [backend setup](../backend/README.md). These services are not configured or live-tested in this checkout. Missing configuration fails visibly. Numeric confidence and control safety are not invented when Gemini omits them; a readable target is not automatically eligible for physical guidance. The snapshot reader is separate from local object reaching and from the friend-video flow.

## Repository layout

Live friend assistance is being added under the [combined R2/R3 specification](../docs/R2_R3_LIVE_FRIEND_ASSISTANCE_SPEC.md). The [policy foundation](Validation/2026-10-24-friend-policy-foundation.md) and [native integration checkpoint](Validation/2026-10-24-friend-native-integration.md) document the deterministic policies and optional serialized coordinator seam now wired through the native session/task engine. The [Convex backend foundation](Validation/2026-10-24-friend-convex-foundation.md) implements auth configuration, directional trust, session presence, reservations and authorization leases. The [LiveKit backend checkpoint](Validation/2026-10-24-livekit-media-integration.md) adds server-side tokens, signed webhooks, reconciliation and cleanup with 62 passing local tests; [backend setup](../backend/README.md) records the required configuration and remaining native publisher/service work. The [native media checkpoint](Validation/2026-10-24-native-media-transport.md) records 96 app and 64 backend tests. The optional native media integration now includes an authenticated Convex HTTP adapter, a pinned LiveKit Swift video driver, and local publisher gates. The separate video demo now supplies Auth0 identity and a user-authorized single-camera adapter, with a browser helper in `apps/friend-web`. The native local R1 guidance/audio path is now implemented separately from this historical media checkpoint. Deployed auth/media services, friend remote-audio integration and the voice worker remain unfinished; kiosk screen-control selections remain rejected until a validated screen model can be bound. See the [R1 integration request](Validation/R1_FRIEND_INTEGRATION_REQUEST.md) for the required native frame/audio seams.

The team specifications live in `docs/` at the repository root. See the [R2 implementation specification](../docs/R2_IOS_IMPLEMENTATION_SPEC.md) and [team build specification](../docs/Cal_Hacks_13_Build_Spec_v3.md). The relay and handoff documents are local-only files excluded from Git; they are not prerequisites for building a fresh clone.


Native video transport is injected through `LiveIntegrationBundle.media`. `NativeMediaCoordinator` connects without publishing, waits for verified owner presence, then admits original R1 frames under a current room generation and monotonic authority deadline. Sharing also requires an exact owner renewal and verified helper identity/SID. End, background, mute, account changes, room retirement and transport invalidation close admission; an explicit new Start is required for recovery. The capture protocol supplies frames already captured by R1; it must not open another camera or audio engine.

LiveKit Swift is pinned to 2.18.0 in the generated Xcode project. Per-sample and SDK processing-queue gates reject stale frames and suspension revisions. Only one processing-queue frame may be outstanding; an SDK drop before the processor intentionally stalls input until a new connection. Frames already handed to WebRTC and automatic SDK reconnect behavior still need physical-device fault testing. This is video transport infrastructure, not a completed human audio/video call feature.


The [trusted-friend backend hardening checkpoint](Validation/2026-10-24-friend-backend-hardening.md) adds durable invitation attempt limits and account-scoped paginated friendship/approval lists, with 75 passing backend tests. It changes no native or teammate vision code. Future friend-management clients must use the updated pagination contract in the [backend README](../backend/README.md).


The [session discovery and media-limit checkpoint](Validation/2026-10-24-discovery-media-limits.md) adds paginated joinable-session discovery and durable caller-scoped limits for LiveKit room/token requests, with 90 passing backend tests. Access-closing operations remain available after exhaustion. No native or teammate vision implementation changed; real-service validation still needs authorized Convex/Auth0/LiveKit configuration.

## Real iPhone video demo

Open **Blind → More tools → Share live video with a friend** with simulation off. This separate full-screen flow provides Auth0 sign-in, invitation codes, explicit friend-request approval, per-session sharing consent and End. A user-authorized rear AR camera adapter feeds the existing LiveKit video gates; no second camera runs alongside normal assistance. Viewers are the native **Not blind → Shared sessions** route and `apps/friend-web`. Neither path changes teammate ScreenModel, Target Selector or Gemini code.

Use [the setup guide](../docs/LIVE_VIDEO_DEMO_SETUP.md) for the missing Convex/Auth0/LiveKit projects, callback/webhook URLs, ignored local config and physical iPhone instructions. No Gemini/Claude/Cartesia key is needed for this demo. Camera, microphone and provider secrets are not simulated into a live success.

The [video-demo validation record](Validation/2026-10-24-live-video-demo.md) distinguishes the passing local builds/tests from the still-unrun real iPhone-to-helper call. The ordinary assistance frame tap is owned by the new native R1 adapter. This separate video-demo capture source remains exclusive and must never run alongside it.
