# Reflex hand guidance

CPU RGB-D finger-to-object guidance prototype for Reflex. It receives paired RGB and LiDAR depth from an iPhone (PathOn REC3D stream), detects a beverage can automatically with YOLO11s (COCO cup/class 41), tracks the index fingertip with the official MediaPipe hand task, computes fingertip and target XYZ from current LiDAR depth, and emits one structured guidance event per frame: LEFT, RIGHT, UP, DOWN, FORWARD, BACKWARD or STOP. There is no sound output; audio will be provided later by a separate integration.

## Launch

Start PathOn streaming on the iPhone, on the same network as the laptop, and close other camera clients. From this directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\launch.ps1 -PhoneIP <CURRENT_PATHON_IP> -Python C:\Users\ngkho\OneDrive\Desktop\visionOS\DeskGuide-Laptop\.venv\Scripts\python.exe
```

On a new machine, run setup.ps1 with Python 3.12 (including Tcl/Tk) first; `-Python` can then be omitted.

Controls: R find again, P pause guidance (STOP), O rotate, F11 fullscreen, Escape leave fullscreen. Detection is automatic.

## Interface

Live camera with hand landmarks, index fingertip and target box; current guidance direction; fingertip-to-target 3D distance with per-axis offsets; hand, depth and target tracking state; FPS and frame age.

## Guidance events

`deskguide/guidance.py` holds the latest `GuidanceEvent` (direction, reason, delta in meters, distance, expiry, sequence). Axes are camera-optical: x right, y down, z forward; delta is target minus fingertip. The dominant axis is chosen with 1.25x hysteresis. STOP is emitted when geometry is unavailable, when every axis is within 2.5 cm, or when the event has expired (180 ms, never beyond the 300 ms frame-age limit). Guidance requires current LiDAR-supported hand and target geometry; estimated distances are display-only and always STOP.

No hardware success or post-change FPS is claimed. See [THIRD_PARTY.md](THIRD_PARTY.md) for dependency licenses and provenance, and [REFACTOR_HANDOFF.md](REFACTOR_HANDOFF.md) for the module map. Other documents in this directory are historical records.
