# Camera tracking liveness — October 24, 2026

## Observed failure

The user reported repeated pauses mainly after requesting an object on the original iPhone 18 Pro. A physical screenshot showed the paused-state reason “Tracking was lost.” The authenticated Convex object action executed, but this does not prove that the phone accepted or announced its result.

The session watchdog used the processed image snapshot timestamp as its tracking heartbeat whenever a task was active. Native image snapshots are throttled to approximately five per second and produced after Vision and JPEG work. Processing gaps can therefore exhaust the 300 ms image lifetime while the camera continues capturing normally. The initial phone build did not log enough detail to distinguish snapshot expiry from AR tracking loss for each pause.

## Change and validation

Native AR capture now publishes bounded tracking metadata independently of Vision/JPEG processing. The session watchdog uses that metadata for camera liveness, with a bounded 300 ms acquisition window for the first sample of each generation. Other adapters retain their snapshot watchdog. The mailbox preserves an observed tracking loss even if a newer recovery arrives before delivery, and capture context/geometry must match the current session.

The 300 ms physical-observation and Resume gates remain unchanged. Raw tracking state supplies no target, hand, depth or movement authority. Native guidance still cancels on stale observations, actual tracking loss still pauses, and recovery does not automatically resume. Debug logs identify raw AR state changes, interrupted/failed camera sessions, physical-observation expiry, capture-heartbeat expiry and legacy image-snapshot expiry without logging images, speech, transcripts or credentials.

- Focused tracking/session suite: 45 tests passed (7 new tracking tests and 38 existing session tests). The new tests cover a pending object request with stalled images and fresh tracking, capture outage, generation acquisition deadline, invalid samples, loss/recovery, retained physical Resume requirements and a queued loss followed by recovery.
- Combined app unit suite: 221 passed, 0 failed, 0 skipped (xcresult summary), including this change with PRs 3, 20, 21, 22 and 23. Signed Debug iPhone build passed.
- The combined preview/tracking merge received an independent source review; both bounded paths, immutable capture metadata and lifecycle guards were retained. Project generation and diff checks pass.
- No post-fix phone recognition or pause-behavior pass is claimed yet.
