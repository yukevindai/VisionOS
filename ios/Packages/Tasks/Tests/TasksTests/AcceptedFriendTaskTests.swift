import XCTest
@testable import Tasks

final class AcceptedFriendTaskTests: XCTestCase {
    private let scope = FriendSelectionScope(sessionID: "session", sessionEpoch: "epoch",
                                             helperID: "helper", helperConnectionID: "connection",
                                             helperLeaseVersion: 1, authorizationVersion: 1)

    private func target(id: String = "cup", kind: TargetKind = .object,
                        label: String = "Cup", safety: ControlSafety = .ordinary,
                        doorAction: DoorAction? = nil) -> ResolvedTarget {
        TaskFixtures.target(id: id, kind: kind, label: label, safety: safety,
                            frameID: "accepted-scene", doorAction: doorAction,
                            doorActionEvidence: doorAction != nil)
    }

    private func binding(oldGeneration: UInt64 = 1, oldTaskID: String = "old",
                         target: ResolvedTarget? = nil) -> FriendSelectionAccepted {
        FriendSelectionAccepted(scope: scope, selectionID: "selection", promptID: "prompt",
                                proposalGeneration: 3, target: target ?? self.target(),
                                oldTaskID: oldTaskID, oldTaskGeneration: oldGeneration,
                                newTaskID: "friend:session:epoch:3",
                                newTaskGeneration: oldGeneration + 1)
    }

    private func started() -> TaskEngine {
        var engine = TaskEngine(sessionID: "session")
        _ = engine.reduce(.start(TaskRequest(id: "old", kind: .button, query: "Old button")), nowMs: 100)
        return engine
    }

    private func receive(_ response: TaskResponse, on engine: inout TaskEngine,
                         at time: Int64, eventID: String,
                         source: EventSource = .r1) -> [TaskEffect] {
        let context = engine.state.pendingRequest!
        let envelope = TaskFixtures.envelope(context: context, eventID: eventID, nowMs: time,
                                             source: source)
        return engine.reduce(.receive(envelope, response), nowMs: time)
    }

    func testRequiresExactPostStopBindingAndPreservesSingleGenerationAdvance() {
        var engine = started()
        let accepted = binding()
        let active = engine.state
        XCTAssertEqual(engine.beginAcceptedSelection(accepted, nowMs: 100), [.rejected(.wrongContext)])
        XCTAssertEqual(engine.state, active)

        let oldRequest = engine.state.pendingRequest!
        let stopped = engine.reduce(.stop(.replaced), nowMs: 101)
        XCTAssertTrue(stopped.contains(.cancelWork(oldRequest.task)))
        XCTAssertTrue(stopped.contains(.allStop(TaskContext(sessionID: "session", taskID: "old", generation: 2))))
        let effects = engine.beginAcceptedSelection(accepted, nowMs: 102)
        XCTAssertEqual(engine.state.context,
                       TaskContext(sessionID: "session", taskID: accepted.newTaskID, generation: 2))
        XCTAssertEqual(engine.state.target, accepted.target)
        XCTAssertEqual(engine.state.phase, .reacquiring)
        XCTAssertTrue(effects.contains { if case .reacquire(let request, let target) = $0 {
            return request.task == engine.state.context && target == accepted.target
        }; return false })
        XCTAssertFalse(effects.contains { if case .resolveTarget = $0 { return true }; return false })
        XCTAssertEqual(engine.beginAcceptedSelection(accepted, nowMs: 103), [.rejected(.wrongContext)])
        XCTAssertEqual(engine.reduce(.receive(TaskFixtures.envelope(context: oldRequest,
                                            eventID: "old-result", nowMs: 103, source: .r3),
                                            .target(target(id: "other"))), nowMs: 103), [.rejected(.wrongContext)])
    }

    func testIdleOwnerCanAcceptFirstTaskAfterReplacementStop() {
        var engine = TaskEngine(sessionID: "session")
        let accepted = binding(oldGeneration: 0, oldTaskID: "")
        XCTAssertNil(engine.state.request)
        XCTAssertEqual(engine.beginAcceptedSelection(accepted, nowMs: 100), [.rejected(.wrongContext)])
        let stop = engine.reduce(.stop(.replaced), nowMs: 100)
        XCTAssertTrue(stop.contains(.cancelWork(TaskContext(sessionID: "session", taskID: "", generation: 0))))
        XCTAssertEqual(engine.state.context.generation, 1)
        let effects = engine.beginAcceptedSelection(accepted, nowMs: 101)
        XCTAssertEqual(engine.state.context.taskID, accepted.newTaskID)
        XCTAssertEqual(engine.state.context.generation, 1)
        XCTAssertTrue(effects.contains { if case .reacquire = $0 { return true }; return false })
    }

    func testPausedOwnerReplacementAdvancesOnceAndCanAccept() {
        var engine = started()
        _ = engine.reduce(.stop(.userPause), nowMs: 101)
        XCTAssertEqual(engine.state.context.generation, 2)
        let stop = engine.reduce(.stop(.replaced), nowMs: 102)
        XCTAssertTrue(stop.contains(.allStop(TaskContext(sessionID: "session", taskID: "old", generation: 3))))
        XCTAssertEqual(engine.reduce(.stop(.replaced), nowMs: 102), [])
        XCTAssertEqual(engine.state.context.generation, 3)
        let effects = engine.beginAcceptedSelection(binding(oldGeneration: 2), nowMs: 103)
        XCTAssertEqual(engine.state.context.generation, 3)
        XCTAssertTrue(effects.contains { if case .reacquire = $0 { return true }; return false })
    }

    func testPreparedBindingIsSingleUseAndFreshBindingAfterACKTimeoutAdvancesAgain() {
        var engine = started()
        let first = binding()
        let firstEffects = engine.prepareAcceptedSelection(first, nowMs: 101)
        XCTAssertTrue(firstEffects.contains(.cancelWork(TaskContext(sessionID: "session", taskID: "old", generation: 1))))
        XCTAssertTrue(firstEffects.contains(.allStop(TaskContext(sessionID: "session", taskID: "old", generation: 2))))
        XCTAssertEqual(engine.state.phase, .paused)
        XCTAssertEqual(engine.state.lastStop, .replaced)
        XCTAssertEqual(engine.prepareAcceptedSelection(first, nowMs: 102), [.rejected(.wrongContext)])
        XCTAssertEqual(engine.state.context.generation, 2)

        // The first authenticated ACK never returned. Its occupied wait is cancelled by
        // the caller; a later, distinct proposal may bind the still-stopped engine.
        let second = FriendSelectionAccepted(scope: scope, selectionID: "selection-4", promptID: "prompt-4",
                                             proposalGeneration: 4, target: target(id: "second-cup"),
                                             oldTaskID: "old", oldTaskGeneration: 2,
                                             newTaskID: "friend:session:epoch:4", newTaskGeneration: 3)
        let secondEffects = engine.prepareAcceptedSelection(second, nowMs: 103)
        XCTAssertTrue(secondEffects.contains(.cancelWork(TaskContext(sessionID: "session", taskID: "old", generation: 2))))
        XCTAssertTrue(secondEffects.contains(.allStop(TaskContext(sessionID: "session", taskID: "old", generation: 3))))
        XCTAssertEqual(engine.state.context.generation, 3)
        let accepted = engine.beginAcceptedSelection(second, nowMs: 104)
        XCTAssertEqual(engine.state.context.taskID, second.newTaskID)
        XCTAssertEqual(engine.state.context.generation, 3)
        XCTAssertTrue(accepted.contains { if case .reacquire(_, let target) = $0 {
            return target.id == "second-cup"
        }; return false })
        XCTAssertFalse(accepted.contains { if case .resolveTarget = $0 { return true }; return false })
    }

    func testFreshAcceptedBindingCanReplaceCancelledPriorTask() {
        var engine = started()
        _ = engine.reduce(.stop(.cancelled), nowMs: 101)
        XCTAssertEqual(engine.state.phase, .cancelled)
        XCTAssertEqual(engine.state.context.generation, 2)
        let accepted = binding(oldGeneration: 2)
        let effects = engine.prepareAcceptedSelection(accepted, nowMs: 102)
        XCTAssertTrue(effects.contains(.allStop(TaskContext(sessionID: "session", taskID: "old", generation: 3))))
        XCTAssertEqual(engine.state.phase, .paused)
        XCTAssertEqual(engine.state.context.generation, 3)
        XCTAssertTrue(engine.beginAcceptedSelection(accepted, nowMs: 103).contains {
            if case .reacquire = $0 { return true }
            return false
        })
    }

    func testMissingWrongGenerationSessionAndStoppedReasonRejectWithoutMutation() {
        var engine = started()
        _ = engine.reduce(.stop(.replaced), nowMs: 101)
        let stopped = engine.state
        XCTAssertEqual(engine.beginAcceptedSelection(binding(oldGeneration: 0), nowMs: 102), [.rejected(.wrongContext)])
        XCTAssertEqual(engine.beginAcceptedSelection(binding(oldTaskID: "other"), nowMs: 102), [.rejected(.wrongContext)])
        let otherScope = FriendSelectionScope(sessionID: "other-session", sessionEpoch: "epoch",
                                              helperID: "helper", helperConnectionID: "connection",
                                              helperLeaseVersion: 1, authorizationVersion: 1)
        let wrongSession = FriendSelectionAccepted(scope: otherScope, selectionID: "selection", promptID: "prompt",
                                                   proposalGeneration: 3, target: target(), oldTaskID: "old",
                                                   oldTaskGeneration: 1, newTaskID: "friend:other-session:epoch:3",
                                                   newTaskGeneration: 2)
        XCTAssertEqual(engine.beginAcceptedSelection(wrongSession, nowMs: 102), [.rejected(.wrongContext)])
        let wrongNewGeneration = FriendSelectionAccepted(scope: scope, selectionID: "selection", promptID: "prompt",
                                                         proposalGeneration: 3, target: target(), oldTaskID: "old",
                                                         oldTaskGeneration: 1, newTaskID: "friend:session:epoch:3",
                                                         newTaskGeneration: 3)
        XCTAssertEqual(engine.beginAcceptedSelection(wrongNewGeneration, nowMs: 102), [.rejected(.wrongContext)])
        let wrongNewID = FriendSelectionAccepted(scope: scope, selectionID: "selection", promptID: "prompt",
                                                 proposalGeneration: 3, target: target(), oldTaskID: "old",
                                                 oldTaskGeneration: 1, newTaskID: "friend:session:epoch:4",
                                                 newTaskGeneration: 2)
        XCTAssertEqual(engine.beginAcceptedSelection(wrongNewID, nowMs: 102), [.rejected(.wrongContext)])
        XCTAssertEqual(engine.beginAcceptedSelection(binding(), nowMs: 99), [.rejected(.wrongContext)])
        XCTAssertEqual(engine.state, stopped)

        var paused = started()
        _ = paused.reduce(.stop(.userPause), nowMs: 101)
        XCTAssertEqual(paused.beginAcceptedSelection(binding(), nowMs: 102), [.rejected(.wrongContext)])
    }

    func testUnsafeTargetsAndKioskDoNotConsumeStoppedGeneration() {
        let rejected: [ResolvedTarget] = [
            target(safety: .emergency), target(safety: .payment),
            target(label: "Confirm purchase"), target(label: ""),
            target(kind: .screenControl)
        ]
        for selected in rejected {
            var engine = started()
            _ = engine.reduce(.stop(.replaced), nowMs: 101)
            let stopped = engine.state
            let effects = engine.beginAcceptedSelection(binding(target: selected), nowMs: 102)
            XCTAssertEqual(effects, [.rejected(selected.kind == .screenControl ? .unsupportedState :
                                               selected.label.isEmpty ? .invalidRequest : .unsafeTarget)])
            XCTAssertEqual(engine.state, stopped)
            XCTAssertEqual(engine.state.context.generation, 2)
        }
    }

    func testSupportedButtonObjectAndDoorUseExistingTargetRules() {
        for selected in [target(kind: .button), target(kind: .object),
                         target(kind: .handle, doorAction: .lever)] {
            var engine = started()
            _ = engine.reduce(.stop(.replaced), nowMs: 101)
            let effects = engine.beginAcceptedSelection(binding(target: selected), nowMs: 102)
            XCTAssertEqual(engine.state.target, selected)
            XCTAssertEqual(engine.state.phase, .reacquiring)
            XCTAssertTrue(effects.contains { if case .reacquire = $0 { return true }; return false })
        }
    }

    func testResumeAndRetryReacquireAcceptedIdentityWithoutTextResolution() {
        var engine = started()
        _ = engine.reduce(.stop(.replaced), nowMs: 101)
        _ = engine.beginAcceptedSelection(binding(), nowMs: 102)
        _ = engine.reduce(.stop(.trackingLost), nowMs: 103)
        let resume = engine.reduce(.resume, nowMs: 104)
        XCTAssertTrue(resume.contains { if case .reacquire(_, let target) = $0 { return target.id == "cup" }; return false })
        XCTAssertFalse(resume.contains { if case .resolveTarget = $0 { return true }; return false })

        _ = receive(.physical(TaskFixtures.physical(targetID: "cup", nowMs: 105)),
                    on: &engine, at: 105, eventID: "resume-evidence")
        XCTAssertEqual(engine.state.confirmation?.purpose, .resumeGuidance)
        let prompt = engine.state.confirmation!
        _ = receive(.confirmation(promptID: prompt.promptID, answer: .yes),
                    on: &engine, at: 106, eventID: "resume-yes", source: .user)
        XCTAssertEqual(engine.state.phase, .reacquiring)
        XCTAssertEqual(engine.state.target?.id, "cup")

        _ = receive(.physical(TaskFixtures.physical(targetID: "cup", nowMs: 107)),
                    on: &engine, at: 107, eventID: "new-evidence")
        XCTAssertEqual(engine.state.phase, .guiding)
        _ = receive(.guidance(mode: .hand, targetID: "cup", status: .onTarget,
                              evidence: TaskFixtures.physical(targetID: "cup", nowMs: 108)),
                    on: &engine, at: 108, eventID: "on-target")
        XCTAssertEqual(engine.state.phase, .verifying)
        let retry = receive(.verification(.failed), on: &engine, at: 109, eventID: "failed-verification")
        XCTAssertTrue(retry.contains { if case .reacquire(_, let target) = $0 { return target.id == "cup" }; return false })
        XCTAssertFalse(retry.contains { if case .resolveTarget = $0 { return true }; return false })
    }
}
