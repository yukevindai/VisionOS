import Foundation
import Tasks
@testable import VisionOS

/// SIMULATED probes for R2's existing adapter proposals. Teammate adapters can be injected into
/// this composition once their actual APIs arrive; these doubles do not validate those APIs,
/// camera geometry, offline recognition, audio playback, BLE delivery, or hardware endurance.
@MainActor
final class AdapterCompatibilityHarness {
    let clock: ManualAppClock
    let perception: FixturePerception
    let speech: RecordingSpeechScheduler
    let conversation: ControlledConversation
    let session: SessionCoordinator

    init(clock: ManualAppClock? = nil, conversation: ControlledConversation? = nil,
         framePolicy: FrameDeliveryPolicy? = nil, perceptionAdapter: (any PerceptionGuidanceClient)? = nil,
         speechAdapter: (any SpeechSchedulerClient)? = nil, conversationAdapter: (any ConversationClient)? = nil,
         evidenceAdapter: (any TargetEvidenceClient)? = nil) {
        let clock = clock ?? ManualAppClock()
        let conversation = conversation ?? ControlledConversation()
        self.clock = clock
        self.conversation = conversation
        perception = FixturePerception(clock: clock)
        speech = RecordingSpeechScheduler()
        let preferences = UserPreferences(defaults: UserDefaults(suiteName: UUID().uuidString)!)
        session = SessionCoordinator(simulated: true, preferences: preferences, diagnostics: Diagnostics(capacity: 16),
            bands: BandService(controller: nil, simulated: true, clock: clock), clock: clock,
            perception: perceptionAdapter ?? perception, speech: speechAdapter ?? speech,
            conversation: conversationAdapter ?? conversation, evidence: evidenceAdapter ?? FixtureEvidence(clock: clock),
            permissions: FixturePermissionClient(), framePolicy: framePolicy)
    }
}

/// Ignores cancellation intentionally, like a provider SDK that finishes only on its own callback.
/// Every test must release pending continuations after End, including on assertion failures.
@MainActor
final class ControlledConversation: ConversationClient {
    var onEvent: ((ConversationEvent) -> Void)?
    var onSend: ((FrameSnapshot) -> Void)?
    var onStart: (() -> Void)?
    var onCompletion: (() -> Void)?
    private(set) var context: SessionContext?
    private(set) var startedContexts: [SessionContext] = []
    private(set) var sentFrames: [FrameSnapshot] = []
    private(set) var maximumConcurrentSends = 0
    private(set) var activeSends = 0
    private(set) var muted = false
    var holdStart = false
    private var starts: [CheckedContinuation<Void, Never>] = []
    private var sends: [CheckedContinuation<Void, Never>] = []
    var pendingStartCount: Int { starts.count }
    var pendingSendCount: Int { sends.count }

    func start(context: SessionContext, audioInput: SharedAudioInput) async throws {
        startedContexts.append(context)
        self.context = context
        if holdStart {
            await withCheckedContinuation { continuation in
                starts.append(continuation)
                onStart?()
            }
        } else { onStart?() }
    }
    func updateContext(_ context: SessionContext) { self.context = context }
    func sendLatestFrame(_ frame: FrameSnapshot) async throws {
        activeSends += 1
        maximumConcurrentSends = max(maximumConcurrentSends, activeSends)
        sentFrames.append(frame)
        await withCheckedContinuation { continuation in
            sends.append(continuation)
            onSend?(frame)
        }
        activeSends -= 1
        onCompletion?()
    }
    func releaseFirstSend() {
        guard !sends.isEmpty else { return }
        sends.removeFirst().resume()
    }
    func releaseAll() {
        let pendingStarts = starts, pendingSends = sends
        starts.removeAll(); sends.removeAll()
        pendingStarts.forEach { $0.resume() }
        pendingSends.forEach { $0.resume() }
    }
    func setMicrophoneMuted(_ muted: Bool) { self.muted = muted }
    func interrupt(context: SessionContext) {}
    func end(context: SessionContext) {
        if self.context?.sessionID == context.sessionID { self.context = nil }
    }
    func emit(_ payload: ConversationPayload, context: SessionContext? = nil) {
        guard let context = context ?? self.context else { return }
        onEvent?(.init(context: context, eventID: UUID().uuidString, payload: payload))
    }
}

@MainActor
final class RecordingSpeechScheduler: SpeechSchedulerClient {
    var onCaption: ((SessionContext, String) -> Void)?
    var onSpeech: (() -> Void)?
    private(set) var calibrations: [TaskContext] = []
    private(set) var cancellations: [SessionContext] = []
    private(set) var localStops = 0
    private(set) var successes = 0
    func speak(_ text: String, priority: Int, context: SessionContext) {
        onCaption?(context, text)
        onSpeech?()
    }
    func enqueueAudio(_ audio: Data, context: SessionContext) {}
    func cancel(context: SessionContext) { cancellations.append(context) }
    func localStop(context: SessionContext) { localStops += 1 }
    func requestSuccess(_ result: TaskResult, context: SessionContext) { successes += 1 }
    func requestCalibration(patternID: UInt8, side: String?, task: TaskContext) throws {
        calibrations.append(task)
    }
}

@MainActor
final class ControlledTargetEvidence: TargetEvidenceClient {
    private let base: FixtureEvidence
    private var continuations: [CheckedContinuation<ResolvedTarget, Error>] = []
    private(set) var requests: [RequestContext] = []
    var onResolve: (() -> Void)?
    var onCompletion: (() -> Void)?
    var pendingCount: Int { continuations.count }
    init(clock: any AppClock) { base = FixtureEvidence(clock: clock) }
    func resolve(_ request: RequestContext, query: String, kind: TaskKind) async throws -> ResolvedTarget {
        requests.append(request)
        let result: ResolvedTarget = try await withCheckedThrowingContinuation { continuation in
            continuations.append(continuation)
            onResolve?()
        }
        onCompletion?()
        return result
    }
    func releaseAll() {
        let held = continuations
        continuations.removeAll()
        held.forEach { $0.resume(returning: TaskFixtures.target()) }
    }
    func readScreen(_ request: RequestContext) async throws -> TaskScreen { try await base.readScreen(request) }
    func readMenu(_ request: RequestContext, query: String) async throws -> MenuContent { try await base.readMenu(request, query: query) }
    func answerMenu(_ request: RequestContext, question: String, menu: MenuContent) async throws -> String {
        try await base.answerMenu(request, question: question, menu: menu)
    }
    func verify(_ request: RequestContext, action: ActionKind, target: ResolvedTarget) async throws -> VerificationOutcome { .unavailable }
    func cancel(context: SessionContext) {}
}
