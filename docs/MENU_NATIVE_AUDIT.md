# Native iPhone feature audit for the Menu Reader work

Audit date: 2026-10-24. Read-only inspection of native code and checked-in validation records.
No native build, test suite, iPhone installation, cloud deployment or live device test was run
for this audit. Only this new document was written. Claude's backend modules were not edited.

## Main finding

The native app has more than interfaces: it has ARKit/Vision/Core ML object assistance,
on-device speech recognition, scheduled speech output, a separate authenticated Gemini
snapshot reader, and tested menu/kiosk state machines. However, those pieces do **not** add
up to a native full-menu assistant or a live elevator/kiosk hand-guidance flow.

The most important wiring fact is [AppDependencies.nativeIntegration](../ios/App/Composition/AppDependencies.swift#L126):
normal assistance supplies `OnDeviceTargetEvidenceClient(clock:)` with no fallback. That
client's [initializer defaults fallback to nil](../ios/App/Targets/OnDeviceTargetEvidenceClient.swift#L78),
and its [delegated operation throws unsupportedTask without fallback](../ios/App/Targets/OnDeviceTargetEvidenceClient.swift#L182).
Thus the existence of `R3TargetEvidenceClient.readMenu` or Tasks' kiosk flow does not mean the
normal Call route can invoke Gemini to execute them.

## Classification by requested capability

| Feature | Classification | Actual native code and tests | What remains unproven/missing |
| --- | --- | --- | --- |
| Identify requested elevator floor, including spoken number words in selector input | **Working only on saved images/tests** for canonical selection; **implemented but unverified** as the snapshot-reader UI | [TargetSelector.select](../ios/Packages/Targets/Sources/Targets/TargetSelector.swift#L94); [testFindFloor5](../ios/Packages/Targets/Tests/TargetsTests/TargetSelectorTests.swift#L52). [TargetReaderModel.captureAndRead](../ios/App/Targets/TargetReaderModel.swift#L179) runs that actual selector on an authenticated model reply. | Snapshot UI input is a text field; normal native voice routing only starts supported object tasks. No physical iPhone elevator pass is recorded. |
| Read elevator button labels | **Implemented but unverified** in the native snapshot path | [ConvexNativeMediaBackend.readScreen](../ios/App/Calling/ConvexNativeMediaBackend.swift#L112) calls `vision:readScreen`; [TargetReaderModel](../ios/App/Targets/TargetReaderModel.swift#L204) validates the result and selects a control. | It retains/displays a selected result, not a full spoken label inventory. Hosted service configuration and real iPhone network capture remain unverified. Recognition depends on cloud reading, not local OCR. |
| Duplicate labels, number identity, unreadability and bad coordinates | **Working only on saved images/tests** | [TargetSelector tests](../ios/Packages/Targets/Tests/TargetsTests/TargetSelectorTests.swift#L78) cover numeric identity, duplicate/multiple candidates, invalid regions and low confidence. `testUnknownLegibilityIsNeverTreatedAsClear` is at line 230. | No glare/real-panel accuracy evaluation is established by those fixtures. Do not translate successful label matching into proven perception accuracy. |
| Distinguish button/menu-item types; reject emergency/payment guidance | **Partially implemented** | [R3TargetMapping.target](../ios/App/Integration/R3TargetMapping.swift#L95) requires actual confidence >=0.6, clear label and explicit ordinary safety; payment screens cannot become physical targets. [testRecognitionAcceptsMissingConfidenceButGuidanceDoesNot](../ios/Tests/ReflexAppTests/R3TargetEvidenceTests.swift#L41). | The provider does not supply trustworthy physical confidence/safety approval. Normal assistance has no cloud fallback. A recognition result alone is not permission to approach a button. |
| Identify a kiosk item such as a burger | **Working only on saved images/tests** for matching; **implemented but unverified** via separate snapshot reader | [testFindTheBigMacPrefersExactLabelOverLongerItem](../ios/Packages/Targets/Tests/TargetsTests/TargetSelectorTests.swift#L62); same `TargetReaderModel`/Convex path can receive a kiosk `ScreenModel`. | No real kiosk order completed on an iPhone is documented. Text recognition and target selection must be separated from actual hand/touch guidance. |
| Understand/read an entire kiosk screen | **Partially implemented** | [ScreenModel](../ios/Packages/Targets/Sources/Targets/ScreenModel.swift#L147) stores all controls, sections, payment state and partial availability; [R3TargetMapping.taskScreen](../ios/App/Integration/R3TargetMapping.swift#L61) maps supported controls. | Native snapshot UI stores `selection` and image, not the complete model as conversational memory. A control inventory does not encode arbitrary screen meaning, item descriptions or verified relationships. Physical task mapping rejects provider controls without confidence/safety. |
| Multi-step kiosk ordering, re-read after tap, payment stop | **Working only on saved images/tests** | [KioskAndMenuTests](../ios/Packages/Tasks/Tests/TasksTests/KioskAndMenuTests.swift#L38) cover payment boundary, coordinate invalidation/re-read, two-item loops, wrong-screen recovery, reset and ambiguous labels. [TaskEngine.menuCommand](../ios/Packages/Tasks/Sources/Tasks/TaskEngine.swift#L635) handles choices/options. | Native production composition does not supply a kiosk evidence provider or observed press verifier. [R3TargetEvidenceClient.verify](../ios/App/Integration/R3TargetEvidenceClient.swift#L67) returns unavailable. No calibrated kiosk/elevator device pass. |
| Read a printed restaurant menu | **Partially implemented** scaffold; full extraction **missing** | [R3TargetMapping.menu](../ios/App/Integration/R3TargetMapping.swift#L75) groups clear control labels/prices into `MenuContent`. [R3TargetEvidenceClient.readMenu](../ios/App/Integration/R3TargetEvidenceClient.swift#L58) calls it. | This depends on a control-oriented model, not dedicated printed-text OCR. No `VNRecognizeTextRequest`, VisionKit DataScanner or equivalent native menu text extractor was found in the inspected app/Targets code. Production fallback is absent. |
| Structured dish names, descriptions, exact prices, categories, size options and allergens | **Partially implemented** labels/prices/section names; rich menu schema **missing** | [InteractiveElement](../ios/Packages/Targets/Sources/Targets/ScreenModel.swift#L123) has label, optional string price/section, legibility and region. [MenuSection/MenuContent](../ios/Packages/Tasks/Sources/Tasks/TaskTypes.swift#L161) have section titles and string items plus blurry/cut-off flags. | No dish description, option/size relation, currency/decimal price model, allergen evidence, per-field OCR provenance, coverage ledger or uncertainty graph in these native types. |
| Ask natural-language questions about the captured menu | **Missing** live grounded answer implementation; state-machine **partially implemented** | [TaskEngine.menuCommand(.question)](../ios/Packages/Tasks/Sources/Tasks/TaskEngine.swift#L652) emits `answerMenuQuestion`. [R3TargetEvidenceClient.answerMenu](../ios/App/Integration/R3TargetEvidenceClient.swift#L63) explicitly throws unsupportedTask. [Fixture menu test](../ios/Packages/Tasks/Tests/TasksTests/KioskAndMenuTests.swift#L181) supplies simulated answers. | No functioning native Q&A provider, grounded arithmetic, ingredient/allergen refusal policy or session menu store feeding such a provider. |
| Microphone + speech-to-text | **Implemented but unverified** on actual hardware | [NativeAudioCoordinator.startInput](../ios/App/Audio/NativeAudioCoordinator.swift#L48) opens one AVAudioEngine, enables voice processing and uses `SFSpeechRecognizer` with on-device recognition required and partial results. | It requires authorized mic/speech access and available on-device en-US recognition. Continuous speech failure/final result currently stops input and invokes failure/recovery; actual microphone/echo/long-session acceptance is absent. |
| Text-to-speech | **Implemented but unverified** on actual hardware | [NativeAudioCoordinator.speak](../ios/App/Audio/NativeAudioCoordinator.swift#L201) schedules local AVSpeechSynthesizer or an optional priority-4 provider. [beginSpeech](../ios/App/Audio/NativeAudioCoordinator.swift#L222) speaks locally; [ConvexSpeechClient](../ios/App/Audio/ConvexSpeechClient.swift) and [ConversationalSpeechRequest tests](../ios/Tests/ReflexAppTests/ConversationalSpeechRequestTests.swift) implement bounded remote-TTS fallback/cancellation. | Snapshot reader currently sets UI text and does not send its result into this speech scheduler. VoiceOver-readable text is not the same as automatic menu narration. Live remote synthesis/deployment and real speaker behavior are unverified. |
| Interrupt the assistant | **Partially implemented** | [receiveTranscript](../ios/App/Audio/NativeAudioCoordinator.swift#L165) checks spoken Stop before echo suppression; [localStop/cancelOutput](../ios/App/Audio/NativeAudioCoordinator.swift#L286) immediately cancel speech/player work. [testStopRecognizedWithinUtteranceWithoutEchoSuppressionRule](../ios/Tests/ReflexAppTests/NativeAudioPolicyTests.swift#L62). | General user speech is discarded while synthesizer/player is speaking or in echo tail (lines 179–182). This is not full arbitrary-question barge-in. Pause/resume commands do not maintain a full-menu reading cursor. Hardware echo reliability remains unverified. |
| Remember a captured menu after moving the camera away; read everything with pause/resume/skip | **Partially implemented** in fixture task state; full native experience **missing** | `TaskEngine` stores `state.menu` and [speakMenu](../ios/Packages/Tasks/Sources/Tasks/TaskEngine.swift#L659) reads one section; `.more`, `.back`, `.repeatOptions` operate on section index. | The actual native voice path has no menu reader/answer provider, no multi-page merge, no evidence coverage and no speech cursor across utterances. Snapshot reader stops capture, but stores only a selection/image, not a usable full-menu session. |
| Help communicate confirmed choice to cashier | **Missing** as a native order-draft feature | Existing TTS and explicit confirmation machinery can be reused; no cashier/order-draft implementation was found. | Add a reviewable draft grounded in selected scanned items, then an explicit user action before speaking it. Existing kiosk task acceptance is not purchase authorization. |

No feature in this table qualifies as **Working and live-tested on a physical iPhone** based on
the inspected checked-in evidence. This does not deny the user's successful desktop Gemini
and PathOn tests; it distinguishes their platform and scope from native iPhone acceptance.

## Actual routes and boundaries

### Normal Call / native physical assistance

`AppDependencies.nativeIntegration` -> `NativeAudioCoordinator` +
`NativePerceptionGuidanceClient` + `OnDeviceTargetEvidenceClient`.
The [local utterance router](../ios/App/Session/SessionCoordinator.swift#L854) accepts local
commands/confirmations and starts object tasks only for cup/can, bottle or bowl words. For
other phrases it explains the local object limitation. It does not turn “read the menu” or
“find floor five” into a full production menu/elevator task.

The simulator-only `submitFixtureText` near line 817 has a broader menu/kiosk/button parser;
its own guard requires simulation. Do not use those UI fixtures as evidence that production
speech routes support the same tasks.

The production conversation adapter is the same local audio coordinator, not Gemini Live:
[NativeAudioCoordinator.sendLatestFrame](../ios/App/Audio/NativeAudioCoordinator.swift#L140)
is an empty method. Recognizing a transcript and retaining task state is not a grounded
natural-language assistant or cloud multimodal conversation session.

### Separate Read an image tool

[HomeView](../ios/App/Views/HomeView.swift#L233) presents `TargetReaderView`. The reader owns a
separate/exclusive camera snapshot route, Auth0 identity and Convex `vision:readScreen`.
`captureAndRead` checks a fresh camera frame, corrects/encodes it, stops the camera, awaits
the service, validates the original frame metadata and runs the canonical selector. It
then explicitly says “image recognition only; no hand or movement guidance is active”.
Its [query TextField](../ios/App/Targets/TargetReaderView.swift#L87) and capture button prove a
native UI exists, but do not establish spoken interaction, hosted readiness or actual device use.

### Reusable Tasks/Targets work

Keep canonical `TargetSelector`, ambiguity handling, schema conventions, frame validation,
payment boundary, kiosk screen-change invalidation and Tasks menu section browsing.
Extend with a dedicated menu document schema and provider; do not replace these with a new
unrelated action engine. An OCR/menu document should remain conversational historical evidence,
not receive a fresh 300ms physical-guidance timestamp just because the user asks about it.

## What test records actually establish

- [Native R1/R3 validation](../ios/Validation/2026-10-24-native-r1-r3.md#L3) explicitly says
  **not installed/tested on a physical iPhone**. It records 136 simulator app tests, Tasks 92,
  Guidance 11 and Targets 20 at that integration stage, plus signed/unsigned builds. Device
  installation failed with CoreDevice/DDI incompatibility, not a successful phone pass.
- Its [provider check](../ios/Validation/2026-10-24-native-r1-r3.md#L35) records a genuine Gemini
  3.8 low-effort call on **synthetic artwork**, selecting “2” in 2.34 seconds. This is a provider
  connectivity/format result, not OCR accuracy or a physical panel/menu/iPhone-network benchmark.
- [R2 speech follow-up](../ios/Validation/2026-10-24-r2-cartesia-ui.md) records 161 app tests in
  its final native slice and simulated provider tests. It explicitly excludes a live Cartesia/
  Convex call, physical microphone/echo cancellation, real VoiceOver and device acceptance.
  It also records unresolved accessibility audit issues; do not report an entirely green UI.
- [Role UI validation](../ios/Validation/2026-10-24-role-ui.md) records simulator Call and reader
  entry/exit flows and explicitly states elevator-button guidance remains future integration.
- [TargetReaderTests](../ios/Tests/ReflexAppTests/TargetReaderTests.swift#L84) verify the canonical
  selector/capture clock, close/late-response rejection, stale/simulated-reply rejection and
  orientation encoding using injected readers/cameras. These are meaningful adapter tests,
  not a live hosted-service or physical-camera exercise.
- This audit did not rerun these suites; counts above are attributed to their checked-in records.

## Ranked native gaps for the restaurant demo

1. Dedicated native OCR and a grounded menu document with exact per-field text/price provenance,
   coverage/incompleteness, uncertainty, and high-resolution multi-frame capture/merge.
2. A working menu evidence adapter wired into production composition, plus a grounded answer
   implementation; `answerMenu` must stop throwing unsupportedTask.
3. Menu conversation routing and session memory in production. Reuse local STT/TTS and Tasks,
   but distinguish fixture menu commands from actual supported speech intents.
4. Streaming/chunked menu reading with an explicit reading cursor and stop/pause/resume/skip;
   add deliberate arbitrary-question interruption instead of dropping speech while talking.
5. Real iPhone acceptance: camera OCR, orientation, small text/glare, on-device speech availability,
   echo behavior, VoiceOver and service setup/network boundaries. Simulator builds cannot replace it.
6. Keep kiosk/elevator physical guidance separate until calibrated current depth/target identity,
   observed screen/tap changes, and robust stop/freshness behavior pass their device tests.

No installation or large-model research was performed by this native-audit subtask.
