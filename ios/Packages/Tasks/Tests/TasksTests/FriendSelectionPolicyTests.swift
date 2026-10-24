import XCTest
@testable import Tasks

final class FriendSelectionPolicyTests: XCTestCase {
    private let scope = FriendSelectionScope(sessionID: "session", sessionEpoch: "epoch",
                                             helperID: "helper", helperConnectionID: "connection",
                                             helperLeaseVersion: 7, authorizationVersion: 9)

    private func policy(generation: UInt64 = 4) -> FriendSelectionPolicy {
        FriendSelectionPolicy(scope: scope, task: TaskContext(sessionID: "session", taskID: "active",
                                                       generation: generation))!
    }

    func testNativeIdleContextCanProposeWithoutInventedTaskID() {
        var subject = FriendSelectionPolicy(scope: scope, task: .init(sessionID: "session", taskID: "", generation: 0))!
        let selected = proposal(taskGeneration: 0)
        ready(&subject, proposal: selected)
        let effects = subject.confirm(answer(selected), nowMs: 25_099)
        guard case .publishAcceptedBinding(let binding) = effects.last else { return XCTFail("Missing acceptance") }
        XCTAssertEqual(binding.oldTaskID, "")
        XCTAssertEqual(binding.newTaskGeneration, 1)
    }

    func testNativeContextSyncCancelsPromptAndPreservesReplayWatermark() {
        var subject = policy()
        let selected = proposal()
        ready(&subject, proposal: selected)
        let next = TaskContext(sessionID: "session", taskID: "ordinary", generation: 7)
        XCTAssertEqual(subject.synchronizeTask(next), [.cancelPending(selectionID: selected.selectionID)])
        XCTAssertEqual(subject.task, next)
        XCTAssertEqual(subject.lastProposalGeneration, 1)
        XCTAssertEqual(subject.confirm(answer(selected), nowMs: 25_099), [.rejected])
        XCTAssertEqual(subject.submit(proposal(taskGeneration: 7), nowMs: 25_100), [.rejected])
        XCTAssertEqual(subject.synchronizeTask(.init(sessionID: "other", taskID: "x", generation: 8)), [.rejected])
        XCTAssertEqual(subject.synchronizeTask(.init(sessionID: "session", taskID: "x", generation: 6)), [.rejected])
        XCTAssertEqual(subject.task, next)
    }

    private func proposal(_ generation: UInt64 = 1, scope: FriendSelectionScope? = nil,
                          x: Double = 0.4, taskGeneration: UInt64 = 4) -> FriendSelectionProposal {
        FriendSelectionProposal(scope: scope ?? self.scope, selectionID: "selection-\(generation)",
                                proposalGeneration: generation, taskGeneration: taskGeneration,
                                referenceFrameID: "helper-frame-\(generation)",
                                referenceDigest: "digest-\(generation)", transportEpoch: "transport",
                                pointX: x, pointY: 0.6)
    }

    private func target(id: String = "cup", safety: ControlSafety = .ordinary,
                        label: String = "cup", region: ImageRegion = ImageRegion(x: 0.2, y: 0.2,
                                                                                   width: 0.2, height: 0.2)) -> ResolvedTarget {
        ResolvedTarget(id: id, kind: .object, label: label, region: region,
                       frameID: "r1-current", safety: safety)
    }

    private func resolution(_ proposal: FriendSelectionProposal, target: ResolvedTarget? = nil,
                            promptID: String = "prompt") -> FriendSelectionResolution {
        FriendSelectionResolution(proposal: proposal, promptID: promptID,
                                  currentSceneFrameID: "r1-current", target: target ?? self.target())
    }

    private func answer(_ proposal: FriendSelectionProposal, promptID: String = "prompt",
                        provenance: FriendConfirmationProvenance = .localOwnerSpeech,
                        answer: FriendConfirmationAnswer = .yes) -> FriendOwnerConfirmation {
        FriendOwnerConfirmation(scope: proposal.scope, selectionID: proposal.selectionID,
                                proposalGeneration: proposal.proposalGeneration, promptID: promptID,
                                provenance: provenance, answer: answer)
    }

    private func ready(_ policy: inout FriendSelectionPolicy,
                       proposal: FriendSelectionProposal, at now: Int64 = 100) {
        XCTAssertEqual(policy.submit(proposal, nowMs: now), [.requestResolution(proposal)])
        XCTAssertEqual(policy.resolve(resolution(proposal), nowMs: now + 14_999),
                       [.requestPrompt(selectionID: proposal.selectionID, promptID: "prompt", target: target())])
        XCTAssertEqual(policy.localPromptPlaybackCompleted(proposal: proposal,
                                                           promptID: "prompt", provenance: .localR1Completion, nowMs: now + 24_998), [])
    }

    func testFullWindowsBeginAtActualPromptCompletionAndAcceptanceBindsExactlyOnce() {
        var subject = policy(); let selected = proposal()
        ready(&subject, proposal: selected)
        XCTAssertEqual(subject.confirmationDeadlineMs, 40_098)
        let effects = subject.confirm(answer(selected), nowMs: 40_097)
        XCTAssertEqual(effects.count, 2)
        guard case .allStopAndCancelOldTask(let old) = effects[0],
              case .publishAcceptedBinding(let binding) = effects[1] else { return XCTFail("wrong acceptance order") }
        XCTAssertEqual(old.taskID, "active")
        XCTAssertEqual(old.generation, 4)
        XCTAssertEqual(binding.selectionID, selected.selectionID)
        XCTAssertEqual(binding.promptID, "prompt")
        XCTAssertEqual(binding.target.id, "cup")
        XCTAssertEqual(binding.oldTaskGeneration, 4)
        XCTAssertEqual(binding.newTaskGeneration, 5)
        XCTAssertNotEqual(binding.newTaskID, old.taskID)
        XCTAssertEqual(subject.task.taskID, binding.newTaskID)
        XCTAssertEqual(subject.confirm(answer(selected), nowMs: 40_098), [.rejected])
    }

    func testSupersessionDoesNotReplaceActiveTaskAndRejectsLateModelOrYes() {
        var subject = policy(); let first = proposal(), second = proposal(2)
        _ = subject.submit(first, nowMs: 100)
        let effects = subject.submit(second, nowMs: 101)
        XCTAssertEqual(effects, [.cancelPending(selectionID: first.selectionID), .requestResolution(second)])
        XCTAssertEqual(subject.task.taskID, "active")
        XCTAssertEqual(subject.task.generation, 4)
        XCTAssertEqual(subject.resolve(resolution(first), nowMs: 102), [.rejected])
        XCTAssertEqual(subject.confirm(answer(first), nowMs: 103), [.rejected])
        XCTAssertEqual(subject.pendingSelectionID, second.selectionID)
        XCTAssertEqual(subject.submit(first, nowMs: 104), [.rejected])
    }

    func testStopEndAndHelperInvalidationRejectLateEvents() {
        var subject = policy(); let selected = proposal()
        _ = subject.submit(selected, nowMs: 100)
        _ = subject.resolve(resolution(selected), nowMs: 101)
        _ = subject.localPromptPlaybackCompleted(proposal: selected, promptID: "prompt", provenance: .localR1Completion, nowMs: 102)
        XCTAssertEqual(subject.stop(nowMs: 103), [.cancelPending(selectionID: selected.selectionID),
                                                   .allStopAndCancelOldTask(TaskContext(sessionID: "session", taskID: "active", generation: 4))])
        XCTAssertEqual(subject.task.generation, 5)
        XCTAssertEqual(subject.confirm(answer(selected), nowMs: 104), [.rejected])
        XCTAssertEqual(subject.resolve(resolution(selected), nowMs: 105), [.rejected])
        let next = proposal(2, taskGeneration: 5)
        _ = subject.submit(next, nowMs: 106)
        XCTAssertEqual(subject.invalidateHelper(nowMs: 107), [.cancelPending(selectionID: next.selectionID)])
        XCTAssertFalse(subject.helperAuthorized)
        XCTAssertEqual(subject.submit(proposal(3, taskGeneration: 5), nowMs: 108), [.rejected])
        XCTAssertEqual(subject.resolve(resolution(next), nowMs: 108), [.rejected])
        XCTAssertEqual(subject.end(nowMs: 109), [.allStopAndCancelOldTask(subject.task)])
        XCTAssertEqual(subject.submit(proposal(3), nowMs: 110), [.rejected])
    }

    func testOnlyTrustedOwnerInputCanAccept() {
        var subject = policy(); let selected = proposal()
        _ = subject.submit(selected, nowMs: 100)
        _ = subject.resolve(resolution(selected), nowMs: 101)
        XCTAssertEqual(subject.confirm(answer(selected), nowMs: 102), [.rejected]) // queued prompt
        XCTAssertEqual(subject.localPromptPlaybackCompleted(proposal: selected,
                                                            promptID: "prompt", provenance: .remoteClaim,
                                                            nowMs: Int64.max), [.rejected])
        _ = subject.localPromptPlaybackCompleted(proposal: selected, promptID: "prompt", provenance: .localR1Completion, nowMs: 103)
        for provenance: FriendConfirmationProvenance in [.helperSpeech, .assistantEcho, .ambiguousAudio, .remoteMessage] {
            XCTAssertEqual(subject.confirm(answer(selected, provenance: provenance), nowMs: 104), [.rejected])
        }
        XCTAssertEqual(subject.confirm(answer(selected, promptID: "other"), nowMs: 105), [.rejected])
        XCTAssertEqual(subject.confirm(answer(selected, provenance: .localOwnerAccessibleControl), nowMs: 106), [.rejected])
        XCTAssertEqual(subject.confirm(answer(selected, provenance: .localOwnerSpeech), nowMs: 107).count, 2)
    }

    func testAccessibleControlMayDeclineButCannotAccept() {
        var subject = policy(); let selected = proposal()
        _ = subject.submit(selected, nowMs: 100)
        _ = subject.resolve(resolution(selected), nowMs: 101)
        _ = subject.localPromptPlaybackCompleted(proposal: selected, promptID: "prompt",
                                                 provenance: .localR1Completion, nowMs: 102)
        XCTAssertEqual(subject.confirm(answer(selected, provenance: .localOwnerAccessibleControl), nowMs: 103), [.rejected])
        XCTAssertEqual(subject.confirm(answer(selected, provenance: .localOwnerAccessibleControl,
                                              answer: .no), nowMs: 104),
                       [.cancelPending(selectionID: selected.selectionID)])
        XCTAssertEqual(subject.task.taskID, "active")
    }

    func testTimeoutsAndLatePlaybackDoNotCreatePartialConfirmation() {
        var subject = policy(); let first = proposal()
        _ = subject.submit(first, nowMs: 100)
        XCTAssertEqual(subject.resolve(resolution(first), nowMs: 15_100), [.cancelPending(selectionID: first.selectionID)])
        let second = proposal(2)
        _ = subject.submit(second, nowMs: 15_102)
        _ = subject.resolve(resolution(second), nowMs: 15_103)
        XCTAssertEqual(subject.localPromptPlaybackCompleted(proposal: second, promptID: "prompt", provenance: .localR1Completion,
                                                           nowMs: 25_103), [.cancelPending(selectionID: second.selectionID)])
        let third = proposal(3)
        _ = subject.submit(third, nowMs: 25_105)
        _ = subject.resolve(resolution(third), nowMs: 25_106)
        _ = subject.localPromptPlaybackCompleted(proposal: third, promptID: "prompt", provenance: .localR1Completion, nowMs: 25_107)
        XCTAssertEqual(subject.confirm(answer(third), nowMs: 40_107), [.cancelPending(selectionID: third.selectionID)])
    }

    func testPromptInterruptionCancelsEvenAfterCompletionCallback() {
        var subject = policy(); let selected = proposal()
        _ = subject.submit(selected, nowMs: 100)
        _ = subject.resolve(resolution(selected), nowMs: 101)
        _ = subject.localPromptPlaybackCompleted(proposal: selected, promptID: "prompt",
                                                 provenance: .localR1Completion, nowMs: 102)
        XCTAssertEqual(subject.localPromptPlaybackCompleted(proposal: selected,
                                                            promptID: "prompt", provenance: .interrupted,
                                                            nowMs: 103), [.cancelPending(selectionID: selected.selectionID)])
        XCTAssertEqual(subject.confirm(answer(selected), nowMs: 104), [.rejected])
        XCTAssertEqual(subject.localPromptPlaybackCompleted(proposal: selected,
                                                            promptID: "prompt", provenance: .localR1Completion,
                                                            nowMs: 105), [.rejected])
    }

    func testStopAndEndIgnoreClockRegression() {
        var subject = policy(); let selected = proposal()
        _ = subject.submit(selected, nowMs: 100)
        XCTAssertEqual(subject.stop(nowMs: -1), [.cancelPending(selectionID: selected.selectionID),
                                                .allStopAndCancelOldTask(TaskContext(sessionID: "session", taskID: "active", generation: 4))])
        XCTAssertEqual(subject.task.generation, 5)
        XCTAssertEqual(subject.end(nowMs: -2), [.allStopAndCancelOldTask(subject.task)])
        XCTAssertTrue(subject.isEnded)
    }

    func testStaleTaskGenerationCannotSubmitAfterStopOrAcceptance() {
        var subject = policy(); let selected = proposal()
        _ = subject.submit(selected, nowMs: 100)
        _ = subject.resolve(resolution(selected), nowMs: 101)
        _ = subject.localPromptPlaybackCompleted(proposal: selected, promptID: "prompt",
                                                 provenance: .localR1Completion, nowMs: 102)
        XCTAssertEqual(subject.confirm(answer(selected), nowMs: 103).count, 2)
        XCTAssertEqual(subject.submit(proposal(2), nowMs: 104), [.rejected])
        let current = proposal(2, taskGeneration: 5)
        XCTAssertEqual(subject.submit(current, nowMs: 105), [.requestResolution(current)])
        _ = subject.stop(nowMs: 106)
        XCTAssertEqual(subject.submit(proposal(3, taskGeneration: 5), nowMs: 107), [.rejected])
    }

    func testOldPlaybackCannotCompleteReusedSelectionAndPromptIDs() {
        var subject = policy()
        let first = proposal()
        _ = subject.submit(first, nowMs: 100)
        _ = subject.resolve(resolution(first), nowMs: 101)
        let second = FriendSelectionProposal(scope: scope, selectionID: first.selectionID,
                                             proposalGeneration: 2, taskGeneration: 4,
                                             referenceFrameID: "different-helper-frame",
                                             referenceDigest: "different-digest", transportEpoch: "transport",
                                             pointX: 0.5, pointY: 0.6)
        XCTAssertEqual(subject.submit(second, nowMs: 102),
                       [.cancelPending(selectionID: first.selectionID), .requestResolution(second)])
        _ = subject.resolve(resolution(second), nowMs: 103) // same prompt ID
        XCTAssertEqual(subject.localPromptPlaybackCompleted(proposal: first, promptID: "prompt",
                                                            provenance: .localR1Completion, nowMs: 104), [.rejected])
        XCTAssertNil(subject.confirmationDeadlineMs)
        XCTAssertEqual(subject.nextWakeupMs, 10_103)
        XCTAssertEqual(subject.confirm(answer(second), nowMs: 105), [.rejected])
        XCTAssertEqual(subject.localPromptPlaybackCompleted(proposal: second, promptID: "prompt",
                                                            provenance: .localR1Completion, nowMs: 106), [])
        XCTAssertEqual(subject.nextWakeupMs, 15_106)
    }

    func testWatchdogDeadlineAdvancesAcrossPhases() {
        var subject = policy(); let selected = proposal()
        XCTAssertNil(subject.nextWakeupMs)
        _ = subject.submit(selected, nowMs: 100)
        XCTAssertEqual(subject.nextWakeupMs, 15_100)
        _ = subject.resolve(resolution(selected), nowMs: 101)
        XCTAssertEqual(subject.nextWakeupMs, 10_101)
        _ = subject.localPromptPlaybackCompleted(proposal: selected, promptID: "prompt",
                                                 provenance: .localR1Completion, nowMs: 102)
        XCTAssertEqual(subject.nextWakeupMs, 15_102)
        XCTAssertEqual(subject.tick(nowMs: 15_102), [.cancelPending(selectionID: selected.selectionID)])
        XCTAssertNil(subject.nextWakeupMs)
    }

    func testUnsafeMetadataAndCrossContextAreRejected() {
        var subject = policy(); let selected = proposal()
        let otherLease = FriendSelectionScope(sessionID: "session", sessionEpoch: "epoch",
                                              helperID: "helper", helperConnectionID: "connection",
                                              helperLeaseVersion: 8, authorizationVersion: 9)
        let otherGrant = FriendSelectionScope(sessionID: "session", sessionEpoch: "epoch",
                                              helperID: "helper", helperConnectionID: "connection",
                                              helperLeaseVersion: 7, authorizationVersion: 10)
        XCTAssertEqual(subject.submit(proposal(1, scope: otherLease), nowMs: 100), [.rejected])
        XCTAssertEqual(subject.submit(proposal(1, scope: otherGrant), nowMs: 100), [.rejected])
        XCTAssertEqual(subject.submit(proposal(1, x: .nan), nowMs: 101), [.rejected])
        _ = subject.submit(selected, nowMs: 102)
        for unsafe in [target(safety: .emergency), target(safety: .payment),
                       target(label: "  "), target(region: ImageRegion(x: .nan, y: 0, width: 1, height: 1))] {
            var isolated = policy(); _ = isolated.submit(selected, nowMs: 100)
            XCTAssertEqual(isolated.resolve(resolution(selected, target: unsafe), nowMs: 101),
                           [.cancelPending(selectionID: selected.selectionID)])
        }
        XCTAssertEqual(subject.resolve(resolution(selected, promptID: ""), nowMs: 103),
                       [.cancelPending(selectionID: selected.selectionID)])
    }

    func testSafeIntegerAndMonotonicClockBoundaries() {
        XCTAssertNil(FriendSelectionPolicy(scope: scope, task: TaskContext(sessionID: "session", taskID: "active",
                                                                      generation: FriendSelectionPolicy.maximumSafeGeneration)))
        var subject = policy(generation: FriendSelectionPolicy.maximumSafeGeneration - 1)
        XCTAssertEqual(subject.submit(proposal(FriendSelectionPolicy.maximumSafeGeneration + 1), nowMs: 1), [.rejected])
        XCTAssertEqual(subject.submit(proposal(), nowMs: Int64.max - 100), [.rejected])
        let current = proposal(taskGeneration: FriendSelectionPolicy.maximumSafeGeneration - 1)
        XCTAssertEqual(subject.submit(current, nowMs: 10), [.requestResolution(current)])
        XCTAssertEqual(subject.resolve(resolution(current), nowMs: 9), [.rejected]) // backwards clock
        XCTAssertEqual(subject.resolve(resolution(proposal(2)), nowMs: Int64.max), [.rejected])
        XCTAssertEqual(subject.resolve(resolution(current), nowMs: 11).count, 1) // wrong event did not poison clock
    }

    func testHelperInvalidationPausesAcceptedFriendTaskAndStopAtCeilingStillStops() {
        var subject = policy(generation: FriendSelectionPolicy.maximumSafeGeneration - 1)
        let selected = proposal(taskGeneration: FriendSelectionPolicy.maximumSafeGeneration - 1)
        _ = subject.submit(selected, nowMs: 100)
        _ = subject.resolve(resolution(selected), nowMs: 101)
        _ = subject.localPromptPlaybackCompleted(proposal: selected, promptID: "prompt", provenance: .localR1Completion, nowMs: 102)
        XCTAssertEqual(subject.confirm(answer(selected), nowMs: 103).count, 2)
        XCTAssertEqual(subject.task.generation, FriendSelectionPolicy.maximumSafeGeneration)
        let effects = subject.invalidateHelper(nowMs: 104)
        XCTAssertEqual(effects, [.allStopAndCancelOldTask(subject.task)])
        XCTAssertTrue(subject.isEnded)
        XCTAssertEqual(subject.submit(proposal(2), nowMs: 105), [.rejected])
    }
}
