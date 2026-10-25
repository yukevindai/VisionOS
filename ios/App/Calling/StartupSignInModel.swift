import Combine
import Foundation

@MainActor
protocol StartupIdentity: AnyObject {
    var status: NativeIdentityStatus { get }
    func loadStoredAccount() async
    func signIn() async throws
}

extension Auth0NativeIdentity: StartupIdentity {}

@MainActor
final class StartupSignInModel: ObservableObject {
    @Published private(set) var hasContinued = false
    @Published private(set) var busy = false
    @Published private(set) var message: String?
    let isConfigured: Bool
    private let identity: (any StartupIdentity)?
    private let ensureAccount: () async throws -> Void
    private var loaded = false

    convenience init(simulated: Bool) {
        if simulated {
            self.init(identity: nil, ensureAccount: {}, bypass: true)
            return
        }
        do {
            let configuration = try LiveServiceConfiguration.load()
            let identity = Auth0NativeIdentity(configuration: configuration)
            let backend = try ConvexNativeMediaBackend(origin: configuration.convexURL, identity: identity)
            self.init(identity: identity, ensureAccount: { try await backend.ensureAccount() })
        } catch {
            self.init(identity: nil, ensureAccount: {})
        }
    }

    init(identity: (any StartupIdentity)?, ensureAccount: @escaping () async throws -> Void,
         bypass: Bool = false) {
        self.identity = identity
        self.ensureAccount = ensureAccount
        isConfigured = identity != nil
        hasContinued = bypass
        if identity == nil && !bypass { message = "Online sign-in is unavailable in this build." }
    }

    func load() async {
        guard !loaded, !hasContinued, let identity else { return }
        loaded = true
        busy = true
        defer { busy = false }
        await identity.loadStoredAccount()
        guard !hasContinued else { return }
        if case .signedIn = identity.status {
            await finishSignIn()
        } else if case .failed = identity.status {
            message = "Your saved sign-in could not be restored. Please sign in again."
        }
    }

    func signIn() async {
        guard !busy, !hasContinued, let identity else { return }
        busy = true
        message = nil
        defer { busy = false }
        do {
            if case .signedIn = identity.status {
                // Retry account setup without asking for credentials again.
            } else {
                try await identity.signIn()
            }
            guard !hasContinued else { return }
            await finishSignIn()
        } catch {
            message = "Sign-in did not complete. Try again when you're ready."
        }
    }

    func continueWithoutSignIn() {
        hasContinued = true
    }

    private func finishSignIn() async {
        do {
            try await ensureAccount()
            hasContinued = true
        } catch {
            message = "We couldn't connect your account. Check your connection and try signing in again."
        }
    }
}
