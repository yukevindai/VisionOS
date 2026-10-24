import Foundation

/// Backend-authenticated identity of one sharing grant, not client-supplied authority.
public struct SharingAuthorizationScope: Equatable, Sendable {
    public let sessionID: String
    public let sessionEpoch: String
    public let authorizationVersion: UInt64
    public let helper: Helper?

    public struct Helper: Equatable, Sendable {
        public let connectionID: String
        public let leaseVersion: UInt64

        public init(connectionID: String, leaseVersion: UInt64) {
            self.connectionID = connectionID
            self.leaseVersion = leaseVersion
        }
    }

    public init(sessionID: String, sessionEpoch: String, authorizationVersion: UInt64, helper: Helper?) {
        self.sessionID = sessionID
        self.sessionEpoch = sessionEpoch
        self.authorizationVersion = authorizationVersion
        self.helper = helper
    }
}

public struct SharingLeaseRenewal: Equatable, Sendable {
    public let scope: SharingAuthorizationScope
    public let nonce: String
    /// Local monotonic time before transport dispatch; network queuing consumes validity.
    public let requestedAtMs: Int64
}

/// An authenticated backend response. This type does not authenticate network input.
public struct SharingLeaseGrant: Equatable, Sendable {
    public let scope: SharingAuthorizationScope
    public let nonce: String
    public let validForMs: Int64

    public init(scope: SharingAuthorizationScope, nonce: String, validForMs: Int64) {
        self.scope = scope
        self.nonce = nonce
        self.validForMs = validForMs
    }
}

public enum SharingLeaseClosure: Equatable, Sendable {
    case expired, revoked, ended, invalidClock, exhaustedRenewals
}

public enum SharingLeaseState: Equatable, Sendable {
    case awaitingAuthorization
    case authorized(deadlineMs: Int64)
    case closed(SharingLeaseClosure)
}

public enum SharingLeaseRejection: Equatable, Sendable {
    case unexpectedGrant, invalidDuration, staleRevocation
}

public enum SharingLeaseEffect: Equatable, Sendable {
    case requestRenewal(SharingLeaseRenewal)
    case cancelFriendProposals
    case pauseFriendInitiatedTask
    /// Must synchronously stop publishing owner video and shared microphone locally.
    /// The adapter must preserve supported private AI/local assistance separately.
    case unpublishSharedMedia
    /// Best-effort remote cleanup follows local unpublish, never gates it.
    case requestMediaEviction(SharingAuthorizationScope)
    case rejected(SharingLeaseRejection)
}

public enum SharingLeaseEvent: Equatable, Sendable {
    case tick
    case granted(SharingLeaseGrant)
    case renewalFailed(scope: SharingAuthorizationScope, nonce: String)
    case backendUnavailable
    case revoked(SharingAuthorizationScope)
    case end
}

public enum SharingLeaseConfigurationError: Error {
    case invalidScope, invalidRenewalInstance
}

/// Pure policy only: no camera, network, timer, or task controller is started here.
///
/// The caller serializes events and executes safety effects before network effects. It
/// must arm a local watchdog for `nextWakeupMs`; network callbacks alone cannot enforce
/// expiry. Every authenticated authority replacement needs a new instance and a fresh
/// locally minted renewalInstanceID. Close the previous instance first. Closed instances
/// never reopen, and successful renewal never resumes a physical task.
public struct SharingAuthorizationLease: Sendable {
    public static let renewalIntervalMs: Int64 = 5_000
    public static let maximumValidityMs: Int64 = 15_000
    public static let maximumSharedInteger: UInt64 = 9_007_199_254_740_991

    public let scope: SharingAuthorizationScope
    public private(set) var state: SharingLeaseState = .awaitingAuthorization
    public private(set) var pendingRenewal: SharingLeaseRenewal?
    public private(set) var backendAvailable = true
    public private(set) var nextRenewalAtMs: Int64?
    private let renewalInstanceID: String
    private var sequence: UInt64 = 0
    private var lastNowMs: Int64?

    public init(scope: SharingAuthorizationScope, renewalInstanceID: String) throws {
        guard Self.validID(scope.sessionID), Self.validID(scope.sessionEpoch),
              scope.authorizationVersion <= Self.maximumSharedInteger,
              scope.helper.map({ Self.validID($0.connectionID) && $0.leaseVersion <= Self.maximumSharedInteger }) ?? true
        else { throw SharingLeaseConfigurationError.invalidScope }
        guard Self.validID(renewalInstanceID) else { throw SharingLeaseConfigurationError.invalidRenewalInstance }
        self.scope = scope
        self.renewalInstanceID = renewalInstanceID
    }

    /// Nil before the first tick and after closure. Call tick immediately on creation.
    public var nextWakeupMs: Int64? {
        if case .closed = state { return nil }
        if case .authorized(let deadline) = state {
            return min(deadline, nextRenewalAtMs ?? deadline)
        }
        return nextRenewalAtMs
    }

    public func permitsNewSelection(nowMs: Int64) -> Bool {
        guard nowMs >= 0, nowMs >= (lastNowMs ?? 0), backendAvailable,
              scope.helper != nil, case .authorized(let deadline) = state else { return false }
        return nowMs < deadline
    }

    @discardableResult
    public mutating func reduce(_ event: SharingLeaseEvent, nowMs: Int64) -> [SharingLeaseEffect] {
        if case .closed = state { return [] }
        guard nowMs >= 0, nowMs >= (lastNowMs ?? 0) else { return close(.invalidClock) }
        lastNowMs = nowMs
        // Expiry wins even over an otherwise valid late renewal response.
        if case .authorized(let deadline) = state, nowMs >= deadline { return close(.expired) }

        switch event {
        case .tick:
            guard nowMs >= (nextRenewalAtMs ?? nowMs) else { return [] }
            guard sequence < Self.maximumSharedInteger else { return close(.exhaustedRenewals) }
            let (next, overflow) = nowMs.addingReportingOverflow(Self.renewalIntervalMs)
            guard !overflow else { return close(.invalidClock) }
            sequence += 1
            let request = SharingLeaseRenewal(scope: scope, nonce: "\(renewalInstanceID):\(sequence)", requestedAtMs: nowMs)
            // Only the latest request is actionable. Transport must also bound its work.
            pendingRenewal = request
            nextRenewalAtMs = next
            return [.requestRenewal(request)]

        case .granted(let grant):
            guard let request = pendingRenewal, grant.scope == scope, grant.nonce == request.nonce else {
                return [.rejected(.unexpectedGrant)]
            }
            guard grant.validForMs > 0, grant.validForMs <= Self.maximumValidityMs else {
                return [.rejected(.invalidDuration)]
            }
            let (deadline, overflow) = request.requestedAtMs.addingReportingOverflow(grant.validForMs)
            guard !overflow else { return close(.invalidClock) }
            guard nowMs < deadline else { return close(.expired) }
            pendingRenewal = nil
            state = .authorized(deadlineMs: deadline)
            backendAvailable = true
            return []

        case .renewalFailed(let failedScope, let nonce):
            guard failedScope == scope, pendingRenewal?.nonce == nonce else { return [] }
            pendingRenewal = nil
            return loseBackend()

        case .backendUnavailable:
            pendingRenewal = nil
            return loseBackend()

        case .revoked(let revokedScope):
            guard revokedScope == scope else { return [.rejected(.staleRevocation)] }
            return close(.revoked)

        case .end:
            return close(.ended)
        }
    }

    private mutating func loseBackend() -> [SharingLeaseEffect] {
        guard backendAvailable else { return [] }
        backendAvailable = false
        return [.cancelFriendProposals, .pauseFriendInitiatedTask]
    }

    private mutating func close(_ reason: SharingLeaseClosure) -> [SharingLeaseEffect] {
        state = .closed(reason)
        pendingRenewal = nil
        nextRenewalAtMs = nil
        backendAvailable = false
        return [.cancelFriendProposals, .pauseFriendInitiatedTask, .unpublishSharedMedia, .requestMediaEviction(scope)]
    }

    private static func validID(_ value: String) -> Bool {
        !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && value.utf8.count <= 256
    }
}
