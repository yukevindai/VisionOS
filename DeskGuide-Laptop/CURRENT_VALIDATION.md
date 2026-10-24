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
