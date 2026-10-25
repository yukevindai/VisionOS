# VisionOS multi-agent laptop demo

This opt-in runtime integrates the existing iPhone PathOn video/depth receiver,
local detection, reference-to-current tracker, calibrated geometry guidance,
Windows microphone recognition and laptop audio player. The native iPhone app
remains native iOS. This change does not add a second camera, wearable driver,
cloud deployment or friend-web workflow.

## Run

Use the existing [live tracking prerequisites](../backend/LIVE_TRACKING.md),
including the compatible Windows Python environment, local detection/tracker
models, microphone permissions and actual phone host. In `backend/.env` set
`GEMINI_API_KEY`, `GEMINI_MODEL`, `ANTHROPIC_API_KEY`, and
`ANTHROPIC_HAIKU_MODEL`. Optional `GEMINI_AGENT_MODEL` selects a separate Flash
model for scene/recall; optional `ANTHROPIC_SONNET_MODEL` enables explicit complex
intent escalation. Keys remain on the laptop, never in the browser or iPhone.

Choose exact model IDs from your account's [Anthropic models API](https://platform.claude.com/docs/en/api/models/list)
and [Gemini models API](https://ai.google.dev/api/models). Do not assume a model
listed in documentation is enabled for your credentials. Agent IDs have no default.

```powershell
cd backend
.\.venv\Scripts\python.exe -m r3_vision.agent_providers verify-models
cd ..
.\backend\start-live-tracking.ps1 -Agents -WebDashboard -Audio -Voice `
  -PhoneHost <CURRENT_PATHON_IP> -Python <YOUR_EXISTING_VISION_PYTHON> `
  -Model <VERIFIED_GEMINI_FLASH_ID> -Detection hybrid
```

Retain your actual `-DistanceCalibration` and `-AudioDevice` arguments. The
launcher `-Model` overrides `GEMINI_MODEL`; pass the verified ID explicitly.
Open `http://127.0.0.1:8765`. Direct Python invocation adds `--agents` to the
existing `--tracker lock --target-kind object --detection hybrid` command
(`gemini` detection is also supported). Startup verifies all configured model IDs
with authenticated catalog requests and fails clearly if unavailable. Catalog
success does not prove generation quota or latency; generation failures remain
visible. The existing demo/allobjects recognition shortcuts are not enabled in
agent mode because the new handoff requires request-specific Gemini verification.

Speak or type “Find the cup,” answer clarification such as “the red one,” ask
“Describe surroundings,” or “What did I miss?” Open-ended requests use the existing
Windows System.Speech engine with dictation enabled. An en-US dictation recognizer
must be installed. Text entry remains available if microphone recognition fails.
“Stop” is local and immediate after recognition; it does not call a provider.
Echo suppression permits Stop while speaking, but other requests wait until speech
ends. Recognition accuracy and recognition-to-stop latency require hardware tests.

## Runtime and safety boundaries

### Voice listening sessions

With `--voice`, say “Thanks Vision” (or “Thank you Vision”) to stop the current
task and speech and pause ordinary voice requests. Say “Hey Vision” to resume,
or combine it with a request: “Hey Vision, describe surroundings.” Stop remains
available even while paused or while VisionOS is speaking. These controls are
local and do not call an LLM.

Request listening starts active and pauses after 30 seconds without an accepted
request. The UI reports the paused state. Pausing request handling does not turn
off the microphone: Windows recognition continues locally to detect wake/stop
phrases; ordinary paused transcripts are discarded rather than routed or logged.
Close the demo or run without `--voice` to turn off its microphone recognition.
The idle timeout only pauses new voice requests; it does not cancel active
tracking. “Thanks Vision” explicitly cancels the current task as well.

`Coordinator` owns a session ID, task ID/generation, current goal, bounded dialogue,
target state, observation times and 64 diagnostic events. It delegates typed tools
through the direct Anthropic Messages API. Obvious find/describe/recall commands
bypass intent inference. Clarification and follow-ups retain dialogue context.
Optional Sonnet escalation is an explicit tool decision, not an automatic retry.

`VisualAgents` supplies distinct object, scene and social instructions and tool
access. All consume `SharedFrameService`, fed by the existing camera worker.
Object finding delegates to existing local detection/Gemini verification. A
provisional hybrid candidate cannot reach the tracker. Verified reference regions
must match the source frame, geometry, generation and epoch and be at most five
seconds old. The existing tracker then reacquires the reference in current pixels;
an old provider box is never directly applied to a current image.

Scene answers identify the age of their source image. Social recall uses at most
six samples from a memory-only history bounded by 20 seconds, 16 JPEGs and 4 MiB,
sampled at most once per second, plus 64 timestamped pipeline events. Camera
reconnection/geometry changes clear the buffer. Receive timestamps are laptop
monotonic times, not claimed sensor capture times. Responses must cite supplied
frame IDs; missing/insufficient evidence is explicit. The buffer has no recorded
audio, and cannot establish unseen causes, speech, emotions or intent.

One coordinator worker and one replaceable pending request bound semantic work.
Each routing/specialist/local-acquisition phase has a five-second maximum deadline; provider sockets
also time out. Stop invalidates contexts and immediately gates the sole audio
player, even while provider or camera work is pending. An in-flight HTTP request
may finish at the transport timeout, but its response cannot apply or speak.
The existing identification slot remains occupied until its worker exits.

`PriorityAudio` wraps the existing player: loss/guidance outrank conversation;
interrupted speech is discarded. Synthesis admits at most one in-flight job.
Model text enters only the conversation path. No model can produce movement cues,
authorize grasp/payment, clear hazards or bypass measured depth/calibration.
The existing 300 ms guidance evidence rule remains unchanged. Target loss or
stale frames cancel an active tracked task; recovery requires another request.
No provider runs in the per-frame hand/geometry guidance loop.

The dashboard reports actual coordinator activity, handoffs, response latency and
errors independently of camera freshness. Its prose fields never feed physical
guidance fields. Without `--agents`, the existing demo retains its behavior.

## Automated and live validation

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests
cd ..
node --test apps/demo-dashboard/state.test.js
```

Tests use mocked providers, generated/replayed images and fake audio/tracker
interfaces. They cover clarification and follow-up tracking, scene/recall evidence,
Stop during provider work, target loss, stale generations/frames, provider errors,
coordinate validation, queue bounds and competing audio. These are **SIMULATED**
checks, not live provider, iPhone, LiDAR, microphone, speaker or wearable acceptance.

Before a physical demo, verify authenticated generation for each configured model;
then run all requested scenarios on the iPhone/laptop with a sighted spotter and a
harmless object. Check open dictation, actual output device, measured stop latency,
camera disconnection, target occlusion, calibrated depth and overlapping speech.
The current implementation session has no provider credentials or attached device.

### Implementation validation — October 25, 2026

- Final local full Python suite: **364 tests run, 26 skipped, no failures**.
  This is automated simulation, not live hardware validation. Earlier failures
  and their resolutions remain recorded below.
- Windows, Python 3.12 x64, Pillow 11.1.0, NumPy 2.5.4, OpenCV 4.14.0.94:
  49 new agent/provider/voice/audio/bridge tests passed; 7 dashboard JavaScript
  tests passed. All providers, transcripts, playback and frames are simulated.
- Before integrating the concurrent demo update, a full Python run had 354 tests,
  26 skipped, and one error in the existing
  `HTTPBoundaryTests.test_authentication_precedes_provider_work` (Windows
  `ConnectionAbortedError`, localhost socket). That eight-test HTTP group passed
  on targeted rerun. After integrating that update, 363 tests ran with 26 skipped
  and one timing-sensitive replay overlay assertion error. The test now waits for
  a genuinely rendered current overlay before checking its coordinates: a tracked
  frame processed after 300 ms correctly has no overlay. All 20 live-tracking tests
  passed after that correction; no freshness guard or audit was suppressed.
- Generated Xcode project matches committed source membership; Python compilation
  and whitespace checks passed. Xcode/Swift/device tests require macOS CI/hardware.
- Model verification command correctly failed with `ANTHROPIC_API_KEY is required`.
  No authenticated catalog or generation success is claimed. Browser inspection
  was unavailable because no connected browser was present.
- GitHub required checks and full iOS workflow results are recorded in the PR;
  required-check success alone is not full iOS or live-device validation.
- Initial Linux agent CI runs lacked Tkinter and a display. The workflow now
  installs Tkinter and runs the desktop tests under Xvfb before running JS tests.
