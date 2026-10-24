# Source-grounded methods and live failure repair

Reviewed read-only: `C:\Users\ngkho\OneDrive\Desktop\testing_ver_calhack\upstream\hands.py`, `perception.py`, `stream.py`, and the directional mapping in `guidance.py`. No upstream module was imported, edited or copied into this project. The implementation below uses independently authored state tracking, geometry and filters. Shared mathematical methods do not imply equivalent accuracy or execution speed.

## What the actual reference does

* **Hand option 2** uses the MediaPipe hand model in temporal/video mode, with image-edge coverage, persistent hand selection, One Euro filtering and a fused hand-depth reference. Its depth approach samples broad palm support and blends LiDAR with a hand-geometry depth estimate rather than requiring a valid tip cell on every frame.
* **Hand option 5** adds learned YOLO hand keypoints and a limited depth/appearance continuation path initialized from a previously observed hand. It is not a separate invisible-fingertip detector. Its continuation and model configuration are not automatically appropriate for a CPU-only laptop.
* **Hand option 7** shares that hybrid hand mechanism but guides a palm reference instead of the index tip. Palm arrival and actual fingertip arrival are different outcomes. Our requested reference stays the index fingertip.
* **Object option 2** maintains world-space memory using camera pose, with a memory lifetime. **Option 3** establishes a stable lock from multiple observations and subsequently relies on camera motion, requiring an explicit new search if the physical object moves. Our prototype keeps neural verification and repeated movement checks instead of assuming an object is permanently stationary.
* Reference object localization looks for a coherent front depth surface and excludes hand-covered pixels. It distinguishes remembered object position from fresh evidence. The exact implementation and numerical constants were not transplanted.
* Guidance maps relative camera-coordinate displacement to horizontal stereo direction, height/pitch and separation-dependent cadence. These are standard directional mappings. The prototype retains its own callback implementation and stricter exclusion of guessed/occluded positions from precision commands.

## Independent changes made here

1. Native PathOn image orientation now defaults to **90 degrees clockwise**. RGB, depth, K and the optical pose basis rotate together. O rotates at runtime and resets tracking. The captured live image confirmed this orientation restores an upright scene.
2. MediaPipe now runs in **VIDEO mode** with strictly increasing timestamps, lower temporal continuation threshold, and an edge margin. If initialization fails, the next call tries a larger edge margin and resampled input; recovered points are mapped back to actual camera pixels. This is an explicitly bounded view-recovery strategy, not a new finger-only model. Local LK observations still bridge neural calls and still expire.
3. Broad palm measurements provide a robust depth-consistency check. A palm-derived planar tip position is labeled **ESTIMATED_3D** and never becomes a precise fingertip measurement. The reference's hand-size/bone fusion and coasting are not used to authorize movement here.
4. Direct tip depth accepts a center-supported coherent cluster in a small neighborhood. It still requires a valid high-confidence center and supporting samples. It does not substitute a nearby foreground blob or a palm estimate for observed tip depth.
5. Depth lookup uses the corresponding scaled depth cell rather than rounding toward the next cell. This fixes a half-cell bias at narrow foreground edges.
6. Hand inference and object neural inference overlap on separate bounded workers; object depth/anchor updates still wait for the current hand result. There are no growing detector queues.
7. The detector threshold is now 0.30 following an actual upright cup candidate at 0.347 that the former 0.40 threshold rejected. Lower detector score alone cannot establish a metric anchor: repeated depth evidence and hand exclusion still apply. NanoDet is retained as the licensed lightweight detector; no Proprio weights are copied.
8. Received FPS now uses a time window instead of averaging inverse packet gaps, which previously exaggerated bursty TCP delivery. Sensor timestamp FPS is reported separately. Camera darkness, lack of high-confidence depth and tip-depth rejection are visible in the panel.
9. Hazard and task-generation rejection are available in the dependency-free guidance policy as **prototype integration parameters**, not a final team-wide Reflex API. Native task/hazard producers and ALL_STOP dispatch are not wired here because the actual shared contracts and Reflex repository are absent.

## Real device evidence from this session

PathOn at the user-provided current hotspot endpoint delivered actual 1920x1440 RGB, 256x192 depth, intrinsics, pose and confidence maps. One initial sampled image was almost black and its confidence map entirely zero. A later uncovered-camera frame had real scene content and mixed confidence levels; a cup candidate appeared only in the upright orientation. Those are distinct samples, not contradictory validity claims.

Before repair, the selected live log had **569 processed frames**, no object lock, only two accepted fingertip depth measurements and no valid guidance frames. Its burst-derived received-FPS statistic was incorrect.

First repaired 20-second run: **316 processed frames**, with object VISIBLE in 56 and TRACKED in 198. No hand was detected in that run. A subsequent captured frame showed the fingertip outside the image, so it would be wrong to represent all of those lost states as a detector accuracy measurement.

After the user placed the whole hand in view, another 20-second run produced **267 processed frames**:

| Reported state | Frames |
|---|---:|
| Finger detected/tracked in 2D, including explicitly estimated-depth states | 249 |
| Finger lost | 18 |
| Finger with palm-assisted estimated depth | 173 |
| Object SEARCHING | 115 |
| Object VISIBLE / TRACKED | 43 |
| Object OCCLUDED | 91 |
| Object PREDICTED | 12 |
| Object other verification/uncertain states | 6 |
| Frames accepted for precise metric guidance | **0** |

These are algorithm state counts, not manually labeled tracking precision/recall or proof that every marker was correct. The user's hand position and cup occlusion changed during the trial. There was no ground-truth fingertip trajectory or independently measured entry/return instant.

For that run, median processed FPS was **14.0**, median sensor timestamp FPS **53.61**, pipeline p50/p95 **32/172 ms**, host receive-age p50/p95 **47/187 ms**, and relative backlog-growth p50/p95 **63.4/143.1 ms**. These are live measurements from the JSONL session, not the earlier blank-input benchmark. Absolute capture-to-host latency remains unknown. Audio was disabled for these diagnostic runs.

The final 12-second verification after the depth-cell correction timed out and received no frames. The correction has a passing regression test but has not yet been physically revalidated. Direct tip LiDAR remains a real limitation; **no precise approach audio, near-target threshold or grasp was validated** in this session.

## Reflex scope and missing integration artifacts

Only an empty VisionOS repository is available alongside this prototype. There is no actual Reflex Build Spec v3.1 file, no `ios/Packages/Perception` or `Guidance` package and no team contracts from sections 5–8 to inspect. The pasted architecture excerpt supplies requirements but is not those complete artifacts. No final shared Swift API or new SwiftUI app was invented.

Production destination is the reusable Reflex feature shared by OBJ, BTN, MNU and DOR. Approved targets come from R3; task generations and app state from R2; cue delivery goes through the existing central scheduler and R2 HapticLink/H1 hardware path. No second ARSession, audio scheduler or BLE manager should be added. Native integration must wait for the actual repository/contracts; Windows audio remains a research-harness adapter only.
