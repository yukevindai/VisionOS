import Foundation
import Tasks
import HapticLink

@MainActor
final class UnavailablePerception: PerceptionGuidanceClient {
    var onEvent: ((PerceptionEvent) -> Void)?
    var onFrame: ((FrameSnapshot) -> Void)?
    var onLocalCommand: ((SessionContext, LocalCommand) -> Void)?
    var onHapticRelease: ((ScheduledHapticRelease) -> Void)?
    func start(context: SessionContext, policy: CameraPolicy) async throws -> PerceptionReadiness { throw IntegrationUnavailable(component: "R1 perception and shared audio") }
    func updateContext(_ context: SessionContext) {}
    func reacquire(_ request: RequestContext, target: ResolvedTarget) async throws -> PhysicalEvidence { throw IntegrationUnavailable(component: "R1 evidence") }
    func guide(_ request: RequestContext, mode: GuidanceMode, target: ResolvedTarget, hand: GuidingHand) async throws -> TaskResponse { throw IntegrationUnavailable(component: "R1 guidance") }
    func authorizePassage(_ request: RequestContext, target: ResolvedTarget) async throws -> PhysicalEvidence { throw IntegrationUnavailable(component: "R1 passage clearance") }
    func cancelGuidance(context: SessionContext) {}
    func stop(context: SessionContext) {}
}

@MainActor
final class UnavailableSpeech: SpeechSchedulerClient {
    var onCaption: ((SessionContext, String) -> Void)?
    func speak(_ text: String, priority: Int, context: SessionContext) {}
    func enqueueAudio(_ audio: Data, context: SessionContext) {}
    func cancel(context: SessionContext) {}
    func localStop(context: SessionContext) {}
    func requestSuccess(_ result: TaskResult, context: SessionContext) {}
    func requestCalibration(patternID: UInt8, side: String?, task: TaskContext) throws { throw IntegrationUnavailable(component: "R1 calibration scheduler") }
}

@MainActor
final class UnavailableConversation: ConversationClient {
    var onEvent: ((ConversationEvent) -> Void)?
    func start(context: SessionContext, audioInput: SharedAudioInput) async throws { throw IntegrationUnavailable(component: "R3 conversation") }
    func updateContext(_ context: SessionContext) {}
    func sendLatestFrame(_ frame: FrameSnapshot) async throws { throw IntegrationUnavailable(component: "R3 frame input") }
    func setMicrophoneMuted(_ muted: Bool) {}
    func interrupt(context: SessionContext) {}
    func end(context: SessionContext) {}
}

@MainActor
final class UnavailableEvidence: TargetEvidenceClient {
    func resolve(_ request: RequestContext, query: String, kind: TaskKind) async throws -> ResolvedTarget { throw IntegrationUnavailable(component: "R3 target resolution") }
    func readScreen(_ request: RequestContext) async throws -> ScreenModel { throw IntegrationUnavailable(component: "R3 screen interpretation") }
    func readMenu(_ request: RequestContext, query: String) async throws -> MenuContent { throw IntegrationUnavailable(component: "R3 menu interpretation") }
    func answerMenu(_ request: RequestContext, question: String, menu: MenuContent) async throws -> String { throw IntegrationUnavailable(component: "R3 menu conversation") }
    func verify(_ request: RequestContext, action: ActionKind, target: ResolvedTarget) async throws -> VerificationOutcome { .unavailable }
    func cancel(context: SessionContext) {}
}
