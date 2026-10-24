# Licensing and provenance

Original DeskGuide Laptop code is MIT (LICENSE). This does not relicense third-party dependencies or model assets.

| Component | Terms / provenance | Role |
|---|---|---|
| Python 3.12.15 | PSF; Tcl/Tk has separate BSD-style notices | Interpreter and UI |
| NumPy 1.26.4 | BSD-3-Clause | Numeric geometry |
| OpenCV contrib wheel 4.11.0.86 | Apache-2.0 plus wheel-bundled component notices | DNN, LK, RANSAC, image I/O |
| NanoDet model and official decoder | Apache-2.0, [OpenCV Zoo model directory](https://github.com/opencv/opencv_zoo/tree/main/models/object_detection_nanodet) | Lightweight CPU object detection |
| MediaPipe 0.10.21 | Apache-2.0, [upstream](https://github.com/google-ai-edge/mediapipe) | Hand initialization |
| Hand Landmarker task | Official Google-hosted model linked in [task documentation](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker/index) | Palm and index landmarks |
| sounddevice 0.5.1 | MIT; bundled PortAudio retains its own MIT-style notices | Audio callback |
| Pillow 11.1.0 | MIT-CMU/HPND-style notices | Tk image rendering |
| psutil 6.1.1 | BSD-3-Clause | CPU and memory telemetry |

`models/NANODET-LICENSE` and `models/MEDIAPIPE-LICENSE` retain downloaded upstream license text. `models.lock.json` records exact URLs, byte sizes and SHA-256 for all assets. The official NanoDet decoder is a distinct third-party file under `models/nanodet.py`; it is not claimed as independently authored. The hand task is downloaded from Google's published versioned model bucket, with the upstream repository license retained. The model card/distribution terms remain authoritative; repository licenses alone do not create additional rights to third-party training data or other assets.

Direct requirements are pinned in `requirements.txt`; `requirements.lock.txt` captures the tested full pip environment. MediaPipe installs transitive packages including JAX/JAXlib, matplotlib and protobuf; those are dependency artifacts, not additional heavyweight models used by the live pipeline. Their licenses remain with the installed distributions. No GPU inference is requested. Review full bundled notices before packaging a distributable executable.

The active audited configuration now installs Ultralytics 8.4.183 and YOLO11s weights (AGPL-3.0), with Torch 2.14.1+cpu and torchvision 0.29.1 (BSD-style licenses). NanoDet is an unused earlier asset. [Ultralytics' license page](https://www.ultralytics.com/license) describes AGPL-3.0 and Enterprise licensing. The MIT license covers original files only. This combined research environment is not a permissively licensed distribution. Resolve AGPL obligations or obtain an appropriate commercial license before incorporating this detector into a closed-source Reflex app.

The REC3D protocol adapter originated in our independently authored ObjectReachLab work in this conversation and was extended for rotation, relative backlog tracking and validation. ObjectReachLab's source and models were only read/copied; the application does not import that project. Standard One Euro and optical-flow methods are cited in DESIGN.md.

USB follow-up: `deskguide/usb.py` is an independent standard-library plist/socket implementation interoperating with Apple's installed Windows USB service. The [libusbmuxd protocol client](https://github.com/libimobiledevice/libusbmuxd) was consulted for framing and message-field facts. Its LGPL library is neither imported nor copied. Apple's driver/software remains separately installed under Apple's terms.

The existing Proprio evaluation directory is untouched. No Proprio source functions, weights copied from Proprio, voice assets, graphics or UI source are included in the feature. Isolated evaluation workers import the existing baseline only for user-authorized comparison; production code never imports it. YOLO weights were independently downloaded from the official publisher. Public Proprio architecture provided problem context, not implementation code. This rebuild is not represented as the old DeskGuide V4 or pre-hackathon submission source.
