> Historical record: external-source research integration and earlier scheduling described below are superseded. See [CLEANUP_AND_ACQUISITION.md](CLEANUP_AND_ACQUISITION.md) for the current independent runtime and validation.

# Actual 62-frame reference run and connection repairs

Status: local prototype updated; hardware verification pending. No live recordings, phone connection or audible tests were requested or run. Original Proprio files were not modified. This report supersedes assumptions in earlier validation documents.

## What live-20261024-011827.json actually records

Read-only source: C:/Users/ngkho/OneDrive/Desktop/testing_ver_calhack/results/live-20261024-011827.json. Summary with file hash: logs/reference-62-audit.json. It contains 62 frames over 17.375 seconds of recorded frame time.

| Recorded output | Count |
|---|---:|
| Hand visible, 21 keypoints, index_tip, MediaPipe (wide) | 59 |
| Hand lost, no keypoints / no hand depth | 3 (indices 58-60) |
| Object visible | 55 |
| Depth source lidar | 44 |
| Depth source blend | 5 |
| Depth source size | 10 |
| State guiding | 6 |
| State grab | 8 |
| State idle | 48 |
| Coasting true | 0 |

The file has NO selected object box, detector score, object label, instance ID, object XYZ, hand XYZ, or effective object-option configuration. It cannot identify Pepsi versus the black tumbler or another cup prediction. `object_visible=true` alone does not establish semantic identity, valid target depth, or active audio. Scalar distance alone cannot recover XYZ displacements. The runtime's cup target uses YOLO11s / COCO class 41, but that is a category prediction, not Pepsi identity.

### Automatic selection / locking

`perception.CocoDetector` selects cup class 41 at confidence 0.2, 640 input and CPU float32. `Perception._pick` ranks the first six detections by reachable front-surface depth (<=1.1 m), then score +0.5*IoU with the previous box, with a near-camera-anchor guard against an unrelated unknown/far replacement.

Hand option 2 is NOT object option 2. Direct run.py uses the environment/default object mode: watch (option 1) unless OBJECT_OPTION or PERCEPTION_USE_POSE requests another. launch-laptop.ps1 sets OBJECT_OPTION=2, but the supplied command itself does not, and the result JSON does not capture the effective environment. This run's exact object mode is therefore unknown. Watch/room repeatedly detect and retain camera/world anchors on brief loss. Only option 3 is found-once locking: ten consistent looks over >=0.4 s, within a 2 s window and 3 cm agreement, then stop detecting until reset/unlock. We cannot claim that this recording demonstrated such a lock.

### Hand path and why size fallback occurs

Original Option 2 loads hands.make('mediapipe_wide'): CPU MediaPipe VIDEO, two hands, detection/presence/tracking thresholds 0.3, 160-pixel replicated border, inverse mapping after detection, landmark 8 as index tip. Stream preparation downsizes to a 960-pixel long side, registers depth with nearest-neighbor sampling, rotates PathOn 90 degrees clockwise and transforms K/pose. Prepared time is local monotonic arrival, not an independently synchronized capture clock.

Perception._measure obtains average-hand geometry via hand_points_3d and samples palm LiDAR via palm_depth (11 palm sites; at least four in the image and >=40% valid support). It applies wearer/reach/background-depth checks. `reason=accepted` in this result means the candidate hand passed those checks, not that its LiDAR was accepted for fusion.

HandDepth learns hand scale with suitable 0.25-0.8 m readings, keeps up to 90 ratios, and uses current LiDAR only when it is within 0.22-0.9 m and agrees with shape within max(8 cm, 30% of shape depth). Its correction follows with 0.12 s and fades with 0.6 s. Reported source depends on residual LiDAR weight (>0.7 lidar, <0.3 size, otherwise blend); the label is not a calibrated confidence. `_fused_hand` can also cap size depth near 0.2 m when the palm is a depth hole among otherwise valid scene readings. It places all keypoints on their camera rays and filters the selected index tip using its One Euro path. None of this makes estimated tip depth a direct tip LiDAR measurement.

Nine size-labeled frames have lidar=null. Their exact physical cause cannot be determined: this JSON does not store sampled depth/confidence/support pixels. The remaining size frame (index 61) has palm LiDAR 0.1726 m, below the 0.22 m fusion minimum, and shape depth 0.1755 m. Thus even a non-null palm reading can yield size. Model-only hand geometry is a valid coarse estimate in Proprio, not verified LiDAR.

### Distance and idle/audio interpretation

run.py computes norm((scene.aim_xyz if present else scene.object_xyz) - scene.hand_xyz). Both vectors are metric camera coordinates: x right, y down, z forward. aim_xyz is the nearest point within the object's middle 70% contact region; object XYZ comes from depth/retained anchoring. Option 2 hand XYZ is the filtered index-tip estimate fused from palm LiDAR and shape. The distance is 3D, not a pixel distance, even when hand depth comes from size. It is not necessarily between two fresh LiDAR observations: seven frames have object_visible=false, while memory can preserve XYZ.

The cited 0.037731232 m occurs at index 29 with size depth and state idle. It is a 3.773 cm MODEL-BASED estimate, not demonstrated physical accuracy.

The state sequence explains silence exactly:
- indices 0-5: guiding; index 0 logs Looking and Found it.
- index 6: Grab is queued; indices 6-13 remain grab.
- index 14: 1.672 s after the grab event, state becomes idle. `_after_grab` can return idle after its 1.5 s grab period. `Guidance.update` then immediately silences the tone and returns idle until reset. run.py continues perception and distance logging regardless of that state.
- indices 14-61: idle, including all ten size estimates and all three lost-hand samples. No directional phrases were logged anywhere in this 62-frame run. Mode 3 combined intentionally omits continuous distance beeps. Phrase records prove requests to the sink, not that audio was heard.

The auto Grab is a proximity/task-state instruction, not proof of contact or a successful grasp. DeskGuide deliberately keeps explicit completion and does not copy this auto-grab-to-idle behavior. Original hold/coasting/estimated-depth guidance also does not establish our stricter 300 ms movement policy. Only one recorded perception latency exceeds 300 ms, but latency_ms excludes transport, queue wait and audio output latency; a 214.86 ms median perception time alone does not prove end-to-end freshness.

## Minimal DeskGuide repairs

- `deskguide/engine.py`: exports all 21 selected/tracked hand landmarks with the same processed frame as fingertip/target geometry. Connects display-only size/shape estimates to the existing validated evaluate vector/distance function. Publishes display_distance_m, display_delta_m, distance_kind, depth source, uncalibrated model score and specific missing reasons. Current accepted LiDAR-fused geometry continues through the existing guidance/audio event path; estimates never authorize directional audio. The 300 ms deadline, connection/reset guards and source pairing stay enforced.
- `deskguide/app.py`: draws 21 landmarks and one cyan index-tip marker independently of depth availability. When a processed frame becomes stale, its markers remain on that same image in a warning color with a stale banner; they do not claim a current observation or enable audio. Displays cm and XYZ direction amounts for fresh measured/estimated geometry, labels ESTIMATED/PAUSED, shows source/uncalibrated confidence and explicit reasons when unavailable. Existing layout is retained. Added explicit --local-reference-hand opt-in.
- `deskguide/tracking.py`: retains real model score instead of a fabricated 1.0 value, clears it after loss, and gives specific target-depth/confirmation/occlusion reasons. Existing geometry equations, filters, thresholds and models unchanged.
- `deskguide/models.py`: thin loader for the ignored local reference adapter, with an explicit error if missing; no silent fallback or new model.
- `.local_reference/hand_adapter.py` and `.local_reference/launch.ps1`: LOCAL ONLY, ignored by Git and not mirrored into the submission checkout. The adapter invokes the original external hands.make('mediapipe_wide', fetch=False), without copying/updating original source. It converts BGR to RGB and maps the returned actual kp/box/score into the existing tracker contract. The local launcher uses the reference Python environment (MediaPipe 1.1.1) with bytecode writes disabled. There is no reimplementation of the original detector in the adapter. Borrowed reference source is visibly labeled in the GUI and logs.
- `.gitignore`: excludes the local adapter/launcher. Common integration glue remains independently authored; the separate native Reflex iOS files are untouched. No commit/push performed.
- `tests/test_automatic_can.py`, `tests/test_live_pipeline.py`: integration checks cover landmark export/rendering, GUI distance labels, estimated-source display, measured XYZ-to-audio, stale/estimated/lost/missing-target suppression. The existing decode-age check now uses a deterministic test clock instead of a short Windows sleep.

### Provenance boundaries

Actual borrowed component: original external upstream/hands.py MediaPipe-wide detector, ONLY in explicit ignored local research mode authorized by the user. Its copying/redistribution permission remains unestablished. No original source was copied, translated or edited. Do not include this local integration in Reflex submission code.

Reused independent components: already validated palm-LiDAR/hand-shape geometry, object surface/contact/selection/tracking, source preparation, vector math and expiring audio. Third-party Ultralytics YOLO11s (AGPL-3.0), Google hand task/MediaPipe attribution, OpenCV and sounddevice notices remain in THIRD_PARTY.md. No new models or dependency installs. The original guidance/speech/state machine is inspected but not imported or copied. The local adapter reuses the detector only; it does not claim full parity with Proprio's candidate selection, near-hole size cap, all filtering branches, speech or memory policies.

### Checks actually performed

34 targeted automated checks passed (independent environment); the 11 automatic/UI and metric-reference checks also passed using the reference Python environment. Distinct checks: automatic/UI connection 10, tracking 14, metric-reference fixture 1, live-pipeline 4, portability 5. They include the unchanged validated geometry fixture. Compilation passed.

One existing saved RGB-D frame was passed through the ACTUAL original local detector in the reference environment: one detected hand, 21 landmarks; the existing DeskGuide tracker produced a fingertip and LiDAR-fused XYZ. GUI/audio modules imported there without opening an audio device. The source hash before/after matched. This is an offline integration smoke, not a new recording, benchmark, audible check or hardware success claim.

### Still unavailable/unverified

Distance is unavailable for a missing index tip, rejected hand geometry/wearer/reach gate, missing/low-confidence/occluded target depth, pending target confirmation, stale frame, changed connection/task generation or explicit completion. Size-only estimates are approximate and intentionally display-only. A stored object anchor alone cannot authorize guidance. GUI and audio output on the real camera, physical XYZ accuracy, latency/freshness, difficult hand poses, reflective can depth, correct instance selection among cup predictions and audible stereo remain for the user's hardware test. Native iOS integration remains separate future Reflex work.
