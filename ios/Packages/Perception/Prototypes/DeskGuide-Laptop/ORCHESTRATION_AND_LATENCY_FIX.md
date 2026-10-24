# Codex / Claude Code: latency and tracking repair

Status: implementation ready for user hardware testing. No phone connection, live recording, audible playback, new model, reference edit or native iOS edit was performed.

## Actual Claude Code orchestration

Executable: C:/Users/ngkho/.local/bin/claude.exe, version 2.1.310. CLI auth status reported authenticated. Calls used safe/restricted mode, Read/Glob/Grep only, dontAsk permissions and no MCP/plugins. Claude could not edit or run commands; Codex made every source edit.

Initial before-edit audit failed with `API Error: Can't reach the API server — check your internet or DNS (ENOTFOUND)`. A network-enabled audit attempt timed out after 240 seconds; the first diff-review attempt timed out after 90 seconds. These attempts are not counted as completed reviews. Codex proceeded from the saved-log diagnosis rather than pretending there was an initial Claude approval.

Subsequent focused calls succeeded: Claude independently diagnosed the old mechanism and reviewed the actual Git diff against the pre-edit checkpoint (53.055 s, seven turns), then reviewed the final fixes (28.684 s). Its final response is **No blocking findings**, with low-level limitations listed below. Claude only read code; it did not execute the tests or validate hardware.

Evidence (local, logs ignored by Git):
- logs/claude-orchestration/invocation-evidence.json: CLI/version/auth confirmation, permissions, successful sessions, tools and final reviewed-diff hash.
- logs/claude-orchestration/claude-review.md: independent diagnosis and initial findings.
- logs/claude-orchestration/claude-final-review.md: final review.
- logs/claude-orchestration/actual-changes.diff: actual `git diff --no-index` against the reversible source checkpoint. Final source was checked to match this reviewed diff.

Checkpoint: .history/checkpoints/orchestration-20261024-021505/source-before.zip with SHA-256 manifest and an extracted before tree. It preserves the pre-edit source/config/tests/docs (54 files), not model/runtime binaries, which this turn did not change. History was not rewritten. No commit or push made.

## Why 63 ms processing can display an approximately 418 ms old frame

Concrete logged example: session-1792833140556937900.jsonl, frame 21717:

| Component | Measured milliseconds |
|---|---:|
| Last packet byte to processing start | 31.0 |
| JPEG decode (already INCLUDED in the 31 ms above) | 16.0 |
| Processing this frame | 63.0 |
| Estimated relative transport backlog | 42.4 |
| Source age at publication: 31 + 63 + 42.4 | 136.4 |
| Gap to the NEXT publication | 266.0 |
| Age of displayed old frame just before next publication | 402.4 |
| YOLO inference in the NEXT frame | 218.0 |

The screenshot's exact frame is not identified, so this is a measured example of the same mechanism, not a claim to reconstruct that particular screenshot. GUI polling is scheduled after 33 ms plus rendering work; old logs do not separately time rendering. A later GUI poll can therefore show an age near 418 ms while still showing the previous frame's 63 ms processing statistic.

There are 35 saved low-processing/stale-display intervals across three recent sessions in logs/claude-orchestration/measured-latency.json. Not every gap can be attributed solely to YOLO; one long gap may also include missing/rejected input. The example above directly shows a detector publication stall. Claude independently confirmed it using the diff, app age calculation and measured data.

### Capture, buffering, selection and clocks

- The receiver already drains complete synchronized RGB-D/K/pose/confidence packets into a single overwrite slot; decoding has a separate overwrite slot. There is no growing acquisition queue to delete. The engine chooses the latest complete decoded frame.
- Arrival is stamped with host monotonic time at the LAST wire byte, BEFORE JPEG decode. Processing time excludes the prior decode/selection wait and excludes time the GUI keeps displaying an already-published result.
- Frame age uses host monotonic arrival plus estimated relative transport backlog. Phone timestamps are used only to estimate relative offset growth, not directly subtracted from host time. The conservative connection-lifetime floor was preserved; no postdating or offset reset hides stale evidence.
- Absolute exposure/capture-to-host latency remains unmeasured. We do not claim the relative-backlog estimate is absolute capture latency.
- Semantic result age is sampled at result collection; tracker identity age is sampled at frame arrival. Both use the same host monotonic scale, with a relative-lag budget on the neural source. Their different evaluation instants explain different logged ages; current physical freshness is still checked separately at dispatch. No clock threshold was relaxed.

## Demonstrated tracking/acquisition gaps

The latest recorded 257-frame session has 110 SEARCHING frames: 84 were detector-not-due frames after identity expiry, and 26 had no returned candidate after expiry. Its reset count stays at 2; repeated resets are not the demonstrated cause in that session. Model misses still occur and are not fabricated into detections. Some other sessions contain explicit reset/generation changes. An approximate 0.9 s semantic identity cannot repair a failed visual track by itself.

Hand landmarks alongside LOST were a display inconsistency: the GUI deliberately rendered stale landmarks in a warning color while its status correctly changed to LOST. That is now removed. Fresh 2D landmarks remain visible without depth; stale landmarks and fingertip markers are cleared.

## Implemented changes (six source/test files)

1. deskguide/engine.py: remove the live `job.result()` wait. Exactly one NN call can be pending; acquisition, hand inference, visual tracking, current depth, guidance and publication continue. A result is consumed only after `done()` and retains source frame, timestamp, epoch, target and generation. Obsolete queued jobs can be cancelled; already-running jobs are discarded after completion. Source-hand regions are attached only to their corresponding source frame. Metadata records pending/completion state, source age and result publication time.
2. deskguide/tracking.py: accept mapped detections with their ORIGINAL neural-source time. No old result is stamped as a new semantic observation. Current RGB support, current paired depth, current hand-occlusion checks and the existing movement/confirmation geometry remain mandatory. Hand model/configuration and all validated hand geometry/filter equations remain unchanged.
3. deskguide/flow.py: reuse checked LK plus forward/backward, photometric, RANSAC inlier, scale and bounds validation to map source boxes into current image pixels. Exclude observed source-hand hulls (with a small margin) from feature seeds. Failed mapping rejects the candidate. No source depth or source XYZ is carried into a newer frame.
4. deskguide/filters.py: remove the old 1.4x inference-cost search delay. While lost, preserve the configured search interval plus a half-cost minimum cooldown to limit CPU contention. Normal reliable-tracking cadence remains unchanged.
5. deskguide/app.py: clear stale hand/tip overlays instead of drawing an old hand while reporting LOST. Existing GUI layout/status/distance fields remain intact.
6. tests/test_automatic_can.py: regressions for a blocked NN while publication continues, older semantic identity with CURRENT depth, no-source-depth reuse, failed current-image support, reconnect invalidation, predicted-anchor audio stop, source-hand feature exclusion, fresh measured/estimated distance display and stale overlay clearing. The asynchronous test requires an actual detected event rather than accepting a pending state as acquisition.

Documentation updated: this report, README.md and CURRENT_VALIDATION.md. Implementation mirrored to the sibling Git checkout without changing native Reflex files.

## Why this does not bypass 300 ms

An older NN class label is only a bounded identity hint (the existing 0.9 s semantic limit). It cannot supply stale target coordinates or depth. It must be mapped onto the CURRENT observed image using checked flow, then pass CURRENT RGB-D/occlusion/confirmation checks. The current hand and target geometry must still satisfy the original <=300 ms frame-age rule. Audio expires at that current source deadline. This is the same distinction between an older learned identity and fresh observed tracking evidence already used between neural detections.

Predicted/occluded/unsupported anchors never enable movement. Size-only fingertip estimates remain clearly labeled display-only. Lost or stale observations pause audio. There is no inferred grasp/success, no new detector and no new manual selection.

## Claude findings and dispositions

- Publication stall and stale-overlay inconsistency: independently confirmed; fixed as above.
- CPU contention while searching: added a half-cost cooldown. Hardware CPU/thermal behavior remains unverified.
- Source hand pixels biasing LK: excluded observed source-hand hulls; existing RANSAC/forward-backward checks and current hand-overlap rejection verified in source. Missing source-hand detections can still leave unmasked pixels; no perfect semantic identity guarantee is claimed.
- Reset/reconnect delay: a running inference cannot safely be preempted in the same model instance. Its result is rejected, fast frames/audio stop remain responsive, but reacquisition may wait up to one inference. This is acknowledged rather than adding parallel model copies.
- Timing/assertion concern: strengthened the tests to explicitly require a detected event and check publication gaps on the deterministic simulated clock. These checks do NOT measure laptop FPS.
- Semantic timing labels: verified the two sampling instants on the same monotonic clock; no physical freshness relaxation. The frame-age and audio gates are unchanged.
- Final Claude review: no blocking findings. Remaining observations were low-level masking, cancellation/cooldown and timing limitations.

## Validation and remaining hardware work

41 targeted automated checks passed in about 3.7 seconds; Python compilation passed. The blocked-detector test explicitly keeps simulated publication gaps below 40 ms while a 340 ms semantic result is pending, and verifies target depth 0.55 m from the CURRENT frame rather than source depth 1.5 m. This is a synthetic regression, not hardware performance. The unchanged numeric hand-geometry fixture also passed.

Not verified: actual post-change FPS/publication-gap percentiles, CPU contention/thermal throttling, fresh hand and target overlap on hardware, Pepsi identity versus other cup predictions, mapping during fast camera/hand movement, reflective LiDAR, absolute capture latency and audible output/loss-to-silence latency. Real stalls will still show STALE and pause guidance; the fix does not hide legitimate safety failures. User will perform all hardware testing.

Launch with the existing project launch.ps1 and current PathOn IP. No reference project or external research adapter is needed.
