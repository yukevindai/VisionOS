# R1 integration request: live friend assistance

**Status: request for R1 review and agreement.** The combined R2/R3 live friend-assistance scope is user-approved. R1's camera/AR, guidance, shared audio, local Stop and output-scheduling ownership remains unchanged. This is a request for adapter seams, not a claim that those seams or live integrations exist today.

## Current native seams — October 24, 2026

R2 now has an optional `FriendAssistanceIntegration` injection with distinct authenticated-transport and local R1/audio clients, plus a serialized coordinator connected to `SessionCoordinator` and `TaskEngine`. The app-clock watchdog enforces local proposal, playback-confirmation, lease, and worker-ACK deadlines. Accepted selections require local Stop, an exact worker binding acknowledgement, and reacquisition of the same target under the new task generation. This remains interface and coordinator code only: no real R1 media/audio client, authenticated transport, backend, worker, or helper client is included. Friend-selected kiosk screen controls are rejected until the task engine can bind a validated screen model. See [native integration status](2026-10-24-friend-native-integration.md) for the current validation state and remaining boundaries.

The local input seam separates owner task confirmations, intent, menu/choice responses, and calibration answers from helper proposals. Generic worker Boolean confirmations and Resume commands are not trusted as owner input. Live operation still depends on the playback-completion, shared processed microphone, echo isolation, remote-audio gating, frame geometry/capture-clock mapping, offline Stop, and target-reacquisition agreements requested below.

## Current R2 seams in this checkout

R2-facing proposals currently live in `ios/App/Integration/AdapterProtocols.swift` and `ContractMapping.swift`:

- `PerceptionGuidanceClient.start(context:policy:)` returns `PerceptionReadiness`, including a camera capability report, opaque `SharedAudioInput`, offline Stop availability and speech-only guidance availability.
- `PerceptionGuidanceClient.onFrame` emits `FrameSnapshot` with `frameID`, `capturedAtMs`, `expiresAtMs`, `geometryID`, transform, optional preview/encoded image, tracking state and depth usability. R2 has a latest-only/bounded frame path and sends selected frames through `ConversationClient.sendLatestFrame` under its configured `FrameDeliveryPolicy`.
- `PerceptionGuidanceClient.reacquire(_:target:)` returns R2 `PhysicalEvidence`; `guide`, `cancelGuidance`, and `stop` provide the task/guidance seam.
- `SpeechSchedulerClient` exposes `enqueueAudio(Data, context:)`, `cancel`, `localStop`, `speak`, and scheduler-approved haptic release handling. There is currently no playback-started/completed callback in this protocol.
- `ConversationClient.start(context:audioInput:)` consumes the opaque shared audio input; it also exposes mute, interrupt and end. `ConversationPayload.audio(Data)` is an adapter proposal, not a declaration of the real audio format.

The names and R2 projections are local adapter contracts. R1 APIs, callback threading, buffer lifetime, audio format and capture-clock conversion must be mapped from actual R1 code before integration. `ios/Validation/ADAPTER_ACCEPTANCE.md` describes additional evidence requirements and unverified areas.

## Requested R1 inputs and outputs

### 1. One-owner camera frame tap

Please expose an adapter from the existing R1 camera/AR session that can provide a `CVPixelBuffer` (or another agreed zero/low-copy native frame handle) while retaining:

- stable native frame ID and original capture instant;
- an explicit mapping of capture time to R2's local monotonic clock, or a documented conversion with bounded uncertainty;
- orientation, crop/resize transform, dimensions and geometry epoch;
- depth/hand/tracking/hazard/free-space values with provenance, or explicit unknown/unavailable states;
- a clear buffer-retention/lifetime rule and a latest-frame delivery/backpressure policy.

R2/R3 need to forward on-demand selected/current frames to the worker without opening another camera session or changing R1's capture schedule. Preserve image and geometry identity end to end. A received or rendered timestamp must never replace capture time.

Illustrative callback only:

```swift
onCapturedFrame(
    pixelBuffer: CVPixelBuffer,
    frameID: NativeFrameID,
    captureTime: R1CaptureTime,
    geometry: ImageGeometry,
    observation: CurrentObservation
)
```

Names are placeholders; please advise the actual R1 equivalents and whether the worker may request a bounded current frame through this same source.

### 2. Shared audio input and independent offline Stop

Please identify the audio-session-owned microphone source R1 can share with the voice worker and any native speech/output component. R2 must not start a competing microphone engine. Specify sample format, sample rate/channel layout, timestamp clock, buffering/backpressure, mute/interruption behavior and how teardown releases consumers.

Please also identify the on-device local Stop command event and its availability conditions. It must be recognized without a cloud round trip and route directly to R2's synchronous local stop path. Report unsupported language/device/offline states honestly. The R2 protocol currently reports `supportsOfflineStop`; no live R1 implementation is present in this checkout.

Illustrative event:

```swift
onLocalCommand(.stop, source: .onDeviceRecognizer, observedAt: appMonotonicTime)
```

R2 immediately invalidates task work, requests `ALL_STOP` if bands are configured, cancels outstanding adapters, and requests R1 local spoken Stop. A network acknowledgement or BLE/audio completion must not gate this path.

### 3. Cancellable PCM playback with actual completion

R2/R3 need a cancellable path for generated speech audio into R1's output scheduler, with an opaque playback ID, session/task generation context, priority, format metadata and completion/cancellation callbacks. The callback must distinguish **accepted/enqueued**, **playback started**, **playback completed**, **interrupted/cancelled**, and **failed**. A function return after enqueue is not proof that the spoken confirmation prompt finished.

The owner confirmation response window must begin only from actual completion of the specific prompt playback, and must be invalidated by Stop, replacement, interruption, stale generation or cancellation. Late PCM must never replay after cancellation.

Illustrative seam:

```swift
let playbackID = try speech.enqueuePCM(data, format: format, context: currentContext)
speech.cancel(playbackID: playbackID, context: currentContext)
speech.onPlaybackEvent { event in
    // event: enqueued | started | completed | cancelled | interrupted | failed
}
```

Please confirm whether R1 already has a suitable scheduler API and how it arbitrates app speech against VoiceOver, hazards, local Stop and remote helper audio.

### 4. Remote-audio admission gate

Please expose a gate in the single R1-owned audio/output path for remote helper audio, and clarify the separate path for worker-generated AI PCM. Stop/Pause must immediately preempt and flush current remote playback so it cannot delay local Stop or safety speech. With a still-valid helper lease and sharing enabled, new live friend audio may be admitted again under the current owner Pause/Resume state; a guidance Pause must not permanently end the friend conversation. Persistent sharing removal/disable, lease expiry, background/teardown and End close helper media admission until fresh authorization/re-enablement. The gate should prevent buffered playback after a close event and act locally without waiting for Convex/worker. Worker PCM must pass R1's scheduler and its own cancellation/generation gate. Turning off helper sharing should preserve a healthy private AI session, though current task speech remains subject to Stop/Pause preemption.

R1 owns final app audio scheduling. This request does not grant helper/worker audio permission to bypass R1 arbitration or local Stop. Please specify whether R1 can signal gate state and actual media eviction separately.

### 5. Accepted-target generation binding and current reacquisition

After the owner gives an explicit spoken Yes for a single pending helper proposal, R2 atomically invalidates the old task/output context and creates a new task generation bound to the exact `selectionId + promptId + proposalGeneration + newTaskId + newTaskGeneration + acceptedTargetIdentity`. Before any new-generation worker tools run, the worker must receive and acknowledge this binding; delayed outputs from the prior generation are rejected. R2 then requests R1 to reacquire the target from a **new current native frame** using its native identifier and geometry. The helper's clicked reference frame and any Gemini/Claude target description are historical interpretation only; neither can be converted into fresh `PhysicalEvidence` by updating a timestamp. A subsequent helper click supersedes only a pending unconfirmed proposal; it does not invalidate an accepted binding or interrupt active guidance. Replacing that active target still requires the owner's explicit acceptance and the normal task replacement/Stop path.

Please provide an R1 API/event that accepts the accepted target description plus session/generation identity and returns:

- current native frame ID and capture time mapped to the app monotonic clock;
- geometry epoch/transform used for the current observation;
- visible/tracked/lost state and supported target/hand measurements;
- depth, free-space and hazard provenance, preserving unknowns;
- cancellation when generation changes, Stop occurs, or the target is superseded.

R2 checks context, identity and the 300 ms evidence freshness limit at dispatch, then applies existing task safety rules before calling R1 guidance. R1 remains authoritative for tracking and guidance; R2 remains authoritative for task transition and owner confirmation. A missing, ambiguous, stale, occluded or unsafe target yields no cue and requests clarification/reselection.

Illustrative exchange:

```swift
let binding = AcceptedTargetBinding(
    selectionId: selectionId,
    promptId: completedPromptID,
    proposalGeneration: proposalGeneration,
    newTaskId: newTaskId,
    newTaskGeneration: newlyAllocatedGeneration,
    acceptedTargetIdentity: helperResolvedTarget.identity
)
try await worker.synchronize(binding) // before tools are enabled for this generation
let observation = try await perception.reacquire(binding)
guard observation.context == binding.context,
      observation.capturedAtMonotonicMs <= clock.nowMs,
      clock.nowMs - observation.capturedAtMonotonicMs <= 300
else { throw ReacquisitionError.staleOrMismatched }
```

This is illustrative, not a request to adopt those exact Swift types.

## Questions for R1

1. Which current implementation modules and APIs can supply these seams, and which are not yet implemented?
2. What are the native frame buffer lifetime, capture-clock domain/mapping and geometry identity guarantees?
3. What shared audio and local Stop behavior is supported on-device and offline? How does R1 expose actual playback completion and cancellation?
4. Can R1 close remote-audio admission and evict buffered playback synchronously on local owner policy changes?
5. What current-target representation can R1 reacquire, and which observations/depth values are actually available with provenance?
6. Which R1-dependent request/response fields need mapping? R2/R3 application schemas can be drafted independently; existing shared task schema v3 remains unchanged absent owner agreement.

## Current evidence boundary

The repository contains R2 adapter protocols, synthetic adapter tests, and documented acceptance criteria. It does not contain an integrated native R1 frame/audio adapter or establish live microphone, local spoken Stop, physical reacquisition, playback completion, remote-audio gating, or device behavior. Simulator/fixture results cannot establish these R1 integrations. Record the actual mapped APIs, unsupported fields and device validation separately when R1 provides them.
