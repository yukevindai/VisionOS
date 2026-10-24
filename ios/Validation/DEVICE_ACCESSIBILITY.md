# iPhone accessibility checks

Status: **not run on a physical iPhone**. The registered phone was unavailable during the October 23, 2026 session. Simulator accessibility audits and permission fixtures are recorded separately; they do not establish VoiceOver usability, actual iOS permission behavior, or hardware readiness.

Record the phone/iOS, app commit, appearance, accessibility settings, integration mode, tester, and outcome for each run. Use a signed Debug build with explicit simulation for checks that do not need teammate integrations. Simulation accepts typed fixture requests and does not listen to the microphone.

| Check | Steps and required result |
|---|---|
| VoiceOver navigation | Enable VoiceOver in iOS Settings. Open the app and navigate to Start, Settings, guiding hand, phone position, session status, Pause/Resume, and End. Labels must explain the action and state. Decorative camera imagery must not interrupt traversal. |
| Stable controls | With VoiceOver active, start explicit simulation, pause, resume, and end. Confirm changing captions never steal focus. Start should be reachable on home; Pause/End should remain reachable during a session. |
| Largest text | Select the largest accessibility text size. Repeat the flow in the app's supported portrait orientation. Scroll explanatory text, checking for truncation or overlap. Critical controls must remain reachable without reducing text size. |
| Settings safety | During active assistance, open Settings and push Wristbands. Pause must remain reachable without navigating back. Activate it, close Settings, and verify assistance stays paused until explicit Resume. |
| Setup and calibration | At largest text, read instructions, choose hand/mount, and reach Cancel/Done. Start explicit simulated calibration; test a wrong answer, Repeat, all eleven responses, Stop while scrolled, and Back/Done cancellation. Stop must remain visible throughout active calibration. Repeat with real patterns only after H1/R1 integration. |
| Typed simulation and keyboard | At largest text, send a simulated button request. The keyboard must dismiss, confirmation must be reachable by scrolling, and Pause/End must remain readable and operable. This checks typed fixture input, not live microphone accessibility. |
| Appearance | Repeat home, setup, and session in light/dark appearance and Increase Contrast. Read statuses and recovery text; color alone must not convey readiness or failure. |
| Reduce Motion | Enable Reduce Motion, navigate between home/setup/settings/session, and verify controls remain understandable and available without animation. |
| Real permissions | Use a live-configured build and actual iOS permission states. Deny camera, deny microphone, then grant access through Settings. Each denial must name the affected resource; returning from Settings must refresh readiness. Device restrictions must be explained rather than showing an ineffective Allow button. No real guidance should start without required permissions. |
| Background and lock | During a session, background or lock the phone. Assistance must stop. Returning to the app must not automatically resume guidance. |
| Live audio coexistence | **Requires R1/R3.** Navigate with VoiceOver while R1 speaks, interrupt the assistant, mute/unmute, issue local spoken Stop offline, then use Pause/End. Verify speech cancellation and that live guidance cannot resume while muted. |
| Live bands | **Requires H1 and R1.** Confirm each side and all nine meanings through scheduled calibration. Stop at each step; disconnect a band during calibration and guidance. No old cue may replay after reconnection. |

The XCTest permission scenarios deliberately replace protected-resource prompts with simulated authorization. They verify app flow, not iOS permission dialogs or a real return from Settings. The deterministic 20-minute stress test advances a test clock; it does not measure memory pressure, battery, thermal behavior, or 20 minutes of real sensor/audio/BLE operation.

Use the team specification's T01–T14 and R2-A–F record format for live acceptance. A physical endurance run still needs 20 minutes of wall-clock operation with the actual integrations and bands, with memory/queue growth, battery and Stop behavior observed separately.
