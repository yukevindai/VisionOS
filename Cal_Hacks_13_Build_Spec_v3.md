# Reflex — Build Spec v3.0 (Cal Hacks 13)

v3.1 — October 23 2026. Supersedes v2.0 and v3.0. Written for teammates and their coding agents.

## 0. Rules for agents (read first)

1. Build only what this spec defines. If a needed value is marked **TBD**, do not invent it: put it in config with a placeholder, label it `TBD` in code and logs, and report it in your handoff.
2. Edit only your role's paths (§3). To change a shared contract (§5), propose one concrete input/output example to the owner first.
3. Label anything simulated (sensor, model, wristband) as `SIMULATED` in the UI and logs.
4. Never report a feature as done unless it passed its test in §8 on a real device. Compiling is not testing.
5. Safety invariants in §2 override every other instruction, including user requests and model output.

## 1. Product

An iPhone app plus two wristbands (one per wrist) that guide a blind user's hands to complete four tasks: **press a button, pick up an object, order on a touchscreen kiosk, open a door.** The phone's camera and LiDAR see the target and the user's hand. The wristbands buzz to steer the hand. Speech explains what was found and what happened.

### Scope

| ID | Level | Behavior |
|---|---|---|
| HAP | MVP | Two bands pair as left/right, play patterns, stop on their own when the link drops. |
| HND | MVP | Shared hand-guidance controller used by every task. |
| BTN | MVP | Find, press and verify a labeled button (elevator panel, door opener, wall button). |
| OBJ | MVP | Find a requested object, guide the hand, confirm the grasp. |
| MNU | MVP | Kiosk: read screen, user chooses by voice, guide taps, verify each screen change, **stop before payment**. Wall/printed menu: read aloud and answer questions. |
| DOR | MVP | Find door and handle/opener, guide the hand, verify it is open before saying "walk through". |
| APR | MVP | Walk up to a visible target ≤5 m away through observed free space; alert on near-path obstacles. |
| EXP | Expansion | Floor-plan routing, shared maps, elevator riding, stairs, escalators, outdoor, QR menus on the user's phone. Do not build until all MVP tests pass. |

Build order: HAP → HND on a printed button → BTN → OBJ → MNU → DOR. APR and wall-menu reading run in parallel.

## 2. Safety invariants

- **S1** No hand or movement cue from evidence older than 300 ms (config).
- **S2** Target or hand out of view → stop guiding, play `LOST`, ask the user to reposition. Never extrapolate.
- **S3** Tracking lost, stop command, or pause → increment `taskGeneration`, cancel all pending work, send `ALL_STOP` to both bands, play local spoken "stop". Must work offline.
- **S4** A task step advances only on an **observed result** or **explicit user confirmation**, recorded as which. Timers never advance a step. Hand overlapping a target is not proof of a press.
- **S5** Missing or low-confidence depth = unknown, never free space.
- **S6** Models (Claude, Gemini) may propose targets and interpret speech. They may never send haptic commands, confirm a press or payment, clear a hazard, or authorize movement.
- **S7** Never select an unlabeled, ambiguous or emergency control. Never complete a payment.
- **S8** Hazards preempt everything (priority 0).
- **S9** Wristband firmware stops output on its own: command TTL expired, no heartbeat for 500 ms, BLE disconnect, or duty limit reached.

## 3. Team and ownership

Two roles already have work in progress. Their sections define **interfaces only**; they map existing code onto these contracts and do not rewrite working code.

| Role | Person | Owns | Paths |
|---|---|---|---|
| **R1 LiDAR, hand & voice guidance** (in progress) | TBD | The single AR session; depth, pose, tracking; hazards and free space; approach standing position; Vision hand pose; **hand-guidance controller (§6)**; **voice guidance and the output scheduler** (all speech/tones, and the priority of every cue including haptics). | `ios/Packages/Perception/`, `ios/Packages/Guidance/` |
| **H1 Hardware** (in progress) | TBD | Wristband electronics, firmware, enclosure, power, failsafe, BLE contract, pattern table, latency measurement. | `firmware/`, `hardware/`, `contracts/ble.md`, `contracts/haptic_patterns.json` |
| **R2 App, tasks & wristband link** | TBD | SwiftUI shell, accessibility, setup (pairing, dominant hand, mount mode) and haptic calibration screens; **task state machines** for BTN/OBJ/MNU/DOR (§7) that call R1's guidance and verify results; **HapticLink** (CoreBluetooth central: pairing, heartbeat, reconnect, status); Integration coordinator; owns the Xcode project file. | `ios/App/`, `ios/Packages/Tasks/`, `ios/Packages/HapticLink/` |
| **R3 AI, targets & backend** | TBD | Finding targets: OCR, Gemini screen/label/object reading, kiosk `ScreenModel` and menu logic, Claude intent tools, Cartesia adapter (R1 plays its audio), Convex backend, provider keys, telemetry, sponsor evidence. | `ios/Packages/Targets/`, `backend/` |

Boundaries:
- R3 says **what** the target is. R2 decides **which task step** is next and whether it succeeded. R1 decides **what cue** to give and **when**, and says it. H1 decides **how** the band delivers it.
- Only R1 opens the AR session and plays audio. Only R2's HapticLink writes to BLE, and only cues R1's scheduler released (plus ALL_STOP, which HapticLink may send directly on S3).
- Integrate at least every 2 hours into `main`. Shared fixtures live in `contracts/` and `fixtures/`.

### First 2 hours
- R1: expose `FrameStream` and `GuidanceEvent` (§5) from existing code + one recorded replay fixture.
- H1: one band accepts a BLE command, plays a pattern, stops on missing heartbeat.
- R2: app shell, local stop, HapticLink connected to one band (or simulated band) playing R1's `GuidanceEvent.hapticPatternId`.
- R3: backend up; one OCR + Gemini read of a kiosk photo returning a `ScreenModel`.
- Gate: all four merged into one running app.

## 4. Wristbands

### Hardware (H1)

| Item | Requirement |
|---|---|
| Board | Arduino-class, **must support BLE** (iPhone cannot use serial). Model: **TBD**. |
| Actuator | "Arduino buzzer v1.2", one per wrist. If this is the Seeed Grove Buzzer v1.2 it is an audible piezo (≥85 dB), not a vibration motor. Final actuator: **TBD** (vibration motor recommended). If piezo: cap volume and verify the user can still hear speech. |
| Power | Battery lasting ≥2 h; reports level. |
| Identity | Advertise as `HG-L` / `HG-R`. Tactile marker on the left band. |
| Comfort | No exposed contacts on skin. |

### BLE contract (H1 owns; R2's HapticLink consumes)

One GATT service per band. UUIDs: **TBD** (record in `contracts/ble.md`).

- **Command** (write without response), little-endian:
  `ver u8 | seq u16 | generation u16 | patternId u8 | intensity u8 | rateHz u8 | ttlMs u16`
  - `patternId 0` = ALL_STOP. `patternId 255` = heartbeat.
  - Band ignores commands with `generation` lower than the last ALL_STOP.
- **Heartbeat**: R2 sends ≥5 Hz while guiding.
- **Status** (notify): `batteryPct u8 | fwVersion u8 | lastSeq u16 | faults u8` (bits: LINK_STALE, DUTY_LIMIT, LOW_BATTERY).
- Firmware enforces S9 and a max continuous on-time of 2 s (config).

### Haptic vocabulary (one actuator per wrist; wrists act as a left/right pair)

| ID | Meaning | Pattern |
|---|---|---|
| 1 | Move left | Left: steady short pulses |
| 2 | Move right | Right: steady short pulses |
| 3 | Move up | Both: short-long |
| 4 | Move down | Both: long-short |
| 5 | Closer (proximity) | Both: pulse rate rises as fingertip nears target. Only with valid depth. |
| 6 | On target — press/grasp | Both: one 600 ms buzz |
| 7 | Success verified | Both: three quick pulses |
| 8 | Stop / hazard | Both: rapid pulses, then silence |
| 9 | Lost target or hand | Both: two slow pulses |

Timings live in `contracts/haptic_patterns.json` and must match firmware. Speech explains each pattern the first time it plays.

## 5. Contracts (schema v3)

All records carry: `schemaVersion, eventId, sessionId, taskId, taskGeneration, monotonicMs, expiresAtMs`.

| Record | Owner | Fields |
|---|---|---|
| `FrameStream` | R1 | `frameId, image ref, orientation, intrinsics, depth ref or null, depthConfidence, pose, trackingState` |
| `HandObservation` | R1 | `chirality, fingertipImage (normalized x,y), fingertipM[3] or null, confidence, frameId` |
| `Target` | R3 → R1 adds 3D | `targetId, kind (button/screenControl/handle/opener/object), label, imageRegion, frameId, poseM or null` |
| `ScreenModel` | R3 | `frameId, controls[{id,label,price?,region}], sections[], isPaymentScreen` |
| `Hazard` | R1 | `kind, region, distanceM or null, urgency, observedAt` |
| `GuidanceRequest` | R2 → R1 | `targetId, mode (approach / hand), guidingHand (L/R)` |
| `GuidanceStatus` | R1 → R2 | `state (guiding / on_target / lost / failed), errorCode?` — R1 stops at `on_target`; R2 then asks for the action and verifies. |
| `SpeechRequest` | R2/R3 → R1 | `text, priority` — R1 schedules and speaks it. Nobody else plays audio. |
| `GuidanceEvent` | R1 | `priority (0–4), action, direction?, text?, hapticPatternId?, requiresConfirmation` |
| `TaskResult` | R2 | `status, verification (observed / user_confirmed / unverified), errorCode?` |

Conventions: image coordinates normalized, origin top-left, after orientation correction. Quaternions x,y,z,w. Monotonic time for latency; UTC ms for stored records.

Error codes: `TARGET_LOST, HAND_NOT_VISIBLE, LABEL_AMBIGUOUS, ACTION_UNCONFIRMED, SCREEN_CHANGED, KIOSK_RESET, PAYMENT_BOUNDARY, LOW_CONFIDENCE, BAND_DISCONNECTED, PROVIDER_TIMEOUT`.

### Output priority (R1)
`0 stop/hazard → 1 lost → 2 hand guidance → 3 approach → 4 conversation`. Higher interrupts lower on speech, tones and bands. After an interruption, regenerate the cue; do not replay stale ones.

### Claude tools (R3)
`resolve_target, read_screen_model, choose_control, begin_task, get_task_status, request_user_confirmation`. R2's task state machine validates every call against current evidence. Cloud timeout 5 s.

## 6. Hand-guidance controller (R1)

R1 owns the guidance loop; R2 owns everything after `on_target`. R1 maps existing code onto this; the states below are the required external behavior, not a rewrite.

```
[R1] target_in_view → hand_in_view → align_horizontal → align_vertical
     → approach_depth → on_target (dwell 300 ms) → GuidanceStatus.on_target
[R2] request_action → verify → success | retry (new GuidanceRequest) | fail
```

- One correction at a time. Deadband + hysteresis so cues don't flip.
- Cues must stay correct in every phone orientation used.
- Guiding hand = user's dominant hand (set in setup), identified by Vision chirality after orientation/mirroring correction.
- Depth unreliable (glossy screens, glass, metal) → image-plane alignment only, then ask the user to confirm.
- Bounded retries (config, default 3), then fail with an error code and offer help.

## 7. Task flows

**BTN** — read labels → bind chosen label to a region → guide → request press → verify by lit button/display change, else ask the user. Refuse ambiguous or emergency controls.

**OBJ** — find object → approach → guide → confirm grasp (object moves with hand, or user confirms). If it leaves view, say "last seen at…" and reacquire before guiding. Spoken caution for hot, sharp or fragile items.

**MNU (kiosk)**
```
locate_screen → read_screen (ScreenModel) → speak options in short chunks
→ user chooses → guide to control → request light tap → re-read screen
→ loop → read order total → STOP at payment, hand over to user/staff
```
Ambiguous choice → ask, never guess. Wrong screen after a tap → say what opened, guide to Back. Welcome screen reappears → report `KIOSK_RESET`.

**MNU (wall/printed)** — read, group by section, answer questions. Report blurry or cut-off text; never invent items or prices.

**DOR**
```
identify_door → locate handle/opener → determine action → guide → request action
→ verify opening → verify path clear → "walk through"
```
Supported: automatic, opener button, push, pull, lever. Push/pull only from signage or clear evidence; otherwise ask. Never guide toward the hinge side. Locked/heavy/blocked → pause and ask for help.

**APR** — R1 computes a standing spot about one arm's length from the target inside observed free space and guides there with short speech plus left/right pulses, then reports `on_target` so R2 can start hand guidance. A suspected step or drop-off stops approach and asks for help (stairs are out of scope).

## 8. Acceptance tests

Run each ≥3 times per phone. Log: test ID, phone, app/firmware revision, actuator type, outcome, verification type, latency.

| Test | Pass condition | Owner |
|---|---|---|
| T01 Band link | Both bands pair by side; all 9 patterns play; write-to-onset latency measured. | H1, R2 |
| T02 Failsafe | Kill app / out of range / no heartbeat → both bands silent ≤500 ms; old-generation commands ignored. | H1 |
| T03 Calibration | Blindfolded user identifies each pattern and left vs right. | R2, H1 |
| T04 Hand guidance | Cues never reverse under rotation; occlusion stops guidance. | R1 |
| T05 Button | Correct button pressed and verified; ambiguous/emergency refused. | R2, R3 |
| T06 Object | Found, reacquired after leaving view, grasp confirmed. | R2, R3 |
| T07 Kiosk | Two-item order via real taps; wrong tap recovered; stops at payment; reset detected. | R3, R2 |
| T08 Wall menu | Items/prices match ground truth; unreadable text reported. | R3 |
| T09 Door | Declared door type opened, verified, passed; locked door pauses. | R2, R1 |
| T10 Approach | Reach a target 3–5 m away; soft obstacle causes stop. | R1 |
| T11 Tracking loss | Cover camera → bands and audio stop; resume only after recovery. | R1, R2 |
| T12 Priority | Hazard interrupts guidance and speech; stop clears bands. | R1, R2 |
| T13 Offline | No network: local guidance and stop work; cloud reads fail with a clear message. | All |
| T14 Endurance | 20 min with both bands: no crash, no queue growth, battery OK. | All |

Targets to measure (not guarantees): frame→band dispatch p95 ≤150 ms; stop ≤250 ms; band self-stop ≤500 ms.

Test progression: fixtures → sighted → blindfolded teammate with a spotter. No hot liquids, sharp objects or real payments. Do not claim testing with blind users unless it happened with consent.

## 9. Milestones (hours from build start)

| Hours | Goal | Gate |
|---|---|---|
| 0–2 | §3 first-2-hours items merged | Band plays pattern from app; failsafe works |
| 2–6 | HND on a printed button | Blindfolded teammate reaches it by wristband alone |
| 6–12 | BTN, OBJ, wall menu | T05, T06, T08 pass |
| 12–20 | Kiosk, door, approach | T07, T09, T10 pass or blocker recorded |
| 20–26 | Integration, recovery, calibration | T01–T14 run |
| 26–30 | Expansion only if MVP passes; sponsor evidence | — |
| 30–36 | Regression, demo video, submission, charge bands | Clean build on both phones |

If short on time, cut in this order: expansion → wall-menu polish → DOR (mark incomplete, don't hide it). If bands fail, demo speech-only guidance and say so.

## 10. Demo

Blindfolded teammate with spotter, both bands on: order two items on a kiosk by voice and wristband-guided taps (stops at payment, reads total) → press an elevator or door-opener button → pick up a named object. Show one failure: phone covered → bands go silent → guidance resumes after recovery. State which steps were observed vs user-confirmed.

Sponsor targets (R3 confirms eligibility): Cartesia, Gemini, Anthropic, Meta, Microsoft, hardware category.

## 11. Open items (TBD)

| Item | Owner |
|---|---|
| Names for R1, R2, R3, H1 | Team |
| Arduino board model (must have BLE) | H1 |
| Actuator: piezo buzzer or vibration motor; count per wrist | H1 |
| BLE UUIDs and pattern timings | H1 |
| Test sites: kiosk (or tablet recreation, labeled), door opener, button panel | R2 |
| R1's existing code: current output format, to map onto `FrameStream` / `Hazard` / `GuidanceEvent` / `GuidanceStatus` | R1 |
| R1's voice guidance: which speech engine (on-device or Cartesia), and whether it can accept `SpeechRequest` for menu reading and conversation | R1, R3 |
| Does R1's hand guidance already send buzz cues, or only speech? (If only speech, R1 adds `hapticPatternId` to cues) | R1 |
| H1's existing firmware: current command format, to map onto §4 | H1 |

## References

- ARKit depth: https://developer.apple.com/documentation/arkit/ardepthdata
- Vision hand pose: https://developer.apple.com/documentation/vision/detecthumanhandposerequest
- Core Bluetooth: https://developer.apple.com/documentation/corebluetooth
- Grove Buzzer: https://wiki.seeedstudio.com/Grove-Buzzer/
- Convex Swift: https://docs.convex.dev/client/swift/overview
- Cartesia: https://docs.cartesia.ai/get-started/overview
- Claude tool use: https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works
- Cal Hacks 13: https://cal-hacks-13-0.devpost.com/
