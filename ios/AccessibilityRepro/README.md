# Largest-text scrolling and accessibility audit reproduction

This standalone native iPhone SwiftUI app has no task engine, packages, sensors, audio, networking, or wristband transport. It reproduces the session's scrolling text and fixed controls separately from Reflex. It is not included in the main app's scheme or CI script.

## What the experiment established

**Xcode's contrast audit can use pixels that do not belong to the reported text.** The isolated scene produced one retained failure for the caption, with an element rectangle extending below the scroll viewport. Its exported element screenshot contains the **End assistance button and black padding outside the screen, with none of the reported caption's text**. See [the crop](Evidence/repro-caption-crop.png) and [the corresponding screen](Evidence/repro-screen.png).

In a separate old-view Reflex snapshot, a contrast-only audit failed `fixture.instructions` at `(24, -493, 354, 661)`. The [exported crop](Evidence/app-instructions-crop.png) includes 493 points of black padding above the screen, the status/navigation bars, and only the final word of the instructions. This is the same class of incorrect sampling as the inherited `session.message` crop containing the Pause button.

**The reproduction is intermittent.** The session test failed once and passed on its immediate repeat. A prior Home audit preceded the failure, but the same visual sequence also passed after adding the static control route; this is not established as its root cause. Merely clipping a long text element or placing a footer under its reported bounds did not reliably reproduce the failure. No particular audit type combination or layout change is claimed to fix it. A later controlled process-isolation check is recorded below.

The fully visible text control and the no-footer control passed. A deliberately simple partially visible text control also passed. These controls use the same large text and colors and show that overlap geometry alone is insufficient to force the diagnostic failure. An early unstyled blue Send button was correctly reported as insufficient contrast at the default size; its neutral label styling was corrected before the retained controls.

The current app, after the separate keyboard/control usability fixes, also failed on a **correct, complete message crop**. See [that crop](Evidence/app-message-crop.png) and [raw pixel measurements](Evidence/app-message-pixels.txt): 1062 × 1118 pixels, embedded sRGB IEC61966-2.1 profile, 990,915 pure white pixels and 165,117 pure black pixels. The text/background endpoint contrast is `(1 + 0.05) / (0 + 0.05) = 21:1`; grayscale edge antialiasing is not a distinct foreground/background pair. This establishes a contrast false positive in the current case, while the earlier cases additionally demonstrate incorrect crop contents. **Incorrect cropping alone is therefore not an established explanation for every failure.**

The measurement reads raw bitmap bytes using `bytesPerRow` and `bitsPerPixel / 8`. This PNG exposes three samples in a four-byte pixel stride; assuming a three-byte stride or converting each `colorAt` result would produce misleading values. Reproduce the measurement with `swift ios/AccessibilityRepro/measure_crop.swift <crop.png>`.

The evidence establishes audit failures inconsistent with the rendered text, **not a clean whole-app accessibility pass**. It does not prove VoiceOver navigation or real-device readability. It does not rule out independent layout defects: actual keyboard/large-text behavior in the assembled app must be tested separately. Every issue handler in this reproduction returns `false`; there are no exemptions or expected-failure annotations.

## Controlled screen isolation

A frozen snapshot of the current app retained the original six audit types, issue handler, screenshot/bounds assertions, and session UI. Its only test change was `app.terminate()` followed by `app.launch()` after the Home audit. The session then followed the same Start/scroll/Pause/End flow in a fresh process with identical appearance and Dynamic Type launch arguments.

The largest-text case passed **all three repetitions** (one test case, three executions), with zero failures, skips, or expected failures. All 48 frozen source/configuration inputs stayed unchanged. Each execution still recorded the pre-existing exception for wholly offscreen `fixture.instructions` at `(24, 1482, 354, 661)`; the handler was not broadened. No `session.message` failure recurred.

The two calls were then applied to the main test and tested with all three appearance cases in one run. **Dark and light passed, but largest text failed again on `session.message`.** The failing crop is byte-identical to the complete 21:1 black/white crop above (SHA-256 `b78abb2309a82218ebfc74fe1366a2e615c4f8a102a4565f0c9f136cbae044e0`). All 42 compiled source/configuration input hashes stayed unchanged through that run.

The three isolated passes therefore **did not establish a durable harness correction**. Process isolation alone is insufficient across the appearance-case sequence. The three added helper lines were reverted; the production audit semantics, audit types, and issue handler remain unchanged. No reliable harness fix has been established. Functional UI tests separately exercise uninterrupted user flows.

## Run

Requires Xcode and an installed iPhone simulator. The included project opens directly. Regenerate only after changing its two source paths:

```sh
python3 ios/AccessibilityRepro/generate_project.py
xcodebuild test \
  -project ios/AccessibilityRepro/Repro.xcodeproj \
  -scheme Repro \
  -destination 'platform=iOS Simulator,id=<simulator-udid>' \
  -derivedDataPath /tmp/reflex-accessibility-repro-derived \
  -resultBundlePath /tmp/reflex-accessibility-repro.xcresult \
  -parallel-testing-enabled NO
```

The result path must not already exist. Audit issue descriptions, screen captures, and element captures remain in the `.xcresult`. Export them with:

```sh
xcrun xcresulttool export attachments \
  --path /tmp/reflex-accessibility-repro.xcresult \
  --output-path /tmp/reflex-accessibility-repro-attachments
```

`testLargestTextCombinedAuditWithFooter` audits the first screen, enters the session, scrolls Send above the footer, then audits again. `testLargestTextFirstAuditWithFooter` performs the same transition and scroll without the first audit. Both retain initial, pre-audit, post-audit, and issue screenshots plus frame descriptions. The three `testSampling…` cases remove almost everything except a long Text, the ScrollView, and a footer, varying only the text position or footer presence.

For repeats, retain distinct result bundles and count failures rather than treating one pass as proof the defect disappeared. Do not run multiple timed app-flow simulator suites alongside this diagnostic; simulator contention can trip the real app's safety watchdog.

## Recorded results — October 24, 2026

Environment: Xcode 26.6 (17F113), iPhone 17 Pro simulator, iOS 26.5 (23F77). All results are simulator-only.

| Diagnostic run | Actual outcome |
| --- | --- |
| `/tmp/reflex-repro-baseline.xcresult` | 2 passed / 1 failed: default blue Send contrast. Early scene, before styling correction. |
| `/tmp/reflex-repro-combined.xcresult` | 4 passed: no initial-screen audit, contrast-only and combined controls. |
| `/tmp/reflex-repro-input.xcresult` | 4 passed after adding input and navigation. |
| `/tmp/reflex-repro-caption-focus.xcresult` | 1 passed after adding selectable caption and accessibility focus. |
| `/tmp/reflex-repro-home.xcresult` | **1 failed:** caption contrast, invalid footer/black-padding crop retained above. |
| `/tmp/reflex-repro-controls.xcresult` | 5 passed: repeated-audit scene, first-audit scene, fully visible, partially visible, and no-footer controls. |
| `/tmp/reflex-repro-overlap.xcresult` | 3 passed: static controls with footer text explicitly positioned inside the oversized text rectangle. |
| `/tmp/reflex-real-contrast-only.xcresult` | **1 failed:** old-view Reflex instructions contrast, partially offscreen crop retained above. |
| `/tmp/r2-accessibility-final.xcresult` | Current app largest-text audit failed on the complete black/white message crop, despite valid bounds and 21:1 endpoints; other suite results are recorded by the main validation report. |
| `/tmp/reflex-appearance-final.xcresult` | **2 passed / 1 failed:** main-project dark/light/largest run with process isolation; largest failed on the same complete black/white crop. This disproved the proposed correction. |
| `/tmp/reflex-isolated-screen-repeat.xcresult` | **3/3 repetitions passed** for the current app with process isolation; original wholly-offscreen instruction exception unchanged. |
| `/tmp/reflex-real-relaunch.xcresult` | **1 passed:** old-view Reflex with all six audit types, terminating/relaunching between Home and Session audits. A single diagnostic pass; no production test change was made from it. |

Each result bundle has a matching `.log` in `/tmp`. These temporary bundles are local; the selected crops above preserve the diagnostic observation in the repository. Earlier runs represent incremental reductions and should not be summed as the current app's test count.

Apple describes audits as screen-level checks and explicitly says passing them does not establish full accessibility: [Performing accessibility audits](https://developer.apple.com/documentation/accessibility/performing-accessibility-audits-for-your-app). The [WWDC audit session](https://developer.apple.com/videos/play/wwdc2023/10035/) discusses investigating false positives. This reproduction records the evidence without broadening Reflex's issue handler.
