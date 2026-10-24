import AVFoundation
import Foundation
import Tasks
import HapticLink

/// Explicit R1 stand-in, instantiated only by simulation composition. Never opens the camera.
@MainActor
final class FixturePerception: PerceptionGuidanceClient {
    var onEvent: ((PerceptionEvent) -> Void)?
    var onFrame: ((FrameSnapshot) -> Void)?
    var onLocalCommand: ((SessionContext, LocalCommand) -> Void)?
    var onHapticRelease: ((ScheduledHapticRelease) -> Void)?
    private let clock: any AppClock
    private var frameTimer: ScheduledWork?
    private(set) var context: SessionContext?
    var trackingNormal = true
    var depthUsable = true
    var staleFrames = false
    var hazardPresent = false
    let camera = CameraCapabilities(selectedLens: "SIMULATED rear camera", depthSupported: true, fallbackReason: "Fixture data; no physical camera", geometryID: "fixture-geometry")
    init(clock: any AppClock) { self.clock = clock }
    func start(context: SessionContext, policy: CameraPolicy) async throws -> PerceptionReadiness {
        self.context = context
        emitFrame()
        return .init(camera: camera, audioInput: .init(identifier: "SIMULATED text input"), supportsOfflineStop: false, supportsSpeechOnlyGuidance: true)
    }
    func updateContext(_ context: SessionContext) { self.context = context }
    func emitFrame() {
        guard let context else { return }
        let now = clock.nowMs
        let captured = now - (staleFrames ? 1_000 : 0)
        onFrame?(.init(context: context, frameID: "fixture-\(now)", capturedAtMs: captured,
                       expiresAtMs: captured + 300, geometryID: camera.geometryID, transform: .identity,
                       preview: nil, encodedImage: nil, trackingNormal: trackingNormal, depthUsable: depthUsable))
        frameTimer = clock.schedule(afterMs: 100) { [weak self] in self?.emitFrame() }
    }
    func evidence(for target: ResolvedTarget) -> PhysicalEvidence {
        .init(targetID: target.id, frameID: "fixture-\(clock.nowMs)", observedAtMs: clock.nowMs - (staleFrames ? 1_000 : 0),
              targetVisible: true, handVisible: true, trackingNormal: trackingNormal, depthUsable: depthUsable, freeSpaceObserved: depthUsable, outputReady: true,
              hazardPresent: hazardPresent, distanceM: target.requiresApproach ? 2 : 0.5)
    }
    func reacquire(_ request: RequestContext, target: ResolvedTarget) async throws -> PhysicalEvidence { evidence(for: target) }
    func guide(_ request: RequestContext, mode: GuidanceMode, target: ResolvedTarget, hand: GuidingHand) async throws -> TaskResponse {
        .guidance(mode: mode, targetID: target.id, status: .onTarget, evidence: evidence(for: target))
    }
    func authorizePassage(_ request: RequestContext, target: ResolvedTarget) async throws -> PhysicalEvidence { evidence(for: target) }
    func cancelGuidance(context: SessionContext) {}
    func stop(context: SessionContext) { guard self.context?.sessionID == context.sessionID else { return }; frameTimer?.cancel(); frameTimer = nil; self.context = nil }
    func inject(_ cause: SessionStopCause) {
        guard let context else { return }
        switch cause {
        case .hazard: hazardPresent = true; onEvent?(.hazard(context))
        case .targetLost: onEvent?(.targetLost(context))
        case .cameraChanged: onEvent?(.cameraChanged(context, camera))
        default: trackingNormal = false; onEvent?(.trackingLost(context))
        }
    }
    func recover() {
        trackingNormal = true; hazardPresent = false; staleFrames = false
        guard let context else { return }
        emitFrame(); onEvent?(.recovered(context)); onEvent?(.hazardCleared(context, observedAtMs: clock.nowMs))
    }
}

/// Test replacement for R1's scheduler. It is the ONLY app speech engine in simulation;
/// live composition does not instantiate it. No audio capture, tones, or real BLE playback.
@MainActor
final class FixtureSpeech: NSObject, SpeechSchedulerClient, AVSpeechSynthesizerDelegate {
    var onCaption: ((SessionContext, String) -> Void)?
    private let synthesizer = AVSpeechSynthesizer()
    let audible: Bool
    private var queue: [(String, Int, SessionContext)] = []
    private var currentUtterance: AVSpeechUtterance?
    private(set) var stoppedCount = 0
    private(set) var spoken: [String] = []
    init(audible: Bool = true) {
        self.audible = audible
        super.init()
        synthesizer.delegate = self
    }
    func speak(_ text: String, priority: Int, context: SessionContext) {
        onCaption?(context, text)
        if spoken.count == 64 { spoken.removeFirst() }
        spoken.append(text)
        guard audible else { return }
        if priority == 0 { cancel(context: context) }
        guard queue.count < 8 else { return }
        queue.append((text, priority, context))
        playNext()
    }
    func enqueueAudio(_ audio: Data, context: SessionContext) {
        // Fixtures never manufacture provider audio or play an unverified audio format.
    }
    func cancel(context: SessionContext) {
        queue.removeAll(); currentUtterance = nil
        synthesizer.stopSpeaking(at: .immediate)
    }
    private func playNext() {
        guard currentUtterance == nil, !queue.isEmpty else { return }
        let index = queue.indices.min { queue[$0].1 < queue[$1].1 }!
        let next = queue.remove(at: index)
        let utterance = AVSpeechUtterance(string: next.0)
        currentUtterance = utterance
        synthesizer.speak(utterance)
    }
    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor [weak self] in
            guard let self, self.currentUtterance === utterance else { return }
            self.currentUtterance = nil; self.playNext()
        }
    }
    func localStop(context: SessionContext) { stoppedCount += 1; speak("Stop", priority: 0, context: context) }
    func requestSuccess(_ result: TaskResult, context: SessionContext) {
        let message = result.verification == .userConfirmed ? "SIMULATED task completed, based on your confirmation." : "SIMULATED task completed with fixture evidence."
        speak(message, priority: 4, context: context)
    }
    func requestCalibration(patternID: UInt8, side: String?, task: TaskContext) throws {
        let context = SessionContext(sessionID: task.sessionID, generation: task.generation)
        let meanings = ["Move left", "Move right", "Move up", "Move down", "Closer", "On target", "Success", "Stop", "Lost target or hand"]
        let meaning = (1...9).contains(patternID) ? meanings[Int(patternID) - 1] : "Unavailable pattern"
        speak("SIMULATED \(meaning)\(side.map { " on the \($0) side" } ?? ""). No physical band is playing.", priority: 2, context: context)
    }
}

@MainActor
final class FixtureConversation: ConversationClient {
    var onEvent: ((ConversationEvent) -> Void)?
    private(set) var context: SessionContext?
    private(set) var frameCount = 0
    private(set) var muted = false
    var failNextStarts = 0
    var holdStart = false
    private(set) var startCount = 0
    private var startContinuation: CheckedContinuation<Void, Never>?
    func start(context: SessionContext, audioInput: SharedAudioInput) async throws {
        startCount += 1
        if failNextStarts > 0 { failNextStarts -= 1; throw IntegrationUnavailable(component: "SIMULATED connection failure") }
        if holdStart { await withCheckedContinuation { startContinuation = $0 } }
        try Task.checkCancellation()
        self.context = context
    }
    func releaseHeldStart() { startContinuation?.resume(); startContinuation = nil }
    func updateContext(_ context: SessionContext) { self.context = context }
    func sendLatestFrame(_ frame: FrameSnapshot) async throws { frameCount += 1 }
    func setMicrophoneMuted(_ muted: Bool) { self.muted = muted }
    func interrupt(context: SessionContext) {}
    func end(context: SessionContext) { if self.context?.sessionID == context.sessionID { self.context = nil } }
    func emit(_ payload: ConversationPayload) {
        guard let context else { return }
        onEvent?(.init(context: context, eventID: UUID().uuidString, payload: payload))
    }
}

@MainActor
final class FixtureEvidence: TargetEvidenceClient {
    private let clock: any AppClock
    var forceAmbiguous = false
    var screenReads = 0
    private var screenTaskIdentity: String?
    #if DEBUG
    enum UITestScenario: String {
        case kioskWrongScreen = "kiosk-wrong-screen"
        case kioskReset = "kiosk-reset"
        case blockedDoor = "blocked-door"
    }
    var uiTestScenario: UITestScenario?
    private var reportedBlockedDoor = false
    #endif
    init(clock: any AppClock) { self.clock = clock }
    func resolve(_ request: RequestContext, query: String, kind: TaskKind) async throws -> ResolvedTarget {
        let targetKind: TargetKind = kind == .object ? .object : kind == .door ? .handle : .button
        let safety: ControlSafety = forceAmbiguous ? .ambiguous : query.lowercased().contains("emergency") ? .emergency : .ordinary
        return .init(id: "fixture-target", kind: targetKind, label: query, region: .init(x: 0.3, y: 0.3, width: 0.2, height: 0.2),
                     frameID: "fixture-\(clock.nowMs)", safety: safety, requiresApproach: kind == .approach,
                     doorAction: kind == .door ? .lever : nil, doorActionEvidence: kind == .door)
    }
    func readScreen(_ request: RequestContext) async throws -> ScreenModel {
        let identity = request.task.sessionID + ":" + request.task.taskID
        if screenTaskIdentity != identity { screenTaskIdentity = identity; screenReads = 0 }
        screenReads += 1
        #if DEBUG
        if let screen = recoveryScreen() { return screen }
        #endif
        let id = "fixture-screen-\(screenReads)"
        let frame = "fixture-\(clock.nowMs)"
        let payment = screenReads >= 3
        let choices = payment ? [] : [ResolvedTarget(id: "fixture-choice-\(screenReads)", kind: .screenControl,
            label: screenReads == 1 ? "Tea" : "Add sandwich", region: .init(x: 0.2, y: 0.2, width: 0.3, height: 0.15),
            frameID: frame, sourceScreenID: id)]
        return .init(id: id, frameID: frame, title: payment ? "Payment" : "SIMULATED café menu", controls: choices,
                     isPaymentScreen: payment, total: payment ? "Fixture total: 8 dollars" : nil)
    }
    #if DEBUG
    /// Deterministic UI-only scenes, never a claim about an actual kiosk or door.
    private func recoveryScreen() -> ScreenModel? {
        let frame = "fixture-\(clock.nowMs)"
        func screen(_ id: String, title: String, label: String? = nil,
                    destination: String? = nil, welcome: Bool = false, payment: Bool = false) -> ScreenModel {
            let controls = label.map { [ResolvedTarget(id: "\(id)-control", kind: .screenControl,
                label: $0, region: .init(x: 0.2, y: 0.2, width: 0.3, height: 0.15),
                frameID: frame, sourceScreenID: id, expectedDestination: destination)] } ?? []
            return .init(id: id, frameID: frame, title: "SIMULATED " + title, controls: controls,
                         isPaymentScreen: payment, isWelcomeScreen: welcome,
                         total: payment ? "Fixture total: 8 dollars" : nil)
        }
        switch uiTestScenario {
        case .kioskWrongScreen:
            switch screenReads {
            case 1: return screen("fixture-menu", title: "café menu", label: "Tea", destination: "fixture-details")
            case 2: return screen("fixture-wrong", title: "wrong screen", label: "Back", destination: "fixture-menu")
            case 3: return screen("fixture-menu", title: "recovered menu", label: "Tea after Back", destination: "fixture-details")
            case 4: return screen("fixture-details", title: "tea details", label: "Add sandwich")
            default: return screen("fixture-payment", title: "payment", payment: true)
            }
        case .kioskReset:
            switch screenReads {
            case 1: return screen("fixture-menu", title: "café menu", label: "Tea")
            case 2: return screen("fixture-welcome", title: "welcome", welcome: true)
            case 3: return screen("fixture-restarted", title: "restarted menu", label: "Tea after restart")
            case 4: return screen("fixture-details", title: "tea details", label: "Add sandwich")
            default: return screen("fixture-payment", title: "payment", payment: true)
            }
        default: return nil
        }
    }
    #endif

    func readMenu(_ request: RequestContext, query: String) async throws -> MenuContent {
        .init(sections: [.init(title: "SIMULATED drinks", items: ["Tea, 3 dollars", "Coffee, 4 dollars"]),
                         .init(title: "SIMULATED food", items: ["Sandwich, 5 dollars"])])
    }
    func answerMenu(_ request: RequestContext, question: String, menu: MenuContent) async throws -> String {
        "This is fixture menu data. " + menu.sections.flatMap(\.items).joined(separator: ". ")
    }
    func verify(_ request: RequestContext, action: ActionKind, target: ResolvedTarget) async throws -> VerificationOutcome {
        #if DEBUG
        if uiTestScenario == .blockedDoor, action == .openDoor, !reportedBlockedDoor {
            reportedBlockedDoor = true
            return .doorBlocked
        }
        #endif
        // Require explicit user confirmation; a timer or a simulated hand overlap is not success.
        return .unavailable
    }
    func cancel(context: SessionContext) {}
}
