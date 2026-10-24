import Foundation
import HapticLink

/// Inject teammate adapters here once their implementations are added to this checkout.
/// No provider URL, credential, UUID or firmware generation policy is guessed by the app.
@MainActor
struct LiveIntegrationBundle {
    let perception: any PerceptionGuidanceClient
    let speech: any SpeechSchedulerClient
    let conversation: any ConversationClient
    let evidence: any TargetEvidenceClient
    let framePolicy: FrameDeliveryPolicy
    /// Omit until H1's actual contract and synchronization implementation are available.
    /// R1/R3 can provide live voice assistance without supplying any wristband values.
    var bands: LiveBandIntegrationBundle? = nil
    /// Requires real authenticated transport and R1/local media/owner-input implementations.
    var friends: FriendAssistanceIntegration? = nil
    /// Requires authenticated identity and R1 original-frame tap. Video transport only.
    var media: NativeMediaIntegration? = nil
}

@MainActor
struct LiveBandIntegrationBundle {
    let hapticConfiguration: HapticLinkConfiguration
    let bandTransport: any BandTransport
    /// Base established by H1's actual persistent/reset handshake, never a guessed reset to zero.
    let generationSeed: () -> UInt64
    /// Install H1 handshake callbacks and forward proofs to BandService.acceptSynchronization.
    let configureBandSynchronization: (BandService) -> Void
}

@MainActor
final class AppDependencies: ObservableObject {
    @Published private(set) var session: SessionCoordinator
    @Published private(set) var simulated: Bool
    let preferences: UserPreferences
    let diagnostics: Diagnostics
    private let live: LiveIntegrationBundle?
    private let clock: any AppClock
    private let permissionOverride: (any PermissionClient)?
    private let defaults: UserDefaults
    init(simulated: Bool = ProcessInfo.processInfo.arguments.contains("--simulate"), live: LiveIntegrationBundle? = nil,
         clock: (any AppClock)? = nil, permissions: (any PermissionClient)? = nil, defaults: UserDefaults? = nil) {
        self.simulated = simulated; self.live = live
        let clock = clock ?? SystemAppClock()
        self.clock = clock; self.permissionOverride = permissions
        let preferenceDefaults: UserDefaults
        if let defaults { preferenceDefaults = defaults }
        else if ProcessInfo.processInfo.arguments.contains("--uitesting") {
            preferenceDefaults = UserDefaults(suiteName: "Reflex.UITests")!
            preferenceDefaults.removePersistentDomain(forName: "Reflex.UITests")
        } else { preferenceDefaults = .standard }
        self.defaults = preferenceDefaults
        preferences = UserPreferences(defaults: preferenceDefaults)
        diagnostics = Diagnostics()
        if ProcessInfo.processInfo.arguments.contains("--simulate") { preferences.setupComplete = true }
        session = Self.compose(simulated: simulated, preferences: preferences, diagnostics: diagnostics,
                               live: live, clock: clock, permissionOverride: permissions, defaults: preferenceDefaults)
    }
    func setSimulation(_ enabled: Bool) {
        guard enabled != simulated else { return }
        session.stop(.userEnd)
        session.bands.shutdown()
        simulated = enabled
        session = Self.compose(simulated: enabled, preferences: preferences, diagnostics: diagnostics,
                               live: live, clock: clock, permissionOverride: permissionOverride, defaults: defaults)
    }
    private static func compose(simulated: Bool, preferences: UserPreferences, diagnostics: Diagnostics,
                                live: LiveIntegrationBundle?, clock: any AppClock,
                                permissionOverride: (any PermissionClient)?, defaults: UserDefaults) -> SessionCoordinator {
        let clock = compositionClock(simulated: simulated, fallback: clock)
        let live = live ?? (simulated ? nil : nativeIntegration(preferences: preferences, clock: clock))
        let liveBands = simulated ? nil : live?.bands
        let controller: HapticLinkController?
        do {
            if let liveBands {
                controller = try HapticLinkController(configuration: liveBands.hapticConfiguration,
                    transport: liveBands.bandTransport, metadataStore: UserDefaultsPairingMetadataStore(defaults: defaults))
            } else { controller = nil }
        } catch {
            controller = nil
            diagnostics.record("haptic_configuration_failed", context: nil, now: clock.nowMs, code: "INVALID_CONFIGURATION")
        }
        let bands = BandService(controller: controller, simulated: simulated, clock: clock)
        if controller != nil { liveBands?.configureBandSynchronization(bands) }
        var permissions: any PermissionClient = permissionOverride ?? (simulated ? FixturePermissionClient() : SystemPermissionClient())
        var enforceSimulatedPermissions = false
        let fixtureEvidence = simulated ? FixtureEvidence(clock: clock) : nil
        #if DEBUG
        let arguments = ProcessInfo.processInfo.arguments
        if simulated, arguments.contains("--uitesting"), arguments.contains("--simulate"),
           let option = arguments.firstIndex(of: "--ui-task-scenario"),
           arguments.indices.contains(option + 1) {
            fixtureEvidence?.uiTestScenario = FixtureEvidence.UITestScenario(rawValue: arguments[option + 1])
        }
        if simulated, arguments.contains("--uitesting"), arguments.contains("--simulate"),
           let option = arguments.firstIndex(of: "--ui-permission-scenario"),
           arguments.indices.contains(option + 1),
           let scenario = UITestPermissionClient.Scenario(rawValue: arguments[option + 1]) {
            permissions = UITestPermissionClient(scenario: scenario)
            enforceSimulatedPermissions = true
        }
        #endif
        return SessionCoordinator(simulated: simulated, preferences: preferences, diagnostics: diagnostics,
            bands: bands, clock: clock,
            perception: simulated ? FixturePerception(clock: clock) : live?.perception ?? UnavailablePerception(),
            speech: simulated ? FixtureSpeech(audible: !ProcessInfo.processInfo.arguments.contains("--uitesting")) : live?.speech ?? UnavailableSpeech(),
            conversation: simulated ? FixtureConversation() : live?.conversation ?? UnavailableConversation(),
            evidence: fixtureEvidence ?? live?.evidence ?? UnavailableEvidence(),
            permissions: permissions, enforceSimulatedPermissions: enforceSimulatedPermissions,
            framePolicy: simulated ? nil : live?.framePolicy, generationSeed: liveBands?.generationSeed ?? { 0 },
            friends: simulated ? nil : live?.friends, media: simulated ? nil : live?.media)
    }

    static func compositionClock(simulated: Bool, fallback: any AppClock,
                                 arguments: [String] = ProcessInfo.processInfo.arguments) -> any AppClock {
        #if DEBUG
        if simulated, arguments.contains("--simulate"), arguments.contains("--uitesting"),
           fallback is SystemAppClock {
            return UITestAppClock()
        }
        #endif
        return fallback
    }

    private static func nativeIntegration(preferences: UserPreferences, clock: any AppClock) -> LiveIntegrationBundle {
        let audio = NativeAudioCoordinator(clock: clock, speechProvider: try? ConvexSpeechClient.live())
        let perception = NativePerceptionGuidanceClient(
            startAudio: { context in try await audio.startInput(context: context) },
            stopAudio: { _ in audio.stopInput() },
            supportsOfflineStop: { audio.offlineStopReady },
            preferredHand: { preferences.guidingHand == .left ? .left : .right },
            onCue: { text, context, expiry in audio.guidance(text, context: context, expiresAtMs: expiry) },
            stopOutput: { context in audio.cancel(context: context) })
        audio.onFailure = { [weak audio] context in
            audio?.onEvent?(.init(context: context, eventID: UUID().uuidString, payload: .disconnected))
        }
        return LiveIntegrationBundle(perception: perception, speech: audio, conversation: audio,
            evidence: GeminiObjectEvidenceClient(reader: try? ConvexObjectClient.live(), clock: clock),
            framePolicy: FrameDeliveryPolicy(minimumIntervalMs: 250, maximumEncodedBytes: 512 * 1_024))
    }
}
