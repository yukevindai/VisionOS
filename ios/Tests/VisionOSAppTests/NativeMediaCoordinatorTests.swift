import CoreVideo
import Foundation
import XCTest
@testable import VisionOS

private final class MediaClockBox: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Int64 = 1_000
    func now() -> Int64 { lock.lock(); defer { lock.unlock() }; return value }
    func advance(_ ms: Int64) { lock.lock(); value += ms; lock.unlock() }
}

@MainActor
private final class MediaManualClock: AppClock {
    private let box: MediaClockBox
    private struct Entry { let deadline: Int64; let action: @MainActor () -> Void; var cancelled: Bool }
    private var entries: [UUID: Entry] = [:]
    init(_ box: MediaClockBox) { self.box = box }
    var nowMs: Int64 { box.now() }
    func hasScheduled(afterMs: Int64) -> Bool {
        entries.values.contains { $0.deadline == nowMs + afterMs }
    }
    func schedule(afterMs: Int64, _ action: @escaping @MainActor () -> Void) -> ScheduledWork {
        let id = UUID()
        entries[id] = Entry(deadline: nowMs + max(0, afterMs), action: action, cancelled: false)
        return ScheduledWork { [weak self] in self?.entries[id] = nil }
    }
    func advance(_ ms: Int64) {
        box.advance(ms)
        for _ in 0..<100 {
            let due = entries.filter { $0.value.deadline <= nowMs }.map(\.key)
            guard !due.isEmpty else { break }
            for id in due {
                if let entry = entries.removeValue(forKey: id) { entry.action() }
            }
        }
    }
}

@MainActor
private final class MediaFakeBackend: NativeMediaBackend {
    var onIdentityInvalidated: (() -> Void)?
    var snapshotValue: NativeMediaSnapshot?
    var snapshotCount = 0
    var renewCount = 0
    var endCount = 0
    var sharingDisabledCount = 0
    var lastBinding: NativeMediaBinding?
    var delayedSnapshotCall: Int?
    var heldSnapshot: CheckedContinuation<Void, Never>?
    var delayRenew = false
    var heldRenew: CheckedContinuation<Void, Never>?
    var versionAtSnapshotCall: [Int: UInt64] = [:]
    var corruptNonceAtCall: Int?
    let room = NativeMediaRoom(roomName: "room-A", roomSID: "sid-A", generation: 1)
    func ensureAccount() async throws {}
    func start(epoch: UUID) async throws -> NativeMediaBinding {
        let binding = NativeMediaBinding(sessionID: "opaque-session", epoch: epoch)
        lastBinding = binding
        return binding
    }
    func snapshot(_ binding: NativeMediaBinding, nonce: String) async throws -> NativeMediaSnapshot {
        snapshotCount += 1
        if snapshotCount == delayedSnapshotCall {
            await withCheckedContinuation { heldSnapshot = $0 }
        }
        guard let snapshotValue else { throw NativeMediaTransportError.unavailable }
        return NativeMediaSnapshot(sessionID: binding.sessionID, epoch: binding.epoch,
                                   status: snapshotValue.status,
                                   authorizationVersion: versionAtSnapshotCall[snapshotCount] ?? snapshotValue.authorizationVersion,
                                   sharing: snapshotValue.sharing, cleanupPending: snapshotValue.cleanupPending,
                                   media: snapshotValue.media, helper: snapshotValue.helper,
                                   validForMs: snapshotValue.validForMs,
                                   nonce: snapshotCount == corruptNonceAtCall ? "forged-nonce" : nonce)
    }
    func heartbeat(_ binding: NativeMediaBinding) async throws {}
    func provision(_ binding: NativeMediaBinding) async throws -> NativeMediaRoom { room }
    func ownerToken(_ binding: NativeMediaBinding, clientInstanceID: UUID) async throws -> NativeMediaToken {
        NativeMediaToken(token: "header.payload.signature", serverURL: URL(string: "wss://example.livekit.cloud")!,
                         room: room, identity: "owner:room-A", expiresAtMs: 9_999_999_999_999)
    }
    func renew(_ binding: NativeMediaBinding, authorizationVersion: UInt64,
               helper: NativeMediaHelperScope?, nonce: String) async throws -> NativeMediaRenewal {
        renewCount += 1
        if delayRenew { await withCheckedContinuation { heldRenew = $0 } }
        return NativeMediaRenewal(binding: binding, authorizationVersion: authorizationVersion,
                                  helper: helper, validForMs: 15_000, nonce: nonce)
    }
    func recover(_ binding: NativeMediaBinding) async throws {}
    func end(_ binding: NativeMediaBinding) async throws { endCount += 1 }
    func setSharing(_ binding: NativeMediaBinding, enabled: Bool) async throws {
        if !enabled { sharingDisabledCount += 1 }
    }
    func set(ownerConnected: Bool, sharing: Bool = false, helper: NativeMediaHelper = .empty) {
        snapshotValue = NativeMediaSnapshot(sessionID: "opaque-session", epoch: UUID(), status: .active,
                                            authorizationVersion: helper == .empty ? 1 : 2,
                                            sharing: sharing, cleanupPending: false,
                                            media: .ready(roomName: room.roomName, generation: room.generation,
                                                          roomSID: room.roomSID, ownerConnected: ownerConnected),
                                            helper: helper, validForMs: 15_000, nonce: "unused")
    }
    func retire() {
        guard let old = snapshotValue else { return }
        snapshotValue = NativeMediaSnapshot(sessionID: old.sessionID, epoch: old.epoch,
                                            status: .active, authorizationVersion: old.authorizationVersion + 1,
                                            sharing: false, cleanupPending: true,
                                            media: .retired(roomName: room.roomName, generation: room.generation),
                                            helper: .empty, validForMs: 15_000, nonce: "unused")
    }
}

@MainActor
private final class MediaFakeDriver: NativeMediaDriver {
    var onInvalidated: ((UUID) -> Void)?
    var onMembershipChanged: ((UUID, NativeMediaMembershipChange) -> Void)?
    var gate: NativeMediaSampleGate?
    var attempt: UUID?
    var connectCount = 0
    var disconnectCount = 0
    var sentCount = 0
    var holdConnect = false
    var connectContinuation: CheckedContinuation<Void, Never>?
    func connect(token: NativeMediaToken, attempt: UUID, gate: NativeMediaSampleGate) async throws {
        connectCount += 1
        self.attempt = attempt
        self.gate = gate
        if holdConnect { await withCheckedContinuation { connectContinuation = $0 } }
    }
    func disconnect(attempt: UUID) async {
        disconnectCount += 1
    }
    func sendVideo(_ pixelBuffer: CVPixelBuffer, capturedAtMs: Int64,
                   rotation: NativeMediaVideoRotation, attempt: UUID) { sentCount += 1 }
    func join(_ identity: String, sid: String) {
        guard let attempt else { return }
        guard let gateRevision = gate?.suspend() else { return }
        onMembershipChanged?(attempt, NativeMediaMembershipChange(identity: identity,
                                                                 participantSID: sid, joined: true,
                                                                 gateRevision: gateRevision))
    }
    func leave(_ identity: String, sid: String) {
        guard let attempt else { return }
        guard let gateRevision = gate?.suspend() else { return }
        onMembershipChanged?(attempt, NativeMediaMembershipChange(identity: identity,
                                                                 participantSID: sid, joined: false,
                                                                 gateRevision: gateRevision))
    }
}

@MainActor
private final class MediaFakeCapture: NativeMediaCaptureClient {
    var receive: ((NativeMediaVideoFrame) -> Void)?
    var localID: String?
    var detachCount = 0
    func attach(localSessionID: String, receive: @escaping (NativeMediaVideoFrame) -> Void) {
        self.localID = localSessionID
        self.receive = receive
    }
    func detach() { receive = nil; localID = nil; detachCount += 1 }
}

@MainActor
final class NativeMediaCoordinatorTests: XCTestCase {
    private func waitUntil(clock: MediaManualClock? = nil, _ condition: () -> Bool) async {
        let deadline = ContinuousClock.now.advanced(by: .seconds(2))
        while !condition() && ContinuousClock.now < deadline {
            clock?.advance(0)
            await Task.yield()
        }
        XCTAssertTrue(condition())
    }
    private func system() -> (NativeMediaCoordinator, MediaFakeBackend, MediaFakeDriver, MediaFakeCapture, MediaManualClock, MediaClockBox) {
        let box = MediaClockBox()
        let clock = MediaManualClock(box)
        let backend = MediaFakeBackend()
        let driver = MediaFakeDriver()
        let capture = MediaFakeCapture()
        let coordinator = NativeMediaCoordinator(clock: clock,
            integration: NativeMediaIntegration(backend: backend, driver: driver, capture: capture),
            gateNowMs: { box.now() })
        return (coordinator, backend, driver, capture, clock, box)
    }

    func testOwnerPresenceOpensOnlyExactGenerationAndStopClosesSynchronously() async {
        let (coordinator, backend, driver, capture, clock, _) = system()
        backend.set(ownerConnected: false)
        coordinator.start(localSessionID: "local-A")
        await waitUntil { driver.connectCount == 1 }
        clock.advance(0)
        await waitUntil(clock: clock) { backend.snapshotCount >= 3 }
        XCTAssertFalse(driver.gate?.isOpen(attempt: driver.attempt!) ?? true)
        backend.set(ownerConnected: true)
        clock.advance(1_000)
        await waitUntil(clock: clock) { coordinator.state == .privateVideoAdmitted }
        XCTAssertEqual(capture.localID, "local-A")
        XCTAssertTrue(driver.gate?.isOpen(attempt: driver.attempt!) ?? false)
        coordinator.stop()
        XCTAssertFalse(driver.gate?.isOpen(attempt: driver.attempt!) ?? true)
        XCTAssertNil(capture.localID)
        await waitUntil { backend.endCount == 1 && driver.disconnectCount == 1 }
    }

    func testHelperJoinSuspendsUntilExactConnectedSIDAndLeaveIsTerminal() async {
        let (coordinator, backend, driver, _, clock, _) = system()
        backend.set(ownerConnected: true, sharing: true)
        coordinator.start(localSessionID: "local-A")
        await waitUntil { driver.connectCount == 1 }
        clock.advance(0)
        await waitUntil(clock: clock) { coordinator.state == .sharedVideoAdmitted }
        driver.join("helper:opaque-session:1", sid: "participant-A")
        XCTAssertFalse(driver.gate?.isOpen(attempt: driver.attempt!) ?? true)
        backend.set(ownerConnected: true, sharing: true,
                    helper: .reserved(connectionID: "helper:opaque-session:1", leaseVersion: 1))
        clock.advance(0)
        await waitUntil(clock: clock) { backend.snapshotCount >= 5 }
        XCTAssertFalse(driver.gate?.isOpen(attempt: driver.attempt!) ?? true)
        backend.set(ownerConnected: true, sharing: true,
                    helper: .connected(connectionID: "helper:opaque-session:1", leaseVersion: 1,
                                       participantSID: "participant-A"))
        clock.advance(1_000)
        await waitUntil(clock: clock) { coordinator.state == .sharedVideoAdmitted && backend.renewCount >= 2 }
        XCTAssertTrue(driver.gate?.isOpen(attempt: driver.attempt!) ?? false)
        driver.leave("helper:opaque-session:1", sid: "participant-A")
        XCTAssertFalse(driver.gate?.isOpen(attempt: driver.attempt!) ?? true)
        await waitUntil { backend.endCount == 1 }
    }

    func testConnectedSIDMismatchTerminatesGeneration() async {
        let (coordinator, backend, driver, _, clock, _) = system()
        backend.set(ownerConnected: true, sharing: true)
        coordinator.start(localSessionID: "local-A")
        await waitUntil { driver.connectCount == 1 }
        clock.advance(0)
        await waitUntil(clock: clock) { coordinator.state == .sharedVideoAdmitted }
        driver.join("helper:opaque-session:1", sid: "participant-A")
        backend.set(ownerConnected: true, sharing: true,
                    helper: .connected(connectionID: "helper:opaque-session:1", leaseVersion: 1,
                                       participantSID: "participant-B"))
        clock.advance(0)
        await waitUntil(clock: clock) { backend.endCount == 1 }
        XCTAssertFalse(driver.gate?.isOpen(attempt: driver.attempt!) ?? true)
    }

    func testNoncooperativeConnectRetainsSlotUntilItReturns() async {
        let (coordinator, backend, driver, capture, _, _) = system()
        backend.set(ownerConnected: true)
        driver.holdConnect = true
        coordinator.start(localSessionID: "local-A")
        await waitUntil { driver.connectContinuation != nil }
        coordinator.stop()
        coordinator.start(localSessionID: "local-B")
        XCTAssertEqual(driver.connectCount, 1)
        XCTAssertNil(capture.localID)
        XCTAssertFalse(driver.gate?.isOpen(attempt: driver.attempt!) ?? true)
        driver.holdConnect = false
        driver.connectContinuation?.resume()
        driver.connectContinuation = nil
        await waitUntil { driver.disconnectCount == 1 }
        await waitUntil { driver.connectCount == 2 }
    }

    func testDelayedSnapshotAndRenewalCannotAdmitPastRequestSendDeadline() async {
        do {
            let (coordinator, backend, driver, _, clock, _) = system()
            backend.set(ownerConnected: true)
            backend.delayedSnapshotCall = 4
            coordinator.start(localSessionID: "local-A")
            await waitUntil { driver.connectCount == 1 }
            await waitUntil(clock: clock) { backend.heldSnapshot != nil }
            clock.advance(15_001)
            backend.heldSnapshot?.resume()
            backend.heldSnapshot = nil
            await waitUntil(clock: clock) { backend.endCount == 1 }
            XCTAssertFalse(driver.gate?.isOpen(attempt: driver.attempt!) ?? true)
        }
        do {
            let (coordinator, backend, driver, _, clock, _) = system()
            backend.set(ownerConnected: true, sharing: true)
            backend.delayRenew = true
            coordinator.start(localSessionID: "local-B")
            await waitUntil { driver.connectCount == 1 }
            await waitUntil(clock: clock) { backend.heldRenew != nil }
            clock.advance(15_001)
            backend.delayRenew = false
            backend.heldRenew?.resume()
            backend.heldRenew = nil
            await waitUntil { clock.hasScheduled(afterMs: 1_000) }
            XCTAssertFalse(driver.gate?.isOpen(attempt: driver.attempt!) ?? true)
            clock.advance(5_000)
            await waitUntil(clock: clock) { backend.endCount == 1 }
            XCTAssertFalse(driver.gate?.isOpen(attempt: driver.attempt!) ?? true)
        }
    }

    func testRetiredOrReplacementGenerationTerminates() async {
        for replacement in [false, true] {
            let (coordinator, backend, driver, _, clock, _) = system()
            backend.set(ownerConnected: true)
            coordinator.start(localSessionID: "local-A")
            await waitUntil { driver.connectCount == 1 }
            await waitUntil(clock: clock) { coordinator.state == .privateVideoAdmitted }
            if replacement {
                backend.snapshotValue = NativeMediaSnapshot(sessionID: "opaque-session", epoch: UUID(), status: .active,
                                                            authorizationVersion: 2, sharing: false, cleanupPending: false,
                                                            media: .ready(roomName: "replacement-room", generation: 2,
                                                                          roomSID: "replacement-sid", ownerConnected: true),
                                                            helper: .empty, validForMs: 15_000, nonce: "unused")
            } else { backend.retire() }
            clock.advance(1_000)
            await waitUntil(clock: clock) { backend.endCount == 1 }
            XCTAssertFalse(driver.gate?.isOpen(attempt: driver.attempt!) ?? true)
            XCTAssertEqual(coordinator.state, .closed)
        }
    }

    func testStaleSDKCallbackCannotCloseReplacementAttempt() async {
        let (coordinator, backend, driver, _, clock, _) = system()
        backend.set(ownerConnected: true)
        coordinator.start(localSessionID: "local-A")
        await waitUntil { driver.connectCount == 1 }
        let old = driver.attempt!
        coordinator.start(localSessionID: "local-B")
        await waitUntil(clock: clock) { driver.connectCount == 2 }
        let replacement = driver.attempt!
        XCTAssertNotEqual(old, replacement)
        driver.onInvalidated?(old)
        await waitUntil(clock: clock) { coordinator.state == .privateVideoAdmitted }
        XCTAssertTrue(driver.gate?.isOpen(attempt: replacement) ?? false)
        coordinator.stop()
    }

    func testLogoutCancelsQueuedStartBehindNoncooperativeConnect() async {
        let (coordinator, backend, driver, _, _, _) = system()
        backend.set(ownerConnected: true)
        driver.holdConnect = true
        coordinator.start(localSessionID: "local-A")
        await waitUntil { driver.connectContinuation != nil }
        coordinator.start(localSessionID: "local-B")
        backend.onIdentityInvalidated?()
        driver.holdConnect = false
        driver.connectContinuation?.resume()
        driver.connectContinuation = nil
        await waitUntil { backend.endCount == 1 }
        XCTAssertEqual(driver.connectCount, 1)
        XCTAssertEqual(coordinator.state, .closed)
    }

    func testTokenWindowVersionAndNonceMismatchesFailClosed() async {
        do {
            let (coordinator, backend, driver, _, clock, _) = system()
            backend.set(ownerConnected: true)
            backend.versionAtSnapshotCall[3] = 2
            coordinator.start(localSessionID: "local-A")
            await waitUntil(clock: clock) { backend.endCount == 1 }
            XCTAssertEqual(driver.connectCount, 0)
        }
        do {
            let (coordinator, backend, driver, _, clock, _) = system()
            backend.set(ownerConnected: true)
            backend.corruptNonceAtCall = 1
            coordinator.start(localSessionID: "local-B")
            await waitUntil(clock: clock) { backend.endCount == 1 }
            XCTAssertEqual(driver.connectCount, 0)
        }
    }

    func testOffMainSuspendBeforeCallbackCannotBeConsumedByInFlightRenewal() async {
        let (coordinator, backend, driver, _, clock, _) = system()
        backend.set(ownerConnected: true, sharing: true)
        backend.delayRenew = true
        coordinator.start(localSessionID: "local-A")
        await waitUntil { driver.connectCount == 1 }
        await waitUntil(clock: clock) { backend.heldRenew != nil }
        let revision = driver.gate!.suspend()!
        backend.delayRenew = false
        backend.heldRenew?.resume()
        backend.heldRenew = nil
        await waitUntil { clock.hasScheduled(afterMs: 1_000) }
        XCTAssertFalse(driver.gate?.isOpen(attempt: driver.attempt!) ?? true)
        backend.set(ownerConnected: true, sharing: true,
                    helper: .connected(connectionID: "helper:opaque-session:1", leaseVersion: 1,
                                       participantSID: "participant-A"))
        driver.onMembershipChanged?(driver.attempt!, NativeMediaMembershipChange(
            identity: "helper:opaque-session:1", participantSID: "participant-A", joined: true,
            gateRevision: revision))
        await waitUntil(clock: clock) { coordinator.state == .sharedVideoAdmitted && backend.renewCount >= 2 }
        XCTAssertTrue(driver.gate?.isOpen(attempt: driver.attempt!) ?? false)
        coordinator.stop()
    }

    func testStaleJoinCallbackCannotConsumeNewerSuspensionOrHideConflictingMember() async {
        let (coordinator, backend, driver, _, clock, _) = system()
        backend.set(ownerConnected: true, sharing: true)
        coordinator.start(localSessionID: "local-A")
        await waitUntil { driver.connectCount == 1 }
        await waitUntil(clock: clock) { coordinator.state == .sharedVideoAdmitted }
        let oldRevision = driver.gate!.suspend()!
        let newRevision = driver.gate!.suspend()!
        driver.onMembershipChanged?(driver.attempt!, NativeMediaMembershipChange(
            identity: "helper:opaque-session:1", participantSID: "participant-A", joined: true,
            gateRevision: oldRevision))
        clock.advance(0)
        XCTAssertFalse(driver.gate?.isOpen(attempt: driver.attempt!) ?? true)
        driver.onMembershipChanged?(driver.attempt!, NativeMediaMembershipChange(
            identity: "helper:opaque-session:2", participantSID: "participant-B", joined: true,
            gateRevision: newRevision))
        await waitUntil { backend.endCount == 1 }
        XCTAssertEqual(coordinator.state, .closed)
    }
}
