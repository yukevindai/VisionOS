# Audio removal update (2026-10-24)

The sound callback (audio.py), sounddevice dependency, unused speech/haptic/data provider ports (providers.py) and the PathOn sample-capture script (diagnose_live.py) were removed. Guidance is now a silent structured event (guidance.py). Comments and docstrings were removed from project-authored Python with an AST-equivalence check; executable code was unchanged. The third-party adapter notices in hand_observations.py and object_observations.py, all license files and the records below are retained. Ultralytics/YOLO11s (AGPL-3.0) is still required by the detector and remains unresolved for a closed-source Reflex submission.

---

# Module refactor provenance update



Hand/object adapters and lifecycle classes were extracted into named modules without changing their algorithm bodies. Original UI and optional integration proposals are project-authored. No research-baseline source/assets were introduced. Existing required notices, historical provenance and unresolved redistribution restrictions are preserved below. See REFACTOR_HANDOFF.md for the current source audit and replacement requirements.



---



# Current provenance status



DeskGuide no longer loads or launches the external Proprio project. The optional external hands.py research adapter and comparison workers are retired in ignored local history; their prior use remains accurately described in historical records below. Active wrappers/geometry/guidance are independently authored. No upstream implementation or audio/artwork was copied into the active runtime.



The project now installs MediaPipe 1.1.1 (Apache-2.0), preserving the previously working library version/configuration through its public API. It uses a project-local PSF-licensed Python 3.12.15 runtime with its original LICENSE.txt and bundled Tcl/Tk notices; local runtime binaries are ignored. The package/runtime were transferred from existing licensed installations, not from Proprio upstream source. Publisher notices are retained in installed distributions, models/MEDIAPIPE-LICENSE and third_party/ULTRALYTICS-AGPL-3.0.txt.



Ultralytics/YOLO11s remain AGPL-3.0. Original MIT code does not relicense these dependencies. See [CLEANUP_AND_ACQUISITION.md](CLEANUP_AND_ACQUISITION.md) for the full source/asset audit and remaining limits.



---



# Historical provenance records (superseded configurations)



# Explicit local-only reference use



The ignored .local_reference adapter may load the original external Proprio hands.py detector in place for user-authorized local research. Upstream copying/distribution permission is unestablished; the original source is not copied, edited or included in Reflex. This explicit local mode is borrowed reference functionality and is labeled as such. The normal independent path remains available. See [REFERENCE_RUN_FINDINGS.md](REFERENCE_RUN_FINDINGS.md) for the current component boundary; older no-import descriptions below refer to that independent path.



# Licensing and provenance



Current automatic can mode uses the existing Ultralytics YOLO11s weights (AGPL-3.0), OpenCV tracking and the existing hand task. No new model was added. The publisher license was transferred from the reference installation to third_party/ULTRALYTICS-AGPL-3.0.txt. Manual selection is disabled in the application. Reference configuration and principles were independently implemented; upstream source was not copied/imported.



Original DeskGuide Laptop code is MIT (LICENSE). This does not relicense third-party dependencies or model assets.



| Component | Terms / provenance | Role |

|---|---|---|

| Python 3.12.15 | PSF; Tcl/Tk has separate BSD-style notices | Interpreter and UI |

| NumPy 1.26.4 | BSD-3-Clause | Numeric geometry |

| OpenCV contrib wheel 4.11.0.86 | Apache-2.0 plus wheel-bundled component notices | DNN, LK, RANSAC, image I/O |

| NanoDet model and official decoder | Apache-2.0, [OpenCV Zoo model directory](https://github.com/opencv/opencv_zoo/tree/main/models/object_detection_nanodet) | Lightweight CPU object detection |

| MediaPipe 0.10.21 | Apache-2.0, [upstream](https://github.com/google-ai-edge/mediapipe) | Hand initialization |

| Hand Landmarker task | Official Google-hosted model linked in [task documentation](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker/index) | Palm and index landmarks |

| sounddevice 0.5.1 | MIT; bundled PortAudio retains its own MIT-style notices | Removed 2026-10-24 (audio output retired); listed for history |

| Pillow 11.1.0 | MIT-CMU/HPND-style notices | Tk image rendering |

| psutil 6.1.1 | BSD-3-Clause | CPU and memory telemetry |



`models/NANODET-LICENSE` and `models/MEDIAPIPE-LICENSE` retain downloaded upstream license text. `models.lock.json` records exact URLs, byte sizes and SHA-256 for all assets. The official NanoDet decoder is a distinct third-party file under `models/nanodet.py`; it is not claimed as independently authored. The hand task is downloaded from Google's published versioned model bucket, with the upstream repository license retained. The model card/distribution terms remain authoritative; repository licenses alone do not create additional rights to third-party training data or other assets.



Direct requirements are pinned in `requirements.txt`; `requirements.lock.txt` captures the tested full pip environment. MediaPipe installs transitive packages including JAX/JAXlib, matplotlib and protobuf; those are dependency artifacts, not additional heavyweight models used by the live pipeline. Their licenses remain with the installed distributions. No GPU inference is requested. Review full bundled notices before packaging a distributable executable.



The active audited configuration now installs Ultralytics 8.4.183 and YOLO11s weights (AGPL-3.0), with Torch 2.14.1+cpu and torchvision 0.29.1 (BSD-style licenses). NanoDet is an unused earlier asset. [Ultralytics' license page](https://www.ultralytics.com/license) describes AGPL-3.0 and Enterprise licensing. The MIT license covers original files only. This combined research environment is not a permissively licensed distribution. Resolve AGPL obligations or obtain an appropriate commercial license before incorporating this detector into a closed-source Reflex app.



The REC3D protocol adapter originated in our independently authored ObjectReachLab work in this conversation and was extended for rotation, relative backlog tracking and validation. ObjectReachLab's source and models were only read/copied; the application does not import that project. Standard One Euro and optical-flow methods are cited in DESIGN.md.



USB follow-up: `deskguide/usb.py` is an independent standard-library plist/socket implementation interoperating with Apple's installed Windows USB service. The [libusbmuxd protocol client](https://github.com/libimobiledevice/libusbmuxd) was consulted for framing and message-field facts. Its LGPL library is neither imported nor copied. Apple's driver/software remains separately installed under Apple's terms.



The existing Proprio evaluation directory is untouched. No Proprio source functions, weights copied from Proprio, voice assets, graphics or UI source are included in the feature. Isolated evaluation workers import the existing baseline only for user-authorized comparison; production code never imports it. YOLO weights were independently downloaded from the official publisher. Public Proprio architecture provided problem context, not implementation code. This rebuild is not represented as the old DeskGuide V4 or pre-hackathon submission source.



Current component-by-component source/asset audit and non-reuse decisions: [REFERENCE_INTEGRATION.md](REFERENCE_INTEGRATION.md).

