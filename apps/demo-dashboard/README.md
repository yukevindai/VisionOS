# VisionOS live demo dashboard

Audience-facing browser display for the **existing Windows R3 Python tracking process**.
Implements the approved [Figma design](https://www.figma.com/design/JnmjymGdRQGturYhNuQcwo?node-id=3-138)
with the ink/charcoal/dusty-rose palette. It does not use or modify the friend viewer.

## Run with the existing demo

From the repository root, add `-WebDashboard` to your working launch command:

```powershell
.\backend\start-live-tracking.ps1 -PhoneHost <CURRENT_PATHON_IP> -Demo -Audio -Voice -WebDashboard
```

Keep your actual phone address, Python environment, calibration, models and audio
device arguments. The sample address placeholder must be replaced. Existing
[tracking prerequisites](../../backend/LIVE_TRACKING.md) still apply. Open
**http://127.0.0.1:8765** on the same laptop. `-WebPort 8767` selects another port.
Direct Python entry: append `--web-dashboard --web-port 8765` to
`python -m r3_vision.live_tracking ...` from `backend/`.

- iPhone supplies video and available depth through the existing PathOn receiver.
- Laptop microphone uses existing Windows voice recognition (`-Voice`).
- Existing laptop audio output uses `-Audio` and `-AudioDevice` as configured.
- The original desktop window remains available. Close it to end the pipeline.
- This dashboard has **no wearable driver or telemetry adapter**. Hardware status
  stays unavailable rather than claiming a command was physically played.

The frontend is plain HTML/CSS/JavaScript, served by Python's standard library.
No Node installation, bundler, cloud deployment, API keys in the browser, or new
camera/microphone permissions are required. IBM Plex Sans is used if installed,
with the existing Windows Segoe UI/system font as fallback; fonts are not fetched
from an external CDN. Figma's shared Button nodes had no importable code component
in this repository; native accessible buttons implement those controls.

## UI

Find & Track, Stop tracking, Describe surroundings, and Quick: 6 objects use the
existing desktop command handlers. Quick is enabled only in the existing demo
recognition mode. Presentation view hides operator controls; exit it to access
those controls again. Operator setup explains current connection roles without
pretending it can change device settings at runtime. Diagnostics shows the latest
serializable pipeline snapshot, excluding image bytes.

The audio panel shows the **last reported cue**, not an invented verbatim transcript.
Stage labels derive from current evidence. Missing/estimated depth is never shown
as a validated physical distance. Only current calibrated axes enable directional
readouts; hand-to-target total distance is distinct from camera-relative axes.

## Display-only preview

To review the visual design without Python vision dependencies or devices:

```sh
python -m http.server 8766 --bind 127.0.0.1 --directory apps/demo-dashboard
```

Open `http://127.0.0.1:8766/?preview=active` (also `scan` or `lost`). The preview is
prominently SIMULATED, uses local illustrative artwork and never sends commands.
Use Operator setup's Preview state selector to switch states. Without the explicit
preview parameter, a disconnected page shows unavailable data; there is no silent
fallback to simulation.

## Transport and freshness

`GET /api/session` obtains a local session token. `GET /api/state` returns one bounded
snapshot with an optional JPEG. `POST /api/command` requires that token and same-origin
access. The service binds only to loopback and rejects foreign Host/Origin headers.
Do not expose it through a public tunnel; it is a single-machine demo bridge.

Snapshots require matching ticket and frame timestamps. At 300 ms, frames and
physical display fields clear. Calibrated evidence may expire sooner. The browser
also subtracts request round-trip time and expires data locally, including when
requests stall. Changing target or stopping clears the prior snapshot immediately.
None of this extends the engine's physical evidence lifetime or authorizes hardware
outputs. Slow browser rendering cannot generate new guidance.

## Validation

```sh
node --test apps/demo-dashboard/state.test.js
cd backend
python -m unittest discover -s tests -p 'test_dashboard.py' -v
```

Tests cover expiry, delayed delivery, disconnected/unlocked data, matching-frame
serialization, bounded commands, Stop priority, token checks and local-origin
access. Automated browser verification was unavailable in the implementation environment
because Chromium could not be installed. Unit tests do not establish live camera,
microphone, calibrated guidance or wearable acceptance. Run that complete
path on the existing Windows laptop and iPhone before the physical demo.

## Agent orchestration

With orchestration enabled in the tracking process, Ask VisionOS submits an
open-ended request to the coordinator. Describe surroundings and What did I miss?
select the corresponding specialists. The agent panel displays reported activity,
handoffs, latency, clarification and errors, independently of camera freshness.
Provider prose is never used as a physical guidance cue. Without orchestration,
legacy Find and Describe controls retain their existing behavior through the
tracking process; recent-event recall requires orchestration.

Laptop recognition enables Windows open dictation alongside the Stop command
grammar in orchestration mode. Stop bypasses cloud intent routing and clears queued
requests and output. Recognition quality and interruption latency still require
microphone/speaker testing on the configured Windows laptop. Agent audio uses the
existing output player, with local guidance above conversation and no replay of
interrupted speech. The browser does not request microphone access.
