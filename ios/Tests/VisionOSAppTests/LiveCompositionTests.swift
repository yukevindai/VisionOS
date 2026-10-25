import XCTest
import Tasks
import HapticLink
@testable import VisionOS

/// Exercises production composition with synthetic adapters. No camera, microphone, Bluetooth,
/// on-device recognition or physical guidance is validated by these tests.
@MainActor
private final class CompositionPerception: PerceptionGuidanceClient {
    var onEvent: ((PerceptionEvent) -> Void)?
    var onFrame: ((FrameSnapshot) -> Void)?
    var onLocalCommand: ((SessionContext, LocalCommand) -> Void)?
    var onHapticRelease: ((ScheduledHapticRelease) -> Void)?
    let clock: ManualAppClock
    let camera = CameraCapabilities(selectedLens: "TEST rear camera", depthSupported: true,
        fallbackReason: "Synthetic test data", geometryID: "test-geometry")
    var supportsOfflineStop = true
    var supportsSpeechOnlyGuidance = true
    private(set) var context: SessionContext?
    private(set) var guidanceRequests = 0
    private(set) var stops = 0
    init(clock: ManualAppClock) { self.clock = clock }
    func start(context: SessionContext, policy: CameraPolicy) async throws -> PerceptionReadiness {
        self.context = context
        return .init(camera: camera, audioInput: .init(identifier: "TEST shared audio"),
                     supportsOfflineStop: supportsOfflineStop, supportsSpeechOnlyGuidance: supportsSpeechOnlyGuidance)
    }
    func updateContext(_ context: SessionContext) { self.context = context }
    func reacquire(_ request: RequestContext, target: ResolvedTarget) async throws -> PhysicalEvidence {
        .init(targetID: target.id, frameID: "test-frame", observedAtMs: clock.nowMs,
              targetVisible: true, handVisible: true, trackingNormal: true, depthUsable: true,
              freeSpaceObserved: true, outputReady: true, hazardPresent: false, distanceM: 0.5)
    }
    func guide(_ request: RequestContext, mode: GuidanceMode, target: ResolvedTarget, hand: GuidingHand) async throws -> TaskResponse {
        guidanceRequests += 1
        return .guidance(mode: mode, targetID: target.id, status: .onTarget,
                         evidence: try await reacquire(request, target: target))
    }
    func authorizePassage(_ request: RequestContext, target: ResolvedTarget) async throws -> PhysicalEvidence {
        try await reacquire(request, target: target)
    }
    func cancelGuidance(context: SessionContext) {}
    func stop(context: SessionContext) { stops += 1; self.context = nil }
    func emitFrame(bytes: Int) {
        guard let context else { return }
        onFrame?(.init(context: context, frameID: "test-\(clock.nowMs)-\(bytes)", capturedAtMs: clock.nowMs,
            expiresAtMs: clock.nowMs + 300, geometryID: camera.geometryID, transform: .identity,
            preview: nil, encodedImage: Data(repeating: 1, count: bytes), trackingNormal: true, depthUsable: true))
    }
}

@MainActor
private final class CompositionEvidence: TargetEvidenceClient {
    private let base: FixtureEvidence
    private(set) var resolutions = 0
    private(set) var menuReads = 0
    private(set) var resolvedQueries: [String] = []
    private(set) var resolvedKinds: [TaskKind] = []
    var resolutionError: GeminiObjectError?
    var holdResolution = false
    private var pendingResolution: CheckedContinuation<Void, Never>?
    var hasPendingResolution: Bool { pendingResolution != nil }
    init(clock: any AppClock) { base = FixtureEvidence(clock: clock) }
    func resolve(_ request: RequestContext, query: String, kind: TaskKind) async throws -> ResolvedTarget {
        resolutions += 1
        resolvedQueries.append(query)
        resolvedKinds.append(kind)
        if holdResolution {
            await withCheckedContinuation { pendingResolution = $0 }
        }
        if let resolutionError { throw resolutionError }
        return try await base.resolve(request, query: query, kind: kind)
    }
    func releaseResolution() {
        pendingResolution?.resume()
        pendingResolution = nil
    }
    func readScreen(_ request: RequestContext) async throws -> TaskScreen { try await base.readScreen(request) }
    func readMenu(_ request: RequestContext, query: String) async throws -> MenuContent {
        menuReads += 1
        return try await base.readMenu(request, query: query)
    }
    func answerMenu(_ request: RequestContext, question: String, menu: MenuContent) async throws -> String {
        try await base.answerMenu(request, question: question, menu: menu)
    }
    func verify(_ request: RequestContext, action: ActionKind, target: ResolvedTarget) async throws -> VerificationOutcome { .unavailable }
    func cancel(context: SessionContext) {}
}

@MainActor
private final class CompositionTransport: BandTransport {
    var onEvent: ((BandTransportEvent) -> Void)?
    let isSimulated = false
    private(set) var discoveries = 0
    private(set) var writes = 0
    private(set) var shutdowns = 0
    func startDiscovery() { discoveries += 1 }
    func stopDiscovery() {}
    func connect(_ peripheralID: UUID) {}
    func disconnect(_ peripheralID: UUID) {}
    func canWrite(_ peripheralID: UUID) -> Bool { false }
    func write(_ data: Data, to peripheralID: UUID) -> Bool { writes += 1; return false }
    func shutdown() { shutdowns += 1; onEvent = nil }
}

@MainActor
final class LiveCompositionTests: XCTestCase {
    @MainActor
    private struct Harness {
        let dependencies: AppDependencies
        let clock: ManualAppClock
        let perception: CompositionPerception
        let speech: RecordingSpeechScheduler
        let conversation: FixtureConversation
        let evidence: CompositionEvidence
        var session: SessionCoordinator { dependencies.session }
    }
    private func harness(offlineStop: Bool = true, speechOnly: Bool = true,
                         bands: LiveBandIntegrationBundle? = nil,
                         permissions: PermissionState = .init(camera: true, microphone: true)) -> Harness {
        let clock = ManualAppClock()
        let perception = CompositionPerception(clock: clock)
        perception.supportsOfflineStop = offlineStop
        perception.supportsSpeechOnlyGuidance = speechOnly
        let speech = RecordingSpeechScheduler(), conversation = FixtureConversation()
        let evidence = CompositionEvidence(clock: clock)
        let suite = "VisionOS.LiveCompositionTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        addTeardownBlock { defaults.removePersistentDomain(forName: suite) }
        let live = LiveIntegrationBundle(perception: perception, speech: speech, conversation: conversation,
            evidence: evidence, framePolicy: .init(minimumIntervalMs: 200, maximumEncodedBytes: 4), bands: bands)
        let dependencies = AppDependencies(simulated: false, live: live, clock: clock,
            permissions: FixturePermissionClient(state: permissions), defaults: defaults)
        return .init(dependencies: dependencies, clock: clock, perception: perception,
                     speech: speech, conversation: conversation, evidence: evidence)
    }
    private func settle() async { for _ in 0..<80 { await Task.yield() } }

    func testLiveSpeechOnlyCompositionNeedsNoH1ValuesAndCannotClaimCalibration() async {
        let h = harness()
        defer { h.session.stop(.userEnd) }
        h.dependencies.preferences.simulatedCalibrationComplete = true
        h.session.start(); await settle()
        XCTAssertEqual(h.session.phase, .active)
        XCTAssertFalse(h.session.simulated)
        XCTAssertTrue(h.session.perception === h.perception)
        XCTAssertTrue(h.session.speech === h.speech)
        XCTAssertEqual(h.session.capabilities.perception, .ready)
        XCTAssertEqual(h.session.capabilities.conversation, .ready)
        XCTAssertEqual(h.session.capabilities.offlineStop, .ready)
        XCTAssertFalse(h.session.bands.isReady)
        XCTAssertNil(h.session.bands.pairSignature)
        XCTAssertFalse(h.session.capabilities.leftBand.usable)
        XCTAssertFalse(h.session.capabilities.rightBand.usable)
        h.session.beginCalibration()
        XCTAssertNil(h.session.calibrationIndex)
        XCTAssertTrue(h.speech.calibrations.isEmpty)
        XCTAssertNil(h.dependencies.preferences.calibratedPairSignature)
    }

    func testLiveSpeechOnlyTaskAdvancesAndLocalStopInvalidatesItSynchronously() async throws {
        let h = harness()
        defer { h.session.stop(.userEnd) }
        h.session.start(); await settle()
        h.conversation.emit(.intent(.init(id: "button", kind: .button, query: "blue button")))
        await settle()
        XCTAssertEqual(h.evidence.resolutions, 1)
        XCTAssertEqual(h.perception.guidanceRequests, 1)
        XCTAssertEqual(h.session.taskState?.phase, .awaitingConfirmation)
        XCTAssertNil(h.session.taskState?.result)
        let before = try XCTUnwrap(h.session.context)
        h.perception.onLocalCommand?(before, .stop)
        XCTAssertEqual(h.session.phase, .paused)
        XCTAssertGreaterThan(try XCTUnwrap(h.session.context).generation, before.generation)
        XCTAssertNil(h.session.taskState?.confirmation)
        XCTAssertEqual(h.speech.localStops, 1)
        XCTAssertEqual(h.speech.successes, 0)
        XCTAssertNil(h.session.bands.lastStopReport, "No transport delivery may be claimed without H1")
    }

    func testOwnerCanRequestArbitraryObjectsWithoutFourNounFilter() async {
        for query in ["find my silver keys", "where is my phone", "find the red book"] {
            let h = harness()
            h.session.start(); await settle()
            XCTAssertEqual(h.session.phase, .active)
            h.session.receiveLocalOwnerUtterance(query)
            await settle()
            XCTAssertEqual(h.evidence.resolvedQueries, [query])
            XCTAssertEqual(h.evidence.resolvedKinds, [.object])
            XCTAssertEqual(h.perception.guidanceRequests, 1)
            h.session.stop(.userEnd)
        }
    }

    func testObjectRecognitionFailuresAreSpokenAndDisplayedWithoutGuidance() async {
        for error in [GeminiObjectError.notConfigured, .identificationOnly("silver keys")] {
            let h = harness()
            h.session.start(); await settle()
            h.evidence.resolutionError = error
            var spoken: [String] = []
            h.speech.onSpeech = { spoken.append(h.session.caption) }
            h.session.receiveLocalOwnerUtterance("what is this")
            await settle()
            XCTAssertEqual(h.evidence.resolvedQueries, ["what is this"])
            XCTAssertEqual(h.session.message, error.localizedDescription)
            XCTAssertEqual(h.session.caption, error.localizedDescription)
            XCTAssertTrue(spoken.contains(error.localizedDescription))
            XCTAssertEqual(h.perception.guidanceRequests, 0)
            XCTAssertEqual(h.speech.successes, 0)
            XCTAssertNotEqual(h.session.taskState?.phase, .guiding)
            h.speech.onSpeech = nil
            h.session.stop(.userEnd)
        }
    }

    func testStopUtteranceTakesPrecedenceOverObjectQuery() async {
        let h = harness()
        defer { h.session.stop(.userEnd) }
        h.session.start(); await settle()
        h.session.receiveLocalOwnerUtterance("stop finding my keys")
        XCTAssertEqual(h.session.phase, .paused)
        XCTAssertEqual(h.speech.localStops, 1)
        await settle()
        XCTAssertTrue(h.evidence.resolvedQueries.isEmpty)
        XCTAssertEqual(h.perception.guidanceRequests, 0)
    }

    func testMutedOwnerUtterancesCannotResolveObjects() async {
        let h = harness()
        defer { h.session.stop(.userEnd) }
        h.session.start(); await settle()
        h.session.setMuted(true)
        XCTAssertTrue(h.session.microphoneMuted)
        h.session.receiveLocalOwnerUtterance("find my phone")
        await settle()
        XCTAssertTrue(h.evidence.resolvedQueries.isEmpty)
        XCTAssertEqual(h.perception.guidanceRequests, 0)
        XCTAssertNotEqual(h.session.phase, .active)
    }

    func testLateObjectFailureCannotReplaceOrSpeakAfterStop() async throws {
        let h = harness()
        defer { h.speech.onSpeech = nil; h.session.stop(.userEnd) }
        h.session.start(); await settle()
        h.evidence.holdResolution = true
        h.evidence.resolutionError = .identificationOnly("silver keys")
        var spoken: [String] = []
        h.speech.onSpeech = { spoken.append(h.session.caption) }
        h.session.receiveLocalOwnerUtterance("find my keys")
        await settle()
        XCTAssertTrue(h.evidence.hasPendingResolution)
        XCTAssertEqual(h.evidence.resolutions, 1)
        h.session.receiveLocalOwnerUtterance("stop")
        let stoppedMessage = h.session.message
        let stoppedCaption = h.session.caption
        let speechAtStop = spoken
        XCTAssertEqual(h.session.phase, .paused)
        h.evidence.releaseResolution()
        await settle()
        XCTAssertEqual(h.session.message, stoppedMessage)
        XCTAssertEqual(h.session.caption, stoppedCaption)
        XCTAssertEqual(spoken, speechAtStop)
        XCTAssertEqual(h.perception.guidanceRequests, 0)
        XCTAssertFalse(spoken.contains(GeminiObjectError.identificationOnly("silver keys").localizedDescription))
    }

    func testMissingOfflineStopBlocksPhysicalTaskWhileMenuRemainsAvailable() async {
        let h = harness(offlineStop: false)
        defer { h.session.stop(.userEnd) }
        h.session.start(); await settle()
        XCTAssertEqual(h.session.phase, .active)
        XCTAssertFalse(h.session.capabilities.offlineStop.usable)
        h.conversation.emit(.intent(.init(id: "button", kind: .button, query: "blue button")))
        await settle()
        XCTAssertEqual(h.evidence.resolutions, 0)
        XCTAssertEqual(h.perception.guidanceRequests, 0)
        h.conversation.emit(.intent(.init(id: "menu", kind: .wallMenu, query: "read menu")))
        await settle()
        XCTAssertEqual(h.evidence.menuReads, 1)
        XCTAssertEqual(h.session.taskState?.phase, .discussingMenu)
        XCTAssertEqual(h.perception.guidanceRequests, 0)
    }

    func testSpeechOnlyPhysicalGuidanceRequiresR1Support() async {
        let h = harness(speechOnly: false)
        defer { h.session.stop(.userEnd) }
        h.session.start(); await settle()
        h.conversation.emit(.intent(.init(id: "button", kind: .button, query: "blue button")))
        await settle()
        XCTAssertEqual(h.evidence.resolutions, 0)
        XCTAssertEqual(h.perception.guidanceRequests, 0)
        XCTAssertEqual(h.session.phase, .active)
    }

    func testLiveCompositionPreservesR3FrameLimits() async {
        let h = harness()
        defer { h.session.stop(.userEnd) }
        h.session.start(); await settle()
        h.perception.emitFrame(bytes: 5); await settle()
        XCTAssertEqual(h.conversation.frameCount, 0)
        h.perception.emitFrame(bytes: 4); await settle()
        XCTAssertEqual(h.conversation.frameCount, 1)
        h.perception.emitFrame(bytes: 3); await settle()
        XCTAssertEqual(h.conversation.frameCount, 1)
        h.clock.advance(200); await settle()
        XCTAssertEqual(h.conversation.frameCount, 2)
    }

    func testLiveBandBundleInstallsSynchronizationAndSeedOnlyInLiveMode() async {
        let transport = CompositionTransport()
        var synchronizedServices: [BandService] = []
        var seedReads = 0
        // An explicitly unavailable test contract must never discover or dispatch physical BLE.
        let bands = LiveBandIntegrationBundle(hapticConfiguration: .unavailableH1, bandTransport: transport,
            generationSeed: { seedReads += 1; return 321 },
            configureBandSynchronization: { synchronizedServices.append($0) })
        let h = harness(bands: bands)
        defer { h.session.stop(.userEnd); h.session.bands.shutdown() }
        XCTAssertEqual(synchronizedServices.count, 1)
        XCTAssertTrue(synchronizedServices.first === h.session.bands)
        h.session.start(); await settle()
        XCTAssertEqual(h.session.context?.generation, 321)
        XCTAssertEqual(seedReads, 1)
        XCTAssertEqual(transport.discoveries, 0)
        XCTAssertEqual(transport.writes, 0)
        h.dependencies.setSimulation(true)
        XCTAssertEqual(h.perception.stops, 1)
        XCTAssertGreaterThan(transport.shutdowns, 0)
        XCTAssertEqual(synchronizedServices.count, 1)
        XCTAssertTrue(h.session.simulated)
        XCTAssertTrue(h.session.perception is FixturePerception)
        XCTAssertTrue(h.session.speech is FixtureSpeech)
        XCTAssertTrue(h.session.clock === h.clock)
        XCTAssertEqual(seedReads, 1)
        h.dependencies.setSimulation(false)
        XCTAssertEqual(synchronizedServices.count, 2)
        XCTAssertTrue(h.session.perception === h.perception)
        h.session.start(); await settle()
        XCTAssertEqual(h.session.context?.generation, 321)
        XCTAssertEqual(seedReads, 2)
    }

    func testLiveCompositionStillRequiresCameraAndMicrophonePermission() async {
        let h = harness(permissions: .init(camera: false, microphone: true))
        h.session.start(); await settle()
        XCTAssertEqual(h.session.phase, .failed)
        XCTAssertTrue(h.session.message.contains("Camera access is denied"))
        XCTAssertEqual(h.conversation.startCount, 0)
        XCTAssertNil(h.perception.context)
    }
}
