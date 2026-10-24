# R2 review of the DeskGuide teammate update

Reviewed October 24, 2026. Synced `main` by fast-forward from `16c1a5a653a40ae2f57bf6595bde0cd1d4c09110` to `d8fbb8b1845977126b52818d44c4c2134449f951` (`Add CPU DeskGuide research prototype and validation records`). The fetched origin advertises only `main`. The update adds 50 files under `DeskGuide-Laptop/`; no native app/package/firmware/backend contracts changed.

All preexisting modified/untracked R2 files and both local continuity files were hashed before the fast-forward and verified byte-for-byte afterward. The prior CI/accessibility/stress work remains uncommitted. No remote writes occurred.

## What is available now

The Python prototype supplies a concrete reaching reference: frame and audio boundaries, optical right/down/forward geometry, target-minus-fingertip deltas, explicit object states, latest-frame buffering, freshness rejection and numerical fixtures. The [current validation report](../Packages/Perception/Prototypes/DeskGuide-Laptop/CURRENT_VALIDATION.md) supersedes older prototype reports. It reports replay agreement with another implementation, not physical accuracy; its live trial produced 69 stale-frame decisions and no usable directional guidance.

At sync time, `ios/Packages/Perception`, `Guidance`, `Targets`, `backend`, `firmware` and `contracts` contained only tracked placeholders. The subsequent repository reorganization moved the prototype intact to `ios/Packages/Perception/Prototypes/DeskGuide-Laptop/`. Native Perception and Guidance implementations are still absent. The prototype is not a Swift package and cannot populate `LiveIntegrationBundle` directly. Source paths in the table below are relative to the prototype directory.

## Mapping and decisions needed

| Area | Concrete incoming source | R2 implication |
|---|---|---|
| Frame age | `deskguide/stream.py` tracks sensor timestamp, laptop receive time and relative lag; `engine.py` estimates age from reception/backlog | Neither receive time nor relative lag proves original capture age. Native R1 must supply an agreed local monotonic capture timestamp and geometry epoch. Never refresh a historical frame's timestamp to make it pass. |
| Hand evidence | `metric_hand.py:120` labels output “palm LiDAR + hand geometry (fused estimate)” | Preserve that provenance and uncertainty. R2's existing `PhysicalEvidence.depthUsable` Boolean cannot encode the distinction alone. Do not map any non-null XYZ to directly measured fingertip depth or observed action success. |
| Target evidence | `guidance_core.py` permits VISIBLE/TRACKED and rejects predicted/occluded/uncertain states | Build adapter acceptance cases around those states. A class such as cup is not automatically a stable, approved R3 target identity. Native R1/R3 must bind evidence to the chosen target and request context. |
| Task scope and hazards | Pure evaluator supports hazard/generation gates; `engine.py:126` does not supply those optional arguments | R2 must retain its own generation/request invalidation. A callable helper parameter is not proof the incoming live pipeline applies that gate. No free-space, passage-clearance or hazard-clearance evidence is supplied by this reaching prototype. |
| Audio/output | `AudioSink` accepts deltas/distance/deadline; engine calls it directly | Native output belongs behind R1's scheduler, not a second R2 audio engine or direct model-to-band path. Laptop tones do not establish offline spoken Stop or VoiceOver coexistence. |
| Completion | `app.py` marks completion on Enter | Manual completion is user input, never observed grasp evidence. Native mapping must bind an explicit answer to R2's current confirmation prompt; arbitrary completion flags must not advance a task. |
| Distance | `guidance_core.py` computes fingertip-to-contact separation | Never use this value as approach standing-position distance or passage clearance. |
| Geometry | Prototype uses RGB-D intrinsics, orientation and optical/world transforms | R1 owns any Swift geometry/tracking port. R2 can test adapter acceptance/rejection and context routing; it should not duplicate the perception implementation in Tasks. |

R2 intentionally supports image-plane hand alignment when metric depth is unavailable (`TaskEngine.swift`). Preserve that behavior; a missing depth source does not justify disabling every hand-alignment mode. It still cannot authorize metric approach or claim a verified grasp.

The newer fused-estimate implementation and older “measured tip” roadmap wording are not interchangeable. Owner agreement and actual device validation are needed before precise movement is enabled. No teammate source was rewritten during this review.

## Useful R2 work to do next

1. **Evidence mapping and replay acceptance cases.** Extend the existing R2 adapter harness with explicitly labeled recorded/synthetic cases: shape-only estimate, fused estimate with missing/current depth support, occluded/predicted target, unknown/future/stale capture time, changed geometry epoch, old generation and manual completion. Preserve original provenance. Decide the supported evidence representation with R1 before implementing a live converter. A local proposal can be written without changing shared contracts.
2. **Separate optional wristbands from live app composition.** `LiveIntegrationBundle` currently requires H1 configuration, transport and synchronization callbacks even for speech-only operation. An optional band bundle would let a later R1/R3 speech-only integration be constructed without fabricated H1 settings. Test unavailable-band behavior and preserve local Stop/generation checks. This is independent R2 work; it does not make absent R1/R3 implementations available.
3. **Prepare the first native integration acceptance path.** Once R1 supplies its single camera/session and scheduler, map frame readiness, preview, tracking loss and local Stop through the existing R2 adapters; then connect R3 target/conversation behavior and only later confirmed bands. The desktop transport, UI and audio are research tools, not production iPhone dependencies.

The most useful immediate implementation is the optional-band composition change plus R2 evidence acceptance cases. Native camera, hand/depth porting and model selection remain R1/R3 responsibilities. Physical guidance remains unavailable until real adapters and offline spoken Stop are validated.

## Validation scope

- Git fast-forward and local-file preservation checks passed; main matches the fetched origin/main.
- Reviewed incoming source, current validation, iOS roadmap and existing R2 adapter/task gates. No application source changed in this sync/review.
- A direct dependency-free policy smoke import was attempted but blocked before any case ran: this Mac's default Python is 3.9.6, while the incoming code uses newer union type syntax and documents a Python 3.12 environment. No dependencies/models were installed and no desktop tests are claimed as locally passed.
- The teammate's documented 38 Python test passes are reported evidence, not a new local run. Raw logs/reports referenced by its validation document are absent from this checkout; the committed numerical fixture alone cannot reproduce all reported measurements. The earlier local R2 129-test/unsigned Release result remains historical and was not rerun for this additive research-only update.
- Real iPhone, live audio, hardware and physical accuracy/endurance remain unverified. See [CI_ACCESSIBILITY.md](CI_ACCESSIBILITY.md) and [DEVICE_ACCESSIBILITY.md](DEVICE_ACCESSIBILITY.md).
