# DeskGuide Laptop / Reflex research harness

Independent Windows CPU prototype for the future native Reflex reaching feature. The current correctness reference is Proprio HAND2 / OBJECT2 / guidance3, audited at commit `6be3180e139b47924e442d0122af32ea840cd218`. See [CURRENT_VALIDATION.md](CURRENT_VALIDATION.md) for measured agreement, live failures and remaining differences. Earlier design/validation documents describe historical configurations and are superseded by that report.

## Run

From your repository root, enter `ios/Packages/Perception/Prototypes/DeskGuide-Laptop`. The prototype's source, models, fixtures, and evaluation tools remain together; historical reports describe the original laptop checkout.

Keep the laptop on Cal Hacks 13.0 and the phone on the existing TONY 0864 hotspot. Open PathOn and keep streaming foregrounded. No hotspot reset or password access is needed. Close other streaming clients.

```powershell
# Start from the VisionOS repository root.
Set-Location 'ios\Packages\Perception\Prototypes\DeskGuide-Laptop'
$phoneIP = Read-Host 'Current IP displayed by PathOn'
.\.venv\Scripts\python.exe -m deskguide --host $phoneIP --port 8888 --target cup
```

Last confirmed endpoint: 192.168.137.181:8888. Enter the current displayed address if it changes. Add `--preview` for camera-only mode. Calling Python directly avoids PowerShell script execution-policy restrictions.

The installed environment is Python 3.12.15, Torch CPU (CUDA unavailable). For a new compatible Python 3.12 installation:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe setup_models.py
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Controls: R reset target, M mute, D depth view, O rotate, V mark object appearance for timing, F11 fullscreen, Escape exit fullscreen/close. Enter manually completes/pauses the task; it does not detect a grasp.

## Active configuration

RGB long side 960; clockwise rotation 90; medium/high depth confidence accepted; YOLO11s CPU at inference size 640, confidence .2, every processed frame; MediaPipe VIDEO two hands with fixed 160px replicated border and .3 detection/presence/tracking thresholds. Palm LiDAR plus hand geometry estimates fingertip depth. Shape-only estimates preserve the marker but cannot authorize audio. Latest frame slot, no growing processing queue. Guidance stops when freshness exceeds 300ms.

The original files are MIT; active YOLO dependencies/weights are AGPL-3.0. Read [THIRD_PARTY.md](THIRD_PARTY.md) before distribution or closed-source iOS integration.

The Python UI does not compile for iOS. Native Reflex should supply its existing ARSession frames to equivalent geometry/tracking logic and route guidance through its central audio/haptic scheduler. This harness now resides in the Reflex repository; final native adapters and confirmed integration contracts are still needed. See the [repository ownership guide](../../../../../docs/REPOSITORY_STRUCTURE.md). USB remains physically unverified; working Wi-Fi transport is the development path.
