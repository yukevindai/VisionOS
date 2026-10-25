import XCTest
import Tasks
import HapticLink
@testable import Reflex

@MainActor
private final class TrackingPerception: PerceptionGuidanceClient, CameraTrackingSource {
    let base: FixturePerception
    var onTrackingState: ((CameraTrackingState) -> Void)?
    var onTrackingIssue: ((SessionContext, String) -> Void)?
    var onEvent: ((PerceptionEvent) -> Void)? { get { base.onEvent } set { base.onEvent = newValue } }
    var onFrame: ((FrameSnapshot) -> Void)? { get { base.onFrame } set { base.onFrame = newValue } }
    var onLocalCommand: ((SessionContext, LocalCommand) -> Void)? { get { base.onLocalCommand } set { base.onLocalCommand = newValue } }
    var onHapticRelease: ((ScheduledHapticRelease) -> Void)? { get { base.onHapticRelease } set { base.onHapticRelease = newValue } }
    init(clock: any AppClock) { base = FixturePerception(clock: clock) }
    func start(context: SessionContext, policy: CameraPolicy) async throws -> PerceptionReadiness { try await base.start(context: context, policy: policy) }
    func updateContext(_ context: SessionContext) { base.updateContext(context) }
    func reacquire(_ request: RequestContext, target: ResolvedTarget) async throws -> PhysicalEvidence { try await base.reacquire(request, target: target) }
    func guide(_ request: RequestContext, mode: GuidanceMode, target: ResolvedTarget, hand: GuidingHand) async throws -> TaskResponse { try await base.guide(request, mode: mode, target: target, hand: hand) }
    func authorizePassage(_ request: RequestContext, target: ResolvedTarget) async throws -> PhysicalEvidence { try await base.authorizePassage(request, target: target) }
    func cancelGuidance(context: SessionContext) { base.cancelGuidance(context: context) }
    func stop(context: SessionContext) { base.stop(context: context) }
}

@MainActor
private final class WaitingObjectEvidence: TargetEvidenceClient {
    var continuation: CheckedContinuation<ResolvedTarget, Error>?
    func resolve(_ request: RequestContext, query: String, kind: TaskKind) async throws -> ResolvedTarget {
        try await withCheckedThrowingContinuation { continuation = $0 }
    }
    func readScreen(_ request: RequestContext) async throws -> TaskScreen { throw CancellationError() }
    func readMenu(_ request: RequestContext, query: String) async throws -> MenuContent { throw CancellationError() }
    func answerMenu(_ request: RequestContext, question: String, menu: MenuContent) async throws -> String { throw CancellationError() }
    func verify(_ request: RequestContext, action: ActionKind, target: ResolvedTarget) async throws -> VerificationOutcome { .unavailable }
    func cancel(context: SessionContext) { continuation?.resume(throwing: CancellationError()); continuation = nil }
}

@MainActor
final class CameraTrackingStateTests: XCTestCase {
    private func harness() -> (SessionCoordinator, ManualAppClock, TrackingPerception) {
        let clock = ManualAppClock()
        // Use the same monotonic clock for capture, evidence and watchdog scheduling.
        let tracking = TrackingPerception(clock: clock)
        let session = SessionCoordinator(simulated: true,
            preferences: UserPreferences(defaults: UserDefaults(suiteName: UUID().uuidString)!),
            diagnostics: Diagnostics(), bands: BandService(controller: nil, simulated: true, clock: clock),
            clock: clock, perception: tracking, speech: FixtureSpeech(audible: false),
            conversation: FixtureConversation(), evidence: WaitingObjectEvidence(), permissions: FixturePermissionClient())
        return (session, clock, tracking)
    }
    private func settle() async { for _ in 0..<80 { await Task.yield() } }
    private func heartbeat(_ session: SessionCoordinator, _ clock: ManualAppClock,
                           _ source: TrackingPerception, status: CameraTrackingState.Status = .normal,
                           context: SessionContext? = nil, geometry: String? = nil, captured: Int64? = nil) {
        source.onTrackingState?(.init(context: context ?? session.context!, geometryID: geometry ?? source.base.camera.geometryID,
                                     capturedAtMs: captured ?? clock.nowMs, status: status))
    }

    func testFreshCaptureKeepsPendingObjectRequestAliveWhenEncodedFramesStall() async {
        let (session, clock, source) = harness()
        session.start(); await settle()
        session.submitTask(.init(id: "object-request", kind: .object, query: "find my bottle")); await settle()
        XCTAssertTrue(session.hasActiveTask)
        XCTAssertEqual(session.taskState?.request?.kind, .object)
        source.base.staleFrames = true
        for _ in 0..<12 { heartbeat(session, clock, source); clock.advance(100) }
        XCTAssertEqual(session.phase, .active)
        XCTAssertFalse(session.diagnostics.events.contains { $0.name == "tracking_loss" })
        session.stop(.userEnd)
    }

    func testCaptureOutageStillPausesPendingObjectRequestWithFreshEncodedFrames() async {
        let (session, clock, source) = harness()
        session.start(); await settle()
        session.submitTask(.init(id: "object-request", kind: .object, query: "find my bottle")); await settle()
        heartbeat(session, clock, source)
        clock.advance(300)
        XCTAssertEqual(session.phase, .paused)
        XCTAssertTrue(session.diagnostics.events.contains { $0.code == "CAPTURE_TRACKING_EXPIRED" })
        session.stop(.userEnd)
    }

    func testNewGenerationWaitsAtMost300msForItsOwnCapture() async {
        let (session, clock, source) = harness()
        session.start(); await settle()
        let oldContext = session.context!
        session.submitTask(.init(id: "object-request", kind: .object, query: "find my bottle")); await settle()
        XCTAssertNotEqual(session.context, oldContext)
        heartbeat(session, clock, source, context: oldContext)
        clock.advance(200)
        XCTAssertEqual(session.phase, .active)
        clock.advance(100)
        XCTAssertEqual(session.phase, .paused)
        session.stop(.userEnd)
    }

    func testWrongGeometryFutureAndStaleCapturesCannotKeepTaskAlive() async {
        let (session, clock, source) = harness()
        session.start(); await settle()
        session.submitTask(.init(id: "object-request", kind: .object, query: "find my bottle")); await settle()
        heartbeat(session, clock, source, geometry: "wrong")
        heartbeat(session, clock, source, captured: clock.nowMs + 1)
        heartbeat(session, clock, source, captured: clock.nowMs - 301)
        clock.advance(300)
        XCTAssertEqual(session.phase, .paused)
        session.stop(.userEnd)
    }

    func testTrueTrackingLossStillStopsAndDoesNotAutomaticallyResume() async {
        let (session, clock, source) = harness()
        session.start(); await settle()
        session.submitTask(.init(id: "object-request", kind: .object, query: "find my bottle")); await settle()
        heartbeat(session, clock, source)
        source.onEvent?(.trackingLost(session.context!))
        XCTAssertEqual(session.phase, .paused)
        heartbeat(session, clock, source)
        source.onEvent?(.recovered(session.context!))
        XCTAssertEqual(session.phase, .paused)
        session.stop(.userEnd)
    }

    func testCameraHeartbeatCannotSubstituteForFreshResumeEvidence() async {
        let (session, clock, source) = harness()
        session.start(); await settle()
        session.stop(.userPause)
        source.base.staleFrames = true
        clock.advance(500)
        heartbeat(session, clock, source)
        session.resume()
        XCTAssertEqual(session.phase, .paused)
        session.stop(.userEnd)
    }

    func testMailboxPreservesLossWhenNewerNormalCaptureArrives() {
        let mailbox = CameraTrackingMailbox(), context = SessionContext(sessionID: "session", generation: 1)
        XCTAssertTrue(mailbox.offer(.init(context: context, geometryID: "g", capturedAtMs: 1_000, status: .insufficientFeatures)))
        XCTAssertFalse(mailbox.offer(.init(context: context, geometryID: "g", capturedAtMs: 1_500, status: .normal)))
        let result = mailbox.take()
        XCTAssertEqual(result.map(\.status), [.insufficientFeatures, .normal])
        XCTAssertFalse(result[0].isFresh(at: 1_500))
        XCTAssertTrue(result[0].isActionable(at: 1_500), "A delayed observed loss must still stop the matching generation")
        XCTAssertTrue(result[1].isFresh(at: 1_500))
        XCTAssertFalse(result[1].isActionable(at: 1_801), "Old normal state cannot establish recovery")
        XCTAssertTrue(mailbox.take().isEmpty)
    }
}
