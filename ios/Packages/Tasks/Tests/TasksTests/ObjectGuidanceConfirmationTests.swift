import XCTest
@testable import Tasks

final class ObjectGuidanceConfirmationTests: XCTestCase {
    private func candidate(kind: TargetKind = .object, safety: ControlSafety = .unassessedObject,
                           approach: Bool = false, label: String = "SIMULATED keys") -> ResolvedTarget {
        TaskFixtures.target(kind: kind, label: label, requiresApproach: approach, safety: safety)
    }

    func testUnassessedObjectRequiresUserConfirmationThenFreshPhysicalEvidence() {
        var h = TaskEngineTests.Harness(); h.start(.object)
        let effects = h.resolve(candidate())
        XCTAssertEqual(h.engine.state.confirmation?.purpose, .objectGuidance)
        XCTAssertFalse(effects.contains { if case .reacquire = $0 { return true }; return false })
        XCTAssertFalse(effects.contains { if case .requestGuidance = $0 { return true }; return false })
        XCTAssertEqual(h.acquire(), [.rejected(.unsupportedState)])
        let prompt = h.engine.state.confirmation!
        XCTAssertEqual(h.send(.confirmation(promptID: prompt.promptID, answer: .yes), source: .model), [.rejected(.wrongSource)])
        h.now += 1_000
        let confirmed = h.confirm()
        XCTAssertEqual(h.engine.state.phase, .reacquiring)
        XCTAssertEqual(h.engine.state.target?.safety, .unassessedObject)
        XCTAssertNil(h.engine.state.confirmation)
        XCTAssertTrue(confirmed.contains { if case .reacquire = $0 { return true }; return false })
        XCTAssertFalse(confirmed.contains { if case .requestGuidance = $0 { return true }; return false })
        XCTAssertFalse(h.engine.isGuidanceDispatchAllowed(context: h.engine.state.pendingRequest!,
                        evidence: TaskFixtures.physical(nowMs: h.now), nowMs: h.now))
        h.acquire()
        XCTAssertEqual(h.engine.state.phase, .guiding)
        XCTAssertFalse(h.engine.isGuidanceDispatchAllowed(context: h.engine.state.pendingRequest!,
                        evidence: TaskFixtures.physical(nowMs: h.now, depthUsable: false), nowMs: h.now))
    }

    func testNoCancelsAndStopInvalidatesOldConfirmation() {
        for decline in [true, false] {
            var h = TaskEngineTests.Harness(); h.start(.object); h.resolve(candidate())
            let prompt = h.engine.state.confirmation!
            if decline { h.confirm(.no) }
            else { _ = h.engine.reduce(.stop(.userPause), nowMs: h.now) }
            XCTAssertNil(h.engine.state.target)
            XCTAssertNil(h.engine.state.confirmation)
            XCTAssertEqual(h.send(.confirmation(promptID: prompt.promptID, answer: .yes), source: .user,
                                  context: prompt.context), [.rejected(.wrongContext)])
        }
    }

    func testExpiredConfirmationCannotAuthorizeReacquisition() {
        var h = TaskEngineTests.Harness(); h.start(.object); h.resolve(candidate())
        let prompt = h.engine.state.confirmation!
        h.now = prompt.context.expiresAtMs + 1
        let effects = h.confirm()
        XCTAssertFalse(effects.contains { if case .reacquire = $0 { return true }; return false })
        XCTAssertNotEqual(h.engine.state.phase, .guiding)
    }

    func testConfirmationCannotBypassFreshHandDepthAndTrackingEvidence() {
        for failure in 0..<4 {
            var h = TaskEngineTests.Harness(); h.start(.object); h.resolve(candidate()); h.confirm()
            let evidence = PhysicalEvidence(targetID: h.engine.state.target!.id, frameID: "SIMULATED-current",
                observedAtMs: failure == 0 ? 0 : h.now, targetVisible: true,
                handVisible: failure != 1, trackingNormal: failure != 2, depthUsable: failure != 3,
                freeSpaceObserved: true, outputReady: true)
            if failure == 0 { h.now += 400 }
            let effects = h.acquire(evidence)
            XCTAssertEqual(h.engine.state.phase, .paused)
            XCTAssertFalse(effects.contains { if case .requestGuidance = $0 { return true }; return false })
        }
    }

    func testUnassessedObjectsNeverAuthorizeOtherTasksApproachOrUnsafeControls() {
        for task in [TaskKind.button, .door, .approach] {
            var h = TaskEngineTests.Harness(); h.start(task); h.resolve(candidate())
            XCTAssertNil(h.engine.state.target)
            XCTAssertNil(h.engine.state.confirmation)
        }
        for target in [candidate(kind: .button), candidate(approach: true), candidate(safety: .emergency),
                       candidate(safety: .payment), candidate(label: "SIMULATED buy keys")] {
            var h = TaskEngineTests.Harness(); h.start(.object); h.resolve(target)
            XCTAssertNil(h.engine.state.target)
            XCTAssertNil(h.engine.state.confirmation)
        }
    }
}
