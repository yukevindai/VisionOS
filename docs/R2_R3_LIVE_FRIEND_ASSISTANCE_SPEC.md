# Reflex Combined R2 and R3 Implementation Spec

Version 1.0 — October 24, 2026

Owner: Kevin, taking responsibility for both R2 and R3. Repository: `yukevindai/VisionOS`. Product: Reflex, a native iPhone app. Suggested repository destination: `docs/R2_R3_LIVE_FRIEND_ASSISTANCE_SPEC.md`.

## 1. Assignment and confirmed decisions

Complete the existing R2 and R3 responsibilities and add live friend assistance. A blind or low-vision user starts an ordinary Reflex assistance session. Previously authorized friends receive a notification and can choose to join immediately. They see the user's live rear-camera video, speak with the user, and tap or click an object or control to propose a guidance target. Reflex interprets the selection, asks the user for spoken confirmation, reacquires the target in the current scene, and executes the existing task and guidance flow.

This is a real live audio/video call inside Reflex, similar in interaction to a FaceTime call. It is not Apple FaceTime integration, screen scraping, a sequence of uploaded photos, or a request-for-help ticket. A selection may carry a captured frame internally for accurate interpretation; the call continues live.

The following choices are confirmed by the user:

| Area | Decision |
|---|---|
| Ownership | Kevin owns R2 and R3; R1 and H1 retain their existing responsibilities. |
| Voice agent | Cartesia for streaming speech recognition and synthesized speech. |
| Visual interpretation | Gemini for scene, object, label, screen, and menu interpretation. |
| Orchestration | Anthropic Claude for conversation, intent resolution, and validated tool calls. |
| Backend | Convex for durable application data, authorization, reactive state, and scheduled work. |
| Calls | LiveKit for live audio/video rooms and participant transport. |
| Friend clients | Both a browser client and a native iOS friend mode. Neither is an optional replacement for the other. |
| Joining | Trusted friends can join an enabled session without a new invitation or per-session approval. |
| Guidance | A friend's click requires the assisted user's spoken confirmation before changing physical guidance. |

Implementation defaults, adjustable without changing the product: one active helper at a time; other trusted friends see “A friend is already assisting”; helper camera optional and off initially; helper microphone enabled after join permission; no recording by default; English first; initial physical demo uses a stationary cup and a printed non-emergency button. Both clients support the complete join/talk/click/leave flow.

## 2. Existing baseline and precedence

The reference branch head observed for this specification is `0e869b1c293a12f974c9d55010fc401530f0c009`. Reinspect the actual branch and local changes before implementing; teammates are building concurrently.

Read `AGENTS.md`, `docs/REPOSITORY_STRUCTURE.md`, `docs/Cal_Hacks_13_Build_Spec_v3.md`, `docs/R2_IOS_IMPLEMENTATION_SPEC.md`, `ios/README.md`, and the relevant validation files. Use actual current source as the integration baseline. The old R2 spec's statement that the repo only contains README/LICENSE is historical and no longer describes the project.

Existing assets to extend:

- Native SwiftUI app and committed `ios/Reflex.xcodeproj`, with a shared Reflex scheme and project-generation script.
- `Tasks` deterministic task engine and `HapticLink` Bluetooth package, with tests.
- `SessionCoordinator`, adapter protocols, permission and preference handling, explicit simulation, and bounded asynchronous work.
- `LiveIntegrationBundle` with perception, speech, conversation, evidence and frame policy. Its optional `LiveBandIntegrationBundle` allows operation without inventing wristband configuration.
- Native adapter acceptance, Bluetooth, accessibility, and team-sync validation documents; preserve reported failures and unrun checks.
- R1's Python research harness at `ios/Packages/Perception/Prototypes/DeskGuide-Laptop/`. It is not a native Swift implementation or proof of a working iPhone perception pipeline.

This spec supersedes older R2/R3 requirements only for combined ownership, provider choices, actual human calling, authenticated online sessions, new friend clients, and notifications. It replaces the old “no human call/WebRTC required” assumption. Existing task behavior and safety invariants remain required. Do not rebuild completed modules or silently narrow the existing MVP to friend assistance alone.

Do not modify R1 or H1 modules unilaterally. Specify required adapter changes with examples and coordinate their implementation. Local `ios/R2_RELAY.md` and `ios/R2_HANDOFF.md` remain ignored and local-only; never commit, force-add, delete, or publish them. Public implementation status goes in `ios/Validation/` and project READMEs.

## 3. Ownership and deliverables

| Owner | Required work |
|---|---|
| Kevin, R2 | Native app shell, setup/accessibility, session coordinator, all task state machines, optional wristband link/calibration, native friend mode, call UI, permissions, notifications, deep links, integration and Xcode wiring. |
| Kevin, R3 | Cartesia/Gemini/Claude adapters; OCR and structured targets; backend/auth; trusted friendships; Convex data/functions; notification dispatch; LiveKit tokens/room lifecycle; realtime worker; browser friend client; sponsor evidence and deployment instructions. |
| R1 | Single camera/AR session, frames and transforms, depth provenance, hand/target tracking, hazards/free space, approach/hand guidance, audio session and final output scheduling, offline Stop detection. |
| H1 | Actual board and actuators, firmware, BLE contract and independent output failsafe. |

Suggested new paths, reusing equivalent existing files where present:

| Path | Contents |
|---|---|
| `ios/App/Friends/` | Friend settings, trusted list, native helper screens, availability and membership. |
| `ios/App/Calling/` | LiveKit lifecycle, externally supplied video, remote audio gate, shared audio integration. |
| `ios/App/Notifications/` | APNs registration and notification/deep-link handling. |
| `ios/Packages/Targets/` | Typed target interpretation client and schema mapping; no secret provider credentials. |
| `backend/convex/` | Schemas, access checks, queries/mutations/actions, notification outbox, token minting and webhooks. |
| `backend/agent/` | Python realtime worker, Cartesia voice pipeline, Claude orchestration, Gemini tools, scoped session transport. |
| `apps/friend-web/` | React/TypeScript browser helper with LiveKit, Convex subscriptions, and Web Push. |
| `contracts/friend_assistance.v1.json` | New cross-client event schemas and examples; keep existing task schema v3 distinct. |
| `contracts/friend_assistance.md` | Roles, clock domains, coordinate mapping, transitions, authorization and R1 adapter agreement. |
| `fixtures/friend_assistance/` | Synthetic scenes and transformations, stale/reordered events, consent and interruption cases. |

Deliver a directly openable native Xcode project, browser source/build configuration, Convex functions, runnable realtime worker, pinned dependencies, example nonsecret environment files, meaningful tests, deployment/run instructions, and a status matrix separating simulation from real-device validation.

## 4. Architecture and provider separation

Convex remains the application backend. LiveKit carries media. A small long-running worker executes the streaming voice agent and model calls. A continuous call must not live inside a Convex query/mutation or a single never-ending action. Convex functions have runtime limits [S4]. No second database is required.

| Component | Responsibilities | Must not do |
|---|---|---|
| Cartesia | Streaming STT, turn boundaries where supported, TTS for dialogue/menu/selection announcements. | Decide physical movement, own task state, or bypass local Stop. |
| Claude | Interpret requests, ask clarifying questions, call allowed tools, explain verified outcomes. | Invent success, directly write BLE, declare free space, or execute arbitrary code. |
| Gemini | Read images/OCR context; propose labels, bounding regions, controls and menu structure; identify a friend's selected object. | Supply trusted depth, silently confirm a press, or authorize passage. |
| Convex | Identity mapping, friendship grants, sessions, helper leases, selections/status, task summaries, notification outbox and authorization. | Transport the continuous video/audio stream or run the physical control loop. |
| LiveKit | Low-latency human audio/video, participant presence, transport for scoped data. | Replace Convex authorization or let a generic client become a room administrator. |
| Realtime worker | Own one voice-agent session per owner session; subscribe to owner microphone; invoke Cartesia/Claude/Gemini; deliver typed proposals and scheduled TTS. | Treat a friend's voice as the owner's consent or replace R2's authoritative task engine. |
| iPhone R2/R1 | Authoritative local physical task state, fresh perception, confirmation, final output scheduling and Stop. | Wait for a remote database acknowledgement before stopping. |

Use Cartesia's STT and TTS with Claude as the reasoning component, through the LiveKit Agents provider plugins or an equivalent direct pipeline. This is the requested Cartesia-powered voice agent; do not introduce another hosted agent brain or use Gemini Live as a competing conversational controller. Current Cartesia and Anthropic LiveKit plugins support this composition [S1, S2].

Select available model/voice IDs through configuration and verify account access before pinning them. Do not invent future model names, credentials, quotas or latency claims. Record selected provider models and SDK versions in the implementation handoff.

### Runtime placement

- Convex Cloud: application data and bounded backend operations.
- LiveKit Cloud: managed media rooms and transport; managed service is the default approved choice.
- Realtime worker: Python container process supporting a LiveKit worker plus HTTPS/WSS endpoints for controlled TTS and frame/tool exchange. Use a host supporting long-running connections; its actual deployment hostname is configuration, not fabricated in source. A local worker is acceptable for development, but remote clients require a reachable authenticated endpoint.
- Browser client: HTTPS hosting. Native app: real iPhone build/signing through Xcode.

The worker is execution infrastructure for the chosen stack, not a replacement backend. Its container, health/readiness checks, environment contract and start command are deliverables. Do not add Redis/Postgres or persistent local session storage.

### Existing adapter integration

Keep `SessionCoordinator` and the current `LiveIntegrationBundle` as the native composition boundary. Implement `ConversationClient` with the authenticated worker connection: start/end session, synchronize authoritative context, send current frames, mute input and interrupt generation. Implement `TargetEvidenceClient` as correlated typed worker requests for resolve/read/answer/verify operations; cancel propagates to worker requests and invalidates late replies locally. Worker tool proposals return through this bridge to R2's validator, never directly to a guidance method.

Extend `SpeechSchedulerClient` only by agreement with R1 to carry utterance/generation metadata, streaming chunks, playback completion and immediate cancellation. Use `PerceptionGuidanceClient` for current frames, reacquisition and validated guidance; introduce an agreed external-video buffer tap instead of another camera session. The private WSS protocol multiplexes typed context/tool/result/cancellation messages, bounded image payloads and audio envelopes with independent request IDs and limits. Keep its status separate from Convex projection readiness. Preserve optional `LiveBandIntegrationBundle`; none of these changes makes bands mandatory.

## 5. Trust, identity and automatic session sharing

Preserve immediate local assistance without sign-in. Online AI uses an authenticated account session but requires no friend setup or sharing permission. Persist sign-in so future starts do not require repeated login. If authentication is unavailable, expose supported local capabilities and explain that online AI is unavailable; do not mint unauthenticated provider/media credentials.

Default identity integration: Auth0, using its native authorization flow and browser flow with Convex's supported JWT integration [S3]. If the repository already has a working identity provider supporting both clients, retain it instead. Configure actual issuer/audiences/redirect URIs; never accept a client-supplied user ID as authentication. This supporting identity choice does not change the four selected application providers.

Friend onboarding:

1. Owner creates a time-limited, single-use invitation link or pairing code.
2. Friend signs in and redeems the invitation. If it was bound to a specific existing account, enforce that identity; otherwise the owner approves the displayed account once before activating the friendship. Possession of a forwarded invitation alone never establishes trusted access.
3. Owner enables “Let trusted friends join my assistance sessions” and understands that this shares camera and microphone during enabled sessions.
4. Friend chooses whether to receive session notifications and grants OS/browser permission.

An active directional friendship grant stores who may join whose sessions. It does not implicitly grant reciprocal access. No address-book import or public directory is required. A forwarded link alone must never permit an untrusted account to watch an active session.

Starting an enabled session announces “Friend sharing is on. Your trusted friends can join.” Friends can then join without another owner approval. Announce actual join/leave events once. Display an accessible participant list and sharing status. One-time sharing authorization is distinct from confirmation of each new physical target.

Owner controls: stop sharing for this session; disable future automatic sharing; remove current helper; revoke friendship; mute incoming friend audio; end assistance. Stop sharing leaves AI assistance available. Session-specific removal blocks rejoining that session until the owner explicitly restores access; friendship revocation blocks future sessions too.

## 6. Session start and call experience

### Assisted user's iPhone

Retain the voice-first Start/Pause/End experience and existing setup for dominant hand, phone mount and optional bands. No visual task picker is required.

Start sequence:

1. Serialize Start; create a new local session and task-generation context.
2. Start R1 perception/audio and validate local Stop readiness. Compose R2/R3 adapters; bands may be absent.
3. For authenticated online assistance, create a Convex session and private owner-plus-worker LiveKit room regardless of the sharing setting. Obtain scoped media/worker tickets; publish the coordinated owner microphone and send on-demand R1 frames through the worker channel.
4. Connect the voice worker. If sharing is enabled, additionally publish rear video from R1's existing frame source and enable helper eligibility. Mark sharing joinable only when its media is ready; atomically enqueue notifications for eligible friends. AI and sharing readiness remain independent.
5. Announce actual capabilities; missing AI must not be displayed as connected. Turning sharing on/off changes helper access and notifications without recreating the local task or private AI session. Re-enabling sharing in the same session does not repeat the session-start alert.

Maintain separate capabilities for perception, physical guidance, voice agent, media, Convex, helper presence and bands. One failed component must not produce a false all-green state.

Call layout: rear-camera preview, concise status, friend name/avatar or optional video tile, captions, stable large Pause/Resume and End buttons, microphone mute, and Friend sharing controls. Keep provider names and metrics in diagnostics only. Helper video must not require switching the owner's camera away from R1's rear camera.

### Native iOS friend mode

Provide a Friends area in the same app, clearly separated from “Start my assistance.” Show authorized contacts with session availability. An APNs notification opens the specific session after authentication and a fresh join check. Join opens the rear-camera feed, microphone, optional selfie camera, target selection overlay, target/status card, Pause guidance, and Leave.

Joining as a helper must not start the owner's task engine, AR session or wristband controller on the helper's device. A person cannot simultaneously run an owner session and a helper call on the same device in this MVP; explain how to leave the current mode.

### Browser friend client

Provide sign-in, invitation acceptance, authorized active-session list, notification settings, and live call view. Support desktop click and mobile tap. Require a Join gesture before microphone/audio playback, handle browser autoplay constraints, and use HTTPS. Helpers can watch/select without granting microphone or camera permissions; explain listen-only operation.

Both helper clients show connecting, live, delayed/frozen, reconnecting, session ended, revoked, busy, selection resolving, waiting for user confirmation, guiding, paused, and completed states. Never show a stationary last frame as unqualified live video.

## 7. Notifications and availability

Implement three distinct delivery paths:

| Path | Required behavior |
|---|---|
| Native APNs | Register per-device tokens, associate with authenticated accounts and environment, send a standard alert on session readiness, and deep-link to the session. Requires actual Apple signing/provisioning and push capability. |
| Browser Web Push | Service worker, push subscription, server VAPID configuration, and notification-click route. On iPhone, supported Home Screen web-app setup and permission are required; an ordinary tab is not sufficient [S5]. |
| Foreground availability | Convex subscription updates the active-session list and in-app alert. This supplements push; it is not proof that background notifications work. |

Notifications are optional join invitations, not incoming phone calls. Do not use PushKit/CallKit to imitate FaceTime ringing for this presence feature. Initial native background/lock stops owner guidance and ends or suspends sharing explicitly; do not assume continuous background camera capture.

Use a durable outbox: unique logical key `(sessionId, friendId, eventType)` plus per-device delivery records. Device-level dispatch is at least once with collapse/deduplication keys, bounded retry and backoff; do not claim exactly-once delivery. Distinguish queued, provider-accepted, failed and app-opened. Delivery timing depends on network, permissions and OS policy.

Before sending or retrying, recheck active session, sharing grant, revocation and expiry. Set expiration to the session's live availability window; on tap, always revalidate because old notifications cannot be reliably recalled. Never put media credentials, private scene descriptions or raw tokens in notifications. Quiet settings and notification permission denial must not block voluntary joining from the active-session list.

Persist session lease updates from the owner with a proposed 5-second heartbeat and 20-second availability timeout. These are presence defaults, not physical safety timers. Expired sessions stop minting join tokens; the worker closes abandoned rooms. A slow notification must not resurrect an ended session.

## 8. LiveKit rooms and media

Use one isolated room per online assistance session. Participants are owner, one voice worker, and at most one helper when sharing is enabled. Private AI sessions have no eligible helpers. Room names are opaque. Credentials are minted server-side only after authentication and current authorization checks. Suggested join-token TTL is 2 minutes; enforce live revocation as well, because expiry alone does not disconnect a connected participant [S6].

Acquire the helper slot with a transactional Convex mutation. Issue a join reservation valid for 15 seconds; after verified room connection, establish a renewable helper lease with a connection identity and lease version. Failed/timed-out joins release their reservation. Do not allow the same account on a second device to silently evict the active connection. Expired lease or leave invalidates pending friend selections; a replacement helper starts with a new lease version.

Grant least privileges: owner publishes rear camera/microphone; helper subscribes and optionally publishes microphone/selfie video; worker subscribes to owner input and exchanges app data. No ordinary client receives room-admin, room-listing or recording grants. Application state changes remain authenticated through Convex or scoped worker endpoints, not arbitrary client-written participant metadata.

Verify LiveKit webhook signatures and event identity. Deduplicate webhook processing; tolerate late/out-of-order join/leave events. Reconcile the actual room membership before an old leave event releases a newer helper lease.

### Camera integration

R1 remains the only owner of `ARSession`/rear-camera capture. Supply its `CVPixelBuffer` frames to a custom LiveKit video source using the pinned Swift SDK's supported buffer-capture API. Do not call the SDK's default camera-enable method on the owner. The helper's optional camera is separate on the helper device.

Preserve R1's widest compatible rear-camera policy; do not force ultra-wide if it breaks registered depth. Normalize orientation once and expose geometry epochs. Lens/format/orientation-transform changes invalidate target geometry. Drop obsolete frames rather than queue them. Start with a proposed 720p/15 fps media budget; measure on the actual phone and adapt down under thermal/network pressure without changing local safety thresholds.

Convex carries low-rate state only. Video and human audio use LiveKit tracks. Do not write JPEG frames, PCM chunks or per-frame hand coordinates into database documents.

## 9. Audio and voice-agent integration

The owner must hear the friend, the AI, and local guidance without overlapping contradictory cues. R1 remains the authority for the iPhone audio session and final playback policy. Agree one microphone-capture owner and expose processed microphone samples to LiveKit and the local Stop detector. If LiveKit supplies the capture engine, R1 explicitly adopts that engine and its tap; never run two competing capture engines.

When R1 configures `AVAudioSession`, disable LiveKit's automatic session configuration through the pinned SDK's supported API and preserve its required mixer wiring. Configure once at startup; avoid repeated category changes that tear down voice processing. Verify that custom TTS participates in the agreed echo-cancellation path and that system ducking cannot hide local warnings [S10]. This audio integration is an initial R1/R2 acceptance gate, not a promise that independent players automatically mix correctly.

Required provider path:

`owner microphone → Cartesia STT → Claude intent/tools → validated result → Cartesia TTS → R1-controlled playback`

Human audio uses LiveKit independently. Configure the worker to process the authenticated owner's microphone track only. It must not automatically select the latest speaker or transcribe the mixed room output. Friend speech remains human conversation; friend labels or clicks are attributed suggestions, not owner confirmations.

### Controlled TTS transport

The baseline design uses a custom worker audio-output adapter: Cartesia PCM is delivered to the owner's authenticated WSS connection with an explicit envelope, then enqueued through R1. Disable automatic agent-audio playout on the owner. This makes cancellation/generation checks independent of a remote jitter buffer and avoids assuming that unlabeled audio-track samples identify their originating task.

Each utterance carries `utteranceId, sessionId, sessionEpoch, taskId?, taskGeneration, requestId, priority, codec, sampleRate, channels, chunkSequence`. Only implement formats the app decoder supports. Bound queued PCM to a configurable small duration; when backpressure exceeds it, cancel the utterance rather than replay seconds of obsolete audio. Control messages precede chunks; reject unknown utterances or invalid format/order. Endpoint/ticket scope binds the connection to one owner session. No secrets appear in a URL.

Cancel/Stop flushes the local queue immediately, marks the utterance cancelled, interrupts upstream generation, and drops late chunks. Reconnecting does not resume a partly heard command. Send actual playback acknowledgement to the worker so it does not assume the user heard cancelled text. Helper clients receive the authorized assistant text/status; synthesizing a second uncontrolled AI speaker into the room is not required.

### Output arbitration

Preserve R1's priority order: `0 Stop/hazard → 1 lost → 2 hand guidance → 3 approach → 4 conversation`. Human friend audio is a conversational source under a local gate. Brief urgent guidance and confirmation prompts duck or mute human audio as appropriate; a hazard/Stop mutes it immediately so the warning is audible. Restore the live edge, never buffered old instructions. Do not let an active-speaker event cancel a local safety warning.

Owner barge-in cancels normal AI speech. Stop/Pause execute locally before notifying cloud services. During a friend conversation the AI stays quiet unless addressed, a task/confirmation requires speech, or safety requires intervention. Use deterministic routing around the active prompt and explicit AI address; do not rely on Claude alone to decide who is being addressed.

Confirmations must be tied to the currently pending prompt and derived from owner input. A separate friend audio track cannot answer. Echo cancellation and remote-audio suppression must be tested so a friend's “yes” played through the speaker is not reaccepted through the microphone. If origin is ambiguous, ask the owner again after silencing remote playback; do not advance the task.

VoiceOver remains available. Avoid duplicate app speech and accessibility announcements. Microphone mute pauses physical guidance because spoken Stop is unavailable; accessible local Stop/End remains. Speaker/earpiece/Bluetooth route changes and interruptions invalidate guidance until explicit recovery. Critical Stop and short guidance cues remain local/offline; ordinary Cartesia speech may fail gracefully when offline.

## 10. Live click to target pipeline

The friend clicks the live video itself. Do not require a separate upload or snapshot-taking step. Treat the click as a target proposal, never a remote movement command or physical tap.

1. Confirm helper membership, live media and current helper lease.
2. Atomically capture the displayed decoded frame and the pointer position relative to its rendered content. Exclude overlays and letterbox bars. Keep video playing; show a brief selection marker and “Finding this target.”
3. Send a compact selection envelope through Convex and its bounded image payload to the authenticated realtime worker. Correlate with a selection ID, digest, geometry and transport epoch. A selection without matching image/context cannot resolve.
4. Gemini identifies the object/control at that point, returning a typed candidate and any ambiguity. A friend-entered label helps interpretation but is not proof of identity or safety.
5. Obtain a current image from the owner's R1 source with its real native frame ID, local capture time and geometry epoch. Gemini can compare the selection image/crop with this current scene; R1 must then reacquire the same target and produce current physical evidence.
6. If ambiguous, missing, unsafe or no longer in view, ask for clarification/reselection. No cue is emitted.
7. When the owner is idle/paused, announce “Alex selected the cup. Shall I guide your hand to it?” and create a scoped confirmation. If another task is active, announce the pending suggestion at a safe conversational opportunity; a click alone does not replace or interrupt its physical target. The friend can explicitly Pause guidance first if needed.
8. After an explicit owner Yes, locally invalidate any prior task, Stop old outputs, establish the new generation/target binding, and reacquire again before starting guidance. The confirmation belongs to this proposal and cannot authorize subsequent selections.
9. R2 runs the appropriate existing task. R1 supplies current guidance. Send honest status to the helper. Completion still requires observed evidence or owner confirmation.

Acceptance is one serialized local transaction: validate `selectionId + promptId + proposalGeneration`, consume that confirmation, invalidate old outputs, and create `SelectionAccepted(selectionId, promptId, proposalGeneration, newTaskId, newTaskGeneration)`. Publish this binding to the worker before accepting tool activity for the new task; project it to Convex with an idempotent event ID. Only the explicitly accepted target identity crosses this boundary. Old-generation audio, model results and other proposals are discarded. Reacquisition uses a new request in the new generation; reconnect cannot replay the acceptance.

One pending proposal per session. Later clicks supersede only an unconfirmed proposal; they never silently change active guidance. Rate-limit clicks, cancel superseded resolution and clear its prompt. Reject a late Yes for the superseded prompt. If a helper disconnects or loses authorization, cancel unconfirmed proposals and pause any task initiated by that helper; owner can explicitly resume through AI after fresh checks.

### Frame identity and delay

Do not assume a data-channel message arrives with the video frame displayed at that moment. Do not stamp a delayed image with “now.” The documented LiveKit frame-metadata support list does not establish native Swift support [S7]. Pin SDKs and verify availability before using native frame metadata.

Baseline cross-platform strategy: store an exact helper-side decoded selection image with a locally minted `selectionFrameId`; this is a reference image, not an R1 AR frame. Send dimensions and the pixel transform alongside it. Resolve object identity from that image, then re-detect/reacquire against a separately identified current R1 frame. Never look up old depth using a helper-generated ID or project the old click directly into current 3D space.

On web, implement selection capture through a tested decoded-frame/canvas render path that binds the displayed image and tap to the same frame. On native helper mode, retain the last rendered frame and its transform on a serialized renderer path. Detect frozen media and disable clicking until live frames return. Tests must cover capture/display races; using an unrelated latest frame is not acceptable.

If a verified SDK path supplies exact source-frame metadata, it may optimize this pipeline, but current reacquisition remains mandatory. A delayed friend selection is valid semantic intent; it is not fresh physical evidence. The 300 ms movement-evidence bound applies to local R1 observations at cue dispatch, not to total video-call latency.

### Coordinate convention

All reference-image coordinates use normalized `(x, y)` with top-left origin after orientation correction. Use aspect-fit by default. For a displayed content rectangle `(left, top, width, height)`, compute `x=(pointerX-left)/width`, `y=(pointerY-top)/height`; reject points outside `[0,1]` rather than clamping. Invert mirroring, rotation, crop or zoom transforms explicitly. Map any downscaled image coordinates through a recorded transform.

Test all four rotations, portrait/landscape layout, letterboxing, retina/browser scaling and native point-versus-pixel units. Do not mix Gemini's model-specific coordinate ordering/range with the app format; convert through a schema adapter and test known boxes [S8].

## 11. Event schemas and authority

New records use `friend_assistance.v1`, separate from existing task schema v3. Internal examples below are proposed application contracts, not provider SDK signatures. JSON numeric generations must remain within the shared safe-integer range; reset via a new session/epoch before overflow. They are not firmware UInt16 generations.

```json
{
  "schemaVersion": "friend_assistance.v1",
  "eventId": "evt-example",
  "sessionId": "session-example",
  "sessionEpoch": "epoch-example",
  "taskGeneration": 8,
  "helperLeaseVersion": 3,
  "selectionId": "selection-example",
  "selectionFrameId": "helper-frame-example",
  "frameOrigin": "helper_decoded_reference",
  "sourceTrackId": "track-example",
  "transportEpoch": "transport-example",
  "image": {
    "width": 1280,
    "height": 720,
    "sha256": "<computed payload digest>",
    "mimeType": "image/jpeg"
  },
  "point": { "x": 0.42, "y": 0.61 },
  "suggestedAction": "pick_up",
  "label": "your cup"
}
```

The server derives sender identity/role from authentication and appends its receive/expiry time. Ignore any body field claiming owner/helper identity. Verify that the media track belongs to the session owner. Limit image payloads to a proposed 1 MiB and supported dimensions/MIME; decode safely and reject malformed content. The worker joins authorized image and metadata arrivals by ID/digest, expiring unmatched halves after 5 seconds.

| Record | Essential fields and authority |
|---|---|
| `CurrentSceneFrame` | Owner-origin frame ID, capture monotonic time, geometry epoch, orientation/transform and image. R1 is the source. |
| `TargetCandidate` | Selection ID, current source frame ID, kind/label/region, ambiguity/unknown fields. Gemini proposes; R2/R1 validate. |
| `SelectionStatus` | Selection ID, lease version, status and reason; authoritative transitions from server/iPhone. |
| `ConfirmationPrompt` | Prompt ID, proposal/target ID, session/task/generation, expiry, requested action. iPhone owns pending state. |
| `OwnerConfirmation` | Prompt ID and answer with owner-input provenance. Only iPhone's accepted owner event can authorize. |
| `ToolInvocation` | Tool-use ID, request ID, session/task/generation, bounded arguments/deadline. Worker proposes; local validator gates effects. |
| `TaskUpdate` | Local task status, target identity, verification source, monotonic sequence. Convex is a projection, not the physical controller. |
| `RemotePause` | Authenticated helper session/lease and event ID. May stop/pause only; cannot resume or acknowledge success. |

Status vocabulary: `submitted → resolving → needs_clarification | awaiting_confirmation → accepted → reacquiring → guiding → awaiting_action → verifying → completed`, with `rejected`, `cancelled`, `expired`, `superseded` and `failed` terminal alternatives. An accepted proposal can still fail reacquisition. Deduplicate on IDs and validate prior state; never infer progression from elapsed time.

Use local monotonic clocks for capture age, cue expiry and Stop latency. Use server timestamps for authorization leases, notification TTL and persisted chronology. Do not subtract unrelated device clocks or trust a friend's clock to certify freshness. Deadline durations from a remote event are capped and started on receipt; capture-time provenance remains R1-owned.

## 12. Convex data model and operations

Proposed tables and indexes:

| Table | Important fields | Required lookup |
|---|---|---|
| `users` | Auth issuer/subject, display name, preferences | Unique issuer plus subject |
| `friendInvites` | Owner, hashed code, expiry, redeemedBy/status | Code hash; owner |
| `friendships` | Owner, helper, accepted/revoked, notify flag | Unique owner/helper; helper |
| `sessions` | Owner, epoch, status, room reference, sharing flag, authorization version, heartbeat, end reason | Owner/status; session ID |
| `sessionParticipants` | Session, account, media identity, join state, connection ID, lease version, expiry, removal | Session/account; session/lease |
| `selections` | Session, helper, proposal metadata, state, current target reference, reason, expiry | Session/state; unique selection ID |
| `taskEvents` | Session, task/generation, sequence, transition, verification type | Session/sequence |
| `notificationEndpoints` | Account, device/subscription, provider/environment, opt-in, invalidatedAt | Account/device |
| `notificationOutbox` | Session/friend/event key, per-device delivery status, attempts, expiry | Status/nextAttempt; logical key |

No raw continuous media is stored. Keep selection/reference images in bounded worker memory and discard on terminal state or within 60 seconds, whichever comes first. If persisted image storage later becomes necessary, add explicit authorization and deletion jobs; do not silently retain it. Proposed default task/event retention is 24 hours for hackathon diagnostics; trusted relationships and preferences persist until removed. Do not log transcripts or scene content by default.

Implement authenticated queries/mutations/actions with Zod/Convex validators as appropriate:

- `friends.createInvite`, `friends.acceptInvite`, `friends.list`, `friends.revoke`, `friends.setNotifications`.
- `sessions.start`, `sessions.markReady`, `sessions.heartbeat`, `sessions.listJoinable`, `sessions.setSharing`, `sessions.end`.
- `assistance.reserveHelper`, `assistance.renewLease`, `assistance.leave`, `assistance.removeHelper`, `assistance.pause`.
- `selections.submit`, `selections.cancel`, `selections.watch`, and owner/worker-authorized state updates.
- `devices.registerPush`, `devices.unregisterPush`; internal dispatch/retry functions.
- `media.issueJoinToken`, `media.issueWorkerTicket`, verified LiveKit webhook ingestion, server-side removal/room teardown.
- Owner-authenticated task projection updates with monotonic sequence checks.

Apply authorization according to caller and operation:

| Caller | Required authority |
|---|---|
| Owner | Authenticated session ownership, matching epoch/state and permitted operation. AI/task operations require neither sharing nor a helper lease. Only the owner may modify trust, confirm physical actions or Resume. |
| Helper, discovery/join | Authenticated accepted friendship, current sharing permission, live owner session and no session-specific removal; atomically acquire a reservation before joining. |
| Helper, connected actions | All join checks plus the active connection identity, helper lease/version and allowed action. A helper can propose or Pause, never confirm or Resume. |
| Worker | Scoped service identity bound to owner/session/epoch and endpoint capability. Helper-related actions also require current sharing/grant/lease; private AI operations do not. |
| Provider webhook | Verified provider signature, deduplicated event identity and reconciliation against current server state; no authority to confirm physical actions. |

Listing sessions must not leak activity of unrelated users. Never put a Convex deployment/admin key into a client. An authenticated backend action issues one-use worker connection tickets with issuer, audience, subject, session/epoch, role, endpoint scopes, unique ID and a proposed 60-second connection deadline. Send the ticket in the authenticated connection handshake, not the URL. Bind the resulting connection to current authorization and revoke it on End; initial ticket validity is not a permanent authorization lease.

Store network side effects outside mutations. A mutation records an intent/outbox row; an action performs provider I/O and records its result. Token issuance must recheck authorization after asynchronous work and compensate for revocation races by removing any joined participant. LiveKit credentials and room names are not authorization evidence by themselves.

## 13. Claude orchestration and Gemini target tools

One Claude conversation context per owner session, bounded by summarized verified task state. The iPhone's state wins over stale worker state. Prompts include available capabilities and current task, not a claim that all features are ready. Treat OCR text, friend labels and screen content as untrusted data, not instructions to change the system policy.

Required tool surface:

| Tool | Function and execution boundary |
|---|---|
| `resolve_target` | Gemini scene/object/label resolution; returns candidate only. |
| `resolve_friend_selection` | Identify clicked object from reference image and match it to a current scene. |
| `read_screen_model` | Structured kiosk controls, sections, total if visible, checkout/payment indicators. |
| `read_menu` / `answer_menu_question` | Grounded read-only menu interaction; unknown items/prices remain unknown. |
| `choose_control` | Select an existing current-screen control ID; no invented coordinates. |
| `begin_task` | Submit a typed task proposal to R2; acceptance is not completion. |
| `get_task_status` | Read authoritative iPhone state or report unavailable/stale. |
| `request_user_confirmation` | Ask R2 to create a prompt; cannot manufacture a Yes. |
| `cancel_task` | Request local Stop; no remote resume shortcut. |

Claude returns structured tool calls; application code validates/executes and returns typed tool results [S9]. Serialize mutating tools and physical tasks. Read-only calls may run concurrently only within configured bounds. Deduplicate tool-use/request IDs, set 5-second default tool deadlines and bounded retries, and reject late results after cancellation. Do not retry a side effect without its original idempotency key. A worker restart must synchronize current state rather than replay conversation commands.

Use Gemini on demand with latest-frame sampling, not one API request per video frame. Keep one active visual request and one replaceable latest pending image per session by default. Use structured outputs and deterministic schema validation. Reject out-of-bounds boxes, absent source-frame IDs, conflicting labels and malformed control lists. Never translate model self-reported confidence into certified physical accuracy.

Gemini may provide observations useful to a verifier, but a generic model Boolean is not proof of an action. Preserve action-specific deterministic evidence or explicit owner confirmation. No models or friends can authorize payment, emergency-control selection, hazard clearance or passage.

## 14. Preserve the complete R2 task scope

| Task | Required implementation |
|---|---|
| BTN | Read and disambiguate labels, bind current target, approach if supported, guide hand, request press, verify indicator/display change or owner confirmation. Reject ambiguous/unlabeled/emergency controls. |
| OBJ | Find specified object, reacquire after loss, approach if supported, guide hand, request grasp, verify valid co-motion or owner confirmation. Disappearance alone is not grasp. |
| MNU kiosk | Read structured screen, speak options, accept voice choice, guide light tap, reread and verify screen transition. Recover wrong selection using a newly resolved Back control; detect reset. Read total and stop before payment/final purchase commitment. |
| MNU printed/wall | Read and group content, answer grounded questions, report blurry/missing content. No invented prices. |
| DOR | Identify door and safe handle/opener, determine action from evidence, approach/guide, verify opening; R1 alone validates path and authorizes passage. Locked/heavy/blocked pauses. |
| APR | Approach a visible target within the existing ≤5 m scope using R1's observed free space. No stairs, outdoor routing, floor plans or navigation expansion. |
| HAP | Preserve optional Bluetooth pairing, side identification, calibration, heartbeat, reconnect, generation synchronization and all-stop. |

Friend selections enter these same flows. They do not create a parallel controller. Buttons and screen controls need the same label/payment checks even when a friend selected them. Unknown selected objects may be described without enabling unsupported physical tasks. Begin live friend validation with OBJ and BTN, then connect the same selection mechanism to supported kiosk and door targets; this order is not permission to mark the others complete while missing.

Maintain one physical task at a time, bounded retries (three after the initial attempt), request correlation, and explicit verification provenance. `on_target` is not task completion. Preserve existing image-plane hand alignment when allowed; missing metric depth prevents metric approach/proximity instructions, not all read-only assistance.

## 15. Safety, stop and recovery

The existing S1–S9 rules remain in force. In particular: fresh local evidence ≤300 ms for movement cues; no extrapolated guidance after loss; unknown depth is not free space; hazard priority; offline Stop; model proposals never directly drive movement; firmware independently expires output.

Any local Stop/Pause, hazard, tracking loss, essential audio interruption or microphone mute:

1. Atomically invalidate task generation and pending confirmation.
2. Cancel task/model/selection/guidance work and flush output queues.
3. Send immediate ALL_STOP through HapticLink if bands are present; do not wait for network.
4. Play local Stop via R1, then publish state best-effort.
5. Require explicit owner Resume and current evidence before new movement.

Pause guidance keeps the friend call available for conversation, with emergency speech priority. End assistance closes media and worker connections, invalidates session tickets/leases and subscriptions, ends presence, and releases camera/microphone. Stop sharing removes helpers and their media access but preserves AI/local assistance. If immediate helper removal cannot be confirmed, unpublish the shared tracks locally before room cleanup; continue supported local assistance without leaking new media.

Revocation requires both app access checks and actual media eviction. Short token expiry is insufficient for connected clients. Use LiveKit Cloud removal/revocation APIs and deny new tokens; test already-issued tokens and reconnects [S6]. Owner session End is terminal, including after an app crash/relaunch; start a fresh session rather than auto-resuming movement.

While sharing, owner and worker renew a backend-issued authorization lease every 5 seconds, valid for at most 15 seconds from server issuance. Bind it to session epoch, authorization version and helper connection/lease; a helper heartbeat alone cannot renew permission. Responses echo a renewal nonce and validity duration; compute a conservative local monotonic deadline from the request-send time, rejecting delayed/out-of-order responses. On expiry, the owner immediately pauses friend-derived tasks, cancels proposals and unpublishes shared video/microphone; the worker denies helper operations and removes the helper or closes the room. These actions do not wait for Convex recovery. If eviction cannot be verified, retain local-only assistance until a fresh private room and authorization are available. This is a maximum authorization grace period, independent of movement-evidence and firmware timers.

| Failure | Required behavior |
|---|---|
| Cartesia/Claude failure | Explain voice-agent unavailability, stop pending AI task effects; friend call can remain active. Local Stop still works. |
| Gemini failure/uncertainty | No new physical target from that selection; allow friend conversation and reselection. |
| Helper leaves/network drops | Cancel pending proposal and pause friend-initiated task; owner can explicitly resume supported AI guidance. |
| Convex connection lost | Disable new joins/selections; pause friend-driven tasks; maintain local Stop. Fail closed when the current sharing authorization lease expires, within 15 seconds of its last issuance. |
| Owner media freezes | Helper sees unavailable/delayed status; selection disabled. Local perception independently determines whether existing local guidance is safe. |
| Owner backgrounds/locks | Pause physical guidance and suspend/end camera sharing; explicit recovery on foreground. |
| Worker restarts | Drop old audio/tools and synchronize session state; no silent task resume. |
| BLE absent/fails | Respect existing explicit speech-only capability path; never show connected bands or simulate them in live mode. |

## 16. Native and browser accessibility

Use standard accessible controls and stable identifiers. The owner must start, know who joined, accept/reject a target, pause/resume, stop sharing, remove a friend and end without seeing the screen. Use meaningful SwiftUI labels/traits, Dynamic Type, minimum 44-point targets, high contrast, safe areas and Reduce Motion. Preserve existing accessibility work and fix the documented largest-text problem rather than hiding it.

Browser controls require keyboard focus, text labels, screen-reader status, sufficient contrast and touch targets. Offer a non-pointer route for helpers: choose from a current detected-target list or enter a target description; those proposals use the same confirmation and reacquisition rules. This supplements live clicking. Exclude decorative overlays from accessibility traversal.

Do not announce every frame or transcript token. Announce meaningful state changes once, avoid stealing VoiceOver focus, and distinguish joining from selecting. Test with VoiceOver while the friend and AI are present; automated label checks alone are insufficient.

## 17. Limits and performance measurements

Defaults below are engineering starting points, not demonstrated performance:

| Item | Initial policy |
|---|---|
| Local movement evidence age | Preserve 300 ms maximum at dispatch. |
| Local Stop target | ≤250 ms; measure actual output stop separately from network updates. |
| Firmware self-stop | Preserve H1's ≤500 ms heartbeat-loss requirement; verify hardware. |
| Helper selection | One pending; up to two submissions/second before rate rejection. |
| Resolution lifetime | 15 seconds from submission to ready-to-confirm; unresolved proposals expire. |
| Prompt queue/playback | Must finish within 10 seconds of ready-to-confirm; otherwise cancel and request a fresh proposal. |
| Confirmation lifetime | 15 seconds beginning at actual confirmation-prompt playback completion; no inherited resolution deadline. Stop/supersession invalidates immediately. A late Yes requires a fresh prompt; accepted targets always reacquire. |
| Visual/tool timeout | 5 seconds per operation; bounded retry only while context remains valid. |
| Media starting budget | 720p/15 fps; adapt to actual device/network. |
| Camera queues | Latest-only; no backlog of full-resolution frames. |
| Reference payload | ≤1 MiB; drop by terminal selection/60 seconds maximum. |
| Helper join | Aim for first media within 5 seconds on tested network. |
| Click acknowledgement | Aim for visible receipt within 500 ms; resolution status remains separate. |
| Reacquisition | Measure click-to-candidate, confirmation-to-tracking, and cue latency separately. |
| Endurance | 20 minutes real wall-clock on devices; log memory, thermal, battery and queue behavior. |

No cloud round trip is part of the high-frequency movement loop. Never relax evidence freshness to make a slow network demo appear to work. Debug metrics must not enter the ordinary owner flow.

## 18. Implementation order and merge plan

Work on a feature branch such as `feat/r2-r3-live-friend-assistance`, created from current main after inspecting local work. Keep changes in coherent PR-sized commits; no force pushes. Integrate through the team's agreed branch/PR process rather than treating the old two-hour merge cadence as permission to overwrite main.

1. **Audit and contracts:** preserve R2 state/tests, confirm R1 frame/audio/reacquisition interfaces, document new schemas and ownership. Create fixture adapters for unavailable R1 inputs.
2. **Real AI session:** worker plus Cartesia STT/TTS, Claude tools, Gemini typed targets, controlled playback, and existing R2 task validation. First run scene description and one button/object flow with explicit live readiness.
3. **Trusted sessions:** identity, invitations, grants, session leases, access tests, token minting and room cleanup. No public anonymous session access.
4. **Live media vertical slice:** owner iPhone to browser helper, two-way speech and optional helper camera, single camera/audio ownership, join/leave announcements and local Stop preemption.
5. **Live click vertical slice:** reference-frame selection, current scene reacquisition, owner confirmation, existing task execution, authoritative helper feedback. Prove rejection cases before demo polish.
6. **Native helper parity:** same live call and selection contracts in SwiftUI; deep links and account switching restrictions.
7. **Actual notifications:** APNs and Web Push delivery, expiry, deduplication, denial handling and foreground availability. Verify notification-to-call on physical devices.
8. **Complete existing scope:** integrate all baseline task flows, optional bands where H1 is ready, accessibility and offline behavior.
9. **Integration hardening:** network loss, permission changes, revoked access, capture rotation, interrupted speech, repeated sessions and endurance; record real outcomes.
10. **Demo and handoff:** reproducible build/configuration, actual provider calls, source-linked evidence and known blockers.

If time runs short, cut optional helper selfie polish and secondary visual effects first. Keep live media, both helper clients, notifications, explicit confirmation and safety gates as required deliverables. Report any incomplete requirement rather than silently substituting a snapshot-only or simulated workflow.

## 19. Acceptance tests

Retain baseline T01–T14 and existing R2 suites. Add tests that exercise authority boundaries and races, not just schema field presence.

| ID | Test and pass condition |
|---|---|
| F01 | Accepted friend receives one logical session-start alert across configured endpoints; reconnect does not create a new session notification. Provider acceptance is recorded separately from observed delivery. |
| F02 | Browser and native helper each join an enabled session without owner approval after initial trust setup. Unauthorized/forwarded links expose no media or session details. |
| F03 | Owner hears accurate join/leave announcements and can disable sharing without ending AI assistance. |
| F04 | Two simultaneous join attempts yield one helper lease; failed reservation expires. A second device cannot silently take control. |
| F05 | Two-way human audio works on different networks with relay connectivity; optional helper camera never replaces owner rear capture. |
| F06 | Click on live video identifies the intended item across rotation, letterboxing, scaling and moving camera; the captured reference matches the rendered frame. |
| F07 | Delayed/old reference is never used as current depth. A moved or missing object forces reacquisition/clarification and emits no wrong movement cue. |
| F08 | No guidance starts before owner confirmation. Friend “yes,” forged confirmation, echo, stale prompt and post-Stop Yes cannot authorize movement. |
| F09 | Rapid selections supersede only the pending proposal; an old model result or Yes cannot replace the current target. |
| F10 | Existing BTN/OBJ/MNU/DOR verification rules apply to friend targets; payment and emergency controls remain rejected. |
| F11 | Owner Stop during friend speech, TTS, Gemini, tool calls or BLE backpressure stops local output; late PCM/tools never replay. |
| F12 | Helper Pause can stop but cannot Resume or mark success. Owner explicit Resume requires current evidence. |
| F13 | Helper leave/removal/revocation invalidates proposals and actually cuts media access; cached tokens and delayed webhooks cannot rejoin or release a newer lease. |
| F14 | APNs works with the native helper app backgrounded; Web Push works on a supported permitted browser/Home Screen installation. Unsupported/denied setup is explained. |
| F15 | Notification opened after session end shows ended state and never starts media or a stale task. |
| F16 | End, crash/relaunch, network reconnect and worker restart do not resume movement; owner presence expires and abandoned rooms close. |
| F17 | Missing Cartesia/Gemini/Claude credentials yield explicit errors, not silent fallback providers or simulation. Friend calling and AI availability are separate. |
| F18 | VoiceOver and largest accessibility text support owner controls and confirmations during a real call; keyboard-only helper can propose a target. |
| F19 | Auth tests cover unrelated users, revoked friendship, wrong session/epoch, expired ticket, forged role, leaked invite reuse and unauthorized worker endpoints. |
| F20 | Twenty-minute real session plus repeated Start/End cycles has bounded frames, PCM, requests and listeners; End releases camera/microphone. |
| F21 | Structured logs show actual Cartesia STT/TTS, Claude orchestration, Gemini image calls and Convex updates without secrets/raw media. |
| F22 | Fresh clone builds native Debug/Release and browser production bundle; worker starts from documented configuration and reports missing values honestly. |
| F23 | Signed-in owner starts AI with sharing disabled and no friends. Enabling/disabling sharing preserves AI in the healthy case; missing account authentication still permits supported local mode. |
| F24 | Acceptance binds exactly one proposal/prompt to the new task generation. Delayed old-generation Gemini/tool/TTS results cannot cross that boundary. |
| F25 | Slow but in-budget resolution and spoken prompt leave the full 15-second response window; expired, interrupted or superseded prompts cannot authorize. |
| F26 | With LiveKit media healthy but Convex unreachable, sharing ceases by authorization-lease expiry; no further frames/audio reach the helper and no proposal remains actionable. Recovery cannot silently resume guidance. |

Automate pure Tasks/selection reducers, schema/transform tests, Convex authorization and idempotency, worker provider mocks, native coordinator races, and browser UI state. Include real service smoke checks and physical iPhone checks separately. Never label mocked provider tests or simulator rendering as successful live calling, push, BLE or perception.

Run existing repository checks rather than replacing them. Typical verification includes Tasks/HapticLink Swift tests, project-generation consistency, existing iOS CI script on an available simulator, unsigned Release build, browser typecheck/build/tests, Convex typecheck/tests, and worker unit/integration tests. Actual signing/install/APNs/camera/route changes require the developer's real device and accounts. Record exact commands, versions and outcomes.

## 20. Configuration and implementation blockers

Keep only public deployment URLs, auth client identifiers, bundle ID and feature flags in client configuration. Keep provider secrets, LiveKit signing secret, APNs key, Web Push private key and worker credentials server-side.

Required server configuration, using descriptive environment names rather than embedding values:

- `CARTESIA_API_KEY`, `CARTESIA_STT_MODEL`, `CARTESIA_TTS_MODEL`, `CARTESIA_VOICE_ID`.
- `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`; `GEMINI_API_KEY`, `GEMINI_MODEL`.
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`.
- Convex deployment configuration; auth issuer/audience/client configuration for both clients; scoped worker trust configuration.
- APNs team/key/bundle/environment configuration and private key; Web Push VAPID keys and contact setting.
- Public web origin, owner/native deep-link association, worker HTTPS/WSS origin, allowed origins and deployment health checks.

Never ask the user to paste secrets into chat or commit them. Check actual SDK/deployment requirements when implementing. Seed demo accounts and friendship through the same authorized paths; no privileged hard-coded bypass in production mode.

External values still requiring real setup: Apple team/provisioning/APNs entitlement; provider credentials and accessible models; domain/hosting; R1's native capture/audio/reacquisition APIs; H1 contract if bands are used. Missing values block their corresponding live acceptance tests, not independent code/spec work. Do not claim a native Swift perception package exists merely because the Python prototype does.

## 21. Demo and completion definition

Demo: owner starts Reflex; trusted friend receives a notification on another device; friend joins without a per-session approval; both speak naturally; friend clicks the cup in live video; Reflex announces the selection and waits for the owner's Yes; current tracking guides the hand; grasp is explicitly confirmed or observed. Then show a Stop/occlusion failure and a clean recovery. Repeat using the second helper client. End the session and demonstrate that the old notification/link cannot reopen it.

Completion requires the combined R2/R3 implementation and both helper clients, with real media/notifications demonstrated and original task requirements accounted for. If native R1 or H1 work is missing, report precise boundaries: friend calling may work while physical guidance remains blocked. Use distinct statuses: implemented, fixture-tested, simulator-tested, service-tested, real-device-tested, blocked. Do not count test totals from historical reports as newly run.

Produce public setup/validation documentation and a concise R1 integration request listing required frame feed, shared microphone/Stop tap, remote-audio gate, cancellable PCM playback and target reacquisition. These are the critical teammate dependencies even though Kevin owns both R2 and R3.

This feature supports a social-participation story, but no Meta/Muse integration or prize eligibility is implied. The selected stack remains Cartesia, Gemini, Convex and Anthropic, plus the approved LiveKit media layer.

## 22. References

Repository baseline and requirements:

- [Repository structure](https://github.com/yukevindai/VisionOS/blob/0e869b1c293a12f974c9d55010fc401530f0c009/docs/REPOSITORY_STRUCTURE.md)
- [Team specification](https://github.com/yukevindai/VisionOS/blob/0e869b1c293a12f974c9d55010fc401530f0c009/docs/Cal_Hacks_13_Build_Spec_v3.md)
- [Original R2 specification](https://github.com/yukevindai/VisionOS/blob/0e869b1c293a12f974c9d55010fc401530f0c009/docs/R2_IOS_IMPLEMENTATION_SPEC.md)
- [App composition](https://github.com/yukevindai/VisionOS/blob/0e869b1c293a12f974c9d55010fc401530f0c009/ios/App/Composition/AppDependencies.swift)
- [Adapter protocols](https://github.com/yukevindai/VisionOS/blob/0e869b1c293a12f974c9d55010fc401530f0c009/ios/App/Integration/AdapterProtocols.swift)
- [iOS setup and validation](https://github.com/yukevindai/VisionOS/blob/0e869b1c293a12f974c9d55010fc401530f0c009/ios/README.md)

Provider/platform references checked October 24, 2026; recheck the pinned SDK API during implementation:

- S1: [Cartesia STT and TTS plugin](https://docs.livekit.io/reference/python/livekit/plugins/cartesia/index.html); [Cartesia realtime STT](https://docs.cartesia.ai/api-reference/stt/turns/websocket).
- S2: [Anthropic LiveKit plugin](https://docs.livekit.io/reference/python/livekit/plugins/anthropic/index.html).
- S3: [Convex Swift client](https://docs.convex.dev/client/swift/overview); [Convex Auth0 integration](https://docs.convex.dev/auth/auth0).
- S4: [Convex actions](https://docs.convex.dev/functions/actions); [current runtime limits](https://docs.convex.dev/production/state/limits). Use runtime-specific limits, not a universal timeout assumption.
- S5: [WebKit Web Push requirements](https://webkit.org/blog/13878/); [Apple APNs registration](https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns).
- S6: [LiveKit tokens, grants and revocation](https://docs.livekit.io/frontends/reference/tokens-grants/).
- S7: [LiveKit frame metadata and SDK support](https://docs.livekit.io/transport/media/frame-metadata/); [LiveKit Swift SDK](https://github.com/livekit/client-sdk-swift).
- S8: [Gemini image understanding](https://ai.google.dev/gemini-api/docs/image-understanding).
- S9: [Claude tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview).
- S10: [LiveKit Swift audio integration](https://github.com/livekit/client-sdk-swift/blob/main/Docs/audio.md).
- Additional: [LiveKit byte streams](https://docs.livekit.io/transport/data/byte-streams/); [LiveKit agent audio-output interface](https://docs.livekit.io/reference/agents-js/classes/agents.voice.AudioOutput.html). API names and extension points differ across Python/JavaScript; do not copy one language's type names into the other.

Implement against this combined assignment by extending the existing app. Preserve native iPhone delivery, all baseline tasks and output safety, and genuine live friend participation.
