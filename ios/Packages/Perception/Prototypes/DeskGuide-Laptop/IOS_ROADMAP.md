# Destination: standalone iPhone application

User decision: **everything runs on the iPhone, including detection, tracking, depth and audio**. A laptop-connected camera app is not the final product. The current Windows app remains the runnable experimental reference; PathOn and USB are temporary acquisition adapters used for development.

Reflex integration clarification: this is one shared feature for OBJ, BTN, MNU and DOR, not a second app. Native perception and guidance belong in the existing R1 packages once their real contracts are available. Reuse the app's existing ARSession and central audio/haptic scheduler; the component mapping below describes platform responsibilities, not permission to create duplicate services. R2 owns app/tasks/HapticLink, R3 owns approved target selection, and H1 owns wristband firmware. See PROPRIO_METHOD_REVIEW.md for the currently missing repository/contracts and source-grounded implementation differences.

## Native implementation boundary

| Current reference | Planned on-device replacement |
|---|---|
| PathOn/REC3D receiver | ARKit frame acquisition directly inside the app |
| Tk interface | SwiftUI camera preview and accessible guidance panel |
| OpenCV NanoDet ONNX adapter | Licensed Core ML object model, selected after device profiling |
| MediaPipe initializer | Evaluate Vision Hand Pose on actual pointing poses; retain a licensed alternative if needed |
| OpenCV optical flow | Native/portable visual tracker selected by measured continuity and runtime cost |
| Python geometry/state/filter code | Independently authored Swift equivalents with numerical parity tests |
| sounddevice/PortAudio callback | AVAudioEngine-based continuous stereo cues and native audio session handling |
| psutil telemetry | On-device frame timing, dropped frames, memory and thermal measurements |

Apple framework documentation: [ARKit sceneDepth](https://developer.apple.com/documentation/arkit/arframe/scenedepth), [Vision Hand Pose](https://developer.apple.com/documentation/vision/vndetecthumanhandposerequest), [Core ML](https://developer.apple.com/documentation/coreml), [AVAudioEngine](https://developer.apple.com/documentation/avfaudio/avaudioengine). These identify candidate native components, not a claim that model conversion or tracking accuracy has already been verified.

The iOS app should need neither a PC nor a streaming connection during a reach. It should perform processing on device, with no cloud dependency. There is no commitment to copy the desktop CPU-only restriction to iPhone hardware: Core ML compute-unit choices can be profiled for latency, power and thermal stability on the actual phone. No accelerator performance is assumed in advance.

## Contracts to preserve

* Capture uses one timestamped RGB-D sample, RGB-resolution intrinsics, depth in meters, explicit image orientation, depth confidence, camera-to-world pose, pose tracking quality and a session/world-origin identifier. Invalid depth or pose is explicit, not zero-filled. Handle interruption/relocalization by invalidating old world anchors.
* Optical coordinates remain right/down/forward. ARKit world conversion remains explicit. Vision normalized/oriented coordinates must be mapped to the exact camera/depth pixels before metric use; copying a normalized landmark directly into depth sampling is not valid.
* Fingertip states separate detected 2D, tracked 2D, measured 3D and lost. An optional estimate, if ever added, is labeled and excluded from precision guidance. 2D display survives missing depth; a fully hidden tip is never represented as observed.
* Objects separate visible/tracked, verification, occluded, predicted and uncertain. New motion needs repeated fresh evidence. Predicted memory is for visualization and reacquisition, not precise approach cues.
* `deskguide/guidance_core.py` is the dependency-free mathematical/policy reference. It returns metric deltas only for measured points, visible/tracked objects and fresh frames. The Swift port must match these signs, distances and rejection cases. Completion suppresses guidance and is user-confirmed. Near-target cues require real hardware validation.
* One newest-frame slot, bounded tasks, adaptive model cadence and an independent audio render path remain mandatory. Do not put neural inference, per-frame allocations or locks in the native audio render callback. UI updates stay on the native main thread.

## Work completed now

The engine accepts a `FrameSource` and `AudioSink` boundary rather than requiring construction of a Windows phone transport. The pure guidance policy was extracted and is used by the live Windows engine. Regression tests cover the source injection and geometry/freshness/occlusion policy. No existing iOS project or compiled iOS binary is claimed.

## What still requires the iOS implementation stage

An Xcode project, deployment target, signing and camera permission descriptions; ARKit capture and orientation tests; model licensing/conversion; detector and hand-pose profiling; native visual tracking; audio session, route changes and interruption handling; SwiftUI/VoiceOver behavior; thermal/endurance tests; and physical contact-region/near-target validation. Building and signing the native application requires an appropriate macOS/Xcode environment, which has not been established on this Windows workspace.

First native milestone: direct ARKit RGB-D preview, explicit frame/pose quality, no laptop. Then add hand initialization/2D continuity, object lock/reprojection, measured XYZ, and finally native continuous audio. Each stage should remain runnable on device. The Python application is a behavior and test reference, not a package that can be compiled unchanged into an iPhone app.
