# Tasks (R2)

Foundation-only Swift 5.9 package, iOS 17 / macOS 13. `TaskEngine.reduce(_:nowMs:)` is synchronous and deterministic. Serialize all calls and execute effects in returned order. The package opens no cameras, plays no audio, sends no BLE commands, and has no provider dependencies.

## Integration contract

These types are local R2 adapter proposals, not confirmed shared-schema extensions.

```swift
var engine = TaskEngine(sessionID: "s-1", initialGeneration: 7, guidingHand: .right)
let effects = engine.reduce(.start(TaskRequest(id: "t-1", kind: .button, query: "Up")), nowMs: 100)
// Dispatch .resolveTarget(context, query) through R3.
// Retain that exact context in its response; never substitute the currently pending context.
```

Every asynchronous response supplies `EventEnvelope` with the original `RequestContext`, event ID, local monotonic timestamp/expiry and trusted adapter source. The engine checks schema, session, task, generation, exact request identity and timing, expected phase, target identity, provenance, and duplicate IDs. Never trust a provider to set `EventSource`; the receiving adapter owns it. Convert server timestamps through an agreed clock mapping rather than comparing unrelated clocks.

- `.start` replaces an existing task only after emitting cancellation and ALL_STOP. `state.context` is authoritative for generation synchronization. Generation exhaustion blocks further work.
- `.stop` invalidates bindings, requests and prompts synchronously and emits cancellation, ALL_STOP and priority-zero speech, even if the supplied clock value has regressed. The coordinator must dispatch the stop locally without awaiting provider cancellation. Repeated stops while already paused are idempotent.
- `.resume` resolves/reacquires again. Recovery from tracking/loss/interruption also asks for confirmation once fresh evidence is available, then reacquires again after the answer.
- `.tick` receives injected monotonic time. It never advances to success. Providers default to a 5-second deadline, user prompts and guidance requests to a configurable 30-second deadline. Guidance still requires fresh evidence continuously; the 30-second request lifetime is not evidence validity.
- `.reacquire` must use R1's current tracking. The original `ResolvedTarget.region/frameID` are historical proposal metadata. For guidance, bind target identity to R1's currently tracked geometry; never send the historical region as a fresh location.
- Before executing `.requestGuidance`, call `isGuidanceDispatchAllowed(context:evidence:nowMs:)` with fresh R1 evidence. R1 must additionally enforce freshness at actual cue dispatch and throughout its controller loop. Default physical freshness is 300 ms. Physical evidence defaults to unavailable; `TaskFixtures` supplies explicitly SIMULATED usable observations.
- R1 approach status includes the request ID and mode. An approach completion never counts as hand alignment. Unknown depth/free space, missing distance, targets beyond 5 m, and suspected steps cannot authorize approach.
- R1 hand `onTarget` requests an action and verification. It never completes a physical action itself. R1 must already have stopped hand movement when it reports this state.
- `.requestVerification` context is the action correlation token. Verification needs a typed action-specific implemented detector, distinct before/after frames, ordered timestamps and matching target/action. LLM interpretations cannot complete actions. Unavailable/uncertain verification asks a task-specific question.
- `.askConfirmation` carries a unique `promptID`. Relay an explicit current user answer as `.confirmation(promptID:answer:)` with `.user` source. Door-action prompts require `.doorAction(...)`, not an unqualified yes. Pending prompts expire and are cleared on interruptions.
- `.successFeedback` is a request to R1's scheduler for verified-success feedback, retaining observed/user-confirmed provenance. It is not permission to write a haptic pattern directly.
- `.requestPassage` asks R1 to validate and release its own passage cue after door opening was observed or explicitly confirmed. Only a correlated `.passageAuthorized` from R1 with fresh, usable clearance evidence completes the door task. R2 emits no passage instruction.
- Automatic doors complete any required approach before opening verification. They require fresh target/tracking evidence but no visible hand or hand-guidance request; passage still requires R1 authorization.

## Kiosk and menu behavior

Screen controls must carry the current screen ID and frame ID. User choice, repeat, more and back are correlated user responses to the current pending request. Option groups contain at most three controls. Ambiguous labels ask for clarification. Each tap invalidates generation and coordinates and requests a fresh screen read. A screen change during guidance immediately stops and re-reads. Unexpected destinations resolve a current identifiable Back control; missing/ambiguous Back fails safely. A welcome-screen reset stops and asks to restart.

Observed transition evidence must identify the returned screen's frame as its after-frame. Evidence for another frame asks for explicit user confirmation instead of recording an observed transition.

Payment screens and controls classified as payment, purchase authorization or uncertain checkout produce `PAYMENT_BOUNDARY` handoff, never success. A conservative English label check adds defense in depth, but live R3 adapters must classify financial controls in their supported languages. No generic label heuristic can replace that classification. Total is spoken only when supplied.

Wall-menu sections are informational. Repeat/next/previous and contextual questions request R3 interpretation and route speech through R1. Blurry/cropped content is disclosed; this package does not manufacture menu items or prices. R3 remains responsible for factual menu answers.

## Verification

Run `swift test` in this directory. The suite uses an injected clock and has no sleeps. Tests cover BTN/OBJ/DOR/APR and kiosk/menu flows, cancellation/replacement, malformed and stale records, wrong request/session/task/generation/mode, duplicates, source/provenance, prompt binding, retries after the initial attempt, dispatch freshness, loss/hazards, depth/distance, payment refusal, resets and Back recovery.

Validated with Apple Swift 6.3.3 in Swift 5 package language mode on macOS. These are fixture tests, not simulator/device acceptance tests. Live R1/R3 integration, physical detector accuracy, speech/BLE scheduling, phone geometry/depth and T01–T14 device checks remain integration responsibilities.
