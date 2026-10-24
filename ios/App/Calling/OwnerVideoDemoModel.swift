import Combine
import Foundation

/// Isolated owner video experience. It does not start the normal assistance session,
/// microphone, R1 guidance, or an AI provider.
@MainActor
final class OwnerVideoDemoModel: ObservableObject {
    @Published private(set) var accountStatus: NativeIdentityStatus = .signedOut
    @Published private(set) var mediaStatus: NativeMediaCoordinatorState = .idle
    @Published private(set) var message = "Sign in to invite a trusted friend."
    @Published private(set) var setupError: String?
    @Published private(set) var invite: NativeOwnerInvite?
    @Published private(set) var requests: [NativePendingFriendRequest] = []
    @Published private(set) var nextRequestCursor: String?
    @Published private(set) var busy = false
    @Published private(set) var startInProgress = false
    @Published var sharingConsent = false

    let camera = NativeCameraFrameSource()
    private let identity: Auth0NativeIdentity?
    private let backend: ConvexNativeMediaBackend?
    private let media: NativeMediaCoordinator?
    private var subscriptions = Set<AnyCancellable>()
    private var generation: UInt64 = 0
    private var loaded = false
    private(set) var sessionRequested = false

    init() {
        do {
            let configuration = try LiveServiceConfiguration.load()
            let identity = Auth0NativeIdentity(configuration: configuration)
            let backend = try ConvexNativeMediaBackend(origin: configuration.convexURL, identity: identity)
            let media = NativeMediaCoordinator(
                clock: SystemAppClock(),
                integration: NativeMediaIntegration(backend: backend,
                                                    driver: LiveKitNativeMediaDriver(),
                                                    capture: camera)
            )
            self.identity = identity
            self.backend = backend
            self.media = media
            identity.$status.sink { [weak self] status in
                self?.accountStatus = status
                if case .signedOut = status, self?.sessionRequested == true { self?.closeVideo() }
            }.store(in: &subscriptions)
            media.$state.sink { [weak self] status in
                guard let self else { return }
                self.mediaStatus = status
                if status == .closed && self.sessionRequested {
                    self.camera.stop()
                    self.sessionRequested = false
                    self.message = "Video ended. Start again to share."
                }
            }.store(in: &subscriptions)
            camera.onStopped = { [weak self] in
                guard let self else { return }
                self.closeVideo()
                self.message = self.camera.errorMessage ?? "Camera stopped. Start again to share video."
            }
        } catch {
            self.identity = nil
            self.backend = nil
            self.media = nil
            setupError = error.localizedDescription
            message = "Service setup required"
        }
    }

    var canSignIn: Bool {
        setupError == nil && !busy && !sessionRequested &&
        accountStatus != .signingIn && accountStatus != .signingOut
    }

    var isSignedIn: Bool {
        if case .signedIn = accountStatus { return true }
        return false
    }

    var canStartVideo: Bool {
        isSignedIn && sharingConsent && !busy && !sessionRequested &&
        mediaStatus != .connecting && !camera.running
    }

    func load() async {
        guard !loaded, let identity, let backend else { return }
        loaded = true
        let at = generation
        await identity.loadStoredAccount()
        guard at == generation else { return }
        if case .signedIn = identity.status {
            do {
                try await backend.ensureAccount()
                guard at == generation else { return }
                message = "Signed in. Create an invitation or review friend requests."
                await loadFirstRequestPage()
            } catch {
                guard at == generation else { return }
                message = "Account service is unavailable. Try again."
            }
        }
    }

    func signIn() async {
        guard canSignIn, let identity, let backend else { return }
        busy = true
        let at = generation
        defer { busy = false }
        do {
            try await identity.signIn()
            guard at == generation else { return }
            try await backend.ensureAccount()
            guard at == generation else { return }
            message = "Signed in. Invite a friend before sharing video."
            await loadFirstRequestPage()
        } catch {
            guard at == generation else { return }
            message = "Sign-in or account setup did not complete. Try again."
        }
    }

    func signOut() async {
        closeVideo()
        guard let identity else { return }
        busy = true
        defer { busy = false }
        do {
            try await identity.signOut()
            invite = nil
            requests = []
            nextRequestCursor = nil
            sharingConsent = false
            message = "Signed out."
        } catch {
            message = "Local media is closed, but secure account storage could not be cleared. Try signing out again."
        }
    }

    func createInvite() async {
        guard isSignedIn, !busy, let backend else { return }
        busy = true
        let at = generation
        defer { busy = false }
        do {
            let result = try await backend.createInvite()
            guard at == generation else { return }
            invite = result
            message = "Invitation created. Share the code only with the friend you trust."
        } catch {
            guard at == generation else { return }
            message = "Could not create an invitation. Try again later."
        }
    }

    func refreshRequests() async {
        guard isSignedIn, !busy, let backend else { return }
        await loadFirstRequestPage(using: backend)
    }

    private func loadFirstRequestPage() async {
        guard isSignedIn, let backend else { return }
        await loadFirstRequestPage(using: backend)
    }

    private func loadFirstRequestPage(using backend: ConvexNativeMediaBackend) async {
        let at = generation
        do {
            let page = try await backend.pendingFriendRequests()
            guard at == generation else { return }
            requests = page.requests
            nextRequestCursor = page.continueCursor
        } catch {
            guard at == generation else { return }
            message = "Could not load friend requests. Try again."
        }
    }

    func loadMoreRequests() async {
        guard isSignedIn, !busy, let backend, let cursor = nextRequestCursor else { return }
        busy = true
        let at = generation
        defer { busy = false }
        do {
            let page = try await backend.pendingFriendRequests(cursor: cursor)
            guard at == generation else { return }
            requests += page.requests
            nextRequestCursor = page.continueCursor
        } catch {
            guard at == generation else { return }
            message = "Could not load more friend requests. Try again."
        }
    }

    func approve(_ request: NativePendingFriendRequest) async {
        guard isSignedIn, !busy, let backend, requests.contains(request) else { return }
        busy = true
        let at = generation
        defer { busy = false }
        do {
            try await backend.approveFriendRequest(request)
            guard at == generation else { return }
            requests.removeAll { $0.id == request.id }
            message = "Approved \(request.displayName). They can join while you share video."
        } catch {
            guard at == generation else { return }
            message = "Approval failed. Refresh requests and try again."
        }
    }

    func startVideo() async {
        guard canStartVideo, let backend, let media else { return }
        busy = true
        startInProgress = true
        let at = generation
        let localSessionID = UUID().uuidString
        defer { busy = false; startInProgress = false }
        do {
            try await backend.ensureAccount()
            guard at == generation else { return }
            try await camera.start(localSessionID: localSessionID)
            guard at == generation, sharingConsent, isSignedIn else {
                camera.stop()
                return
            }
            sessionRequested = true
            media.start(localSessionID: localSessionID, sharingEnabled: true)
            message = "Connecting your video…"
        } catch {
            guard at == generation else { return }
            camera.stop()
            message = camera.errorMessage ?? "Could not start the camera or video room. Try again."
        }
    }

    /// Synchronous local closure. Invoke before dismissal, sign-out or awaiting cleanup.
    func closeVideo() {
        generation &+= 1
        startInProgress = false
        sessionRequested = false
        media?.stop()
        camera.stop()
        sharingConsent = false
    }
}
