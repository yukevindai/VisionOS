import Foundation
import Tasks
import HapticLink

/// All protocols below are R2-facing adapter proposals. Map existing teammate implementations
/// to them; they are not claims that R1/R3 expose these exact names on the wire.
@MainActor
protocol PerceptionGuidanceClient: AnyObject {
    var onEvent: ((PerceptionEvent) -> Void)? { get set }
    var onFrame: ((FrameSnapshot) -> Void)? { get set }
    var onLocalCommand: ((SessionContext, LocalCommand) -> Void)? { get set }
    var onHapticRelease: ((ScheduledHapticRelease) -> Void)? { get set }
    func start(context: SessionContext, policy: CameraPolicy) async throws -> PerceptionReadiness
    func updateContext(_ context: SessionContext)
    func reacquire(_ request: RequestContext, target: ResolvedTarget) async throws -> PhysicalEvidence
    func guide(_ request: RequestContext, mode: GuidanceMode, target: ResolvedTarget, hand: GuidingHand) async throws -> TaskResponse
    func authorizePassage(_ request: RequestContext, target: ResolvedTarget) async throws -> PhysicalEvidence
    func cancelGuidance(context: SessionContext)
    func stop(context: SessionContext)
}

struct PerceptionReadiness {
    let camera: CameraCapabilities
    let audioInput: SharedAudioInput
    let supportsOfflineStop: Bool
    let supportsSpeechOnlyGuidance: Bool
}

enum PerceptionEvent {
    case trackingLost(SessionContext)
    case hazard(SessionContext)
    case targetLost(SessionContext)
    case cameraChanged(SessionContext, CameraCapabilities)
    case recovered(SessionContext)
    case hazardCleared(SessionContext, observedAtMs: Int64)
    case guidanceResponse(EventEnvelope, TaskResponse)
}

@MainActor
protocol SpeechSchedulerClient: AnyObject {
    var onCaption: ((SessionContext, String) -> Void)? { get set }
    func speak(_ text: String, priority: Int, context: SessionContext)
    func enqueueAudio(_ audio: Data, context: SessionContext)
    func cancel(context: SessionContext)
    func localStop(context: SessionContext)
    func requestSuccess(_ result: TaskResult, context: SessionContext)
    func requestCalibration(patternID: UInt8, side: String?, task: TaskContext) throws
}

struct ConversationEvent {
    let context: SessionContext
    let eventID: String
    let payload: ConversationPayload
}

enum ConversationPayload {
    case localOwnerUtterance(String)
    case intent(TaskRequest)
    case command(LocalCommand)
    case confirmation(promptID: String, answer: ConfirmationAnswer, explicitUserResponse: Bool)
    case calibrationAnswer(String, explicitUserResponse: Bool)
    case choice(requestID: String, value: String)
    case menuCommand(requestID: String, command: MenuCommand)
    case transcript(String)
    case audio(Data)
    case status(ActivityStatus)
    case disconnected
    case screenChanged
}

@MainActor
protocol ConversationClient: AnyObject {
    var onEvent: ((ConversationEvent) -> Void)? { get set }
    func start(context: SessionContext, audioInput: SharedAudioInput) async throws
    func updateContext(_ context: SessionContext)
    func sendLatestFrame(_ frame: FrameSnapshot) async throws
    func setMicrophoneMuted(_ muted: Bool)
    func interrupt(context: SessionContext)
    func end(context: SessionContext)
}

@MainActor
protocol TargetEvidenceClient: AnyObject {
    func resolve(_ request: RequestContext, query: String, kind: TaskKind) async throws -> ResolvedTarget
    func readScreen(_ request: RequestContext) async throws -> TaskScreen
    func readMenu(_ request: RequestContext, query: String) async throws -> MenuContent
    func answerMenu(_ request: RequestContext, question: String, menu: MenuContent) async throws -> String
    func verify(_ request: RequestContext, action: ActionKind, target: ResolvedTarget) async throws -> VerificationOutcome
    func cancel(context: SessionContext)
}
