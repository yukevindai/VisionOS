# R2 Bluetooth connection and task acceptance continuation

October 24, 2026. Continues the local [lifecycle safety checkpoint](2026-10-24-lifecycle-safety.md) above published baseline `a1a6637`. Existing uncommitted changes were preserved. No teammate module, shared hardware contract, commit, push, or deployment was changed.

## Changes

- Reconfirming an assigned ready or connecting band no longer restarts its connection. Previously this could clear the CoreBluetooth command characteristic before the controller attempted Stop. The controller now preserves active connection states; the transport independently preserves an existing connection or pending retry and its retry budget. An explicitly disconnected or failed pairing can still be retried.
- A task response rejected for semantic reasons no longer consumes its event ID or advances the pending request's timestamp watermark. Previously a wrong-source or wrong-target message could suppress a later valid R1 status. Accepted statuses still enforce event ordering; a new request resets its own watermark. Missing physical evidence still invalidates the generation, cancels work, and sends Stop.
- The [Bluetooth integration checklist](BLUETOOTH_INTEGRATION.md) identifies the actual composition seams and required H1 inputs. The user confirmed the planned connection will use Bluetooth; BLE/GATT compatibility and all firmware-specific values remain unconfirmed.

## Regression evidence

The two pairing regressions failed before the fix and passed afterward. They cover repeated confirmation during active output, each connection phase, and explicit retry after disconnection/fault. These use a synthetic transport and do not exercise physical CoreBluetooth delegate ordering. Logs: `/tmp/reflex-r2-bluetooth-before.log` and `/tmp/reflex-r2-bluetooth-after.log`.

The rejected-event regression failed before the fix with eight assertions across wrong-source/wrong-target and reused/distinct event IDs. That pre-fix output was captured in the agent tool session, not a disk log. The added missing-evidence regression verifies cancellation, generation invalidation, ALL_STOP, and rejection of late work. All 44 Tasks tests passed afterward; log: `/tmp/reflex-r2-tasks-rejected-events-after.log`.

Independent cross-review found no further actionable issue in either fix. GPT-6 Astra handled Bluetooth safety, GPT-6.1 Sol handled task acceptance, and GPT-6 Luna prepared the integration checklist. Root reviewed the changes and ran combined validation.

## Accessibility investigation

The prior failure screenshots were inspected. Replacing the session's inner stack with `LazyVStack` reproduced the same `session.message` contrast failure. Moving the fixed footer to a safe-area inset instead failed to reveal the simulated Send control. Neither probe established an accessibility fix; both were reverted. The final session view and UI tests are byte-identical to the incoming checkpoint. No audit exception was added or broadened.

Diagnostic bundles and matching logs: `/tmp/reflex-r2-lazy-audit.xcresult` and `/tmp/reflex-r2-inset-audit.xcresult`. Prior short-copy/pre-scroll probes remain documented in the lifecycle checkpoint; do not repeat them as fixes. A minimal reproduction of the audit's scrolling/sampling behavior is still needed. Apple's [audit documentation](https://developer.apple.com/documentation/accessibility/performing-accessibility-audits-for-your-app) describes the screen-level checks; an automated result does not replace manual accessibility testing.

## Combined validation

**147 passed, 1 failed out of 148 tests.** Tasks 44 passed, HapticLink 44 passed, app unit 49 passed, UI 10 passed/1 failed. The sole failure remains `testAccessibilityAuditWithLargestAccessibilityText`, reporting contrast on `session.message`. Light/dark audits passed. The shared CI script exited 1; this is not a passing full-suite result. The `.xcresult` independently reports 59 app/UI passes, 1 failure, 0 skips.

The unsigned Release generic iPhone build passed. Generated project regeneration was byte-identical, whitespace checks passed, and all 46 frozen source/config/script hashes remained unchanged after validation. Both local continuity files remain ignored and untracked.

Environment: Xcode 26.6 (17F113), Swift 6.3.3 in Swift 5 language mode, minimum iOS 17; iPhone 17 Pro simulator running iOS 26.5 (23F77).

```sh
IOS_SIMULATOR_UDID=F6C5FDA3-8DE1-441D-BFFE-2FE0E7F272FB \
CI_ARTIFACT_DIR=/tmp/reflex-r2-bluetooth-verified \
bash ios/Scripts/ci_xcodebuild.sh
```

Artifacts: `/tmp/reflex-r2-bluetooth-verified/` contains individual logs and `Reflex-tests.xcresult`; aggregate log `/tmp/reflex-r2-bluetooth-verified.log`; source/config/script hash manifest `/tmp/reflex-r2-bluetooth-source.json`. These temporary artifacts are not committed. The earlier hosted baseline run does not cover these local changes.

## Remaining integration

Native R1/R3 adapters and H1's confirmed contract are still absent. Production band configuration remains unavailable. No actual camera, microphone, offline spoken Stop, physical BLE delivery/onset, firmware failsafe, signed install, VoiceOver/audio coexistence, or wall-clock endurance was validated. Use [adapter acceptance](ADAPTER_ACCEPTANCE.md), [device accessibility](DEVICE_ACCESSIBILITY.md), and the team T01–T14/R2-A–F procedures when those inputs arrive.
