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

The project and scheme were accepted by `xcodebuild -list` with both local packages resolved. The final validation passed 36 Tasks tests, 34 HapticLink tests, 25 app unit tests, and 3 UI tests. Debug simulator and unsigned Release device builds passed. See [the validation record](Validation/2026-10-23.md) and [the repository ownership guide](../docs/REPOSITORY_STRUCTURE.md). Physical camera, microphone, Bluetooth, wristband timing, and H1 failsafe checks have not been run as part of this setup guide; simulator success does not establish hardware behavior.

## Connect live adapters

`AppDependencies` accepts an optional `LiveIntegrationBundle`. Supply the confirmed adapters and configuration there when integrating R1, R3, and H1:

- `PerceptionGuidanceClient` for R1-owned camera/session frames, tracking, guidance, and scheduled haptic releases; `FrameDeliveryPolicy` from R3 for its supported image size/rate limits.
- `SpeechSchedulerClient` for R1-owned app speech and priority-zero local Stop.
- `ConversationClient` and `TargetEvidenceClient` for R3 conversation, intent/target proposals, evidence, and verification.
- `HapticLinkConfiguration` and `BandTransport` for H1's confirmed BLE contract.
- `generationSeed` and `configureBandSynchronization` for H1's actual generation handshake and synchronization proof.

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
