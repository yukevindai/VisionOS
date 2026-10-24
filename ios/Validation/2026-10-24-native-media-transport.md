# Native media transport and publisher gates — October 24, 2026

## Scope

Implemented optional native iPhone owner video transport. This is not a completed live human call or physical-device privacy acceptance. Existing policy/backend work is preserved; R1 capture/audio and H1 hardware modules were not replaced.

- `NativeMediaBackend` defines opaque Convex IDs separately from native session IDs. `ConvexNativeMediaBackend` uses injected native identity, strict response validation, exact nonce/scope checks, HTTPS/WSS origin validation, no redirects, an 8-second request timeout and bounded 1 MiB responses. Response accumulation runs off MainActor and cancels its URLSession task on exit. Identity invalidation closes admission; no credential or endpoint is fabricated.
- Owner-only `sessions.mediaStatus` projects room generation/name/SID, owner connectivity, helper connection/version/participant SID, and at most 15 seconds of remaining owner presence. It does not renew sharing. Durations are measured from monotonic request-send time.
- `NativeMediaCoordinator` connects with its gate closed, verifies token and pre/post-token authority scope, waits for reconciled owner presence, and only then attaches the R1 frame tap. Sharing requires an exact fresh owner grant and matching backend/SDK helper identity and participant SID. It polls about once per second and heartbeats about every five seconds. Bootstrap is bounded to 20 seconds; authority expiry is independently scheduled and checked after asynchronous work and on every frame.
- Retired/replacement rooms, expired authority, account changes, SDK reconnect/disconnect, helper departure or conflicting identity close the attempt. Stop Sharing closes admission before server revocation. End, background and mute are wired through SessionCoordinator. Recovery requires an explicit fresh Start; no old credential/room is reused and no movement resumes automatically.
- A single retained control/cleanup slot prevents cancelled noncooperative operations from accumulating. Driver teardown precedes backend cleanup and retains outstanding publication/disconnect work before releasing its connection slot.
- `NativeMediaSampleGate` atomically fences admission by attempt, suspension revision and deadline. Suspension preserves the previous expiry watermark. Membership events carry the exact revision produced on the SDK delegate queue, so a delayed MainActor callback cannot consume a newer suspension.
- `LiveKitNativeMediaDriver` uses exact LiveKit Swift 2.18.0, automatic subscription disabled, reconnect attempts zero and microphone disabled. It verifies actual room name/SID and owner identity and accounts for participants already present at connect. Its buffer track receives only original R1 frames no older than 300 ms. First publication requires a real authorized frame.
- A retained SDK `VideoProcessor` rechecks timestamp, revision and authority on the SDK processing queue. One pending frame permit plus a 67 ms capture cadence bounds ingress. A frame dropped by the SDK before this callback intentionally stalls further input until a new connection.

## Validation actually run

- Full `ReflexAppTests`: **96 passed, zero failures**, iPhone 17 Pro simulator, iOS 26.5. Existing 68 plus 28 new cases: Convex transport 7, coordinator 11, sample gate 7, actual LiveKit frame processor 3. SDK processor tests construct actual `LiveKit.VideoFrame` values; they do not connect to a service.
- Final log `/tmp/r2-native-media-tests-final.log`; result bundle `/tmp/r2-native-media-final.xcresult`. The earlier 95-test run passed before one final CAS test and transport parsing changes; it is superseded.
- Backend: **64 passed across 6 files**, zero failures; TypeScript typecheck and offline codegen passed. Added owner-status authorization/expiry and helper-SID/nonrenewal cases. Logs `/tmp/r2-native-media-backend-tests.log`, `/tmp/r2-native-media-backend-typecheck.log`, `/tmp/r2-native-media-codegen.log`.
- Project generator and repeated offline Convex codegen reproduced identical output. Source whitespace checks passed. The pre-existing user scheme SHA256 remains `540920b42c01d7e87b818a3281cdddf9a71b2ba065ed982bc5972028a7c8f511`.
- Unsigned generic iPhone Release build passed (`CODE_SIGNING_ALLOWED=NO`), log `/tmp/r2-native-media-release.log`. This is compilation, not signing or device acceptance.
- Exact LiveKit dependency resolution and unsigned simulator compilation passed. The processor's first targeted run passed 3/3 on iOS 26.2; the final full suite above supersedes it.
- Previously passing Tasks 89 and HapticLink 44 tests were not rerun because those sources were unchanged in this slice. Historical five accessibility contrast failures remain unresolved.

## Remaining acceptance and integration

The default app still supplies no `LiveIntegrationBundle.media`; real native Auth0 identity and R1 original-frame adapters are required. There is no processed-microphone bridge, remote-audio playback, worker authentication/publisher, helper client or deployment in this slice. LiveKit initializes its shared AudioManager internally, although this adapter requests no microphone/remote subscription/playout; coexistence with R1 audio still requires device validation.

The gates stop new source/processor admission. They cannot retract frames already handed to WebRTC or prove an immediate network cutoff. The SDK's internally forced/full reconnect and automatic track republishing need actual fault testing, and possibly an SDK veto, before a strict no-republish acceptance claim. SDK drops before processor completion can stall video. Provider room deletion does not revoke cached/refreshed tokens; backend tombstones and native generation retirement remain necessary.

No live Auth0, Convex, LiveKit, Gemini, physical iPhone, BLE or 20-minute endurance acceptance was performed. No deployment, commit or push was made.

The user reports a teammate's unpushed R3 ScreenModel/Target Selector and Gemini image pipeline. Their live API validation is blocked by credits. Preserve the current implementation and inspect the actual teammate code before reconciling interfaces; no assumed merge or deletion is justified, and no funded Gemini key was supplied.
