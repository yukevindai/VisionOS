# Native iPhone handoff — October 24, 2026

Continue from branch `codex/r2-r3-live-friend-assistance`. Development and device testing were paused at the user's request for handoff to a teammate with compatible macOS/Xcode. No physical-device pass has been recorded.

## Get the code and run

```sh
git fetch origin
git switch --track origin/codex/r2-r3-live-friend-assistance
```

If the branch already exists locally, switch to it and use `git pull --ff-only`. Preserve your own uncommitted changes.

1. Select an Xcode version that supports your connected phone. The original test phone reported iOS 27.2 beta; Xcode 26.6 could build/sign the app but could not install it because its developer image lacked this hardware variant. Updating macOS alone does not update Xcode device support.
2. From the repository root, prepare the ignored Core ML model (Python 3.12 required):

   ```sh
   mkdir -p .runtime
   python3.12 -m venv .runtime/coreml-export
   .runtime/coreml-export/bin/python -m pip install -r ios/Scripts/native-model-requirements.txt
   .runtime/coreml-export/bin/python ios/Scripts/prepare_native_model.py
   ```

3. Open `ios/Reflex.xcodeproj`, select the `Reflex` scheme, configure your development team and bundle ID, and select the connected, trusted, unlocked phone with Developer Mode enabled. Signing settings can go in ignored `ios/Config/Local.xcconfig`; start from `Local.example.xcconfig`.
4. For the first physical check, add `--native-device-check` to the scheme's Debug Run arguments. Start the check and observe actual camera/depth/hand counters with a hand in view. This route produces no movement cues, audio or cloud uploads. Record actual outcomes; remove the argument before testing normal assistance.
5. Test normal assistance with simulation off and grant camera, microphone and speech recognition access. Start with an ordinary cup or bottle in a controlled setting. Validate dominant-hand/orientation mapping, occlusion/lost tracking, stale evidence, local spoken Stop, Pause/End and interruptions before relying on guidance. The initial thresholds and physical behavior remain unvalidated.

## What is connected

- Native ARKit camera/LiDAR, Vision hand pose/tracking, the teammate's YOLO11s model converted to Core ML, Tasks, native Guidance and local audio/Stop are connected for cup/can, bottle and bowl requests. No laptop sensor stream or Record3D app is needed for this native path. The original Python research prototype is preserved.
- The separate **Read an image** route uses R3's Gemini screen reader and canonical target selection through an authenticated backend. Gemini results do not currently drive physical hand guidance.
- Friend-video UI, Auth0/Convex/LiveKit integration and helper web app are included. Real service deployment/configuration and a physical friend-video call remain outstanding.
- No approach/free-space/door traversal, automatic grasp verification, H1 wristband integration, Claude intent service or Cartesia service is claimed complete.

## Gemini and online configuration

The selected local configuration is `GEMINI_MODEL=gemini-3.8-flash` and `GEMINI_THINKING_LEVEL=low`. The API key is **not committed**; obtain it separately and put it in the server's secret configuration. The ignored local `.env` does not transfer with Git.

The real Gemini provider returned a correct selection on one synthetic two-button image in 2.34 seconds. Default effort timed out at five seconds. This is not a real-camera accuracy or latency benchmark. Keep the five-second timeout and do not automatically switch models.

Use [backend setup](../backend/README.md) for the private Python vision service and authenticated Convex bridge, and [video service setup](LIVE_VIDEO_DEMO_SETUP.md) for Auth0/Convex/LiveKit. To meet the user's **no laptop** requirement, host the backend in the cloud; do not treat running Python on a teammate's Mac as the final runtime.

## Validation and next work

Passing before handoff: 136 app unit tests and two reader UI tests on the simulator; Tasks 92, Guidance 11, Targets 20; backend 100 TypeScript and 43 Python tests; signed Debug and unsigned Release iPhone builds. The model smoke test uses a synthetic blank image. Real iPhone installation failed at the developer-image step, so camera, depth, speech and guidance are still pending physical testing.

Read [the full validation record](../ios/Validation/2026-10-24-native-r1-r3.md) and [iOS setup](../ios/README.md). The updated hosted CI has not been verified. Historical accessibility audit failures are recorded in the iOS README; the focused passing tests do not clear those failures.

Keep team ownership and safety boundaries in [the build specification](Cal_Hacks_13_Build_Spec_v3.md). `ios/R2_RELAY.md` and `ios/R2_HANDOFF.md` are intentionally local-only and absent from Git; this public handoff contains the information needed to continue.
