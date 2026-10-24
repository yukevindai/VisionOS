# R2 connection, event ordering, and wristband safety

October 24, 2026. Native iPhone R2 continuation above `a1a663742ef4ee72deaccb37d34a79b788d29487`. Changes are local and uncommitted. No teammate source, shared contract, or hardware configuration was changed.

## Changes

- Session startup and conversation recovery share one occupied connection-attempt slot. Cancellation retains the slot until the adapter returns, preventing repeated Start/End or recovery timeouts from accumulating noncooperative starts. Start reports the pending cancellation; stalled recovery pauses with an explanation. A late callback cannot revive assistance, and a new explicit Start works after the old call returns. End remains synchronous and the coordinator is not retained across the adapter wait.
- Home shows the ended-session message, including the explanation when a previous connection attempt blocks Start.
- Tasks rejects regressing event timestamps within a pending request. An older `onTarget` cannot request a physical action after a newer guidance status. Distinct events with equal millisecond timestamps remain allowed; each new request resets the comparison.
- HapticLink checks output expiry between heartbeat writes and flushes priority stops. An output expiring during the first wrist's write cannot receive a heartbeat on the second wrist. Bluetooth loss clears cached connection/status evidence; a new synchronization proof requires fresh readiness and status.

## Validation

Environment: Xcode 26.6 (17F113), Swift 6.3.3 in Swift 5 language mode; iPhone 17 Pro simulator, iOS 26.5 (23F77), UDID below. Minimum target remains iOS 17.

- Tasks: **42 passed**.
- HapticLink: **42 passed**.
- App unit: **49 passed** (27 coordinator, 10 lifecycle, 7 composition, 5 band-service).
- UI: **10 passed, 1 failed**. `testAccessibilityAuditWithLargestAccessibilityText` reports contrast failure on `session.message`. Light/dark audits and other UI cases passed.
- Total: **143 passed, 1 failed**. The `.xcresult` independently reports 59 app/UI passes, 1 failure, 0 skips. The shared CI script exited 1; this is not a passing full-suite result.
- Unsigned Release generic iPhone build **passed**. No signed installation or device run.
- All 44 validation-input hashes match the final source. Generated project regeneration is byte-identical; whitespace checks passed.

### Unresolved accessibility audit

The failure screenshot contains black text on white, with the sampled `session.message` rectangle extending below the scroll viewport into the fixed Pause control. A short-copy diagnostic made the complete message visible, but the audit still failed on that black-on-white message. Auditing before manual scrolling instead failed on `session.caption`, which extended below the viewport. These probes do not establish a root cause or a fix. Both diagnostic edits were reverted; the final source matches the original full run exactly. No exception was added or broadened. The existing narrow offscreen `fixture.instructions` exception remains as documented in [CI_ACCESSIBILITY.md](CI_ACCESSIBILITY.md).

Before calling accessibility validation complete, investigate the audit sampling and scroll behavior with the retained crops and a minimal reproduction, or obtain a toolchain-level diagnosis. Do not suppress visible/partially visible text failures or claim a device accessibility pass from these screenshots.

Diagnostic artifacts:

- `/tmp/reflex-r2-lifecycle-attachments/`: full-run largest-text screenshots, crop, issue text, and recording.
- `/tmp/reflex-r2-lifecycle-large-text.xcresult` and matching `.log`: short-copy diagnostic failure; exported failure screenshots in `/tmp/reflex-r2-lifecycle-large-text-attachments/`.
- `/tmp/reflex-r2-lifecycle-audit-order.xcresult` and matching `.log`: pre-scroll audit diagnostic failure on the caption.

The two new app regressions failed against the prior coordinator: repeated Start/End accumulated 21 held starts, and reconnect timeouts accumulated three held recovery attempts. After the fix, all 10 lifecycle tests passed, including the existing weak-ownership cleanup cases. The new Tasks ordering regression and both HapticLink regressions also failed before their respective fixes; the agents then passed all 42 Tasks and 42 HapticLink tests.

The final local command uses the shared CI script:

```sh
IOS_SIMULATOR_UDID=F6C5FDA3-8DE1-441D-BFFE-2FE0E7F272FB \
CI_ARTIFACT_DIR=/tmp/reflex-r2-lifecycle-verified \
bash ios/Scripts/ci_xcodebuild.sh
```

Temporary evidence: `/tmp/reflex-r2-start-regression-before.log`, `/tmp/reflex-r2-start-regression-after.log`, `/tmp/reflex-r2-lifecycle-verified.log`, the logs and `Reflex-tests.xcresult` inside `/tmp/reflex-r2-lifecycle-verified/`, and `/tmp/reflex-r2-lifecycle-source.json` for frozen validation-input hashes.

## Hosted CI and remaining integration

The first [hosted CI run](https://github.com/yukevindai/VisionOS/actions/runs/40076838872) completed successfully for baseline commit `a1a6637`, confirmed at the final session check. That run does not validate these newer local changes or resolve the local largest-text failure. No workflow or CI script defect was demonstrated, and those files were unchanged.

Native R1/R3 adapters and confirmed H1 configuration remain absent from the checked remote and checkout. Default live launch still reports unavailable integrations. These tests use synthetic adapters and transport; no real microphone, offline recognizer, camera/depth, physical BLE delivery/onset, signed install, VoiceOver/audio coexistence, firmware failsafe, or wall-clock 20-minute endurance was tested. Use [adapter acceptance](ADAPTER_ACCEPTANCE.md) and the [device checklist](DEVICE_ACCESSIBILITY.md) when actual integrations and hardware arrive.
