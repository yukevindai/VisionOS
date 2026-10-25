import Foundation
import Targets

// These are R2 adapter projections, not additions to the shared wire schema.
public enum TaskKind: String, Codable, Sendable { case button, object, kiosk, wallMenu, door, approach }
public enum GuidingHand: String, Codable, Sendable { case left, right }
public enum GuidanceMode: String, Codable, Sendable { case approach, hand }
public enum DoorAction: String, Codable, Sendable { case automatic, opener, push, pull, lever }
public enum StopReason: String, Sendable {
    case userPause, cancelled, ended, trackingLost, targetLost, handLost, hazard, bandDisconnected
    case backgrounded, audioInterrupted, microphoneMuted, geometryChanged, replaced
}
public enum TaskError: String, Codable, Sendable {
    case targetLost = "TARGET_LOST", handNotVisible = "HAND_NOT_VISIBLE", labelAmbiguous = "LABEL_AMBIGUOUS"
    case actionUnconfirmed = "ACTION_UNCONFIRMED", screenChanged = "SCREEN_CHANGED", kioskReset = "KIOSK_RESET"
    case paymentBoundary = "PAYMENT_BOUNDARY", lowConfidence = "LOW_CONFIDENCE", bandDisconnected = "BAND_DISCONNECTED"
    case providerTimeout = "PROVIDER_TIMEOUT"
}
public struct TaskConfiguration: Equatable, Sendable {
    public var freshnessMs: Int64
    public var requestTimeoutMs: Int64
    public var confirmationTimeoutMs: Int64
    /// Retries AFTER the initial attempt.
    public var maximumRetries: Int
    public init(freshnessMs: Int64 = 300, requestTimeoutMs: Int64 = 5_000,
                confirmationTimeoutMs: Int64 = 30_000, maximumRetries: Int = 3) {
        self.freshnessMs = max(1, freshnessMs)
        self.requestTimeoutMs = max(1, requestTimeoutMs)
        self.confirmationTimeoutMs = max(1, confirmationTimeoutMs)
        self.maximumRetries = max(0, maximumRetries)
    }
}
public struct TaskRequest: Equatable, Sendable {
    public let id: String
    public let kind: TaskKind
    public let query: String
    public init(id: String, kind: TaskKind, query: String) { self.id = id; self.kind = kind; self.query = query }
}
public struct TaskContext: Equatable, Hashable, Sendable {
    public let sessionID: String
    public let taskID: String
    public let generation: UInt64
    public init(sessionID: String, taskID: String, generation: UInt64) {
        self.sessionID = sessionID; self.taskID = taskID; self.generation = generation
    }
}
public struct RequestContext: Equatable, Hashable, Sendable {
    public let task: TaskContext
    public let requestID: String
    public let issuedAtMs: Int64
    public let expiresAtMs: Int64
    public init(task: TaskContext, requestID: String, issuedAtMs: Int64, expiresAtMs: Int64) {
        self.task = task; self.requestID = requestID; self.issuedAtMs = issuedAtMs; self.expiresAtMs = expiresAtMs
    }
}
public enum EventSource: String, Sendable { case user, r1, r3, model }
public struct EventEnvelope: Equatable, Sendable {
    public let schemaVersion: Int
    public let eventID: String
    public let context: RequestContext
    public let monotonicMs: Int64
    public let expiresAtMs: Int64
    public let source: EventSource
    public init(schemaVersion: Int = 3, eventID: String, context: RequestContext,
                monotonicMs: Int64, expiresAtMs: Int64, source: EventSource) {
        self.schemaVersion = schemaVersion; self.eventID = eventID; self.context = context
        self.monotonicMs = monotonicMs; self.expiresAtMs = expiresAtMs; self.source = source
    }
}
public typealias ImageRegion = Targets.BoundingBox
public enum TargetKind: String, Sendable { case button, object, screenControl, handle, opener, door }
public enum ControlSafety: String, Sendable { case ordinary, ambiguous, emergency, payment, purchaseAuthorization, unknownCheckout, unassessedObject }
public struct ResolvedTarget: Equatable, Sendable {
    public let id: String
    public let kind: TargetKind
    public let label: String
    public let region: ImageRegion
    public let frameID: String
    public let safety: ControlSafety
    public let requiresApproach: Bool
    public let caution: String?
    public let doorAction: DoorAction?
    public let doorActionEvidence: Bool
    public let isHingeSide: Bool
    public let sourceScreenID: String?
    public let expectedDestination: String?
    public init(id: String, kind: TargetKind, label: String, region: ImageRegion, frameID: String,
                safety: ControlSafety = .ordinary, requiresApproach: Bool = false, caution: String? = nil,
                doorAction: DoorAction? = nil, doorActionEvidence: Bool = false, isHingeSide: Bool = false,
                sourceScreenID: String? = nil, expectedDestination: String? = nil) {
        self.id = id; self.kind = kind; self.label = label; self.region = region; self.frameID = frameID
        self.safety = safety; self.requiresApproach = requiresApproach; self.caution = caution
        self.doorAction = doorAction; self.doorActionEvidence = doorActionEvidence; self.isHingeSide = isHingeSide
        self.sourceScreenID = sourceScreenID; self.expectedDestination = expectedDestination
    }
}
/// Local R1 observation. A provider's historical target must never be copied into this type with a new timestamp.
public struct PhysicalEvidence: Equatable, Sendable {
    public let targetID: String
    public let frameID: String
    public let observedAtMs: Int64
    public let targetVisible: Bool
    public let handVisible: Bool
    public let trackingNormal: Bool
    public let depthUsable: Bool
    public let freeSpaceObserved: Bool
    public let outputReady: Bool
    public let hazardPresent: Bool
    public let distanceM: Double?
    public let suspectedStep: Bool
    public init(targetID: String, frameID: String, observedAtMs: Int64, targetVisible: Bool = false,
                handVisible: Bool = false, trackingNormal: Bool = false, depthUsable: Bool = false,
                freeSpaceObserved: Bool = false, outputReady: Bool = false, hazardPresent: Bool = false,
                distanceM: Double? = nil, suspectedStep: Bool = false) {
        self.targetID = targetID; self.frameID = frameID; self.observedAtMs = observedAtMs
        self.targetVisible = targetVisible; self.handVisible = handVisible; self.trackingNormal = trackingNormal
        self.depthUsable = depthUsable; self.freeSpaceObserved = freeSpaceObserved; self.outputReady = outputReady
        self.hazardPresent = hazardPresent; self.distanceM = distanceM; self.suspectedStep = suspectedStep
    }
}
public enum VerificationKind: String, Codable, Sendable { case observed, userConfirmed = "user_confirmed", unverified }
public enum ActionKind: String, Sendable { case press, grasp, tap, openDoor }
public enum ObservationKind: String, Sendable { case buttonIndicatorChanged, objectHandCoMotion, screenTransition, doorOpening }
public enum VerificationProvenance: String, Sendable { case r1Detector, implementedVisualVerifier, modelInterpretation }
public struct VerificationEvidence: Equatable, Sendable {
    public let targetID: String
    public let actionRequestID: String
    public let kind: ObservationKind
    public let provenance: VerificationProvenance
    public let beforeFrameID: String
    public let afterFrameID: String
    public let beforeMs: Int64
    public let afterMs: Int64
    public let isCertain: Bool
    public init(targetID: String, actionRequestID: String, kind: ObservationKind,
                provenance: VerificationProvenance, beforeFrameID: String, afterFrameID: String,
                beforeMs: Int64, afterMs: Int64, isCertain: Bool = true) {
        self.targetID = targetID; self.actionRequestID = actionRequestID; self.kind = kind; self.provenance = provenance
        self.beforeFrameID = beforeFrameID; self.afterFrameID = afterFrameID; self.beforeMs = beforeMs; self.afterMs = afterMs
        self.isCertain = isCertain
    }
}
public enum VerificationOutcome: Equatable, Sendable { case observed(VerificationEvidence), unavailable, uncertain, failed, doorBlocked }
public enum GuidanceStatus: String, Sendable { case guiding, onTarget, lost, failed }
public struct TaskScreen: Equatable, Sendable {
    public let id: String
    public let frameID: String
    public let title: String
    public let controls: [ResolvedTarget]
    public let isPaymentScreen: Bool
    public let isWelcomeScreen: Bool
    public let total: String?
    public let transitionEvidence: VerificationEvidence?
    public init(id: String, frameID: String, title: String, controls: [ResolvedTarget], isPaymentScreen: Bool = false,
                isWelcomeScreen: Bool = false, total: String? = nil, transitionEvidence: VerificationEvidence? = nil) {
        self.id = id; self.frameID = frameID; self.title = title; self.controls = controls
        self.isPaymentScreen = isPaymentScreen; self.isWelcomeScreen = isWelcomeScreen; self.total = total
        self.transitionEvidence = transitionEvidence
    }
}
public struct MenuSection: Equatable, Sendable {
    public let title: String
    public let items: [String]
    public init(title: String, items: [String]) { self.title = title; self.items = items }
}
public struct MenuContent: Equatable, Sendable {
    public let sections: [MenuSection]
    public let isBlurry: Bool
    public let isCutOff: Bool
    public init(sections: [MenuSection], isBlurry: Bool = false, isCutOff: Bool = false) {
        self.sections = sections; self.isBlurry = isBlurry; self.isCutOff = isCutOff
    }
}
public enum ConfirmationPurpose: String, Sendable { case actionResult, resumeGuidance, doorAction, restartKiosk, objectGuidance }
public struct PendingConfirmation: Equatable, Sendable {
    public let context: RequestContext
    public let purpose: ConfirmationPurpose
    public let question: String
    public var promptID: String { context.requestID }
}
public enum ConfirmationAnswer: Equatable, Sendable { case yes, no, doorAction(DoorAction) }
public enum MenuCommand: Equatable, Sendable { case repeatOptions, more, back, question(String) }
public enum TaskResponse: Equatable, Sendable {
    case target(ResolvedTarget)
    case physical(PhysicalEvidence)
    case guidance(mode: GuidanceMode, targetID: String, status: GuidanceStatus, evidence: PhysicalEvidence?)
    case verification(VerificationOutcome)
    case screen(TaskScreen)
    case screenChanged
    case menu(MenuContent)
    case menuAnswer(String)
    case choice(String)
    case menuCommand(MenuCommand)
    case confirmation(promptID: String, answer: ConfirmationAnswer)
    /// Only R1 can release a passage cue. R2 never speaks movement instructions itself.
    case passageAuthorized(PhysicalEvidence)
    case unavailable(TaskError)
}
public enum TaskEvent: Equatable, Sendable {
    case start(TaskRequest)
    case stop(StopReason)
    case resume
    case receive(EventEnvelope, TaskResponse)
    /// Timeouts only fail, pause, or ask for confirmation; they never mark success.
    case tick
}
public enum TaskPhase: String, Sendable {
    case idle, resolving, reacquiring, approaching, guiding, verifying, readingScreen, choosing
    case readingMenu, discussingMenu, answeringMenu, awaitingConfirmation, awaitingPassage, paused, completed, failed, cancelled
}
public enum ResultStatus: String, Codable, Sendable { case completed, failed, cancelled, handoff }
public struct TaskResult: Equatable, Sendable {
    public let context: TaskContext
    public let status: ResultStatus
    public let verification: VerificationKind
    public let error: TaskError?
}
public enum Rejection: String, Sendable {
    case invalidRequest, unsupportedState, malformed, stale, duplicate, wrongContext, wrongRequest, wrongSource, wrongTarget, unsafeTarget, missingEvidence
}
public enum TaskEffect: Equatable, Sendable {
    case resolveTarget(RequestContext, query: String)
    case reacquire(RequestContext, target: ResolvedTarget)
    case requestGuidance(RequestContext, mode: GuidanceMode, target: ResolvedTarget, hand: GuidingHand)
    case requestVerification(RequestContext, action: ActionKind, target: ResolvedTarget)
    case readScreen(RequestContext)
    case readMenu(RequestContext, query: String)
    case answerMenuQuestion(RequestContext, question: String, menu: MenuContent)
    case awaitChoice(RequestContext)
    case awaitMenuCommand(RequestContext)
    case requestPassage(RequestContext, target: ResolvedTarget)
    case speak(text: String, priority: Int)
    case askConfirmation(PendingConfirmation)
    case cancelWork(TaskContext)
    case allStop(TaskContext)
    case successFeedback(TaskResult)
    case finished(TaskResult)
    case rejected(Rejection)
}
public struct TaskState: Equatable, Sendable {
    public internal(set) var context: TaskContext
    public internal(set) var request: TaskRequest?
    public internal(set) var phase: TaskPhase = .idle
    public internal(set) var target: ResolvedTarget?
    public internal(set) var pendingRequest: RequestContext?
    public internal(set) var confirmation: PendingConfirmation?
    public internal(set) var screen: TaskScreen?
    public internal(set) var menu: MenuContent?
    public internal(set) var retries = 0
    public internal(set) var result: TaskResult?
    public internal(set) var lastStop: StopReason?
    public internal(set) var lastVerification: VerificationKind = .unverified
    public internal(set) var sectionIndex = 0
    public internal(set) var selectedDoorAction: DoorAction?
}
