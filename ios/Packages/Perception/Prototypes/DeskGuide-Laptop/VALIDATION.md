# Executed validation

Date: October 23, 2026, America/Los_Angeles.

## Environment and preservation

Verified processor: **13th Gen Intel Core i7-1355U, 12 logical processors**, read from Windows CIM. Windows 11 build 26200. The app uses CPU inference only. Python **3.12.15** runs in `DeskGuide-Laptop/.venv`, with NumPy 1.26.4, OpenCV contrib 4.11.0.86, MediaPipe 0.10.21, sounddevice 0.5.1, Pillow 11.1.0 and psutil 6.1.1. `pip check` reported no broken requirements. `requirements.lock.txt` captures the complete tested environment.

All new source, environment, logs and documentation were written inside DeskGuide-Laptop. ObjectReachLab was read for our own previously authored adapter and official licensed assets, but was not edited. VisionOS and the Proprio evaluation were not edited. A pre-existing Python 3.12.15 interpreter outside the upstream source checkout supplied the base runtime for the new venv; no Proprio modules were imported. The base runtime must remain installed or the venv should be recreated with a normal Python 3.12 installation.

## Automated checks

Final command: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`

**21 tests passed in 1.379 seconds.** Source compilation passed as well.

Coverage includes:

* Fragmented REC3D v1/v2 packets, truncated packets and oversized payload rejection.
* A real local TCP server sending a burst of frames into the single latest slot, followed by reconnect and epoch change.
* Detector initialization of a 2D tip with all depth absent; valid local optical-flow motion between detections.
* Lost fingertip reacquisition and rejection of a distant second hand during a brief loss.
* Depth-jump rejection while preserving the 2D observation.
* Object anchor preservation under synthetic foreground hand depth, camera translation, and a longer covered interval.
* Three-observation verification before replacing the anchor for a moved object.
* Anchor expiry, reconnect invalidation, and hiding a box without valid pose or flow.
* Rotation/world-coordinate invariance, coordinate signs, coherent surface sampling, low-confidence depth rejection.
* Temporal jitter reduction and inference cadence budgeting.
* Left/right stereo energy, stale/muted silence, finite continuous audio synthesis and bounded amplitude.

The sandbox prevented the loopback TCP test from connecting to its local server during an initial run. Running the suite with normal network access passed. The test failure was not treated as evidence of a broken phone protocol.

Model assets and license files passed their hash checks. `--check` initialized and executed the actual NanoDet and MediaPipe models on blank input and returned no detections. This verifies model compatibility, not object or hand detection accuracy.

Tcl/Tk initialization passed. The actual GUI ran with AI initialization and no audio, then closed cleanly after a timed event loop. This verifies startup/model lifecycle; it is not a live-camera layout/geometry acceptance test.

`--audio-test` opened an output device and completed all six stages and completion tone, exit code 0. Physical headphone routing and cue interpretation require the user's listening check. No claim is made that synthesized energy tests prove correct human perception.

## Sustained CPU measurement

Command: `.\.venv\Scripts\python.exe -m deskguide --benchmark 30`

Input was a **blank 640x480 image, absent depth, and search-mode model scheduling**, warmed with three calls per detector. This is a synthetic microbenchmark, not an iPhone replay. It includes no Wi-Fi transport, live JPEG decode, real hand landmarks, GUI rendering or active reach audio. Those costs can lower live FPS. A 30 Hz source budget was simulated; faster empty flow iterations do not imply higher camera throughput.

| Metric | Measured result |
|---|---:|
| Elapsed | 30.047 s |
| Processing iterations | 615 |
| Processing loop | **20.47 iterations/s** |
| NanoDet calls | 139 |
| Hand model calls | 383 |
| NanoDet median / p95 | 78.10 / 109.39 ms |
| Hand median / p95 | 15.34 / 23.95 ms |
| Pipeline median / p95 / max | 15.00 / 109.00 / 141.00 ms |
| Process CPU, fraction of whole 12-thread machine | 5.76% |
| RSS start / end / observed peak | 201.71 / 137.93 / 204.43 MB |

Full results and configuration: [BENCHMARK.json](BENCHMARK.json). CPU percentage is process CPU-time divided by wall time and logical CPU count; it does not mean a detector uses only 5.76% of one core. Memory samples show no growth during this run, but do not establish long-session behavior.

## Live phone status

An eight-second unsandboxed headless preview attempt to **192.168.1.253:8888** timed out with `last_metrics: null` and no frames. The application reported that phone connection was not validated and returned a nonzero status. The actual cause may be address, server state, hotspot routing or availability; it was not determined here.

Therefore **no real camera FPS, capture latency, cup acquisition, return-to-view reacquisition, fingertip continuity, surface contact accuracy or stereo direction during an actual reach was measured**. Logged receive age is host age plus observed backlog growth; fixed network delay and optical exposure skew remain unknown. The V appearance marker provides only human-timed entry-to-confirmation measurement until a synchronized external recording is available.

## Remaining physical and functional gaps

* Verify PathOn's live RGB/depth alignment, intrinsics, rotation and ARKit pose conventions on the actual phone.
* Validate stationary-tip jitter, horizontal pointing, dropout/reacquisition and identity continuity with actual hand footage. No separate finger-only model is included.
* Validate cup occlusion, pose reprojection, physical movement and actual return-to-view latency. Multi-cup identity can still switch without an appearance model.
* A missed hand detector can allow foreground contamination; depth coherence does not prove object ownership.
* The lower-front patch is a contact candidate, not a segmented reachable surface. Transparency, cup interiors, handles and background require physical checks.
* The spread proxy is uncalibrated and does not account for all metric errors. No centimeter-level accuracy or grasp success is claimed.
* Near-target cues remain disabled without physical threshold validation. Completion is explicit.
* USB streaming was added after the initial build; see the update below. Voice input, advanced experiments/calibration dashboards and heavyweight mesh models are not part of this minimal build.
* Continue the live run for several minutes to measure actual responsiveness, model cost, memory and audio underruns under reaching conditions.

## USB follow-up

Added independently authored usbmux plist framing, USB-only enumeration, phone-port connect handshake, socket cleanup on refusal, reconnect integration, `--usb` and `--usb-check`. This does not import libusbmuxd or the restricted PathOn SDK. Protocol facts were checked against the public libusbmuxd client; no original source implementation was copied.

Updated regression suite: **27 tests passed in 1.305 seconds**, including six USB protocol/error tests using mock sockets. Live `--usb-check` reported that the Apple USB service was unavailable at localhost:27015. No running Apple Mobile Device service or iproxy command was found by the preliminary inventory. USB connection, real device enumeration and real USB frame delivery remain **unverified** until Apple Devices/driver installation and phone trust are completed.

## Standalone iOS architecture follow-up

The destination is now explicitly a standalone iPhone app. Extracted the dependency-free guidance policy used by the live engine and added frame-source/audio-sink interfaces with source injection. Updated regression suite: 31 tests passed in 1.578 seconds; source compilation passed. Four new tests cover measured XYZ parity, rejected prediction/estimate states, freshness/completion gating, and transport-independent engine construction. Native iOS source, model conversion and device performance are not yet implemented or validated. See IOS_ROADMAP.md.

## Real-stream failure repair

Latest regression suite: 37 tests passed in 1.442 seconds; source compilation and updated GUI startup/shutdown passed. Actual RGB-D frames, upright cup detection, partial-hand recovery and 2D continuity were tested on the user's live hotspot stream. Direct fingertip metric guidance remained unavailable in the measured trial, and the final post-mapping-correction live retry timed out. Full source-grounded method comparison, actual per-state frame counts, timings and remaining VisionOS integration gaps are in PROPRIO_METHOD_REVIEW.md. Earlier no-live-frame statements in this document describe the initial build and are superseded by that live-session report.
