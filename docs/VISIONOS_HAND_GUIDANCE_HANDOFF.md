# Hand-guidance development handoff

The independently maintained Windows prototype is in ios/Packages/Perception/Prototypes/DeskGuide-Laptop. This updates the team repository layout; it does not replace or alter native Swift packages. The local borrowed research baseline, its assets and launcher are deliberately absent. No permission to redistribute original Proprio implementations has been established.

Included: automatic YOLO11s cup/class-41 beverage-can acquisition, official MediaPipe hand landmarks, independently maintained metric geometry, latest-frame capture, nonblocking detection scheduling, freshness-gated directional audio, stale-overlay clearing, configuration and tests. Hardware behavior remains unverified; this prototype is not a production accessibility guarantee.

Integration proposal: VISIONOS_GUIDANCE_INTERFACE_PROPOSAL.md in the prototype. Consume hand/target XYZ, displacement, distance, confidence, depth source, frame/clock identity, observation time and STOP. Keep the 300 ms freshness gate and reject predicted/estimated geometry for movement cues. R1 owns perception/output scheduling; R2 owns BLE transport; H1 owns actuator patterns. This is a proposal, not a shared BLE contract or implemented iOS adapter. Native Apple camera/LiDAR/hand tracking should feed the future interface directly.

Setup uses Python 3.12 plus setup.ps1; binaries, model weights, environment, recordings and private logs are not shipped. Model setup uses official publisher downloads. Ultralytics/YOLO are AGPL-3.0; the original MIT source license does not relicense dependencies. Resolve distribution licensing before using YOLO in a closed-source iOS app. See THIRD_PARTY.md for accurate historical and current provenance.

Local checkpoint/stash preserves pre-sync work. No Git history was rewritten. Existing teammate iOS changes remain in the feature branch ancestry. Live camera, accuracy, FPS, audio and wristband integration require owner testing.
