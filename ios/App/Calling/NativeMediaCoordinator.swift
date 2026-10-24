import Combine
import Foundation

enum NativeMediaCoordinatorState: Equatable {
    case idle, connecting, privateVideoAdmitted, sharedVideoAdmitted, closed
}

/// Owner video admission only. It never opens a camera or microphone, and never authorizes
/// friend proposals. Local Stop/End closes the sample gate before any async backend work.
@MainActor
final class NativeMediaCoordinator: ObservableObject {
    private final class Attempt {
        let id = UUID()
        let localSessionID: String
        let sharingEnabled: Bool?
        let epoch = UUID()
        let gate: NativeMediaSampleGate
        let startedAtMs: Int64
        var binding: NativeMediaBinding?
        var room: NativeMediaRoom?
        var token: NativeMediaToken?
        var connected = false
        var captureAttached = false
        var closing = false
        var shouldEnd = false
        var shouldDisableSharing = false
        var member: NativeMediaMembershipChange?
        var membershipRevision: UInt64 = 0
        var observedGateRevision: UInt64 = 0
        var lastHeartbeatAtMs: Int64
        var lastAuthorityDeadlineMs: Int64?

        init(localSessionID: String, sharingEnabled: Bool?, startedAtMs: Int64,
             gateNowMs: @escaping @Sendable () -> Int64) {
            self.localSessionID = localSessionID
            self.sharingEnabled = sharingEnabled
            self.startedAtMs = startedAtMs
            self.lastHeartbeatAtMs = startedAtMs
            self.gate = NativeMediaSampleGate(attempt: id, nowMs: gateNowMs)
        }
    }

    private let clock: any AppClock
    private let integration: NativeMediaIntegration
    private let gateNowMs: @Sendable () -> Int64
    private var current: Attempt?
    private var pendingStart: (localSessionID: String, sharingEnabled: Bool?)?
    private var operation: Task<Void, Never>?
    private var tick: ScheduledWork?
    private var watchdog: ScheduledWork?
    private var tickPending = false
    @Published private(set) var state: NativeMediaCoordinatorState = .idle
    private(set) var binding: NativeMediaBinding?
    private(set) var activeRoom: NativeMediaRoom?

    init(clock: any AppClock, integration: NativeMediaIntegration,
         gateNowMs: @escaping @Sendable () -> Int64 = {
             Int64(ProcessInfo.processInfo.systemUptime * 1_000)
         }) {
        self.clock = clock
        self.integration = integration
        self.gateNowMs = gateNowMs
        integration.backend.onIdentityInvalidated = { [weak self] in self?.stop() }
        integration.driver.onInvalidated = { [weak self] attempt in
            guard let self, self.current?.id == attempt else { return }
            self.close(end: true, disableSharing: false)
        }
        integration.driver.onMembershipChanged = { [weak self] attempt, change in
            self?.membershipChanged(attempt: attempt, change: change)
        }
    }

    /// Queues one requested local session. An old noncooperative operation keeps the slot
    /// occupied until it returns and its driver/backend cleanup completes.
    func start(localSessionID: String, sharingEnabled: Bool? = nil) {
        guard !localSessionID.isEmpty else { return }
        pendingStart = (localSessionID, sharingEnabled)
        if current != nil { close(end: true, disableSharing: false) }
        drain()
    }

    func stop() {
        pendingStart = nil
        close(end: true, disableSharing: false)
    }

    /// Close video immediately, then revoke sharing at Convex. A fresh Start is required
    /// for this slice to provision a new private generation after room retirement.
    func stopSharing() {
        pendingStart = nil
        close(end: false, disableSharing: true)
    }

    private func close(end: Bool, disableSharing: Bool) {
        guard let current else {
            if pendingStart == nil { state = .closed }
            return
        }
        let wasClosing = current.closing
        current.shouldEnd = current.shouldEnd || end
        current.shouldDisableSharing = current.shouldDisableSharing || disableSharing
        if !current.closing {
            current.closing = true
            current.gate.close()
            integration.capture.detach()
            current.captureAttached = false
            tick?.cancel(); tick = nil
            watchdog?.cancel(); watchdog = nil
            state = .closed
            activeRoom = nil
            binding = nil
        }
        if !wasClosing { operation?.cancel() }
        if operation == nil { beginCleanup(current) }
    }

    private func drain() {
        guard current == nil, operation == nil, let request = pendingStart else { return }
        pendingStart = nil
        let attempt = Attempt(localSessionID: request.localSessionID,
                              sharingEnabled: request.sharingEnabled,
                              startedAtMs: clock.nowMs, gateNowMs: gateNowMs)
        current = attempt
        state = .connecting
        armWatchdog(attempt)
        operation = Task { [weak self] in
            guard let self else { return }
            await self.bootstrap(attempt)
            self.finishOperation(attempt)
        }
    }

    private func bootstrap(_ attempt: Attempt) async {
        do {
            try checkCurrent(attempt)
            try await integration.backend.ensureAccount()
            try checkCurrent(attempt)
            let binding = try await integration.backend.start(epoch: attempt.epoch)
            attempt.binding = binding
            self.binding = binding
            try checkCurrent(attempt)
            if let sharingEnabled = attempt.sharingEnabled {
                try await integration.backend.setSharing(binding, enabled: sharingEnabled)
                try checkCurrent(attempt)
            }
            let firstSend = clock.nowMs
            let firstNonce = UUID().uuidString
            var snapshot = try await integration.backend.snapshot(binding, nonce: firstNonce)
            try checkCurrent(attempt)
            guard isFresh(snapshot, binding: binding, sentAtMs: firstSend, nonce: firstNonce) else { throw Failure.stale }
            if case .unready = snapshot.media {
                guard !snapshot.cleanupPending else { throw Failure.stale }
                attempt.room = try await integration.backend.provision(binding)
                try checkCurrent(attempt)
            }
            let beforeTokenSend = clock.nowMs
            let beforeTokenNonce = UUID().uuidString
            snapshot = try await integration.backend.snapshot(binding, nonce: beforeTokenNonce)
            try checkCurrent(attempt)
            guard isFresh(snapshot, binding: binding, sentAtMs: beforeTokenSend, nonce: beforeTokenNonce),
                  !snapshot.cleanupPending,
                  case .ready(let name, let generation, let sid, _) = snapshot.media else {
                throw Failure.stale
            }
            let room = NativeMediaRoom(roomName: name, roomSID: sid, generation: generation)
            let tokenAuthorizationVersion = snapshot.authorizationVersion
            if let provisioned = attempt.room, provisioned != room { throw Failure.stale }
            attempt.room = room
            let token = try await integration.backend.ownerToken(binding, clientInstanceID: attempt.id)
            try checkCurrent(attempt)
            guard token.room == room else { throw Failure.stale }
            attempt.token = token
            let beforeConnectSend = clock.nowMs
            let beforeConnectNonce = UUID().uuidString
            let beforeConnect = try await integration.backend.snapshot(binding, nonce: beforeConnectNonce)
            try checkCurrent(attempt)
            guard isFresh(beforeConnect, binding: binding, sentAtMs: beforeConnectSend, nonce: beforeConnectNonce),
                  beforeConnect.authorizationVersion == tokenAuthorizationVersion,
                  matches(room: room, snapshot: beforeConnect), !beforeConnect.cleanupPending else {
                throw Failure.stale
            }
            try await integration.driver.connect(token: token, attempt: attempt.id, gate: attempt.gate)
            attempt.connected = true
            try checkCurrent(attempt)
            // The SDK validates its actual room name/SID/owner identity. The gate remains closed
            // until a later backend snapshot proves the owner connected in this generation.
            requestTick(afterMs: 0)
        } catch {
            close(end: true, disableSharing: false)
        }
    }

    private func checkCurrent(_ attempt: Attempt) throws {
        guard current === attempt, !attempt.closing,
              clock.nowMs >= attempt.startedAtMs,
              clock.nowMs < (attempt.lastAuthorityDeadlineMs ?? attempt.startedAtMs + 20_000) else {
            throw Failure.stale
        }
    }

    private func isFresh(_ snapshot: NativeMediaSnapshot, binding: NativeMediaBinding,
                         sentAtMs: Int64, nonce: String) -> Bool {
        snapshot.sessionID == binding.sessionID && snapshot.epoch == binding.epoch &&
        snapshot.nonce == nonce && snapshot.status == .active &&
        snapshot.validForMs > 0 && snapshot.validForMs <= 15_000 &&
        sentAtMs <= clock.nowMs && clock.nowMs - sentAtMs < snapshot.validForMs
    }

    private func matches(room: NativeMediaRoom, snapshot: NativeMediaSnapshot) -> Bool {
        guard case .ready(let name, let generation, let sid, _) = snapshot.media else { return false }
        return room.roomName == name && room.roomSID == sid && room.generation == generation
    }

    private func requestTick(afterMs: Int64) {
        guard let current, !current.closing else { return }
        if operation != nil { tickPending = true; return }
        tick?.cancel()
        tick = clock.schedule(afterMs: max(0, afterMs)) { [weak self, weak current] in
            guard let self, let current, self.current === current, !current.closing else { return }
            self.poll(current)
        }
    }

    private func poll(_ attempt: Attempt) {
        guard operation == nil, current === attempt, !attempt.closing else { return }
        tick?.cancel(); tick = nil
        operation = Task { [weak self] in
            guard let self else { return }
            await self.pollAsync(attempt)
            self.finishOperation(attempt)
        }
    }

    private func pollAsync(_ attempt: Attempt) async {
        do {
            try checkCurrent(attempt)
            guard let binding = attempt.binding, let room = attempt.room, attempt.connected else { throw Failure.stale }
            let gateRevision = attempt.observedGateRevision
            let membershipRevision = attempt.membershipRevision
            guard attempt.gate.revision == gateRevision else {
                return
            }
            let snapshotSend = clock.nowMs
            let snapshotNonce = UUID().uuidString
            let snapshot = try await integration.backend.snapshot(binding, nonce: snapshotNonce)
            try checkCurrent(attempt)
            guard attempt.membershipRevision == membershipRevision,
                  attempt.gate.revision == gateRevision else { return }
            guard isFresh(snapshot, binding: binding, sentAtMs: snapshotSend, nonce: snapshotNonce),
                  matches(room: room, snapshot: snapshot), !snapshot.cleanupPending,
                  case .ready(_, _, _, let ownerConnected) = snapshot.media else { throw Failure.stale }
            if !ownerConnected {
                suspendLocally(attempt, expectedRevision: gateRevision)
                if attempt.lastAuthorityDeadlineMs != nil { throw Failure.stale }
                return
            }
            if clock.nowMs - attempt.lastHeartbeatAtMs >= 5_000 {
                try await integration.backend.heartbeat(binding)
                try checkCurrent(attempt)
                guard attempt.membershipRevision == membershipRevision,
                      attempt.gate.revision == gateRevision else { return }
                attempt.lastHeartbeatAtMs = clock.nowMs
            }
            var grantMs = snapshot.validForMs
            if snapshot.sharing {
                let helper: NativeMediaHelperScope?
                switch snapshot.helper {
                case .empty, .reserved:
                    guard attempt.member == nil else { suspendLocally(attempt, expectedRevision: gateRevision); return }
                    helper = nil
                case .connected(let connectionID, let leaseVersion, let participantSID):
                    guard let member = attempt.member else { suspendLocally(attempt, expectedRevision: gateRevision); return }
                    guard member.identity == connectionID, member.participantSID == participantSID,
                          member.joined else { throw Failure.stale }
                    helper = NativeMediaHelperScope(connectionID: connectionID, leaseVersion: leaseVersion)
                }
                let renewalSend = clock.nowMs
                let renewalNonce = UUID().uuidString
                let renewal = try await integration.backend.renew(binding, authorizationVersion: snapshot.authorizationVersion,
                                                                   helper: helper, nonce: renewalNonce)
                try checkCurrent(attempt)
                guard membershipRevision == attempt.membershipRevision,
                      attempt.gate.revision == gateRevision,
                      renewal.binding == binding, renewal.nonce == renewalNonce,
                      renewal.authorizationVersion == snapshot.authorizationVersion,
                      renewal.helper == helper, renewal.validForMs > 0, renewal.validForMs <= 15_000,
                      renewalSend <= clock.nowMs,
                      clock.nowMs - renewalSend < renewal.validForMs else {
                    suspendLocally(attempt, expectedRevision: gateRevision); return
                }
                let verifySend = clock.nowMs
                let verifyNonce = UUID().uuidString
                let verified = try await integration.backend.snapshot(binding, nonce: verifyNonce)
                try checkCurrent(attempt)
                guard membershipRevision == attempt.membershipRevision,
                      attempt.gate.revision == gateRevision,
                      isFresh(verified, binding: binding, sentAtMs: verifySend, nonce: verifyNonce),
                      verified.authorizationVersion == snapshot.authorizationVersion,
                      verified.sharing, verified.helper == snapshot.helper,
                      matches(room: room, snapshot: verified), !verified.cleanupPending,
                      case .ready(_, _, _, true) = verified.media else {
                    suspendLocally(attempt, expectedRevision: gateRevision); return
                }
                grantMs = min(grantMs - (clock.nowMs - snapshotSend),
                              renewal.validForMs - (clock.nowMs - renewalSend),
                              verified.validForMs - (clock.nowMs - verifySend))
            } else {
                guard attempt.member == nil else { throw Failure.stale }
                grantMs -= clock.nowMs - snapshotSend
            }
            guard grantMs > 0, grantMs <= 15_000,
                  clock.nowMs <= Int64.max - grantMs,
                  attempt.gate.authorize(untilMs: clock.nowMs + grantMs,
                                         expectedRevision: gateRevision) else {
                throw Failure.stale
            }
            attempt.observedGateRevision = gateRevision
            attempt.lastAuthorityDeadlineMs = clock.nowMs + grantMs
            if !attempt.captureAttached {
                integration.capture.attach(localSessionID: attempt.localSessionID) { [weak self, weak attempt] frame in
                    guard let self, let attempt, self.current === attempt,
                          !attempt.closing, frame.localSessionID == attempt.localSessionID,
                          attempt.gate.isOpen(attempt: attempt.id) else { return }
                    self.integration.driver.sendVideo(frame.pixelBuffer, capturedAtMs: frame.capturedAtMs,
                                                      rotation: frame.rotation, attempt: attempt.id)
                }
                attempt.captureAttached = true
            }
            state = snapshot.sharing ? .sharedVideoAdmitted : .privateVideoAdmitted
            activeRoom = room
            armWatchdog(attempt)
        } catch {
            close(end: true, disableSharing: false)
        }
    }

    private func membershipChanged(attempt id: UUID, change: NativeMediaMembershipChange) {
        guard let attempt = current, attempt.id == id, !attempt.closing else { return }
        attempt.membershipRevision &+= 1
        guard change.joined else { close(end: true, disableSharing: false); return }
        if let prior = attempt.member,
           prior.identity != change.identity || prior.participantSID != change.participantSID {
            close(end: true, disableSharing: false)
            return
        }
        attempt.member = change
        // A delayed older callback still records identity evidence, but cannot consume
        // a newer off-main suspension revision to reopen video.
        if change.gateRevision == attempt.gate.revision {
            attempt.observedGateRevision = change.gateRevision
        }
        state = .connecting
        requestTick(afterMs: 0)
    }

    private func suspendLocally(_ attempt: Attempt, expectedRevision: UInt64) {
        guard attempt.observedGateRevision == expectedRevision,
              let revision = attempt.gate.suspend(expectedRevision: expectedRevision) else { return }
        attempt.observedGateRevision = revision
    }

    private func armWatchdog(_ attempt: Attempt) {
        watchdog?.cancel(); watchdog = nil
        guard current === attempt, !attempt.closing else { return }
        let startupDeadline = attempt.startedAtMs + 20_000
        let deadline = attempt.lastAuthorityDeadlineMs ?? startupDeadline
        watchdog = clock.schedule(afterMs: max(1, deadline - clock.nowMs)) { [weak self, weak attempt] in
            guard let self, let attempt, self.current === attempt, !attempt.closing else { return }
            if self.clock.nowMs >= deadline { self.close(end: true, disableSharing: false) }
            else { self.armWatchdog(attempt) }
        }
    }

    private func finishOperation(_ attempt: Attempt) {
        operation = nil
        guard current === attempt else { drain(); return }
        if attempt.closing { beginCleanup(attempt); return }
        if tickPending { tickPending = false; requestTick(afterMs: 0) }
        else { requestTick(afterMs: 1_000) }
    }

    private func beginCleanup(_ attempt: Attempt) {
        guard operation == nil, current === attempt else { return }
        operation = Task { [weak self] in
            guard let self else { return }
            await self.integration.driver.disconnect(attempt: attempt.id)
            if let binding = attempt.binding {
                if attempt.shouldDisableSharing && !attempt.shouldEnd {
                    try? await self.integration.backend.setSharing(binding, enabled: false)
                }
                // Recheck after every await: Stop may upgrade a pending Stop Sharing.
                if attempt.shouldEnd { try? await self.integration.backend.end(binding) }
            }
            self.operation = nil
            if self.current === attempt { self.current = nil }
            self.drain()
        }
    }

    private enum Failure: Error { case stale }
}
