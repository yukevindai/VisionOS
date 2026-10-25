import XCTest
@testable import Tasks

final class ObjectRecognitionTimeoutTests: XCTestCase {
    func testObjectRecognitionWaitsOneMinuteAndThenFailsClosed() {
        var h = TaskEngineTests.Harness(); h.start(.object)
        let request = h.engine.state.pendingRequest!
        XCTAssertEqual(request.expiresAtMs - request.issuedAtMs, 60_000)
        for elapsed: Int64 in [5_000, 59_999] {
            h.now = request.issuedAtMs + elapsed
            XCTAssertEqual(h.engine.reduce(.tick, nowMs: h.now), [])
            XCTAssertEqual(h.engine.state.phase, .resolving)
            XCTAssertNil(h.engine.state.result)
        }
        h.now = request.expiresAtMs
        _ = h.engine.reduce(.tick, nowMs: h.now)
        XCTAssertEqual(h.engine.state.result?.error, .providerTimeout)
        XCTAssertNil(h.engine.state.target)
        XCTAssertEqual(h.send(.target(TaskFixtures.target(kind: .object)), source: .r3, context: request),
                       [.rejected(.malformed)])
    }

    func testExtendedRecognitionStillCancelsImmediatelyOnStop() {
        var h = TaskEngineTests.Harness(); h.start(.object)
        let request = h.engine.state.pendingRequest!
        h.now += 10_000
        let effects = h.engine.reduce(.stop(.userPause), nowMs: h.now)
        XCTAssertTrue(effects.contains(.cancelWork(request.task)))
        XCTAssertEqual(h.engine.state.phase, .paused)
        XCTAssertNil(h.engine.state.pendingRequest)
        XCTAssertEqual(h.send(.target(TaskFixtures.target(kind: .object)), source: .r3, context: request),
                       [.rejected(.wrongContext)])
    }

    func testRecognitionExtensionDoesNotChangePhysicalOrConfirmationDeadlines() {
        var h = TaskEngineTests.Harness(); h.start(.object)
        h.now += 10_000
        h.resolve(TaskFixtures.target(kind: .object))
        let physical = h.engine.state.pendingRequest!
        XCTAssertEqual(physical.expiresAtMs - physical.issuedAtMs, 5_000)
        h.acquire(); h.onTarget(); h.send(.verification(.unavailable))
        let confirmation = h.engine.state.confirmation!.context
        XCTAssertEqual(confirmation.expiresAtMs - confirmation.issuedAtMs, 30_000)
        XCTAssertEqual(h.engine.configuration.freshnessMs, 300)
        for kind in [TaskKind.button, .door, .approach, .kiosk, .wallMenu] {
            var other = TaskEngineTests.Harness(); other.start(kind)
            let request = other.engine.state.pendingRequest!
            XCTAssertEqual(request.expiresAtMs - request.issuedAtMs, 5_000)
        }
    }

    func testObjectRecognitionTimeoutIsConfigurable() {
        var engine = TaskEngine(sessionID: "SIMULATED", configuration: .init(objectRecognitionTimeoutMs: 42_000))
        _ = engine.reduce(.start(TaskRequest(id: "object", kind: .object, query: "SIMULATED keys")), nowMs: 100)
        XCTAssertEqual(engine.state.pendingRequest?.expiresAtMs, 42_100)
    }
}
