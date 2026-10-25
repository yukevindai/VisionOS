import XCTest
@testable import Tasks

final class InformationalAnswerTests: XCTestCase {
    func testAnswerSpeaksDirectlyWithoutPhysicalSuccessOrGuidance() throws {
        var h = TaskEngineTests.Harness(); h.start(.object)
        let effects = h.send(.answer("  SIMULATED: A blue cup is on the table.  "), source: .r3)
        let result = try XCTUnwrap(h.engine.state.result)
        XCTAssertEqual(effects, [.speak(text: "SIMULATED: A blue cup is on the table.", priority: 4), .finished(result)])
        XCTAssertEqual(result.status, .answered)
        XCTAssertEqual(result.verification, .unverified)
        XCTAssertEqual(h.engine.state.phase, .completed)
        XCTAssertNil(h.engine.state.confirmation)
        XCTAssertNil(h.engine.state.target)
        XCTAssertNil(h.engine.state.pendingRequest)
    }

    func testAnswersCannotBypassSourceTaskOrPhaseGates() {
        for source in [EventSource.model, .r1, .user] {
            var h = TaskEngineTests.Harness(); h.start(.object)
            XCTAssertEqual(h.send(.answer("SIMULATED answer"), source: source), [.rejected(.wrongSource)])
        }
        for kind in [TaskKind.button, .approach, .door, .kiosk, .wallMenu] {
            var h = TaskEngineTests.Harness(); h.start(kind)
            XCTAssertEqual(h.send(.answer("SIMULATED answer"), source: .r3), [.rejected(.unsupportedState)])
        }
        var h = TaskEngineTests.Harness(); h.start(.object); h.resolve(TaskFixtures.target(kind: .object))
        XCTAssertEqual(h.send(.answer("SIMULATED answer"), source: .r3), [.rejected(.unsupportedState)])
    }

    func testMalformedAnswersAreNotSpoken() {
        for answer in ["", " \n\t ", String(repeating: "x", count: 2_001), "answer\u{0000}"] {
            var h = TaskEngineTests.Harness(); h.start(.object)
            XCTAssertEqual(h.send(.answer(answer), source: .r3), [.rejected(.malformed)])
            XCTAssertNil(h.engine.state.result)
        }
    }

    func testStopAndExpiredContextRejectLateAnswers() {
        var h = TaskEngineTests.Harness(); h.start(.object)
        let request = h.engine.state.pendingRequest!
        _ = h.engine.reduce(.stop(.userPause), nowMs: h.now)
        XCTAssertEqual(h.send(.answer("SIMULATED late answer"), source: .r3, context: request), [.rejected(.wrongContext)])
        var expired = TaskEngineTests.Harness(); expired.start(.object)
        let pending = expired.engine.state.pendingRequest!
        let envelope = TaskFixtures.envelope(context: pending, eventID: "expired", nowMs: expired.now, source: .r3)
        let effects = expired.engine.reduce(.receive(envelope, .answer("SIMULATED expired answer")), nowMs: pending.expiresAtMs)
        XCTAssertEqual(effects, [.rejected(.stale)])
        XCTAssertNil(expired.engine.state.result)
    }
}
