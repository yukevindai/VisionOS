import Combine
import Foundation
import LiveKit

@MainActor
final class FriendSessionsModel: ObservableObject {
    @Published private(set) var accountStatus: NativeIdentityStatus = .signedOut
    @Published private(set) var sessions: [NativeHelperSession] = []
    @Published private(set) var nextCursor: String?
    @Published private(set) var setupRequired = false
    @Published private(set) var busy = false
    @Published private(set) var message = "Sign in to see sessions shared with you."
    @Published private(set) var selectedSession: NativeHelperSession?
    @Published private(set) var track: RemoteVideoTrack?
    @Published private(set) var videoFresh = false
    @Published private(set) var connectionStatus = "Connecting"

    private let identity: Auth0NativeIdentity?
    private let backend: ConvexNativeMediaBackend?
    private var subscriptions = Set<AnyCancellable>()
    private var revision: UInt64 = 0
    private var signInAttempt: UUID?
    private var loaded = false
    private var room: Room?
    private var observer: HelperRoomObserver?
    private var connection: NativeHelperConnection?
    private var token: NativeMediaToken?
    private var localSID: String?
    private var status: NativeHelperStatus?
    private var authorityDeadline: TimeInterval = 0
    private var pollTask: Task<Void, Never>?
    private var watchdogTask: Task<Void, Never>?
    private var frameClock = HelperFrameClock()

    init() {
        do {
            let configuration = try LiveServiceConfiguration.load()
            let identity = Auth0NativeIdentity(configuration: configuration)
            let backend = try ConvexNativeMediaBackend(origin: configuration.convexURL, identity: identity)
            self.identity = identity
            self.backend = backend
            identity.$status.sink { [weak self] in self?.accountStatus = $0 }.store(in: &subscriptions)
            backend.onIdentityInvalidated = { [weak self] in
                guard let self else { return }
                // Replacing stored credentials is part of sign-in. Close old media
                // authority without cancelling the account operation that requested it.
                self.closeMedia("Account changed. Join again after signing in.")
                self.sessions = []
                self.nextCursor = nil
                self.busy = self.signInAttempt != nil
            }
        } catch {
            identity = nil
            backend = nil
            setupRequired = true
            message = "Shared sessions need online service setup. Local assistance is still available in the Blind role."
        }
    }

    var signedIn: Bool {
        if case .signedIn = accountStatus { return true }
        return false
    }

    func load() async {
        guard !loaded, let identity, let backend else { return }
        loaded = true
        let at = revision
        await identity.loadStoredAccount()
        guard at == revision, signedIn else { return }
        do {
            try await backend.ensureAccount()
            guard at == revision else { return }
            await refresh()
        } catch {
            guard at == revision else { return }
            message = "Could not prepare your account. Try signing in again."
        }
    }

    func signIn() async {
        guard !busy, selectedSession == nil, let identity, let backend else { return }
        busy = true
        let attempt = UUID()
        signInAttempt = attempt
        defer {
            if signInAttempt == attempt {
                signInAttempt = nil
                busy = false
            }
        }
        do {
            try await identity.signIn()
            guard signInAttempt == attempt, !Task.isCancelled else { return }
            try await backend.ensureAccount()
            guard signInAttempt == attempt, !Task.isCancelled else { return }
            signInAttempt = nil
            busy = false
            await refresh()
        } catch {
            guard signInAttempt == attempt, !Task.isCancelled else { return }
            message = "Sign-in did not finish. Please try again."
        }
    }

    func signOut() async {
        close("")
        sessions = []
        nextCursor = nil
        guard let identity else { return }
        busy = true
        defer { busy = false }
        do {
            try await identity.signOut()
            message = "Signed out."
        } catch {
            message = "The call is closed. Secure sign-out did not finish; please try again."
        }
    }

    func refresh() async { await loadPage(cursor: nil) }
    func loadMore() async {
        guard let nextCursor else { return }
        await loadPage(cursor: nextCursor)
    }

    private func loadPage(cursor: String?) async {
        guard signedIn, !busy, selectedSession == nil, let backend else { return }
        busy = true
        let at = revision
        defer { if at == revision { busy = false } }
        do {
            let page = try await backend.helperSessions(cursor: cursor)
            guard at == revision else { return }
            if cursor == nil { sessions = page.sessions }
            else {
                let known = Set(sessions.map(\.id))
                sessions += page.sessions.filter { !known.contains($0.id) }
            }
            nextCursor = page.nextCursor
            message = sessions.isEmpty ? "No available sessions on this page. Friends may be offline, sharing may be off, or another helper may be connected." : "Choose a session to see your friend’s rear camera."
        } catch {
            guard at == revision else { return }
            if cursor == nil { sessions = []; nextCursor = nil }
            message = "Session availability could not be checked. Refresh to try again."
        }
    }

    func redeem(_ rawCode: String) async {
        guard signedIn, !busy, selectedSession == nil, let backend else { return }
        let code = rawCode.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !code.isEmpty else { message = "Enter the invitation code your friend shared."; return }
        busy = true
        let at = revision
        defer { if at == revision { busy = false } }
        do {
            let accepted = try await backend.redeemHelperInvite(code)
            guard at == revision else { return }
            message = accepted ? "Invitation accepted. Refresh shared sessions." : "Invitation submitted. Your friend must approve your account before you can join."
        } catch {
            guard at == revision else { return }
            message = "The invitation could not be accepted. Check the code or ask for a new one."
        }
    }

    func join(_ session: NativeHelperSession) async {
        guard signedIn, !busy, selectedSession == nil, let backend else { return }
        revision &+= 1
        let at = revision
        selectedSession = session
        connectionStatus = "Reserving a place"
        let clientID = UUID().uuidString.replacingOccurrences(of: "-", with: "")
        do {
            let connection = try await backend.reserveHelper(session, clientID: clientID)
            guard at == revision else {
                try? await backend.leaveHelper(connection)
                return
            }
            self.connection = connection
            authorityDeadline = ProcessInfo.processInfo.systemUptime + 12
            startWatchdog(revision: at)
            connectionStatus = "Authorizing video"
            let token = try await backend.helperToken(session, clientID: clientID)
            guard at == revision else { return }
            guard token.identity == connection.connectionID else { throw NativeMediaTransportError.invalidResponse }
            self.token = token
            let observer = HelperRoomObserver {
                Task { @MainActor [weak self] in
                    guard let self, self.revision == at else { return }
                    self.close("Media disconnected. Choose Join again to reconnect.")
                }
            }
            self.observer = observer
            let room = Room(delegate: observer,
                connectOptions: ConnectOptions(autoSubscribe: false, reconnectAttempts: 0, enableMicrophone: false),
                roomOptions: RoomOptions(suspendLocalVideoTracksInBackground: false))
            self.room = room
            connectionStatus = "Connecting video"
            try await room.connect(url: token.serverURL.absoluteString, token: token.token)
            let roomSID = try await room.sid().stringValue
            guard at == revision else { await room.disconnect(); return }
            guard !observer.isInvalidated, room.connectionState == .connected,
                  room.name == token.room.roomName, roomSID == token.room.roomSID,
                  room.localParticipant.identity?.stringValue == token.identity,
                  let sid = room.localParticipant.sid?.stringValue, !sid.isEmpty else {
                throw NativeMediaTransportError.invalidResponse
            }
            localSID = sid
            connectionStatus = "Verifying access"
            pollTask = Task { [weak self] in
                while !Task.isCancelled, let self, self.revision == at {
                    await self.poll(revision: at)
                    do { try await Task.sleep(for: .seconds(2)) } catch { return }
                }
            }
        } catch {
            guard at == revision else { return }
            close("This session could not be joined. It may have ended or another helper may have joined. Refresh and try again.")
        }
    }

    private func poll(revision at: UInt64) async {
        guard let backend, let connection, let token, let room, let localSID else { return }
        let sentAt = ProcessInfo.processInfo.systemUptime
        do {
            let next = try await backend.helperStatus(connection, nonce: UUID().uuidString)
            guard revision == at else { return }
            guard ProcessInfo.processInfo.systemUptime < authorityDeadline,
                  let next, next.matches(token: token, localSID: localSID),
                  observer?.isInvalidated == false, room.connectionState == .connected else {
                close("Sharing ended or your access changed.")
                return
            }
            let deadline = sentAt + Double(next.validForMs) / 1_000 - 0.75
            guard ProcessInfo.processInfo.systemUptime < deadline else {
                close("Sharing authorization expired. Join again after sharing is restored.")
                return
            }
            if let previous = status,
               previous.ownerIdentity != next.ownerIdentity || previous.ownerSID != next.ownerSID {
                close("The shared camera identity changed. Join again after sharing is restored.")
                return
            }
            authorityDeadline = deadline
            status = next
            if next.state == .connected {
                try await subscribeCamera(room: room, status: next, revision: at)
                guard revision == at else { return }
                try await backend.helperHeartbeat(connection, authorizationVersion: next.authorizationVersion)
            } else {
                clearTrack()
                connectionStatus = "Verifying access"
            }
        } catch {
            guard revision == at else { return }
            close("Access could not be verified. The video has been closed.")
        }
    }

    private func subscribeCamera(room: Room, status: NativeHelperStatus, revision at: UInt64) async throws {
        guard let owner = room.remoteParticipants.values.first(where: { $0.identity?.stringValue == status.ownerIdentity }) else {
            clearTrack()
            connectionStatus = "Waiting for the camera"
            return
        }
        guard owner.sid?.stringValue == status.ownerSID else { throw NativeMediaTransportError.invalidResponse }
        let cameras = owner.trackPublications.values.compactMap { $0 as? RemoteTrackPublication }.filter { $0.source == .camera && $0.kind == .video }
        guard cameras.count <= 1 else { throw NativeMediaTransportError.invalidResponse }
        guard let camera = cameras.first, !camera.isMuted else {
            clearTrack()
            connectionStatus = "Camera unavailable"
            return
        }
        try await camera.set(subscribed: true)
        guard revision == at, observer?.isInvalidated == false,
              ProcessInfo.processInfo.systemUptime < authorityDeadline,
              let video = camera.track as? RemoteVideoTrack else { return }
        if track !== video {
            clearTrack()
            frameClock = HelperFrameClock()
            video.add(videoRenderer: frameClock)
            track = video
            connectionStatus = "Waiting for camera frames"
        }
    }

    private func startWatchdog(revision at: UInt64) {
        watchdogTask = Task { [weak self] in
            while !Task.isCancelled, let self, self.revision == at {
                let now = ProcessInfo.processInfo.systemUptime
                if now >= self.authorityDeadline || self.observer?.isInvalidated == true {
                    self.close("Sharing authorization ended. Join again after sharing is restored.")
                    return
                }
                if self.track != nil {
                    let receivedAt = self.frameClock.lastFrameAt
                    self.videoFresh = receivedAt > 0 && now - receivedAt <= 3
                    self.connectionStatus = self.videoFresh ? "Live camera" : "Video delayed or unavailable"
                }
                do { try await Task.sleep(for: .milliseconds(250)) } catch { return }
            }
        }
    }

    private func clearTrack() {
        track?.remove(videoRenderer: frameClock)
        track = nil
        videoFresh = false
    }

    func close(_ notice: String = "You left the session.") {
        signInAttempt = nil
        closeMedia(notice)
    }

    private func closeMedia(_ notice: String) {
        revision &+= 1
        observer?.invalidateWithoutNotification()
        pollTask?.cancel(); pollTask = nil
        watchdogTask?.cancel(); watchdogTask = nil
        clearTrack()
        let oldRoom = room
        let oldConnection = connection
        room = nil; observer = nil; connection = nil; token = nil; localSID = nil; status = nil
        authorityDeadline = 0
        selectedSession = nil
        busy = false
        message = notice
        if let oldRoom { Task { await oldRoom.disconnect() } }
        if let oldConnection, let backend { Task { try? await backend.leaveHelper(oldConnection) } }
    }
}

private final class HelperFrameClock: NSObject, VideoRenderer, @unchecked Sendable {
    private let lock = NSLock()
    private var receivedAt: TimeInterval = 0
    @MainActor var isAdaptiveStreamEnabled: Bool { false }
    @MainActor var adaptiveStreamSize: CGSize { .zero }
    var lastFrameAt: TimeInterval {
        lock.lock(); defer { lock.unlock() }
        return receivedAt
    }
    nonisolated func render(frame: VideoFrame) {
        lock.lock(); receivedAt = ProcessInfo.processInfo.systemUptime; lock.unlock()
    }
}

private final class HelperRoomObserver: NSObject, RoomDelegate, @unchecked Sendable {
    private let lock = NSLock()
    private var invalidated = false
    private let onInvalidation: @Sendable () -> Void
    init(onInvalidation: @escaping @Sendable () -> Void) { self.onInvalidation = onInvalidation }
    var isInvalidated: Bool {
        lock.lock(); defer { lock.unlock() }
        return invalidated
    }
    func invalidateWithoutNotification() {
        lock.lock(); invalidated = true; lock.unlock()
    }
    private func invalidate() {
        lock.lock()
        let notify = !invalidated
        invalidated = true
        lock.unlock()
        if notify { onInvalidation() }
    }
    func room(_ room: Room, didUpdateConnectionState state: ConnectionState, from oldState: ConnectionState) {
        if state == .reconnecting || (state == .disconnected && oldState != .connecting) { invalidate() }
    }
    func room(_ room: Room, didDisconnectWithError error: LiveKitError?) { invalidate() }
    func room(_ room: Room, didStartReconnectWithMode reconnectMode: ReconnectMode) { invalidate() }
    func room(_ room: Room, didMoveToRoomNamed roomName: String) { invalidate() }
    func room(_ room: Room, participantDidDisconnect participant: RemoteParticipant) { invalidate() }
}
