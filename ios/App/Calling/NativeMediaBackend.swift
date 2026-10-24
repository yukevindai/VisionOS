import Foundation

/// A Convex document ID is opaque. The local SessionContext UUID is never sent as that ID.
struct NativeMediaBinding: Equatable {
    let sessionID: String
    let epoch: UUID
}

enum NativeMediaStatus: Equatable { case active, ended }
enum NativeMediaState: Equatable {
    case unready
    case provisioning(roomName: String, generation: UInt64)
    case ready(roomName: String, generation: UInt64, roomSID: String, ownerConnected: Bool)
    case retired(roomName: String, generation: UInt64)
}
enum NativeMediaHelper: Equatable {
    case empty
    case reserved(connectionID: String, leaseVersion: UInt64)
    case connected(connectionID: String, leaseVersion: UInt64, participantSID: String)
}

struct NativeMediaSnapshot: Equatable {
    let sessionID: String
    let epoch: UUID
    let status: NativeMediaStatus
    let authorizationVersion: UInt64
    let sharing: Bool
    let cleanupPending: Bool
    let media: NativeMediaState
    let helper: NativeMediaHelper
    /// Server-calculated remaining authority; local deadline starts at request send.
    let validForMs: Int64
    let nonce: String
}

struct NativeMediaRoom: Equatable {
    let roomName: String
    let roomSID: String
    let generation: UInt64
}

struct NativeMediaToken: Equatable {
    let token: String
    let serverURL: URL
    let room: NativeMediaRoom
    let identity: String
    /// Server Unix milliseconds. A caller must also recheck current generation before using it.
    let expiresAtMs: Int64
}

struct NativeMediaHelperScope: Equatable {
    let connectionID: String
    let leaseVersion: UInt64
}

struct NativeMediaRenewal: Equatable {
    let binding: NativeMediaBinding
    let authorizationVersion: UInt64
    let helper: NativeMediaHelperScope?
    let validForMs: Int64
    let nonce: String
}

/// Supplied by the actual native Auth0 account integration. Token rotation for the same
/// account preserves identityEpoch; logout and account switch must change it.
struct NativeMediaCredentials: Equatable {
    let jwt: String
    let accountID: String
    let identityEpoch: UUID
}

@MainActor
protocol NativeMediaIdentityProviding: AnyObject {
    /// Fires synchronously on logout or account switch, before another identity can be used.
    /// A same-account token refresh does not fire it.
    var onIdentityInvalidated: (() -> Void)? { get set }
    func currentCredentials() async throws -> NativeMediaCredentials
}

@MainActor
protocol NativeMediaBackend: AnyObject {
    var onIdentityInvalidated: (() -> Void)? { get set }
    func ensureAccount() async throws
    func start(epoch: UUID) async throws -> NativeMediaBinding
    func snapshot(_ binding: NativeMediaBinding, nonce: String) async throws -> NativeMediaSnapshot
    func heartbeat(_ binding: NativeMediaBinding) async throws
    func provision(_ binding: NativeMediaBinding) async throws -> NativeMediaRoom
    func ownerToken(_ binding: NativeMediaBinding, clientInstanceID: UUID) async throws -> NativeMediaToken
    func renew(_ binding: NativeMediaBinding, authorizationVersion: UInt64,
               helper: NativeMediaHelperScope?, nonce: String) async throws -> NativeMediaRenewal
    func recover(_ binding: NativeMediaBinding) async throws
    func end(_ binding: NativeMediaBinding) async throws
    func setSharing(_ binding: NativeMediaBinding, enabled: Bool) async throws
}
