# HYBRID default and experimental discovery — 2026-10-24

Normal launches now select HYBRID. ALL OBJECTS remains available only through an explicit
`-AllObjects` / `-Detection allobjects` launcher argument or `--detection allobjects` Python argument.
The previous launcher and Python default was GEMINI, not ALL OBJECTS; existing explicit HYBRID
already gated discovery correctly. This change makes that mode the preferred default.

## Runtime boundary

`TrackingWorker` constructs `AllObjects` only when `detection == 'allobjects'`.
HYBRID has `all_objects = None`, so it cannot offer frames to discovery, draw inventory,
choose inventory targets or invoke periodic inventory verification. No `r3-discovery`,
`r3-inventory-verify` or `r3-yoloe-pf` worker is started in HYBRID.

HYBRID retains the existing request flow: query-specific YOLO vocabulary and a Gemini check
for the request, with the existing verification/rejection behavior. Selective here means
request-driven; the current algorithm still starts one Gemini request alongside each HYBRID
identification attempt. Existing cooldown/re-identification limits apply. It is not a promise
that every generic request skips Gemini. No provider settings or algorithms were changed.

The local YOLO model loads once and performs its existing blank-image warm-up. It remains
resident in memory for requests; this is not repeated background camera discovery. Tracking,
MediaPipe, depth, audio and menu/kiosk implementations are unchanged.

## Launch

From `VisionOS/backend`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-live-tracking.ps1
```

PathOn defaults remain `192.168.137.223:8888`. Restart an existing ALL OBJECTS process for the
default change to take effect. For an explicit comparison, append `-AllObjects`; omit it
for normal HYBRID. Preserve your usual audio, target kind and request arguments in both runs.

## Validation

- Three new focused default/idle tests: passed. The real idle worker processes simulated frames
  with zero detector calls, zero cloud calls and no experimental discovery threads.
- Five existing HYBRID worker/session tests: passed, including confirmation, disagreement and
  mismatched-frame rejection.
- Twenty-two existing experimental ALL OBJECTS tests: passed.
- PowerShell launcher syntax: passed.
- No full regression suite, live-phone acceptance test or physical LiDAR test was run.

## Reproduce the bounded offline comparison

From `backend`, using the existing DeskGuide Python environment:

```powershell
& ..\..\DeskGuide-Laptop\.venv\Scripts\python.exe -m r3_vision.discovery_ab
```

This diagnostic reuses the recorded PathOn photograph and existing replay harness. Camera motion
and Gemini responses are **SIMULATED** (fixed two-second provider delay); local YOLO, target CV
and MediaPipe are real. No network/provider calls, GUI rendering, audio or depth are measured.
It loads cached models before sampling, uses four Torch threads and one OpenCV thread in both
modes, measures three-second idle/locked windows, and issues one Red Bull request per mode.
CPU percent is process CPU time divided by elapsed time (100% = one logical CPU).
Frame age is local receive-to-log age, not sensor exposure or network latency.
Results are written under `backend/.runtime/discovery-ab/<run>/report.json` (ignored).

Final settled run: `1792886644326932800`, twelve-second settling period per mode:

| Metric | ALL OBJECTS | HYBRID |
|---|---:|---:|
| Idle FPS | 13.74 | 17.29 |
| Locked FPS | 11.89 | 16.08 |
| Idle CPU (100% = one logical CPU) | 306.2% | 115.0% |
| Locked CPU | 306.1% | 163.6% |
| Idle frame age median / p95 | 78 / 140 ms | 47 / 63 ms |
| Locked frame age median / p95 | 94 / 156 ms | 62 / 87.25 ms |
| Request → verified lock | 1.016 s | 2.422 s |
| Total detector calls | 8 | 1 (the request) |
| Simulated Gemini calls | 1 background inventory verification | 1 user request |

ALL OBJECTS had already paid for background verification before the request, so its cached
inventory locked faster in this example. HYBRID improved this run's FPS/CPU/frame age while
making zero idle YOLO calls and starting no inventory threads. The ALL OBJECTS verification
started during warm-up and overlapped the idle sample; `idleGeminiCalls` counts only starts
inside that window, so its empty list does not mean no background verification occurred.
This is evidence for the resource tradeoff, not a guarantee of faster recognition or live FPS.

This is one short sequential ALL OBJECTS → HYBRID comparison, not a statistically controlled
benchmark. Actual phone request-to-lock includes real provider latency and camera motion.
An earlier sandbox run was stopped and excluded because YOLO never finished loading during
the measurement window; it cannot support a discovery performance comparison.

Live acceptance remains: use the same scene/request, wait for initialization, compare HYBRID
and explicit ALL OBJECTS with the same hands/audio/depth settings, and inspect the session
FPS, `frameAgeMs`, `processMs` and selection logs. HYBRID session records must have
`allObjects: null`; no periodic inventory verification should consume its request budget.
