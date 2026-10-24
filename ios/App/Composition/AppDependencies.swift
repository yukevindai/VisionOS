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
    let hapticConfiguration: HapticLinkConfiguration
    let bandTransport: any BandTransport
    let framePolicy: FrameDeliveryPolicy
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
    init(simulated: Bool = ProcessInfo.processInfo.arguments.contains("--simulate"), live: LiveIntegrationBundle? = nil) {
        self.simulated = simulated; self.live = live
        let defaults: UserDefaults
        if ProcessInfo.processInfo.arguments.contains("--uitesting") {
            defaults = UserDefaults(suiteName: "Reflex.UITests")!
            defaults.removePersistentDomain(forName: "Reflex.UITests")
        } else { defaults = .standard }
        preferences = UserPreferences(defaults: defaults)
        diagnostics = Diagnostics()
        if ProcessInfo.processInfo.arguments.contains("--simulate") { preferences.setupComplete = true }
        session = Self.compose(simulated: simulated, preferences: preferences, diagnostics: diagnostics, live: live)
    }
    func setSimulation(_ enabled: Bool) {
        guard enabled != simulated else { return }
        session.stop(.userEnd)
        session.bands.shutdown()
        simulated = enabled
        session = Self.compose(simulated: enabled, preferences: preferences, diagnostics: diagnostics, live: live)
    }
    private static func compose(simulated: Bool, preferences: UserPreferences, diagnostics: Diagnostics,
                                live: LiveIntegrationBundle?) -> SessionCoordinator {
        let clock = SystemAppClock()
        let controller: HapticLinkController?
        do {
            controller = simulated ? nil : try HapticLinkController(
                configuration: live?.hapticConfiguration ?? .unavailableH1,
                transport: live?.bandTransport ?? UnavailableBandTransport(),
                metadataStore: UserDefaultsPairingMetadataStore())
        } catch {
            controller = nil
            diagnostics.record("haptic_configuration_failed", context: nil, now: clock.nowMs, code: "INVALID_CONFIGURATION")
        }
        let bands = BandService(controller: controller, simulated: simulated, clock: clock)
        if !simulated { live?.configureBandSynchronization(bands) }
        return SessionCoordinator(simulated: simulated, preferences: preferences, diagnostics: diagnostics,
            bands: bands, clock: clock,
            perception: simulated ? FixturePerception(clock: clock) : live?.perception ?? UnavailablePerception(),
            speech: simulated ? FixtureSpeech(audible: !ProcessInfo.processInfo.arguments.contains("--uitesting")) : live?.speech ?? UnavailableSpeech(),
            conversation: simulated ? FixtureConversation() : live?.conversation ?? UnavailableConversation(),
            evidence: simulated ? FixtureEvidence(clock: clock) : live?.evidence ?? UnavailableEvidence(),
            permissions: simulated ? FixturePermissionClient() : SystemPermissionClient(),
            framePolicy: simulated ? nil : live?.framePolicy, generationSeed: live?.generationSeed ?? { 0 })
    }
}
