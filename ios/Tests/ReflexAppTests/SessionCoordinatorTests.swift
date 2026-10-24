import XCTest
import UIKit
import Tasks
@testable import Reflex

@MainActor
final class ManualAppClock: AppClock {
    var nowMs: Int64 = 1_000
    private var work: [UUID: (Int64, @MainActor () -> Void)] = [:]
    var pendingCount: Int { work.count }
    func schedule(afterMs: Int64, _ action: @escaping @MainActor () -> Void) -> ScheduledWork {
        let id = UUID(); work[id] = (nowMs + afterMs, action)
        return ScheduledWork { [weak self] in self?.work.removeValue(forKey: id) }
    }
    func advance(_ milliseconds: Int64) {
        let target = nowMs + milliseconds
        while let first = work.filter({ $0.value.0 <= target }).min(by: { $0.value.0 < $1.value.0 }) {
            nowMs = first.value.0; work.removeValue(forKey: first.key); first.value.1()
        }
        nowMs = target
    }
}

@MainActor
private final class DelayedEvidence: TargetEvidenceClient {
    let base: FixtureEvidence
    var pending: CheckedContinuation<ResolvedTarget, Error>?
    var received: RequestContext?
    init(clock: any AppClock) { base = FixtureEvidence(clock: clock) }
    func resolve(_ request: RequestContext, query: String, kind: TaskKind) async throws -> ResolvedTarget {
        received = request
        return try await withCheckedThrowingContinuation { pending = $0 }
    }
    func readScreen(_ request: RequestContext) async throws -> TaskScreen { try await base.readScreen(request) }
    func readMenu(_ request: RequestContext, query: String) async throws -> MenuContent { try await base.readMenu(request, query: query) }
    func answerMenu(_ request: RequestContext, question: String, menu: MenuContent) async throws -> String { try await base.answerMenu(request, question: question, menu: menu) }
    func verify(_ request: RequestContext, action: ActionKind, target: ResolvedTarget) async throws -> VerificationOutcome { .unavailable }
    func cancel(context: SessionContext) {} // Deliberately ignores cancellation to model a late provider response.
    func complete() {
        pending?.resume(returning: TaskFixtures.target())
        pending = nil
    }
}

@MainActor
final class SessionCoordinatorTests: XCTestCase {
    private struct Harness {
        let session: SessionCoordinator
        let clock: ManualAppClock
        let perception: FixturePerception
        let conversation: FixtureConversation
        let speech: FixtureSpeech
    }
    private func makeHarness(evidence: (any TargetEvidenceClient)? = nil, clock: ManualAppClock? = nil,
                             permission: PermissionState = .init(camera: true, microphone: true), simulated: Bool = true) -> Harness {
        let clock = clock ?? ManualAppClock()
        let prefs = UserPreferences(defaults: UserDefaults(suiteName: UUID().uuidString)!)
        prefs.setupComplete = true
        let perception = FixturePerception(clock: clock), speech = FixtureSpeech(audible: false), conversation = FixtureConversation()
        let session = SessionCoordinator(simulated: simulated, preferences: prefs, diagnostics: Diagnostics(capacity: 8),
            bands: BandService(controller: nil, simulated: simulated, clock: clock), clock: clock,
            perception: perception, speech: speech, conversation: conversation,
            evidence: evidence ?? FixtureEvidence(clock: clock), permissions: FixturePermissionClient(state: permission))
        return .init(session: session, clock: clock, perception: perception, conversation: conversation, speech: speech)
    }
    private func settle() async { for _ in 0..<80 { await Task.yield() } }

    func testRepeatedStartAndEndAreIdempotent() async {
        let h = makeHarness()
        h.session.start(); let first = h.session.context
        h.session.start(); await settle()
        XCTAssertEqual(first, h.session.context)
        XCTAssertEqual(h.session.phase, .active)
        h.session.stop(.userEnd); let generation = h.session.context?.generation
        h.session.stop(.userEnd)
        XCTAssertEqual(h.session.context?.generation, generation)
        XCTAssertEqual(h.session.phase, .ended)
        XCTAssertEqual(h.clock.pendingCount, 0)
    }
    #if DEBUG
    func testTaskHazardRetainsExplanationAfterUnifiedStop() async {
        let clock = ManualAppClock()
        let evidence = FixtureEvidence(clock: clock)
        evidence.uiTestScenario = .blockedDoor
        let h = makeHarness(evidence: evidence, clock: clock)
        h.session.start(); await settle()
        h.session.submitFixtureText("open the door"); await settle()
        let explanation = "The door may be locked, heavy, or blocked. Please ask for help."
        XCTAssertEqual(h.session.phase, .paused)
        XCTAssertEqual(h.session.taskState?.lastStop, .hazard)
        XCTAssertEqual(h.session.message, explanation)
        XCTAssertEqual(h.session.caption, explanation)
        XCTAssertGreaterThan(h.speech.stoppedCount, 0)
        XCTAssertEqual(h.speech.spoken.filter { $0 == explanation }.count, 1)
        XCTAssertNil(h.session.taskState?.confirmation)
        h.session.confirmCurrent(true); await settle()
        XCTAssertNil(h.session.taskState?.result)
        XCTAssertEqual(h.session.phase, .paused)
        h.session.stop(.userEnd)
    }

    #endif

    func testButtonRequiresExplicitScopedConfirmation() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.session.submitFixtureText("press the blue button"); await settle()
        XCTAssertEqual(h.session.taskState?.phase, .awaitingConfirmation)
        XCTAssertNil(h.session.taskState?.result)
        h.session.confirmCurrent(true); await settle()
        XCTAssertEqual(h.session.taskState?.result?.verification, .userConfirmed)
        XCTAssertEqual(h.session.taskState?.result?.status, .completed)
        h.session.stop(.userEnd)
    }
    func testStopRejectsNonCooperativeLateProviderResponse() async {
        let clock = ManualAppClock(), delayed = DelayedEvidence(clock: ManualAppClock())
        let h = makeHarness(evidence: delayed, clock: clock)
        h.session.start(); await settle()
        h.session.submitFixtureText("press the button"); await settle()
        XCTAssertNotNil(delayed.pending)
        let oldGeneration = h.session.context!.generation
        h.session.stop(.localStop)
        XCTAssertGreaterThan(h.session.context!.generation, oldGeneration)
        XCTAssertEqual(h.session.phase, .paused)
        delayed.complete(); await settle()
        XCTAssertEqual(h.session.phase, .paused)
        XCTAssertNil(h.session.taskState?.confirmation)
        XCTAssertFalse(h.speech.spoken.contains { $0.contains("task completed") })
        h.session.stop(.userEnd)
    }
    func testOldConfirmationAfterStopDoesNotCompleteTask() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.session.submitFixtureText("pick up the cup"); await settle()
        let pending = h.session.taskState!.confirmation!
        let old = h.session.context!
        h.session.stop(.userPause)
        h.conversation.onEvent?(.init(context: old, eventID: "late", payload: .confirmation(promptID: pending.promptID, answer: .yes, explicitUserResponse: true)))
        h.session.confirmCurrent(true)
        XCTAssertNil(h.session.taskState?.result)
        XCTAssertEqual(h.session.phase, .paused)
        h.session.stop(.userEnd)
    }
    func testMuteBackgroundAndAudioInterruptionRequireExplicitResume() async {
        for cause in [SessionStopCause.background, .audioInterruption, .audioRouteLost] {
            let h = makeHarness(); h.session.start(); await settle()
            h.session.stop(cause); h.clock.advance(100)
            XCTAssertEqual(h.session.phase, .paused)
            h.session.resume(); XCTAssertEqual(h.session.phase, .active)
            h.session.setMuted(true)
            XCTAssertEqual(h.session.phase, .paused)
            h.session.resume(); XCTAssertEqual(h.session.phase, .paused)
            h.session.setMuted(false); XCTAssertEqual(h.session.phase, .paused)
            h.session.stop(.userEnd)
        }
    }
    func testDeniedPermissionsDoNotStartAdapters() async {
        let h = makeHarness(permission: .init(camera: false, microphone: true), simulated: false)
        h.session.start(); await settle()
        XCTAssertTrue(h.session.showSetup)
        XCTAssertEqual(h.session.phase, .idle)
        XCTAssertNil(h.perception.context)
    }
    func testPhysicalGuidanceIsBlockedWithoutLocalSpokenStopInLiveMode() async {
        let h = makeHarness(simulated: false); h.session.start(); await settle()
        h.session.submitTask(.init(id: "task", kind: .button, query: "Blue button"))
        XCTAssertNil(h.session.taskState?.request)
        XCTAssertTrue(h.session.message.contains("offline spoken Stop"))
        h.session.stop(.userEnd)
    }
    func testTrackingRecoveryDoesNotAutomaticallyResume() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.perception.inject(.trackingLost)
        XCTAssertEqual(h.session.phase, .paused)
        h.perception.recover()
        XCTAssertEqual(h.session.phase, .paused)
        h.session.resume(); XCTAssertEqual(h.session.phase, .active)
        h.session.stop(.userEnd)
    }
    func testStaleFrameWatchdogStopsActiveTask() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.session.submitFixtureText("press the button"); await settle()
        h.perception.staleFrames = true
        h.clock.advance(500)
        XCTAssertEqual(h.session.phase, .paused)
        XCTAssertNil(h.session.taskState?.confirmation)
        h.session.stop(.userEnd)
    }

    func testPreviewExpiresWhileListeningWithoutAnActiveTask() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.perception.stop(context: h.session.context!)
        XCTAssertNil(h.session.taskState?.request)
        let image = syntheticPreview(.red)
        let now = h.clock.nowMs
        deliverPreview(image, to: h, capturedAtMs: now - 200, expiresAtMs: now + 500)
        XCTAssertTrue(h.session.preview === image)

        h.clock.advance(99)
        XCTAssertTrue(h.session.preview === image)
        h.clock.advance(1)
        XCTAssertNil(h.session.preview, "A frozen image must clear at the 300 ms capture-age limit, even without a task.")
        XCTAssertEqual(h.session.phase, .active)
        h.session.stop(.userEnd)
        XCTAssertEqual(h.clock.pendingCount, 0)
    }

    func testPausedPreviewExpiresAtItsEarlierFrameDeadline() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.perception.stop(context: h.session.context!)
        h.session.stop(.userPause)
        let image = syntheticPreview(.blue)
        let now = h.clock.nowMs
        deliverPreview(image, to: h, capturedAtMs: now, expiresAtMs: now + 20)
        XCTAssertTrue(h.session.preview === image)

        h.clock.advance(19)
        XCTAssertTrue(h.session.preview === image)
        h.clock.advance(1)
        XCTAssertNil(h.session.preview, "Pausing guidance must not leave the last camera image indefinitely visible.")
        XCTAssertEqual(h.session.phase, .paused)
        h.session.stop(.userEnd)
        XCTAssertEqual(h.clock.pendingCount, 0)
    }

    func testNewPreviewSurvivesThePreviousFramesExpiry() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.perception.stop(context: h.session.context!)
        let first = syntheticPreview(.red)
        let second = syntheticPreview(.blue)
        let start = h.clock.nowMs
        deliverPreview(first, to: h, capturedAtMs: start, expiresAtMs: start + 100)
        h.clock.advance(50)
        deliverPreview(second, to: h, capturedAtMs: h.clock.nowMs, expiresAtMs: start + 300)

        h.clock.advance(50)
        XCTAssertTrue(h.session.preview === second, "The superseded frame's deadline must not remove the current camera image.")
        h.clock.advance(199)
        XCTAssertTrue(h.session.preview === second)
        h.clock.advance(1)
        XCTAssertNil(h.session.preview)
        h.session.stop(.userEnd)
        XCTAssertEqual(h.clock.pendingCount, 0)
    }

    func testStopImmediatelyClearsPreviewIncludingRepeatedPause() async {
        for cause in [SessionStopCause.userPause, .localStop, .trackingLost, .background, .userEnd] {
            let h = makeHarness(); h.session.start(); await settle()
            h.perception.stop(context: h.session.context!)
            let image = syntheticPreview(.red)
            deliverPreview(image, to: h, capturedAtMs: h.clock.nowMs, expiresAtMs: h.clock.nowMs + 300)
            XCTAssertNotNil(h.session.preview)
            h.session.stop(cause)
            XCTAssertNil(h.session.preview, "Stop cause: \(cause)")
            if cause == .userPause {
                deliverPreview(image, to: h, capturedAtMs: h.clock.nowMs, expiresAtMs: h.clock.nowMs + 300)
                XCTAssertNotNil(h.session.preview)
                h.session.stop(.userPause)
                XCTAssertNil(h.session.preview, "A repeated Stop must also clear a fresh image received while paused.")
            }
            h.session.stop(.userEnd)
            XCTAssertEqual(h.clock.pendingCount, 0)
        }
    }

    private func syntheticPreview(_ color: UIColor) -> UIImage {
        // Pixel-backed fixture only; this does not exercise a physical camera.
        let bounds = CGRect(x: 0, y: 0, width: 4, height: 4)
        return UIGraphicsImageRenderer(size: bounds.size).image { context in
            color.setFill()
            context.fill(bounds)
        }
    }

    private func deliverPreview(_ image: UIImage, to h: Harness, capturedAtMs: Int64, expiresAtMs: Int64) {
        h.perception.onFrame?(.init(context: h.session.context!, frameID: UUID().uuidString,
            capturedAtMs: capturedAtMs, expiresAtMs: expiresAtMs, geometryID: h.perception.camera.geometryID,
            transform: .identity, preview: image, encodedImage: nil, trackingNormal: true, depthUsable: true))
    }

    func testRepeatedSessionsCleanUpTimersAndUseFreshIdentities() async {
        let h = makeHarness(); var ids = Set<String>()
        for _ in 0..<10 {
            h.session.start(); await settle()
            ids.insert(h.session.context!.sessionID)
            h.session.stop(.userEnd)
            XCTAssertEqual(h.clock.pendingCount, 0)
        }
        XCTAssertEqual(ids.count, 10)
        XCTAssertLessThanOrEqual(h.session.diagnostics.events.count, 8)
    }
    func testTransformRejectsInvalidCropAndMapsToOriginalCoordinates() async {
        let crop = FrameTransform(originX: 0.2, originY: 0.3, scaleX: 0.5, scaleY: 0.4)
        XCTAssertEqual(crop.originalPoint(x: 0.5, y: 0.5)?.x ?? 0, 0.45, accuracy: 0.0001)
        XCTAssertNil(crop.originalPoint(x: .nan, y: 0))
        XCTAssertFalse(FrameTransform(originX: 0.5, originY: 0, scaleX: 0.8, scaleY: 1).isValid)
    }
    func testObjectDoorAndApproachFixtureFlows() async {
        for query in ["pick up the cup", "open the door", "approach the button"] {
            let h = makeHarness(); h.session.start(); await settle()
            h.session.submitFixtureText(query); await settle()
            if h.session.taskState?.confirmation != nil { h.session.confirmCurrent(true); await settle() }
            XCTAssertEqual(h.session.taskState?.result?.status, .completed, query)
            h.session.stop(.userEnd)
        }
    }
    func testKioskStopsAtPaymentAndNextTaskStartsAtFirstScreen() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.session.submitFixtureText("order at kiosk"); await settle()
        XCTAssertEqual(h.session.taskState?.phase, .choosing)
        h.session.submitFixtureText("Tea"); await settle()
        h.session.confirmCurrent(true); await settle()
        XCTAssertEqual(h.session.taskState?.phase, .choosing)
        h.session.submitFixtureText("Add sandwich"); await settle()
        XCTAssertEqual(h.session.taskState?.result?.error, .paymentBoundary)
        XCTAssertEqual(h.session.taskState?.result?.status, .handoff)
        h.session.submitFixtureText("order at kiosk"); await settle()
        XCTAssertEqual(h.session.taskState?.phase, .choosing)
        h.session.stop(.userEnd)
    }
    func testCalibrationStopAndBackgroundAlwaysCancel() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.session.beginCalibration()
        XCTAssertEqual(h.session.calibrationIndex, 0)
        let first = h.session.context!.generation
        h.session.playCalibration()
        XCTAssertGreaterThan(h.session.context!.generation, first)
        let stops = h.speech.stoppedCount
        h.session.stopCalibration()
        XCTAssertNil(h.session.calibrationIndex)
        XCTAssertGreaterThan(h.speech.stoppedCount, stops)
        h.session.beginCalibration(); h.session.bands.onSafetyPause?()
        XCTAssertNil(h.session.calibrationIndex)
        h.session.beginCalibration(); h.session.backgrounded()
        XCTAssertNil(h.session.calibrationIndex)
        h.session.stop(.userEnd)
    }
    func testCalibrationCompletesOnlyAfterAllExplicitAnswers() async {
        let h = makeHarness(); h.session.start(); await settle(); h.session.beginCalibration()
        h.session.answerCalibration("Wrong")
        XCTAssertEqual(h.session.calibrationIndex, 0)
        for _ in 0..<11 { h.session.answerCalibration(h.session.calibrationExpectedAnswer!) }
        XCTAssertNil(h.session.calibrationIndex)
        XCTAssertTrue(h.session.preferences.simulatedCalibrationComplete)
        XCTAssertNil(h.session.preferences.calibratedPairSignature)
        h.session.stop(.userEnd)
    }
    func testReconnectIsBoundedAndDoesNotResumeMovement() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.conversation.emit(.disconnected)
        XCTAssertEqual(h.session.phase, .recovering)
        h.clock.advance(250); await settle()
        XCTAssertEqual(h.session.phase, .paused)
        XCTAssertEqual(h.conversation.startCount, 2)
        h.clock.advance(100); h.session.resume()
        h.conversation.failNextStarts = 3
        h.conversation.emit(.disconnected)
        for delay in [250, 500, 1_000] { h.clock.advance(Int64(delay)); await settle() }
        XCTAssertEqual(h.session.phase, .paused)
        XCTAssertEqual(h.conversation.startCount, 5)
        h.session.stop(.userEnd)
    }
    func testEndInterruptsReconnectBackoff() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.conversation.emit(.disconnected); h.session.stop(.userEnd)
        h.clock.advance(10_000); await settle()
        XCTAssertEqual(h.session.phase, .ended)
        XCTAssertEqual(h.conversation.startCount, 1)
        XCTAssertEqual(h.clock.pendingCount, 0)
    }
    func testStartupTimeoutIgnoresLateStartCompletion() async {
        let h = makeHarness(); h.conversation.holdStart = true
        h.session.start(); await settle()
        XCTAssertEqual(h.session.phase, .preparing)
        h.clock.advance(5_000)
        XCTAssertEqual(h.session.phase, .failed)
        h.conversation.releaseHeldStart(); await settle()
        XCTAssertEqual(h.session.phase, .failed)
        h.session.stop(.userEnd)
    }
    func testFrameDeliveryContinuesAcrossTaskGenerationChanges() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.clock.advance(100); await settle()
        let before = h.conversation.frameCount
        h.session.submitFixtureText("press the button"); await settle()
        h.clock.advance(100); await settle()
        XCTAssertGreaterThan(h.conversation.frameCount, before)
        h.session.stop(.userEnd)
    }
    func testModelConfirmationAndWrongPromptCannotFinishTask() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.session.submitFixtureText("press the button"); await settle()
        let prompt = h.session.taskState!.confirmation!.promptID
        h.conversation.emit(.confirmation(promptID: prompt, answer: .yes, explicitUserResponse: false))
        XCTAssertNil(h.session.taskState?.result)
        h.conversation.emit(.confirmation(promptID: "old-prompt", answer: .yes, explicitUserResponse: true))
        XCTAssertNil(h.session.taskState?.result)
        h.session.stop(.userEnd)
    }

    func testDisconnectWhilePausedRequiresRecoveryBeforeResume() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.session.stop(.userPause)
        h.conversation.holdStart = true
        h.conversation.emit(.disconnected)
        XCTAssertEqual(h.session.phase, .recovering)
        XCTAssertFalse(h.session.capabilities.conversation.usable)
        h.clock.advance(250); await settle()
        h.session.resume()
        XCTAssertEqual(h.session.phase, .recovering)
        h.conversation.releaseHeldStart(); await settle()
        XCTAssertEqual(h.session.phase, .paused)
        h.clock.advance(100); h.session.resume()
        XCTAssertEqual(h.session.phase, .active)
        h.session.stop(.userEnd)
    }

    func testNewSessionResetsTransportMute() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.session.setMuted(true)
        XCTAssertTrue(h.conversation.muted)
        h.session.stop(.userEnd)
        h.session.start(); await settle()
        XCTAssertEqual(h.session.phase, .active)
        XCTAssertFalse(h.session.microphoneMuted)
        XCTAssertFalse(h.conversation.muted)
        h.session.stop(.userEnd)
    }

    func testReconnectRestoresMuteWhenTransportResetsItsAudioState() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.session.setMuted(true)
        h.conversation.holdStart = true
        h.conversation.emit(.disconnected)
        h.clock.advance(250); await settle()
        h.conversation.setMicrophoneMuted(false)
        h.conversation.releaseHeldStart(); await settle()
        XCTAssertEqual(h.session.phase, .paused)
        XCTAssertTrue(h.session.microphoneMuted)
        XCTAssertTrue(h.conversation.muted)
        h.session.resume()
        XCTAssertEqual(h.session.phase, .paused)
        h.session.stop(.userEnd)
    }

    func testResumeRejectsExpiredFrameWithinAgeLimit() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.session.stop(.userPause)
        let now = h.clock.nowMs
        h.perception.onFrame?(.init(context: h.session.context!, frameID: "short-lived",
            capturedAtMs: now, expiresAtMs: now + 1, geometryID: h.perception.camera.geometryID,
            transform: .identity, preview: nil, encodedImage: nil, trackingNormal: true, depthUsable: true))
        h.clock.advance(2)
        h.session.resume()
        XCTAssertEqual(h.session.phase, .paused)
        h.perception.emitFrame()
        h.session.resume()
        XCTAssertEqual(h.session.phase, .active)
        h.session.stop(.userEnd)
    }

    func testCalibrationCannotRestartOutputDuringHazardOrMute() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.perception.inject(.hazard)
        h.session.beginCalibration()
        XCTAssertNil(h.session.calibrationIndex)
        h.perception.recover()
        h.session.setMuted(true)
        h.session.beginCalibration()
        XCTAssertNil(h.session.calibrationIndex)
        h.session.setMuted(false)
        h.session.beginCalibration()
        XCTAssertEqual(h.session.calibrationIndex, 0)
        h.session.stop(.userEnd)
    }

    func testRepeatDoesNotReplayInstructionsWhilePaused() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.session.stop(.userPause)
        h.speech.onCaption?(h.session.context!, "Move left")
        let count = h.speech.spoken.count
        h.session.command(.repeatLast)
        XCTAssertEqual(h.speech.spoken.count, count)
        h.session.stop(.userEnd)
    }

    func testRepeatMenuOptionsUsesPendingTaskInsteadOfUnrelatedCaption() async {
        let h = makeHarness(); h.session.start(); await settle()
        h.session.submitFixtureText("order at kiosk"); await settle()
        XCTAssertEqual(h.session.taskState?.phase, .choosing)
        h.speech.onCaption?(h.session.context!, "Unrelated conversation")
        h.session.command(.repeatLast)
        XCTAssertTrue(h.speech.spoken.last?.contains("Tea") == true)
        XCTAssertEqual(h.session.taskState?.phase, .choosing)
        h.session.stop(.userEnd)
    }

}
