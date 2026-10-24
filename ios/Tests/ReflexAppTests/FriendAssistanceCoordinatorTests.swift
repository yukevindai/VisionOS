import Foundation
import XCTest
import Tasks
@testable import Reflex

/// SIMULATED adapter tests. They exercise local ordering and authority only, never live media.
@MainActor
private final class HeldFriendTransport: FriendAssistanceTransport {
    var onHelperEvent: ((FriendHelperEvent) -> Void)?
    var onAuthorizationLost: ((SharingAuthorizationScope) -> Void)?
    var onBackendUnavailable: ((SharingAuthorizationScope) -> Void)?
    private(set) var renewals: [SharingLeaseRenewal] = []
    private(set) var resolutions: [FriendSelectionProposal] = []
    private(set) var acknowledgements: [FriendSelectionAccepted] = []
    private(set) var cancelled: [FriendSelectionProposal] = []
    private(set) var evictions: [SharingAuthorizationScope] = []
    var onEvict: (() -> Void)?
    private var renewalWaits: [CheckedContinuation<SharingLeaseGrant, Error>] = []
    private var resolutionWaits: [CheckedContinuation<FriendSelectionResolution, Error>] = []
    private var acknowledgementWaits: [CheckedContinuation<FriendSelectionAccepted, Error>] = []
    private var evictionWaits: [CheckedContinuation<Void, Never>] = []
    var pendingRenewals: Int { renewalWaits.count }
    var pendingResolutions: Int { resolutionWaits.count }
    var pendingAcknowledgements: Int { acknowledgementWaits.count }

    func renew(_ request: SharingLeaseRenewal) async throws -> SharingLeaseGrant {
        renewals.append(request)
        return try await withCheckedThrowingContinuation { renewalWaits.append($0) }
    }
    func resolve(_ proposal: FriendSelectionProposal) async throws -> FriendSelectionResolution {
        resolutions.append(proposal)
        return try await withCheckedThrowingContinuation { resolutionWaits.append($0) }
    }
    func synchronizeAccepted(_ binding: FriendSelectionAccepted) async throws -> FriendSelectionAccepted {
        acknowledgements.append(binding)
        return try await withCheckedThrowingContinuation { acknowledgementWaits.append($0) }
    }
    func cancelSelection(_ proposal: FriendSelectionProposal) { cancelled.append(proposal) }
    func evictHelper(_ scope: SharingAuthorizationScope) async {
        evictions.append(scope); onEvict?()
        await withCheckedContinuation { evictionWaits.append($0) }
    }
    func releaseRenewal(validForMs: Int64 = 15_000, scope: SharingAuthorizationScope? = nil) {
        guard !renewalWaits.isEmpty, !renewals.isEmpty else { return }
        let request = renewals[renewals.count - renewalWaits.count]
        renewalWaits.removeFirst().resume(returning: .init(scope: scope ?? request.scope,
                                                            nonce: request.nonce, validForMs: validForMs))
    }
    func releaseResolution(_ resolution: FriendSelectionResolution) {
        guard !resolutionWaits.isEmpty else { return }
        resolutionWaits.removeFirst().resume(returning: resolution)
    }
    func releaseAcknowledgement(_ reply: FriendSelectionAccepted? = nil) {
        guard !acknowledgementWaits.isEmpty, let accepted = reply ?? acknowledgements.last else { return }
        acknowledgementWaits.removeFirst().resume(returning: accepted)
    }
    func releaseAll() {
        while !renewalWaits.isEmpty { releaseRenewal() }
        while !resolutionWaits.isEmpty {
            let proposal = resolutions[resolutions.count - resolutionWaits.count]
            releaseResolution(.init(proposal: proposal, promptID: "released",
                                    currentSceneFrameID: "current-scene",
                                    target: TaskFixtures.target(id: "released", kind: .object,
                                                                label: "SIMULATED released", frameID: "current-scene")))
        }
        while !acknowledgementWaits.isEmpty { releaseAcknowledgement() }
        let held = evictionWaits; evictionWaits.removeAll(); held.forEach { $0.resume() }
    }
}

@MainActor
private final class RecordingFriendLocal: FriendAssistanceLocalClient {
    var onOwnerInput: ((FriendLocalInput) -> Void)?
    private(set) var prompts: [FriendSelectionPrompt] = []
    private(set) var cancellations: [FriendSelectionPrompt] = []
    private(set) var sharingChanges: [Bool] = []
    private(set) var preemptions = 0
    var onPreempt: (() -> Void)?
    var onCancel: (() -> Void)?
    private var completions: [@MainActor (FriendPromptPlaybackProvenance) -> Void] = []
    var isSharing = false
    func playPrompt(_ prompt: FriendSelectionPrompt,
                    completion: @escaping @MainActor (FriendPromptPlaybackProvenance) -> Void) {
        prompts.append(prompt); completions.append(completion)
    }
    func cancelPrompt(_ prompt: FriendSelectionPrompt) {
        cancellations.append(prompt)
        let callback = onCancel; onCancel = nil; callback?()
    }
    func setSharingAllowed(_ allowed: Bool, scope: SharingAuthorizationScope) {
        isSharing = allowed; sharingChanges.append(allowed)
    }
    func isSharedMediaLive(scope: SharingAuthorizationScope) -> Bool { isSharing }
    func preemptHelperAudio() {
        preemptions += 1
        let callback = onPreempt; onPreempt = nil; callback?()
    }
    func completePrompt(_ provenance: FriendPromptPlaybackProvenance = .localR1Completion) {
        guard !completions.isEmpty else { return }
        let completion = completions[0]
        if provenance == .localR1Completion || provenance == .interrupted { completions.removeFirst() }
        completion(provenance)
    }
}

/// SIMULATED R1 callback that sends one synchronous local Stop during Stop speech.
@MainActor
private final class StopOnSpeechScheduler: SpeechSchedulerClient {
    let base: RecordingSpeechScheduler
    var onLocalStop: (() -> Void)?
    private var fired = false
    init(base: RecordingSpeechScheduler) { self.base = base }
    var onCaption: ((SessionContext, String) -> Void)? {
        get { base.onCaption }
        set { base.onCaption = newValue }
    }
    func speak(_ text: String, priority: Int, context: SessionContext) {
        base.speak(text, priority: priority, context: context)
    }
    func enqueueAudio(_ audio: Data, context: SessionContext) { base.enqueueAudio(audio, context: context) }
    func cancel(context: SessionContext) { base.cancel(context: context) }
    func localStop(context: SessionContext) {
        base.localStop(context: context)
        guard !fired else { return }
        fired = true
        onLocalStop?()
    }
    func requestSuccess(_ result: TaskResult, context: SessionContext) {
        base.requestSuccess(result, context: context)
    }
    func requestCalibration(patternID: UInt8, side: String?, task: TaskContext) throws {
        try base.requestCalibration(patternID: patternID, side: side, task: task)
    }
}

@MainActor
final class FriendAssistanceCoordinatorTests: XCTestCase {
    private struct Harness {
        let session: SessionCoordinator
        let clock: ManualAppClock
        let perception: FixturePerception
        let conversation: FixtureConversation
        let speech: RecordingSpeechScheduler
        let stopOnSpeech: StopOnSpeechScheduler?
        let transport: HeldFriendTransport
        let local: RecordingFriendLocal
    }

    private func makeHarness(evidence: (any TargetEvidenceClient)? = nil,
                             stopDuringSpeech: Bool = false) -> Harness {
        let clock = ManualAppClock()
        let preferences = UserPreferences(defaults: UserDefaults(suiteName: UUID().uuidString)!)
        preferences.setupComplete = true
        let perception = FixturePerception(clock: clock)
        let conversation = FixtureConversation()
        let speech = RecordingSpeechScheduler()
        let stopOnSpeech = stopDuringSpeech ? StopOnSpeechScheduler(base: speech) : nil
        let speechAdapter: any SpeechSchedulerClient
        if let stopOnSpeech { speechAdapter = stopOnSpeech }
        else { speechAdapter = speech }
        let transport = HeldFriendTransport()
        let local = RecordingFriendLocal()
        let session = SessionCoordinator(simulated: true, preferences: preferences,
            diagnostics: Diagnostics(capacity: 16),
            bands: BandService(controller: nil, simulated: true, clock: clock), clock: clock,
            perception: perception, speech: speechAdapter, conversation: conversation,
            evidence: evidence ?? FixtureEvidence(clock: clock), permissions: FixturePermissionClient(),
            friends: .init(transport: transport, local: local))
        return .init(session: session, clock: clock, perception: perception,
                     conversation: conversation, speech: speech, stopOnSpeech: stopOnSpeech,
                     transport: transport, local: local)
    }

    private func settle() async { for _ in 0..<80 { await Task.yield() } }
    private func scope(_ h: Harness, epoch: String = "epoch", version: UInt64 = 1) -> FriendSelectionScope {
        .init(sessionID: h.session.context?.sessionID ?? "missing-session", sessionEpoch: epoch,
              helperID: "helper", helperConnectionID: "connection",
              helperLeaseVersion: 1, authorizationVersion: version)
    }
    private func proposal(_ h: Harness, scope: FriendSelectionScope,
                          generation: UInt64 = 1) -> FriendSelectionProposal {
        .init(scope: scope, selectionID: "selection-\(generation)",
              proposalGeneration: generation, taskGeneration: h.session.taskState?.context.generation ?? UInt64.max,
              referenceFrameID: "rendered-\(generation)", referenceDigest: "digest-\(generation)",
              transportEpoch: "transport", pointX: 0.4, pointY: 0.5)
    }
    private func resolution(_ proposal: FriendSelectionProposal, id: String = "exact-cup") -> FriendSelectionResolution {
        .init(proposal: proposal, promptID: "prompt-\(proposal.proposalGeneration)",
              currentSceneFrameID: "current-scene",
              target: TaskFixtures.target(id: id, kind: .object, label: "SIMULATED cup",
                                          frameID: "current-scene"))
    }
    private func answer(_ proposal: FriendSelectionProposal,
                        provenance: FriendConfirmationProvenance = .localOwnerSpeech) -> FriendOwnerConfirmation {
        .init(scope: proposal.scope, selectionID: proposal.selectionID,
              proposalGeneration: proposal.proposalGeneration,
              promptID: "prompt-\(proposal.proposalGeneration)",
              provenance: provenance, answer: .yes)
    }
    private func authorize(_ h: Harness, scope: FriendSelectionScope) async {
        XCTAssertTrue(h.session.enableFriendSharing(authenticatedScope: scope))
        await settle()
        XCTAssertEqual(h.transport.pendingRenewals, 1)
        h.transport.releaseRenewal()
        await settle()
        XCTAssertTrue(h.local.isSharing)
    }
    private func proposeAndPrompt(_ h: Harness, scope: FriendSelectionScope,
                                  generation: UInt64 = 1) async -> FriendSelectionProposal {
        let selected = proposal(h, scope: scope, generation: generation)
        h.transport.onHelperEvent?(.proposal(authenticatedScope: scope, selected))
        await settle()
        XCTAssertEqual(h.transport.pendingResolutions, 1)
        h.transport.releaseResolution(resolution(selected))
        await settle()
        XCTAssertEqual(h.local.prompts.last?.proposal, selected)
        return selected
    }
    private func finish(_ h: Harness) async {
        h.session.stop(.userEnd)
        await settle()
        h.transport.releaseAll()
        await settle()
        h.transport.releaseAll()
        await settle()
    }

    func testSimulatedIdleAcceptedObjectWaitsForWorkerACKAndFreshGeneration() async {
        let h = makeHarness(); h.session.start(); await settle()
        let trusted = scope(h)
        await authorize(h, scope: trusted)
        let selected = await proposeAndPrompt(h, scope: trusted)
        guard let old = h.session.taskState?.context else {
            XCTFail("Missing initial task state"); await finish(h); return
        }
        h.local.onOwnerInput?(.selection(answer(selected, provenance: .helperSpeech)))
        h.local.onOwnerInput?(.selection(answer(selected, provenance: .remoteMessage)))
        XCTAssertNil(h.session.friendAssistance?.pendingAcceptance)
        h.local.completePrompt(.queued)
        h.local.onOwnerInput?(.selection(answer(selected)))
        XCTAssertNil(h.session.friendAssistance?.pendingAcceptance)
        h.local.completePrompt()
        h.local.onOwnerInput?(.selection(answer(selected)))
        await settle()
        XCTAssertEqual(h.transport.pendingAcknowledgements, 1)
        XCTAssertEqual(h.session.phase, .paused)
        XCTAssertNil(h.session.taskState?.target)
        XCTAssertEqual(h.session.taskState?.context.generation, old.generation + 1)
        XCTAssertGreaterThan(h.speech.localStops, 0)
        h.transport.releaseAcknowledgement()
        await settle()
        XCTAssertEqual(h.session.taskState?.context.taskID, h.transport.acknowledgements.last?.newTaskID)
        XCTAssertEqual(h.session.taskState?.context.generation, old.generation + 1)
        XCTAssertEqual(h.session.bands.desiredContext?.taskID, h.transport.acknowledgements.last?.newTaskID)
        XCTAssertEqual(h.session.bands.desiredContext?.generation, UInt16(exactly: old.generation + 1))
        XCTAssertEqual(h.session.taskState?.target?.id, "exact-cup")
        XCTAssertNotEqual(h.session.taskState?.phase, .resolving)
        await finish(h)
    }

    func testSimulatedStopDuringHeldACKRejectsLateReply() async {
        let h = makeHarness(); h.session.start(); await settle()
        let trusted = scope(h); await authorize(h, scope: trusted)
        let selected = await proposeAndPrompt(h, scope: trusted)
        h.local.completePrompt(); h.local.onOwnerInput?(.selection(answer(selected)))
        await settle()
        XCTAssertEqual(h.transport.pendingAcknowledgements, 1)
        h.session.stop(.localStop)
        guard let stopped = h.session.taskState?.context else {
            XCTFail("Missing stopped task state"); await finish(h); return
        }
        XCTAssertNil(h.session.friendAssistance?.pendingAcceptance)
        h.transport.releaseAcknowledgement(); await settle()
        XCTAssertEqual(h.session.phase, .paused)
        XCTAssertEqual(h.session.taskState?.context, stopped)
        XCTAssertNil(h.session.taskState?.target)
        await finish(h)
    }

    func testSimulatedOwnerPauseDuringHeldACKRejectsLateReply() async {
        let h = makeHarness(); h.session.start(); await settle()
        let trusted = scope(h); await authorize(h, scope: trusted)
        let selected = await proposeAndPrompt(h, scope: trusted)
        h.local.completePrompt(); h.local.onOwnerInput?(.selection(answer(selected)))
        await settle()
        XCTAssertEqual(h.transport.pendingAcknowledgements, 1)
        h.session.stop(.userPause)
        guard let stopped = h.session.taskState?.context else {
            XCTFail("Missing stopped task state"); await finish(h); return
        }
        XCTAssertNil(h.session.friendAssistance?.pendingAcceptance)
        h.transport.releaseAcknowledgement(); await settle()
        XCTAssertEqual(h.session.phase, .paused)
        XCTAssertEqual(h.session.taskState?.context, stopped)
        XCTAssertNil(h.session.taskState?.target)
        await finish(h)
    }

    func testSimulatedSynchronousLocalStopDuringPreparationNeverStartsACK() async {
        let h = makeHarness(stopDuringSpeech: true)
        h.session.start(); await settle()
        let session = h.session
        h.stopOnSpeech?.onLocalStop = { [weak session] in session?.stop(.localStop) }
        let trusted = scope(h); await authorize(h, scope: trusted)
        let selected = await proposeAndPrompt(h, scope: trusted)
        h.local.completePrompt(); h.local.onOwnerInput?(.selection(answer(selected)))
        await settle()
        XCTAssertGreaterThan(h.speech.localStops, 0)
        XCTAssertNil(h.session.friendAssistance?.pendingAcceptance)
        XCTAssertTrue(h.transport.acknowledgements.isEmpty)
        XCTAssertEqual(h.session.phase, .paused)
        XCTAssertNil(h.session.taskState?.target)
        await finish(h)
    }

    func testSimulatedFreshSelectionAfterACKTimeoutCanUseNextGeneration() async {
        let h = makeHarness(); h.session.start(); await settle()
        let trusted = scope(h); await authorize(h, scope: trusted)
        let first = await proposeAndPrompt(h, scope: trusted)
        h.local.completePrompt(); h.local.onOwnerInput?(.selection(answer(first)))
        await settle()
        XCTAssertEqual(h.transport.pendingAcknowledgements, 1)
        guard let firstStoppedGeneration = h.session.taskState?.context.generation else {
            XCTFail("Missing prepared generation"); await finish(h); return
        }
        h.clock.advance(5_000); await settle()
        XCTAssertNil(h.session.friendAssistance?.pendingAcceptance)
        XCTAssertEqual(h.session.taskState?.context.generation, firstStoppedGeneration)
        // Cancellation cannot free an occupied noncooperative transport slot early.
        h.transport.releaseAcknowledgement(); await settle()
        XCTAssertEqual(h.transport.pendingAcknowledgements, 0)
        let second = await proposeAndPrompt(h, scope: trusted, generation: 2)
        h.local.completePrompt(); h.local.onOwnerInput?(.selection(answer(second)))
        await settle()
        XCTAssertEqual(h.transport.pendingAcknowledgements, 1)
        XCTAssertEqual(h.session.taskState?.context.generation, firstStoppedGeneration + 1)
        h.transport.releaseAcknowledgement(); await settle()
        XCTAssertEqual(h.session.taskState?.context.taskID, h.transport.acknowledgements.last?.newTaskID)
        await finish(h)
    }

    func testSimulatedWatchdogUnpublishesBeforeEvictionWithoutNetworkCallback() async {
        let h = makeHarness(); h.session.start(); await settle()
        let trusted = scope(h); await authorize(h, scope: trusted)
        let selected = await proposeAndPrompt(h, scope: trusted)
        h.local.completePrompt(); h.local.onOwnerInput?(.selection(answer(selected)))
        await settle(); h.transport.releaseAcknowledgement(); await settle()
        XCTAssertEqual(h.session.taskState?.context.taskID, h.transport.acknowledgements.last?.newTaskID)
        h.transport.onEvict = { XCTAssertFalse(h.local.isSharing) }
        h.clock.advance(15_000); await settle()
        XCTAssertFalse(h.local.isSharing)
        XCTAssertEqual(h.session.phase, .paused)
        XCTAssertNil(h.session.friendAssistance?.selection?.pendingSelectionID)
        XCTAssertEqual(h.transport.evictions.count, 1)
        XCTAssertLessThanOrEqual(h.transport.pendingRenewals, 1)
        await finish(h)
    }

    func testSimulatedWrongGrantScopeAndOldHelperCannotAuthorize() async {
        let h = makeHarness(); h.session.start(); await settle()
        let trusted = scope(h)
        XCTAssertTrue(h.session.enableFriendSharing(authenticatedScope: trusted))
        await settle()
        let wrong = SharingAuthorizationScope(sessionID: trusted.sessionID, sessionEpoch: "wrong",
                                               authorizationVersion: 1,
                                               helper: .init(connectionID: "connection", leaseVersion: 1))
        h.transport.releaseRenewal(scope: wrong); await settle()
        XCTAssertFalse(h.local.isSharing)
        h.transport.onHelperEvent?(.proposal(authenticatedScope: trusted, proposal(h, scope: trusted)))
        await settle(); XCTAssertTrue(h.transport.resolutions.isEmpty)
        h.session.stopFriendSharing()
        XCTAssertFalse(h.session.enableFriendSharing(authenticatedScope: trusted))
        await finish(h)
    }

    func testSimulatedTaskChangeCancelsOldProposalAndOldOwnerYes() async {
        let h = makeHarness(); h.session.start(); await settle()
        let trusted = scope(h); await authorize(h, scope: trusted)
        let selected = await proposeAndPrompt(h, scope: trusted)
        h.local.completePrompt()
        h.session.submitTask(.init(id: "ordinary", kind: .wallMenu, query: "Read menu"))
        await settle()
        XCTAssertEqual(h.session.taskState?.context.taskID, "ordinary")
        XCTAssertNil(h.session.friendAssistance?.selection?.pendingSelectionID)
        h.local.onOwnerInput?(.selection(answer(selected)))
        await settle()
        XCTAssertTrue(h.transport.acknowledgements.isEmpty)
        XCTAssertTrue(h.transport.cancelled.contains(selected))
        await finish(h)
    }

    func testSimulatedNoncooperativeResolutionSlotStaysBoundedAcrossSupersessionAndClose() async {
        let h = makeHarness(); h.session.start(); await settle()
        let trusted = scope(h); await authorize(h, scope: trusted)
        let first = proposal(h, scope: trusted)
        let second = proposal(h, scope: trusted, generation: 2)
        h.transport.onHelperEvent?(.proposal(authenticatedScope: trusted, first)); await settle()
        XCTAssertEqual(h.transport.pendingResolutions, 1)
        h.clock.advance(500)
        h.transport.onHelperEvent?(.proposal(authenticatedScope: trusted, second)); await settle()
        XCTAssertEqual(h.transport.pendingResolutions, 1)
        XCTAssertEqual(h.transport.resolutions.count, 1)
        h.session.stopFriendSharing()
        XCTAssertFalse(h.local.isSharing)
        h.transport.releaseResolution(resolution(first)); await settle()
        XCTAssertEqual(h.transport.resolutions.count, 1)
        XCTAssertTrue(h.local.prompts.isEmpty)
        await finish(h)
    }

    func testSimulatedStoppingSharingLeavesOrdinaryTaskAndHelperPauseLeavesCall() async {
        let h = makeHarness()
        h.session.start(); await settle()
        h.session.submitTask(.init(id: "ordinary", kind: .wallMenu, query: "Read menu"))
        await settle()
        guard let ordinary = h.session.taskState?.context else {
            XCTFail("Missing ordinary task"); await finish(h); return
        }
        let trusted = scope(h); await authorize(h, scope: trusted)
        h.session.stopFriendSharing()
        XCTAssertEqual(h.session.phase, .active)
        XCTAssertEqual(h.session.taskState?.context, ordinary)
        let newer = scope(h, epoch: "new-epoch", version: 2)
        await authorize(h, scope: newer)
        h.transport.onHelperEvent?(.pause(authenticatedScope: newer))
        XCTAssertEqual(h.session.phase, .paused)
        XCTAssertTrue(h.local.isSharing)
        h.transport.onHelperEvent?(.left(authenticatedScope: newer))
        XCTAssertFalse(h.local.isSharing)
        await finish(h)
    }

    func testSimulatedGenericWorkerYesResumeAndAudioCannotAuthorize() async {
        let h = makeHarness(); h.session.start(); await settle()
        let trusted = scope(h); await authorize(h, scope: trusted)
        let selected = await proposeAndPrompt(h, scope: trusted)
        h.local.completePrompt()
        guard let current = h.session.context else {
            XCTFail("Missing session context"); await finish(h); return
        }
        h.conversation.emit(.confirmation(promptID: "prompt-1", answer: .yes, explicitUserResponse: true))
        h.conversation.emit(.command(.resume))
        h.conversation.emit(.audio(Data([1, 2, 3])))
        h.local.onOwnerInput?(.selection(answer(selected, provenance: .assistantEcho)))
        await settle()
        XCTAssertEqual(h.session.context, current)
        XCTAssertNil(h.session.friendAssistance?.pendingAcceptance)
        XCTAssertTrue(h.transport.acknowledgements.isEmpty)
        await finish(h)
    }

    func testSimulatedHelperLeaveAfterBackendLossUnpublishesImmediately() async {
        let h = makeHarness(); h.session.start(); await settle()
        let trusted = scope(h); await authorize(h, scope: trusted)
        guard let authority = h.transport.renewals.first?.scope else {
            XCTFail("Missing authorization scope"); await finish(h); return
        }
        let stale = SharingAuthorizationScope(sessionID: authority.sessionID, sessionEpoch: "stale",
                                               authorizationVersion: authority.authorizationVersion,
                                               helper: authority.helper)
        h.transport.onBackendUnavailable?(stale)
        XCTAssertTrue(h.local.isSharing)
        h.transport.onBackendUnavailable?(authority)
        XCTAssertTrue(h.local.isSharing) // Existing lease remains until expiry or helper removal.
        h.transport.onHelperEvent?(.left(authenticatedScope: trusted))
        XCTAssertFalse(h.local.isSharing)
        await settle()
        XCTAssertEqual(h.transport.evictions.count, 1)
        await finish(h)
    }

    func testSimulatedTrustedLocalMenuIntentStillWorksAfterSharingStops() async {
        let h = makeHarness(); h.session.start(); await settle()
        let trusted = scope(h); await authorize(h, scope: trusted)
        h.session.stopFriendSharing()
        XCTAssertFalse(h.local.isSharing)
        guard let context = h.session.context else { XCTFail("Missing started session"); await finish(h); return }
        let request = TaskRequest(id: "local-menu", kind: .wallMenu, query: "SIMULATED menu")
        h.local.onOwnerInput?(.intent(context, request, provenance: .remoteMessage))
        XCTAssertNotEqual(h.session.taskState?.context.taskID, request.id)
        h.local.onOwnerInput?(.intent(context, request, provenance: .localOwnerSpeech))
        await settle()
        XCTAssertEqual(h.session.taskState?.context.taskID, request.id)
        XCTAssertEqual(h.session.taskState?.phase, .discussingMenu)
        guard let pending = h.session.taskState?.pendingRequest else {
            XCTFail("Local menu command was not armed"); await finish(h); return
        }
        guard let menuContext = h.session.context else {
            XCTFail("Missing menu context"); await finish(h); return
        }
        h.local.onOwnerInput?(.menuCommand(menuContext, requestID: pending.requestID,
                                            .more, provenance: .assistantEcho))
        XCTAssertEqual(h.session.taskState?.sectionIndex, 0)
        h.local.onOwnerInput?(.menuCommand(menuContext, requestID: pending.requestID,
                                            .more, provenance: .localOwnerSpeech))
        XCTAssertEqual(h.session.taskState?.sectionIndex, 1)
        await finish(h)
    }

    func testSimulatedLocalHelperAudioPreemptsBeforeLocalStopSpeech() async {
        let h = makeHarness(); h.session.start(); await settle()
        let trusted = scope(h); await authorize(h, scope: trusted)
        let before = h.speech.localStops
        var localStopsSeenAtFirstPreemption: Int?
        h.local.onPreempt = {
            if localStopsSeenAtFirstPreemption == nil {
                localStopsSeenAtFirstPreemption = h.speech.localStops
            }
        }
        h.session.stop(.localStop)
        XCTAssertEqual(localStopsSeenAtFirstPreemption, before)
        XCTAssertGreaterThan(h.speech.localStops, before)
        XCTAssertGreaterThan(h.local.preemptions, 0)
        await finish(h)
    }

    func testSimulatedEndCancelsFriendWatchdogsAndUnpublishes() async {
        let h = makeHarness(); h.session.start(); await settle()
        let trusted = scope(h); await authorize(h, scope: trusted)
        XCTAssertGreaterThan(h.clock.pendingCount, 0)
        h.session.stop(.userEnd)
        await settle()
        XCTAssertEqual(h.session.phase, .ended)
        XCTAssertFalse(h.local.isSharing)
        XCTAssertEqual(h.clock.pendingCount, 0)
        XCTAssertEqual(h.transport.evictions.count, 1)
        h.transport.releaseAll(); await settle()
        h.transport.releaseAll(); await settle()
        h.clock.advance(20_000)
        XCTAssertEqual(h.clock.pendingCount, 0)
    }

    func testSimulatedOwnerResumeCannotBypassAcceptedFriendRevocation() async {
        let h = makeHarness(); h.session.start(); await settle()
        let trusted = scope(h); await authorize(h, scope: trusted)
        let selected = await proposeAndPrompt(h, scope: trusted)
        h.local.completePrompt(); h.local.onOwnerInput?(.selection(answer(selected)))
        await settle(); h.transport.releaseAcknowledgement(); await settle()
        guard let acceptedID = h.transport.acknowledgements.last?.newTaskID else {
            XCTFail("Missing accepted binding"); await finish(h); return
        }
        XCTAssertEqual(h.session.taskState?.context.taskID, acceptedID)
        h.session.stop(.userPause)
        h.clock.advance(100)
        h.session.resume(); await settle()
        XCTAssertEqual(h.session.phase, .active)
        XCTAssertEqual(h.session.taskState?.context.taskID, acceptedID)
        guard let authority = h.transport.renewals.first?.scope else {
            XCTFail("Missing authorization scope"); await finish(h); return
        }
        h.transport.onAuthorizationLost?(authority)
        XCTAssertEqual(h.session.phase, .paused)
        XCTAssertFalse(h.local.isSharing)
        await finish(h)
    }

    func testSimulatedReentrantEndDuringHelperAudioPreemptionStaysTerminal() async {
        let h = makeHarness(); h.session.start(); await settle()
        let trusted = scope(h); await authorize(h, scope: trusted)
        let session = h.session
        h.local.onPreempt = { [weak session] in session?.stop(.userEnd) }
        h.session.stop(.localStop)
        await settle()
        XCTAssertEqual(h.session.phase, .ended)
        XCTAssertFalse(h.local.isSharing)
        XCTAssertEqual(h.clock.pendingCount, 0)
        XCTAssertEqual(h.transport.evictions.count, 1)
        h.transport.releaseAll(); await settle()
        h.transport.releaseAll(); await settle()
        XCTAssertEqual(h.session.phase, .ended)
    }

    func testSimulatedReentrantEndDuringPromptCancellationClosesLease() async {
        let h = makeHarness(); h.session.start(); await settle()
        let trusted = scope(h); await authorize(h, scope: trusted)
        _ = await proposeAndPrompt(h, scope: trusted)
        XCTAssertEqual(h.local.prompts.count, 1)
        let session = h.session
        h.local.onCancel = { [weak session] in session?.stop(.userEnd) }
        h.session.stop(.userPause)
        await settle()
        XCTAssertEqual(h.local.cancellations.count, 1)
        XCTAssertEqual(h.session.phase, .ended)
        XCTAssertFalse(h.local.isSharing)
        XCTAssertEqual(h.clock.pendingCount, 0)
        XCTAssertEqual(h.transport.evictions.count, 1)
        h.transport.releaseAll(); await settle()
        h.transport.releaseAll(); await settle()
        XCTAssertEqual(h.session.phase, .ended)
    }
}
