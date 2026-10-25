# Continuous Windows R3 tracking (2D visual experiment)

From the outer workspace directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\VisionOS\backend\start-live-tracking.ps1
```

## Object lock (default, 2026-10-24)

The launcher connects to `192.168.137.223:8888` without prompting (override with `-PhoneHost`/`-Port`) and defaults to `Find the Red Bull can`, `-Tracker lock`,
`-TargetKind object` and Gemini thinking `low`. Diagnosis of the earlier flicker:

- Gemini with thinking `high` took 22-30 s per request (two 30 s timeouts), so identification referred to a frame tens of seconds old.
- The panel prompt lists only pressable controls, so a can was not a valid answer.
- The previous tracker dropped the target whenever an immutable template score fell below 0.80, any 3x3 tile fell below 0.65,
  or the background failed a single planar homography. Camera pans with parallax fail these checks: on recorded frames with
  SIMULATED motion it kept the can on 1/16 pan frames, 11/18 rotation frames and 0/6 half-covered frames.

Second live run (11:17): Gemini found the can in 6.5 and 11.8 s, but the box still flickered. Causes found:

- Latency: at PathOn's 1920x1440 the worker ran resize, can tracking and hand tracking one after another at about 4 FPS,
  so displayed frames were often older than 300 ms and the preview swapped the video for "TARGET LOST" text. Now the frame is
  resized with OpenCV (37.6 to about 14 ms), tracking runs on a 640 px working image (still within the <=960 px frame
  contract), and hand tracking runs in parallel with can tracking on the same packet. Measured on a real Red Bull frame with
  SIMULATED motion: frame age median 63 ms, p95 151 ms, max 230 ms, about 8 FPS (source limited to 10). A frame 0.3-1 s old
  now shows the video without the box instead of blanking the preview.
- Identity checks: two failed SIFT re-checks dropped the lock, and a shiny can's features from one Gemini view did not match a
  different angle (7-8 matches between the two real captures). Feature checks no longer drop a strong lock by themselves,
  and up to 5 extra views are learned while the lock is strong, so reacquisition works from new angles. One weak frame no
  longer turns the box amber.
- Each run writes `recordings/r3-tracking/session-*.jsonl` with per-frame state, reason, tracker score, colour match,
  feature-check failures, frame age and FPS.
- Keep the Reflex preview window out of the camera's view: the camera then sees a second can on the laptop screen.

Third round (12:00-12:20), from session logs and saved Gemini results:

- Touching the can: the box dropped only after 0.4-0.7 s pauses between received frames, which reset the tracker. The lock
  now tolerates pauses up to 1.5 s, and the PathOn frame is converted once instead of on every 5 ms poll. MediaPipe loses
  the hand on about half the frames while fingers wrap the can; the hand overlay is cleared then, by design.
- 3D: `spatial.py` reuses the project's own DeskGuide `stream.prepare`, `geometry.surface` and `metric_hand.HandDepthFusion`
  (the old testing_ver_calhack code is upstream Proprio with no licence and is not copied). Measured live: LiDAR on 164
  frames, labelled size estimate on 143 (palm closer than 22 cm), unavailable on 2. The hand area is masked out of the
  can's depth. Shown as SIDE/HEIGHT/REACH; never contact detection.
- Bagel: Gemini detected the bagel in every saved frame. "Find the bagel" was AMBIGUOUS because two real bagels were in
  view with the same label; "Find the bread" and "Find the redbull can" were NO_MATCH wording problems. The object prompt
  now asks for distinct labels for same-kind objects plus a general category, `object_select.py` matches joined brand words
  in object mode only (the shared selector keeps Swift parity), and AMBIGUOUS lists the candidates and waits for a more
  specific request instead of re-calling Gemini.

Fourth round (13:00-13:10 logs): every box drop with a strong lock (score 0.91-0.94) followed a 300-700 ms PathOn stream
pause. During a pause the worker blanked the whole preview to "TARGET LOST" text and reset the tracker, whose ~200 ms SIFT
search then caused further pauses. Now a pause under 1.5 s keeps the tracker and shows the last frame without a box; the
search runs at 480 px every 0.25 s (at most ~43 ms here). If SIFT finds nothing within 2 s of Gemini's answer, ViTTrack is
run from Gemini's box on its own frame to the current frame (score, colour, size and position checked, then confirmed on
the next frame), for objects with few features. Two identical objects are detected from two separate consistent groups
of tied feature matches, so neither path chooses between them.

## HYBRID detection and audio guidance (optional, 2026-10-24)

`python -m r3_vision.detector_bench` (DeskGuide venv, Ultralytics) on 12 saved PathOn frames, Gemini boxes as reference:
YOLOE-26n-seg-pf 668 ms/frame, boxes on every object but misleading names (cans "tequila"/"root beer", bagel "banana
bread", 21 false "banana"); YOLO-World v2-s 357 ms/frame, every object boxed, bagel/cup named correctly, cans called
cup/bottle; YOLO11s COCO 329 ms, tumbler boxed in 5/12. Saved Gemini-only identification: 6.2-13.6 s. Local detectors
locate objects but are not identity evidence. `--hybrid` reports decisions: "Find the laptop" locked locally and correctly
in 7/7 frames in ~1.7-2.1 s; local relation ranking chose a can for "cup beside the laptop" in 5/5 frames, so relations,
brands, colours and duplicates wait for Gemini.

`--detection hybrid` (launcher `-Detection hybrid`, uses the DeskGuide venv): the usual Gemini search starts at once and
YOLO-World runs alongside on the same frame. A plain object word with exactly one plainly named candidate locks
immediately; Gemini's answer then confirms it or removes the box as UNCERTAIN. Otherwise Gemini's selection is used,
with an overlapping local box as its region. One Gemini call per request, as in GEMINI_ONLY (the default). Weights live in
ignored `.runtime/models`; Ultralytics auto-install is disabled (`YOLO_AUTOINSTALL=false`).

Speed (from the 14:30 HYBRID logs: Gemini 5.0-9.3 s per request, verified lock 6.8-15 s): live requests now send a JPEG
straight away and write the diagnostic PNGs afterwards (~0.6 s less before each Gemini call); Gemini 3 image detail
defaults to `medium` (560 instead of 1120 image tokens; `-MediaResolution low` = 280, per the Gemini media-resolution
docs); the object prompt lists matching objects first and at most 8 objects; YOLO-World encodes each phrase once and
keeps its predictor (identical names and boxes to `set_classes` on 20/20 checks, ~132 ms instead of ~600 ms). "cup of
coffee" / "deck of cards" now keep their object word. Live Gemini gains still need to be measured on PathOn.

Audio and latency investigation (14:45-14:52 logs):
- Audio was enabled and cues were computed on hundreds of frames, but MediaPipe lost the hand on about half the frames
  and every lost frame stopped the clip ~70 ms after it started. Now a dropout under 0.3 s neither cuts nor repeats a cue
  (no new cue is issued from invalid evidence), and playback runs on its own thread through one persistent sounddevice
  stream (device selectable; winsound fallback). Events: AUDIO_ENABLED, AUDIO_INITIALIZED, CUE_COMPUTED,
  CUE_SUPPRESSED, PLAYBACK_STARTED/COMPLETED/INTERRUPTED/FAILED (console and recordings/r3-tracking/audio-*.jsonl).
  `--audio-test` speaks Left/Right/Up/Down/Tracking lost with no camera or models.
- Recognition did not degrade with audio: every request after it was enabled was SELECTED correctly. Slow requests came
  from the 20 s API cooldown blocking the user's next Find (27 s case), one 17.8 s Gemini reply, and a PathOn stream
  slowdown (~1 s frame gaps) with no audio playing. A user's own Find now skips the cooldown (automatic retries keep it).
- The 8-object inventory cap truncated scene lists (second bagel, coffee cup omitted), which could hide ambiguity; it
  was removed. `--gemini-reply target` asks only FOUND/NOT_FOUND/AMBIGUOUS for the request (target_query.py).
- `python -m r3_vision.gemini_bench` compares models, thinking, image detail and reply format on saved captures with
  real API calls; it needs GEMINI_API_KEY in the shell or backend/.env and never prints it.

`--audio` (launcher `-Audio`): `audio_guidance.py` speaks LEFT/RIGHT/UP/DOWN from 2D, and FORWARD/BACK/CLOSER/READY TO
GRASP only with measured LiDAR, after checking a LOCKED box and fingertip from the same frame <=300 ms old. Offline
Windows voice clips, non-blocking, never overlapping, repeated at most every 2.5 s, direction changes confirmed over two
frames, silenced at once on invalid evidence, "Tracking lost" once after 0.6 s. Proximity cue only; sighted tester.

`object_lock.py` uses ViTTrack frame to frame, a colour histogram and object-only SIFT features (never background) for
identity, rejects jumps and sudden size changes, and searches the current frame with SIFT after loss. It refuses to lock
when two similar objects match. States: SEARCHING, IDENTIFYING, LOCKED (green), UNCERTAIN (amber, at most 0.5 s), TARGET LOST
(no box), REACQUIRING (no box until confirmed on the next frame). Gemini is never called while locked; a late Gemini result
cannot replace a current lock. With a hand visible, the preview shows the 2D distance from index fingertip to the can box;
this is not touch detection. `python -m r3_vision.lock_replay` compares trackers offline; `--tracker vit` keeps the previous path.
 Use the working phone,
network and endpoint, with optional `-Rotation 90`/180/270 as needed. Keep PathOn foregrounded
and streaming; close other REC3D clients. Override with `-PhoneHost <IP> -Port <PORT>` if needed. Dependencies remain
`backend/requirements-live.txt`; credentials remain `GEMINI_API_KEY` and `GEMINI_MODEL` in
environment or ignored backend/.env. No additional ML models are required.

Enter **Find floor 5** (or another control), click **Find & Track** or press Enter. A fresh frame
is selected automatically. Keep the panel visible while Gemini processes. The box appears on
LIVE VIDEO only after current-frame checks pass on TWO distinct frames. Move slowly at first.
Edit the request and click Find & Track to change targets. **Stop tracking** clears the box,
invalidates the active generation and stops new cloud requests; an already submitted request
cannot be recalled. Preview continues. No F5 or manual capture is needed.

States: SEARCHING -> IDENTIFYING -> REACQUIRING -> TRACKING. Any failed current-frame check
removes the box on that processed frame and shows TARGET LOST. ERROR identifies provider/local
failures; it never preserves a box. A scene change first loses tracking, then schedules fresh
identification within budgets. Reconnects or changed dimensions invalidate old references/replies.

## Current tracker (ViTTrack replacement)

The default is now pretrained OpenCV ViTTrack, forward/backward LK context checks, a bounded delayed-frame replay buffer, and SIFT recovery. The earlier ORB-only approach is retained only through --tracker orb. See [TRACKING_RESEARCH.md](TRACKING_RESEARCH.md) for published implementations, model provenance, measured comparisons and limitations.

The first launch downloads a verified 715 KB public model if needed; it is already cached on this implementation host. Tracking and model tests do not call Gemini except through the existing explicitly configured live provider. The frozen debugger is unchanged.

## Previous ORB diagnostic algorithm (only --tracker orb)

Gemini's historical box is never placed unchanged on new video. The original upright reference
and region are retained. ORB extracts up to 1800 context features around the control, since a
small button alone may lack enough features. Mutual descriptor ratio tests reject ambiguous
correspondences. RANSAC estimates a planar homography with >=12 inliers, >=65% consensus,
bounded reprojection error, feature support enclosing the target, and plausible non-reflected
geometry fully inside the current image.

Matching the panel is insufficient: CURRENT target pixels are warped back into reference
coordinates. Whole-target and informative tile correlations must independently pass. This
rejects occluded/changed buttons even when surrounding panel features match. Reference-to-current
matching repeats on every processed frame. No accumulated transforms, extrapolated boxes,
adaptive appearance templates or hardcoded object classes. Two matches are required after loss;
one failed check hides the box. Local checks verify geometry/appearance, not a second semantic
reading of the label. Gemini can initially identify the wrong control.

Local CV has its own worker and latest-only preview buffer; Gemini has a separate single worker.
Working images are at most 960 pixels on the long side, processed up to roughly 10-14 Hz,
depending on CPU/image cost. Displayed image and box always come from the SAME processed frame. Current ViTTrack uses the temporal path for routine updates, with the older per-frame ORB algorithm available only as a debugger.
The UI hides stale output after 300ms of LOCAL RECEIVE age. Phone exposure age is unknown;
this is a 2D visual experiment, not physical guidance evidence.

## Re-identification and API budgets

Defaults: >=20 seconds between request STARTS, <=3 requests in a rolling 60 seconds, <=20 requests
per program session. At most one request is in flight, including across target changes. Failures
count; target changes/clicks do not reset budgets. After 2 seconds of continuous loss, Gemini
re-identification is scheduled when allowed. Local reacquisition can run until that request
starts; during identification the box stays hidden. References expire after 60 seconds. Desktop
timeout is 60s (raised after observed 30-second timeouts); existing backend defaults are unchanged. At the session cap, an existing local
reference can continue tracking but no additional cloud requests are sent.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\VisionOS\backend\start-live-tracking.ps1 -Cooldown 30 -PerMinute 2 -MaxRequests 10
```

Additional settings, from `VisionOS/backend`:

```powershell
python -m r3_vision.live_tracking --host <ACTUAL_HOST> --port <ACTUAL_PORT> --rotation 90 --reidentify-after 3 --reference-ttl 90 --timeout 60
```

An explicit `--video 0`, real URL or video file can replace `--host/--port`.
The frozen debugger and its ROI fallback remain unchanged:

```powershell
python -m r3_vision.live_camera --host <ACTUAL_HOST> --port <ACTUAL_PORT>
```

## Debug records and scores

References are saved with the existing frozen tool's `process_capture` under ignored
`backend/recordings/r3-tracking/track-<uuid>/`: original/full upright image, source frame/epoch
if available, explicit rotation, snapshot/arrival times, crop metadata and full pipeline result.
The annotated PNG there is a REFERENCE STILL, not current tracking output. Live quadrilaterals
are in the resized upright preview coordinates and are never used for 3D/hand guidance.

UI/logs show state changes, inlier count, appearance correlation, Gemini processing time after
identification, request counts and budget waits. Correlation is an uncalibrated image similarity
score, NOT Gemini confidence or identity probability. Errors show sanitized categories; endpoint
URLs and API keys are not logged. If matching fails, move closer, show more panel context,
improve lighting and reduce blur. No extra endpoint/model is silently substituted.

## Tests, phone check and limitations

```powershell
cd .\VisionOS\backend
python -m unittest discover -s tests
```

Regression scenes/provider replies are SIMULATED: translation, motion sequences, 90-degree
rotation, delayed Gemini, partial/full occlusion, leaving the frame, unrelated/repetitive features,
changed control appearance, stale frames, scene changes, expiry, target changes, reconnects,
request limits, timeouts and HTTP failures. Worker integration delays the cloud while the source
moves, checks preview continues, verifies the box at the CURRENT location, then checks its removal
on occlusion. No test uses the real phone or API.

Reflective metal, blank/tiny controls, blur, large viewpoint changes, lighting changes,
parallax/nonplanar panels, changing screens and visually identical controls/panels can defeat
this conservative planar tracker. Two visually indistinguishable scenes cannot be semantically
distinguished by these image checks. Partial occlusions that barely change the checked appearance
may evade detection. These are limitations, not guaranteed universal occlusion/identity detection.
Difficult panels may frequently show TARGET LOST. No OCR confirmation is added.

Phone acceptance remains for the user: Find & Track an elevator control, move the phone, cover
the button, pan away/return, change requests and disconnect. Check that the correct box moves,
hides when lost and never remains on stale video. The actual phone experience has NOT been
verified here. No audio, haptics, movement guidance, LiDAR alignment, Swift or R1/R2/H1 changes.

## Implementation validation (Windows, 2026-10-24)

- 56 offline backend tests passed, including 20 new tracking regressions (19 core tests and
  one asynchronous worker integration test).
- Hidden-window Tk/OpenCV smoke passed with a generated SIMULATED moving MJPEG video and
  delayed SIMULATED Gemini reply: Find & Track, responsive preview, live current-location
  tracking, reference output save and shutdown. This was not a visual usability review.
- PowerShell launcher syntax and Python compilation checked. No phone stream or real API
  request was used in these validation runs. Frozen debugger remains available.


The UI retains the last Gemini outcome and geometric tracking-loss reason across automatic retries, and shows elapsed identification time. The launcher accepts -Timeout to override the 60-second desktop default.


Shared hand-module interface: [R3_HAND_FRAME_INTERFACE.md](R3_HAND_FRAME_INTERFACE.md). Claude owns the isolated hand module; the synchronized bridge is pending its actual interface. Frame stamps and orientation are already exposed by the target worker.


## Combined preview and requested Gemini configuration

The launch command now selects `gemini-3.8-flash` with `GEMINI_THINKING_LEVEL=high`, sends the
REST `generationConfig.thinkingConfig.thinkingLevel` setting and preserves Gemini 3's native
sampling defaults. This is the user's specifically requested model; no fallback to another model
is automatic. High reasoning may take longer; the desktop timeout remains configurable with
`-Timeout`. The backend's shared timeout default remains unchanged. The ignored local .env was
updated only for model/effort, without printing or changing its key. The launcher passes these
settings to its child process even if an older model is set in the terminal, then restores the
parent environment. No real Gemini 3.8 request has been run by the new validation tests.

Claude's hand module is now connected through an adapter without editing hand_tracking/.
The default preview shows the red target box, hand skeletons and yellow index-fingertip rings.
Both algorithms process the exact same upright RGB packet; frame ID, epoch, receive timestamp,
rotation and dimensions must agree. If hands disappear, the hand overlay disappears; missing
hand/target observations are not inferred from old frames. This is still a 2D visual test without
metric fingertip distance, audio, haptics or movement cues.

Pass `--no-hands` to `python -m r3_vision.live_tracking` for target-only debugging. If MediaPipe
is unavailable, the UI reports a hand-module error while target tracking remains available.
Combined optional dependencies: `python -m pip install -r requirements-preview.txt` from backend.
The hand module uses its existing local MediaPipe task asset; no R1 files are changed or downloaded
by the bridge. [Shared frame interface](R3_HAND_FRAME_INTERFACE.md).

A hidden-window Windows UI test passed using the actual ViTTrack model, a delayed SIMULATED
Gemini reply, and Claude's HandTracker with SIMULATED landmark detections. Both overlays were
present in one preview and the buffered reference caught up successfully. This is not an actual
phone/hand acceptance pass.
