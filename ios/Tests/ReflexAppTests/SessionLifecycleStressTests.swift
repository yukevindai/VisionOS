import XCTest
import Tasks
@testable import Reflex

@MainActor
final class SessionLifecycleStressTests: XCTestCase {
    private func start(_ h: AdapterCompatibilityHarness, retryWhilePreviousAttemptStops: Bool = false) async {
        let ready = expectation(description: "R2 announced readiness")
        h.speech.onSpeech = { ready.fulfill() }
        h.session.start()
        // Releasing an adapter continuation does not synchronously finish its caller.
        // Retry the public Start action until that cancelled attempt has unwound;
        // a single MainActor turn is not a FIFO completion barrier.
        let retry = retryWhilePreviousAttemptStops ? Task { @MainActor in
            while h.session.phase == .ended && !Task.isCancelled {
                await Task.yield()
                guard !Task.isCancelled else { return }
                h.session.start()
            }
        } : nil
        await fulfillment(of: [ready], timeout: 2)
        retry?.cancel()
        h.speech.onSpeech = nil
        XCTAssertEqual(h.session.phase, .active)
    }

    private func beginHeldSend(_ h: AdapterCompatibilityHarness) async {
        let sent = expectation(description: "Transport entered held frame send")
        h.conversation.onSend = { _ in sent.fulfill() }
        h.perception.emitFrame()
        await fulfillment(of: [sent], timeout: 2)
        h.conversation.onSend = nil
        XCTAssertEqual(h.conversation.pendingSendCount, 1)
    }

    /// A main-actor queue turn gives newly scheduled adapter tasks a chance to enter their gates.
    /// Completion-sensitive assertions additionally wait on an explicit adapter callback.
    private func drainActorTurn() async {
        await withCheckedContinuation { continuation in
            Task { @MainActor in continuation.resume() }
        }
    }

    func testQueuedFrameCannotUseLongExpiryToOutliveCaptureFreshness() async {
        let h = AdapterCompatibilityHarness()
        defer { h.session.stop(.userEnd); h.conversation.releaseAll() }
        await start(h)
        await beginHeldSend(h)
        let context = h.session.context!
        h.perception.onFrame?(.init(context: context, frameID: "queued-long-expiry",
            capturedAtMs: h.clock.nowMs, expiresAtMs: h.clock.nowMs + 5_000,
            geometryID: h.perception.camera.geometryID, transform: .identity,
            preview: nil, encodedImage: nil, trackingNormal: true, depthUsable: true))
        h.perception.stop(context: context)
        h.clock.advance(301)
        let completed = expectation(description: "Stalled send returns after pending frame ages out")
        h.conversation.onCompletion = { completed.fulfill() }
        h.conversation.releaseFirstSend()
        await fulfillment(of: [completed], timeout: 2)
        h.conversation.onCompletion = nil
        await drainActorTurn()
        XCTAssertEqual(h.conversation.sentFrames.count, 1)
        XCTAssertEqual(h.conversation.activeSends, 0)

        let fresh = expectation(description: "A fresh frame can use the released slot")
        h.conversation.onSend = { _ in fresh.fulfill() }
        h.perception.onFrame?(.init(context: context, frameID: "fresh-after-backpressure",
            capturedAtMs: h.clock.nowMs, expiresAtMs: h.clock.nowMs + 300,
            geometryID: h.perception.camera.geometryID, transform: .identity,
            preview: nil, encodedImage: nil, trackingNormal: true, depthUsable: true))
        await fulfillment(of: [fresh], timeout: 2)
        h.conversation.onSend = nil
        XCTAssertEqual(h.conversation.sentFrames.last?.frameID, "fresh-after-backpressure")
    }

    func testFrameExpiringBeforeSendTaskRunsIsDroppedAndSlotIsReleased() async {
        let h = AdapterCompatibilityHarness()
        defer { h.session.stop(.userEnd); h.conversation.releaseAll() }
        await start(h)
        let context = h.session.context!
        h.perception.stop(context: context)
        h.perception.onFrame?(.init(context: context, frameID: "expires-before-send",
            capturedAtMs: h.clock.nowMs, expiresAtMs: h.clock.nowMs + 1,
            geometryID: h.perception.camera.geometryID, transform: .identity,
            preview: nil, encodedImage: nil, trackingNormal: true, depthUsable: true))
        h.clock.advance(2)
        await drainActorTurn()
        XCTAssertTrue(h.conversation.sentFrames.isEmpty)
        XCTAssertEqual(h.conversation.activeSends, 0)

        let fresh = expectation(description: "Dropped send does not retain the occupied slot")
        h.conversation.onSend = { _ in fresh.fulfill() }
        h.perception.onFrame?(.init(context: context, frameID: "fresh-after-expiry",
            capturedAtMs: h.clock.nowMs, expiresAtMs: h.clock.nowMs + 300,
            geometryID: h.perception.camera.geometryID, transform: .identity,
            preview: nil, encodedImage: nil, trackingNormal: true, depthUsable: true))
        await fulfillment(of: [fresh], timeout: 2)
        h.conversation.onSend = nil
        XCTAssertEqual(h.conversation.sentFrames.last?.frameID, "fresh-after-expiry")
    }

    func testTwentyVirtualMinutesKeepOnlyLatestFrameBehindStalledTransport() async {
        let h = AdapterCompatibilityHarness()
        defer { h.session.stop(.userEnd); h.conversation.releaseAll() }
        await start(h)
        await beginHeldSend(h)
        let initialTime = h.clock.nowMs
        for _ in 0..<1_200 {
            h.clock.advance(1_000)
            XCTAssertLessThanOrEqual(h.clock.pendingCount, 2, "Only fixture capture and freshness watchdog remain scheduled")
        }
        XCTAssertEqual(h.clock.nowMs - initialTime, 20 * 60 * 1_000)
        XCTAssertEqual(h.conversation.sentFrames.count, 1)
        XCTAssertEqual(h.conversation.activeSends, 1)
        let latest = expectation(description: "Latest frame replaces entire stalled backlog")
        h.conversation.onSend = { _ in latest.fulfill() }
        h.conversation.releaseFirstSend()
        await fulfillment(of: [latest], timeout: 2)
        h.conversation.onSend = nil
        XCTAssertEqual(h.conversation.sentFrames.count, 2)
        XCTAssertEqual(h.conversation.sentFrames.last?.frameID, "fixture-\(h.clock.nowMs)")
        XCTAssertEqual(h.conversation.maximumConcurrentSends, 1)
        h.session.stop(.userEnd)
        XCTAssertEqual(h.clock.pendingCount, 0)
        XCTAssertLessThanOrEqual(h.session.diagnostics.events.count, 16)
        let completed = expectation(description: "Held send finishes after End")
        h.conversation.onCompletion = { completed.fulfill() }
        h.conversation.releaseAll()
        await fulfillment(of: [completed], timeout: 2)
        h.conversation.onCompletion = nil
        XCTAssertEqual(h.conversation.activeSends, 0)
        XCTAssertEqual(h.conversation.sentFrames.count, 2)
    }

    func testStalledSendRemainsBoundedAcrossRepeatedStopResumeAndNewSessions() async {
        let h = AdapterCompatibilityHarness()
        defer { h.session.stop(.userEnd); h.conversation.releaseAll() }
        await start(h)
        await beginHeldSend(h)
        let firstSession = h.session.context!.sessionID
        for _ in 0..<40 {
            let previous = h.session.context!.generation
            h.session.stop(.localStop)
            XCTAssertGreaterThan(h.session.context!.generation, previous)
            h.perception.emitFrame()
            h.session.resume()
            h.perception.emitFrame()
            await drainActorTurn()
            XCTAssertEqual(h.session.phase, .active)
            XCTAssertEqual(h.conversation.activeSends, 1)
        }
        var identities = Set([firstSession])
        for _ in 0..<25 {
            h.session.stop(.userEnd)
            XCTAssertEqual(h.clock.pendingCount, 0)
            await start(h)
            identities.insert(h.session.context!.sessionID)
            h.perception.emitFrame()
            await drainActorTurn()
            XCTAssertEqual(h.conversation.activeSends, 1)
        }
        XCTAssertEqual(identities.count, 26)
        XCTAssertEqual(h.conversation.maximumConcurrentSends, 1)
        let resumed = expectation(description: "New session receives only its current pending frame")
        h.conversation.onSend = { _ in resumed.fulfill() }
        h.conversation.releaseFirstSend()
        await fulfillment(of: [resumed], timeout: 2)
        h.conversation.onSend = nil
        XCTAssertEqual(h.conversation.sentFrames.count, 2)
        XCTAssertEqual(h.conversation.sentFrames.last?.context, h.session.context)
        XCTAssertNotEqual(h.conversation.sentFrames.last?.context.sessionID, firstSession)
    }

    func testReconnectDuringCalibrationRejectsOldAnswersAndRequiresNewCalibration() async {
        let h = AdapterCompatibilityHarness()
        defer { h.session.stop(.userEnd); h.conversation.releaseAll() }
        await start(h)
        h.session.beginCalibration()
        let oldContext = h.session.context!
        XCTAssertEqual(h.speech.calibrations.count, 1)
        h.conversation.emit(.disconnected)
        XCTAssertEqual(h.session.phase, .recovering)
        XCTAssertNil(h.session.calibrationIndex)
        XCTAssertTrue(h.speech.cancellations.contains(oldContext))
        let reconnected = expectation(description: "Recovery attempts conversation start")
        h.conversation.onStart = { reconnected.fulfill() }
        h.clock.advance(250)
        await fulfillment(of: [reconnected], timeout: 2)
        h.conversation.onStart = nil
        XCTAssertEqual(h.session.phase, .paused)
        for answer in ["Left", "Right", "Move left", "Move right", "Move up", "Move down", "Closer", "On target", "Success", "Stop", "Lost target or hand"] {
            h.conversation.emit(.calibrationAnswer(answer, explicitUserResponse: true), context: oldContext)
        }
        XCTAssertFalse(h.session.preferences.simulatedCalibrationComplete)
        XCTAssertEqual(h.speech.calibrations.count, 1)
        h.session.beginCalibration()
        XCTAssertEqual(h.session.calibrationIndex, 0)
        XCTAssertEqual(h.speech.calibrations.count, 2)
        XCTAssertGreaterThan(h.speech.calibrations.last!.generation, oldContext.generation)
    }

    func testEndReleasesCoordinatorWhileTransportIgnoresCancellation() async {
        var h: AdapterCompatibilityHarness? = AdapterCompatibilityHarness()
        let transport = h!.conversation, clock = h!.clock
        defer { transport.releaseAll() }
        await start(h!)
        await beginHeldSend(h!)
        weak var released = h!.session
        h!.session.stop(.userEnd)
        h = nil
        await drainActorTurn()
        XCTAssertNil(released, "A stalled adapter must not retain the ended coordinator")
        XCTAssertEqual(clock.pendingCount, 0)
        let completed = expectation(description: "Release noncooperative transport continuation")
        transport.onCompletion = { completed.fulfill() }
        transport.releaseAll()
        await fulfillment(of: [completed], timeout: 2)
        transport.onCompletion = nil
        XCTAssertEqual(transport.activeSends, 0)
        XCTAssertEqual(transport.sentFrames.count, 1)
    }

    func testEndReleasesCoordinatorWhileConversationStartIsHeld() async {
        let transport = ControlledConversation()
        transport.holdStart = true
        var h: AdapterCompatibilityHarness? = AdapterCompatibilityHarness(conversation: transport)
        let clock = h!.clock
        defer { transport.releaseAll() }
        let starting = expectation(description: "Conversation startup entered noncooperative gate")
        transport.onStart = { starting.fulfill() }
        h!.session.start()
        await fulfillment(of: [starting], timeout: 2)
        transport.onStart = nil
        XCTAssertEqual(transport.pendingStartCount, 1)
        weak var released = h!.session
        h!.session.stop(.userEnd)
        h = nil
        await drainActorTurn()
        XCTAssertNil(released)
        XCTAssertEqual(clock.pendingCount, 0)
        transport.releaseAll()
        await drainActorTurn()
        XCTAssertEqual(transport.pendingStartCount, 0)
        XCTAssertEqual(clock.pendingCount, 0)
    }

    func testCancelledStartupBlocksNewStartsUntilAdapterReturns() async {
        let transport = ControlledConversation()
        transport.holdStart = true
        let h = AdapterCompatibilityHarness(conversation: transport)
        defer { h.session.stop(.userEnd); transport.releaseAll() }
        let entered = expectation(description: "Initial startup is held")
        transport.onStart = { entered.fulfill() }
        h.session.start()
        await fulfillment(of: [entered], timeout: 2)
        transport.onStart = nil
        h.session.stop(.userEnd)
        for _ in 0..<20 {
            h.session.start()
            await drainActorTurn()
            h.session.stop(.userEnd)
        }
        XCTAssertEqual(transport.pendingStartCount, 1)
        XCTAssertEqual(transport.startedContexts.count, 1)
        XCTAssertEqual(h.session.phase, .ended)
        transport.releaseAll()
        await drainActorTurn()
        XCTAssertEqual(h.session.phase, .ended, "Late startup cannot revive the ended session")
        transport.holdStart = false
        await start(h, retryWhilePreviousAttemptStops: true)
        XCTAssertEqual(transport.startedContexts.count, 2)
    }

    func testReconnectTimeoutDoesNotOverlapNoncooperativeStart() async {
        let h = AdapterCompatibilityHarness()
        defer { h.session.stop(.userEnd); h.conversation.releaseAll() }
        await start(h)
        h.conversation.holdStart = true
        h.conversation.emit(.disconnected)
        let entered = expectation(description: "Reconnect startup is held")
        h.conversation.onStart = { entered.fulfill() }
        h.clock.advance(250)
        await fulfillment(of: [entered], timeout: 2)
        h.conversation.onStart = nil
        for _ in 0..<10 {
            h.clock.advance(10_000)
            await drainActorTurn()
        }
        XCTAssertEqual(h.conversation.pendingStartCount, 1)
        XCTAssertEqual(h.conversation.startedContexts.count, 2)
        XCTAssertEqual(h.session.phase, .paused)
        XCTAssertFalse(h.session.capabilities.conversation.usable)
        h.session.stop(.userEnd)
        h.session.start()
        await drainActorTurn()
        XCTAssertEqual(h.session.phase, .ended)
        XCTAssertEqual(h.conversation.pendingStartCount, 1)
        h.conversation.releaseAll()
        await drainActorTurn()
        XCTAssertEqual(h.session.phase, .ended)
        h.conversation.holdStart = false
        await start(h, retryWhilePreviousAttemptStops: true)
        XCTAssertEqual(h.conversation.startedContexts.count, 3)
    }

    func testNoncooperativeTargetRequestsStayBoundedAcrossTaskReplacement() async {
        let clock = ManualAppClock(), evidence = ControlledTargetEvidence(clock: ManualAppClock())
        let h = AdapterCompatibilityHarness(clock: clock, evidenceAdapter: evidence)
        defer { h.session.stop(.userEnd); evidence.releaseAll(); h.conversation.releaseAll() }
        await start(h)
        for index in 0..<8 {
            let entered = expectation(description: "Target provider entered request \(index)")
            evidence.onResolve = { entered.fulfill() }
            h.session.submitTask(.init(id: "replacement-\(index)", kind: .button, query: "Blue button"))
            await fulfillment(of: [entered], timeout: 2)
            evidence.onResolve = nil
        }
        XCTAssertEqual(evidence.pendingCount, 8)
        h.session.submitTask(.init(id: "over-capacity", kind: .button, query: "Blue button"))
        await drainActorTurn()
        XCTAssertEqual(evidence.requests.count, 8)
        XCTAssertEqual(h.session.taskState?.result?.error, .providerTimeout)
        XCTAssertEqual(h.speech.successes, 0)
        let drained = expectation(description: "All cancelled target requests return")
        drained.expectedFulfillmentCount = 8
        evidence.onCompletion = { drained.fulfill() }
        evidence.releaseAll()
        await fulfillment(of: [drained], timeout: 2)
        evidence.onCompletion = nil
        XCTAssertEqual(h.speech.successes, 0)
        let entered = expectation(description: "Capacity is restored when cancelled providers finish")
        evidence.onResolve = { entered.fulfill() }
        h.session.submitTask(.init(id: "after-drain", kind: .button, query: "Blue button"))
        await fulfillment(of: [entered], timeout: 2)
        evidence.onResolve = nil
        XCTAssertEqual(evidence.requests.count, 9)
        XCTAssertEqual(evidence.pendingCount, 1)
    }

}
