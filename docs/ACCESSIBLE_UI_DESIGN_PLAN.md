# VisionOS role-based accessible UI plan

October 24, 2026 — revised after the camera-first screenshot review. This version supersedes the earlier shared Home/task-controls concept. The approved interface is now implemented in the native iOS shell; service and physical-device acceptance remain separate.

[Figma current overview](https://www.figma.com/design/L4c5A5bH9pjnDCTFPDcwHs?node-id=3-27) · [Current specification](https://www.figma.com/design/L4c5A5bH9pjnDCTFPDcwHs?node-id=3-28)

## Confirmed product flow

The user explicitly requested two choices on opening: **Blind** and **Not blind**. Blind opens a screen with one large **Call** button. Call starts AI immediately, while trusted friends can join. The assisted user then sees a clean rear-camera stream, similar in purpose to Record3D Stream, and receives spoken guidance. Not blind opens authorized sessions and then a detailed helper view.

The role choice selects an interface, not a diagnosis, trust grant or sharing consent. Either person may use accessibility features. Do not infer disability from VoiceOver settings. Do not introduce public session discovery or copy Record3D's laptop streaming architecture; VisionOS remains native iPhone.

| Step | Blind user | Sighted helper |
| --- | --- | --- |
| App opens | Choose Blind | Choose Not blind |
| Landing | One large Call action | Shared sessions, actual status, Join |
| During session | Clean live camera, concise connection status, Stop / End call | Camera, hand landmarks, object label, distances, task/user status |
| Task selection | Speak a request, such as Find my cup or Press floor 2 | May propose a target; user confirms |
| Recovery | Spoken explanation; explicit Resume by voice or accessible control | Show lost/stale/unknown state; avoid outdated telemetry |

The Call screen has no task grid, contact picker or diagnostics. The live preview has no skeleton, classification labels or distance dashboard. Stop and End remain stable, accessible controls. Settings, first-use permissions, pairing and trust management stay outside the normal Call path. Role selection remains reachable; the current request specifies choosing on app opening rather than silently persisting a role.

## Call behavior

After initial setup, one Call gesture starts the session. Essential camera/audio permissions and local Stop readiness still gate actual operation. AI asks what the user needs and accepts voice requests. Show connected only when the corresponding capability is ready; report failures honestly and preserve available local assistance. AI does not wait for a human helper.

First-use setup explains camera/microphone sharing and obtains explicit consent. A friend becomes trusted only through the existing authenticated invitation and account-approval flow. Choosing Blind or pressing Call is not a substitute for that initial sharing choice. Subsequently, enabled sessions can be joined by previously trusted friends without repeated per-session approval. Announce joins and leaves once. Keep an accessible Stop sharing action available through voice and a reachable privacy interface; choosing role must not expose media.

Only one helper joins at a time. The session list shows authorized people, actual task status and whether joining is available, already occupied, ended or offline. An AI-only active session is available for a helper to join. A helper cannot join two sessions simultaneously or run an owner session at the same time on the same phone.

## Helper visualization and distance semantics

The user requested “media pipe”; the mockup interprets this as the visible hand-landmark skeleton. Current native iPhone code uses Apple Vision hand pose, not MediaPipe. This design does not replace that engine or claim MediaPipe integration.

Helper camera view includes:

- Hand landmarks and tracking availability.
- Bounding region for the selected target.
- Recognized object class, such as Cup; for a button, type and exact readable label, such as Elevator button / 2.
- Owner task state: awaiting request, identifying, awaiting confirmation, guiding, paused or completion with verification provenance.
- Separate measured camera-to-target range and fingertip-to-target separation, in meters.
- Freshness and depth availability; optional Hide overlays.

Distances refer to the same identified target and current coordinate frame. Derive actual 3D range/separation from validated geometry; do not relabel an optical-axis depth as Euclidean range. In particular, the current native adapter assigns target.xyz.z to a distance field, so the future UI mapping must verify semantics before using it as camera-to-target range. Missing, uncertain, occluded or stale evidence displays **Unknown**, removes stale guidance overlays and prohibits metric movement cues. Do not extrapolate or fabricate a number. A displayed destination distance is not proof of a safe path.

All mockup identities, scenes, hand landmarks, frame ages and numeric readings are explicitly **SIMULATED**. They are illustrative design data, not measurements or accuracy evidence. A label and a distance do not authorize movement.

Both native and browser helpers need labeled detected-object alternatives to coordinate clicking. A helper proposal must be confirmed by the owner and followed by current target/hand reacquisition. Helper audio cannot answer for the owner. Never route helper input directly to haptics or movement.

## Immediate task scope

1. **Cup pickup:** identify the requested cup; obtain fresh hand/target geometry; guide one correction at a time; stop on loss; establish grasp through observed evidence or explicit owner confirmation. Hand overlap does not establish a grasp.
2. **Elevator button:** identify the intended non-emergency button by its visible label; reject ambiguity; guide the hand; request a press; verify an actual indicator/result change or obtain explicit owner confirmation. Reaching the button alone does not prove it was pressed.

These are the immediate design priorities. No autonomous elevator travel, routing between floors, stairs, destination navigation expansion or model-authorized movement is introduced. Preserve existing S1–S9 safety boundaries and team ownership.

## Stop and failure behavior

| Event/control | Required behavior |
| --- | --- |
| Stop | Immediate local output cancellation; conversation remains available. Explicit owner Resume and fresh evidence required. |
| End call | Immediate local Stop, close sharing/media/worker/presence and release camera/microphone. No confirmation that delays stopping. |
| Stop sharing | Remove helper access while retaining supported AI/local assistance. |
| Microphone mute | Pause physical guidance; explain spoken Stop is unavailable. Local Stop/End remains active. Unmute does not auto-resume. |
| Hand/target lost | Stop guidance, speak one concise reason, clear stale overlay/distance, wait for explicit recovery. |
| Depth unavailable | Unknown distances; no metric approach/proximity instructions; allow appropriate read-only conversation. |
| Delayed/frozen stream | Obscure stale frame and disable selection; never retain an unqualified Live label. |
| Permission/service failure | Explain actual unavailable capability; provide recovery without false readiness. |
| Revoked/ended session | Clear media/selection promptly; require fresh authorization to join again. |

## Accessibility specification

- Role choices have accessible names and concise purpose descriptions. Initial assisted landing focus goes to Call. Call's hint explains that AI starts and authorized friends may join. The decorative phone icon is not a separate focus stop.
- Native semantic controls with visible labels. Blind occupies about half the role screen and Not blind about 15%; purpose descriptions remain VoiceOver hints. The heading is “Choose your view.” Call is a large blue rounded rectangle on black, filling the available landing area; other primary controls are at least 60pt tall. The minimum implementation target is 44pt.
- Native SF Pro through semantic system text styles and Dynamic Type. At maximum accessibility sizes, preserve content, wrap labels, stack controls and allow scrolling. Role and Call targets have minimum, not fixed, heights so their labels grow and the page scrolls when needed.
- Significant state changes are spoken once. Coordinate VoiceOver, AI and friend audio through R1's output authority; Stop/hazards preempt conversation. Captions or telemetry updates must not steal focus or announce every frame.
- No critical gesture-only, color-only, visual-only or timed interaction. Provide accessible manual confirmation/recovery alternatives when speech fails, without displaying a permanent task menu.
- Keep the complete camera frame visible with aspect-fit. Use a compact status pill and circular details control at the top, with concise instructions/captions and a dark control dock at the bottom. The dock retains immediate Stop/Resume and End call. Use opaque dark surfaces for stable contrast over the camera; secondary confirmation/details surfaces honor Reduce Transparency and Increase Contrast. Honor Reduce Motion. Reuse the established Light/Dark semantic tokens.
- Helper web supports keyboard-only target selection, visible focus and status announcements. Do not rely on reading the landmark overlay to use core session controls.

## Figma deliverables and review

Current overview contains the role picker, one-button assisted landing, full camera assisted call, helper sessions list, desktop helper with skeleton/label/distance overlays, native helper phone, elevator-button example and missing-depth recovery example. The former v1 overview and notes are retained to the right as Previous direction / v1 and Previous notes / v1.

The file uses editable text, vectors and reusable controls, not flattened full-screen images. All current texts use available Inter because Figma listed SF Pro but reported it missing during editing. This is a mockup limitation; the native app's font should not change. The screens are static design review frames, not a functional media prototype.

Visual review covered all revised compositions; the current phone body bounds fit their content. No missing fonts or unfinished placeholders were reported. Existing palette calculations remain: light body 14.66:1, light secondary 5.96:1, primary 6.55:1 and danger 6.51:1; dark body 16.23:1, secondary on panel 8.12:1, primary 8.89:1 and danger 9.92:1. These are token checks, not an application accessibility audit.

## Implementation sequence and current limitations

1. Implement role routing, the single Call landing and clean assisted camera layout while preserving the existing coordinator and local safety behavior.
2. Integrate AI startup and authorized shared-session discovery/join. Keep single camera/audio ownership and independent capability readiness.
3. Add aligned helper landmarks, selected-target labels, typed 3D distance semantics, freshness and Unknown states from existing native evidence. Retain Apple Vision unless a tracking-engine change is separately justified and authorized.
4. Integrate and validate the labeled elevator-button task with R2 task state, R3 label evidence and R1 guidance authority. Do not imply current object guidance already covers buttons.
5. Verify real iPhone owner/helper flows, browser keyboard operation, VoiceOver alongside speech, maximum Dynamic Type, offline Stop, interruptions, missing depth, reconnect and revocation. Obtain consenting blind/low-vision user feedback when feasible.

Normal Call now uses the authenticated native Gemini object client, pending the R3 backend endpoint/configuration; the local cup/can/bottle/bowl adapter remains in source. The authenticated snapshot reader and watch-only friend sharing are separate tools. Full unified AI calls, helper audio/telemetry and integrated elevator-button guidance remain implementation/validation work. The native shell now provides role routing, one-button local assistance, translucent camera controls, and authenticated watch-only helper discovery/join. The normal Call does not yet publish a shared room: owner sharing remains a separate tool with explicit consent. See the iOS validation record for actual checks.

References: repository R2/R3 live friend assistance and build specifications; [Apple accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility); [VoiceOver](https://developer.apple.com/design/human-interface-guidelines/voiceover); [WCAG 2.2](https://www.w3.org/TR/WCAG22/); [Record3D features](https://record3d.app/features) as visual-stream context only.

## Screenshot refresh — October 24, 2026

The current Figma role (11:42), Call (11:57), and calling (11:76) frames were read before this revision and edited in place. The supplied Be My Eyes screenshots define the dominant blue call target, black canvas, camera focus and bottom controls. VisionOS retains its own Call/Stop/End meanings and existing role/tools/settings navigation; unrelated tabs, Siri prompts, photo capture and history are not introduced. The Figma scene is illustrative, not camera validation.

The native role buttons use minimum heights of 56% and 17% of usable view height to approximate the requested 50%/15% screen areas after margins and system safe areas. Dynamic Type can increase heights, with scrolling preserving both choices.

Normal Call selects a rear format from `ARWorldTrackingConfiguration.supportedVideoFormats` at startup: physical ultra-wide first, then wide, preserving ARKit preference within each lens. It reports the actual selected lens and fallback reason. A phone having an ultra-wide lens does not establish that ARKit world tracking exposes it; no hardcoded 0.5× label or digital zoom is applied. The single AR session, scene-depth registration and geometry invalidation remain intact. Physical lens/field-of-view verification is outstanding.
