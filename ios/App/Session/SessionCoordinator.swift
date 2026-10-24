import Foundation
import UIKit
import Tasks

@MainActor
final class SessionCoordinator: ObservableObject {
    @Published private(set) var phase: SessionPhase = .idle
    @Published private(set) var activity: ActivityStatus = .ended
    @Published private(set) var capabilities = SessionCapabilities()
    @Published private(set) var message = "Ready when you are."
    @Published private(set) var caption = ""
    @Published private(set) var camera: CameraCapabilities?
    @Published private(set) var preview: UIImage?
    @Published private(set) var microphoneMuted = false
    @Published private(set) var taskState: TaskState?
    @Published private(set) var showSetup = false
    @Published private(set) var permissionState = PermissionState(camera: false, microphone: false)
    @Published private(set) var requestingPermissions = false
    @Published private(set) var calibrationIndex: Int?
    @Published private(set) var calibrationMessage = ""
    let simulated: Bool
    let preferences: UserPreferences
    let diagnostics: Diagnostics
    let bands: BandService
    let clock: any AppClock
    let perception: any PerceptionGuidanceClient
    let speech: any SpeechSchedulerClient
    let conversation: any ConversationClient
    let evidence: any TargetEvidenceClient
    let permissions: any PermissionClient
    private var engine: TaskEngine?
    private(set) var context: SessionContext?
    private var startWork: Task<Void, Never>?
    private var adapterStartInFlight = false
    private var requestWork: [RequestContext: Task<Void, Never>] = [:]
    private var deadline: ScheduledWork?
    private var frameWatchdog: ScheduledWork?
    private var frameWork: Task<Void, Never>?
    private var pendingFrame: FrameSnapshot?
    private var lastFrame: FrameSnapshot?
    private var seenConversationEvents: [String] = []
    private var supportsSpeechOnlyGuidance = false
    private var hazardActive = false
    private var startupEpoch = UUID()
    private var startupDeadline: ScheduledWork?
    private var recoveryWork: Task<Void, Never>?
    private var recoveryDeadline: ScheduledWork?
    private var recoveryRetry: ScheduledWork?
    private var recoveryAttempt = 0
    private var recoveryToken = UUID()
    private var sharedAudioInput: SharedAudioInput?
    private let configuration: SessionConfiguration
    private let framePolicy: FrameDeliveryPolicy?
    private var frameThrottle: ScheduledWork?
    private var lastFrameDispatchMs: Int64?
    private let generationSeed: () -> UInt64
    private let enforceSimulatedPermissions: Bool
    private var calibrationTaskID: String?
    var hasActiveTask: Bool {
        guard let state = taskState else { return false }
        return ![.idle, .completed, .failed, .cancelled, .paused].contains(state.phase)
    }
    var canResume: Bool { phase == .paused && calibrationIndex == nil && !microphoneMuted }
    var requiresPermissions: Bool { !simulated || enforceSimulatedPermissions }

    init(simulated: Bool, preferences: UserPreferences, diagnostics: Diagnostics, bands: BandService,
         clock: any AppClock, perception: any PerceptionGuidanceClient, speech: any SpeechSchedulerClient,
         conversation: any ConversationClient, evidence: any TargetEvidenceClient, permissions: any PermissionClient,
         enforceSimulatedPermissions: Bool = false,
         configuration: SessionConfiguration = .init(), framePolicy: FrameDeliveryPolicy? = nil,
         generationSeed: @escaping () -> UInt64 = { 0 }) {
        self.simulated = simulated; self.preferences = preferences; self.diagnostics = diagnostics; self.bands = bands
        self.clock = clock; self.perception = perception; self.speech = speech; self.conversation = conversation
        self.evidence = evidence; self.permissions = permissions
        self.configuration = configuration; self.framePolicy = framePolicy; self.generationSeed = generationSeed
        self.enforceSimulatedPermissions = enforceSimulatedPermissions
        permissionState = permissions.current()
        bindAdapters()
    }

    private func bindAdapters() {
        perception.onFrame = { [weak self] in self?.receiveFrame($0) }
        perception.onEvent = { [weak self] in self?.receivePerception($0) }
        perception.onLocalCommand = { [weak self] context, command in
            guard let self, self.context == context else { return }; self.command(command)
        }
        perception.onHapticRelease = { [weak self] release in
            guard let self, let context = self.context, release.context.sessionID == context.sessionID,
                  UInt64(release.context.generation) == context.generation,
                  release.context.taskID == (self.calibrationTaskID ?? self.engine?.state.context.taskID),
                  self.phase == .active || self.calibrationIndex != nil else { return }
            guard self.calibrationIndex != nil || self.preferences.calibratedPairSignature == self.bands.pairSignature,
                  self.bands.pairSignature != nil else { return }
            if release.kind == .movement {
                guard [.guiding, .approaching].contains(self.engine?.state.phase), !self.microphoneMuted,
                      !self.hazardActive else { return }
            }
            self.bands.dispatch(release)
        }
        speech.onCaption = { [weak self] context, text in
            guard let self, context == self.context else { return }; self.caption = text
        }
        conversation.onEvent = { [weak self] in self?.receiveConversation($0) }
        bands.onAvailabilityChanged = { [weak self] left, right in
            self?.capabilities.leftBand = left; self?.capabilities.rightBand = right
        }
        bands.onGenerationSynchronized = { [weak self] floor in
            guard let self, let old = self.context, old.generation < floor, self.phase.canEnd else { return }
            self.stop(.bandFailure)
            let generation = max(floor, self.context?.generation ?? floor)
            self.engine = TaskEngine(sessionID: old.sessionID, initialGeneration: generation,
                                     guidingHand: self.preferences.guidingHand == .left ? .left : .right)
            self.synchronizeContext(); self.taskState = self.engine?.state
            self.message = "Wristbands synchronized. Calibrate if needed, then choose Resume and request your task again."
        }
        bands.onSafetyPause = { [weak self] in
            guard let self, (self.phase == .active && self.hasActiveTask) || self.calibrationIndex != nil else { return }; self.stop(.bandFailure)
        }
    }

    func start() {
        guard phase == .idle || phase == .ended || phase == .failed else { return }
        guard !adapterStartInFlight else {
            message = "The previous connection attempt is still stopping. Try Start again shortly."
            return
        }
        guard preferences.setupComplete else { showSetup = true; return }
        permissionState = permissions.current()
        guard !requiresPermissions || permissionState.ready else { showSetup = true; message = permissionState.explanation; return }
        startupEpoch = UUID()
        let epoch = startupEpoch
        let id = UUID().uuidString
        engine = TaskEngine(sessionID: id, initialGeneration: max(generationSeed(), bands.minimumNextGeneration), guidingHand: preferences.guidingHand == .left ? .left : .right)
        context = .init(sessionID: id, generation: engine!.state.context.generation)
        guard let context else { return }
        phase = .preparing; activity = .preparing; message = "Preparing assistance."
        hazardActive = false; microphoneMuted = false; seenConversationEvents.removeAll(); caption = ""
        lastFrame = nil; pendingFrame = nil; preview = nil; lastFrameDispatchMs = nil
        capabilities = SessionCapabilities(); capabilities.leftBand = bands.leftStatus; capabilities.rightBand = bands.rightStatus
        capabilities.perception = .preparing; capabilities.conversation = .preparing
        bands.discover()
        log("session_start")
        // Startup is cancellable, but Stop never waits for an adapter to cooperate.
        let perception = self.perception, conversation = self.conversation
        startupDeadline = clock.schedule(afterMs: configuration.startupTimeoutMs) { [weak self] in
            guard let self, self.startupEpoch == epoch, self.phase == .preparing else { return }
            self.stop(.providerFailure)
            self.closeAdapters()
            self.phase = .failed; self.message = "Starting assistance timed out. Try again when the connection is available."
        }
        adapterStartInFlight = true
        startWork = Task { [weak self] in
            defer { self?.adapterStartInFlight = false }
            do {
                try Task.checkCancellation()
                let ready = try await perception.start(context: context, policy: CameraPolicy())
                if let self {
                    guard self.startupEpoch == epoch, self.phase == .preparing, !Task.isCancelled else { return }
                    self.camera = ready.camera; self.sharedAudioInput = ready.audioInput; self.supportsSpeechOnlyGuidance = ready.supportsSpeechOnlyGuidance
                    self.capabilities.perception = self.simulated ? .simulated : .ready
                    self.capabilities.microphone = self.simulated ? .unavailable("SIMULATED text input; microphone is not listening") : .ready
                    self.capabilities.offlineStop = ready.supportsOfflineStop ? .ready : .unavailable("Offline spoken Stop is not integrated. Use Pause or End.")
                } else { return }
                try await conversation.start(context: context, audioInput: ready.audioInput)
                guard let self, self.startupEpoch == epoch, self.phase == .preparing, !Task.isCancelled else { return }
                conversation.setMicrophoneMuted(self.microphoneMuted)
                self.capabilities.conversation = self.simulated ? .simulated : .ready
                self.startupDeadline?.cancel(); self.startupDeadline = nil
                self.phase = .active; self.activity = .listening
                self.message = self.simulated ? "SIMULATED assistance. Use the fixture input below; the microphone is not listening." : "Voice assistance is available. Wristbands require connection and calibration."
                self.speech.speak(self.simulated ? "SIMULATED session. No real camera or wristbands. Enter a simulated request to try the flow." : "I'm ready. What would you like help with? Your wristbands are not active.", priority: 4, context: context)
                UIApplication.shared.isIdleTimerDisabled = true
                self.armFrameWatchdog()
                self.log("session_ready", code: self.simulated ? "SIMULATED" : "LIVE")
            } catch {
                guard let self, self.startupEpoch == epoch, self.phase == .preparing else { return }
                self.stop(.providerFailure)
                self.closeAdapters()
                self.phase = .failed; self.message = error.localizedDescription
                self.capabilities.perception = .unavailable("R1 integration unavailable")
                self.capabilities.conversation = .unavailable("R3 integration unavailable")
            }
        }
    }

    func refreshPermissions() { permissionState = permissions.current() }

    func requestPermissions() {
        guard !requestingPermissions else { return }
        requestingPermissions = true
        let permissions = self.permissions
        Task { [weak self] in
            let state = await permissions.request()
            guard let self else { return }
            self.permissionState = state
            self.requestingPermissions = false
        }
    }
    func finishSetup() { preferences.setupComplete = true; showSetup = false; start() }
    func dismissSetup() { showSetup = false }

    /// Serialized synchronous stop: invalidate first, cancel work, stop BLE, then request R1 speech.
    /// No await, network request, BLE acknowledgment, or audio completion can block this path.
    func stop(_ cause: SessionStopCause) {
        guard phase != .ended && phase != .idle else { return }
        if phase == .paused && cause == .userPause && calibrationIndex == nil { return }
        let old = context
        startupEpoch = UUID(); startWork?.cancel(); startWork = nil
        startupDeadline?.cancel(); startupDeadline = nil
        recoveryToken = UUID(); recoveryWork?.cancel(); recoveryWork = nil
        recoveryDeadline?.cancel(); recoveryDeadline = nil; recoveryRetry?.cancel(); recoveryRetry = nil
        let effects = engine?.reduce(.stop(taskStopReason(cause)), nowMs: clock.nowMs) ?? []
        synchronizeContext()
        cancelPendingWork()
        calibrationIndex = nil; calibrationTaskID = nil
        caption = ""
        bands.allStop(reason: cause.rawValue)
        if let old {
            perception.cancelGuidance(context: old); evidence.cancel(context: old)
            conversation.interrupt(context: old); speech.cancel(context: old)
        }
        if let context { speech.localStop(context: context) }
        phase = cause == .userEnd ? .ending : .paused
        activity = cause == .userEnd ? .ended : .paused
        message = cause == .userEnd ? "Assistance ended." : stopMessage(cause)
        UIApplication.shared.isIdleTimerDisabled = false
        log("session_stop", code: cause.rawValue)
        // reducer cancellation effects are already executed above; never replay its queued speech.
        taskState = engine?.state
        _ = effects
        if cause == .userEnd {
            closeAdapters()
            frameWatchdog?.cancel(); frameWatchdog = nil
            pendingFrame = nil; lastFrame = nil; preview = nil
            bands.shutdown()
            if cause == .userEnd { phase = .ended }
        }
    }

    func resume() {
        refreshPermissions()
        guard !requiresPermissions || permissionState.ready else { message = permissionState.explanation; return }
        guard canResume, !hazardActive, let frame = lastFrame,
              frame.trackingNormal, frame.capturedAtMs <= clock.nowMs,
              frame.expiresAtMs > clock.nowMs, clock.nowMs - frame.capturedAtMs <= 300,
              frame.context == context, capabilities.perception.usable, capabilities.conversation.usable else {
            if phase == .paused { message = "Fresh tracking and working audio are required. Reposition the phone, then choose Resume." }
            return
        }
        phase = .active; activity = .listening
        message = simulated ? "SIMULATED assistance resumed." : "Assistance resumed."
        UIApplication.shared.isIdleTimerDisabled = true
        if engine?.state.phase == .paused, engine?.state.request != nil {
            apply(.resume)
        } else if let context {
            engine = TaskEngine(sessionID: context.sessionID, initialGeneration: context.generation,
                                guidingHand: preferences.guidingHand == .left ? .left : .right)
            taskState = engine?.state
        }
        armFrameWatchdog()
        // TaskEngine asks a fresh, scoped confirmation before reacquiring paused physical work.
    }

    func setMuted(_ muted: Bool) {
        guard microphoneMuted != muted else { return }
        microphoneMuted = muted; conversation.setMicrophoneMuted(muted)
        if muted { stop(.microphoneMuted) }
        else { message = "Microphone unmuted. Choose Resume when ready." }
    }
    func command(_ command: LocalCommand) {
        switch command {
        case .stop: stop(.localStop)
        case .pause: stop(.userPause)
        case .resume: resume()
        case .end: stop(.userEnd)
        case .cancelTask: stop(.cancelTask)
        case .repeatLast:
            guard phase == .active, let context else { return }
            if let request = engine?.state.pendingRequest,
               engine?.state.phase == .choosing || engine?.state.phase == .discussingMenu {
                receiveResponse(request, source: .user, response: .menuCommand(.repeatOptions))
            } else if !caption.isEmpty {
                speech.speak(caption, priority: 4, context: context)
            }
        }
    }
    func backgrounded() { if phase == .active || phase == .preparing || phase == .recovering || calibrationIndex != nil { stop(.background) } }

    func submitTask(_ request: TaskRequest) {
        guard phase == .active, !microphoneMuted, calibrationIndex == nil else { return }
        guard request.kind == .wallMenu || simulated || capabilities.offlineStop.usable else {
            message = "Physical guidance is unavailable until offline spoken Stop is integrated. Scene and menu conversation can continue."
            if let context { speech.speak(message, priority: 4, context: context) }
            return
        }
        guard request.kind == .wallMenu || supportsSpeechOnlyGuidance || (bands.isReady && preferences.calibratedPairSignature == bands.pairSignature) else {
            message = "Calibrate both wristbands before physical guidance, or use a supported speech-only mode."; return
        }
        apply(.start(request))
    }

    private func apply(_ event: TaskEvent) {
        guard var engine else { return }
        let effects = engine.reduce(event, nowMs: clock.nowMs)
        self.engine = engine
        synchronizeContext()
        taskState = engine.state
        for effect in effects { execute(effect) }
        // Cancel obsolete requests even when their providers ignore Swift task cancellation.
        let pending = self.engine?.state.pendingRequest
        for (request, work) in requestWork where request != pending { work.cancel() }
        if self.engine?.state.phase == .paused, phase == .active {
            stop(.userPause)
        }
        deadline?.cancel(); deadline = nil
        if let request = self.engine?.state.pendingRequest {
            deadline = clock.schedule(afterMs: max(1, request.expiresAtMs - clock.nowMs)) { [weak self] in self?.apply(.tick) }
        }
    }
    private func synchronizeContext() {
        guard let task = engine?.state.context else { return }
        let new = SessionContext(sessionID: task.sessionID, generation: task.generation)
        if context != new {
            frameWork?.cancel(); pendingFrame = nil
            frameThrottle?.cancel(); frameThrottle = nil
            context = new
            perception.updateContext(new); conversation.updateContext(new)
            // UInt64 app generations cannot be truncated into UInt16 firmware generations.
            do { try bands.setContext(.init(sessionID: task.sessionID, taskID: task.taskID, generation: task.generation)) }
            catch { log("band_context_unavailable", code: "H1_SYNCHRONIZATION_REQUIRED") }
        }
    }
    private func cancelPendingWork() {
        // Keep cancelled operations counted until the adapter actually returns.
        requestWork.values.forEach { $0.cancel() }
        deadline?.cancel(); deadline = nil
        frameWork?.cancel(); pendingFrame = nil
        frameThrottle?.cancel(); frameThrottle = nil
    }
    private func log(_ name: String, code: String? = nil) {
        diagnostics.record(name, context: context, now: clock.nowMs, taskID: engine?.state.context.taskID, code: code)
    }

    private func taskStopReason(_ cause: SessionStopCause) -> StopReason {
        switch cause {
        case .userEnd: return .ended
        case .trackingLost: return .trackingLost
        case .targetLost: return .targetLost
        case .hazard: return .hazard
        case .bandFailure: return .bandDisconnected
        case .background: return .backgrounded
        case .audioInterruption, .audioRouteLost: return .audioInterrupted
        case .microphoneMuted: return .microphoneMuted
        case .cameraChanged: return .geometryChanged
        case .cancelTask: return .cancelled
        case .taskReplacement: return .replaced
        default: return .userPause
        }
    }
    private func stopMessage(_ cause: SessionStopCause) -> String {
        switch cause {
        case .hazard: return "Stopped for a hazard. Wait for current clearance before resuming."
        case .trackingLost, .targetLost: return "Tracking was lost. Reposition the phone, then choose Resume."
        case .microphoneMuted: return "Microphone muted. Spoken Stop cannot be heard. Pause and End remain available."
        case .bandFailure: return "A wristband stopped responding. Guidance is paused."
        case .background, .audioInterruption, .audioRouteLost: return "Assistance paused after an interruption. Choose Resume when ready."
        default: return "Assistance paused. Choose Resume when ready."
        }
    }
}

private extension SessionCoordinator {
    func closeAdapters() {
        if let context { perception.stop(context: context); conversation.end(context: context) }
        frameWatchdog?.cancel(); frameWatchdog = nil
        sharedAudioInput = nil
    }
    func beginRecovery() {
        guard phase == .active || phase == .paused else { return }
        stop(.providerFailure)
        capabilities.conversation = .unavailable("Conversation disconnected")
        guard sharedAudioInput != nil else { message = "Connection unavailable. End and start again."; return }
        phase = .recovering; activity = .reconnecting
        capabilities.conversation = .preparing; message = "Reconnecting. Physical assistance remains stopped."
        recoveryAttempt = 0
        scheduleRecoveryAttempt()
    }
    func scheduleRecoveryAttempt() {
        guard phase == .recovering, let context, let input = sharedAudioInput else { return }
        guard recoveryAttempt < configuration.reconnectDelaysMs.count else {
            phase = .paused; activity = .paused
            capabilities.conversation = .unavailable("Reconnection failed. End and start again when available.")
            message = capabilities.conversation.label
            return
        }
        let delay = configuration.reconnectDelaysMs[recoveryAttempt]
        recoveryAttempt += 1
        let token = UUID(); recoveryToken = token
        recoveryRetry = clock.schedule(afterMs: delay) { [weak self] in
            guard let self, self.phase == .recovering, self.recoveryToken == token else { return }
            guard !self.adapterStartInFlight else {
                self.phase = .paused; self.activity = .paused
                self.capabilities.conversation = .unavailable("The previous connection attempt is still stopping. End assistance, then try Start again shortly.")
                self.message = self.capabilities.conversation.label
                return
            }
            let conversation = self.conversation
            conversation.end(context: context)
            self.recoveryDeadline = self.clock.schedule(afterMs: self.configuration.startupTimeoutMs) { [weak self] in
                guard let self, self.phase == .recovering, self.recoveryToken == token else { return }
                self.recoveryWork?.cancel(); self.recoveryWork = nil
                self.scheduleRecoveryAttempt()
            }
            self.adapterStartInFlight = true
            self.recoveryWork = Task { [weak self] in
                defer { self?.adapterStartInFlight = false }
                do {
                    try Task.checkCancellation()
                    try await conversation.start(context: context, audioInput: input)
                    guard !Task.isCancelled, let self, self.phase == .recovering, self.recoveryToken == token else { return }
                    conversation.setMicrophoneMuted(self.microphoneMuted)
                    self.recoveryDeadline?.cancel(); self.recoveryDeadline = nil
                    self.recoveryWork = nil; self.phase = .paused; self.activity = .paused
                    self.capabilities.conversation = self.simulated ? .simulated : .ready
                    self.message = "Connection restored. Choose Resume when ready."
                    self.log("conversation_recovered")
                } catch {
                    guard let self, self.phase == .recovering, self.recoveryToken == token else { return }
                    self.recoveryDeadline?.cancel(); self.recoveryDeadline = nil
                    self.recoveryWork = nil; self.scheduleRecoveryAttempt()
                }
            }
        }
    }
    func execute(_ effect: TaskEffect) {
        guard let context else { return }
        let perception = self.perception, evidence = self.evidence
        switch effect {
        case .resolveTarget(let request, let query):
            activity = .thinking
            let kind = engine?.state.request?.kind ?? .button
            launch(request, source: .r3) { .target(try await evidence.resolve(request, query: query, kind: kind)) }
        case .reacquire(let request, let target):
            launch(request, source: .r1) { .physical(try await perception.reacquire(request, target: target)) }
        case .requestGuidance(let request, let mode, let target, let hand):
            activity = .guiding
            launch(request, source: .r1) { [weak self] in
                let fresh = try await perception.reacquire(request, target: target)
                guard self?.engine?.isGuidanceDispatchAllowed(context: request, evidence: fresh, nowMs: self?.clock.nowMs ?? -1) == true else {
                    return .unavailable(.lowConfidence)
                }
                return try await perception.guide(request, mode: mode, target: target, hand: hand)
            }
        case .requestVerification(let request, let action, let target):
            activity = .thinking
            launch(request, source: .r1) { .verification(try await evidence.verify(request, action: action, target: target)) }
        case .readScreen(let request):
            activity = .thinking
            launch(request, source: .r3) { .screen(try await evidence.readScreen(request)) }
        case .readMenu(let request, let query):
            launch(request, source: .r3) { .menu(try await evidence.readMenu(request, query: query)) }
        case .answerMenuQuestion(let request, let question, let menu):
            launch(request, source: .r3) { .menuAnswer(try await evidence.answerMenu(request, question: question, menu: menu)) }
        case .requestPassage(let request, let target):
            launch(request, source: .r1) { .passageAuthorized(try await perception.authorizePassage(request, target: target)) }
        case .speak(let text, let priority):
            speech.speak(text, priority: priority, context: context)
        case .askConfirmation(let pending):
            activity = .listening
            speech.speak(pending.question, priority: 4, context: context)
        case .awaitChoice, .awaitMenuCommand:
            activity = .listening
        case .cancelWork(let task):
            cancelPendingWork()
            let old = SessionContext(sessionID: task.sessionID, generation: task.generation)
            perception.cancelGuidance(context: old); evidence.cancel(context: old)
            conversation.interrupt(context: old); speech.cancel(context: old)
        case .allStop:
            bands.allStop(reason: "task_transition")
        case .successFeedback(let result):
            speech.requestSuccess(result, context: context)
        case .finished(let result):
            activity = .listening
            log("task_finished", code: result.error?.rawValue ?? result.verification.rawValue)
        case .rejected(let reason):
            log("event_rejected", code: reason.rawValue)
        }
    }

    func launch(_ request: RequestContext, source: EventSource,
                operation: @escaping @MainActor () async throws -> TaskResponse) {
        guard requestWork.count < 8, requestWork[request] == nil else {
            receiveResponse(request, source: source, response: .unavailable(.providerTimeout)); return
        }
        requestWork[request] = Task { [weak self] in
            defer { self?.requestWork.removeValue(forKey: request) }
            let response: TaskResponse
            do { try Task.checkCancellation(); response = try await operation() }
            catch is CancellationError { return }
            catch { response = .unavailable(.lowConfidence) }
            guard !Task.isCancelled, let self else { return }
            self.requestWork.removeValue(forKey: request)
            self.receiveResponse(request, source: source, response: response)
        }
    }
    func receiveResponse(_ request: RequestContext, source: EventSource, response: TaskResponse) {
        guard let context, request.task.sessionID == context.sessionID,
              request.task.generation == context.generation, phase == .active else {
            log("late_response_rejected"); return
        }
        let now = clock.nowMs
        apply(.receive(.init(eventID: UUID().uuidString, context: request, monotonicMs: now,
                            expiresAtMs: request.expiresAtMs, source: source), response))
    }
    func receiveConversation(_ event: ConversationEvent) {
        guard event.context == context, !seenConversationEvents.contains(event.eventID),
              phase == .active || phase == .paused else { return }
        if seenConversationEvents.count == 64 { seenConversationEvents.removeFirst() }
        seenConversationEvents.append(event.eventID)
        switch event.payload {
        case .command(let value): command(value)
        case .intent(let request): submitTask(request)
        case .confirmation(let promptID, let answer, let explicitUserResponse):
            guard explicitUserResponse, phase == .active, let pending = engine?.state.confirmation, pending.promptID == promptID else { return }
            receiveResponse(pending.context, source: .user, response: .confirmation(promptID: promptID, answer: answer))
        case .calibrationAnswer(let answer, let explicitUserResponse):
            if explicitUserResponse, calibrationIndex != nil { answerCalibration(answer) }
        case .choice(let requestID, let choice):
            guard let request = engine?.state.pendingRequest, request.requestID == requestID else { return }
            receiveResponse(request, source: .user, response: .choice(choice))
        case .menuCommand(let requestID, let command):
            guard let request = engine?.state.pendingRequest, request.requestID == requestID else { return }
            receiveResponse(request, source: .user, response: .menuCommand(command))
        case .transcript(let text):
            if phase == .active { caption = text }
        case .audio(let audio):
            if phase == .active { speech.enqueueAudio(audio, context: event.context) }
        case .status(let status):
            if phase == .active { activity = status }
        case .disconnected:
            beginRecovery()
        case .screenChanged:
            guard let request = engine?.state.pendingRequest else { return }
            receiveResponse(request, source: .r3, response: .screenChanged)
        }
    }
    func receivePerception(_ event: PerceptionEvent) {
        switch event {
        case .trackingLost(let received): if received == context { stop(.trackingLost) }
        case .targetLost(let received): if received == context { stop(.targetLost) }
        case .hazard(let received):
            if received == context { hazardActive = true; stop(.hazard) }
        case .cameraChanged(let received, let camera):
            if received == context { stop(.cameraChanged); self.camera = camera; lastFrame = nil; preview = nil }
        case .recovered(let received):
            if received == context {
                if phase == .paused { message = "Tracking recovered. Choose Resume when ready." }
            }
        case .hazardCleared(let received, let observedAtMs):
            if received == context, observedAtMs <= clock.nowMs, clock.nowMs - observedAtMs <= 300 {
                hazardActive = false
            }
        case .guidanceResponse(let envelope, let response):
            if phase == .active { apply(.receive(envelope, response)) }
        }
    }
    func receiveFrame(_ frame: FrameSnapshot) {
        guard [.preparing, .active, .paused, .recovering].contains(phase),
              frame.context == context, frame.transform.isValid, frame.capturedAtMs >= 0, frame.capturedAtMs <= clock.nowMs,
              frame.expiresAtMs > clock.nowMs, clock.nowMs - frame.capturedAtMs <= 300,
              camera == nil || frame.geometryID == camera?.geometryID else { return }
        lastFrame = frame; preview = frame.preview
        if !frame.trackingNormal { if phase == .active { stop(.trackingLost) }; return }
        guard phase == .active else { return }
        pendingFrame = frame
        pumpFrame()
    }
    func pumpFrame() {
        guard frameWork == nil, let frame = pendingFrame, phase == .active else { return }
        guard frameIsFreshForConversation(frame) else { pendingFrame = nil; return }
        if let policy = framePolicy {
            guard (frame.encodedImage?.count ?? 0) <= policy.maximumEncodedBytes else {
                pendingFrame = nil; log("frame_dropped", code: "TRANSPORT_SIZE_LIMIT"); return
            }
            if let last = lastFrameDispatchMs, clock.nowMs - last < policy.minimumIntervalMs {
                frameThrottle?.cancel()
                frameThrottle = clock.schedule(afterMs: policy.minimumIntervalMs - (clock.nowMs - last)) { [weak self] in self?.pumpFrame() }
                return
            }
        }
        pendingFrame = nil; lastFrameDispatchMs = clock.nowMs
        let conversation = self.conversation
        frameWork = Task { [weak self] in
            // A frame can expire while waiting for this task to enter the main actor.
            if !Task.isCancelled, self?.frameIsFreshForConversation(frame) == true {
                do { try await conversation.sendLatestFrame(frame) }
                catch { /* Transport emits connection state separately. */ }
            }
            // Even an obsolete send owns the single slot until it returns. Only current
            // pending input may be pumped; the old frame never re-enters the session.
            guard let self else { return }
            self.frameWork = nil
            self.pumpFrame()
        }
    }
    func frameIsFreshForConversation(_ frame: FrameSnapshot) -> Bool {
        let now = clock.nowMs
        return phase == .active && frame.context == context &&
            frame.capturedAtMs >= 0 && frame.capturedAtMs <= now &&
            frame.expiresAtMs > now && now - frame.capturedAtMs <= 300
    }
    func armFrameWatchdog() {
        frameWatchdog?.cancel()
        frameWatchdog = clock.schedule(afterMs: 100) { [weak self] in
            guard let self, self.phase == .active else { return }
            if self.hasActiveTask && (self.lastFrame == nil || self.clock.nowMs >= (self.lastFrame?.expiresAtMs ?? 0) || self.clock.nowMs - (self.lastFrame?.capturedAtMs ?? 0) > 300) {
                self.stop(.trackingLost)
            }
            if self.phase == .active { self.armFrameWatchdog() }
        }
    }
}

extension SessionCoordinator {
    /// Debug-only text stand-in for R3 voice intents. Live mode never uses this parser.
    func submitFixtureText(_ text: String) {
        guard simulated, let conversation = conversation as? FixtureConversation else { return }
        let input = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let lower = input.lowercased()
        let commands: [String: LocalCommand] = ["stop": .stop, "pause": .pause, "resume": .resume,
            "repeat": .repeatLast, "cancel this task": .cancelTask, "end assistance": .end]
        if let command = commands[lower] { conversation.emit(.command(command)); return }
        if calibrationIndex != nil {
            conversation.emit(.calibrationAnswer(input, explicitUserResponse: true)); return
        }
        if let pending = taskState?.confirmation, lower == "yes" || lower == "no" {
            conversation.emit(.confirmation(promptID: pending.promptID, answer: lower == "yes" ? .yes : .no, explicitUserResponse: true)); return
        }
        if taskState?.phase == .choosing, let request = taskState?.pendingRequest {
            if lower == "more" || lower == "back" {
                conversation.emit(.menuCommand(requestID: request.requestID, command: lower == "more" ? .more : .back))
            } else { conversation.emit(.choice(requestID: request.requestID, value: input)) }
            return
        }
        if taskState?.phase == .discussingMenu, let request = taskState?.pendingRequest {
            conversation.emit(.menuCommand(requestID: request.requestID, command: lower == "more" ? .more : lower == "back" ? .back : .question(input))); return
        }
        guard !input.isEmpty else { return }
        let kind: TaskKind = lower.contains("kiosk") || lower.contains("order") ? .kiosk :
            lower.contains("menu") ? .wallMenu : lower.contains("door") ? .door :
            lower.contains("pick") || lower.contains("object") ? .object : lower.contains("approach") ? .approach : .button
        conversation.emit(.intent(.init(id: UUID().uuidString, kind: kind, query: input)))
    }
    func confirmCurrent(_ answer: Bool) {
        guard let pending = taskState?.confirmation else { return }
        receiveResponse(pending.context, source: .user,
                        response: .confirmation(promptID: pending.promptID, answer: answer ? .yes : .no))
    }
    func beginCalibration() {
        guard phase == .active || phase == .paused else {
            calibrationMessage = "Start an assistance session before calibration so the speech scheduler is available."; return
        }
        guard simulated || bands.isReady else {
            calibrationMessage = "Connect and synchronize both confirmed bands before calibration."; return
        }
        guard !hazardActive, !microphoneMuted, capabilities.conversation.usable,
              simulated || capabilities.offlineStop.usable else {
            calibrationMessage = "Calibration requires working spoken Stop, an unmuted microphone, and no active hazard."; return
        }
        stop(.calibration)
        calibrationIndex = 0
        playCalibration()
    }
    func playCalibration() {
        guard let index = calibrationIndex, let oldContext = context else { return }
        guard oldContext.generation < UInt64.max - 1 else { stop(.generationExhausted); return }
        bands.allStop(reason: "calibration_replay")
        speech.cancel(context: oldContext)
        // Calibration cancels the prior physical task. Each playback gets a fresh generation.
        engine = TaskEngine(sessionID: oldContext.sessionID, initialGeneration: oldContext.generation + 1,
                            guidingHand: preferences.guidingHand == .left ? .left : .right)
        synchronizeContext(); taskState = engine?.state
        calibrationTaskID = "calibration-" + oldContext.sessionID
        guard let context else { return }
        // Two side checks, then all nine pattern meanings. Patterns pass through the R1 scheduler.
        let side = index == 0 ? "left" : index == 1 ? "right" : nil
        let pattern: UInt8 = index < 2 ? UInt8(index + 1) : UInt8(index - 1)
        calibrationMessage = simulated ? "SIMULATED calibration. No physical band is playing." : "Identify the side or pattern you felt."
        do {
            if let task = engine?.state.context {
                try bands.setContext(.init(sessionID: task.sessionID, taskID: calibrationTaskID ?? task.taskID, generation: task.generation), calibration: true)
            }
            try speech.requestCalibration(patternID: pattern, side: side, task: .init(sessionID: context.sessionID, taskID: calibrationTaskID!, generation: context.generation))
        } catch { calibrationMessage = error.localizedDescription; calibrationIndex = nil; calibrationTaskID = nil }
    }
    var calibrationExpectedAnswer: String? {
        guard let index = calibrationIndex else { return nil }
        return ["Left", "Right", "Move left", "Move right", "Move up", "Move down", "Closer", "On target", "Success", "Stop", "Lost target or hand"][index]
    }
    func answerCalibration(_ answer: String) {
        guard let index = calibrationIndex, let expected = calibrationExpectedAnswer else { return }
        guard answer.caseInsensitiveCompare(expected) == .orderedSame else { calibrationMessage = "That did not match. Listen to the explanation and try again."; playCalibration(); return }
        bands.allStop(reason: "calibration_step")
        if index == 10 {
            calibrationIndex = nil; calibrationTaskID = nil
            if simulated { preferences.simulatedCalibrationComplete = true }
            else if let signature = bands.pairSignature {
                do { try bands.completeCalibration(); preferences.completeCalibration(pairSignature: signature) }
                catch { calibrationMessage = "Band calibration could not be saved. Reconnect and try again."; return }
            }
            calibrationMessage = simulated ? "SIMULATED calibration complete. Real bands remain uncalibrated." : "Calibration complete. Choose Resume when ready."
        } else { calibrationIndex = index + 1; playCalibration() }
    }
    func stopCalibration() { stop(.userPause); calibrationIndex = nil; calibrationMessage = "Calibration stopped." }
}
