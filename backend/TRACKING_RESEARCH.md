# Target tracking replacement: research and measured evidence

2026-10-24. The earlier ORB-only reference matcher did not satisfy the user's live-camera
experience. Increasing provider timeouts did not fix target tracking. This change replaces the
per-frame algorithm; it does not claim a real-device acceptance pass.

## Published implementations reviewed

| Implementation | Evidence provided upstream | Decision here |
| --- | --- | --- |
| [OpenCV Zoo ViTTrack](https://github.com/opencv/opencv_zoo/tree/main/models/object_tracking_vittrack) | Pretrained ONNX model, Python/camera demo, demonstration video, tracking score, dataset results. [C++ camera sample](https://github.com/opencv/opencv/blob/4.x/samples/dnn/vit_tracker.cpp). | Selected as the temporal tracker. Uses the actual `cv2.TrackerVit` implementation, not a recreated neural network. |
| [OpenCV LK homography example](https://github.com/opencv/opencv/blob/4.x/samples/python/lk_homography.py) | Runnable point-tracking example with reverse-flow verification and geometric estimation. [Optical-flow tutorial](https://docs.opencv.org/4.x/d4/dee/tutorial_optical_flow.html). | Selected as an independent local context/motion check. Our adapter is independently written using OpenCV APIs. |
| [OpenCV SIFT](https://docs.opencv.org/4.x/da/df5/tutorial_py_sift_intro.html) | Scale/orientation-aware descriptors and executable feature-extraction examples. | Selected for reference-to-current recovery when temporal tracking is unavailable. |
| [LightGlue, official authors](https://github.com/cvg/LightGlue) | Learned matching, pretrained checkpoints, CPU/GPU benchmarks. | A credible alternative for harder wide-baseline matching. Not installed in this change; no performance claim on this laptop. |
| [CoTracker3, official authors](https://github.com/facebookresearch/co-tracker) | Online point-tracking API, checkpoints, demonstrations and evaluations. CUDA is recommended; small CPU tasks are supported. | Not selected for this small CPU-first desktop change. No laptop benchmark was run. |

OpenCV Zoo's performance table distinguishes ONNX Runtime benchmarking from OpenCV inference.
Its published numbers are not used as this laptop's measured speed. NanoTrack was considered;
the ViTTrack authors specifically discuss the usefulness of ViTTrack's varying tracking score
for loss detection. Scores alone still do not prove identity or visibility.

## Implemented architecture

1. Gemini and the existing selector still identify the control in one immutable reference.
2. A bounded JPEG history retains intervening upright frames: <=75 seconds, <=48 MiB, <=800
   entries. It does not record an unbounded video. Frames are initially processed at <=960px.
3. After identification, a cloud-side catch-up phase initializes ViTTrack ON THE REFERENCE,
   then advances over actual buffered measurements at up to 5 Hz. The local preview continues.
   A >500ms gap, visibility/check failure, dimensions mismatch or 8-second work limit aborts
   temporal reuse. It never copies the old box onto live video.
4. Without valid catch-up, SIFT plus mutual matching/RANSAC and immutable target appearance
   checks attempts recovery on a CURRENT image. SIFT matching is throttled after loss.
5. ViTTrack then updates frame-to-frame. Release requires its score >=0.5, forward/backward LK
   context consistency, agreement between measured context motion and the learned box, and
   immutable target appearance checks. A local multiscale template check refines its coarse
   box; conflicting peaks are rejected. Templates are not overwritten with new pixels.
6. Any failed check hides the box. Two distinct current matches are still required after loss.
   Stale frames and gaps reset temporal tracking. Existing epochs/generations and API budgets
   remain enforced. The frozen debugger and `--tracker orb` preserve the older diagnostic path.

## Local evidence (not live acceptance)

`python -m r3_vision.compare_trackers` reads already-saved camera captures, calls no provider,
and writes ignored `.runtime/tracker-comparison/comparison.json` plus an annotated example.

Three saved Gemini-selected floor-5 images produced nine ordered reference/current pairs:
ORB accepted **4/9**, SIFT accepted **6/9** with the same geometric/appearance gates. This is
match acceptance, NOT a semantic accuracy score; no manually labeled ground truth exists for
these pairs. In particular, SIFT does not solve every large viewpoint change.

Using one real saved phone image with **SIMULATED translations**, the ViTTrack/LK/appearance
adapter accepted **21/21** frames. Measured median was **14.43ms** excluding warm-up on this host;
projected corners matched the injected motion in that controlled sequence. It is not a live
camera test, an end-to-end latency measurement, or evidence of universal occlusion handling.

Tests also cover generated motion, replay gaps, occlusion, scene changes, low scores, false
neighbor jumps, SIFT recovery, asynchronous delayed identification, buffers and shared-frame joins.
Model tests explicitly skip if the model/API is absent; they never auto-download or call Gemini.

## Model provenance

The official `object_tracking_vittrack_2023sep.onnx` is **714726 bytes**, SHA-256
`2990f0b7cd44d92afa48cd97db6de7be113fc1d9594fddb74e2725c10478e91d`.
The download is checked against both values; weights and the upstream license are cached in
ignored `backend/.runtime/models/`. The upstream model directory states Apache-2.0. No upstream
tracker source files are copied into this repository. The first launch downloads the public
asset if missing; subsequent launches verify and reuse it. `python -m r3_vision.tracker_models`
can prepare it separately. No key or uploaded camera image is involved in that download.

## Remaining acceptance and hand integration

The user must still verify actual phone motion, reflective panels, target loss/return and screen
changes. Reflections, blur, tiny targets, large out-of-view movement and identical controls remain
limitations; this is a 2D visual test, not safe physical guidance. Claude owns the isolated
MediaPipe module, now wrapped by `hand_bridge.py` for one shared capture source and exact stamps
as described in [R3_HAND_FRAME_INTERFACE.md](R3_HAND_FRAME_INTERFACE.md). Its files are untouched.
