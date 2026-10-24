import Foundation
import Tasks

/// Serial native integration boundary. Async operations retain their occupied slot until
/// the adapter returns, even after cancellation. All effects touching local safety are sync.
@MainActor
final class FriendAssistanceCoordinator {
    private let clock: any AppClock
    private let integration: FriendAssistanceIntegration
    var currentTask: (() -> TaskContext?)?
    var sessionAllowsSharing: (() -> Bool)?
    var mayPrompt: (() -> Bool)?
    var mayAccept: (() -> Bool)?
    var prepareAcceptance: ((FriendSelectionAccepted) -> Bool)?
    var completeAcceptance: ((FriendSelectionAccepted) -> Bool)?
    var pause: (() -> Void)?
    var localInput: ((FriendLocalInput) -> Void)?
    private(set) var scope: FriendSelectionScope?
    private(set) var lease: SharingAuthorizationLease?
    private(set) var selection: FriendSelectionPolicy?
    private(set) var pendingAcceptance: FriendSelectionAccepted?
    private var acceptedTaskID: String?
    private var watchdog: ScheduledWork?
    private var acceptanceDeadline: Int64?
    private var proposal: FriendSelectionProposal?
    private var prompt: FriendSelectionPrompt?
    private var promptStarted = false
    private var queuedRenewal: SharingLeaseRenewal?
    private var queuedResolution: FriendSelectionProposal?
    private var queuedEviction: SharingAuthorizationScope?
    private var renewalWork: Task<Void, Never>?
    private var resolutionWork: Task<Void, Never>?
    private var acceptanceWork: Task<Void, Never>?
    private var evictionWork: Task<Void, Never>?
    private var selectionTimes: [Int64] = []
    private var stopping = false
    private var terminalCloseRequested = false

    init(clock: any AppClock, integration: FriendAssistanceIntegration) {
        self.clock = clock; self.integration = integration
        integration.transport.onHelperEvent = { [weak self] in self?.receive($0) }
        integration.transport.onBackendUnavailable = { [weak self] in self?.backendUnavailable($0) }
        integration.transport.onAuthorizationLost = { [weak self] in self?.revoke($0) }
        integration.local.onOwnerInput = { [weak self] input in
            guard let self else { return }
            if case .selection(let answer) = input { self.confirm(answer) }
            else { self.localInput?(input) }
        }
    }

    /// Called only with a backend-authenticated grant after explicit owner sharing setup.
    /// Reusing a closed grant cannot reactivate sharing; replacement requires fresh authority.
    @discardableResult
    func configure(_ newScope: FriendSelectionScope) -> Bool {
        guard sessionAllowsSharing?() == true, let task = currentTask?(), task.sessionID == newScope.sessionID else { return false }
        if let old = scope, old.sessionID == newScope.sessionID {
            guard newScope.sessionEpoch != old.sessionEpoch ||
                    newScope.authorizationVersion > old.authorizationVersion ||
                    (newScope.authorizationVersion == old.authorizationVersion &&
                     newScope.helperLeaseVersion > old.helperLeaseVersion) else { return false }
        }
        guard FriendSelectionPolicy(scope: newScope, task: task) != nil,
              let newLease = try? SharingAuthorizationLease(scope: Self.authorization(newScope), renewalInstanceID: UUID().uuidString)
        else { return false }
        close()
        guard sessionAllowsSharing?() == true, let current = currentTask?(),
              let newSelection = FriendSelectionPolicy(scope: newScope, task: current) else { return false }
        scope = newScope; selection = newSelection; lease = newLease
        selectionTimes.removeAll()
        integration.local.setSharingAllowed(false, scope: newLease.scope)
        applyLease(.tick)
        armWatchdog()
        return true
    }

    func taskChanged() {
        guard pendingAcceptance == nil, let task = currentTask?() else { return }
        if acceptedTaskID != task.taskID { acceptedTaskID = nil }
        if var policy = selection {
            let effects = policy.synchronizeTask(task)
            selection = policy
            applySelection(effects)
        }
        pumpPrompt()
    }

    func preemptForStop() { integration.local.preemptHelperAudio() }

    /// Owner Stop cancels prompts and ACK waits, but a normal pause keeps human calling.
    func ownerStopped(endSharing: Bool) {
        guard !stopping else { terminalCloseRequested = terminalCloseRequested || endSharing; return }
        if endSharing { close(); return }
        stopping = true
        defer {
            stopping = false
            if terminalCloseRequested { terminalCloseRequested = false; close() }
            else { armWatchdog() }
        }
        cancelProposal()
        abortAcceptance()
        taskChanged()
    }

    func close() {
        guard !stopping else { terminalCloseRequested = true; return }
        stopping = true
        defer { stopping = false; terminalCloseRequested = false }
        cancelProposal()
        abortAcceptance()
        if var lease {
            let effects = lease.reduce(.end, nowMs: clock.nowMs)
            self.lease = lease
            applyLeaseEffects(effects)
        }
        renewalWork?.cancel(); queuedRenewal = nil
        watchdog?.cancel(); watchdog = nil
        selection = nil
    }

    private func receive(_ event: FriendHelperEvent) {
        checkDeadlines()
        if case .left(let sender) = event {
            if sender == scope { close() }
            return
        }
        guard authorized else { return }
        switch event {
        case .proposal(let sender, let incoming):
            guard sender == scope, incoming.scope == sender, pendingAcceptance == nil,
                  integration.local.isSharedMediaLive(scope: Self.authorization(sender)) else { return }
            taskChanged()
            let now = clock.nowMs
            selectionTimes.removeAll { now >= $0 && now - $0 >= 1_000 }
            guard selectionTimes.count < 2 else { return }
            guard var policy = selection else { return }
            let effects = policy.submit(incoming, nowMs: now)
            selection = policy
            if effects.contains(where: { if case .requestResolution = $0 { return true }; return false }) {
                selectionTimes.append(now)
            }
            applySelection(effects)
        case .pause(let sender):
            guard sender == scope else { return }
            pause?()
        case .left(let sender):
            guard sender == scope else { return }
            close()
        }
        armWatchdog()
    }

    private var authorized: Bool { lease?.permitsNewSelection(nowMs: clock.nowMs) == true }

    private func confirm(_ answer: FriendOwnerConfirmation) {
        checkDeadlines()
        guard authorized, pendingAcceptance == nil, acceptanceWork == nil,
              mayAccept?() == true, var policy = selection else { return }
        taskChanged()
        guard let current = selection, current.task == policy.task else { return }
        let previous = current
        let effects = policy.confirm(answer, nowMs: clock.nowMs)
        selection = policy
        if let binding = effects.compactMap({ effect -> FriendSelectionAccepted? in
            if case .publishAcceptedBinding(let binding) = effect { return binding }
            return nil
        }).first {
            if !startAcceptance(binding) {
                if authorized, scope == previous.scope { selection = previous }
                cancelProposal(); taskChanged()
            }
        } else { applySelection(effects) }
        armWatchdog()
    }

    private func applySelection(_ effects: [FriendSelectionEffect]) {
        for effect in effects {
            switch effect {
            case .cancelPending:
                clearProposalResources()
            case .requestResolution(let incoming):
                proposal = incoming; queuedResolution = incoming
                pumpResolution()
            case .requestPrompt(_, let promptID, let target):
                guard let proposal else { continue }
                // Kiosk requires a currently validated ScreenModel; do not fake that context.
                guard target.kind != .screenControl else { cancelProposal(); continue }
                prompt = .init(proposal: proposal, promptID: promptID, target: target)
                promptStarted = false
                pumpPrompt()
            case .allStopAndCancelOldTask:
                // The following binding executes one unified local Stop before any async work.
                break
            case .publishAcceptedBinding(let binding):
                _ = startAcceptance(binding)
            case .rejected: break
            }
        }
    }

    private func pumpResolution() {
        guard resolutionWork == nil, authorized, let next = queuedResolution,
              proposal == next, selection?.pendingSelectionID == next.selectionID else { return }
        queuedResolution = nil
        let transport = integration.transport
        resolutionWork = Task { [weak self] in
            let result = try? await transport.resolve(next)
            guard let self else { return }
            self.resolutionWork = nil
            self.checkDeadlines()
            if !Task.isCancelled, self.authorized, self.proposal == next, var policy = self.selection {
                if let result {
                    let effects = policy.resolve(result, nowMs: self.clock.nowMs)
                    self.selection = policy
                    self.applySelection(effects)
                } else { self.cancelProposal() }
            }
            self.pumpResolution(); self.armWatchdog()
        }
    }

    private func pumpPrompt() {
        guard !stopping, authorized, !promptStarted, let prompt, mayPrompt?() == true else { return }
        promptStarted = true
        integration.local.playPrompt(prompt) { [weak self] result in
            guard let self else { return }
            self.checkDeadlines()
            guard self.authorized, self.prompt == prompt, var policy = self.selection else { return }
            let effects = policy.localPromptPlaybackCompleted(proposal: prompt.proposal, promptID: prompt.promptID,
                                                              provenance: result, nowMs: self.clock.nowMs)
            self.selection = policy; self.applySelection(effects); self.armWatchdog()
        }
    }

    private func startAcceptance(_ binding: FriendSelectionAccepted) -> Bool {
        guard authorized, acceptanceWork == nil else { return false }
        clearProposalResources()
        pendingAcceptance = binding
        let (deadline, overflow) = clock.nowMs.addingReportingOverflow(5_000)
        guard !overflow, prepareAcceptance?(binding) == true,
              pendingAcceptance == binding, authorized else { abortAcceptance(); return false }
        acceptanceDeadline = deadline
        let transport = integration.transport
        acceptanceWork = Task { [weak self] in
            let reply = try? await transport.synchronizeAccepted(binding)
            guard let self else { return }
            self.acceptanceWork = nil
            self.checkDeadlines()
            guard !Task.isCancelled, self.authorized, self.pendingAcceptance == binding else { return }
            guard reply == binding, self.completeAcceptance?(binding) == true else {
                self.abortAcceptance(); self.armWatchdog(); return
            }
            self.pendingAcceptance = nil; self.acceptanceDeadline = nil
            self.acceptedTaskID = binding.newTaskID
            self.taskChanged(); self.armWatchdog()
        }
        return true
    }

    private func abortAcceptance() {
        pendingAcceptance = nil; acceptanceDeadline = nil
        acceptanceWork?.cancel()
        taskChanged()
    }

    private func clearProposalResources() {
        let oldPrompt = prompt, oldProposal = proposal
        prompt = nil; proposal = nil; promptStarted = false; queuedResolution = nil
        resolutionWork?.cancel()
        if let oldPrompt { integration.local.cancelPrompt(oldPrompt) }
        if let oldProposal { integration.transport.cancelSelection(oldProposal) }
    }

    private func cancelProposal() {
        if var policy = selection {
            let effects = policy.cancelPendingProposal()
            selection = policy; applySelection(effects)
        }
        clearProposalResources()
    }

    private func backendUnavailable(_ scope: SharingAuthorizationScope) {
        guard scope == lease?.scope else { return }
        applyLease(.backendUnavailable); armWatchdog()
    }
    private func revoke(_ scope: SharingAuthorizationScope) { applyLease(.revoked(scope)); armWatchdog() }

    private func applyLease(_ event: SharingLeaseEvent) {
        guard var lease else { return }
        let effects = lease.reduce(event, nowMs: clock.nowMs)
        self.lease = lease
        applyLeaseEffects(effects)
        if case .granted = event, authorized {
            integration.local.setSharingAllowed(true, scope: lease.scope)
        }
    }

    private func applyLeaseEffects(_ effects: [SharingLeaseEffect]) {
        // Local effects all run before any asynchronous remote request is scheduled.
        for effect in effects {
            switch effect {
            case .cancelFriendProposals: cancelProposal()
            case .pauseFriendInitiatedTask:
                let shouldPause = pendingAcceptance != nil ||
                    (acceptedTaskID != nil && currentTask?()?.taskID == acceptedTaskID)
                abortAcceptance()
                if shouldPause { pause?() }
            case .unpublishSharedMedia:
                if let lease { integration.local.setSharingAllowed(false, scope: lease.scope) }
            default: break
            }
        }
        for effect in effects {
            switch effect {
            case .requestRenewal(let request): queuedRenewal = request
            case .requestMediaEviction(let scope): queuedEviction = scope
            default: break
            }
        }
        pumpRenewal(); pumpEviction()
    }

    private func pumpRenewal() {
        guard renewalWork == nil, let request = queuedRenewal,
              lease?.pendingRenewal == request else { return }
        queuedRenewal = nil
        let transport = integration.transport
        renewalWork = Task { [weak self] in
            let grant = try? await transport.renew(request)
            guard let self else { return }
            self.renewalWork = nil
            if !Task.isCancelled {
                if let grant { self.applyLease(.granted(grant)) }
                else { self.applyLease(.renewalFailed(scope: request.scope, nonce: request.nonce)) }
            }
            self.pumpRenewal(); self.armWatchdog()
        }
    }

    private func pumpEviction() {
        guard evictionWork == nil, let scope = queuedEviction else { return }
        queuedEviction = nil
        let transport = integration.transport
        evictionWork = Task { [weak self] in
            await transport.evictHelper(scope)
            guard let self else { return }
            self.evictionWork = nil; self.pumpEviction()
        }
    }

    private func checkDeadlines() {
        applyLease(.tick)
        if let deadline = acceptanceDeadline, clock.nowMs >= deadline { abortAcceptance() }
        if var policy = selection {
            let effects = policy.tick(nowMs: clock.nowMs)
            selection = policy; applySelection(effects)
        }
    }

    private func armWatchdog() {
        watchdog?.cancel(); watchdog = nil
        let deadlines = [lease?.nextWakeupMs, selection?.nextWakeupMs, acceptanceDeadline].compactMap { $0 }
        guard let next = deadlines.min() else { return }
        watchdog = clock.schedule(afterMs: max(1, next - clock.nowMs)) { [weak self] in
            guard let self else { return }
            self.checkDeadlines(); self.pumpPrompt(); self.armWatchdog()
        }
    }

    private static func authorization(_ scope: FriendSelectionScope) -> SharingAuthorizationScope {
        .init(sessionID: scope.sessionID, sessionEpoch: scope.sessionEpoch, authorizationVersion: scope.authorizationVersion,
              helper: .init(connectionID: scope.helperConnectionID, leaseVersion: scope.helperLeaseVersion))
    }
}
