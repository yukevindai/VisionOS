import Foundation

@MainActor
protocol ConversationalSpeechSynthesizing: AnyObject {
    /// Nil requests local speech, including the backend's explicitly simulated response.
    func synthesize(text: String, requestID: String) async throws -> Data?
}

@MainActor
final class ConvexSpeechClient: ConversationalSpeechSynthesizing {
    private let backend: ConvexNativeMediaBackend

    static func live() throws -> ConvexSpeechClient {
        let configuration = try LiveServiceConfiguration.load()
        let identity = StoredSpeechIdentity(configuration: configuration)
        return ConvexSpeechClient(backend: try ConvexNativeMediaBackend(
            origin: configuration.convexURL, identity: identity))
    }

    init(backend: ConvexNativeMediaBackend) { self.backend = backend }

    func synthesize(text: String, requestID: String) async throws -> Data? {
        try await backend.synthesizeSpeech(text: text, requestID: requestID)
    }
}

/// Speech uses an existing sign-in and never opens authentication UI during assistance.
/// Reload both before and after HTTP so another screen's logout/account switch invalidates
/// this request even though that screen owns a separate identity adapter.
@MainActor
private final class StoredSpeechIdentity: NativeMediaIdentityProviding {
    var onIdentityInvalidated: (() -> Void)?
    private let identity: Auth0NativeIdentity

    init(configuration: LiveServiceConfiguration) {
        identity = Auth0NativeIdentity(configuration: configuration)
        identity.onIdentityInvalidated = { [weak self] in self?.onIdentityInvalidated?() }
    }

    func currentCredentials() async throws -> NativeMediaCredentials {
        try Task.checkCancellation()
        await identity.loadStoredAccount()
        try Task.checkCancellation()
        return try await identity.currentCredentials()
    }
}
