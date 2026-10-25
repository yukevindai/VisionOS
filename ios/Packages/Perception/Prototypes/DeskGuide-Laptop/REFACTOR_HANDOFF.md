> 2026-10-24 update: audio.py, providers.py and diagnose_live.py were removed; guidance.py emits silent LEFT/RIGHT/UP/DOWN/FORWARD/BACKWARD/STOP events. The Mute control and audio status were removed from the UI. Statements below about audio describe the earlier state.

# VisionOS module and presentation handoff — 2026-10-24



Branch: feature/reflex-modular-ui. Recovery point: b9e99f7e526ae9d17e552fd5a0bf8da55c118fd6; earlier local checkpoints and stash remain. Both the original testing_ver_calhack directory and .local-research/proprio-baseline were left untouched. A before/after SHA-256 manifest of the local baseline is retained in ignored logs/refactor.



## Presentation



Original VisionOS title and camera/sidebar layout, large fingertip-to-target distance, XYZ directions, hand/depth/target state and audio status. No research-origin banner, baseline-status sentence or splash is displayed. No splash screen was introduced. CLI title is VisionOS hand guidance. Required notices and provenance remain outside the interface. SIMULATED labels remain mandatory for synthetic sources; none are represented as real observations.



The sidebar scrolls on smaller displays. Keyboard controls: R find again, P pause, M mute, O rotate, F11 fullscreen, Escape leave fullscreen. Tab/Space operate normal buttons. Estimated distances retain their source label and never enable movement audio. Old frames and pipeline faults clear live hand overlays. Pause clears audio and requests task-generation invalidation; dispatch and pause share a lock so an in-flight update cannot restore a paused command. No automatic grasp-success claim or completion control is displayed.



## Ownership map



| Responsibility | Module |

|---|---|

| Paired RGB-D capture, latest slot, timestamps, preparation | deskguide/stream.py; optional usb.py |

| Licensed inference adapters | hand_observations.py; object_observations.py |

| Hand and object observation lifecycles | hand_tracking.py; object_tracking.py |

| Metric hand fusion, surface geometry, filters, checked flow | metric_hand.py; geometry.py; filters.py; flow.py |

| Platform-neutral guidance policy | guidance_core.py |

| Orchestration and expiring guidance events | engine.py; guidance.py |

| UI state and annotations | presentation.py; overlays.py |

| Tk window; command line; configuration; resources | app.py; cli.py; configuration.py; paths.py |

| Host boundaries | contracts.py |



models.py and tracking.py retain compatibility exports. HandDetector, ObjectDetector, FingerTracker, ObjectTracker and hand_covers have identical AST bodies to the recovery commit; no tracking math was cosmetically rewritten. stream, metric geometry, guidance policy, audio, flow and filters also have identical ASTs. Functional tests were retained. Unused manual selection and disabled benchmark implementations were removed; their history remains intact. The automatic tests use a separate deterministic SIMULATED scene fixture. Model setup no longer downloads the unused NanoDet decoder/weights; their historical manifest and notices remain for provenance.



## Source and licensing audit



This branch changes the existing independently authored prototype, not the copied reference baseline. Active imports and staged files were inspected for reference paths, copied source and excluded artifacts. Public algorithm descriptions and the official MediaPipe/Ultralytics APIs underpin the existing adapters; no upstream Proprio implementation, voice asset or UI artwork was added. Historical comparison records and attribution were retained rather than hidden or rewritten. This audit records provenance evidence, not a new grant of third-party rights.



- Project source/UI/contracts: maintained as project-owned code under the existing LICENSE.

- MediaPipe hand task/library: official Google distribution, Apache-2.0 notices and publisher model terms retained.

- Ultralytics/YOLO11s: AGPL-3.0; required notice remains in third_party. Original-code MIT terms do not relicense this dependency. Licensing must be resolved before a closed-source VisionOS app uses this detector, or it must be replaced using an appropriately licensed implementation.

- NumPy, OpenCV, Pillow, sounddevice/PortAudio, psutil and Python: existing licensed packages; full installed notices and THIRD_PARTY.md remain authoritative.

- Original Proprio source and reference voice assets: redistribution rights unestablished, isolated locally and excluded. They are not VisionOS-owned replacements and are not shipped here.

- No provider, haptic implementation, model or dependency version was added or changed.



## Native and sponsor boundaries



integration/GuidanceContracts.swift is a separately authored, unlinked Swift contract proposal, not a Swift package or app integration. It defines typed observations for XYZ, depth source, confidence with provenance, coordinate/clock/frame identity, freshness, distance, direction, generation and STOP. Consumer validation is still required; decoding an ACTIVE value is not authorization. Confidence/synchronization thresholds remain integration decisions, never invented from visibility. Unknown absolute capture age remains unknown.



providers.py and the Swift proposal define replaceable speech, interpretation, haptic-output and data-service ports with no provider selected. The existing guidance_core imports only math/dataclasses; it does not depend on these services. R1 owns sensor/output scheduling, R2 owns HapticLink, H1 owns BLE/pattern definitions. No shared contract or native app module was overwritten. Do not connect raw research-baseline output to wristbands. Recompute age at consumption, enforce <=300 ms, reject predicted/estimated evidence, match generations, and stop both bands on loss/expiry.



## Validation and limits



68 automated tests passed in 4.906 seconds, including hidden Tk construction with camera/audio mocked, current/estimated/stale GUI state, pause dispatch, current geometry, automatic acquisition, audio expiry and a synthetic localhost reconnect. The first full run's localhost test timed out in the network sandbox; it passed alone outside that sandbox and in the final suite. No phone connection, recording, model inference, audible playback, benchmark or live test was run. Structural algorithm comparisons are additional evidence, not hardware proof.



Claude's single architecture check timed out after 40 seconds without a verdict; it was not retried. The one final read-only review completed with No blocking findings. Its two minor presentation/document findings were addressed: estimated directional values now carry an estimate marker, and handoff documentation is UTF-8. Claude did not execute tests, validate AST equivalence or inspect the Swift proposal. No repeated review was run. Swift is uncompiled here; iOS integration, actual UI readability, can recognition, LiDAR quality, performance, audio routing and wristbands remain unverified. The working research baseline remains the user-controlled comparison/reference path.

