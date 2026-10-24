import Foundation
import XCTest
@testable import Reflex

private final class GateClock: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Int64 = 1_000
    var now: Int64 { lock.lock(); defer { lock.unlock() }; return value }
    func advance(_ delta: Int64) { lock.lock(); value += delta; lock.unlock() }
}

final class NativeMediaSampleGateTests: XCTestCase {
    func testClosedBootstrapAndExactAttemptFreshFramesOnly() {
        let clock = GateClock(), attempt = UUID()
        let gate = NativeMediaSampleGate(attempt: attempt, nowMs: { clock.now })
        var admitted = 0
        XCTAssertFalse(gate.withAdmission(attempt: attempt, capturedAtMs: clock.now) { admitted += 1 })
        XCTAssertTrue(gate.authorize(untilMs: clock.now + 15_000))
        XCTAssertFalse(gate.withAdmission(attempt: UUID(), capturedAtMs: clock.now) { admitted += 1 })
        XCTAssertFalse(gate.withAdmission(attempt: attempt, capturedAtMs: clock.now + 1) { admitted += 1 })
        XCTAssertFalse(gate.withAdmission(attempt: attempt, capturedAtMs: clock.now - 301) { admitted += 1 })
        XCTAssertTrue(gate.withAdmission(attempt: attempt, capturedAtMs: clock.now - 300) { admitted += 1 })
        XCTAssertEqual(admitted, 1)
    }

    func testQueuedFrameFromPreviousAdmissionCannotCrossSuspendResume() {
        let clock = GateClock(), attempt = UUID()
        let gate = NativeMediaSampleGate(attempt: attempt, nowMs: { clock.now })
        XCTAssertTrue(gate.authorize(untilMs: clock.now + 100))
        let capturedRevision = gate.revision
        XCTAssertTrue(gate.authorize(untilMs: clock.now + 200))
        XCTAssertEqual(gate.revision, capturedRevision, "Ordinary renewal preserves current admission")
        gate.suspend()
        XCTAssertTrue(gate.authorize(untilMs: clock.now + 200))
        XCTAssertFalse(gate.withAdmission(attempt: attempt, capturedAtMs: clock.now,
                                         expectedRevision: capturedRevision) { XCTFail("Stale queued frame") })
        XCTAssertTrue(gate.withAdmission(attempt: attempt, capturedAtMs: clock.now,
                                        expectedRevision: gate.revision) {})
    }

    func testOffQueueSuspensionRejectsGrantIssuedBeforeMembershipCallback() {
        let clock = GateClock(), attempt = UUID()
        let gate = NativeMediaSampleGate(attempt: attempt, nowMs: { clock.now })
        XCTAssertTrue(gate.authorize(untilMs: clock.now + 100))
        let requestedRevision = gate.revision
        gate.suspend()
        XCTAssertFalse(gate.authorize(untilMs: clock.now + 200, expectedRevision: requestedRevision))
        XCTAssertFalse(gate.isOpen(attempt: attempt))
        XCTAssertTrue(gate.authorize(untilMs: clock.now + 200, expectedRevision: gate.revision))
    }

    func testLocalSuspensionCannotConsumeAnUnseenSDKRevision() {
        let clock = GateClock(), attempt = UUID()
        let gate = NativeMediaSampleGate(attempt: attempt, nowMs: { clock.now })
        XCTAssertTrue(gate.authorize(untilMs: clock.now + 100, expectedRevision: 0))
        XCTAssertEqual(gate.revision, 0)
        let sdkRevision = gate.suspend()
        XCTAssertEqual(sdkRevision, 1)
        XCTAssertNil(gate.suspend(expectedRevision: 0))
        XCTAssertEqual(gate.revision, sdkRevision)
        XCTAssertFalse(gate.authorize(untilMs: clock.now + 100, expectedRevision: 0))
        XCTAssertTrue(gate.authorize(untilMs: clock.now + 100, expectedRevision: sdkRevision))
        XCTAssertEqual(gate.revision, sdkRevision)
    }

    func testExpiryFailsClosedWithoutWatchdogOrMainActor() {
        let clock = GateClock(), attempt = UUID()
        let gate = NativeMediaSampleGate(attempt: attempt, nowMs: { clock.now })
        XCTAssertTrue(gate.authorize(untilMs: clock.now + 100))
        clock.advance(100)
        XCTAssertFalse(gate.withAdmission(attempt: attempt, capturedAtMs: clock.now) { XCTFail("Expired frame") })
        XCTAssertFalse(gate.authorize(untilMs: clock.now + 15_000))
    }

    func testSuspensionRetainsExpiryAndCannotResurrectLateGrant() {
        let clock = GateClock(), attempt = UUID()
        let gate = NativeMediaSampleGate(attempt: attempt, nowMs: { clock.now })
        XCTAssertTrue(gate.authorize(untilMs: clock.now + 100))
        gate.suspend()
        XCTAssertFalse(gate.isOpen(attempt: attempt))
        clock.advance(100)
        XCTAssertFalse(gate.authorize(untilMs: clock.now + 15_000))
        XCTAssertFalse(gate.isOpen(attempt: attempt))
    }

    func testFreshAuthorizationCanResumeSuspensionBeforeExpiryButNeverTerminalClose() {
        let clock = GateClock(), attempt = UUID()
        let gate = NativeMediaSampleGate(attempt: attempt, nowMs: { clock.now })
        XCTAssertFalse(gate.authorize(untilMs: clock.now + 15_001))
        XCTAssertTrue(gate.authorize(untilMs: clock.now + 100))
        gate.suspend()
        clock.advance(10)
        XCTAssertTrue(gate.authorize(untilMs: clock.now + 100))
        XCTAssertTrue(gate.isOpen(attempt: attempt))
        gate.close()
        XCTAssertFalse(gate.authorize(untilMs: clock.now + 100))
        XCTAssertFalse(gate.isOpen(attempt: attempt))
    }
}
