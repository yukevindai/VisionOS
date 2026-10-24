# Repository structure and ownership

The root README contains the canonical directory tree. This layout was recorded on October 23, 2026 and updated on October 24 to place the laptop research prototype under R1's perception directory. Both team specifications are stored in `docs/`; their original filenames are preserved.

| Path | Owner | Contents |
|---|---|---|
| `docs/` | Team | Build specifications and repository documentation |
| `ios/Reflex.xcodeproj/` | R2 | iPhone targets, dependencies, build settings, shared scheme |
| `ios/App/` | R2 | SwiftUI shell, accessibility, session coordination and integration adapters |
| `ios/Packages/Perception/` | R1 | Camera/AR session, frames, depth, pose and perception |
| `ios/Packages/Perception/Prototypes/DeskGuide-Laptop/` | R1 | Standalone Python perception/guidance research harness, evaluation tools and original validation records |
| `ios/Packages/Guidance/` | R1 | Hand/approach guidance, app speech and output scheduling |
| `ios/Packages/Tasks/` | R2 | Task state machines and evidence validation |
| `ios/Packages/HapticLink/` | R2 | CoreBluetooth transport and wristband command safety |
| `ios/Packages/Targets/` | R3 | Target interpretation and model adapters |
| `ios/Tests/` | Target owner | App and integration tests; package tests also live in each package |
| `ios/UITests/` | R2 | App UI and accessibility automation |
| `backend/` | R3 | Provider/backend integration and server-side credentials |
| `firmware/` | H1 | Wristband firmware and independent failsafe |
| `hardware/` | H1 | Electronics, actuator and enclosure information |
| `contracts/` | Agreed owners | Cross-team schemas and confirmed interfaces; H1 owns BLE/pattern definitions |
| `fixtures/` | Team | Shared test inputs with clear provenance and simulation labels |
| `.github/workflows/` | Team | Automated repository checks |

Supporting R2 paths remain under `ios/`: `Config/` contains build settings, `Scripts/` contains the reproducible Xcode project generator, and `Validation/` contains recorded results. `LICENSE` and `AGENTS.md` remain at the repository root.

## Research prototype

The former root `DeskGuide-Laptop/` directory now lives intact at `ios/Packages/Perception/Prototypes/DeskGuide-Laptop/`. Run its commands from that directory. Its fixtures, model manifests, dependency lists and licenses remain beside the code so its relative paths and provenance are preserved. Historical reports retain their original paths and environment descriptions.

This location groups the prototype with its R1 owner without making it an iOS dependency. It has no Swift package manifest and is not linked into the Xcode project. R1 still owns the native perception and guidance implementations, including camera and audio scheduling; R2's integration boundaries are unchanged. Promote fixtures to the shared `fixtures/` directory only when they become agreed cross-team inputs.

## Placeholder directories

An empty directory contains only `.gitkeep` until its owner adds content. A placeholder does not represent an implemented Swift package, an agreed hardware contract, or an active CI workflow. R2's Xcode project currently links the implemented Tasks and HapticLink packages; wire in teammate packages once their actual manifests and interfaces are available.

Remove a directory's `.gitkeep` when meaningful content makes it unnecessary. Preserve the ownership and output-safety rules in the [team specification](Cal_Hacks_13_Build_Spec_v3.md) when integrating contributions.

## Local continuity files

`ios/R2_RELAY.md` and `ios/R2_HANDOFF.md` are intentionally excluded from commits and pushes. They remain on the developer's machine for continuing work. This local-only handling follows the user's repository-layout instruction and overrides any earlier delivery wording that implied committing the handoff.

A fresh clone uses the public [iOS README](../ios/README.md), [R2 specification](R2_IOS_IMPLEMENTATION_SPEC.md), and validation records. Missing local continuity files must not block setup, builds, or contribution.
