# Windows R3 camera experiment

This tool performs on-demand frozen-image interpretation only. It does not load the old
hand/object/audio engine, produce guidance, or modify Swift/R1/R2/H1 code. It reuses the
existing Gemini prompt, image preparation, ScreenModel builder and Target Selector.

## Install and launch

Use a Python installation with Tkinter (the standard python.org Windows installer includes it):

```powershell
python -m pip install -r .\VisionOS\backend\requirements-live.txt
```

Set `GEMINI_API_KEY` and `GEMINI_MODEL` in your current environment, or the existing ignored
`backend/.env`. Never put the key in a command-line argument or screenshot.

From the outer workspace folder, one command starts the tool using the user-provided default `192.168.137.223:8888` without prompting:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\VisionOS\backend\start-live-camera.ps1
```

Use `-Python <path-to-python.exe>` for an existing virtual environment. The desktop timeout is
30 seconds, configurable with the Python CLI `--timeout`. The production backend default remains 5s.

## Connect PathOn

1. Put the iPhone and Windows laptop on the same reachable Wi-Fi/hotspot network.
2. Open PathOn on the phone, enable its REC3D camera streaming server and keep it in the foreground.
   The launcher uses the user-provided endpoint `192.168.137.223:8888`. Override with `-PhoneHost <IP> -Port <PORT>` if it changes.
   This is the protocol the repository's existing `deskguide.stream.Phone` receiver expects, not HTTP.
3. Close the old DeskGuide program and other clients that may occupy the phone's stream.
4. Run the PowerShell command. No endpoint entry is needed. Wait for live camera pixels.
   A black view may mean the camera is covered; no frames may mean streaming is off, wrong port,
   network client isolation/firewall, or incompatible PathOn protocol. The exact current PathOn
   button names/version have not been inspected on this phone; no new endpoint is inferred.
5. Check orientation yourself. If sideways, quit and relaunch with `-Rotation 90` (or 180/270).
   Earlier prototype reports used 90 clockwise, but this tool does not assume your orientation.
6. Type a request in the entry, then click **Capture ONE frame** or press **F5**.
   The right image freezes immediately. The left preview keeps updating while Gemini runs.
   Each panel is captioned: the right shows `FROZEN CAPTURE <time> - NOT LIVE`; if no frame has
   arrived for 1 s the left image is cleared and reads `NO CURRENT FRAME`, never a stale picture.
   Only one request runs at a time. Edit the request and capture again when it finishes.
7. Read selection status, target label, actually available confidence (normally `not reported`),
   and Gemini processing seconds above the images. Red box appears ONLY on the frozen right image.
   NOT_FOUND, AMBIGUOUS, PROVIDER_UNAVAILABLE and PROVIDER_TIMEOUT never draw a guessed box.

## Other source modes

Run these from `VisionOS/backend`:

```powershell
# Only use a URL actually exposed by your streaming software, or a real camera index:
python -m r3_vision.live_camera --video 0
# Explicit visible preview ROI selection on the PRIMARY desktop; drag, then Enter; Esc cancels:
python -m r3_vision.live_camera --select-roi
# Fixed ROI in virtual desktop pixel coordinates (can include negative monitor origins):
python -m r3_vision.live_camera --roi 100 200 640 480
```

ROI fallback captures exactly the user-selected desktop area with Pillow ImageGrab.
Keep the PathOn preview visible, unobscured and in that position; move the R3 window elsewhere.
It does not follow a moved/resized/minimized window and cannot distinguish a frozen PathOn preview
from a live one. Reselect the ROI if the window moves. A visible capture can include other windows
if they overlap: select only the camera pixels. No automatic window discovery/screen transmission.
The interactive selector uses the primary monitor; use explicit ROI for other monitors and verify
Windows display scaling against the preview. No arbitrary app/window is captured automatically.

## Capture records and transforms

Each press writes an ignored `backend/recordings/r3-live/capture-<uuid>/` folder:

- `original.png`: exact decoded RGB pixels captured from the source, before requested rotation.
- `captured.png`: full-resolution clockwise-rotated image submitted to the existing pipeline.
- `capture.json`: raw/upright dimensions, rotation, identity crop within the captured pixels,
  desktop ROI offset if used, source frame ID/epoch and sensor timestamp if supplied by REC3D,
  local arrival/snapshot clocks and snapshot UTC. It does not store endpoint URLs or keys.
- `result.json`: existing pipeline result including model, selection, timing and provider errors.
- `annotated.png`: upright frozen capture with the selected box, no live tracking.

ROI is the source crop and its desktop offset is retained separately. No crop/resize is applied
between `original.png` and `captured.png`, only explicit rotation. R3 may uniformly downsize its
JPEG internally; normalized coordinates still map to full upright dimensions. Pixel bounds use
floor(left/top), ceil(right/bottom)-1, bounded to image edges, and reject invalid regions.
`raw_point` implements the inverse normalized rotation: 90 `(y,1-x)`, 180 `(1-x,1-y)`,
270 `(1-y,x)`. Source mirroring is unknown and unchanged. Neither exposure UTC nor true
camera/network latency is available; snapshot UTC is labeled as such, never phone capture UTC.
Frames older than one second since local arrival are refused. This is NOT the 300ms guidance gate.

## Offline validation

```powershell
cd .\VisionOS\backend
python -m unittest discover -s tests
# Test UI with the SAME live/ROI source, but a SIMULATED provider, no API calls:
python -m r3_vision.live_camera --select-roi --replay tests/fixtures/SIMULATED_gemini_reply_elevator.json
```

The replay reply is fixed and unrelated to what the camera sees. Use it only to test the UI,
never to measure recognition. Captures/results are labeled SIMULATED by the existing pipeline.
Errors show categories without echoing arbitrary URLs, keys or external exception contents.
Review the saved sanitized provider error for provider failures. Source failures require checking
PathOn/network; REC3D retries automatically, while OpenCV/ROI acquisition requires restart.

## Limits / remaining phone work

No current-phone streaming validation has happened. Frozen boxes are model proposals; Gemini can
be wrong and takes seconds. There is no continuous target tracking, depth validation, hand detection,
safety authorization, audio, haptics or 3D unprojection. Native iPhone ARKit/R1 integration still
must provide aligned camera/LiDAR/hand observations, calibrated geometry, task generation checks,
the 300ms freshness watchdog and STOP behavior. Desktop selection cannot authorize movement.
Closing the tool stops preview; an already submitted cloud request cannot be recalled and its
worker may exit without saving its result. Local recordings are retained until you delete them.

## Implementation-host validation (Windows, 2026-10-24)

- All 33 offline backend tests passed (26 existing plus 7 desktop capture tests).
- Hidden-window Windows Tk/OpenCV smoke passed using a generated SIMULATED MJPEG video
  and the existing SIMULATED Gemini fixture: preview event loop, capture button, pipeline,
  annotated-image save and close. This is not a visual usability review or phone test.
- Default sandbox temporary directory denied one existing test's file operations; tests passed
  with TEMP and TMP set to the workspace-local backend/.test-tmp folder.
- Actual PathOn/iPhone stream and live Gemini from this tool have NOT been verified.

