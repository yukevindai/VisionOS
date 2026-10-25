# Camera-relative directional distance — implementation and validation boundary

The Windows preview now prioritizes three independent metric axis differences, with a large
forward/back field and equally legible left/right and up/down fields. Euclidean distance and
raw fingertip/target/difference XYZ are available only under the diagnostics toggle.
No Gemini/YOLO/ViTTrack/PathOn/MediaPipe algorithms or native Swift modules were rewritten.

Follow-up display change: the old prototype's `target XYZ - hand XYZ` axis values now also
appear prominently in amber when the geometry is estimated/unvalidated. These are labeled
ESTIMATED ONLY or UNVALIDATED ONLY and NOT FOR MOVEMENT; they use no MOVE/REACH command wording.
This restores observable forward/side/height numbers without authorizing audio or physical
guidance. A >3 cm depth conflict, missing/mismatched geometry, lost target or stale frame
still hides them. Raw XYZ and Euclidean distance remain under the diagnostics toggle.

## Reused geometry

Read-only inspection of `C:/Users/ngkho/OneDrive/Desktop/testing_ver_calhack/upstream` found:

- `interfaces.py`, `Observation.delta`: `target/aim XYZ - hand XYZ`.
- `hands.py`: optical backprojection using measured depth and intrinsics.
- `guidance.py`, `_line`: aligned/release hysteresis, historically 2.5/5 cm.

Reflex already vendors its own DeskGuide `geometry.backproject`, `surface`, `fingertip_depth`
and `stream.prepare`. These remain the calculation source; no upstream Proprio source was
copied. The existing nearest visible front-surface target selection is retained, with hand
pixels and pixels outside the tracked target polygon excluded. Intrinsics/depth/confidence
continue through the existing resize/rotation alignment path.

Coordinates are **display-rotated camera optical meters**: X right, Y down, Z away from the
phone. Positive deltas mean RIGHT, DOWN, FORWARD; negative deltas mean LEFT, UP, BACK.
The supplied unvalidated example yields `(-8.7, -2.0, +7.2) cm`, not a physical acceptance test.
The default historical deadband treats <=2.5 cm as aligned and retains alignment until >5 cm.
These are cue-stability tolerances, not claims of sensor accuracy; exact raw differences remain
diagnostic. With that deadband, the example's 2 cm Y delta is ALIGNED rather than a movement cue.

## Corrected depth provenance

The current hand fusion code explicitly returns a **palm LiDAR + nominal hand geometry
fingertip estimate**, with a 5 cm sigma. Previously the adapter promoted that to `LIDAR`.
It is now `ESTIMATED` until a separate center-supported direct fingertip depth agrees within
3 cm and has high confidence. Only then is the direct fingertip point used for the offset.

A 6.9 cm disagreement now produces `UNRELIABLE`, with numeric `depthDisagreementM` in the log.
It no longer merely prints a warning while authorizing movement. Missing direct depth remains
estimated. Missing/low confidence or uncertain target surface blocks physical directions.
Three centimeters is a conservative adapter rejection threshold, not a measured device error bound.

All centimeter movement fields require the same hand/target frame stamp, identical RGB-D
receive timestamp, locked target, valid geometry, high confidence, an agreeing direct fingertip
check, and a matching explicit physical calibration record. Lost tracking, mismatches and
>300 ms local evidence age suppress every physical movement field and stop preview audio.
The panel independently clears stale readouts. Pixel offsets remain labeled pixels.

## Run now

From `VisionOS/backend`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-live-tracking.ps1 -DepthView
```

The stream remains `192.168.137.223:8888`, HYBRID is the default, and hands stay enabled.
Choose an object and enable Diagnostics to inspect raw values and depth provenance.
Without physical validation, **UNRELIABLE is expected** and centimeter movement cues remain
suppressed. A fused estimate shows amber estimated forward/side/height numbers for observation,
with raw XYZ confined to diagnostics. It never enters the validated movement/speech axes.

## Physical calibration record — owner/tester work still required

No accepted physical calibration record has been created or supplied in this session. The
record is an operator's measured validation artifact, not a model-generated assertion or a
checkbox that automatically proves calibration. Do not fabricate passing values.

For the same phone, lens, resolution, orientation and mounting setup:

1. Verify no mirroring and verify positive X/Y/Z with a ruler or fixed reference jig. Phone
   motion must not be confused with hand motion. Keep camera-relative wording; no user/body
   transform is implemented.
2. Check registered RGB/depth on physical surfaces and verify metric scale. Test at least three
   known hand-to-surface displacements on each axis, both signs, throughout the intended reach
   range. Record maximum absolute error; the adapter requires <=2 cm on each axis.
3. Independently check direct fingertip depth versus the palm/geometry estimate, including
   thin fingertips, occlusion and background leakage. A low discrepancy alone does not prove
   accuracy: both methods can be wrong. Resolve the reported 6.9 cm case physically.
4. Establish a verified phone-sensor-clock to desktop-monotonic-clock mapping with bounded
   uncertainty. PathOn supplies a sensor timestamp but this preview has no synchronization
   protocol. **The adapter refuses physical instructions without that mapping.** Never infer
   it by subtracting a packet's receive time: that would hide network latency. Native R1 or
   the stream owner must supply verified timing evidence. Clock mappings expire within 60 s;
   a reconnect/epoch change invalidates them. Local frame age alone remains diagnostic.
5. Record source, dimensions and **prepared/display-aligned** K from the actual setup. Revalidate
   after changing phone/lens/mount, mirroring, orientation, resolution or depth registration.

Schema accepted by `--distance-calibration` / PowerShell `-DistanceCalibration <absolute path>`:

| Field | Required content |
|---|---|
| `schema` | 1 |
| `physicallyValidated` | true only after the physical checks above |
| `validationId` | Nonempty identifier referencing the actual test evidence |
| `validatedAtUnixS` | Actual validation Unix seconds; expires after 24 hours |
| `coordinateSystem` | `display_rotated_camera_optical` |
| `mirrored` | false, physically checked |
| `host`, `port` | Actual source host and numeric port |
| `rotationClockwise` | 0, 90, 180 or 270 matching the preview |
| `rawSize`, `imageSize` | Actual `[width,height]` arrays before/after display preparation |
| `intrinsics` | Actual 3x3 prepared K; must match within 1% / 0.01 absolute |
| `depthAlignmentVerified` | true only after registration checks |
| `axisChecks` | keys x,y,z, each containing `samples` >=3, measured `maxErrorM` <=0.02 and `positiveDirectionVerified:true` |
| `sensorClock` | Verified `offsetToMonotonicS`, `uncertaintyS` <=0.03, `verifiedAtMonotonicS`, `validForS` >0 and <=60, and matching `connectionEpoch` |

Host/port identify the configured connection, not cryptographic physical-device identity.
The profile cannot independently prove an operator's measurements or clock calibration.
The adapter checks mapped sensor age plus its uncertainty <=300 ms and also bounds receive
age; UI and audio expire at the stricter deadline. No real mapping was supplied or created.
This version loads a profile at startup; it stops when clock evidence expires. Ongoing clock
renewal needs the stream/R1 owner rather than fabricated offsets or silently extended validity.
Consequently this remains a Windows measurement preview, not validated physical guidance.
Do not use it to direct physical movement until those checks have actually passed.

## Audio integration boundary

Claude's `audio_guidance.py`, devices, players and speech clips are unchanged. The live adapter
now gates its existing output and stops it immediately on unreliable/stale evidence. That path
still uses the existing direction-only clips; **numeric spoken distances are not wired into it**.

`distance_speech.DistanceSpeech` is an isolated, tested numeric speech scheduler ready for the
audio owner. It takes the same `DistanceReadout`, emits e.g. `Left, 9 centimeters.`, rotates
through required axes, has a one-second change gap / 2.5-second reminder interval / 2 cm
distance-change threshold, refreshes active playback only with fresh evidence, and cancels
on loss/reversal. It emits no grab/contact/ready-to-grasp message. Its player contract is
`busy`, `play(text, expires_monotonic)`, `refresh(expires_monotonic)`, `stop()`; asynchronous
synthesis and actual playback must recheck expiry and must not overlap.

Automatic approval review rejected a proposal to replace the shared live audio path, citing
interference with Claude's active audio code and an unsupported player argument. That proposal
was not applied; the standalone adapter and existing-audio reliability gate are the safer
alternative. Audio-owner binding and actual listening tests remain incomplete.

## Validation and files

45 focused tests passed (new directional/integration checks plus existing spatial, HYBRID,
audio and default-mode regressions). PowerShell syntax and changed Python module compilation
also passed. Focused simulated tests cover signs, reversed axes, historical hysteresis, stale/future/invalid
clocks, missing hand/target/depth, mismatched timestamps, source/orientation/K/calibration errors,
estimated fingertip, missing confidence, 6.9 cm disagreement, agreeing direct depth, numeric
speech scheduling and live-worker audio suppression. No provider or physical sensor is used.
The Tk panel was instantiated and updated with stale data; layout extent was 490x628 pixels
with diagnostics collapsed. Actual live video/physical LiDAR and audible numeric speech have
not been tested. No full regression suite was run.

Four additional display-only regression tests cover the old numerical example, low-confidence
estimate labeling, direct-but-uncalibrated labeling, and hidden numbers on stale/lost/conflicting/
mismatched/nonfinite data. They live in `backend/tests/test_estimate_preview.py`.

Changed for this directional request:

- `backend/r3_vision/directional_distance.py` (new typed readout/calibration gate)
- `backend/r3_vision/distance_speech.py` (new isolated audio-owner adapter)
- `backend/r3_vision/spatial.py` (provenance, direct-tip cross-check, polygon mask)
- `backend/r3_vision/spatial_panel.py` (axis-first display, diagnostics toggle)
- `backend/r3_vision/live_tracking.py` (integration and strict audio gate)
- `backend/start-live-tracking.ps1` (optional calibration file argument)
- `backend/tests/test_directional_distance.py` (focused simulated tests)
- `docs/DIRECTIONAL_DISTANCE_GUIDANCE.md` (this handoff)

The preceding performance request additionally changed defaults in the launcher/live CLI and
added `r3_vision/discovery_ab.py`, `tests/test_discovery_defaults.py`, and
`docs/HYBRID_DEFAULT_PERFORMANCE.md`. Its recorded-frame results precede these directional UI
changes; no new FPS guarantee follows from them. ALL OBJECTS stays opt-in. No commits/pushes.
