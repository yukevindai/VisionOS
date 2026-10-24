import Foundation

struct NativeHelperSession: Identifiable, Equatable {
    let sessionID: String
    let epoch: String
    let ownerName: String
    var id: String { "\(sessionID):\(epoch)" }
}

struct NativeHelperPage: Equatable {
    let sessions: [NativeHelperSession]
    let nextCursor: String?
}

struct NativeHelperConnection: Equatable {
    let session: NativeHelperSession
    let connectionID: String
    let leaseVersion: UInt64
}

struct NativeHelperStatus: Equatable {
    enum State: String { case reserved, connected }
    let state: State
    let room: NativeMediaRoom
    let ownerIdentity: String
    let ownerSID: String
    let helperSID: String?
    let authorizationVersion: UInt64
    let validForMs: Int64

    func matches(token: NativeMediaToken, localSID: String) -> Bool {
        room == token.room && !ownerIdentity.isEmpty && !ownerSID.isEmpty &&
        !localSID.isEmpty && authorizationVersion > 0 && validForMs > 0 && validForMs <= 15_000 &&
        (state == .reserved || helperSID == localSID)
    }
}
