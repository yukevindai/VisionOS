# VisionOS hand tracking (desktop, CPU)

Isolated 21-landmark hand tracker for the Windows R3 preview. It reuses the existing MediaPipe Hand Landmarker adapter (`deskguide.hand_observations.HandDetector` in the DeskGuide-Laptop prototype: CPU delegate, VIDEO mode, up to two hands, 160 px replicated border for edge hands) and its downloaded `hand_landmarker.task`. No new model, receiver, window or dashboard. No sound, haptics or guidance.

## Interface

```python
from hand_tracking import HandTracker
from hand_tracking.render import draw_hands

tracker = HandTracker()                      # loads MediaPipe once
result = tracker.process(image, capture_timestamp_s, frame_id=stamp, color_order='RGB')
overlay = draw_hands(image, result, frame_id=stamp)   # optional; returns a copy
tracker.close()
```

Input
- `image`: the exact frame the target tracker uses. A PIL image (any mode, converted to RGB) or an `HxWx3 uint8` array; pass `color_order='BGR'` for OpenCV arrays. The tracker never resizes, rotates, crops or mirrors it.
- `capture_timestamp_s`: finite seconds on one monotonic clock, strictly increasing. A timestamp equal to or older than the previous call returns `STALE_INPUT` with no hands, so a frame cannot be processed twice.
- `frame_id`: any object (for example `FramePacket.stamp`). It is returned unchanged as `result.frame_id` for exact joins.

Output: `HandFrame`
| Field | Meaning |
|---|---|
| `status` | `TRACKING` (one or more hands), `NO_HAND`, `INVALID_INPUT`, `STALE_INPUT` |
| `frame_id`, `capture_timestamp_s`, `image_size` | Copied from this call; `image_size` is `(width, height)` |
| `coordinate_system` | Pixels of the supplied image, origin top-left, x right, y down; normalized = pixels / (width, height) |
| `hands` | Tuple of `Hand`, sorted by `track_id`; empty unless `TRACKING` |
| `primary` | Lowest `track_id` present (the longest-tracked hand), or `None` |
| `processing_ms` | Time spent in this call |

`Hand`
| Field | Meaning |
|---|---|
| `track_id` | Stable integer identity across frames |
| `identity` | `NEW`, `CONTINUED` or `NEW_AMBIGUOUS` (see identity rules) |
| `landmarks_px`, `landmarks_norm` | `(21, 2)` arrays in MediaPipe order; names in `LANDMARK_NAMES` |
| `in_frame` | `(21,)` bool: landmark lies inside the image (MediaPipe extrapolates hidden or off-edge points) |
| `index_tip_px`, `index_tip_norm`, `index_tip_in_frame` | Landmark 8 |
| `palm_center_px` | Mean of wrist and the four finger MCP joints (0, 5, 9, 13, 17) |
| `box_px` | Landmark bounding box `[x0, y0, x1, y1]` |
| `mediapipe_handedness`, `handedness_score` | MediaPipe's left/right label and that classification's score. This is not a detection confidence. MediaPipe assumes a mirrored selfie image; with an unmirrored rear camera the label is usually the opposite anatomical hand |

Landmark indices: 0 wrist; thumb 1-4; index 5-8; middle 9-12; ring 13-16; pinky 17-20; fingertips 4, 8, 12, 16, 20. MediaPipe's z coordinate is not returned: it is model-relative, not measured depth.

`to_dict()` on both types gives a plain dictionary.

## Behavior

- Every call returns only that frame's detections. Missing, invalid or stale input returns no hands; previous coordinates are never reused or extrapolated.
- Identity: a detection continues a track if its wrist is within 25% of the image diagonal of that track's last wrist and the track was seen within the last 0.5 s. A different MediaPipe handedness label adds a cost. Assignment minimizes total cost. If another assignment is within 0.05 of the best (for example crossing hands), the affected hands get new IDs (`NEW_AMBIGUOUS`) instead of a possible silent swap. A hand that returns after more than 0.5 s gets a new ID. All thresholds are constructor arguments.
- `reset()` clears identities (for example on reconnect, rotation or resolution change).

## Coordinates and rotation

Call the tracker on the same upright image used for target tracking; landmarks are then already in the target tracker's pixel and normalized coordinates. `upright_to_raw_norm(points, rotation)` and `raw_to_upright_norm(points, rotation)` convert normalized points between an upright image and the raw sensor frame for clockwise `rotation` in {0, 90, 180, 270}, matching `PIL.Image.rotate(-rotation, expand=True)`.

## Integration example

`r3_vision/hand_bridge.py` (owned by the target-tracking side) already follows this pattern:

```python
result = tracker.process(packet.image, packet.stamp.received_monotonic_s, frame_id=packet.stamp)
if result.frame_id == target.stamp and result.status == 'TRACKING':
    tip = result.primary.index_tip_norm          # same normalized space as the target polygon
```

A 2D overlap between the fingertip and the target box does not mean the finger touched the button. It carries no depth information.

## Replay and performance

From `backend`, with a Python environment that has MediaPipe (for example the DeskGuide-Laptop venv):

```powershell
C:\Users\ngkho\OneDrive\Desktop\visionOS\DeskGuide-Laptop\.venv\Scripts\python.exe -m hand_tracking.replay <folder of .npz frames or video> [--rotation 90] [--out renders]
```

Measured on this laptop (CPU, MediaPipe CPU delegate), tracker time per frame excluding the first warm-up frame, real recorded iPhone frames with one visible hand:

| Input | Frames tracked | Mean | p95 | Tracker-only FPS |
|---|---|---|---|---|
| 960 x 720 | 14 / 14 | 22.0 ms | 23.2 ms | 45.5 |
| 1920 x 1440 | 14 / 14 | 37.6 ms | 39.4 ms | 26.6 |

These figures exclude decoding, target tracking and UI drawing.

## Tests

`backend/tests/test_hand_tracking.py`: 13 simulated-detection tests (identity, clearing, input checks, coordinates, rotation, rendering) and 5 tests on real recorded iPhone frames (21 landmarks, rotation, disappearance and return, two hands, occlusion). The recorded tests skip when the local model or `DeskGuide-Laptop/logs/identical-input-hand-visible` is missing.

## Limitations

- Not yet run on a live PathOn stream; recorded frames are sparse (0.17 to 0.8 s apart), so identity continuity at 30 fps was tested with simulated detections and composed recorded frames.
- MediaPipe tracks at most two hands. It supplies no per-landmark visibility, so occluded landmarks are reported as estimated positions; only off-image landmarks are flagged.
- Edge-on and heavily rotated hands depend on MediaPipe; only 90 and 270 degree camera rotations were tested.
- Identity uses only 2D wrist position and handedness label; two hands that touch or cross are given new IDs.
