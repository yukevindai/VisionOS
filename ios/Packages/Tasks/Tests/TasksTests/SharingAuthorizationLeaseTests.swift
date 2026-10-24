import XCTest
@testable import Tasks

final class SharingAuthorizationLeaseTests: XCTestCase {
    private func scope(epoch: String = "epoch", authorization: UInt64 = 1,
                       connection: String = "helper-connection", lease: UInt64 = 1) -> SharingAuthorizationScope {
        .init(sessionID: "session", sessionEpoch: epoch, authorizationVersion: authorization,
              helper: .init(connectionID: connection, leaseVersion: lease))
    }

    private func policy(_ scope: SharingAuthorizationScope? = nil, instance: String = "local-instance") throws -> SharingAuthorizationLease {
        try .init(scope: scope ?? self.scope(), renewalInstanceID: instance)
    }

    private func request(_ policy: inout SharingAuthorizationLease, at now: Int64) throws -> SharingLeaseRenewal {
        let effects = policy.reduce(.tick, nowMs: now)
        guard case .requestRenewal(let request) = effects.first else {
            XCTFail("Expected renewal request, got \(effects)")
            throw NSError(domain: "SharingLeaseTest", code: 1)
        }
        return request
    }

    private func grant(_ request: SharingLeaseRenewal, duration: Int64 = 15_000) -> SharingLeaseEvent {
        .granted(.init(scope: request.scope, nonce: request.nonce, validForMs: duration))
    }

    private func assertClosed(_ policy: SharingAuthorizationLease, _ effects: [SharingLeaseEffect],
                              reason: SharingLeaseClosure, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(policy.state, .closed(reason), file: file, line: line)
        XCTAssertNil(policy.pendingRenewal, file: file, line: line)
        XCTAssertNil(policy.nextWakeupMs, file: file, line: line)
        XCTAssertEqual(effects, [.cancelFriendProposals, .pauseFriendInitiatedTask,
                                .unpublishSharedMedia, .requestMediaEviction(policy.scope)], file: file, line: line)
    }

    func testDelayedResponseUsesRequestTimeAndExpiresLocally() throws {
        var p = try policy()
        XCTAssertFalse(p.permitsNewSelection(nowMs: 1_000))
        let r = try request(&p, at: 1_000)
        XCTAssertEqual(p.reduce(grant(r), nowMs: 15_999), [])
        XCTAssertEqual(p.state, .authorized(deadlineMs: 16_000))
        XCTAssertTrue(p.permitsNewSelection(nowMs: 15_999))
        XCTAssertFalse(p.permitsNewSelection(nowMs: 16_000))
        let effects = p.reduce(.tick, nowMs: 16_000)
        assertClosed(p, effects, reason: .expired)
        XCTAssertEqual(p.reduce(.tick, nowMs: 17_000), [])
    }

    func testGrantReceivedAtItsDeadlineNeverActivatesSharing() throws {
        var p = try policy()
        let r = try request(&p, at: 1_000)
        let effects = p.reduce(grant(r), nowMs: 16_000)
        assertClosed(p, effects, reason: .expired)
    }

    func testNewRequestSupersedesOldAndDuplicatesNeverExtendLease() throws {
        var p = try policy()
        let a = try request(&p, at: 1_000)
        let b = try request(&p, at: 6_000)
        XCTAssertNotEqual(a.nonce, b.nonce)
        XCTAssertEqual(p.reduce(grant(a), nowMs: 6_001), [.rejected(.unexpectedGrant)])
        XCTAssertEqual(p.pendingRenewal, b)
        p.reduce(grant(b), nowMs: 6_002)
        XCTAssertEqual(p.state, .authorized(deadlineMs: 21_000))
        XCTAssertEqual(p.reduce(grant(b), nowMs: 7_000), [.rejected(.unexpectedGrant)])
        XCTAssertEqual(p.reduce(grant(a), nowMs: 8_000), [.rejected(.unexpectedGrant)])
        XCTAssertEqual(p.state, .authorized(deadlineMs: 21_000))
    }

    func testWrongBindingsAndNoncePreserveLegitimatePendingRenewal() throws {
        var p = try policy()
        let r = try request(&p, at: 1_000)
        let scopes = [scope(epoch: "old"), scope(authorization: 2), scope(connection: "other"), scope(lease: 2),
                      SharingAuthorizationScope(sessionID: "other", sessionEpoch: "epoch", authorizationVersion: 1, helper: r.scope.helper)]
        for wrong in scopes {
            XCTAssertEqual(p.reduce(.granted(.init(scope: wrong, nonce: r.nonce, validForMs: 15_000)), nowMs: 1_001),
                           [.rejected(.unexpectedGrant)])
            XCTAssertEqual(p.pendingRenewal, r)
        }
        XCTAssertEqual(p.reduce(.granted(.init(scope: r.scope, nonce: "unsolicited", validForMs: 15_000)), nowMs: 1_001),
                       [.rejected(.unexpectedGrant)])
        XCTAssertEqual(p.pendingRenewal, r)
        p.reduce(grant(r), nowMs: 1_002)
        XCTAssertTrue(p.permitsNewSelection(nowMs: 1_002))
    }

    func testExpiredAuthorityWinsOverOutstandingRenewalSuccess() throws {
        var p = try policy()
        let a = try request(&p, at: 1_000)
        p.reduce(grant(a), nowMs: 1_001)
        let b = try request(&p, at: 11_000)
        let effects = p.reduce(grant(b), nowMs: 16_000)
        assertClosed(p, effects, reason: .expired)
        XCTAssertEqual(p.reduce(grant(b), nowMs: 16_001), [])
    }

    func testBackendLossDisablesProposalsImmediatelyAndSharingAtOriginalDeadline() throws {
        var p = try policy()
        let a = try request(&p, at: 1_000)
        p.reduce(grant(a), nowMs: 1_001)
        XCTAssertEqual(p.reduce(.backendUnavailable, nowMs: 2_000), [.cancelFriendProposals, .pauseFriendInitiatedTask])
        XCTAssertFalse(p.permitsNewSelection(nowMs: 2_000))
        XCTAssertEqual(p.state, .authorized(deadlineMs: 16_000))
        XCTAssertEqual(p.reduce(.backendUnavailable, nowMs: 2_001), [])
        _ = try request(&p, at: 6_000)
        let effects = p.reduce(.tick, nowMs: 16_000)
        assertClosed(p, effects, reason: .expired)
    }

    func testFailedRenewalCannotExtendAuthorityOrLeavePriorReplyActionable() throws {
        var p = try policy()
        let a = try request(&p, at: 1_000)
        p.reduce(grant(a), nowMs: 1_001)
        let b = try request(&p, at: 6_000)
        XCTAssertEqual(p.reduce(.renewalFailed(scope: b.scope, nonce: b.nonce), nowMs: 6_001),
                       [.cancelFriendProposals, .pauseFriendInitiatedTask])
        XCTAssertNil(p.pendingRenewal)
        XCTAssertEqual(p.reduce(grant(b), nowMs: 6_002), [.rejected(.unexpectedGrant)])
        XCTAssertEqual(p.state, .authorized(deadlineMs: 16_000))
        XCTAssertFalse(p.permitsNewSelection(nowMs: 6_002))
    }

    func testStaleFailuresAndRevocationsCannotAffectNewHelper() throws {
        var old = try policy()
        let a = try request(&old, at: 1_000)
        var fresh = try policy(scope(authorization: 2, connection: "new-helper", lease: 2), instance: "fresh-instance")
        let b = try request(&fresh, at: 1_000)
        fresh.reduce(grant(b), nowMs: 1_001)
        XCTAssertEqual(fresh.reduce(.renewalFailed(scope: a.scope, nonce: a.nonce), nowMs: 1_002), [])
        XCTAssertEqual(fresh.reduce(.revoked(a.scope), nowMs: 1_002), [.rejected(.staleRevocation)])
        XCTAssertEqual(fresh.reduce(grant(a), nowMs: 1_002), [.rejected(.unexpectedGrant)])
        XCTAssertTrue(fresh.permitsNewSelection(nowMs: 1_002))
    }

    func testEndAndRevocationAreTerminalEvenIfReconnectSucceeds() throws {
        for end in [true, false] {
            var p = try policy()
            let a = try request(&p, at: 1_000)
            p.reduce(grant(a), nowMs: 1_001)
            let b = try request(&p, at: 6_000)
            let effects = p.reduce(end ? .end : .revoked(p.scope), nowMs: 6_001)
            assertClosed(p, effects, reason: end ? .ended : .revoked)
            XCTAssertEqual(p.reduce(.tick, nowMs: 7_000), [])
            XCTAssertEqual(p.reduce(grant(b), nowMs: 7_001), [])
            XCTAssertFalse(p.permitsNewSelection(nowMs: 7_001))
        }
    }

    func testSuccessfulRecoveryBeforeExpiryNeverEmitsResume() throws {
        var p = try policy()
        let a = try request(&p, at: 1_000)
        p.reduce(grant(a), nowMs: 1_001)
        p.reduce(.backendUnavailable, nowMs: 2_000)
        let b = try request(&p, at: 6_000)
        XCTAssertEqual(p.reduce(grant(b), nowMs: 6_001), [])
        XCTAssertTrue(p.permitsNewSelection(nowMs: 6_001))
        XCTAssertEqual(p.state, .authorized(deadlineMs: 21_000))
    }

    func testInvalidDurationsNeverAuthorizeAndDoNotConsumeValidPendingNonce() throws {
        var p = try policy()
        let r = try request(&p, at: 1_000)
        for duration: Int64 in [-1, 0, 15_001, Int64.max] {
            XCTAssertEqual(p.reduce(grant(r, duration: duration), nowMs: 1_001), [.rejected(.invalidDuration)])
            XCTAssertEqual(p.state, .awaitingAuthorization)
            XCTAssertEqual(p.pendingRenewal, r)
        }
        p.reduce(grant(r, duration: 1), nowMs: 1_001)
        XCTAssertEqual(p.state, .closed(.expired))
    }

    func testClockRegressionAndNegativeTimeFailClosed() throws {
        for invalid: Int64 in [-1, 999] {
            var p = try policy()
            let r = try request(&p, at: 1_000)
            p.reduce(grant(r), nowMs: 1_001)
            let effects = p.reduce(.tick, nowMs: invalid)
            assertClosed(p, effects, reason: .invalidClock)
        }
    }

    func testOverflowFailsClosedWithoutTrapping() throws {
        var p = try policy()
        let effects = p.reduce(.tick, nowMs: Int64.max)
        assertClosed(p, effects, reason: .invalidClock)
        var q = try policy()
        let r = try request(&q, at: Int64.max - 6_000)
        let grantEffects = q.reduce(grant(r), nowMs: Int64.max - 5_999)
        assertClosed(q, grantEffects, reason: .invalidClock)
    }

    func testWatchdogSelectsEarlierExpiryAndRenewalsAreThrottled() throws {
        var p = try policy()
        let r = try request(&p, at: 1_000)
        XCTAssertEqual(p.nextWakeupMs, 6_000)
        XCTAssertEqual(p.reduce(.tick, nowMs: 1_001), [])
        p.reduce(grant(r, duration: 2_000), nowMs: 1_002)
        XCTAssertEqual(p.nextWakeupMs, 3_000)
        let effects = p.reduce(.tick, nowMs: 3_000)
        assertClosed(p, effects, reason: .expired)
    }

    func testWrongMessageStillProcessesDueExpiry() throws {
        var p = try policy()
        let r = try request(&p, at: 1_000)
        p.reduce(grant(r), nowMs: 1_001)
        let effects = p.reduce(.granted(.init(scope: scope(epoch: "wrong"), nonce: "wrong", validForMs: 15_000)), nowMs: 16_000)
        assertClosed(p, effects, reason: .expired)
    }

    func testSharingWithoutHelperCanRenewButCannotAcceptSelections() throws {
        let privateScope = SharingAuthorizationScope(sessionID: "session", sessionEpoch: "epoch", authorizationVersion: 1, helper: nil)
        var p = try policy(privateScope)
        let r = try request(&p, at: 1_000)
        p.reduce(grant(r), nowMs: 1_001)
        XCTAssertEqual(p.state, .authorized(deadlineMs: 16_000))
        XCTAssertFalse(p.permitsNewSelection(nowMs: 1_001))
    }

    func testConfigurationRejectsInvalidIdentifiersAndUnsafeIntegers() throws {
        XCTAssertThrowsError(try policy(scope(epoch: " ")))
        XCTAssertThrowsError(try policy(scope(authorization: SharingAuthorizationLease.maximumSharedInteger + 1)))
        XCTAssertThrowsError(try policy(scope(connection: "")))
        XCTAssertThrowsError(try policy(scope(lease: UInt64.max)))
        XCTAssertThrowsError(try policy(instance: ""))
        XCTAssertThrowsError(try policy(instance: String(repeating: "a", count: 257)))
    }

    func testStaleFailureWithSameScopePreservesLatestPendingRenewal() throws {
        var p = try policy()
        let a = try request(&p, at: 1_000)
        let b = try request(&p, at: 6_000)
        XCTAssertEqual(p.reduce(.renewalFailed(scope: a.scope, nonce: a.nonce), nowMs: 6_001), [])
        XCTAssertEqual(p.pendingRenewal, b)
        XCTAssertTrue(p.backendAvailable)
        p.reduce(grant(b), nowMs: 6_002)
        XCTAssertTrue(p.permitsNewSelection(nowMs: 6_002))
    }

    func testFreshInstanceRejectsOldGrantEvenWhenScopeIsIdentical() throws {
        var old = try policy(instance: "old-instance")
        let a = try request(&old, at: 1_000)
        var fresh = try policy(instance: "new-instance")
        let b = try request(&fresh, at: 1_000)
        XCTAssertEqual(fresh.reduce(grant(a), nowMs: 1_001), [.rejected(.unexpectedGrant)])
        XCTAssertEqual(fresh.pendingRenewal, b)
        XCTAssertFalse(fresh.permitsNewSelection(nowMs: 1_001))
    }

    func testRepeatedTicksWithoutBackendGrantNeverAuthorizeSharing() throws {
        var p = try policy()
        for now: Int64 in stride(from: 0, through: 60_000, by: 5_000) {
            _ = try request(&p, at: now)
            XCTAssertEqual(p.state, .awaitingAuthorization)
            XCTAssertFalse(p.permitsNewSelection(nowMs: now))
        }
    }
}
