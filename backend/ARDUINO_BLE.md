# Laptop → Arduino 101 BLE

Opt-in connection for the existing four-servo CurieBLE sketch. The laptop runs
vision and sends BLE commands directly; the iPhone continues providing video.
Existing iOS manual controls and speech/video features remain available.

## Start

1. Upload `firmware/Arduino101ServoController/Arduino101ServoController.ino`
   to the Arduino 101 using its Curie board support. Power the mechanism according
   to the hardware teammate's wiring. Disconnect the iPhone Arduino test screen
   so the laptop can connect.
2. In **the same Python environment used for tracking**, from `backend`, run:

   ```powershell
   python -m pip install -r requirements-ble.txt
   ```

3. Add `-ArduinoBLE` to your normal PowerShell launch, retaining your existing
   camera, audio, model and voice options. For example, from `backend`:

   ```powershell
   .\start-live-tracking.ps1 -PhoneHost auto -Audio -Voice -ArduinoBLE
   ```

   Or add `--arduino-ble` to an existing `python -m r3_vision.live_tracking` command.
   The bridge scans for the service below and connects only if exactly one matches.
   For multiple matching boards, provide `-ArduinoAddress '<device address>'`
   (Python: `--arduino-address '<device address>'`). macOS uses a device UUID.
   A failed/disconnected link requires restarting the program; it never silently
   reconnects and replays a prior direction. Connection state appears in console
   logs and tracking diagnostics. Missing Bleak leaves the vision pipeline running
   and reports a dependency error.

4. Find a target and place your hand in view. Only a fresh, locked target plus
   fingertip from the same frame can drive left/right/up/down. Geometry uses the
   upright camera image; verify physical mounting/direction mapping before wear.
   This feature does not map forward/back, grasp, success, or cloud prose to servos.
   It works with audio off and with `-Agents`.

5. Stop tracking, say “stop tracking” or “be quiet”, or close the program to release.
   Quiet disables hardware until a new find request. Tracking/hand loss, errors,
   or evidence older than 300 ms also release. Hardware is disabled by default.

## Existing firmware contract (unchanged)

| Item | Value |
|---|---|
| Advertised name | `ServoController` |
| Service | `19B10000-E8F2-537E-4F6C-D104768A1214` |
| Command characteristic | `19B10001-E8F2-537E-4F6C-D104768A1214` |
| GATT operation | Write **with response**, ASCII text, no newline |
| Press | `up0`, `down0`, `left0`, `right0` |
| Release | `up80`, `down80`, `left80`, `right80` |
| Stop all | `reset` |
| Pins | Up 2, down 3, right 4, left 5 |

## Hardware discrepancies to confirm

- The `*80` release names actually command **70°**, while press commands use 0°.
  These angles are preserved from the existing sketch.
- This is **one four-servo controller**, not the older specification's two-band
  binary protocol. Do not send that protocol's packets to this characteristic.
- The sketch has a 2-second independent contact cutoff and disconnect release.
  It does **not** implement the older spec's 500 ms heartbeat watchdog, generation
  rejection, battery/status notifications, or actuator acknowledgment. A successful
  GATT write does not prove a servo moved.
- The laptop requests nominal 200 ms pulses with a 300 ms release interval, one
  direction at a time. These are host scheduling targets, not measured actuator
  timings. A stalled BLE write or killed host can leave firmware's 2-second cutoff
  as the final bound. This implementation does not claim the original T02 pass.

## Bench acceptance (hardware teammate)

Secure the mechanism off-body first. Confirm each of the four cues matches the
mounted direction, releases to the intended physical position, and never jams.
Then verify: cover camera, remove hand, lose target, stop/quiet, close app, kill
process, turn Bluetooth off, unplug board, and reconnect manually. Record actual
release latency and verify the firmware timeout independently. Check a second
matching board prevents automatic selection. Only then try a sighted user;
blindfolded tests need the existing spec's spotter and harmless test object.

## Validation

Mocked BLE and synthetic evidence tests are in `tests/test_arduino_ble.py` and
`tests/test_hardware_guidance.py`. They test command encoding, timeout, freshness,
stop cancellation, discovery, no replay, and integration gates. They are not a
radio, Arduino compiler, servo-onset, or live-user acceptance result.
