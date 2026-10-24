# Current modular/presentation validation — 2026-10-24

68 offline tests passed in 4.906 seconds. No live hardware test. See REFACTOR_HANDOFF.md for scope, unchanged algorithm evidence, source provenance and remaining limits. Earlier validation follows and describes historical configurations.

---

# Current: nonblocking detection / stale-overlay repair

[ORCHESTRATION_AND_LATENCY_FIX.md](ORCHESTRATION_AND_LATENCY_FIX.md) records actual CLI Claude diagnosis/reviews, the measured 63 ms versus approximately 418 ms age gap, final source changes and 41 targeted passing checks. Hardware performance remains unverified. Earlier scheduling descriptions below are historical.

---

> Historical record: external-source research integration and earlier scheduling described below are superseded. See [CLEANUP_AND_ACQUISITION.md](CLEANUP_AND_ACQUISITION.md) for the current independent runtime and validation.

# Current: landmarks and distance connections

[REFERENCE_RUN_FINDINGS.md](REFERENCE_RUN_FINDINGS.md) supersedes the historical claims below. It audits the actual 62-frame run and records 34 passing checks plus one saved-frame smoke using the original local detector. No hardware success is claimed. The local adapter is ignored and excluded from the submission checkout.

---

# Current paired-frame integration repair

See [REFERENCE_INTEGRATION.md](REFERENCE_INTEGRATION.md) for the current implementation, exact failure evidence, reused components and 33 targeted passing checks. The historical sections below describe superseded scheduling and manual-selection workflows; they are not post-repair performance claims.

---

# Automatic detection restored - hardware behavior still unverified

This section supersedes the historical manual-default mode below. Automatic YOLO is now the default; no rectangle is required. Optional manual selection requires detection_mode=manual.

Reference inspection: launch-laptop.ps1 selects CPU, COCO detector, target cup and object option 2. The model is yolo11s.pt, input 640, confidence 0.2, float32, COCO cup/class 41. Verified Ultralytics prediction settings are rect=True, class-aware NMS IoU 0.7, max_det=300. DeskGuide now explicitly uses those settings. Both checkpoints have SHA-256 85a76fe86dd8afe384648546b56a7a78580c7cb7b404fc595f97969322d502d5. Our existing weights were independently downloaded; no reference source was copied/imported.

Existing preparation is unchanged: 960-pixel long side, aligned nearest-neighbor depth, clockwise 90-degree rotation with intrinsics/pose adjustment. YOLO receives BGR and performs its existing letterboxing/normalization. The reference converts its prepared RGB back to BGR. Hand geometry is unchanged.

ACTUAL PREDICTION: On the supplied Proprio screenshot cropped to the camera area, the silver Diet Pepsi can predicts cup/class 41 at confidence 0.4567316. The black tumbler also predicts cup at 0.8400338; the rear can predicts cup at 0.282254. This is screenshot inference with overlays, not a clean sensor recording. Evidence: logs/reference-screenshot-cup-prediction.json and .jpg. On existing raw frame logs/identical-input-hand-visible/0000.npz, the black tumbler predicts cup at 0.52864 but the central can's back side is NOT detected. Evidence: logs/can-class-audit.json and .jpg. There is no dedicated beverage-can category and other cup-like objects can compete.

Cause: the prior manual-only branch incorrectly inferred from the absent dedicated can class that YOLO could not recognize this can; it skipped YOLO whenever target=can. The user-facing can target now maps to observed class 41 and uses the automatic engine. Default drawing bindings/instructions are removed; improved UI is retained.

Restored selection rules, independently implemented in ObjectTracker: consider up to six confidence-ranked candidates; reachable depth <=1.1 m ranks above unknown and far; then rank by confidence +0.5*IoU with the previous box. Retain the reference's camera-space near-target guard against a nonoverlapping unknown/far replacement. Existing current-depth, hand-occlusion, movement-confirmation and contact-surface checks remain.

Existing real-time repairs remain active: independent acquisition/decode with single overwrite slots, one asynchronous pending detector job, no detector wait in the hand loop, stale/generation checks, delayed-box optical-flow validation before current depth sampling, and expiring audio commands. The 300 ms gate remains unchanged. These changes do not prove CPU/network timing meets that deadline.

Quick checks: seven automatic/manual fallback checks and fourteen tracking tests passed. They cover model routing, automatic acquisition without selection, depth/continuity ranking and existing tracking. No live tests, new recordings, benchmarks or audible playback were run.

UNVERIFIED: reliable automatic selection of the intended can, back-side detection/reacquisition, occlusion/movement, reflective LiDAR, fresh hand/target XYZ on hardware, real FPS/latency and directional sound. Multiple cup predictions remain ambiguous. Native iOS integration is future work. Historical live results below are not post-change measurements.

---

# Beverage-can mode and interface update — awaiting user hardware testing

The current default is ONLY the manually selected beverage can. The local YOLO11s checkpoint metadata was inspected:80 COCO labels, no beverage-can class. No can-to-cup assumption is used. YOLO is not instantiated, warmed up or run in can mode. No new model or dependency was added.

Select by clicking and dragging a tight rectangle around the can in the RGB preview. Mouse coordinates account for image scaling and letterbox margins. The selected image initializes an appearance reference only; it never authorizes old depth. Current-frame local normalized template matching at bounded scales, existing checked LK motion and valid camera pose locate that selected appearance. Ambiguous/weak matches, hand overlap, lost tracking, changed camera epoch, missing valid current depth or evidence older than300ms pause guidance. A lost selection asks for manual reselection; it cannot switch to a COCO cup or another target automatically.

`SelectedCanTracker` reuses the existing target front-surface geometry, confirmation/movement checks, world anchoring and reachable contact rectangle. Every valid target update samples the current paired LiDAR frame. The hand/fingertip algorithms were left unchanged in this update. Receiver/decode overwrite slots remain bounded; removing YOLO also removes its CPU cost and result wait from this workflow. Genuine sensor/network delay still fails closed; no freshness limit was relaxed.

The original interface now follows the requested visual layout: large camera view, dark high-contrast sidebar, prominent distance and SIDE/HEIGHT/REACH rows, green can box, cyan index marker, yellow contact line/distance, red stale banner, and a small depth thumbnail. Invalid/stale geometry displays no physical distance. Full hand skeleton remains hidden.

Quick checks only: four offline can-selection/tracking/engine checks passed, the existing metric-reference test passed, Python compilation passed, and a hidden UI smoke checked rendering and mouse selection without phone or sound. No live recording, benchmark, replay comparison or audio test was run. Real silver-can appearance tracking, reflective-surface depth, physical accuracy, processing latency and audible guidance remain UNVERIFIED until the user tests. This is a temporary manual target fallback, not automatic beverage-can recognition. Earlier results below describe older implementations.

---

# Implementation update — awaiting user hardware testing

The live results below describe the PREVIOUS implementation. They have not been rerun and are not post-fix performance claims. No phone connection, recording, replay comparison or audible playback was started during this repair.

Changes:

- `deskguide/stream.py`: separate socket draining from JPEG decoding. Both compressed and decoded frames use one overwrite slot; paired RGB/depth/K/pose/confidence stay together. Host arrival is stamped after the last packet byte, before decoding, so decode time is included in evidence age. Relative backlog remains a conservative connection-lifetime minimum-offset estimate; absolute capture latency remains unknown.
- `deskguide/engine.py`: remove the per-frame YOLO barrier. One detector job can run in the background; there is no submitted-job backlog. Hand tracking, current depth, guidance and GUI results continue without waiting for YOLO. Cold predictor setup runs during initialization while acquisition drains. Delayed detections are discarded on stale time, reset, changed target or camera epoch. Accepted boxes must be checked against current-image optical flow before current paired depth is sampled. The 300ms gate and expiring audio deadlines remain enforced.
- `deskguide/models.py`, `config.json`: retain YOLO11s/640/.2 and the same hand task, but explicitly budget PyTorch to two CPU inference threads. MediaPipe VIDEO timestamps now correspond to the frame's host arrival rather than the variable time inference happens to begin.
- `deskguide/tracking.py`, `flow.py`: use existing checked LK/RANSAC for delayed detection alignment, never blindly reuse an old box. Identity durations and palm fusion use frame-arrival time. Hand occlusion uses observed landmark regions with a10% object margin rather than the entire empty hand bounding rectangle; actual overlap still suppresses target measurements.
- `deskguide/metric_hand.py`: geometry equations and acceptance thresholds are unchanged. Rejections now retain exact registered depth samples, confidence values, sample support, nominal/calibrated shape depth, accepted/rejected status and the failed gate in session logs. The prior generic log is insufficient to identify which depth gate failed during the old live run; a particular physical cause is NOT claimed fixed.
- `deskguide/audio.py`: retain the persistent callback and stereo/directional tones, but select one dominant correction with hysteresis rather than waiting through a900ms axis cycle. Resume starts a pulse immediately; stop/expiry produces silence in the next callback, with within-block evidence expiry also enforced. No inferred grasp or near-target claim.
- `deskguide/app.py`: medium-confidence depth is correctly described as usable; it no longer displays the contradictory high-confidence-only warning.

Reference inspection: Proprio publishes its newest paired frame and uses a single ordered hand worker. Its main perception loop also waits for neural results; no claim is made that the reference itself meets Reflex's300ms constraint. The background object job is a targeted scheduling change to remove that wait in this prototype, not copied upstream source or a new geometry algorithm. The upstream evaluation was only read.

Quick offline checks: Python compilation;4 live-pipeline regression checks;1 existing metric-reference test;14 tracking checks;7 protocol/audio checks; and a silent simulated engine startup/shutdown smoke. All passed. The localhost protocol check needed socket permission in this sandbox. These establish no hardware performance or accuracy result.

Ready for user testing. Remaining UNVERIFIED: fresh live XYZ availability, actual processing rate/latency, cup acquisition and low-texture optical-flow behavior, palm-LiDAR acceptance, motion/occlusion recovery, and audible directional/stereo correctness. Slow or unsupported evidence will still pause guidance. USB and native iOS integration remain unverified/unfinished.

---

# Current evidence and limitations

## Audit and first divergence

Baseline: existing independent evaluation, commit `6be3180e139b47924e442d0122af32ea840cd218`, CPU, COCO detector, HAND2, OBJECT2, helper off, guidance3. Source hashes and runtime configuration are retained in logs/pipeline-baseline.json. Baseline workers run separately, disable bytecode writes, and write outputs only here. Production imports no baseline modules.

First divergence was input preparation: prototype used 640px RGB and high-confidence-only depth; baseline uses 960px RGB and confidence >=1, with depth nearest-aligned before rotation. After correction, recorded RGB/depth/intrinsics/effective pose agree exactly. Coordinates are right/down/forward; ARKit pose converts with diag(1,-1,-1). Displacement is target minus fingertip in meters.

Subsequent corrections: fixed 160px hand border and .3 thresholds; independent palm-depth/hand-shape fusion; matching YOLO11s official weights and detector settings; front-surface depth population; three-observation world-position averaging; nearest point in the inner70% target rectangle at measured front depth. This is an approximate contact plane, not reconstructed grasp geometry.

Hand geometry uses index segments .040/.025/.022m, projection factor .9, shape correction .12s, missing-depth decay .6s, metric adaptive filter .8/15/1.5, and current agreeing palm LiDAR. Full explicit parameters are in deskguide/metric_hand.py. Fused XYZ is an estimate, not direct fingertip LiDAR or calibrated centimeter accuracy.

## Identical-input replay

Fourteen real hand/cup frames were processed by both implementations. Both detected a hand in14/14. Identical landmarks and depth produce XYZ agreement within floating-point precision (median 5.55e-17m). With each runtime's own landmarks: median handXYZ difference 1.72mm, p95 11.59mm. ObjectXYZ median difference below .001mm, p95 .346mm; contact-point median difference .324mm, p95 .774mm. These measure agreement with the baseline, not physical accuracy. Prototype obtains object3D13/14 because it requires a second confirming observation.

Evidence: logs/comparison-input-fixed/report.json, comparison-visible-hand-fixed/report.json, comparison-depth-equations.json, pipeline-baseline.json, pipeline-prototype-matched.json, pipeline-comparison-summary.json. Replay wall times are not a fair live speed comparison: replay execution scheduling differs. Tests retain numeric observations only, without importing baseline code.

## Live check with audio requested

Forty-second run: logs/session-1792823584064296500.jsonl. Real frames received;69 frames processed. All69 guidance decisions were stale_frame and audio PAUSED. No valid directional guidance was produced. Median/p95 processing266/369ms; object inference255/349ms; hand inference89/122ms; receive-age281/369ms; estimated relative backlog growth239/288ms. Median process CPU54.7% of logical capacity, RSS596MB (p95 615MB). Displayed processing rate median2.15FPS. Absolute capture-to-output latency is unknown because clocks are not synchronized.

Object SEARCHING38 / UNCERTAIN31; hand shape-only2D estimate53 / lost16. Current palm LiDAR was not accepted. The live test therefore FAILED usable guidance; do not describe the repaired app as reliable or complete. Timing and current depth acceptance require further diagnosis without relaxing validity gates.

## Remaining differences and physical tests

MediaPipe runtime version differs (.10.21 versus baseline1.1.1). Prototype confirms initial targets twice, movement three times, uses conservative hand-box masking and eight-second anchor retention; baseline uses its own point mask and15-second memory. Occluded anchors never authorize prototype audio. Baseline Mode3 uses temporally stabilized speech; prototype uses independently synthesized stereo tones. Speech timing and audible equivalence remain UNVERIFIED.

A hand+cup: replay detection verified; live usable XYZ failed. B approach: accuracy/timing UNVERIFIED. C side view: pose-specific success UNVERIFIED. D camera motion: physical anchor stability UNVERIFIED. E cup occlusion: live preservation/recovery UNVERIFIED. F finger out/back: observed lost/2D states, annotated reacquisition delay UNVERIFIED. G invalid depth: automated guidance exclusion checked; physical depth accuracy UNVERIFIED. No ruler-based accuracy, verified near threshold, successful grasp, or audible stereo perception is claimed.

Next work is live CPU/transport timing and the rejected-palm-depth cause, then annotated A-G trials. No additional product features or speculative native APIs are required.

Automated validation: 38 tests passed in 1.403s with localhost socket access; pip check reported no broken requirements. The restricted sandbox initially blocked the loopback reconnect test; the full suite passed when socket access was permitted. These tests do not establish physical reaching performance.
