import Foundation

/// A local policy boundary, not a wire decoder or an authentication system. The caller must
/// establish the helper's authenticated connection/lease, match reference image bytes to its
/// digest and rendered frame, and attest that `nowMs` is a local monotonic clock. Model targets
/// remain semantic proposals; their frame IDs and regions are never physical R1 evidence.
/// The candidate filter here does not replace TaskEngine's task-specific safety validation.
/// The coordinator must run that validation and current R1 reacquisition before guidance.
public struct FriendSelectionScope: Equatable, Sendable {
    public let sessionID: String
    public let sessionEpoch: String
    public let helperID: String
    public let helperConnectionID: String
    public let helperLeaseVersion: UInt64
    public let authorizationVersion: UInt64
    public init(sessionID: String, sessionEpoch: String, helperID: String,
                helperConnectionID: String, helperLeaseVersion: UInt64,
                authorizationVersion: UInt64) {
        self.sessionID = sessionID; self.sessionEpoch = sessionEpoch; self.helperID = helperID
        self.helperConnectionID = helperConnectionID; self.helperLeaseVersion = helperLeaseVersion
        self.authorizationVersion = authorizationVersion
    }
}

public struct FriendSelectionProposal: Equatable, Sendable {
    public let scope: FriendSelectionScope
    public let selectionID: String
    public let proposalGeneration: UInt64
    public let taskGeneration: UInt64
    public let referenceFrameID: String
    public let referenceDigest: String
    public let transportEpoch: String
    public let pointX: Double
    public let pointY: Double
    public init(scope: FriendSelectionScope, selectionID: String, proposalGeneration: UInt64,
                taskGeneration: UInt64,
                referenceFrameID: String, referenceDigest: String, transportEpoch: String,
                pointX: Double, pointY: Double) {
        self.scope = scope; self.selectionID = selectionID; self.proposalGeneration = proposalGeneration
        self.taskGeneration = taskGeneration
        self.referenceFrameID = referenceFrameID; self.referenceDigest = referenceDigest
        self.transportEpoch = transportEpoch; self.pointX = pointX; self.pointY = pointY
    }
}

public struct FriendSelectionResolution: Equatable, Sendable {
    public let proposal: FriendSelectionProposal
    public let promptID: String
    /// An owner-origin R1 frame identity supplied by the current-scene adapter. This is
    /// a semantic comparison context only; reacquisition still needs fresh R1 evidence.
    public let currentSceneFrameID: String
    public let target: ResolvedTarget
    public init(proposal: FriendSelectionProposal, promptID: String,
                currentSceneFrameID: String, target: ResolvedTarget) {
        self.proposal = proposal; self.promptID = promptID
        self.currentSceneFrameID = currentSceneFrameID; self.target = target
    }
}

/// Construct only at a trusted local owner-input ingress. Network transcripts, helper speech,
/// assistant echo and unclassified audio must enter as their own untrusted cases. Positive
/// acceptance requires the owner's spoken Yes; an accessible control may decline a prompt.
/// The reducer cannot authenticate an arbitrary caller that lies about this provenance.
public enum FriendConfirmationProvenance: Equatable, Sendable {
    case localOwnerSpeech
    case localOwnerAccessibleControl
    case helperSpeech
    case assistantEcho
    case ambiguousAudio
    case remoteMessage
}

public enum FriendConfirmationAnswer: Equatable, Sendable { case yes, no }

public enum FriendPromptPlaybackProvenance: Equatable, Sendable {
    case localR1Completion
    case queued
    case interrupted
    case remoteClaim
}

public struct FriendOwnerConfirmation: Equatable, Sendable {
    public let scope: FriendSelectionScope
    public let selectionID: String
    public let proposalGeneration: UInt64
    public let promptID: String
    public let provenance: FriendConfirmationProvenance
    public let answer: FriendConfirmationAnswer
    public init(scope: FriendSelectionScope, selectionID: String, proposalGeneration: UInt64,
                promptID: String, provenance: FriendConfirmationProvenance,
                answer: FriendConfirmationAnswer) {
        self.scope = scope; self.selectionID = selectionID
        self.proposalGeneration = proposalGeneration; self.promptID = promptID
        self.provenance = provenance; self.answer = answer
    }
}

public struct FriendSelectionAccepted: Equatable, Sendable {
    public let scope: FriendSelectionScope
    public let selectionID: String
    public let promptID: String
    public let proposalGeneration: UInt64
    public let target: ResolvedTarget
    public let oldTaskID: String
    public let oldTaskGeneration: UInt64
    public let newTaskID: String
    public let newTaskGeneration: UInt64
}

public enum FriendSelectionEffect: Equatable, Sendable {
    case cancelPending(selectionID: String)
    case requestResolution(FriendSelectionProposal)
    case requestPrompt(selectionID: String, promptID: String, target: ResolvedTarget)
    case allStopAndCancelOldTask(TaskContext)
    /// Publish this exact binding before allowing any new-generation worker/tool activity.
    /// The coordinator must complete ALL_STOP, then reacquire the target in this generation.
    case publishAcceptedBinding(FriendSelectionAccepted)
    case rejected
}

public struct FriendSelectionPolicy: Sendable {
    public static let maximumSafeGeneration: UInt64 = 9_007_199_254_740_991
    public static let resolutionWindowMs: Int64 = 15_000
    public static let promptWindowMs: Int64 = 10_000
    public static let confirmationWindowMs: Int64 = 15_000

    private struct Pending: Sendable {
        var proposal: FriendSelectionProposal
        var resolutionDeadline: Int64
        var resolution: FriendSelectionResolution?
        var promptDeadline: Int64?
        var confirmationDeadline: Int64?
    }

    public private(set) var scope: FriendSelectionScope
    public private(set) var task: TaskContext
    public private(set) var lastProposalGeneration: UInt64 = 0
    public private(set) var isEnded = false
    public private(set) var helperAuthorized = true
    public private(set) var activeFriendScope: FriendSelectionScope?
    public var pendingSelectionID: String? { pending?.proposal.selectionID }
    public var confirmationDeadlineMs: Int64? { pending?.confirmationDeadline }
    /// Schedule a local monotonic watchdog at this time; equality means expired.
    public var nextWakeupMs: Int64? {
        guard let item = pending else { return nil }
        return item.confirmationDeadline ?? item.promptDeadline ?? item.resolutionDeadline
    }
    private var pending: Pending?
    private var lastNowMs: Int64 = 0

    public init?(scope: FriendSelectionScope, task: TaskContext) {
        guard Self.valid(scope), (task.taskID.isEmpty || Self.validID(task.taskID)), task.sessionID == scope.sessionID,
              task.generation < Self.maximumSafeGeneration else { return nil }
        self.scope = scope; self.task = task
    }

    /// The native task controller remains authoritative, including ordinary task changes.
    /// Preserve the proposal watermark so a context update cannot replay old submissions.
    public mutating func synchronizeTask(_ context: TaskContext) -> [FriendSelectionEffect] {
        guard context.sessionID == scope.sessionID, context.generation >= task.generation,
              context.generation <= Self.maximumSafeGeneration else { return [.rejected] }
        guard context != task else { return [] }
        let effects = clearPending()
        task = context
        activeFriendScope = nil
        return effects
    }

    public mutating func cancelPendingProposal() -> [FriendSelectionEffect] { clearPending() }

    /// Caller serializes mutations. A later valid proposal supersedes only `pending`, never
    /// the active `task`. No effect here can start guidance or play media.
    public mutating func submit(_ proposal: FriendSelectionProposal, nowMs: Int64) -> [FriendSelectionEffect] {
        guard validTime(nowMs), !isEnded, helperAuthorized, proposal.scope == scope,
              Self.valid(proposal), proposal.taskGeneration == task.generation,
              proposal.proposalGeneration > lastProposalGeneration else { return [.rejected] }
        guard let deadline = Self.deadline(nowMs, Self.resolutionWindowMs) else { return [.rejected] }
        lastNowMs = nowMs
        var effects: [FriendSelectionEffect] = []
        if let old = pending { effects.append(.cancelPending(selectionID: old.proposal.selectionID)) }
        pending = Pending(proposal: proposal, resolutionDeadline: deadline)
        lastProposalGeneration = proposal.proposalGeneration
        effects.append(.requestResolution(proposal))
        return effects
    }

    public mutating func resolve(_ resolution: FriendSelectionResolution, nowMs: Int64) -> [FriendSelectionEffect] {
        guard validTime(nowMs), !isEnded, var item = pending,
              item.proposal == resolution.proposal, item.resolution == nil else { return [.rejected] }
        lastNowMs = nowMs
        guard nowMs < item.resolutionDeadline else { return expirePending() }
        guard Self.valid(resolution), let deadline = Self.deadline(nowMs, Self.promptWindowMs) else {
            return expirePending()
        }
        item.resolution = resolution; item.promptDeadline = deadline
        pending = item
        return [.requestPrompt(selectionID: resolution.proposal.selectionID,
                               promptID: resolution.promptID, target: resolution.target)]
    }

    /// Call only after R1 reports actual playback completion of this local owner prompt.
    /// Queuing, sending TTS, or receiving a remote playback assertion does not call this.
    public mutating func localPromptPlaybackCompleted(proposal: FriendSelectionProposal, promptID: String,
                                                       provenance: FriendPromptPlaybackProvenance,
                                                       nowMs: Int64) -> [FriendSelectionEffect] {
        guard !isEnded, var item = pending,
              item.proposal == proposal, item.resolution?.promptID == promptID else { return [.rejected] }
        if provenance == .interrupted {
            lastNowMs = max(lastNowMs, nowMs)
            return expirePending()
        }
        guard provenance == .localR1Completion, item.confirmationDeadline == nil,
              validTime(nowMs) else { return [.rejected] }
        lastNowMs = nowMs
        guard let promptDeadline = item.promptDeadline, nowMs < promptDeadline else { return expirePending() }
        guard let deadline = Self.deadline(nowMs, Self.confirmationWindowMs) else { return expirePending() }
        item.confirmationDeadline = deadline
        pending = item
        return []
    }

    public mutating func confirm(_ confirmation: FriendOwnerConfirmation,
                                 nowMs: Int64) -> [FriendSelectionEffect] {
        guard validTime(nowMs), !isEnded, let item = pending,
              confirmation.scope == scope,
              confirmation.selectionID == item.proposal.selectionID,
              confirmation.proposalGeneration == item.proposal.proposalGeneration,
              confirmation.promptID == item.resolution?.promptID,
              let deadline = item.confirmationDeadline,
              confirmation.provenance == .localOwnerSpeech ||
                (confirmation.answer == .no && confirmation.provenance == .localOwnerAccessibleControl)
              else { return [.rejected] }
        lastNowMs = nowMs
        guard nowMs < deadline else { return expirePending() }
        if confirmation.answer == .no { return expirePending() }
        guard let resolution = item.resolution, task.generation < Self.maximumSafeGeneration,
              let nextGeneration = Self.next(task.generation) else { return expirePending() }
        let newID = "friend:\(scope.sessionID):\(scope.sessionEpoch):\(item.proposal.proposalGeneration)"
        guard newID != task.taskID else { return expirePending() }
        let old = task
        let binding = FriendSelectionAccepted(scope: scope, selectionID: item.proposal.selectionID,
                                              promptID: resolution.promptID,
                                              proposalGeneration: item.proposal.proposalGeneration,
                                              target: resolution.target, oldTaskID: old.taskID,
                                              oldTaskGeneration: old.generation,
                                              newTaskID: newID, newTaskGeneration: nextGeneration)
        task = TaskContext(sessionID: old.sessionID, taskID: newID, generation: nextGeneration)
        activeFriendScope = scope
        pending = nil
        return [.allStopAndCancelOldTask(old), .publishAcceptedBinding(binding)]
    }

    /// Unified local Stop invalidates the prompt and generation. The app's Stop coordinator
    /// still owns actual output cancellation and physical ALL_STOP delivery.
    public mutating func stop(nowMs: Int64) -> [FriendSelectionEffect] {
        guard !isEnded else { return [.rejected] }
        lastNowMs = max(lastNowMs, nowMs)
        let old = task
        if let generation = Self.next(task.generation) {
            task = TaskContext(sessionID: old.sessionID, taskID: old.taskID, generation: generation)
        } else {
            // A fresh session/epoch is required before any more work. Stopping cannot fail.
            isEnded = true
        }
        activeFriendScope = nil
        let cancelled = clearPending()
        return cancelled + [.allStopAndCancelOldTask(old)]
    }

    public mutating func end(nowMs: Int64) -> [FriendSelectionEffect] {
        guard !isEnded else { return [.rejected] }
        lastNowMs = max(lastNowMs, nowMs)
        let old = task
        isEnded = true
        activeFriendScope = nil
        return clearPending() + [.allStopAndCancelOldTask(old)]
    }

    public mutating func invalidateHelper(nowMs: Int64) -> [FriendSelectionEffect] {
        guard !isEnded else { return [.rejected] }
        lastNowMs = max(lastNowMs, nowMs)
        helperAuthorized = false
        if activeFriendScope == scope { return stop(nowMs: nowMs) }
        return clearPending()
    }

    public mutating func tick(nowMs: Int64) -> [FriendSelectionEffect] {
        guard validTime(nowMs), !isEnded, let item = pending else { return [] }
        lastNowMs = nowMs
        let deadline = item.confirmationDeadline ?? item.promptDeadline ?? item.resolutionDeadline
        return nowMs >= deadline ? expirePending() : []
    }

    private mutating func clearPending() -> [FriendSelectionEffect] {
        guard let item = pending else { return [] }
        pending = nil
        return [.cancelPending(selectionID: item.proposal.selectionID)]
    }

    private mutating func expirePending() -> [FriendSelectionEffect] { clearPending() }

    private func validTime(_ nowMs: Int64) -> Bool { nowMs >= lastNowMs }

    private static func deadline(_ now: Int64, _ duration: Int64) -> Int64? {
        let (value, overflow) = now.addingReportingOverflow(duration)
        return overflow ? nil : value
    }

    private static func next(_ value: UInt64) -> UInt64? {
        value < maximumSafeGeneration ? value + 1 : nil
    }

    private static func validID(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= 256 && !value.unicodeScalars.contains(where: { $0.value < 32 })
    }

    private static func valid(_ scope: FriendSelectionScope) -> Bool {
        [scope.sessionID, scope.sessionEpoch, scope.helperID, scope.helperConnectionID].allSatisfy(validID)
        && scope.helperLeaseVersion > 0 && scope.helperLeaseVersion <= maximumSafeGeneration
        && scope.authorizationVersion > 0 && scope.authorizationVersion <= maximumSafeGeneration
    }

    private static func valid(_ proposal: FriendSelectionProposal) -> Bool {
        valid(proposal.scope) && proposal.proposalGeneration > 0
        && proposal.proposalGeneration <= maximumSafeGeneration
        && proposal.taskGeneration <= maximumSafeGeneration
        && [proposal.selectionID, proposal.referenceFrameID, proposal.referenceDigest,
            proposal.transportEpoch].allSatisfy(validID)
        && proposal.pointX.isFinite && proposal.pointY.isFinite
        && (0...1).contains(proposal.pointX) && (0...1).contains(proposal.pointY)
    }

    private static func valid(_ resolution: FriendSelectionResolution) -> Bool {
        let target = resolution.target
        guard validID(resolution.promptID), validID(resolution.currentSceneFrameID),
              validID(target.id), validID(target.label.trimmingCharacters(in: .whitespacesAndNewlines)),
              validID(target.frameID), target.frameID == resolution.currentSceneFrameID,
              target.frameID != resolution.proposal.referenceFrameID,
              target.region.isValid, target.safety == .ordinary,
              target.kind != .door || (target.doorAction != nil && target.doorActionEvidence && !target.isHingeSide),
              target.kind != .screenControl || target.sourceScreenID.map(validID) == true else { return false }
        return true
    }
}
