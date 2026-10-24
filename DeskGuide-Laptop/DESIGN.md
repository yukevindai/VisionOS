# Tracking design and independence

This is the lightweight rebuild requested after the original DeskGuide folder was confirmed absent. It does not import, translate or reconstruct Proprio functions. It uses ordinary projective geometry, adaptive low-pass filtering, sparse optical flow and repeated-observation state estimation. Original source changes here are not represented as pre-existing DeskGuide V4 or hackathon submission code.

## Processing ownership

1. REC3D reader: bounded packet validation and one replaceable frame slot; reconnect epochs reset coordinate history.
2. Inference worker: transform images/geometry once, calculate grayscale once, advance local flow, schedule CPU models only when due, publish a new immutable result snapshot.
3. Tk main thread: scale/render one annotated result, update status and controls; never wait on inference inside the event loop.
4. PortAudio callback: one device, one latest command, sample-continuous oscillator, amplitude ramps and a freshness deadline. Missing metric evidence clears the command.

No unbounded list of frames, detector jobs or audio commands is created. Model calls are synchronous inside the worker, which prevents simultaneous expensive jobs from oversubscribing the CPU. Per-model cost estimates increase neural intervals under load.

## Fingertip lifecycle

The initializer is MediaPipe Hand Landmarker, with two candidate hands. Landmark 8 supplies an **observed initial index tip**, not an independent finger-only model. Identity matching compares wrist motion and handedness; the latter is a weak preference because view-dependent classification can change. The tracker can reject a distant new hand during short loss and permits a new explicitly numbered identity after a longer gap.

Local Shi–Tomasi texture features around the tip initialize pyramidal Lucas–Kanade. Forward-backward endpoint disagreement, photometric error, out-of-frame positions, spatially incoherent movement and elapsed time without detection all limit tracking. Median local displacement moves the observed tip between neural updates. This can drift or follow nearby background texture, so its lifetime is capped at 220 ms. It does not infer a fully hidden tip. Sparse depth checks reject implausible metric depth changes; they do not suppress accepted 2D evidence.

One Euro display filtering uses `alpha = 1 / (1 + 1/(2*pi*cutoff*dt))`, a low-pass derivative estimate and a cutoff increasing with observed speed. Timestamp gaps reset the filter. The metric point is calculated from the raw observation and current depth, not the smoothed display marker. Filter tuning is in pixel units and still requires live jitter/lag trials.

Reference: Casiez, Roussel and Vogel, [1€ Filter: A Simple Speed-based Low-pass Filter for Noisy Input in Interactive Systems](https://gery.casiez.net/1euro/), CHI 2012. `filters.py` is independently authored from the mathematical method; no upstream implementation was copied.

Reference: [OpenCV Lucas–Kanade optical flow documentation](https://docs.opencv.org/4.x/d4/dee/tutorial_optical_flow.html), including the image-constancy assumption. Optical flow is evidence subject to those assumptions, not proof of finger identity.

## Object lock and movement

NanoDet observes object bounds; local sparse features maintain a robust affine transform between detector updates. RANSAC rejects inconsistent features, and extreme scale change is rejected. Association favors a detected box near the current pose prediction or last visual reference. This handles one target, but appearance embeddings and reliable multi-instance identity are not implemented.

At least two consistent metric observations establish the initial lock. Candidate movement away from an existing world anchor requires three consistent observations; precision guidance pauses during verification. Hand-region overlap blocks anchor replacement. A memory anchor alone never enables precise audio. During loss, the remembered surface and planar bounds project with the current valid camera pose for up to the configured eight-second anchor lifetime. On no pose/no flow, the box is hidden. The planar box is a visualization approximation, not a reconstructed cup mesh.

This cannot infer a cup that physically moves while fully hidden. Consequently prediction is explicitly labeled, old memory expires, and audible precision requires fresh measurements. Missed hand detection is a remaining foreground-depth contamination risk.

## Geometry and quality

With image pixel `p=[u,v,1]` and depth Z, the optical point is `P=Z*K^-1*p`. Optical camera axes are right/down/forward. Let `S=diag(1,-1,-1,1)` and T be the ARKit camera-to-world transform. A point transforms as `Pw=T*S*[P,1]`, and reprojects through `P=S*T^-1*[Pw,1]` followed by `K*P/P.z`.

Image rotation changes the pixel transform A and optical rotation R. The implementation updates `K'=A*K*R^T` and `T'=T*S*R^T*S` in homogeneous coordinates, alongside image/depth rotation. Resizing scales K consistently. Synthetic invariance tests cover the 90-degree transform; this does not establish that the live iPhone metadata is correctly calibrated.

Neighborhood depth requires multiple finite in-range samples, high v2 confidence where provided, and a small local depth spread. The reachable-surface **candidate** is one actual measured pixel from a coherent lower-front set of patches. It avoids inventing depth at a box center, but still needs physical contact-region validation. A median absolute deviation proxy with a conservative floor is logged; it omits systematic error, registration error and pose error, so it is not a certified accuracy bound.

The adapter relies on REC3D packet-level RGB-D pairing and RGB-resolution K described in the [PathOn SDK documentation](https://github.com/PathOn-AI/PathOnRoboticsOSS/tree/main/software/iphone_sensor_suite/Record3DStream/sdk). No SDK code is imported or included. The optical exposure skew and absolute capture age are unavailable; the application reports that limitation explicitly.

## Detector and model scope

[OpenCV Zoo NanoDet](https://github.com/opencv/opencv_zoo/tree/main/models/object_detection_nanodet) supplies the official Apache-2.0 ONNX model and decoding dependency. Our adapter letterboxes native input, reverses the mapping and identifies model output tensors by shape rather than OpenCV-specific output ordering. [MediaPipe Hand Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker/index) supplies the palm/landmark initializer; learned hand-relative world landmarks are not treated as metric LiDAR depth.

An independent finger-only detector is not included: no licensed model was shown to improve the user's horizontal pointing poses on this laptop. That remains an optional evidence-driven investigation after real failure clips are available, rather than adding WiLoR or a heavyweight mesh model by default.
