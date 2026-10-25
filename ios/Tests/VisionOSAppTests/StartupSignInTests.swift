import XCTest
@testable import VisionOS

@MainActor
private final class StartupTestIdentity: StartupIdentity {
    var status: NativeIdentityStatus = .signedOut
    var restoredStatus: NativeIdentityStatus = .signedOut
    var signInError: Error?
    var signInCalls = 0
    var loadCalls = 0
    var suspendLoad = false
    var loadContinuation: CheckedContinuation<Void, Never>?

    func loadStoredAccount() async {
        loadCalls += 1
        if suspendLoad { await withCheckedContinuation { loadContinuation = $0 } }
        status = restoredStatus
    }

    func signIn() async throws {
        signInCalls += 1
        if let signInError { throw signInError }
        status = .signedIn(accountID: "owner")
    }
}

@MainActor
final class StartupSignInTests: XCTestCase {
    func testSignedOutLaunchDoesNotOpenBrowserOrCreateAccount() async {
        let identity = StartupTestIdentity()
        var ensures = 0
        let model = StartupSignInModel(identity: identity, ensureAccount: { ensures += 1 })
        await model.load()
        await model.load()
        XCTAssertEqual(identity.loadCalls, 1)
        XCTAssertEqual(identity.signInCalls, 0)
        XCTAssertEqual(ensures, 0)
        XCTAssertFalse(model.hasContinued)
        XCTAssertFalse(model.busy)
    }

    func testStoredAccountIsEnsuredBeforeProceedingWithoutBrowser() async {
        let identity = StartupTestIdentity()
        identity.restoredStatus = .signedIn(accountID: "owner")
        var continuation: CheckedContinuation<Void, Never>?
        let model = StartupSignInModel(identity: identity, ensureAccount: {
            await withCheckedContinuation { continuation = $0 }
        })
        let load = Task { await model.load() }
        for _ in 0..<100 where continuation == nil { await Task.yield() }
        XCTAssertNotNil(continuation)
        XCTAssertFalse(model.hasContinued)
        XCTAssertTrue(model.busy)
        continuation?.resume()
        await load.value
        XCTAssertTrue(model.hasContinued)
        XCTAssertEqual(identity.signInCalls, 0)
    }

    func testExplicitSignInEnsuresAccountAndContinues() async {
        let identity = StartupTestIdentity()
        var ensures = 0
        let model = StartupSignInModel(identity: identity, ensureAccount: { ensures += 1 })
        await model.signIn()
        XCTAssertTrue(model.hasContinued)
        XCTAssertEqual(identity.signInCalls, 1)
        XCTAssertEqual(ensures, 1)
    }

    func testCancelledLoginCanRetryWithoutProceedingEarly() async {
        let identity = StartupTestIdentity()
        identity.signInError = CancellationError()
        var ensures = 0
        let model = StartupSignInModel(identity: identity, ensureAccount: { ensures += 1 })
        await model.signIn()
        XCTAssertFalse(model.hasContinued)
        XCTAssertFalse(model.busy)
        XCTAssertNotNil(model.message)
        XCTAssertEqual(ensures, 0)
        identity.signInError = nil
        await model.signIn()
        XCTAssertTrue(model.hasContinued)
        XCTAssertNil(model.message)
    }

    func testAccountSetupFailureCanRetryWithoutAnotherBrowserLogin() async {
        let identity = StartupTestIdentity()
        var fail = true
        let model = StartupSignInModel(identity: identity, ensureAccount: {
            if fail { throw URLError(.notConnectedToInternet) }
        })
        await model.signIn()
        XCTAssertFalse(model.hasContinued)
        XCTAssertNotNil(model.message)
        fail = false
        await model.signIn()
        XCTAssertTrue(model.hasContinued)
        XCTAssertEqual(identity.signInCalls, 1)
    }

    func testSkipDuringRestoreDoesNotWaitForOrStartAccountSetup() async {
        let identity = StartupTestIdentity()
        identity.suspendLoad = true
        identity.restoredStatus = .signedIn(accountID: "owner")
        var ensures = 0
        let model = StartupSignInModel(identity: identity, ensureAccount: { ensures += 1 })
        let load = Task { await model.load() }
        for _ in 0..<100 where identity.loadContinuation == nil { await Task.yield() }
        model.continueWithoutSignIn()
        XCTAssertTrue(model.hasContinued)
        identity.loadContinuation?.resume()
        await load.value
        XCTAssertEqual(ensures, 0)
    }

    func testRepeatedSignInWhileAccountSetupPendingDoesNotDuplicateRequests() async {
        let identity = StartupTestIdentity()
        var continuation: CheckedContinuation<Void, Never>?
        let model = StartupSignInModel(identity: identity, ensureAccount: {
            await withCheckedContinuation { continuation = $0 }
        })
        let first = Task { await model.signIn() }
        for _ in 0..<100 where continuation == nil { await Task.yield() }
        await model.signIn()
        XCTAssertEqual(identity.signInCalls, 1)
        continuation?.resume()
        await first.value
        XCTAssertTrue(model.hasContinued)
    }

    func testMissingConfigurationStillAllowsLocalUse() {
        let model = StartupSignInModel(identity: nil, ensureAccount: {})
        XCTAssertFalse(model.isConfigured)
        XCTAssertNotNil(model.message)
        model.continueWithoutSignIn()
        XCTAssertTrue(model.hasContinued)
    }
}
