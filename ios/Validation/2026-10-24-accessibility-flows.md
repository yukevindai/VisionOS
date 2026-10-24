# Accessibility reproduction and simulated task-flow validation

This work starts from `17cfb0b`; validation below was completed before publication. It preserves the native iPhone target and R1/R3/H1 ownership. No physical-device, real microphone, VoiceOver, camera, BLE or actuator pass is claimed.

## Audit diagnosis

The [standalone reproduction](../AccessibilityRepro/README.md) removes task logic, adapters, sensors and audio. It retained a contrast failure whose reported caption crop contains the End button and black offscreen padding instead of caption text. A separate old-view app run sampled navigation/status content and offscreen padding for `fixture.instructions`. Fully visible controls pass; related scene repeats also pass. The sampling failure is intermittent, and its precise trigger is not established. The latest app run additionally failed on a complete, correctly cropped session message whose raw sRGB foreground/background endpoints are black/white (21:1). Incorrect crops are therefore one observed failure mode, not the sole explanation for audit false positives.

This establishes audit behavior for the retained session failure, not a blanket accessibility pass. A frozen current-app control passed three consecutive executions with a fresh process between Home and Session audits. However, the combined dark/light/largest appearance sequence then produced two passes and the same largest-text failure; its message crop was byte-identical to the 21:1 crop. The proposed relaunch workaround was reverted. No reliable harness correction was established. `ReflexUITests.swift` remains unchanged, including all six audit types and its existing narrow wholly-offscreen fixture-instructions exception. No new exception or expected-failure annotation hides partially visible failures.

The four setup/calibration reports persist after the color correction. Their [retained crops and measurements](Evidence/2026-10-24-setup-accessibility/README.md) show navigation chrome, separate Done/Cancel controls, or offscreen padding within the reported text frames. These support a sampling problem but do not establish that every reported setup element or accessibility behavior is correct. A bounded standalone Form comparison did not reproduce contrast findings; its test failed because the reveal helper overscrolled a target before the audit, so it is inconclusive rather than a passing suite (`/tmp/r2-form-repro-result.xcresult`).

## Actual app defects and changes

| Severity | Location | Before | After | Why |
| --- | --- | --- | --- | --- |
| High | `App/Views/AssistanceSessionView.swift` | Largest text with keyboard open truncated Pause, overflowed End, and prevented reaching Yes after Send. | Compact visible Pause/Resume/End at accessibility sizes, full accessibility labels retained; Send dismisses the keyboard. | Confirmation and stable safety controls remain usable without reducing text size. |
| High | `App/Views/SettingsView.swift` | Stop calibration followed eleven answers and was absent from the initial accessible viewport. Settings could cover session controls while assistance remained active. | Persistent calibration Stop; persistent Pause across Settings and pushed wristband pages during active/preparing/recovering phases. | Stopping does not require scrolling through answers or navigating back to the session. |
| Medium | `App/Session/SessionCoordinator.swift` | A wrong calibration answer set corrective text and immediately overwrote it when replaying the pattern. | Replay happens before retaining corrective feedback, unless playback itself stops calibration. | The user can read why the step did not advance. |
| Medium | `App/Session/SessionCoordinator.swift` | A blocked-door task paused safely but unified Stop erased its specific explanation. | Cancellation and ALL_STOP still execute first; task-pause speech follows unified Stop, preserving the actual cause and readable explanation. | The user hears that the door may be locked/heavy/blocked and is asked to seek help. |
| High | Calibration and setup/band action labels | Native blue action text rendered as `#0088FF` on white (3.52:1), below the normal-text 4.5:1 threshold. | Explicit `Color.primary` for neutral actions (hierarchical `.primary` still inherited the button tint); established blue/white palette retained for Pause/Stop. | WCAG 1.4.3: readable action labels in both appearances. This does not attribute every largest-text audit failure to color. |
| Medium | Setup/settings preferences and instructions | Compact pickers and native Done/Cancel toolbar text limited largest-text readability. | Inline choices, vertically expanding instructions, accessibility-size Done/Cancel footers; native toolbar preserved at regular text sizes. | Hand/mount choices, cancellation and explanatory text can be read and operated at large text. |

The corrected calibration labels measure **21:1** in light (`#000000` on `#FFFFFF`) and **13.94:1** in dark (`#FFFFFF` on the rendered `#2C2C2E` card).

The baseline contrast measurement uses raw pixels from the screenshot tagged sRGB IEC61966-2.1. An initial AppKit color-conversion result of 2.89:1 was incorrect and is superseded by 3.52:1; the known footer raw value `#005ABE` matched the declared palette and measured 6.55:1 against white.

The largest-text keyboard regression has retained [before](Evidence/keyboard-largest-before.png) and [after](Evidence/keyboard-largest-after.png) screenshots. The final flow additionally taps Yes and End successfully.

No text-size cap, guidance freshness relaxation, fabricated physical discovery, or additional speech/camera engine was introduced. The test scroll helper targets `session.content`, avoiding the keyboard's internal ScrollView. Settings navigation uses the native Back identifier rather than whichever toolbar button happens to appear first.

## Simulated UI coverage

`SimulatedTaskFlowUITests` drives the assembled app through visible buttons and labeled typed fixture input:

- Button: explicit result confirmation, No/retry, cancel/restart, completion and End.
- Object: Pause/Resume, cancel/restart, grasp confirmation, tracking loss/recovery with fresh confirmation.
- Kiosk: ambiguous choice, cancel/restart, two-item sequence and payment handoff.
- Printed menu: sections, Back, question, cancel/restart.
- Door: No/retry, cancel/restart, opening confirmation and fixture R1 passage authorization.
- Largest-text button request with keyboard, Send, confirmation, Pause/End reachability.

`RecoveryTaskFlowUITests` adds DEBUG-only scenarios gated by `--simulate --uitesting --ui-task-scenario`: wrong kiosk destination with fresh Back recovery, kiosk reset requiring explicit restart, and blocked door followed by cancellation and a different-door request. The scenarios are synthetic and never instantiate live dependencies. No payment occurs.

`SetupAccessibilityUITests` separately checks functional setup/calibration flows and strict accessibility audits. It covers permission explanation/recovery, hand/mount choices, band instructions/discovery availability, repeat/mismatch, all eleven calibration responses, Stop and navigation exit. Simulation has no physical discoveries; left/right physical assignment and actual pattern identification remain device work.

## Reproduced failures and intermediate evidence

- `/tmp/r2-ui-flows-scoped.xcresult`: four task flows passed; tracking-recovery navigation failed because an offscreen virtualized Settings row was queried before scrolling. Printed-menu flow hit tracking-freshness Stop while multiple simulators were active. The unchanged menu flow passed in `/tmp/r2-ui-flows-targeted.xcresult`; the strict 300ms guard was not relaxed.
- `/tmp/r2-ui-flows-targeted.xcresult`: largest-text confirmation failed before the keyboard/control fix; `/tmp/r2-largest-flow-current.png` shows truncation/overflow with the keyboard. Tracking-recovery test then exposed generic toolbar indexing, corrected to the actual Back identifier.
- `/tmp/reflex-setup-stop-baseline.xcresult`: baseline largest-text Stop was absent after Begin calibration.
- `/tmp/r2-recovery-baseline.xcresult`: two kiosk cases passed; blocked-door case failed with generic pause text. `/tmp/r2-recovery-fixed.xcresult`: all three passed, zero skips.
- Reproduction experiments and retained crops are enumerated in the standalone README. Their historical passes/failures must not be added to the final app suite count.

## Final validation

The following focused runs cover **169 unique app/package cases: 164 passed and five strict contrast audits failed**, with zero skips. This is a composite of focused runs, not a clean CI execution. It comprises **138 passing logic tests, all 23 functional UI tests passing, and three passing/eight total audit tests**. Diagnostic repetitions are excluded.

| Group | Pass | Fail | Artifact |
| --- | ---: | ---: | --- |
| Tasks package | 44 | 0 | `/tmp/r2-accessibility-tasks.log` |
| HapticLink package | 44 | 0 | `/tmp/r2-accessibility-haptics.log` |
| App unit + existing UI + core task UI | 67 | 1 | `/tmp/r2-accessibility-final.xcresult` |
| Recovery UI | 3 | 0 | `/tmp/r2-recovery-fixed.xcresult` |
| Setup/calibration functional + audits | 6 | 4 | `/tmp/reflex-setup-accessibility-final.xcresult` |
| **Unique-case total** | **164** | **5** | **No skips or expected failures** |

The 68-test app run contains 50 app-unit passes, all seven core task-flow passes, and 10/11 existing UI passes. Its only failure is the strict largest-text session audit described above. The setup run contains all five functional passes, the passing wristband setup audit, and four contrast failures (setup, Settings, calibration light/dark). After the final explicit `Color.primary` correction, those four affected audits were rerun in `/tmp/r2-setup-color-verified.xcresult`; all four still failed contrast. Those executions replace the earlier outcomes for these same cases, rather than adding four new cases. The five functional setup cases were not repeated for the label-color-only correction.

A dark pre-audit screenshot shows the Stop shape without its label; the subsequent failure screenshot shows the label correctly. Both unedited captures are retained in the setup evidence. Stable dark-mode rendering and physical-device behavior are not established by this discrepancy.

The unsigned Release generic iPhone build passed against final production source (`/tmp/r2-accessibility-release-final.log`). The Release binary contains none of `--ui-task-scenario`, `UITestScenario`, `--ui-permission-scenario`, or `--ui-appearance`. Both project generators produced byte-identical projects; Python generator syntax and whitespace checks passed. The original app audit helper was restored byte-for-byte; the final 49-input source manifest is `/tmp/r2-accessibility-completed-source.json`. Both continuity files remain ignored and untracked. No commit, push, or deployment was part of the validation runs.

Environment: Xcode 26.6 (17F113), Swift 6.3.3 / Swift 5 language mode, iPhone 17 Pro simulator on iOS 26.5 (23F77), minimum deployment iOS 17. Simulator tests are serialized after concurrent runs demonstrated a freshness pause under load.

Real iPhone VoiceOver traversal while R1 speaks, Reduce Motion, Increase Contrast, physical pairing/calibration, actual permission prompts, signed install and live T01–T14/R2-A–F remain unrun. Follow [DEVICE_ACCESSIBILITY.md](DEVICE_ACCESSIBILITY.md). A simulator audit or unsigned device build does not establish those outcomes.
