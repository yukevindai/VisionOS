# Live friend assistance: policy foundation

## Scope and implementation status

October 24, 2026. Base commit: `0e869b1c293a12f974c9d55010fc401530f0c009`. This is the first implementation checkpoint for the [combined R2/R3 specification](../../docs/R2_R3_LIVE_FRIEND_ASSISTANCE_SPEC.md), on `codex/r2-r3-live-friend-assistance`. The native iPhone app and R1/H1 ownership boundaries are preserved.

| Component | Status at this checkpoint |
|---|---|
| Friend proposal/confirmation policy | Implemented; 14 deterministic XCTest cases passed |
| Sharing authorization lease policy | Implemented; 20 deterministic XCTest cases passed |
| Existing Tasks/HapticLink behavior | Package regression tests passed |
| Native app compatibility | 50 app unit tests passed on simulator; unsigned Release iPhone build passed |
| R1 frame/audio/reacquisition contract | Proposed; agreement and native implementation pending |
| Policies wired into SessionCoordinator/TaskEngine effects | Not implemented |
| Convex/auth, worker/providers, LiveKit media, both helper clients, notifications | Not implemented |
| Service calls, signed physical iPhone, media eviction, spoken consent/echo, push, BLE/endurance | Not tested |

The policies live in the existing Foundation-only Tasks package and are automatically included by SwiftPM. They start no camera, microphone, network connection, timer, task or BLE output. Neither a fixture-tested acceptance binding nor a media-unpublish effect establishes actual guidance or media teardown.

## Behavior covered

`FriendSelectionPolicy` retains one pending proposal without replacing an active physical task. Resolution has a 15-second budget, prompt queue/playback has a separate 10-second budget, and the full 15-second response window begins only after the specific prompt's trusted local playback completion. A positive answer requires trusted owner speech. An accessible control may decline; helper speech, ambiguous audio, assistant echo, remote claims and accessible-control Yes cannot accept.

The policy checks session/epoch, authorization version, helper identity/connection/lease, proposal and task generations, reference metadata, current-scene target identity and prompt correlation. Helper images remain semantic references. Acceptance emits old-output cancellation followed by an exact accepted-target/new-task-generation binding; it never emits guidance. Existing TaskEngine safety validation and new-generation R1 reacquisition remain mandatory before movement.

Tests cover superseded results, reused selection/prompt IDs, stale task generations, interruption before/after playback completion, Stop/End with regressing clocks, terminal helper invalidation, unsafe metadata, deadline boundaries, safe integer limits and watchdog deadlines. An external authenticated ingress must establish provenance; these Swift enums do not authenticate network callers.

`SharingAuthorizationLease` renews on a five-second schedule and caps grants at 15 seconds. Its conservative deadline starts before local request dispatch, not at response receipt. It binds scope and a locally issued instance/sequence nonce, retains at most one actionable renewal, rejects stale/duplicate replies, and closes permanently on expiry, revocation or End. Backend loss immediately disables proposals and requests pause of friend-originated work. Expiry requests local unpublish before best-effort remote eviction. Renewal never emits Resume.

Tests cover delayed/reordered replies, wrong epochs/authorization/helper leases, duplicate nonces, failed renewals, recovery before expiry, terminal closure, clock regression/overflow, missing grants and fresh instance isolation. Runtime code must arm the exposed monotonic watchdog and execute local effects without waiting for network; that integration is not delivered by this checkpoint.

## Validation performed

Toolchain: Xcode 26.6 (17F113), Apple Swift 6.3.3; project Swift language mode 5, minimum iOS 17. Simulator: iPhone 17 Pro, UDID `F6C5FDA3-8DE1-441D-BFFE-2FE0E7F272FB`, iOS 26.5 per installed simulator inventory.

| Command/check | Actual result | Local evidence |
|---|---|---|
| `swift test --package-path ios/Packages/Tasks` | 78 passed, 0 failed: 44 existing + 14 selection + 20 lease | `/tmp/r2-friend-tasks-final.log` |
| `swift test --package-path ios/Packages/HapticLink` | 44 passed, 0 failed | `/tmp/r2-friend-haptic-tests.log` |
| App test command below | 50 passed, 0 failed | `/tmp/r2-friend-app-final.log`, `/tmp/r2-friend-app-final.xcresult` |
| Release command below | Build succeeded; unsigned, not installed on a device | `/tmp/r2-friend-release-final.log` |
| Project generation | `project.pbxproj` unchanged from Git | `python3 ios/Scripts/generate_project.py`; `git diff --exit-code -- ios/Reflex.xcodeproj/project.pbxproj` |
| Downloaded specification copy | Byte-identical | `cmp` against original downloaded file |

The final composite run contains **172 passing logic/app unit cases**, including 34 new policy cases. It excludes UI audits and device/service acceptance. The initial lease-only compile overlapped an agent's source/test edits and failed due to a transient missing playback-provenance argument; final full-package validation above supersedes that attempt. Intermediate focused runs are not added to totals.

```sh
xcodebuild -project ios/Reflex.xcodeproj -scheme Reflex \
  -destination 'platform=iOS Simulator,id=F6C5FDA3-8DE1-441D-BFFE-2FE0E7F272FB' \
  -parallel-testing-enabled NO -only-testing:ReflexAppTests \
  -resultBundlePath /tmp/r2-friend-app-final.xcresult \
  CODE_SIGNING_ALLOWED=NO test

xcodebuild -project ios/Reflex.xcodeproj -scheme Reflex \
  -configuration Release -sdk iphoneos -destination 'generic/platform=iOS' \
  CODE_SIGNING_ALLOWED=NO build
```

Choose a new result path on repeat: Xcode refuses to overwrite an existing result bundle. Build output includes existing weak-variable warnings in `SessionLifecycleStressTests.swift`, signed XCTest framework stripping notices and skipped App Intents metadata extraction. No new Swift source warning was reported for these policies.

The pre-existing shared scheme edit was preserved byte-for-byte: SHA256 `540920b42c01d7e87b818a3281cdddf9a71b2ba065ed982bc5972028a7c8f511`. No commits, pushes, deployment or external teammate messages were performed. Local continuity files remain ignored and untracked.

## Remaining work and acceptance limits

The prior [accessibility validation](2026-10-24-accessibility-flows.md) still records five unsuppressed strict contrast failures. UI audits were not rerun or fixed in this policy change. Do not combine historical UI counts with this checkpoint or call the full product green.

Next integrate the policies through one serialized native coordinator boundary and authenticated backend state. Preserve exact accepted target identity through ALL_STOP, worker binding synchronization and current R1 reacquisition. Do not send a friend click directly to existing `submitTask`. Do not decode arbitrary helper/worker events into existing trusted `.command(.resume)` or Boolean confirmation callbacks. The existing evidence adapter's `.r1` verification source must never turn Gemini interpretation into detector provenance.

Resolve R1 dependencies in the [integration request](R1_FRIEND_INTEGRATION_REQUEST.md), including actual prompt playback completion, processed owner microphone, offline Stop, external native video frames, remote audio admission and accepted-target reacquisition. Continue the independent backend, worker, helper client and notification work from the combined specification. Rate limits, encoded wire schemas, decoded-frame geometry/capture, credentials, deployment and F01–F26 acceptance are still outstanding. No F-series live acceptance test is marked passed by these pure-policy tests.
