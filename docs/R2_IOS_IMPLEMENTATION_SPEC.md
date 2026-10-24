# Reflex R2 Native iOS Implementation Spec

Version 1.0 — October 23, 2026

Repository: `yukevindai/VisionOS` · Product: **Reflex** · Role: **R2 — Accessible session experience, task execution, and wristband integration**

## 1. Implementation assignment

Build the complete R2 role as a **native iPhone application written in Swift and SwiftUI**. Developers will edit the repository in VS Code and then open the committed Xcode project to build and run it on an iPhone. Deliver actual compilable source, local Swift packages, tests, resources, a shared Xcode scheme, and setup instructions. Do not deliver a web app, React Native project, visionOS target, UI-only prototype, or loose Swift files that require manually rebuilding the project in Xcode.

The repository name `VisionOS` does not change the platform: this is an **iOS app for iPhone**, not an Apple Vision Pro app.

The everyday experience is:

> Open Reflex → activate Start assistance → speak naturally with a live AI assistant that sees through the rear camera → receive speech and wristband guidance → pause or end whenever needed.

The user must not select a task from a visual dashboard, manually choose a camera lens, operate a target overlay, or read a transcript to complete a supported task. The visible interface is intentionally small; most of R2 is coordination, task execution, and the Bluetooth connection.

### Source and precedence

This specification expands R2 from `Cal_Hacks_13_Build_Spec_v3.md`, whose internal heading is v3.1. That file defines team ownership and safety invariants. This document makes the user-requested call-style experience and iOS delivery explicit, and carries the R2 requirements needed to implement without that attachment.

- R1 perception, hand guidance, and voice guidance are already in progress. H1 wristband hardware and firmware are already in progress. Adapt to their actual implementations; do not replace them.
- Read current repository instructions and teammate code before implementation. The repository snapshot reviewed for this document contained only `README.md` and `LICENSE` on `main`; teammate implementations were not available there. Reinspect when building.
- Preserve the agreed shared contracts. New cross-role interfaces below are **adapter proposals**, not evidence that R1, R3, or H1 already implement them.
- Build all independent R2 work while interfaces are pending. Use explicitly labeled fixtures and mocks; report unavailable live integration accurately.
- Do not invent hardware UUIDs, pattern timings, provider endpoints, credentials, depth support, or teammate APIs. Keep unresolved external values typed and configurable with an explicit unavailable state.
- A working mock, simulator run, or successful compile is not a real-device pass.

## 2. Team boundaries

| Owner | Responsibilities | R2 interaction |
|---|---|---|
| R1 | Single AR session; camera frames, depth, pose, hazards, free-space assessment, hand tracking, approach and hand-guidance controllers; all application speech/tones and cue scheduling | Consume frames/status; request guidance and speech; receive only scheduler-released haptic events |
| H1 | Electronics, firmware, GATT service/characteristics, pattern timing, actuator limits and independent failsafe | Implement iPhone CoreBluetooth central against H1's confirmed contract |
| R2 | SwiftUI app, accessibility, setup/calibration, session lifecycle, task state machines, HapticLink, integration coordinator, Xcode project | Own implementation and integration of these components |
| R3 | OCR and model interpretation, target resolution, kiosk/menu models, intent tools, backend, provider adapters, credentials and telemetry backend | Send session inputs through the agreed adapter; validate returned proposals before task execution |

Only R1 opens the camera/AR session. Only R1 plays app-owned audio and schedules output. Only R2's HapticLink writes BLE commands. R2 may send `ALL_STOP` directly. All other patterns, including calibration patterns, must pass through R1's scheduler or an explicitly labeled test scheduler in mock mode.

VoiceOver remains a system accessibility service. Its navigation announcements are not a second app speech engine; coordinate app speech with it rather than disabling accessibility.

### Owned paths

- `ios/App/`
- `ios/Packages/Tasks/`
- `ios/Packages/HapticLink/`
- `ios/Reflex.xcodeproj/` and shared scheme
- R2 test targets, Xcode build/configuration files, and R2 setup/handoff documentation

Do not modify `ios/Packages/Perception/`, `ios/Packages/Guidance/`, `ios/Packages/Targets/`, `firmware/`, `hardware/`, or `backend/` without coordination with that owner. H1 owns `contracts/ble.md` and `contracts/haptic_patterns.json`; consume them, do not silently revise them. If a shared type package already exists, reuse it. Otherwise keep R2-facing projections inside R2-owned code until owners agree a shared location.

## 3. iOS and Xcode delivery

### Required implementation approach

- Native SwiftUI UI, Foundation task logic, CoreBluetooth transport, structured Swift concurrency, and protocol-based adapters.
- Proposed baseline: iOS 17.0 or later, Swift 5.9-compatible source. If teammate code requires a newer deployment target or language mode, align the project and document the actual minimum. Do not downgrade existing teammate requirements.
- Use the team's installed stable Xcode with the required iOS SDK. Record the exact Xcode/Swift version used to validate. VS Code is the editor; building/signing the iOS app still requires macOS and Xcode.
- Use system frameworks and local Swift packages wherever possible. No new third-party calling SDK or backend is required by R2.
- Provider secrets belong in R3's backend. Bundle only nonsecret configuration. Any session token must be short-lived, handled through the adapter, and excluded from logs.
- Keep UI state on `@MainActor`; serialize task mutations and BLE state. Do not block the main thread with inference, image encoding, Bluetooth retries, or network operations.
- No absolute developer-machine paths in project files. Commit all resources, package manifests, target membership, and shared schemes necessary for a fresh clone.

### Proposed file organization

Use equivalent existing names when present; do not create competing copies.

```text
ios/
  Reflex.xcodeproj/
    project.pbxproj
    xcshareddata/xcschemes/Reflex.xcscheme
  App/
    ReflexApp.swift
    Composition/AppDependencies.swift
    Session/SessionCoordinator.swift
    Session/SessionState.swift
    Session/StopCoordinator.swift
    Integration/PerceptionGuidanceAdapter.swift
    Integration/ConversationAdapter.swift
    Integration/TargetEvidenceAdapter.swift
    Integration/ContractMapping.swift
    Views/HomeView.swift
    Views/AssistanceSessionView.swift
    Views/SetupView.swift
    Views/BandSetupView.swift
    Views/CalibrationView.swift
    Views/SettingsView.swift
    Accessibility/
    Persistence/UserPreferences.swift
    Diagnostics/
    Mocks/
    Resources/Assets.xcassets/
    Info.plist
  Packages/
    Tasks/
      Package.swift
      Sources/Tasks/
      Tests/TasksTests/
    HapticLink/
      Package.swift
      Sources/HapticLink/
      Tests/HapticLinkTests/
  Tests/ReflexAppTests/
  UITests/ReflexUITests/
  Config/Debug.xcconfig
  Config/Release.xcconfig
  Config/Local.example.xcconfig
  README.md
  R2_HANDOFF.md
```

The dependency graph must remain acyclic: app composition imports Tasks, HapticLink, and available teammate modules; task logic does not import SwiftUI, CoreBluetooth, or provider SDKs. App adapters translate domain effects into teammate calls. Keep mocks under R2 paths, not inside teammate packages.

If generating the Xcode project, commit the generation source and the generated project. A fresh clone must open directly in Xcode without first installing a project generator. Do not commit `xcuserdata`, DerivedData, certificates, provisioning profiles, or secret local configuration.

Configure iPhone device and simulator builds, Debug/Release configurations, bundle identifier configuration, version/build number, launch resources, and the shared `Reflex` scheme with tests. Leave the signing team for the developer to select; document it. Do not hard-code a personal team ID.

Include clear purpose strings for camera, microphone, and Bluetooth permissions. Add speech-recognition permission only if the integrated implementation actually uses the API requiring it. Do not request contacts, location, photos, or background modes that this MVP does not use.

## 4. User experience and accessibility

### 4.1 Home

- Product name, one short explanation, and a large **Start assistance** button.
- Secondary **Settings** action for bands, preferences, and calibration.
- No mandatory login, task grid, onboarding slideshow, or provider selection before assistance.
- Start remains reachable with VoiceOver and large text. Repeated activation must not create duplicate sessions.
- Before permission prompts, briefly explain the reason in accessible text. Handle each denied/restricted permission with a specific explanation and Open Settings action where appropriate.

### 4.2 First-time setup

Setup must be accessible and remembered. Ask only what is needed:

1. Camera/microphone permissions for the assistance session.
2. Bluetooth permission when setting up bands, followed by discovery and explicit left/right confirmation.
3. Guiding hand, left or right. Ask once; do not silently infer from the camera.
4. Phone position: handheld in the other hand or mounted. Explain that the rear camera must see the working hand and target. Do not assume the user can hold the phone and perform a two-handed task simultaneously.
5. Short guided band calibration, with an option to repeat it later.

Allow an explicitly announced speech-only session if bands are unavailable or calibration is skipped. Do not pretend wristband guidance is active. Do not block ordinary scene/menu conversation on optional band setup. A later band connection does not silently activate uncalibrated haptics.

### 4.3 Active assistance session

Present a call-style session with a rear-camera preview, short status, optional readable captions, and stable controls:

- **Pause assistance / Resume assistance**
- **End assistance**
- Optional **Mute microphone** only if its consequences are correctly implemented and explained

The preview supports low-vision users and demo observers; it must not be essential to the interaction. Keep developer overlays, provider names, frame rates, JSON, and task state diagrams in a separate debug interface.

Distinguish Preparing, Listening, Thinking, Speaking, Guiding, Paused, Reconnecting, and Ended. Do not announce rapidly changing state every frame. Avoid a fake connected indicator while adapters are unavailable. The assistant's opening message should describe capabilities actually available: for example, "I'm ready. What would you like help with?" or "Your wristbands aren't connected. Voice assistance is available."

Listen for natural requests and route intents into BTN/OBJ/MNU/DOR automatically. No task-selection screen is needed. Support "stop," "pause," "resume," "repeat," "cancel this task," "end assistance," and confirmation responses in context.

### 4.4 Voice and accessibility rules

- Standard SwiftUI `Button`, `Form`, and navigation semantics; meaningful labels, values, traits, and hints where needed. Decorative icons and preview overlays must not clutter VoiceOver traversal.
- Minimum 44 × 44 point targets; make Start, Pause, and End larger. Use text plus icon, never color alone.
- Support Dynamic Type, light/dark appearance, increased contrast, Reduce Motion, and safe areas. Do not truncate critical status or recovery instructions.
- Initial VoiceOver focus goes to Start; after starting, expose a useful session status and stable controls. Do not repeatedly steal focus for transcript updates.
- Route app speech through R1. Avoid duplicating a spoken message with an automatic accessibility announcement of identical content. Test VoiceOver navigation while app speech is active.
- Natural interruption must cancel or interrupt conversation through R1/R3 adapters without allowing stale speech to resume. A local Stop always preempts everything.
- A microphone mute must pause movement/hand guidance and explain that spoken Stop cannot be heard; Pause and End remain accessible. Resume guidance only after the microphone is unmuted and readiness is checked.
- No essential action may depend solely on a custom gesture, a visual overlay, a countdown, or reading captions. Optional VoiceOver magic-tap integration may supplement visible accessible controls.

## 5. Camera and live AI session

### 5.1 Widest compatible rear-camera policy

Request this policy from R1:

> Select the widest rear-camera field of view supported by the active perception configuration. Prefer the physical ultra-wide lens, commonly presented as 0.5×, when that configuration supports it. Otherwise use the standard wide rear lens, commonly presented as 1×. Do not require manual selection or default to the selfie camera.

R1 must inspect the selected AR configuration's `supportedVideoFormats` and capture device types on the actual iPhone. The presence of an ultra-wide lens in AVFoundation alone is not proof that it is available in the required ARKit/depth configuration. Camera-app zoom labels are not a universal numeric value to assign to a capture device's zoom property.

R2 consumes R1's preview/frame output and records reported camera capabilities. R2 must not create another `ARSession`, `AVCaptureSession`, or camera-opening video SDK. Do not separately capture ultra-wide RGB and attach depth from a different camera without an explicitly implemented and validated registration pipeline; that pipeline is outside R2.

Preserve the camera image's usable field of view when displaying it; prefer an aspect-fit preview. Preserve full-scene input for perception. Any resized/cropped AI image needs a reversible coordinate transform back to the original oriented frame. Never pass crop-relative target coordinates directly into full-frame guidance.

Lens or format changes invalidate frame/target geometry and require pause, new generation, and reacquisition. Do not silently auto-switch lenses during hand guidance. If the widest lens breaks depth/hand-guidance support, use the widest compatible lens and record the fallback reason. Distinguish unsupported hardware from temporary low-confidence depth; both must prevent unsupported movement cues.

### 5.2 Session transport and audio ownership

"Video call" means a live voice-and-camera session with the AI, not a human call or a requirement to add WebRTC, CallKit, FaceTime integration, or a separate conferencing platform. Integrate R3's selected transport. If a transport SDK is used, it must accept externally supplied camera frames and respect the single R1-owned audio system.

R2 owns session connection state, start/end/reconnect orchestration, cancellation, and UI bindings. R3 owns provider protocol details, AI session transport, model adapters, and intent interpretation. R1 remains the owner of audio-session/output coordination. Agree how microphone samples and local command events are exposed; do not start independent microphone engines that conflict with R1.

The conversation adapter must provide:

- Start/end and connection status.
- Camera input derived from R1, with original frame ID and coordinate transform.
- Microphone input or a handle to the agreed shared audio input.
- Transcript/intent events, interrupt/cancel, and session-scoped errors.
- Audio responses delivered to R1's scheduler, never directly played by R3 or R2.
- Correlation IDs and cancellation context for any requested action.

Use bounded buffers and latest-frame sampling. Never queue every camera frame behind slow network requests. Frame rate, image size, and provider transport limits come from R3's supported interface and device measurements; keep configurable, not invented guarantees. Keep preview, local guidance, and Stop independent of cloud latency.

Local Stop recognition must not depend on a cloud response. Consume an R1-owned on-device command detector or agree an equivalent local adapter using the shared audio input. Verify offline availability on the actual phone/language; do not assume all speech APIs are offline. If this adapter is unavailable, label spoken offline Stop as an integration blocker, keep the accessible local Stop control working, and do not claim completion of the requirement.

## 6. Session lifecycle and cancellation

Model session lifecycle separately from task lifecycle. Suggested session states:

`idle → preparing → active ↔ paused → ending → ended`

`preparing/active` may enter `recovering` or a recoverable `failed` state. The session also exposes capability status for perception, conversation, microphone, left band, and right band; one vague connected Boolean is insufficient.

### Start sequence

1. Serialize Start; create a fresh session ID and initialize cancellation/generation context.
2. Check permissions and persisted preferences. Present only missing setup.
3. Ask R1 to start its existing session with the camera policy; subscribe to fresh frame/tracking and hazard events.
4. Reconnect known bands and validate characteristics/status. Connection discovery must not delay an immediate local Stop path.
5. Start R3 conversation using the agreed audio/frame adapters.
6. Announce actual readiness through R1; enable conversation as capabilities become ready. Allow physical guidance only when its own prerequisites are satisfied.

### Unified Stop path

The same idempotent stop operation must be reachable from UI Pause/End, local spoken Stop, tracking loss, hazard, band failure during haptic guidance, and app/audio interruptions.

1. Atomically invalidate the active task generation before processing further async results.
2. Cancel pending guidance, verification, provider requests, scheduled speech, and non-stop haptic work; clear pending user confirmation and target bindings.
3. Send `ALL_STOP` to both connected bands immediately through HapticLink, without waiting for cloud, speech, or acknowledgment.
4. Request the local spoken "Stop" through R1 at priority 0. If audio is interrupted, retain the stopped state; never wait for audio before stopping bands.
5. Transition to paused, recovering, cancelled, or ended according to cause. Record the cause without claiming a BLE packet was delivered when it was only enqueued.

Stopping must still complete locally when providers never return. If BLE is unavailable, clear app queues and cease keepalive; firmware's independent expiry/watchdog is the final physical fallback. Do not assume app code runs after a crash or force-quit.

### Resume and interruption behavior

- User pause/Stop requires explicit resume. End destroys the session and releases subscriptions/resources.
- Recovery requires fresh tracking, fresh target/hand evidence, current generation, usable output paths, and no active hazard. Never replay old commands after reconnect.
- After tracking recovery, announce readiness and request confirmation before restarting physical guidance. This deliberately avoids an unexpected movement cue.
- Backgrounding, locking, audio-session interruption, or losing an essential audio route pauses physical guidance. Do not rely on continuing camera guidance in background. Foregrounding does not auto-resume movement.
- A cloud outage may leave validated local guidance/Stop available if R1's local controller can maintain fresh evidence. Pause any task step requiring a cloud result; do not fake a successful read or completion.
- Disable the idle timer only while an active foreground session needs it; restore it on pause/end as appropriate. Document that manually locking the phone interrupts assistance.
- Bounded reconnection attempts with cancellable backoff; Stop and End must interrupt them. Connection recovery alone cannot finish or resume a task.

## 7. Contracts and integration adapters

### 7.1 Baseline schema

All baseline records carry `schemaVersion, eventId, sessionId, taskId, taskGeneration, monotonicMs, expiresAtMs`. Monotonic values are local session timing, not UTC. Do not compare unrelated device/server monotonic clocks. Keep backend UTC timestamps separate and establish provenance when converting provider responses into local records.

| Record | Direction | Baseline fields |
|---|---|---|
| FrameStream | R1 → consumers | `frameId, imageRef, orientation, intrinsics, depthRef?, depthConfidence, pose, trackingState` |
| HandObservation | R1 → consumers | `chirality, fingertipImage, fingertipM?, confidence, frameId` |
| Target | R3 → R1/R2 | `targetId, kind, label, imageRegion, frameId, poseM?` |
| ScreenModel | R3 → R2 | `frameId, controls[{id,label,price?,region}], sections, isPaymentScreen` |
| Hazard | R1 → R2 | `kind, region, distanceM?, urgency, observedAt` |
| GuidanceRequest | R2 → R1 | `targetId, mode: approach/hand, guidingHand: L/R` |
| GuidanceStatus | R1 → R2 | `state: guiding/on_target/lost/failed, errorCode?` |
| SpeechRequest | R2/R3 → R1 | `text, priority` |
| GuidanceEvent | R1 → R2 | `priority, action, direction?, text?, hapticPatternId?, requiresConfirmation` |
| TaskResult | R2 → consumers | `status, verification: observed/user_confirmed/unverified, errorCode?` |

Coordinates are normalized, top-left origin after orientation correction; quaternions use x/y/z/w. Reject malformed values rather than clamping an invalid target into a plausible location.

Error codes already agreed: `TARGET_LOST, HAND_NOT_VISIBLE, LABEL_AMBIGUOUS, ACTION_UNCONFIRMED, SCREEN_CHANGED, KIOSK_RESET, PAYMENT_BOUNDARY, LOW_CONFIDENCE, BAND_DISCONNECTED, PROVIDER_TIMEOUT`.

Internal app errors may distinguish permission denial, unsupported hardware, invalid configuration, contract mismatch, and transport interruption. Do not quietly add new wire enum values that teammates do not support.

### 7.2 Required R2 seams

Define Swift protocols or equivalent narrow abstractions for:

- **PerceptionGuidanceClient:** readiness/frame/capability/status/hazard streams; start/stop perception; request/cancel approach or hand guidance; scheduler-released cues.
- **SpeechSchedulerClient:** request priority speech, cancel by context, local Stop, interruption, calibration requests. Live implementation delegates to R1.
- **ConversationClient:** start/end, input feeds, status/transcript/intent stream, cancel/interrupt. Live implementation delegates to R3.
- **TargetEvidenceClient:** resolve target, read screen/menu, request verification evidence, return explicit unavailable/uncertain outcomes. Delegates to R1/R3 according to evidence owner.
- **HapticTransport:** connect, link status, transmit approved scheduled envelope, heartbeat, all-stop, disconnect. Live implementation uses CoreBluetooth.
- **Clock and diagnostics sink:** deterministic monotonic timing for tests; structured bounded logging.

These are responsibilities, not existing teammate method names. Map to real APIs rather than requiring teammates to rename working code. All async responses retain task/session/generation/request correlation. A timeout or cancellation must prevent a late response from mutating current state even if the underlying operation cannot be physically cancelled.

### 7.3 Contract gaps requiring owner agreement

Record these in the handoff with one input/output example per proposal. Implement mock adapters while waiting; never disguise a proposed field as an existing contract.

| Gap | Proposed resolution | Owner |
|---|---|---|
| GuidanceStatus does not identify approach vs hand request | Correlate each request/status by request ID and mode in an adapter or agreed extension; permit one active guidance request | R1/R2 |
| Scheduled event lacks BLE side, intensity, rate and TTL | R1 releases a complete scheduled envelope or agreed pattern-table mapping; R2 validates and encodes it | R1/H1/R2 |
| Verification evidence format is unspecified | Typed evidence with source frame/time, action correlation, uncertainty and provenance; no generic model Boolean authorizes completion | R1/R3/R2 |
| Continuous microphone input/local stop/interrupt events are unspecified | Single coordinated audio input and local command interface; R3 handles conversation, R1 schedules playback | R1/R3/R2 |
| Camera capabilities/preview output not specified | R1 reports selected lens/format, depth availability, orientation transforms and frame-source handle | R1/R2 |
| UInt16 wire generation across reconnect/relaunch/wrap is unspecified | Agree persistent monotonic session policy or explicit reset/handshake; never reset to zero and assume acceptance | H1/R2 |
| BLE status omits generation and session identity | Agree how current-generation stop synchronization is verified; lastSeq alone is not proof of all lifecycle state | H1/R2 |
| Pre-task records require taskId | Agree session-level sentinel/optional encoding; keep internal session context separate until resolved | All |

Example interaction: R2 starts hand-guidance request `g-42` for target `button-up`, session `s-1`, task `t-1`, generation 8. R1 returns `on_target` correlated to `g-42`. R2 requests a press. After a Stop increments generation to 9, a delayed response for `g-42` or generation 8 is rejected. An earlier approach completion must never be misread as hand alignment.

## 8. Task execution engine

### 8.1 Architecture

Implement explicit task states and events, ideally a pure reducer/transition function that emits effects. An actor or serialized coordinator executes effects. Views render state and send user actions; they do not contain task logic.

One physical task is active at a time. A new request cancels or explicitly replaces the previous task with generation invalidation and Stop before changing targets. Informational conversation can coexist only when R1's scheduler permits it.

Typical physical flow:

`resolve → approach if needed → guide hand → request user action → verify → complete/retry/fail`

Do not treat `on_target` as a completed press, grasp, tap, or door opening. R1 stops hand-guidance movement at `on_target`; R2 then requests the action and verifies the outcome.

Each transition validates current session/task/generation, request ID, target identity, evidence freshness appropriate to the action, and expected prior state. Deduplicate events. Stale requests, malformed tool arguments, or unsupported task types produce explicit rejection, not a guessed operation.

Fresh physical cues require evidence no older than 300 ms by default, checked at dispatch, not only when a cloud request started. Historical before/after evidence may support verification, but may not authorize a new physical cue. A cloud-resolved target must be reacquired/currently tracked by R1 before guidance begins; do not falsify its timestamp to make it look fresh.

### 8.2 Success and confirmation rules

- Observed completion needs action-specific evidence and provenance. An LLM saying "probably pressed" is not a press verifier.
- If no implemented observation verifier exists, ask a clear task-specific question and record `user_confirmed` only for an explicit answer to that pending question.
- Bind confirmation to task, generation, step, and a unique prompt ID. Clear it on interruption, target change, timeout, and Stop. A generic "yes" from a prior conversation cannot complete the next step.
- Lack of evidence results in uncertainty, retry, or `unverified`, never automatic success.
- Timers may detect timeout/failure; they never advance a step to success.
- Default maximum retries: 3, configurable. Define and test whether this means retries after the initial attempt; use 3 retries after the initial attempt consistently. Exhaustion ends the task with an explanation and an offer to ask someone nearby for help.
- Pattern 7, success verified, is requested through R1 only after observed or explicit user-confirmed completion, with the verification type retained in logs. Conversation success text must make user confirmation distinguishable when relevant.

### 8.3 BTN button task

1. Resolve requested label and bind it to a specific region/target.
2. Reject unlabeled, ambiguous, or emergency controls. Ask for clarification rather than selecting the nearest plausible button.
3. If needed, request R1 approach; wait for that request's completion.
4. Request hand guidance using the persisted guiding hand.
5. On hand `on_target`, request "Press the button now" through R1.
6. Verify an appropriate observed response, such as an implemented indicator/display change detector, or ask the user to confirm. Mere overlap of hand and button is not sufficient.
7. Complete, reacquire and retry, or fail with a clear reason.

### 8.4 OBJ object task

1. Resolve the named object; disambiguate multiple matches.
2. Approach only through R1's validated standing-position guidance.
3. Guide the hand, then request grasp.
4. Confirm with implemented object/hand co-motion evidence or explicit user confirmation. Occlusion or disappearance alone is not a grasp.
5. If object/hand leaves view, stop and reacquire. A spoken "last seen" location is historical context, never a new movement instruction.
6. Have R1 speak a caution for identified hot, sharp, or fragile items. Do not test with these items in the hackathon demo.

### 8.5 MNU kiosk task

`locate screen → read → speak short option groups → user choice → resolve control → approach if needed → guide → request light tap → re-read → verify transition → repeat → total → payment boundary`

- R3 owns menu understanding and `ScreenModel`; R2 owns progress and action eligibility.
- Bind each selected control to its source screen/frame. After every tap or detected screen change, invalidate old target coordinates, stop hand guidance, and read the new screen before another tap.
- If the screen changes mid-guidance, cancel the stale target immediately. Do not finish a tap based on the old layout.
- Ambiguous choices require clarification. Keep options in short spoken chunks with Repeat/More/Back as conversational actions.
- Wrong destination: describe the observed screen, then resolve a current, identifiable Back control and guide it. Never blindly tap a remembered Back coordinate.
- Welcome screen reappears: report `KIOSK_RESET`, stop, and offer to restart after confirmation.
- Read the total only when actually available. Missing/unclear prices are unknown.
- Stop before payment or final purchase authorization. Do not guide Pay/Confirm purchase merely because the screen is not marked `isPaymentScreen`; ambiguous checkout controls require clarification and conservative stop. No automatic order submission that creates a financial commitment.
- Report `PAYMENT_BOUNDARY` as an intentional handoff, not a payment success. The user or staff handles payment independently.

### 8.6 MNU wall or printed menu

R2 starts the read-only interaction and routes R3's sections/options to R1 speech. Support questions, repeat, and next section. Blurry/cut-off content is reported; do not invent items or prices. No hand-guidance operation is necessary for a read-only request.

### 8.7 DOR door task

`identify door → resolve handle/opener → determine action → approach if needed → guide → request action → verify opening → verify traversable path → passage instruction`

- Supported actions: automatic door, opener button, push, pull, lever.
- Push/pull requires signage or clear evidence; otherwise ask. Never guide toward the hinge side.
- A hand reaching a handle is not evidence the door is open. A user confirming an action is not evidence that the path is clear.
- Locked, heavy, or blocked door: stop and explain; ask for help rather than applying repeated force.
- R2 may coordinate an opening verification, but R1 alone evaluates current hazards/free space and authorizes movement. Do not let R2/R3 independently say "walk through." Ask R1 for its validated passage cue only after the opening requirement is satisfied.

### 8.8 APR approach integration

R2 requests approach to a bound target when necessary and waits for the matching response before hand guidance. R1 chooses the observed-free-space standing position, approximately arm's length away, and handles movement cues. The MVP is approach to a visible target no more than 5 m away, not route planning.

Unknown depth is not free space. Suspected steps/drop-offs stop approach. Stairs, escalators, outdoor navigation, elevator riding, floor plans, shared maps, and QR menus on the user's phone are out of scope.

## 9. Wristband link and calibration

### 9.1 Connection behavior

Implement HapticLink as a real CoreBluetooth central with an injectable transport for tests. Track central authorization/power and per-band discovery, connecting, service discovery, ready, reconnecting, disconnected, and fault states.

- Consume H1's service/characteristic UUIDs from configuration once provided. Missing UUIDs disable live discovery with an actionable diagnostic; do not invent values.
- Discover intended devices by confirmed service and identity. Names `HG-L` and `HG-R` are hints, not proof of side. Persist confirmed peripheral identity/side and support forgetting/re-pairing.
- Prevent a peripheral from occupying both sides, duplicate-side assignment, or silent side swaps. Pair one at a time if necessary and use the left band's tactile marker plus scheduled calibration to confirm.
- Validate characteristic properties for command writes and status notifications before marking ready.
- Reconnect known bands using bounded, cancellable attempts. Rediscover/validate after reconnect. Do not play a cached cue when a link returns.
- If either band drops during bilateral guidance, stop both and pause. Offer an explicit speech-only fallback when R1 supports it; do not silently remap bilateral patterns onto one band.
- Log status battery/firmware/faults and last sequence. Do not invent a low-battery percentage threshold; use the firmware fault or an agreed setting.

### 9.2 Baseline BLE packet

Little-endian command, **10 bytes**:

| Byte offset | Field | Type |
|---|---|---|
| 0 | ver | UInt8 |
| 1–2 | seq | UInt16 |
| 3–4 | generation | UInt16 |
| 5 | patternId | UInt8 |
| 6 | intensity | UInt8 |
| 7 | rateHz | UInt8 |
| 8–9 | ttlMs | UInt16 |

Encode exactly offsets 0–9, with no padding or reserved byte. Unit tests must assert 10 bytes. Serialize field by field, never by copying Swift struct memory.

`patternId = 0` means ALL_STOP; `patternId = 255` means heartbeat. Protocol version, intensity/rate semantics, and heartbeat values must match H1's confirmed implementation. The application schema version and firmware packet version are separate values.

Status notification, **5 bytes**: `batteryPct UInt8 | fwVersion UInt8 | lastSeq UInt16 | faults UInt8`. Fault bit meanings are LINK_STALE, DUTY_LIMIT, LOW_BATTERY; exact bit assignments require H1 confirmation if absent from the contract. Reject malformed length/version and record a protocol error.

Send heartbeat at least 5 Hz while guiding, targeting a 200 ms period. It must not re-trigger an expired pattern. Firmware must stop output on TTL expiry, 500 ms without heartbeat, disconnect, or its duty limit; maximum continuous on-time is 2 s per the baseline config. R2 cannot guarantee execution of a final packet on force-quit.

Respect `canSendWriteWithoutResponse` and its readiness callback. Use bounded queues, replace superseded movement cues with the latest eligible cue, expire stale entries, and prioritize ALL_STOP over everything. Do not keep a FIFO backlog of obsolete guidance. BLE write completion/enqueue and `lastSeq` are not measured actuator onset; H1 measures onset separately.

### 9.3 Patterns and freshness

| ID | Meaning | Band behavior from baseline |
|---|---|---|
| 1 | Move left | Left short pulses |
| 2 | Move right | Right short pulses |
| 3 | Move up | Both short-long |
| 4 | Move down | Both long-short |
| 5 | Closer | Both increasing pulse rate with valid depth |
| 6 | On target, press/grasp | Both one 600 ms buzz |
| 7 | Success verified | Both three quick pulses |
| 8 | Stop/hazard | Both rapid pulses, then silence |
| 9 | Lost target/hand | Both two slow pulses |

H1's pattern file is authoritative for timings. R1 selects/schedules patterns and resolves which band receives each command; HapticLink encodes the agreed envelope. Do not duplicate the guidance controller in Bluetooth code.

Every movement cue needs valid generation, fresh evidence, unexpired release, and a ready link at dispatch. Bound its remaining TTL to the remaining eligible evidence window. Heartbeats do not make old evidence fresh. Ensure the nonparticipating band does not continue an obsolete directional pattern when direction changes.

Pattern 6 lasts 600 ms in the baseline while movement evidence freshness is 300 ms. Treat it as a finite action-ready notification, not permission to continue directional/depth movement. R1/H1 must agree this distinction and its stop behavior. All notifications remain immediately cancellable by ALL_STOP. Do not silently shorten the shared pattern or extend the freshness bound to hide the mismatch.

Pattern 8 and 9 notifications may only be emitted by R1 after movement is stopped, with bounded duration in the current generation. They must never delay ALL_STOP or restart the cancelled movement. If this sequence is not supported by firmware, use local spoken Stop and keep bands silent until the contract is resolved.

### 9.4 Generation handling

The firmware ignores a command whose generation is lower than its last ALL_STOP. Implement sequence/generation bounds explicitly and test reconnect, app relaunch, firmware reboot, and UInt16 exhaustion. Do not wrap with `&+` or reset generation on relaunch unless the confirmed firmware reset/session policy makes it safe.

When synchronization is unknown, keep output disabled, attempt the agreed handshake/reset procedure, and report the blocker. Do not interpret a successful BLE connection as generation synchronization. H1 must specify how state survives disconnect/reboot and how both bands are synchronized; status currently lacks generation acknowledgment.

### 9.5 Calibration

Use a dedicated calibration state, with physical task guidance suspended. Explain each pattern through R1, request its scheduled playback, and ask the user to identify the side/meaning. Test both individual sides and all nine patterns. Repeat misunderstood patterns and allow Stop at any time.

Persist side mapping and calibration completion only after explicit responses; record phone mount/hand settings. Re-pairing or changing pattern firmware should invalidate affected calibration. Never display real connection/calibration success when using a simulated band. Mock playback is visibly and audibly labeled SIMULATED.

## 10. Safety, prioritization, and evidence

Preserve all baseline invariants:

1. No hand/movement cue from evidence older than 300 ms by default.
2. Target/hand out of view stops guidance; never extrapolate a movement target.
3. Tracking loss, Stop, or Pause invalidates generation, cancels work, sends ALL_STOP, and requests local spoken Stop without cloud dependence.
4. Task completion requires observed evidence or explicit user confirmation, identified in results. Timers and hand overlap are insufficient.
5. Missing/low-confidence depth means unknown, not free space.
6. Models can propose targets and interpret speech; they cannot dispatch haptics, confirm a press/payment, clear hazards, or authorize movement.
7. Refuse unlabeled, ambiguous, and emergency controls; never complete payment.
8. Hazards preempt all work.
9. Firmware independently enforces TTL, heartbeat timeout, disconnect, and duty limits.

R1's priority order is `0 stop/hazard → 1 lost → 2 hand guidance → 3 approach → 4 conversation`. R2 must never bypass it to speak a menu item or play a success pattern. After interruption, regenerate eligible cues; do not resume a queue of stale audio/haptics.

R3's baseline tools are `resolve_target`, `read_screen_model`, `choose_control`, `begin_task`, `get_task_status`, and `request_user_confirmation`. R2 validates calls against session, task, permissions, evidence, and current state. Cloud task requests time out at 5 seconds by default; this is not an idle timeout for the entire live session.

## 11. Configuration, persistence, and diagnostics

Keep configurable values in typed config with validation. Defined defaults: evidence age 300 ms, maximum 3 retries after initial attempt, cloud task timeout 5 s, heartbeat interval 200 ms, firmware heartbeat loss 500 ms, firmware continuous-on cap 2 s. The last two are H1 guarantees to verify, not settings R2 can unilaterally change.

External TBDs: BLE UUIDs, protocol version, exact pattern timing/intensity/rate semantics, generation reset policy, board/actuator identity, actual R1/R3 adapter signatures, voice input/interrupt integration, actual camera/depth capabilities, backend endpoint/auth configuration, test phone and Xcode version.

Persist only necessary preferences and pairing metadata. Keep active task state ephemeral; do not automatically resume a physical task after process death. Persist generation state only according to the agreed firmware/session policy.

Record structured events with session/task/generation/request IDs, monotonic timestamps, frame IDs, transitions, evidence/verification type, selected camera mode, band side/link status, queue drops, cancellations, and errors. Keep a bounded in-memory diagnostic buffer and an explicit developer export. No raw camera/audio recording by default; avoid transcript and sensitive scene content in routine logs. Never log tokens/keys.

Performance targets to measure, not claims: frame-to-band dispatch p95 ≤150 ms; local Stop ≤250 ms; band self-stop ≤500 ms. Measure on the real phone and firmware. Separate app dispatch time from hardware onset and never subtract server timestamps from device monotonic time.

## 12. Mock mode and integration availability

Provide explicit dependency injection for real, fixture, and failure-injection implementations. Mock mode must run the shell and task flow without teammate modules, bands, provider keys, or LiDAR. It must not silently activate in a real session when an integration fails.

Mocks must include fresh/stale frame events, approach/hand correlation, tracking loss, target disappearance, screen changes, evidence outcomes, disconnects, delayed provider results, malformed BLE status, and failed generation synchronization. Use a controllable clock for timeouts and queue expiry; avoid sleep-driven tests.

Display a persistent SIMULATED label and announce it when entering mock mode. Mock band status must never say a real band is connected. Release/live composition must fail clearly for missing dependencies; a passing demo fixture is not live integration completion.

## 13. Build sequence and deliverables

### Milestone A: project and first integration

1. Audit current repository, instructions, R1/H1/R3 interfaces, and available Xcode/toolchain.
2. Create the native project, local packages, shared scheme, dependency composition, and accessible Home/Session views.
3. Implement the local idempotent Stop path, explicit mock mode, and generation cancellation.
4. Consume one real R1 frame/status stream if available; transmit one scheduler-approved pattern to one confirmed band if available.
5. Run a fresh-clone project build on macOS; record missing external dependencies separately.

### Milestone B: complete one button task

Build BTN end to end: spoken intent → target → optional approach → hand guidance → action request → verification → result. Implement real HapticLink discovery/status/heartbeat/stop. Verify stale callbacks cannot complete the cancelled task. Integrate early; do not wait for all screens to be polished.

### Milestone C: all R2 flows

Add OBJ, kiosk and wall-menu orchestration, DOR, band pairing/calibration, settings, recovery, voice interruption, and first-time setup. Preserve the same shared guidance/verification/cancellation machinery across tasks.

### Milestone D: real-device acceptance and handoff

Run the tests below with teammates. Fix failures or document precise blockers. Complete `ios/README.md` and `ios/R2_HANDOFF.md`. Integrate at least every two hours according to the team's agreed Git workflow. Avoid force-pushing or replacing teammate work.

## 14. Verification and acceptance criteria

### 14.1 Automated logic and transport tests

Use XCTest or the team's established test framework. Cover meaningful behaviors, not only property assignments:

- Every physical task's valid success path, explicit-confirmation path, uncertainty, cancellation, and retry exhaustion.
- `on_target` does not complete an action; approach completion cannot become hand completion.
- Out-of-order, duplicated, expired, wrong-task and old-generation events cannot advance state or dispatch output.
- Stop during an in-flight request causes late response rejection; repeated Stop/End is safe.
- Confirmation applies only to its current pending question, never after Stop or a target change.
- Kiosk change invalidates old regions; wrong-tap recovery re-resolves Back; reset is detected; payment boundary rejects further taps.
- Door opening confirmation alone cannot authorize passage; current R1 clearance is required.
- Missing depth prevents proximity/approach commands, while allowed image-plane guidance remains explicitly differentiated.
- Exact 10-byte command serialization and 5-byte status parsing, byte order, bounds, reserved pattern IDs, malformed input, and no native-memory padding.
- BLE backpressure drops expired/superseded cues; ALL_STOP preempts queued cues; heartbeat cannot revive expired movement.
- Disconnect, permission denial, Bluetooth off, generation mismatch/wrap/relaunch, and reconnect do not resume stale output.
- Session background/interruption/microphone mute invalidates movement; UI End remains responsive during provider timeout/reconnect.
- Bounded queues and subscription cleanup across repeated Start/End cycles.

### 14.2 UI and accessibility checks

Automate stable accessibility identifiers for Start, Pause/Resume, End, Settings, pairing, and calibration. Test permission-denied/recovery states and no duplicate Start sessions. Manually verify on iPhone with VoiceOver, largest practical accessibility text, light/dark mode, and Reduce Motion.

A blind or low-vision user must be able to start, hear readiness, request a task, answer clarification, pause, resume, and end without reading the screen. Test VoiceOver traversal while app speech is active; do not infer accessibility from labels alone.

### 14.3 Real-device tests

Run each applicable test at least three times per phone. Log phone model/iOS, app and firmware revisions, actuator type, test ID, outcome, verification source, and latency. Fixtures and simulator outcomes are recorded separately.

| Test | Required result | Owners |
|---|---|---|
| T01 Band link | Both confirmed sides connect; all nine patterns play; dispatch/onset measured separately | R2/H1 |
| T02 Failsafe | App kill, out-of-range, or missing heartbeat silences both within firmware limit; stale generations rejected | H1 with R2 harness |
| T03 Calibration | User identifies left/right and pattern meanings; errors can be repeated; Stop works | R2/H1 |
| T04 Hand guidance | Rotation does not reverse cues; occlusion stops guidance | R1 with R2 integration |
| T05 Button | Correct button action verified; ambiguous/emergency rejected | R2/R3 |
| T06 Object | Target reacquired after leaving view; grasp has valid evidence/confirmation | R2/R3 |
| T07 Kiosk | Two-item sequence, wrong tap recovery, reset handling, payment stop | R2/R3 |
| T08 Wall menu | Spoken items/prices match known content; unreadable text reported | R3 with R2 orchestration |
| T09 Door | Action/opening verified; movement requires current R1 clearance; locked door pauses | R2/R1 |
| T10 Approach | Visible target at 3–5 m reached; soft obstacle stops movement cues | R1 with R2 integration |
| T11 Tracking loss | Cover camera: existing guidance speech and band movement stop; local Stop message allowed; no stale replay on recovery | R1/R2 |
| T12 Priority | Hazard interrupts speech/guidance and clears movement output | R1/R2 |
| T13 Offline | Local Stop works; supported local guidance works with fresh evidence; unavailable cloud reads explained | All |
| T14 Endurance | 20-minute session with both bands; no crash, growing queues, or leaked work after End | All |
| R2-A Start/end | One activation starts an honest live session; End stops bands and releases streams/resources | R2 |
| R2-B Camera | Rear widest compatible lens selected automatically; fallback logged; full field/coordinates aligned | R2/R1 |
| R2-C Accessibility | Core flow completed using VoiceOver/voice without visual task selection | R2 |
| R2-D Interruption | Lock/background/audio interruption/mic mute stops guidance; return does not silently resume | R2/R1 |
| R2-E Race | Stop while cloud verification is pending; its delayed result produces no success cue | R2/R3 |
| R2-F Fresh clone | Open committed project, select signing team/device, build and run without source/project reconstruction | R2 |

Test progression: fixtures → sighted teammate → blindfolded teammate with a spotter in a controlled setting. No hot liquids, sharp objects, real payments, stairs, or unsupervised navigation tests. Do not claim testing with blind users unless it actually occurred with consent.

### 14.4 Xcode validation

Run on macOS with Xcode installed. Substitute a simulator/device actually listed by the toolchain, not a hard-coded unverified phone name.

```bash
xcodebuild -list -project ios/Reflex.xcodeproj
xcodebuild -showdestinations -project ios/Reflex.xcodeproj -scheme Reflex
xcodebuild -project ios/Reflex.xcodeproj -scheme Reflex -configuration Debug -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO build
xcodebuild -project ios/Reflex.xcodeproj -scheme Reflex -destination 'platform=iOS Simulator,id=<available-simulator-id>' test
xcodebuild -project ios/Reflex.xcodeproj -scheme Reflex -configuration Release -sdk iphoneos -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO build
```

The unsigned device build checks compilation only. Select the development team and actual phone in Xcode for signing/install/run. Validate physical camera, LiDAR, audio, and BLE on hardware. If working from Linux/Windows or a machine without Xcode, produce the source/project and run only supported checks; explicitly mark Xcode/device validation unrun, not passed.

## 15. Required implementation handoff

`ios/README.md` must tell a teammate how to edit in VS Code, open the project in Xcode, select the scheme/signing team/device, supply nonsecret configuration, run tests, select explicit mock mode, and integrate live adapters.

`ios/R2_HANDOFF.md` must include:

- Implemented files and final package dependency graph.
- Actual deployment target, language mode, and validated Xcode/phone versions.
- A status matrix distinguishing implemented, fixture-tested, simulator-tested, real-device-tested, and blocked.
- Exact adapter methods/events mapped to R1/R3/H1; unresolved contract proposals with example messages.
- BLE UUID/pattern/generation synchronization status and actual camera/depth capability results.
- Tests run, failures, latency measurements, unrun device checks, and remaining blockers.
- Confirmation that no second camera, competing app speech engine, direct model-to-haptic path, or provider secret was added.

Completion means all R2 code and project wiring are delivered and available integrations are exercised. If R1/H1/R3 are incomplete, finish the independent R2 work and leave narrow, documented integration seams. Do not declare the complete live product tested when dependencies remain mocked.

## 16. Platform references

Consult the current Apple documentation and installed SDK when implementing availability-sensitive APIs:

- [ARKit configuration and camera ownership](https://developer.apple.com/documentation/arkit/arconfiguration)
- [Supported ARKit video formats](https://developer.apple.com/documentation/arkit/arconfiguration/supportedvideoformats)
- [Video format capture device type](https://developer.apple.com/documentation/arkit/arconfiguration/videoformat-swift.class/capturedevicetype)
- [ARKit scene depth](https://developer.apple.com/documentation/arkit/arconfiguration/framesemantics-swift.struct/scenedepth)
- [Ultra-wide camera device type](https://developer.apple.com/documentation/avfoundation/avcapturedevice/devicetype-swift.struct/builtinultrawidecamera)
- [CoreBluetooth write readiness](https://developer.apple.com/documentation/corebluetooth/cbperipheral/cansendwritewithoutresponse)
- [Protected resource permission keys](https://developer.apple.com/documentation/bundleresources/protected-resources)

Implement R2 against this specification now. Preserve teammate ownership, keep the normal flow voice-first, and make the final project directly usable from Xcode.
