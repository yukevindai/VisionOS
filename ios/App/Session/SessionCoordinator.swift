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
    private(set) var friendAssistance: FriendAssistanceCoordinator?
    private(set) var nativeMedia: NativeMediaCoordinator?
    private(set) var context: SessionContext?
    private var startWork: Task<Void, Never>?
    private var adapterStartInFlight = false
    private var permissionStartPending = false
    private var applicationActive = true
    private var requestWork: [RequestContext: Task<Void, Never>] = [:]
    private var deadline: ScheduledWork?
    private var frameWatchdog: ScheduledWork?
    private var latestTrackingState: CameraTrackingState?
    private var trackingContextBeganAtMs: Int64 = 0
    private var previewExpiry: ScheduledWork?
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
         generationSeed: @escaping () -> UInt64 = { 0 },
         friends: FriendAssistanceIntegration? = nil, media: NativeMediaIntegration? = nil) {
        self.simulated = simulated; self.preferences = preferences; self.diagnostics = diagnostics; self.bands = bands
        self.clock = clock; self.perception = perception; self.speech = speech; self.conversation = conversation
        self.evidence = evidence; self.permissions = permissions
        self.configuration = configuration; self.framePolicy = framePolicy; self.generationSeed = generationSeed
        self.enforceSimulatedPermissions = enforceSimulatedPermissions
        permissionState = permissions.current()
        bindAdapters()
        if let friends { bindFriendAssistance(friends) }
        if let media { nativeMedia = NativeMediaCoordinator(clock: clock, integration: media) }
    }

    private func bindAdapters() {
        perception.onFrame = { [weak self] in self?.receiveFrame($0) }
        (perception as? any CameraPreviewSource)?.onPreview = { [weak self] in self?.receiveCameraPreview($0) }
        if let source = perception as? any CameraTrackingSource {
            source.onTrackingState = { [weak self] in self?.receiveTrackingState($0) }
            source.onTrackingIssue = { [weak self] context, reason in
                guard let self, context == self.context else { return }
                self.log("tracking_loss", code: reason)
            }
        }
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
        guard !requestingPermissions else {
            message = "Finish the permission request before starting another call."
            return
        }
        permissionState = permissions.current()
        if requiresPermissions && !permissionState.ready {
            guard permissionState.canRequest else {
                phase = .failed
                message = permissionState.explanation
                return
            }
            requestAccessAndStart()
            return
        }
        startupEpoch = UUID()
        let epoch = startupEpoch
        let id = UUID().uuidString
        engine = TaskEngine(sessionID: id, initialGeneration: max(generationSeed(), bands.minimumNextGeneration), guidingHand: preferences.guidingHand == .left ? .left : .right)
        taskState = engine?.state
        context = .init(sessionID: id, generation: engine!.state.context.generation)
        if let context { (evidence as? any FrameTargetEvidenceClient)?.updateContext(context) }
        guard let context else { return }
        phase = .preparing; activity = .preparing; message = "Preparing assistance."
        hazardActive = false; microphoneMuted = false; seenConversationEvents.removeAll(); caption = ""
        latestTrackingState = nil; trackingContextBeganAtMs = clock.nowMs
        lastFrame = nil; pendingFrame = nil; preview = nil; lastFrameDispatchMs = nil
        previewExpiry?.cancel(); previewExpiry = nil
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
                let objectIntroduction = (self.evidence as? GeminiObjectEvidenceClient)?.introduction
                self.message = self.simulated ? "SIMULATED assistance. Use the fixture input below; the microphone is not listening." :
                    (objectIntroduction ?? "Voice assistance is available. Wristbands require connection and calibration.")
                self.speech.speak(self.simulated ? "SIMULATED session. No real camera or wristbands. Enter a simulated request to try the flow." :
                    (objectIntroduction ?? "I'm ready. What would you like help with? Your wristbands are not active."), priority: 4, context: context)
                UIApplication.shared.isIdleTimerDisabled = true
                self.armFrameWatchdog()
                self.nativeMedia?.start(localSessionID: context.sessionID)
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

    private func requestAccessAndStart() {
        startupEpoch = UUID()
        let epoch = startupEpoch
        permissionStartPending = true
        requestingPermissions = true
        phase = .preparing; activity = .preparing
        message = "Allow camera, microphone and speech recognition access to start your call."
        let permissions = self.permissions
        Task { [weak self] in
            guard self?.permissionStartPending == true, self?.startupEpoch == epoch else {
                self?.requestingPermissions = false
                return
            }
            let state = await permissions.request()
            guard let self else { return }
            self.requestingPermissions = false
            self.permissionState = state
            guard self.permissionStartPending, self.startupEpoch == epoch else { return }
            guard state.ready else {
                self.permissionStartPending = false
                self.phase = .failed
                self.message = state.explanation
                return
            }
            self.continueAfterPermissions()
        }
    }

    private func continueAfterPermissions() {
        guard permissionStartPending, !requestingPermissions, applicationActive,
              phase == .preparing, permissionState.ready else { return }
        permissionStartPending = false
        phase = .idle
        start()
    }

    func becameActive() {
        applicationActive = true
        refreshPermissions()
        continueAfterPermissions()
    }

    func becameInactive() {
        applicationActive = false
        if !permissionStartPending { backgrounded() }
    }

    /// Serialized synchronous stop: invalidate first, cancel work, stop BLE, then request R1 speech.
    /// No await, network request, BLE acknowledgment, or audio completion can block this path.
    func stop(_ cause: SessionStopCause) { stop(cause, preparedFriendEffects: nil) }

    private func stop(_ cause: SessionStopCause, preparedFriendEffects: [TaskEffect]?) {
        if [.userEnd, .background, .microphoneMuted, .cameraChanged, .providerFailure].contains(cause) { nativeMedia?.stop() }
        guard phase != .ended && phase != .idle else { return }
        permissionStartPending = false
        startupEpoch = UUID()
        let stopEpoch = startupEpoch
        friendAssistance?.preemptForStop()
        guard startupEpoch == stopEpoch else { return }
        previewExpiry?.cancel(); previewExpiry = nil; preview = nil
        if phase == .paused && cause == .userPause && calibrationIndex == nil {
            friendAssistance?.ownerStopped(endSharing: false)
            return
        }
        let old = context
        startWork?.cancel(); startWork = nil
        startupDeadline?.cancel(); startupDeadline = nil
        recoveryToken = UUID(); recoveryWork?.cancel(); recoveryWork = nil
        recoveryDeadline?.cancel(); recoveryDeadline = nil; recoveryRetry?.cancel(); recoveryRetry = nil
        let effects = preparedFriendEffects ?? engine?.reduce(.stop(taskStopReason(cause)), nowMs: clock.nowMs) ?? []
        synchronizeContext()
        guard startupEpoch == stopEpoch else { return }
        cancelPendingWork()
        calibrationIndex = nil; calibrationTaskID = nil
        caption = ""
        bands.allStop(reason: cause.rawValue)
        if let old {
            perception.cancelGuidance(context: old); evidence.cancel(context: old)
            conversation.interrupt(context: old); speech.cancel(context: old)
        }
        if let context { speech.localStop(context: context) }
        guard startupEpoch == stopEpoch else { return }
        phase = cause == .userEnd ? .ending : .paused
        activity = cause == .userEnd ? .ended : .paused
        message = cause == .userEnd ? "Assistance ended." : stopMessage(cause)
        UIApplication.shared.isIdleTimerDisabled = false
        log("session_stop", code: cause.rawValue)
        // reducer cancellation effects are already executed above; never replay its queued speech.
        taskState = engine?.state
        _ = effects
        if preparedFriendEffects == nil {
            friendAssistance?.ownerStopped(endSharing: [.userEnd, .background].contains(cause))
        }
        if cause == .userEnd {
            closeAdapters()
            frameWatchdog?.cancel(); frameWatchdog = nil
            pendingFrame = nil; lastFrame = nil; preview = nil
            bands.shutdown()
            if cause == .userEnd { phase = .ended }
        }
    }

    func resume() {
        guard friendAssistance?.pendingAcceptance == nil else {
            message = "The selected target is still being synchronized. Pause or End can cancel it."
            return
        }
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
        friendAssistance?.taskChanged()
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
    func backgrounded() {
        applicationActive = false
        if permissionStartPending {
            stop(.userEnd)
            return
        }
        nativeMedia?.stop()
        if phase == .active || phase == .preparing || phase == .recovering || calibrationIndex != nil { stop(.background) }
        else { friendAssistance?.close() }
    }

    func submitTask(_ request: TaskRequest) {
        guard phase == .active, !microphoneMuted, calibrationIndex == nil else { return }
        let informational = request.kind == .object && ObjectRequestIntent.isQuestion(request.query)
        guard informational || request.kind == .wallMenu || simulated || capabilities.offlineStop.usable else {
            message = "Physical guidance is unavailable until offline spoken Stop is integrated. Scene and menu conversation can continue."
            if let context { speech.speak(message, priority: 4, context: context) }
            return
        }
        guard informational || request.kind == .wallMenu || supportsSpeechOnlyGuidance || (bands.isReady && preferences.calibratedPairSignature == bands.pairSignature) else {
            message = "Calibrate both wristbands before physical guidance, or use a supported speech-only mode."; return
        }
        apply(.start(request))
    }

    private func apply(_ event: TaskEvent, failureExplanation: String? = nil) {
        guard var engine else { return }
        let effects = engine.reduce(event, nowMs: clock.nowMs)
        self.engine = engine
        applyEffects(effects, failureExplanation: failureExplanation)
    }
    private func applyEffects(_ effects: [TaskEffect], failureExplanation: String? = nil) {
        guard let engine else { return }
        let effectEpoch = startupEpoch
        synchronizeContext()
        guard self.engine?.state == engine.state, startupEpoch == effectEpoch else { return }
        taskState = engine.state
        friendAssistance?.taskChanged()
        let pausesSession = engine.state.phase == .paused && phase == .active
        for effect in effects {
            guard self.engine?.state == engine.state, startupEpoch == effectEpoch else { return }
            // The unified Stop cancels speech; announce the reason after it completes.
            if pausesSession, case .speak = effect { continue }
            if let explanation = failureExplanation, case .speak(_, let priority) = effect {
                message = explanation
                execute(.speak(text: explanation, priority: priority))
            } else { execute(effect) }
        }
        // Cancel obsolete requests even when their providers ignore Swift task cancellation.
        let pending = self.engine?.state.pendingRequest
        for (request, work) in requestWork where request != pending { work.cancel() }
        if pausesSession {
            stop(sessionStopCause(engine.state.lastStop ?? .userPause))
            for case let .speak(text, priority) in effects where text != "Stop" {
                message = text
                execute(.speak(text: text, priority: priority))
            }
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
            latestTrackingState = nil; trackingContextBeganAtMs = clock.nowMs
            (evidence as? any FrameTargetEvidenceClient)?.updateContext(new)
            perception.updateContext(new)
            guard engine?.state.context == task, context == new else { return }
            conversation.updateContext(new)
            guard engine?.state.context == task, context == new else { return }
        }
        // Accepted binding changes task ID within the generation allocated by Stop.
        // UInt64 app generations still cannot be truncated into UInt16 firmware generations.
        do { try bands.setContext(.init(sessionID: task.sessionID, taskID: task.taskID, generation: task.generation)) }
        catch { log("band_context_unavailable", code: "H1_SYNCHRONIZATION_REQUIRED") }
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
        #if DEBUG
        if name == "tracking_loss" || name == "session_stop" {
            print("Reflex diagnostic: \(name) code=\(code ?? "none")")
        }
        #endif
    }

    private func sessionStopCause(_ reason: StopReason) -> SessionStopCause {
        switch reason {
        case .userPause: return .userPause
        case .cancelled: return .cancelTask
        case .ended: return .userEnd
        case .trackingLost: return .trackingLost
        case .targetLost, .handLost: return .targetLost
        case .hazard: return .hazard
        case .bandDisconnected: return .bandFailure
        case .backgrounded: return .background
        case .audioInterrupted: return .audioInterruption
        case .microphoneMuted: return .microphoneMuted
        case .geometryChanged: return .cameraChanged
        case .replaced: return .taskReplacement
        }
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

    private func bindFriendAssistance(_ integration: FriendAssistanceIntegration) {
        let bridge = FriendAssistanceCoordinator(clock: clock, integration: integration)
        friendAssistance = bridge
        bridge.currentTask = { [weak self] in self?.engine?.state.context }
        bridge.sessionAllowsSharing = { [weak self] in
            guard let self else { return false }
            return [.active, .paused].contains(self.phase) && self.calibrationIndex == nil
        }
        bridge.mayPrompt = { [weak self] in
            guard let self else { return false }
            return [.active, .paused].contains(self.phase) && !self.hasActiveTask && self.calibrationIndex == nil
        }
        bridge.mayAccept = { [weak self] in self?.friendAcceptanceReady == true }
        bridge.prepareAcceptance = { [weak self] binding in self?.prepareFriendAcceptance(binding) == true }
        bridge.completeAcceptance = { [weak self] binding in self?.completeFriendAcceptance(binding) == true }
        bridge.pause = { [weak self] in self?.stop(.userPause) }
        bridge.localInput = { [weak self] input in
            guard let self else { return }
            switch input {
            case .command(let context, let command):
                guard context == self.context else { return }
                self.command(command)
            case .taskConfirmation(let context, let promptID, let answer, let provenance):
                guard context == self.context, provenance == .localOwnerSpeech,
                      let pending = self.engine?.state.confirmation, pending.promptID == promptID else { return }
                self.receiveResponse(pending.context, source: .user, response: .confirmation(promptID: promptID, answer: answer))
            case .selection: break
            case .intent(let context, let request, let provenance):
                guard context == self.context, provenance == .localOwnerSpeech,
                      self.friendAssistance?.pendingAcceptance == nil else { return }
                self.submitTask(request)
            case .choice(let context, let requestID, let value, let provenance):
                guard context == self.context, provenance == .localOwnerSpeech,
                      let pending = self.engine?.state.pendingRequest, pending.requestID == requestID else { return }
                self.receiveResponse(pending, source: .user, response: .choice(value))
            case .menuCommand(let context, let requestID, let command, let provenance):
                guard context == self.context, provenance == .localOwnerSpeech,
                      let pending = self.engine?.state.pendingRequest, pending.requestID == requestID else { return }
                self.receiveResponse(pending, source: .user, response: .menuCommand(command))
            case .calibrationAnswer(let context, let answer, let provenance):
                guard context == self.context, provenance == .localOwnerSpeech, self.calibrationIndex != nil else { return }
                self.answerCalibration(answer)
            }
        }
    }

    /// Trusted backend adapter calls this after explicit owner setup; no UI enables it by default.
    @discardableResult
    func enableFriendSharing(authenticatedScope: FriendSelectionScope) -> Bool {
        guard [.active, .paused].contains(phase), calibrationIndex == nil else { return false }
        return friendAssistance?.configure(authenticatedScope) == true
    }

    func stopFriendSharing() {
        nativeMedia?.stopSharing()
        friendAssistance?.close()
    }

    private var friendAcceptanceReady: Bool {
        guard friendExecutionReady, let frame = lastFrame, frame.context == context,
              frame.trackingNormal, frame.capturedAtMs <= clock.nowMs,
              frame.expiresAtMs > clock.nowMs, clock.nowMs - frame.capturedAtMs <= 300 else { return false }
        return true
    }

    private var friendExecutionReady: Bool {
        [.active, .paused].contains(phase) && !microphoneMuted && !hazardActive && calibrationIndex == nil &&
        capabilities.perception.usable && capabilities.conversation.usable &&
        (simulated || capabilities.offlineStop.usable) &&
        (supportsSpeechOnlyGuidance || (bands.isReady && preferences.calibratedPairSignature == bands.pairSignature)) &&
        (!requiresPermissions || permissions.current().ready)
    }

    private func prepareFriendAcceptance(_ binding: FriendSelectionAccepted) -> Bool {
        guard friendAcceptanceReady, let current = engine?.state.context,
              current.sessionID == binding.scope.sessionID, current.taskID == binding.oldTaskID,
              current.generation == binding.oldTaskGeneration,
              let frame = lastFrame, frame.context == context, frame.trackingNormal,
              frame.capturedAtMs <= clock.nowMs, frame.expiresAtMs > clock.nowMs,
              clock.nowMs - frame.capturedAtMs <= 300, var engine else { return false }
        let effects = engine.prepareAcceptedSelection(binding, nowMs: clock.nowMs)
        guard !effects.contains(where: { if case .rejected = $0 { return true }; return false }) else { return false }
        self.engine = engine
        stop(.taskReplacement, preparedFriendEffects: effects)
        return self.engine?.state.context.generation == binding.newTaskGeneration && phase == .paused
    }

    private func completeFriendAcceptance(_ binding: FriendSelectionAccepted) -> Bool {
        guard friendExecutionReady, phase == .paused, var engine else { return false }
        let effects = engine.beginAcceptedSelection(binding, nowMs: clock.nowMs)
        guard !effects.contains(where: { if case .rejected = $0 { return true }; return false }) else { return false }
        self.engine = engine
        phase = .active; activity = .thinking
        message = "Finding the selected target in the current scene."
        UIApplication.shared.isIdleTimerDisabled = true
        applyEffects(effects)
        guard phase == .active, self.engine?.state.context.taskID == binding.newTaskID,
              self.engine?.state.context.generation == binding.newTaskGeneration else { return false }
        armFrameWatchdog()
        return true
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
            launch(request, source: .r3) {
                if kind == .object, ObjectRequestIntent.isQuestion(query) {
                    guard let answering = evidence as? any QuestionAnswering else { throw GeminiObjectError.unavailable }
                    return .answer(try await answering.answer(request, question: query))
                }
                let target = try await evidence.resolve(request, query: query, kind: kind)
                if target.safety == .unassessedObject,
                   let preparer = perception as? any ObjectTargetPreparing {
                    try await preparer.prepareTarget(request, target: target)
                }
                return .target(target)
            }
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
            var failureExplanation: String?
            do { try Task.checkCancellation(); response = try await operation() }
            catch is CancellationError { return }
            catch let error as GeminiObjectError {
                response = .unavailable(.lowConfidence)
                failureExplanation = error.localizedDescription
            }
            catch { response = .unavailable(.lowConfidence) }
            guard !Task.isCancelled, let self else { return }
            self.requestWork.removeValue(forKey: request)
            self.receiveResponse(request, source: source, response: response, failureExplanation: failureExplanation)
        }
    }
    func receiveResponse(_ request: RequestContext, source: EventSource, response: TaskResponse,
                         failureExplanation: String? = nil) {
        guard let context, request.task.sessionID == context.sessionID,
              request.task.generation == context.generation, phase == .active else {
            log("late_response_rejected"); return
        }
        let now = clock.nowMs
        apply(.receive(.init(eventID: UUID().uuidString, context: request, monotonicMs: now,
                            expiresAtMs: request.expiresAtMs, source: source), response), failureExplanation: failureExplanation)
    }
    func receiveConversation(_ event: ConversationEvent) {
        guard event.context == context, !seenConversationEvents.contains(event.eventID),
              phase == .active || phase == .paused else { return }
        if seenConversationEvents.count == 64 { seenConversationEvents.removeFirst() }
        seenConversationEvents.append(event.eventID)
        if friendAssistance != nil {
            // This legacy worker channel cannot establish local owner provenance or carry
            // generation-scoped PCM. The separately injected local ingress handles consent.
            switch event.payload {
            case .confirmation, .calibrationAnswer, .choice, .menuCommand, .audio, .intent, .localOwnerUtterance: return
            case .command(let command) where ![.stop, .pause, .cancelTask].contains(command): return
            default: break
            }
        }
        switch event.payload {
        case .localOwnerUtterance(let text): receiveLocalOwnerUtterance(text)
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
    func receiveCameraPreview(_ frame: CameraPreviewFrame) {
        guard [.preparing, .active, .paused, .recovering].contains(phase),
              frame.context == context, frame.capturedAtMs >= 0, frame.capturedAtMs <= clock.nowMs,
              clock.nowMs - frame.capturedAtMs < 300,
              camera == nil || frame.geometryID == camera?.geometryID else { return }
        showPreview(frame.image, expiresAtMs: frame.capturedAtMs + 300)
    }

    private func showPreview(_ image: UIImage?, expiresAtMs: Int64) {
        preview = image
        previewExpiry?.cancel()
        previewExpiry = nil
        if image != nil {
            previewExpiry = clock.schedule(afterMs: expiresAtMs - clock.nowMs) { [weak self] in
                self?.preview = nil
                self?.previewExpiry = nil
            }
        }
    }

    func receiveFrame(_ frame: FrameSnapshot) {
        guard [.preparing, .active, .paused, .recovering].contains(phase),
              frame.context == context, frame.transform.isValid, frame.capturedAtMs >= 0, frame.capturedAtMs <= clock.nowMs,
              frame.expiresAtMs > clock.nowMs, clock.nowMs - frame.capturedAtMs <= 300,
              camera == nil || frame.geometryID == camera?.geometryID else { return }
        lastFrame = frame
        if !(perception is any CameraPreviewSource) {
            showPreview(frame.preview, expiresAtMs: min(frame.expiresAtMs, frame.capturedAtMs + 300))
        }
        if !frame.trackingNormal { if phase == .active { stop(.trackingLost) }; return }
        guard phase == .active else { return }
        let capturedAtUTCms = Int64(Date().timeIntervalSince1970 * 1_000) - (clock.nowMs - frame.capturedAtMs)
        (evidence as? any FrameTargetEvidenceClient)?.receive(frame, capturedAtUTCms: capturedAtUTCms)
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
    func receiveTrackingState(_ state: CameraTrackingState) {
        guard [.preparing, .active, .paused, .recovering].contains(phase),
              state.context == context, state.geometryID == camera?.geometryID,
              state.isFresh(at: clock.nowMs),
              latestTrackingState.map({ state.capturedAtMs >= $0.capturedAtMs }) ?? true else { return }
        latestTrackingState = state
    }

    func armFrameWatchdog() {
        frameWatchdog?.cancel()
        frameWatchdog = clock.schedule(afterMs: 100) { [weak self] in
            guard let self, self.phase == .active else { return }
            if self.hasActiveTask {
                let issue: String?
                if self.perception is any CameraTrackingSource {
                    if let state = self.latestTrackingState, state.context == self.context,
                       state.geometryID == self.camera?.geometryID, state.isFresh(at: self.clock.nowMs) {
                        issue = state.status == .normal ? nil : "AR_STATE_" + state.status.rawValue
                    } else if self.latestTrackingState == nil,
                              self.clock.nowMs - self.trackingContextBeganAtMs < 300 {
                        // Wait for the first capture in this generation. This grants no physical evidence.
                        issue = nil
                    } else { issue = "CAPTURE_TRACKING_EXPIRED" }
                } else {
                    issue = self.lastFrame == nil || self.clock.nowMs >= (self.lastFrame?.expiresAtMs ?? 0) ||
                        self.clock.nowMs - (self.lastFrame?.capturedAtMs ?? 0) > 300 ? "ENCODED_FRAME_EXPIRED" : nil
                }
                if let issue {
                    self.log("tracking_loss", code: issue)
                    self.stop(.trackingLost)
                }
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
    func receiveLocalOwnerUtterance(_ text: String) {
        guard !simulated, friendAssistance == nil, !microphoneMuted else { return }
        let input = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !input.isEmpty, input.count <= 2_000 else { return }
        if let command = NativeVoiceInput.command(input) { self.command(command); return }
        guard phase == .active else { return }
        caption = input
        let normalized = input.lowercased().trimmingCharacters(in: .punctuationCharacters)
        if let prompt = taskState?.confirmation {
            let answer: ConfirmationAnswer?
            switch normalized {
            case "yes", "yes i did", "done": answer = .yes
            case "no", "not yet": answer = .no
            default: answer = nil
            }
            if let answer {
                receiveResponse(prompt.context, source: .user,
                    response: .confirmation(promptID: prompt.promptID, answer: answer))
            }
            if answer != nil { return }
            if ObjectRequestIntent.isQuestion(input) {
                submitTask(.init(id: UUID().uuidString, kind: .object, query: input))
            }
            return
        }
        if ObjectRequestIntent.isQuestion(input), taskState?.phase == .choosing {
            submitTask(.init(id: UUID().uuidString, kind: .object, query: input))
            return
        }
        if taskState?.phase == .choosing, let pending = taskState?.pendingRequest {
            receiveResponse(pending, source: .user, response: .choice(input))
            return
        }
        guard !hasActiveTask || ObjectRequestIntent.isQuestion(input) else { return }
        guard input.utf16.count <= 500 else {
            message = "Describe one object in a shorter request."
            if let context { speech.speak(message, priority: 4, context: context) }
            return
        }
        submitTask(.init(id: UUID().uuidString, kind: .object, query: input))
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
        guard answer.caseInsensitiveCompare(expected) == .orderedSame else {
            playCalibration()
            if calibrationIndex != nil {
                calibrationMessage = "That did not match. Listen to the explanation and try again."
            }
            return
        }
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
