# Repository structure and ownership

The root README contains the canonical directory tree. This layout was recorded on October 23, 2026 and updated on October 24 for the preserved laptop research prototype and native R1/R3 integration. Both team specifications are stored in `docs/`; their original filenames are preserved.

| Path | Owner | Contents |
|---|---|---|
| `docs/` | Team | Build specifications and repository documentation |
| `ios/VisionOS.xcodeproj/` | R2 | iPhone targets, dependencies, build settings, shared scheme |
| `ios/App/` | R2 integration | SwiftUI shell, accessibility, session coordination and native adapters |
| `ios/App/Perception/` | R1 implementation through R2 integration | Single ARKit camera, Vision hand/target observations, registered depth and native guidance adapter |
| `ios/App/Audio/` | R1 implementation through R2 integration | Shared native audio, local speech commands and output scheduling |
| `ios/App/Friends/` | R2 client integration | Native authenticated session discovery and watch-only helper camera UI |
| `ios/Resources/Assets.xcassets/` | R2 | Native UI assets, including the approved Figma call glyph |
| `ios/App/Targets/` | R3 integration | Native Gemini object client, retained Core ML adapter and separate snapshot-reader UI |
| `ios/Resources/Models/` | R1/R3 integration | Model notice, provenance manifest and ignored generated YOLO11s Core ML binary |
| `ios/Packages/Perception/` | R1 | Preserved perception research root; the current native ARKit/Vision adapter lives in `App/Perception` |
| `ios/Packages/Perception/Prototypes/DeskGuide-Laptop/` | R1 | Standalone Python perception/guidance research harness, evaluation tools and original validation records |
| `ios/Packages/Guidance/` | R1 | Foundation-only measured geometry and hand-guidance policy port; Python parity fixtures |
| `ios/Packages/Tasks/` | R2 | Task state machines and evidence validation |
| `ios/Packages/HapticLink/` | R2 | CoreBluetooth transport and wristband command safety |
| `ios/Packages/Targets/` | R3 | Target interpretation and model adapters |
| `ios/Tests/` | Target owner | App and integration tests; package tests also live in each package |
| `ios/UITests/` | R2 | App UI and accessibility automation |
| `backend/convex/` | R3 | Auth/trust/media state and authenticated vision proxy; server-side configuration |
| `backend/r3_vision/` | R3 | Teammate Gemini parser/image preparation, private vision HTTP service and CLI |
| `backend/eval/` | R3 | Offline and real-photo evaluation tooling; simulation labels remain explicit |
| `apps/demo-dashboard/` | R2/R3 demo integration | Local audience dashboard for the Python R3 live-tracking pipeline |
| `apps/friend-web/` | R3 client integration | Authenticated watch-only browser helper |
| `firmware/` | H1 | Wristband firmware and independent failsafe |
| `hardware/` | H1 | Electronics, actuator and enclosure information |
| `contracts/` | Agreed owners | Cross-team schemas and confirmed interfaces; H1 owns BLE/pattern definitions |
| `fixtures/` | Team | Shared test inputs with clear provenance and simulation labels |
| `.github/workflows/` | Team | Automated repository checks |

Supporting R2 paths remain under `ios/`: `Config/` contains build settings, `Scripts/` contains the reproducible Xcode project generator, and `Validation/` contains recorded results. `LICENSE` and `AGENTS.md` remain at the repository root.

## Research prototype

The former root `DeskGuide-Laptop/` directory now lives intact at `ios/Packages/Perception/Prototypes/DeskGuide-Laptop/`. Run its commands from that directory. Its fixtures, model manifests, dependency lists and licenses remain beside the code so its relative paths and provenance are preserved. Historical reports retain their original paths and environment descriptions.

The Python prototype remains intact and is not an iOS runtime dependency. The native `Guidance` package ports its measured guidance/geometry policy with source-linked parity fixtures; `App/Perception` supplies ARKit/Vision observations and `App/Audio` supplies native speech/output scheduling. These native adapters preserve R1's capture/output authority through R2's existing coordinator interfaces.

Native local object recognition uses the same hash-pinned YOLO11s model for cups/cans, bottles and bowls, converted to Core ML. Generated model binaries are ignored. Fresh clones must run `ios/Scripts/prepare_native_model.py` using the isolated exporter setup in [ios/README.md](../ios/README.md). The source model's [AGPL notice](../ios/Resources/Models/NOTICE.txt) and provenance remain separate from the repository's MIT license.

Normal Call now composes the authenticated Gemini object client; R3 must implement the proposed action in [the integration proposal](R3_GEMINI_OBJECT_INTEGRATION.md). The snapshot reader remains a separate UI. The four-category Core ML adapter remains in source and tests, but normal Call no longer selects it. Both Gemini clients require cloud configuration and sign-in. No live service or device acceptance is implied by source integration or model conversion; see [native R1/R3 validation](../ios/Validation/2026-10-24-native-r1-r3.md). Promote fixtures to shared `fixtures/` only when they become agreed cross-team inputs.

## Placeholder directories

An empty directory contains only `.gitkeep` until its owner adds content. A placeholder does not represent an implemented Swift package, an agreed hardware contract, or an active CI workflow. The Xcode project now links Guidance, Targets, Tasks and HapticLink. The Python prototype remains outside the native dependency graph.

Remove a directory's `.gitkeep` when meaningful content makes it unnecessary. Preserve the ownership and output-safety rules in the [team specification](Cal_Hacks_13_Build_Spec_v3.md) when integrating contributions.

## Local continuity files

`ios/R2_RELAY.md` and `ios/R2_HANDOFF.md` are intentionally excluded from commits and pushes. They remain on the developer's machine for continuing work. This local-only handling follows the user's repository-layout instruction and overrides any earlier delivery wording that implied committing the handoff.

A fresh clone uses the public [iOS README](../ios/README.md), [R2 specification](R2_IOS_IMPLEMENTATION_SPEC.md), and validation records. Missing local continuity files must not block setup, builds, or contribution.
