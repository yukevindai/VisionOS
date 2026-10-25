> Historical record: external-source research integration and earlier scheduling described below are superseded. See [CLEANUP_AND_ACQUISITION.md](CLEANUP_AND_ACQUISITION.md) for the current independent runtime and validation.

# Reference integration repair - 24 October 2026

Status: implementation updated; user hardware verification pending. This is a Windows development prototype for one VisionOS feature. No native iOS submission files were changed. This report supersedes earlier manual-mode and asynchronous-detector descriptions.

## Observed failure

Existing session `logs/session-1792828946561639100.jsonl`: 429 results all SEARCHING, 89 completed detector results all discarded_stale, 340 pending. Median recorded detector inference 314.0 ms; completed-result age 413.1 ms. Overall processing median 63.0 ms, with 176 LiDAR-fused hand states. These are pre-repair session measurements, not a new benchmark or a physical-accuracy result.

The detector path discarded results before target selection whenever cross-frame result age exceeded 300 ms, even for initial 2D recognition. It also required cross-frame optical-flow support for initial detection and used two Torch threads. Thus valid YOLO predictions could never initialize a target. A prior manual workaround bypassed YOLO altogether. The model category was not the cause: this can is predicted as cup/class 41.

## Reference inspection and reuse

Reference: `C:/Users/ngkho/OneDrive/Desktop/testing_ver_calhack`. Its setup pins upstream `DrealVeerNanda/ucsc-ai-hackathon-2026` at `6be3180e139b47924e442d0122af32ea840cd218`.

| Component | Provenance / applicable terms | Integration decision |
|---|---|---|
| `upstream/perception.py`, `hands.py`, `stream.py`, `interfaces.py`, `guidance.py`, `audio_out.py`, `debug_view.py` | Proprio upstream; no project-wide LICENSE/COPYING or explicit copying permission found in inspected checkout. Some comments describe adaptation from RTHACK, not a license grant. Vendor licenses do not cover the whole project. | Kept isolated. No source copied, translated or imported by DeskGuide runtime. Existing independent hand/depth geometry retained. Only the small scheduling/selection integration was repaired from observed behavior. |
| `run.py`, `setup.ps1`, `launch-laptop.ps1`, `test_runner.py` outside upstream | Evaluation orchestration/wrappers, described as our own work in the user's project; `run.py` explicitly imports upstream algorithms rather than owning them. | Reused the working launch/configuration facts. Did not transfer upstream imports or the diagnostic runner, which would introduce the protected dependency into the feature. |
| Ultralytics 8.4.183 / YOLO11s | Third-party AGPL-3.0 package and weights. Same installed version in both environments. | Uses the actual existing Ultralytics inference implementation, not a new detector. Publisher license copied from the reference installation to `third_party/ULTRALYTICS-AGPL-3.0.txt`. Existing independently downloaded weights are byte-identical to reference. |
| MediaPipe Hand Landmarker task | Google asset; existing Apache-2.0 package/model attribution retained in THIRD_PARTY.md and models/MEDIAPIPE-LICENSE. | Uses the existing real hand-task implementation and identical model bytes. Reference package is 1.1.1; DeskGuide remains 0.10.21. No speculative package upgrade or change to validated hand geometry. |
| NumPy / OpenCV / sounddevice / PortAudio | Existing BSD / Apache-2.0 / MIT dependencies and their bundled notices | Reuses the installed libraries for depth, LK, image handling and persistent audio. Versions remain pinned in requirements.lock.txt. |
| Proprio recorded speech/audio and Piper voice assets | No blanket upstream asset reuse permission established; voice models have separate terms | Not transferred. Existing independent expiring directional tones retained. This is not Proprio Mode 3 speech parity. |
| PathOn / Record3DStream SDK | Reference's separately installed SDK has restricted personal/noncommercial terms and no-redistribution restrictions per its documented license | SDK source not transferred or bundled. Existing independently authored REC3D wire reader retained. |
| WiLoR and other unused models | Separate model terms; evaluation README identifies WiLoR CC-BY-NC-4.0 | Not transferred or loaded. Only the existing YOLO11s and hand task are active. |

The prototype's MIT license applies to original DeskGuide files only; it does not relicense AGPL/model dependencies. These files are not silently incorporated into the native VisionOS submission. Upstream licensing permission remains unestablished, rather than a claim that copying is categorically impossible.

Model hashes (both reference and DeskGuide):
- YOLO11s: `85a76fe86dd8afe384648546b56a7a78580c7cb7b404fc595f97969322d502d5`
- Hand task: `fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1`

## Exact runtime changes

- `deskguide/engine.py`: replaces the delayed cross-frame detector mailbox with the reference's paired-frame parallel behavior. One YOLO job overlaps hand processing on exactly the same prepared RGB/depth/K/pose frame; the engine joins this job before target selection. Acquisition and JPEG decoding stay in their existing separate overwrite-slot threads. There is no inference-job queue and the next cycle takes the latest available frame. Initial recognition no longer depends on optical-flow mapping from a different frame. Late detections can update the recorded recognition state, but never authorize late movement. Added candidate counts and paired_frame scheduling metadata; reconnected/changed-task evidence cannot dispatch guidance.
- `deskguide/models.py`, `config.json`: existing YOLO11s, CPU float32, 640, confidence 0.2, class 41, class-aware NMS IoU 0.7, max_det 300 and rectangular letterboxing retained; quantize=None explicit. Torch budget restored to eight, matching the reference's Ultralytics CPU NUM_THREADS setting on this laptop. Both hand and object intervals remain zero (every processed frame), helper off by absence. No new model or dependency installed.
- `deskguide/tracking.py`: retains the previously restored reference-compatible depth/continuity selection (top six confidence-ranked candidates, <=1.1 m reach priority, confidence +0.5*IoU, camera-space near-anchor guard). The previous box now remains an identity hint if flow/pose cannot project it; it cannot provide depth or a visible observation by itself. Existing front-surface depth, occlusion checks, movement confirmation and anchoring remain.
- `deskguide/app.py`: automatic recognition is the only active workflow, even with a legacy manual config. Removed rectangle bindings and selection handlers; current layout unchanged.
- `tests/test_automatic_can.py`, `tests/test_selected_can.py`: paired automatic acquisition, stale-recognition starvation, valid XYZ-to-audio and disabled legacy manual routing checks replace the manual-only engine check. Pure historical appearance-tracker utilities may remain on disk but are not imported by the production engine.
- `README.md`, `CURRENT_VALIDATION.md`, `THIRD_PARTY.md`, this report and the publisher's license notice: current instructions, evidence and provenance updated. Changes mirrored into the sibling Git checkout `VisionOS/DeskGuide-Laptop`; no commit or push made in this repair.

Unchanged safety: source arrival plus estimated relative transport backlog must be <=300 ms at guidance evaluation, and audio commands expire at that source deadline. Late geometry is not substituted into a newer frame. Missing/uncertain hand or target depth, occlusion, reset and generation mismatch pause movement cues. Absolute phone-to-laptop capture latency remains unmeasured. The reference allows some estimated-depth/coasting behavior; those are not copied into the stricter movement policy.

## Necessary offline checks only

- 33 distinct targeted automated tests passed: automatic integration 5, tracking 14, validated metric-reference fixture 1, live-pipeline regression 4, selected packet/audio checks 4, portability/guidance checks 5.
- Python compilation passed.
- Actual installed YOLO integration (warmup plus one existing screenshot) passed with eight CPU threads and no new assets. Silver Pepsi can: cup/class 41, confidence 0.4567. Result: logs/paired-integration-detection.json. The screenshot contains overlays, so this is not a new clean sensor recording or live accuracy test.
- Slow simulated detector: recognition is retained while stale geometry sends no directional command. Fresh synthetic hand and target XYZ: a directional command reaches the simulated audio sink. Existing audio callback tests check stereo mapping and expiry without opening a sound device.

No phone connection, new recording, live test, benchmark or audible playback was started. The reference directory was not modified.

## Still unverified

Physical can detection/selection, hand and target XYZ accuracy, reflective LiDAR, movement/occlusion/reacquisition, sustained CPU performance below the 300 ms deadline and audible output/routing need the user's test. The model also labels the black tumbler as cup (0.8400 in the prior screenshot audit); a saved back-side can frame was missed. Matching the working class/selection configuration does not establish unique Pepsi identity. Original speech behavior is not reproduced. Native iOS camera/LiDAR/tracking/audio integration remains future VisionOS work.
