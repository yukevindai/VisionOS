# R3 live tracking handoff (2026-10-24, ~15:40)

Branch `feature/reflex-modular-ui`. HEAD `740d682` (user merged origin) on top of `e0a874c` (user committed this work).
Pushed to origin. Do not commit/push without the user's permission.

## 1. Implemented (backend/r3_vision, Windows desktop preview)

- **Live tool** `live_tracking.py` + launcher `start-live-tracking.ps1` (PathOn `192.168.137.223:8888` by default).
  Modes: `--detection gemini` (GEMINI_ONLY, default) or `hybrid`; `--tracker lock` (default) / `vit` / `orb`;
  `--target-kind object|control`; `--gemini-reply inventory|target`; `--thinking`; `--media-resolution` (default medium).
- **Object lock** `object_lock.py` + `lock_session.py`: ViTTrack per frame; identity from colour histogram and
  object-only SIFT views (learns up to 6 views while strongly locked); refuses two similar objects; rejects jumps.
  States SEARCHING, IDENTIFYING, LOCKED, UNCERTAIN, **HIDDEN**, REACQUIRING, TARGET LOST, AMBIGUOUS.
  **HIDDEN (new):** when a confirmed lock is covered (hand or anything), the last box is carried with background
  camera motion (LK + RANSAC affine, BoT-SORT-style GMC) for up to 10 s, drawn dashed orange "remembered location,
  not live"; reacquired near that place from the last confirmed appearance; lost if background can't be followed,
  box leaves frame, or timeout. No audio and no 3D reading while HIDDEN.
  Stream pauses < 1.5 s keep the tracker; preview shows last frame without box.
- **HYBRID** `hybrid.py`, `local_detector.py`: YOLO-World v2-s (`.runtime/models/yolov8s-worldv2.pt`) runs in
  parallel with the normal Gemini search on the same frame. Unique plainly named object locks locally, then
  Gemini confirms or the box is removed (UNCERTAIN). Brands/colours/relations/duplicates wait for Gemini; Gemini box
  snapped to overlapping local box. One Gemini call per request. Phrase-embedding cache: ~132 ms/detect.
- **Gemini** `gemini.py`: object prompt (distinct labels, category, user's words tagged), `object_select.py`
  (joined brand words), `target_query.py` (FOUND/NOT_FOUND/AMBIGUOUS reply), media resolution, `minimal` thinking
  only for models that support it. User's own Find skips the 20 s cooldown. 8-object cap removed (it hid ambiguity).
- **Hand** `backend/hand_tracking/` (MediaPipe 21 landmarks, stable IDs) via `hand_bridge.py`; 2D fingertip readout.
- **3D** `spatial.py`: reuses DeskGuide `stream.prepare`, `geometry.surface`, `metric_hand.HandDepthFusion`.
  Target point = nearest visible LiDAR surface point of the object to the fingertip (hand pixels masked).
  Confidence HIGH/MEDIUM/LOW from share of ARKit confidence-2 samples. Direct tip-depth cross-check.
- **Big panel (new, not yet seen live)** `spatial_panel.py`: Tk panel beside the video: TARGET, TRACKING, DIRECTION,
  distance 60 px, FINGER/TARGET/DIFF XYZ grid 30 px (cm), axis legend (X+ right, Y+ down, Z+ away from phone),
  depth source/confidence, 2D offset in pixels (never cm), frame age/FPS, audio status, optional depth map
  (`--depth-view`). Stale frame clears values ("FRAME STALE"); no LiDAR shows "3D UNAVAILABLE". `--no-panel` disables.
- **Audio** `audio_guidance.py`: offline SAPI clips, one persistent sounddevice stream (winsound fallback), device
  select, grace 0.3 s for hand dropouts, 2D cues LEFT/RIGHT/UP/DOWN; FORWARD/BACK/CLOSER/READY only with measured
  LiDAR; 300 ms freshness; "Tracking lost" once. Events logged to console + `recordings/r3-tracking/audio-*.jsonl`.
- **Diagnostics:** `depth_check.py` (live REC3D field report), `detector_bench.py`, `gemini_bench.py`,
  `lock_replay.py`; per-frame `recordings/r3-tracking/session-*.jsonl`.

## 2. Uncommitted / in progress

- `backend/r3_vision/gemini.py` (mine, uncommitted): after the merge, (a) constructor allows `minimal` thinking only
  for `MINIMAL_THINKING_MODELS`; (b) `describe()` keeps the merged screen-inventory structure check for the inventory
  schema and, for other schemas (target query), requires the schema's required keys. Fixes 3 `test_target_query` tests.
- Untracked, NOT mine (another agent active): `backend/menu_reader/`, `docs/MENU_NATIVE_AUDIT.md`,
  `docs/MENU_TECHNOLOGY_RESEARCH.md`. Do not touch.
- This file `backend/HANDOFF.md` (untracked).

## 3. PathOn data verified (real recorded REC3D frames via `deskguide.stream`)

RGB 1920x1440 JPEG; depth 256x192 float32 metres (0.11-4.36 m); confidence uint8 0/1/2 (v2) with 63-69% low(0) and
6-27% high(2); intrinsics fx=fy~1397-1448, cx~961, cy~719 (RGB pixels); 4x4 pose validated; frame id + sensor
timestamp. `prepare()` zeroes confidence<1 depth. Earlier live runs: 3D LiDAR on 164 frames, size estimate 143.
Offline on real RGB-D: fingertip-to-can 17-19 cm, target confidence LOW (shiny can), finger LOW-HIGH, direct
fingertip pixel depth usually unavailable (palm LiDAR + hand geometry used). Not validated with a ruler yet.

## 4. Tests

Last full run (system python and DeskGuide venv): 193 tests; all mine pass. Failing in full runs only:
`test_hybrid_tracker...test_async_live_worker_with_vit_default_path` (Codex's timing-sensitive test; passes alone
3/3 and in every pairwise combination; still failing in full `test_[a-h]*` runs after the merge; cause not found -
not the background PNG save, not leftover threads). `test_vision_service` (teammate's HTTP test) had a one-off
localhost ConnectionAbortedError. Untested live: big panel, HIDDEN mode, new audio player, target reply, Flash-Lite.
Gemini benchmark never ran (no report at `.runtime/gemini-bench/report.json`; API key only in user's shell).
Standalone audio test ran here: all 5 cues PLAYBACK_COMPLETED on "Speaker (2- Realtek(R) Audio)" (audibility unconfirmed).

## 5. Running processes

None. Background test probe was stopped. No servers.

## 6. Remaining tasks (priority)

1. Live-check the big panel (readability at distance, layout on the user's screen); fix layout issues.
2. Validate fingertip-to-object distance with a ruler (far/mid/near, sideways hand, wrap, camera moving, low
   confidence); log error from `session-*.jsonl` offset3d. Do not trust the fixed 6 cm READY threshold until then.
3. Audio: confirm audible on the live run; directional calibration (move hand right -> finger X increases, down ->
   Y increases, away -> Z increases; spoken cue matches). Optional proximity-tone mode not implemented.
4. Investigate the full-suite-only ViTTrack test failure; then final test run and live demo.
5. User's new request (separate session): ALL OBJECTS mode (continuous YOLO-World inventory with stable IDs,
   async Gemini verification, request searches inventory first, falls back to HYBRID).
6. Run `gemini_bench` with the user's key; pick the HYBRID default from real results.

## 6b. ALL OBJECTS mode (2026-10-24, side session; uncommitted, NOT live-tested)

New `r3_vision/all_objects.py` (Inventory with IoU/centre association + LK/RANSAC camera-motion compensation, stable
IDs; background discovery thread, adaptive 1.0 s interval, skips when the tracking loop averages >120 ms/frame;
async Gemini verification of new/changed objects via the session RequestBudget, 15 s interval, 5 requests reserved
for Find; inventory-first selection: VERIFIED label match via object_select with no unverified-unchecked similar
object, or unique generic class -> HYBRID local lock + Gemini check; else HYBRID fallback; `SelectionLatency` logs
request -> verified lock to `selection-*.jsonl` in every mode). Hooks in `live_tracking.py` (`--detection allobjects`,
`_identify_allobjects`, `_identify_hybrid(local=...)`, overlay, logs); launcher `-AllObjects`. Verification is
withdrawn on jumps, crowded matches, or reappearing after >=2 missed passes and >1.5 s.
Offline smoke `python -m r3_vision.allobjects_replay` (DeskGuide venv; recorded capture d6a6..., SIMULATED pan/zoom,
SIMULATED Gemini = saved labels after 7 s): verified inventory picks 0.47-0.94 s request->verified lock vs HYBRID
7.23-7.45 s (Gemini delay simulated; live HYBRID logs show 6.8-27 s). Tracking FPS with hands, source 20 fps:
no discovery 15.2-16.7 locked; discovery 0.5 s interval 11.5-15.4; 1.0 s interval 14.1-16.1 (one run 10.7 under a
697 ms detect spike - machine load suspected). PyTorch threads 1/2/4 made no clear difference (GIL contention likely;
a separate discovery process would be the next step). YOLOE-pf works but slower (detect 335-650 ms) and labels are
wrong ("banana bread", "bank vault"); hints only. Tests: `tests/test_all_objects.py` 22 pass; full suite 215: venv OK,
system Python only the known flaky ViTTrack test. Confirmation replay: Red Bull 0.81 s via inventory, but Pepsi fell
back to HYBRID (7.47 s) and locked FPS was 12.1/15.6 vs 16.2/16.6. Fallback reason not yet read: see the
"ALL OBJECTS request" lines in `.runtime/allobjects-replay-confirm.log`. Next: that, then a live run (overlay
readability, real verify latency).

    powershell -NoProfile -ExecutionPolicy Bypass -File .\VisionOS\backend\start-live-tracking.ps1 -AllObjects

## 6c. DEMO mode: six objects + hand guidance (2026-10-24 evening; uncommitted, NOT live-tested)

`r3_vision/demo_objects.py`: Pepsi, Red Bull, Coca-Cola, deck of cards (Bicycle box), white half bagel, brown bagel.
Fixed YOLO-World vocabulary + SIFT homography for the card box (YOLO-World found it 0/25), identity by CLIP ViT-B/32
(`backend/weights/clip/ViT-B-32.pt`) + HSV against reference photos in `.runtime/demo-objects/<key>/` (harvested: 24
per object + 60 non-targets from recorded captures; `register` CLI for new photos). Open-set MIN_SCORE 1.2 / margin
.02; two lookalikes or any doubt -> Gemini via HYBRID flow; "bagel"/"can" with several distinct matches -> local
AMBIGUOUS. No background detection. `live_tracking --detection demo` (`_identify_demo`), launcher `-Demo`
(auto-harvests on first run). `demo_eval.py` (held-out sessions; quick run 15 frames x 4 scenes): 0 false matches
at 1.2 (77/77 absent not locked), 107/133 present locked locally, rest Gemini. Replay (real worker, recorded frame,
SIMULATED pan; references include this frame, optimistic): all six LOCAL, request->lock 0.83-1.20 s, 0 Gemini calls,
locked FPS 15.6-16.1 vs HYBRID 16.7-17.3; HYBRID 7.4 s for cans (7 s simulated Gemini), cards/bagels NOT_FOUND.
Audio: DEMO speaks 2D LEFT/RIGHT/UP/DOWN words (pixels; LOCKED + same-frame fingertip + 300 ms) when 3D is not
validated. Centimetre clips ("Left, 8 centimeters.", 348 SAPI clips) and NEAR_OBJECT ("Near the object. Check your
grip.") only through the other session's validated-3D path (needs `-DistanceCalibration` record; none exists, so
cm/forward/grasp stay suppressed - fingertip depth is ESTIMATED). Standalone audio test: all 9 cues
PLAYBACK_COMPLETED on Speaker (2- Realtek(R) Audio); audibility not confirmed by a person.
Tests: `tests/test_demo_objects.py` + focused 83 pass. Full venv suite 252: 6 fail - test_spatial/test_spatial_display
(stale vs the other session's ESTIMATED/panel changes, not mine), the known flaky ViTTrack test, and
test_live_tracking worker test (passes 3/3 alone; load timing). Next: live run on PathOn; ruler calibration.

    powershell -NoProfile -ExecutionPolicy Bypass -File .\VisionOS\backend\start-live-tracking.ps1 -Demo -Audio -DepthView -Request "Find the Pepsi can"

## 6d. Describe surroundings (AI) + DEMO quick summary + estimated forward word (2026-10-24 night; uncommitted)

- `scene_describe.py`: one Gemini call on the current fresh frame (user press only; session budget, cooldown skipped
  like Find). Prompt: hazards first, objects left/center/right near/far, readable brands only, "possibly", people
  never identified; whitelisted reply. "Describe surroundings" button in every mode, or type "what's around me".
  Speech: "Looking." clip at once, then `ClipPlayer.say()` (offline SAPI on a background thread, cached by text).
  SIMULATED end-to-end: reply 2.2 s (2 s fake delay), 7 s sentence played to completion. Real Gemini NOT tested here
  (no key on this machine). Log: `recordings/r3-tracking/scene-*.jsonl`.
- `AudioGuidance.reset()` now stops only guidance cues it started (it used to cut every sound each frame).
- DEMO "Quick: 6 objects" button: local summary (0.67-1.18 s on 6 recorded frames, all labelled objects, no extras).
- 2D+EST: word-only FORWARD/BACK when fingertip is over the target and the ESTIMATED depth gap >= 6 cm and >= 2x
  direct-tip disagreement (live log: 50/93 frames would say Forward, none Back). No cm, no grip cue.
- Phone moved to 192.168.137.161 (hotspot DHCP); pass `-PhoneHost`.

## 7. Commands (from `C:\Users\ngkho\OneDrive\Desktop\visionOS`)

    # Demo: HYBRID + audio + big panel + depth map
    powershell -NoProfile -ExecutionPolicy Bypass -File .\VisionOS\backend\start-live-tracking.ps1 -Detection hybrid -Audio -DepthView
    # GEMINI_ONLY baseline
    powershell -NoProfile -ExecutionPolicy Bypass -File .\VisionOS\backend\start-live-tracking.ps1
    # Audio test (no camera/models); add -AudioDevice <n> from the printed list
    powershell -NoProfile -ExecutionPolicy Bypass -File .\VisionOS\backend\start-live-tracking.ps1 -AudioTest
    # Live PathOn field check
    powershell -NoProfile -ExecutionPolicy Bypass -File .\VisionOS\backend\start-live-tracking.ps1 -DepthCheck
    # Gemini benchmark (needs GEMINI_API_KEY)
    Set-Location .\VisionOS\backend; python -m r3_vision.gemini_bench --frames 4
    # Tests (from VisionOS\backend); HYBRID needs the DeskGuide venv
    python -m unittest discover -s tests
    C:\Users\ngkho\OneDrive\Desktop\visionOS\DeskGuide-Laptop\.venv\Scripts\python.exe -m unittest discover -s tests

Notes: set `YOLO_AUTOINSTALL=false` (launcher does) - Ultralytics once auto-installed `clip` etc. into the DeskGuide
venv. Ultralytics/YOLO is AGPL-3.0. testing_ver_calhack (Proprio) is unlicensed: read-only, never copy.
