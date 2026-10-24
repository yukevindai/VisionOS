# Setup and calibration accessibility evidence — October 24, 2026

These are unedited iPhone 17 Pro simulator screenshots (iOS 26.5, 402 × 874 points, 3× pixels), at the largest accessibility text size. They are **not physical-device or VoiceOver validation**. The images contain explicitly simulated assistance, no real camera, microphone, or wristband output.

## Changes and validation

Calibration Stop is outside the scrolling answer list. Active/preparing/recovering assistance has a persistent Pause action throughout Settings, including pushed wristband pages. At accessibility text sizes, Done and Cancel use growing footer controls; regular text retains native toolbar controls. Inline preference choices are a reviewed readability improvement, not a separately reproduced clipping failure. Incorrect calibration answers retain their correction message after replay.

`SetupAccessibilityUITests` contains five functional tests and five strict `.all` audits. The complete ten-test run `/tmp/reflex-setup-accessibility-final.xcresult` produced **6 passes / 4 failures**: every functional flow and the wristband setup audit passed. The functional flows cover setup Cancel/reopen, denied-permission recovery, hand/mount choices, session start/end, simulation band-discovery instructions, all eleven calibration answers, mismatch/repeat, immediate Stop, exit/restart, and Settings → wristbands → Pause → explicit session Resume.

The final `Color.primary` correction changes only intended label colors. Four affected audits were rerun in `/tmp/r2-setup-color-verified.xcresult`; **all four still reported contrast failures**, with the element/crop details below. No new audit exemption or suppression was added. The native toolbar Dynamic Type findings did not recur with the accessible footer controls.

Band discovery in simulation intentionally returns no physical devices. Actual left/right assignment, physical calibration, spoken Stop, VoiceOver/audio coexistence, and live BLE remain unverified.

## Retained images

| File | Source and meaning |
| --- | --- |
| [calibration-before-blue-light.png](calibration-before-blue-light.png) | From `/tmp/reflex-setup-accessibility-v4.xcresult`, app failure screenshot `157DE0B2-7668-4B90-B156-6F5D513F9FCC.png`. Native blue answer text before the color correction. |
| [calibration-after-light.png](calibration-after-light.png) | Current-color run, attachment `0BE7DC7C-8EAA-4BEF-9694-874022418AA4.png`, captured before the light audit. Explicit `Color.primary` produces black answer text. |
| [calibration-after-dark-before-audit.png](calibration-after-dark-before-audit.png) | Current-color run, attachment `2E867244-A4BB-417D-9A02-5D47F134B5DC.png`. White answer text; **the Stop shape has no visible label in this particular capture**. |
| [calibration-after-dark-after-audit.png](calibration-after-dark-after-audit.png) | Same dark test, attachment `9BBC5F0E-031B-4258-BB6E-792C08114B0F.png`, captured with the audit failure. The white Stop label is visible. This difference must not be hidden or treated as proof of stable physical-device rendering. |
| [audit-calibration-dark-crop.png](audit-calibration-dark-crop.png) | Reported element `calibration.answer.Move up`, frame `(16, 13.3, 370, 93.3)` points. The crop contains status/navigation chrome and blurred underlying content, rather than an isolated answer row. Attachment `F3C68320-EDA8-4F62-8210-A32B33C89CF3.png`. |
| [audit-calibration-light-crop.png](audit-calibration-light-crop.png) | Reported element `calibration.answer.Move down`, frame `(16, 106.7, 370, 93.3)`. The crop includes native navigation content/blur across the row's upper portion. Attachment `8C8A6147-3677-4B2E-9CE2-593BBA57ADAF.png`. |
| [audit-settings-choice-crop.png](audit-settings-choice-crop.png) | Reported selected choice “Held in my other hand”, frame `(16, 661.7, 370, 155.3)`. The crop contains the clipped choice plus the separate Done footer. Attachment `73A859F1-178C-4171-AE05-1F2E22EDCAFA.png`. |
| [audit-setup-instructions-crop.png](audit-setup-instructions-crop.png) | Reported `setup.instructions`, frame `(16, 547.3, 370, 775.3)`. The crop contains the clipped instruction, Cancel footer, home indicator, and black padding beyond the screen. Attachment `E2FB0B76-8363-42A9-BFA1-5DE06CB4B269.png`. |

All “current-color run” images come from `/tmp/r2-setup-color-verified.xcresult`. Full exports and the manifest are at `/tmp/reflex-setup-color-verified-attachments/`; root also exported `/tmp/r2-setup-color-verified-attachments/`. Earlier nil-element contrast reports are superseded for diagnosis by these identified elements and retained crops; the strict test failures remain.

The crops show why a full accessibility-element frame is not necessarily an isolated rendered-text sample after scrolling. Measured legible pixels do not make the strict audit pass and do not prove every accessibility behavior is correct.

## Measured colors

The PNGs embed `sRGB IEC61966-2.1`. [measure_colors.swift](measure_colors.swift) reads the raw 8-bit interleaved bitmap using `bitsPerPixel / 8` for each pixel and `bytesPerRow` for each row. This matters: these images expose three color samples in a four-byte padded pixel. A three-byte stride gives invalid colors.

Run on macOS:

```sh
swift ios/Validation/Evidence/2026-10-24-setup-accessibility/measure_colors.swift \
  ios/Validation/Evidence/2026-10-24-setup-accessibility/calibration-after-light.png
```

For each measured sRGB channel `c = byte / 255`, linearize with `c / 12.92` for `c ≤ 0.04045`, otherwise `((c + 0.055) / 1.055)^2.4`. Relative luminance is `0.2126R + 0.7152G + 0.0722B`. Contrast is `(Llighter + 0.05) / (Ldarker + 0.05)`.

| Rendered pair | Contrast | Interpretation |
| --- | --- | --- |
| Before: blue `#0088FF` on white `#FFFFFF` | 3.52:1 | Meets large-text 3:1, below normal-text 4.5:1. This value alone does not explain a largest-text audit failure. |
| After, light answers: `#000000` on `#FFFFFF` | 21.00:1 | Meets both text thresholds. |
| After, dark answers: `#FFFFFF` on `#2C2C2E` | 13.94:1 | Meets both text thresholds. |
| Visible Stop text: `#FFFFFF` on `#005ABE` | 6.55:1 | Meets both text thresholds; see the dark pre-audit capture caveat. |
| Done/Cancel text: `#000000` on `#F2F2F7` | 18.82:1 | Meets both text thresholds. |

The initial `.foregroundStyle(.primary)` attempt did not remove the native button tint because it selected `HierarchicalShapeStyle.primary`. The final code explicitly uses `Color.primary` and the after images confirm the difference. A provisional **2.89:1** measurement came from an incorrect `NSBitmapImageRep.colorAt` → sRGB conversion; it is superseded and must not be used. Raw bitmap colors were checked against the app's known `#005ABE` action fill.

The remaining dark Stop visibility discrepancy and live accessibility checks require follow-up; these records do not approve untested behavior.
