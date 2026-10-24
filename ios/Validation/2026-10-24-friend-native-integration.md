# Live friend assistance: native integration checkpoint

**Status: native coordinator seams and task-engine binding are implemented; real transport and media adapters are not.** This checkpoint records the source state on October 24, 2026. It does not claim live calling, provider, backend, or device acceptance.

## Native composition and trust boundary

`AppDependencies` accepts an optional `FriendAssistanceIntegration` only in the non-simulated composition. The integration keeps remote transport separate from the local R1/audio client. There is no default implementation, no silent simulation fallback, and no second camera, microphone, speech, or output scheduler.

Remote helper input is limited to an authenticated-scope proposal, pause, or departure. The transport adapter must authenticate the connection, validate the reference payload, and derive scope from server identity before constructing these typed events; the Swift types do not authenticate network data. Generic worker Boolean confirmations, remote Resume, success claims, arbitrary tools, and unscoped audio are not accepted through this boundary. Owner confirmation and task/menu/calibration input arrive through a separate local-client callback and are checked against the current session context, pending request, and local-owner-speech provenance. Accessible local selection rejection is supported; it cannot positively accept a target.

## Coordinator behavior

The serialized `FriendAssistanceCoordinator` composes `FriendSelectionPolicy` and `SharingAuthorizationLease` and uses the app's monotonic `AppClock` to schedule local watchdogs for proposal, prompt, lease, and acceptance deadlines. Expiry therefore does not depend on a network callback. Cancellation does not free an async operation's slot until its adapter returns; at most one renewal, target resolution, acceptance synchronization, or eviction operation runs at a time, with only the latest queued work retained where applicable.

Accepted target flow is prepare → stop/cancel → authenticated worker binding acknowledgement → begin the exact new task → current-scene reacquisition. Preparation checks the current frame and old task context and performs the unified local Stop before waiting for the worker ACK. The ACK must exactly match the accepted binding. A timeout or mismatch aborts acceptance. The task engine then reacquires the selected target under the new task generation; it does not submit a generic text task. Resume and retry continue to require the same selected target identity to be reacquired, not a fresh unrelated target choice.

Helper departure, authorization revocation, backend loss, owner Stop, and End invalidate proposals and cancel pending work. Backend loss/revocation pauses friend-originated physical work; expiry/revocation closes local sharing before best-effort remote eviction. Normal owner pause retains the call but cancels pending proposals and acceptance synchronization. End closes sharing and adapters. Kiosk `screenControl` selections are currently rejected because the task engine has no validated `ScreenModel` binding for friend-originated controls; no screen-control bypass was added. Existing Tasks behavior and safety checks remain in force.

Owner intent, task confirmation, menu/choice responses, and calibration answers use separate local trusted ingress. The coordinator does not convert arbitrary worker Boolean confirmations or Resume commands into local task events. Existing prompt/audio interfaces still need real R1 playback-completion, microphone, echo, and remote-audio gating implementations before live use.

## Validation status

Final composite validation: **201 passing cases, 0 failures** across package and simulator app unit runs. This comprises Tasks89 + HapticLink44 + app68; it excludes UI audits and all service/device acceptance. Compared with the previous foundation checkpoint, this adds 11 Tasks cases and 18 app cases. Intermediate runs are not additive.

| Check | Actual result | Local evidence |
|---|---|---|
| Tasks package | 89/89 passed | `/tmp/r2-friend-native-tasks.log` |
| HapticLink package | 44/44 passed | `/tmp/r2-friend-native-haptics.log` |
| Native app unit tests | 68/68 passed, including all 18 simulated friend coordinator cases | `/tmp/r2-friend-native-verified.log`, `/tmp/r2-friend-native-verified.xcresult` |
| Unsigned Release iPhone build | BUILD SUCCEEDED; not a signed install or device run | `/tmp/r2-friend-native-release.log` |
| Project generation | Repeated generation byte-identical; new source/test membership included | `ios/Scripts/generate_project.py` |
| User's pre-existing Xcode scheme | Byte-preserved | SHA256 `540920b42c01d7e87b818a3281cdddf9a71b2ba065ed982bc5972028a7c8f511` |
| Source/config/test manifest | 54 final files | `/tmp/r2-friend-native-source.json` |

Toolchain remains Xcode26.6 (17F113), Swift6.3.3 with project Swift5 mode, minimum iOS17. Native tests ran on the existing iPhone17Pro/iOS26.5 simulator with parallel testing disabled. Final build logs contain the skipped AppIntents metadata extraction warning; no new Swift source warning is recorded in the final native runs.

```sh
swift test --package-path ios/Packages/Tasks
swift test --package-path ios/Packages/HapticLink
xcodebuild -project ios/Reflex.xcodeproj -scheme Reflex \
  -destination 'platform=iOS Simulator,id=F6C5FDA3-8DE1-441D-BFFE-2FE0E7F272FB' \
  -parallel-testing-enabled NO -only-testing:ReflexAppTests \
  -resultBundlePath /tmp/r2-friend-native-verified.xcresult \
  CODE_SIGNING_ALLOWED=NO test
xcodebuild -project ios/Reflex.xcodeproj -scheme Reflex \
  -configuration Release -sdk iphoneos -destination 'generic/platform=iOS' \
  CODE_SIGNING_ALLOWED=NO build
```

Use a new result path when rerunning. An initial compile failure caused by a shadowed local engine binding was corrected. The v2 native test run then exposed a nil initial `taskState`; native Start now publishes the engine's real idle state, rather than inventing a task. The stale v2 run was interrupted after the crash and is not a pass. Later 65- and 66-case runs passed while review added the final reentrant-End cases; the 68-case final run above supersedes them.

Review and regression checks cover Pause/Stop/End during an occupied ACK, new acceptance after timeout, same-generation band task-ID synchronization, scoped backend failure, helper departure during outage, separate trusted owner workflows, audio preemption before Stop speech, and synchronous Stop/End callbacks during cancellation and acceptance. The bridge permits at most two submitted selections per local rolling second. Real provider limits still need configuration and service tests.

The prior five strict contrast-audit failures were not rerun or fixed in this checkpoint. Simulator/unit results do not establish real media removal, voice/echo isolation, provider/backend auth, or device behavior. All new adapters in the tests are explicitly simulated.

## Remaining integration and external requirements

The composition and policy seams have no real adapters. Convex identity/trust/session authorization and leases, LiveKit owner media lifecycle, the realtime worker and scoped wire protocol, Cartesia/Gemini/Claude adapters, both browser and native helper clients, notifications/deep links, and actual authenticated worker ACKs remain outstanding. Real scoped PCM/audio metadata, R1 camera-frame and geometry mapping, prompt playback completion, processed owner microphone, echo isolation, local offline Stop, remote-audio admission, and same-identity target reacquisition require agreed R1/native implementations. Provider SDK versions, rate limits, credentials, endpoints, deployment configuration, and both helper-client integration remain unresolved. Do not put secrets in source or request them in chat.

H1 BLE/GATT compatibility and firmware generation handshake remain unconfirmed. The earlier accessibility record still has five unsuppressed strict contrast failures; they were not rerun here. Physical camera/audio/BLE, signed installation, hardware onset, APNs/Web Push delivery, and endurance acceptance remain unrun. Follow the [combined R2/R3 specification](../../docs/R2_R3_LIVE_FRIEND_ASSISTANCE_SPEC.md), [policy foundation report](2026-10-24-friend-policy-foundation.md), and [R1 integration request](R1_FRIEND_INTEGRATION_REQUEST.md) for requirements and external seams.
