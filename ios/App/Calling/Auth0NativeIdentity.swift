import Auth0
import Combine
import Foundation

enum NativeIdentityError: Error, Equatable, LocalizedError {
    case unauthenticated, busy, changed, invalidToken, secureStorageUnavailable

    var errorDescription: String? {
        switch self {
        case .unauthenticated: return "Sign in to use online services."
        case .busy: return "An account change is already in progress."
        case .changed: return "The signed-in account changed. Try again."
        case .invalidToken: return "Auth0 returned an invalid identity token. Sign in again."
        case .secureStorageUnavailable: return "The secure account store is unavailable."
        }
    }
}

enum NativeIdentityStatus: Equatable {
    case signedOut
    case loading
    case signingIn
    case signingOut
    case signedIn(accountID: String)
    case failed(String)
}

/// One instance per app process. Auth0 Web Auth handles authorization code + PKCE;
/// CredentialsManager stores/refreshes credentials in the iOS Keychain.
@MainActor
final class Auth0NativeIdentity: ObservableObject, NativeMediaIdentityProviding {
    @Published private(set) var status: NativeIdentityStatus = .signedOut
    var onIdentityInvalidated: (() -> Void)?

    private let configuration: LiveServiceConfiguration
    private let manager: CredentialsManager
    private var accountID: String?
    private var identityEpoch = UUID()
    private var attemptGeneration: UInt64 = 0
    private var credentialTask: Task<Credentials, Error>?

    init(configuration: LiveServiceConfiguration) {
        self.configuration = configuration
        let authentication = Auth0.authentication(
            clientId: configuration.auth0NativeClientID,
            domain: configuration.auth0Domain
        )
        manager = CredentialsManager(
            authentication: authentication,
            storeKey: "reflex-native-\(configuration.auth0NativeClientID)"
        )
    }

    /// Call on app launch before exposing live calling. An expired stored token is refreshed
    /// by CredentialsManager; a refresh failure is visible and never grants media access.
    func loadStoredAccount() async {
        guard status != .signingIn, status != .signingOut else { return }
        guard let storedSubject = manager.user?.sub else {
            if accountID != nil { invalidateIdentity() }
            status = .signedOut
            return
        }
        status = .loading
        let generation = attemptGeneration
        do {
            let credentials = try await retrieveCredentials()
            guard generation == attemptGeneration else { return }
            let subject = try subject(of: credentials.idToken)
            guard subject == storedSubject else {
                invalidateIdentity()
                _ = manager.clear()
                status = .failed("Stored account changed during refresh. Please sign in again.")
                return
            }
            adopt(subject: subject)
        } catch {
            guard generation == attemptGeneration else { return }
            if accountID != nil { invalidateIdentity() }
            status = .failed("Stored sign-in could not be restored. Please sign in again.")
        }
    }

    /// Explicit invocation is required for an account switch. Existing media authority is
    /// removed synchronously at the beginning, before browser UI or Keychain work suspends.
    func signIn() async throws {
        guard status != .signingIn, status != .signingOut else { throw NativeIdentityError.busy }
        if accountID != nil || manager.user != nil {
            try await signOut()
        }
        attemptGeneration &+= 1
        let generation = attemptGeneration
        status = .signingIn
        do {
            let credentials = try await Auth0.webAuth(
                clientId: configuration.auth0NativeClientID,
                domain: configuration.auth0Domain
            )
            .redirectURL(configuration.auth0CallbackURL)
            .issuer(configuration.auth0Issuer.absoluteString)
            .scope("openid profile email offline_access")
            .useEphemeralSession()
            .start()

            guard generation == attemptGeneration, status == .signingIn else {
                throw NativeIdentityError.changed
            }
            let subject = try subject(of: credentials.idToken)
            guard manager.store(credentials: credentials) else {
                throw NativeIdentityError.secureStorageUnavailable
            }
            adopt(subject: subject)
        } catch {
            guard generation == attemptGeneration else { throw NativeIdentityError.changed }
            status = .failed("Sign-in did not complete. Please try again.")
            throw error
        }
    }

    /// Local authority ends before the first suspension. The ephemeral Web Auth session has
    /// no shared browser cookie to revoke; Keychain credentials are cleared after in-flight
    /// refresh work finishes, so a late refresh cannot restore a logged-out account.
    func signOut() async throws {
        guard status != .signingOut else { throw NativeIdentityError.busy }
        let hadStoredAccount = accountID != nil || manager.user != nil
        attemptGeneration &+= 1
        invalidateIdentity()
        status = .signingOut
        if let credentialTask { _ = await credentialTask.result }
        self.credentialTask = nil
        let cleared = manager.clear()
        if hadStoredAccount && !cleared {
            status = .failed("Secure account storage could not be cleared.")
            throw NativeIdentityError.secureStorageUnavailable
        }
        status = .signedOut
    }

    func currentCredentials() async throws -> NativeMediaCredentials {
        guard let expectedAccount = accountID, case .signedIn = status else {
            throw NativeIdentityError.unauthenticated
        }
        let generation = attemptGeneration
        let epoch = identityEpoch
        let credentials = try await retrieveCredentials()
        guard generation == attemptGeneration, accountID == expectedAccount,
              identityEpoch == epoch else { throw NativeIdentityError.changed }
        let actualAccount: String
        do { actualAccount = try subject(of: credentials.idToken) }
        catch {
            invalidateIdentity()
            status = .failed("The identity token is invalid. Please sign in again.")
            _ = manager.clear()
            throw error
        }
        guard actualAccount == expectedAccount else {
            invalidateIdentity()
            status = .failed("The signed-in account changed. Please sign in again.")
            _ = manager.clear()
            throw NativeIdentityError.changed
        }
        return NativeMediaCredentials(jwt: credentials.idToken,
                                      accountID: actualAccount,
                                      identityEpoch: epoch)
    }

    private func retrieveCredentials() async throws -> Credentials {
        if let credentialTask { return try await credentialTask.value }
        let task = Task {
            let credentials = try await manager.credentials(minTTL: 60)
            // The SDK's minTTL applies to the access token. Convex receives the ID token,
            // which can expire independently, so renew it before returning it to callers.
            if (Self.claims(of: credentials.idToken)?["exp"] as? NSNumber)?.doubleValue ?? 0
                <= Date().timeIntervalSince1970 + 60 {
                return try await manager.renew()
            }
            return credentials
        }
        credentialTask = task
        defer { credentialTask = nil }
        return try await task.value
    }

    private func adopt(subject: String) {
        if let accountID, accountID != subject { invalidateIdentity() }
        self.accountID = subject
        status = .signedIn(accountID: subject)
    }

    private func invalidateIdentity() {
        guard accountID != nil else { return }
        accountID = nil
        identityEpoch = UUID()
        onIdentityInvalidated?()
    }

    /// These local claim checks catch configuration and account-continuity errors. The
    /// Convex server verifies the token signature and claims before granting any access.
    private func subject(of idToken: String) throws -> String {
        guard let claims = Self.claims(of: idToken),
              let subject = claims["sub"] as? String, !subject.isEmpty, subject.count <= 512,
              claims["iss"] as? String == configuration.auth0Issuer.absoluteString,
              let expiry = claims["exp"] as? NSNumber,
              expiry.doubleValue > Date().timeIntervalSince1970 + 30 else {
            throw NativeIdentityError.invalidToken
        }
        let audience = claims["aud"]
        guard (audience as? String) == configuration.auth0NativeClientID ||
                (audience as? [String])?.contains(configuration.auth0NativeClientID) == true else {
            throw NativeIdentityError.invalidToken
        }
        return subject
    }

    private static func claims(of idToken: String) -> [String: Any]? {
        let segments = idToken.split(separator: ".", omittingEmptySubsequences: false)
        guard segments.count == 3 else { return nil }
        var payload = String(segments[1]).replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        payload += String(repeating: "=", count: (4 - payload.count % 4) % 4)
        guard let data = Data(base64Encoded: payload),
              let claims = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return nil
        }
        return claims
    }
}
