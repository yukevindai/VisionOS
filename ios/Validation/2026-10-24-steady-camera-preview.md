# Continuous camera preview

October 24, 2026. Native iPhone preview correction; no backend or recognition-policy change.

The Call background previously displayed the same JPEG snapshots used for recognition. Those snapshots are produced at most five times per second, after Vision and depth processing, and expire 300 ms after capture. Processing delays could consume the remaining display lifetime, repeatedly replacing camera imagery with the black placeholder.

The existing single AR session now feeds a separate preview renderer directly from capture. It renders an uncropped, upright image at up to 15 frames per second, with a longest edge of 960 pixels and no JPEG encoding. One pending capture and one pending main-actor image are retained; slower consumers receive the newest frame rather than an accumulating queue. Recognition snapshots cannot replace or expire the independent preview.

The preview still clears when no fresh capture arrives for 300 ms. Stop, End, backgrounding, session generation and camera geometry continue to invalidate old images. Preview pixels do not become recognition or movement evidence, and cannot satisfy the evidence required to Resume. Vision processing retains the original capture's context, rotation and geometry instead of attaching metadata from a later capture. All existing guidance/evidence freshness limits remain unchanged.

## Validation

- Xcode 27.1, iOS 26.5 simulator: **206 app unit tests passed**, including seven new preview tests. A final focused four-test renderer/mailbox rerun passed after adding an out-of-order capture case.
- Swift package tests: **167 passed** (Guidance 11, Targets 20, Tasks 92, HapticLink 44).
- Unsigned Release iPhone build passed (`/tmp/reflex-steady-preview-release.log`).
- Generated project is deterministic; `git diff --check` passed.
- Physical iPhone smoothness, rotation, interrupted camera capture and Stop/Resume/End acceptance remain for the integrated device build. Simulator tests do not establish a live-device pass.
- Full UI/accessibility audits were not rerun locally for this change. Historical light/dark simulated-caption contrast failures remain unsuppressed; required-check success is not a full iOS pass.

Local logs: `/tmp/reflex-steady-preview-final-tests.log`, `/tmp/reflex-steady-preview-order-tests.log`, `/tmp/reflex-steady-preview-{Guidance,Targets,Tasks,HapticLink}.log`.
