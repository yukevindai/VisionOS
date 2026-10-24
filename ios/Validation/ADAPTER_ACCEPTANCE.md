# Native R1/R3 adapter acceptance

This is an R2 integration checklist for future native adapters. Names under **R2 seam** below are existing R2-facing proposals, not asserted R1/R3 API names. Map the actual owner interfaces when they arrive; keep perception/guidance in R1, target/conversation behavior in R3, task decisions in R2, and output scheduling in R1. The laptop prototype is research evidence, not an iPhone adapter.

## Mapping rules

| Incoming evidence | R2 seam / existing projection | Acceptance rule |
|---|---|---|
| Capture time | `FrameSnapshot.capturedAtMs`; `PhysicalEvidence.observedAtMs`; `EventEnvelope.monotonicMs` | Convert capture/observation times to the app's agreed local monotonic clock before freshness checks. Preserve original capture time through buffering. Never compare sensor/laptop/server wall clocks directly, substitute receive time for capture time, or refresh an old frame. Reject unknown, future, expired, or cross-clock-unmapped time. |
| Hand point and depth | `PhysicalEvidence.depthUsable` is the current coarse task projection | Keep fused XYZ distinct from directly measured fingertip depth in adapter metadata/diagnostics. Existing `PhysicalEvidence` has no field for measurement provenance or XYZ; `depthUsable` alone must not imply a directly measured tip. Use metric depth only when its source and confidence support that operation. |
| Contact versus approach | `PhysicalEvidence.distanceM`; `GuidanceMode` and task approach gates | A fingertip-to-contact distance may inform hand alignment only. It is not approach distance, free-space clearance, or passage clearance. Approach requires current known metric distance and observed free space; missing/uncertain depth fails closed for approach. |
| Image-plane hand guidance | `FrameSnapshot` transform/geometry and `PhysicalEvidence` | When metric depth is absent, preserve explicitly image-plane alignment where R1 supports it. Do not infer metric proximity, approach, grasp, or clearance from image overlap. Coordinate mapping must account for orientation/crop and the current `geometryID`. |
| Task completion | `VerificationOutcome`, `VerificationEvidence`, `TaskResult`, `ConversationPayload.confirmation` | Observed success must be scoped to the current action request, target, and before/after frames with valid provenance. Explicit user confirmation must answer the current pending `promptID`; unscoped/global/manual completion flags are not observed evidence. `TaskResult.verification` distinguishes `.observed`, `.userConfirmed`, and `.unverified`. |
| Stop and speech-only operation | `SpeechSchedulerClient.localStop(context:)`; `LiveIntegrationBundle` | The coordinator's Stop path must cancel guidance and invalidate task work without network, provider, speech completion, or BLE acknowledgment; the speech method only requests R1's local spoken Stop. Offline spoken Stop is a separate readiness capability. `LiveIntegrationBundle.bands` is optional, grouping confirmed H1 dependencies in `LiveBandIntegrationBundle` when available. Without it, bands remain unavailable and no BLE controller is constructed. |

## Adapter acceptance cases

Run these against the actual native adapters and record adapter revision, phone/iOS, source timestamp basis, frame/geometry IDs, mode, outcome, and whether the run was fixture, simulator, or live device. No native/live result is recorded here.

| Case | Input / action | Required result |
|---|---|---|
| Time basis | Fresh frame plus sensor, receive, server, or unknown timestamps | Accept only a capture time mapped to local monotonic time; reject unmapped/unknown, future, expired, and stale evidence. Backlog or later receive time does not refresh capture age. |
| Geometry epoch | Frame/crop/orientation changes while evidence is in flight | Reject evidence for an obsolete geometry epoch or transform; verify points map back to the original oriented image before use. |
| Hand provenance | Direct depth, fused XYZ, shape-only estimate, absent/low-confidence depth | Preserve provenance and uncertainty. Fused/shape-only values do not become measured fingertip depth. Missing metric depth can still permit supported image-plane hand alignment, but not metric claims. |
| Range meaning | Contact separation supplied to hand guidance and then approach request | Contact range is never consumed as standing-position/approach range. Approach without separate valid range and observed free space stops/fails closed. |
| Tracking/target state | Visible, occluded, predicted, uncertain, then reacquired target | Stop stale guidance on loss; do not promote prediction/occlusion to current evidence. Resume only from fresh reacquired target and hand evidence in current context. |
| Completion scope | Observed action result; current explicit answer; stale prompt answer; global/manual completion flag | Only current action-scoped observed evidence or explicit answer to current prompt may complete. Reject stale/unscoped completion; timers, overlap, and model interpretation alone do not mark success. |
| Stop offline | Stop while provider/guidance work is pending, with network unavailable | Local Stop immediately invalidates context, cancels pending work, and requests ALL_STOP. Late responses cannot emit success or guidance. Verify offline spoken Stop separately on phone. |
| Speech-only readiness | Actual R1 speech/perception with bands absent or unconfigured | Supported conversation/speech-only functions can start without invented H1 configuration; physical guidance stays unavailable unless offline spoken Stop is ready. No band command is emitted. |

## Existing R2 evidence (not native acceptance)

These tests exercise R2 policy with fixtures or controlled adapters; they do not validate actual R1/R3 APIs, camera geometry, depth accuracy, offline recognition/audio, or hardware:

- `TaskEngineTests.testUnknownDepthAllowsOnlyHandAlignmentNotApproach`, `testApproachRequiresKnownDepthDistanceAndFreeSpace`, and `testApproachModeCorrelationAndFreshHandAcquisition` cover task-mode/depth/range gates.
- `TaskEngineTests.testStaleAndWrongTargetPhysicalEvidenceStop`, `testReplacementStopsBeforeResolvingAndRejectsLateWork`, and `testScreenChangeMidGuidanceStopsAndRejectsOldCoordinates` cover stale/context/target invalidation.
- `SessionCoordinatorTests.testOldConfirmationAfterStopDoesNotCompleteTask` and the scoped-confirmation checks cover R2 prompt handling; `testPhysicalGuidanceIsBlockedWithoutLocalSpokenStopInLiveMode` covers fail-closed readiness when Stop is unavailable.
- `SessionLifecycleStressTests` exercises bounded work and stale callback behavior through test doubles. `AdapterCompatibilityHarness` explicitly documents that its doubles do not validate teammate adapters.
- `LiveCompositionTests` exercises production composition with synthetic R1/R3 adapters: optional bands, supported speech-only task progress, offline Stop and permission gates, R3 frame limits, and isolation from simulation.
- `SessionLifecycleStressTests.testQueuedFrameCannotUseLongExpiryToOutliveCaptureFreshness` and `testFrameExpiringBeforeSendTaskRunsIsDroppedAndSlotIsReleased` cover frame age after buffering and at asynchronous dispatch. `TaskEngineTests.testDispatchClockRegressionCannotMakeOldEvidenceFresh` rejects a dispatch clock older than the reducer's accepted time.

No existing test establishes native capture-clock mapping, R1 hand-measurement provenance, live image-plane accuracy, actual offline spoken Stop, a session using real speech-only adapters, or physical device behavior. Add focused adapter tests after actual interfaces and evidence semantics are agreed; then complete the applicable team T01–T14 and R2-A–F device checks. Fixture/simulator success is not a live-device pass.
