# Friend assistance interface proposal

**Status: proposed; pending agreement with R1 and review by R2/R3.** This document records interface boundaries for the combined R2/R3 live friend-assistance scope approved by the user. It does not change an existing shared contract, R1 implementation, H1 contract, or the task schema v3. Types and examples below describe behavior, not finalized wire formats.

## Ownership and authority

- **R1** owns the single iPhone camera/AR session, capture timestamps and geometry, depth provenance, tracking, hazards, guidance, shared audio coordination, local spoken Stop detection, and final application audio/output scheduling.
- **R2** owns the native iPhone experience, task engine, session/task generations, confirmation state, local Stop path, and validation that a remote proposal is still authorized before it can affect physical guidance.
- **R3** owns backend/provider behavior, authenticated friend access, realtime worker, image interpretation, and helper-facing status. These components may propose targets and dialogue; they do not authorize movement or claim physical evidence.
- **H1** retains hardware, firmware, BLE/GATT and output failsafe ownership. No friend-assistance interface changes H1 ownership or values.

An owner may start and end assistance, enable or disable sharing, pause/resume, accept or reject a proposal, and stop immediately. A trusted helper may join only when sharing and the current helper lease are active; speak; submit a target proposal; and request Pause, which stops guidance but cannot resume it. A helper may not confirm on the owner's behalf or mark a task complete. The worker may interpret inputs and deliver proposals, but cannot confirm, mint current physical evidence, issue haptic commands, or bypass Stop.

## Proposed data boundaries

1. **Reference selection:** The helper's click binds to the exact rendered helper frame, its media/transport epoch, rendered-content geometry, pointer coordinate, and a selection ID. The reference image is historical interpretive context. It is never R1 physical evidence and does not authorize movement.
2. **Current scene:** After interpretation and owner confirmation, R2 requests a fresh observation from R1. R1 reacquires the selected target in the current native scene and returns its native frame ID, capture time mapped to the agreed local monotonic clock, current geometry identity, and physical evidence. A model result or helper reference frame cannot be relabeled as this observation.
3. **Pending versus accepted binding:** A newer helper selection supersedes only an older pending, unconfirmed proposal. It does not replace an accepted target or interrupt active guidance. Only an explicit owner acceptance of a current proposal may replace the active task, through the existing Stop/replacement path. Acceptance atomically binds `selectionId`, `promptId`, `proposalGeneration`, the new `taskId` and `taskGeneration`, and the accepted target identity. Delayed results and tool outputs must match the complete binding. Before any tools run under the new generation, the worker must receive/acknowledge that binding or reject the call; outputs from the prior generation are discarded. Stop or task replacement invalidates the binding. Reacquisition must happen again after acceptance and before physical guidance.
4. **Task authority:** Existing R2 task validation decides which task actions are allowed and what observed or explicit-user-confirmed result advances a step. Models and helpers cannot authorize emergency controls, payment, passage, or success.

Illustrative flow (names are placeholders, not a JSON schema):

```text
HelperSelection {
  selectionId, helperLeaseId, mediaEpoch,
  renderedFrameId, renderedAt (transport metadata),
  contentSize, displayedTransform, normalizedPoint, optional description
}

ResolvedProposal {
  proposalId, selectionId, proposalGeneration, candidateLabel, candidateKind,
  referenceFrameId, geometry, ambiguity
}

OwnerDecision {
  selectionId, proposalId, promptId, proposalGeneration, explicitOwnerAnswer
}

AcceptedTargetBinding {
  selectionId, promptId, proposalGeneration,
  newTaskId, newTaskGeneration, acceptedTargetIdentity
}

CurrentReacquisition {
  newTaskId, newTaskGeneration, acceptedTargetIdentity, nativeFrameId,
  localCaptureMonotonicMs, geometryId, visibility, tracking,
  depthProvenance, handState, hazardState
}
```

The `CurrentReacquisition` fields are illustrative. R1 should return only measurements and provenance its actual implementation supports; unknown depth/free space stays unknown. The helper's media timestamp is transport/render context and must not be compared to the phone's monotonic clock as though they shared a clock.

## Clock and freshness rules

- The phone's R1 capture and observation times must map to the monotonic time base R2 uses for local freshness checks. Preserve the original capture time through buffering; do not substitute receive time or refresh a stale frame.
- Server/backend timestamps are UTC wall time for authorization, invitations and durable records. They do not determine local evidence freshness.
- Helper media time is scoped to the media track/transport clock. Retain frame identity/epoch and any supported mapping as provenance; do not directly subtract it from phone monotonic or server UTC time.
- Provider/worker receive and completion times are diagnostic/server context, not sensor observation times.
- Reject unknown, future, expired, stale, cross-session, cross-generation or unmapped evidence for physical actions. Baseline movement evidence freshness remains at most 300 ms at dispatch, subject to the actual R2/R1 contract.

## Stop, access expiry and recovery

Owner Stop/Pause, tracking loss, hazard, relevant audio/camera interruption, task replacement, or authorization-lease expiry immediately invalidates pending proposal/prompt bindings and task work. R2 advances/invalidates its task generation and clears output locally; `ALL_STOP` and local R1 Stop do not wait for a server or worker response. The worker/helper must stop accepting actions when their server-issued authorization is no longer valid. Remote failure must not delay local Stop. A helper Pause can stop guidance/output but cannot resume or confirm it.

Sharing authorization is separate from task evidence freshness. Proposed lease renewal and expiry behavior follows the combined R2/R3 specification; it must use server-issued authorization state, not helper heartbeats alone. Stop/Pause immediately preempts and flushes already-buffered remote audio so it cannot delay local Stop or safety speech. Under a still-valid sharing authorization, new live friend audio may be admitted again according to the owner's Pause/Resume state; Pause does not permanently end the friend conversation. Disabling sharing, lease expiry, helper removal, background/teardown, and End close helper media admission until sharing is explicitly re-enabled and authorized. Worker-generated AI PCM has its own R1 scheduling gate: stopping helper sharing must preserve a healthy private AI session, while Stop/Pause still preempts current playback and cancels stale task speech. On expiry or uncertain eviction, helper-derived proposals stop and shared media is unpublished/removed while supported local assistance can continue. Resume requires explicit owner action, current local readiness/evidence, and a fresh target binding as applicable; no crash, reconnect, or worker restart silently resumes movement.

## Proposed examples for R1 agreement

These pseudocode exchanges identify the needed seams; actual Swift signatures and event encoding remain open.

```swift
// R1 -> R2/R3: one camera owner, source frame identity retained.
frameTap.publish(
    pixelBuffer: frame.capturedImage,       // CVPixelBuffer owned/retained per agreed lifetime
    frameID: frameID,
    capturedAt: captureTimeMappedToAppMonotonic,
    geometry: geometryIDAndImageTransform,
    depth: depthWithProvenanceOrUnknown
)

// R2/R3 -> R1: after owner acceptance, bind the target to a fresh task generation.
let current = try await perception.reacquire(
    context: SessionContext(sessionID: sessionID, generation: newGeneration),
    acceptedTarget: proposal.target
)
// R1 -> R2: current physical observation only; caller checks freshness before guide.
```

Remote audio/media access is separately gated by current owner sharing state, helper lease, session epoch and authorization version. A valid helper selection does not itself enable audio or video. R1's gate must preempt/flush current helper playback immediately on Stop/Pause; it may admit subsequent live friend audio again under the current valid authorization and owner state. Persistent removal/expiry/disable/End closes helper admission and requires fresh authorization before reopening. Worker-generated AI PCM is separately gated so turning off helper sharing does not end healthy private AI assistance.

## R1-dependent fields to resolve

- R1's real frame callback type, ownership/lifetime, pixel format, orientation/crop mapping, capture-clock mapping, and supported depth/hand/hazard provenance.
- R1's shared microphone tap and local Stop command event semantics; playback cancellation and actual playback-completion signal.
- R1's remote-audio admission/mute gate and safe interruption behavior.
- The concrete accepted-target request/response and cancellation behavior for current-scene reacquisition.
- R2/R3 selection transport and helper-render transform representation, plus server-issued lease encoding and revocation acknowledgement.
- Which values are stable cross-owner identifiers versus local-only references.

The app-level R2/R3 schema and tests can be drafted and stabilized independently. Keep only R1-dependent fields and mappings provisional until R1 confirms the native seams. All illustrative names here remain proposals, not confirmed provider or network API fields.
