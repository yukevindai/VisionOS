import XCTest
@testable import Tasks

final class TaskEngineTests: XCTestCase {
    struct Harness {
        var engine = TaskEngine(sessionID: "session")
        var now: Int64 = 100
        var count = 0
        @discardableResult mutating func send(_ response: TaskResponse, source: EventSource = .r1,
                                              context: RequestContext? = nil) -> [TaskEffect] {
            count += 1
            let request = context ?? engine.state.pendingRequest!
            return engine.reduce(.receive(TaskFixtures.envelope(context: request, eventID: "event-\(count)", nowMs: now, source: source), response), nowMs: now)
        }
        @discardableResult mutating func start(_ kind: TaskKind = .button) -> [TaskEffect] {
            engine.reduce(.start(TaskRequest(id: "task", kind: kind, query: "SIMULATED request")), nowMs: now)
        }
        @discardableResult mutating func resolve(_ target: ResolvedTarget = TaskFixtures.target()) -> [TaskEffect] {
            send(.target(target), source: .r3)
        }
        @discardableResult mutating func acquire(_ evidence: PhysicalEvidence? = nil) -> [TaskEffect] {
            send(.physical(evidence ?? TaskFixtures.physical(targetID: engine.state.target!.id, nowMs: now)))
        }
        @discardableResult mutating func onTarget(_ mode: GuidanceMode = .hand) -> [TaskEffect] {
            let target = engine.state.target!.id
            return send(.guidance(mode: mode, targetID: target, status: .onTarget,
                                  evidence: TaskFixtures.physical(targetID: target, nowMs: now)))
        }
        @discardableResult mutating func confirm(_ answer: ConfirmationAnswer = .yes) -> [TaskEffect] {
            send(.confirmation(promptID: engine.state.confirmation!.promptID, answer: answer), source: .user)
        }
        mutating func toVerification(_ kind: TaskKind = .button, target: ResolvedTarget = TaskFixtures.target()) {
            start(kind); resolve(target); acquire(); onTarget()
        }
        func observation(_ kind: ObservationKind = .buttonIndicatorChanged,
                         provenance: VerificationProvenance = .r1Detector,
                         targetID: String? = nil, requestID: String? = nil) -> VerificationEvidence {
            VerificationEvidence(targetID: targetID ?? engine.state.target!.id,
                                 actionRequestID: requestID ?? engine.state.pendingRequest!.requestID,
                                 kind: kind, provenance: provenance,
                                 beforeFrameID: "before", afterFrameID: "after",
                                 beforeMs: 99, afterMs: now)
        }
    }

    func testButtonRequiresActionEvidenceAfterOnTarget() {
        var h = Harness(); h.toVerification()
        XCTAssertEqual(h.engine.state.phase, .verifying)
        XCTAssertNil(h.engine.state.result)
        let effects = h.send(.verification(.observed(h.observation())))
        XCTAssertEqual(h.engine.state.result?.verification, .observed)
        XCTAssertTrue(effects.contains { if case .successFeedback = $0 { return true }; return false })
    }

    func testUnimplementedVerifierAsksBoundQuestionAndMarksUserConfirmation() {
        var h = Harness(); h.toVerification()
        h.send(.verification(.unavailable))
        XCTAssertEqual(h.engine.state.confirmation?.purpose, .actionResult)
        h.confirm()
        XCTAssertEqual(h.engine.state.result?.verification, .userConfirmed)
    }

    func testModelCannotVerifyOrConfirm() {
        var h = Harness(); h.toVerification()
        XCTAssertEqual(h.send(.verification(.observed(h.observation())), source: .model), [.rejected(.wrongSource)])
        h.send(.verification(.observed(h.observation(provenance: .modelInterpretation))), source: .r3)
        XCTAssertEqual(h.engine.state.phase, .awaitingConfirmation)
        XCTAssertEqual(h.send(.confirmation(promptID: h.engine.state.confirmation!.promptID, answer: .yes), source: .model), [.rejected(.wrongSource)])
        XCTAssertNil(h.engine.state.result)
    }

    func testInvalidObservationCannotCompleteAction() {
        for kind in [ObservationKind.objectHandCoMotion, .screenTransition, .doorOpening] {
            var h = Harness(); h.toVerification()
            h.send(.verification(.observed(h.observation(kind))))
            XCTAssertEqual(h.engine.state.phase, .awaitingConfirmation)
        }
        for evidence in ["wrong-target", "wrong-action"] {
            var h = Harness(); h.toVerification()
            let observation = h.observation(targetID: evidence == "wrong-target" ? "other" : nil,
                                            requestID: evidence == "wrong-action" ? "other" : nil)
            h.send(.verification(.observed(observation)))
            XCTAssertNil(h.engine.state.result)
        }
    }

    func testThreeRetriesAfterInitialAttemptThenFailure() {
        var h = Harness(); h.start()
        for attempt in 0...3 {
            h.resolve(); h.acquire(); h.onTarget()
            h.send(.verification(.failed))
            if attempt < 3 {
                XCTAssertEqual(h.engine.state.retries, attempt + 1)
                XCTAssertEqual(h.engine.state.phase, .resolving)
                XCTAssertNil(h.engine.state.target)
            }
        }
        XCTAssertEqual(h.engine.state.result?.status, .failed)
        XCTAssertEqual(h.engine.state.result?.error, .actionUnconfirmed)
        XCTAssertEqual(h.engine.state.retries, 3)
    }

    func testStopInvalidatesPendingConfirmationAndOldRequest() {
        var h = Harness(); h.toVerification(); h.send(.verification(.unavailable))
        let prompt = h.engine.state.confirmation!
        let oldGeneration = h.engine.state.context.generation
        let effects = h.engine.reduce(.stop(.userPause), nowMs: h.now)
        XCTAssertEqual(h.engine.state.context.generation, oldGeneration + 1)
        XCTAssertNil(h.engine.state.confirmation); XCTAssertNil(h.engine.state.target)
        XCTAssertTrue(effects.contains { if case .allStop = $0 { return true }; return false })
        XCTAssertEqual(h.send(.confirmation(promptID: prompt.promptID, answer: .yes), source: .user, context: prompt.context), [.rejected(.wrongContext)])
        XCTAssertEqual(h.engine.reduce(.stop(.userPause), nowMs: h.now), [])
    }

    func testConfirmationWrongPromptCannotComplete() {
        var h = Harness(); h.toVerification(); h.send(.verification(.unavailable))
        XCTAssertEqual(h.send(.confirmation(promptID: "old-conversation", answer: .yes), source: .user), [.rejected(.wrongRequest)])
        XCTAssertNil(h.engine.state.result)
    }

    func testStopCancelsGuidanceDespiteClockRegressionWithoutRewindingClock() {
        for invalidTime: Int64 in [99, -1] {
            var h = Harness(); h.start(); h.resolve(); h.acquire()
            let pending = h.engine.state.pendingRequest!
            let effects = h.engine.reduce(.stop(.userPause), nowMs: invalidTime)
            XCTAssertEqual(h.engine.state.phase, .paused)
            XCTAssertNil(h.engine.state.pendingRequest)
            XCTAssertEqual(h.engine.state.context.generation, pending.task.generation + 1)
            XCTAssertTrue(effects.contains(.cancelWork(pending.task)))
            XCTAssertTrue(effects.contains(.allStop(h.engine.state.context)))
            XCTAssertFalse(h.engine.isGuidanceDispatchAllowed(context: pending,
                           evidence: TaskFixtures.physical(nowMs: h.now), nowMs: h.now))
            XCTAssertEqual(h.engine.reduce(.resume, nowMs: 99), [.rejected(.stale)])
            XCTAssertEqual(h.send(.guidance(mode: .hand, targetID: "simulated-button", status: .onTarget,
                                          evidence: TaskFixtures.physical(nowMs: h.now)), context: pending), [.rejected(.wrongContext)])
        }
    }

    func testDuplicateAndRequestCorrelation() {
        var h = Harness(); h.start(); h.resolve(); h.acquire()
        let pending = h.engine.state.pendingRequest!
        let envelope = TaskFixtures.envelope(context: pending, eventID: "same", nowMs: h.now)
        let event = TaskEvent.receive(envelope, .guidance(mode: .hand, targetID: "simulated-button", status: .guiding,
                                                       evidence: TaskFixtures.physical(nowMs: h.now)))
        XCTAssertEqual(h.engine.reduce(event, nowMs: h.now), [])
        XCTAssertEqual(h.engine.reduce(event, nowMs: h.now), [.rejected(.duplicate)])
        let wrong = RequestContext(task: pending.task, requestID: "wrong", issuedAtMs: pending.issuedAtMs, expiresAtMs: pending.expiresAtMs)
        XCTAssertEqual(h.send(.physical(TaskFixtures.physical(nowMs: h.now)), context: wrong), [.rejected(.wrongRequest)])
    }

    func testOutOfOrderGuidanceCannotRequestActionAfterNewerStatus() {
        var h = Harness(); h.start(); h.resolve(); h.acquire()
        let pending = h.engine.state.pendingRequest!
        h.now = 200
        h.send(.guidance(mode: .hand, targetID: "simulated-button", status: .guiding,
                         evidence: TaskFixtures.physical(nowMs: h.now)))
        h.now = 250
        let older = TaskFixtures.envelope(context: pending, eventID: "late-on-target", nowMs: 150)
        let effects = h.engine.reduce(.receive(older, .guidance(mode: .hand, targetID: "simulated-button",
                                      status: .onTarget, evidence: TaskFixtures.physical(nowMs: 150))), nowMs: h.now)
        XCTAssertEqual(effects, [.rejected(.stale)])
        XCTAssertEqual(h.engine.state.phase, .guiding)
        XCTAssertEqual(h.engine.state.pendingRequest, pending)
        XCTAssertNil(h.engine.state.result)

        // Distinct events in the same millisecond remain valid.
        let current = TaskFixtures.envelope(context: pending, eventID: "current-on-target", nowMs: 200)
        let accepted = h.engine.reduce(.receive(current, .guidance(mode: .hand, targetID: "simulated-button",
                                       status: .onTarget, evidence: TaskFixtures.physical(nowMs: 200))), nowMs: h.now)
        XCTAssertEqual(h.engine.state.phase, .verifying)
        XCTAssertTrue(accepted.contains { if case .requestVerification = $0 { return true }; return false })
    }

    func testRejectedGuidanceCannotConsumeValidEventOrAdvanceOrdering() {
        for source in [EventSource.model, .r1] {
            for reusesEventID in [false, true] {
                var h = Harness(); h.start(); h.resolve(); h.acquire()
                let pending = h.engine.state.pendingRequest!
                let beforeRejection = h.engine.state
                h.now = 250
                let rejected = TaskFixtures.envelope(context: pending, eventID: "guidance-status", nowMs: 250, source: source)
                let rejection = h.engine.reduce(.receive(rejected, .guidance(mode: .hand,
                    targetID: source == .r1 ? "other-target" : "simulated-button", status: .onTarget,
                    evidence: TaskFixtures.physical(nowMs: 250))), nowMs: h.now)
                XCTAssertEqual(rejection, [.rejected(source == .r1 ? .wrongTarget : .wrongSource)])
                XCTAssertEqual(h.engine.state, beforeRejection)

                let valid = TaskFixtures.envelope(context: pending,
                    eventID: reusesEventID ? "guidance-status" : "valid-guidance-status", nowMs: 200)
                let effects = h.engine.reduce(.receive(valid, .guidance(mode: .hand, targetID: "simulated-button",
                    status: .onTarget, evidence: TaskFixtures.physical(nowMs: 200))), nowMs: h.now)
                XCTAssertEqual(h.engine.state.phase, .verifying)
                XCTAssertTrue(effects.contains { if case .requestVerification = $0 { return true }; return false })
            }
        }
    }

    func testMissingGuidanceEvidenceStillStopsAndInvalidatesRequest() {
        var h = Harness(); h.start(); h.resolve(); h.acquire()
        let pending = h.engine.state.pendingRequest!
        let effects = h.send(.guidance(mode: .hand, targetID: "simulated-button", status: .onTarget, evidence: nil))
        XCTAssertEqual(h.engine.state.phase, .paused)
        XCTAssertNil(h.engine.state.pendingRequest)
        XCTAssertEqual(h.engine.state.context.generation, pending.task.generation + 1)
        XCTAssertTrue(effects.contains(.rejected(.missingEvidence)))
        XCTAssertTrue(effects.contains(.cancelWork(pending.task)))
        XCTAssertTrue(effects.contains(.allStop(h.engine.state.context)))
        XCTAssertEqual(h.send(.guidance(mode: .hand, targetID: "simulated-button", status: .onTarget,
            evidence: TaskFixtures.physical(nowMs: h.now)), context: pending), [.rejected(.wrongContext)])
    }

    func testCrossSessionAndTaskResponsesRejected() {
        var h = Harness(); h.start()
        for context in [TaskContext(sessionID: "other", taskID: "task", generation: 1),
                        TaskContext(sessionID: "session", taskID: "other", generation: 1)] {
            let request = RequestContext(task: context, requestID: h.engine.state.pendingRequest!.requestID, issuedAtMs: h.now, expiresAtMs: 5_100)
            XCTAssertEqual(h.send(.target(TaskFixtures.target()), source: .r3, context: request), [.rejected(.wrongContext)])
        }
    }

    func testExpiredFutureAndMalformedResponsesRejected() {
        var h = Harness(); h.start()
        let pending = h.engine.state.pendingRequest!
        for envelope in [EventEnvelope(schemaVersion: 4, eventID: "schema", context: pending, monotonicMs: 100, expiresAtMs: 200, source: .r3),
                         EventEnvelope(eventID: "future", context: pending, monotonicMs: 101, expiresAtMs: 200, source: .r3),
                         EventEnvelope(eventID: "expired", context: pending, monotonicMs: 99, expiresAtMs: 100, source: .r3)] {
            let effects = h.engine.reduce(.receive(envelope, .target(TaskFixtures.target())), nowMs: h.now)
            XCTAssertTrue(effects.contains(.rejected(.stale)) || effects.contains(.rejected(.malformed)))
        }
    }

    func testHistoricalTargetAlwaysReacquiredAndDispatchFreshnessRechecked() {
        var h = Harness(); h.start()
        let effects = h.resolve()
        XCTAssertTrue(effects.contains { if case .reacquire = $0 { return true }; return false })
        XCTAssertFalse(effects.contains { if case .requestGuidance = $0 { return true }; return false })
        h.acquire()
        let pending = h.engine.state.pendingRequest!
        let evidence = TaskFixtures.physical(nowMs: h.now)
        XCTAssertTrue(h.engine.isGuidanceDispatchAllowed(context: pending, evidence: evidence, nowMs: 400))
        XCTAssertFalse(h.engine.isGuidanceDispatchAllowed(context: pending, evidence: evidence, nowMs: 401))
        _ = h.engine.reduce(.stop(.userPause), nowMs: 401)
        XCTAssertFalse(h.engine.isGuidanceDispatchAllowed(context: pending, evidence: evidence, nowMs: 401))
    }

    func testStaleAndWrongTargetPhysicalEvidenceStop() {
        for evidence in [TaskFixtures.physical(nowMs: 0), TaskFixtures.physical(targetID: "other", nowMs: 500)] {
            var h = Harness(); h.now = 500; h.start(); h.resolve(); h.acquire(evidence)
            XCTAssertEqual(h.engine.state.phase, .paused)
        }
    }

    func testDispatchClockRegressionCannotMakeOldEvidenceFresh() {
        var h = Harness(); h.start(); h.resolve(); h.acquire()
        let pending = h.engine.state.pendingRequest!
        h.now = 1_000
        h.send(.guidance(mode: .hand, targetID: "simulated-button", status: .guiding,
                         evidence: TaskFixtures.physical(nowMs: h.now)))
        XCTAssertEqual(h.engine.state.phase, .guiding)
        XCTAssertFalse(h.engine.isGuidanceDispatchAllowed(context: pending,
                       evidence: TaskFixtures.physical(nowMs: 300), nowMs: 500))
        XCTAssertTrue(h.engine.isGuidanceDispatchAllowed(context: pending,
                      evidence: TaskFixtures.physical(nowMs: h.now), nowMs: h.now))
    }

    func testTrackingRecoveryRequiresFreshReacquisitionAndExplicitConfirmation() {
        var h = Harness(); h.start(); h.resolve(); h.acquire()
        _ = h.engine.reduce(.stop(.trackingLost), nowMs: h.now)
        _ = h.engine.reduce(.resume, nowMs: h.now)
        h.resolve(); h.acquire()
        XCTAssertEqual(h.engine.state.confirmation?.purpose, .resumeGuidance)
        h.confirm()
        XCTAssertEqual(h.engine.state.phase, .reacquiring)
        h.acquire()
        XCTAssertEqual(h.engine.state.phase, .guiding)
    }

    func testHazardAndLossPreemptGuidance() {
        for evidence in [TaskFixtures.physical(nowMs: 100, hazardPresent: true),
                         TaskFixtures.physical(nowMs: 100, handVisible: false),
                         TaskFixtures.physical(nowMs: 100, targetVisible: false),
                         TaskFixtures.physical(nowMs: 100, suspectedStep: true)] {
            var h = Harness(); h.start(); h.resolve(); h.acquire()
            let effects = h.send(.guidance(mode: .hand, targetID: "simulated-button", status: .onTarget, evidence: evidence))
            XCTAssertEqual(h.engine.state.phase, .paused)
            XCTAssertTrue(effects.contains { if case .allStop = $0 { return true }; return false })
            XCTAssertFalse(effects.contains { if case .requestVerification = $0 { return true }; return false })
        }
    }

    func testUnsafeTargetsNeverGuide() {
        for target in [TaskFixtures.target(label: ""), TaskFixtures.target(safety: .ambiguous),
                       TaskFixtures.target(safety: .emergency), TaskFixtures.target(label: "Confirm purchase"),
                       ResolvedTarget(id: "bad", kind: .button, label: "Up", region: .init(x: .nan, y: 0, width: 0.1, height: 0.1), frameID: "f")] {
            var h = Harness(); h.start(); let effects = h.resolve(target)
            XCTAssertTrue([TaskPhase.failed, .completed].contains(h.engine.state.phase))
            XCTAssertFalse(effects.contains { if case .requestGuidance = $0 { return true }; return false })
        }
    }

    func testApproachModeCorrelationAndFreshHandAcquisition() {
        var h = Harness(); h.start(); h.resolve(TaskFixtures.target(requiresApproach: true)); h.acquire()
        XCTAssertEqual(h.engine.state.phase, .approaching)
        XCTAssertEqual(h.onTarget(.hand), [.rejected(.unsupportedState)])
        h.onTarget(.approach)
        XCTAssertEqual(h.engine.state.phase, .reacquiring)
        h.acquire()
        XCTAssertEqual(h.engine.state.phase, .guiding)
    }

    func testApproachRequiresKnownDepthDistanceAndFreeSpace() {
        for evidence in [TaskFixtures.physical(nowMs: 100, depthUsable: false),
                         TaskFixtures.physical(nowMs: 100, distanceM: nil),
                         TaskFixtures.physical(nowMs: 100, distanceM: 5.01),
                         TaskFixtures.physical(nowMs: 100, distanceM: .nan),
                         TaskFixtures.physical(nowMs: 100, suspectedStep: true)] {
            var h = Harness(); h.start(.approach); h.resolve(); h.acquire(evidence)
            XCTAssertEqual(h.engine.state.phase, .paused)
        }
        var h = Harness(); h.start(.approach); h.resolve(); h.acquire(); h.onTarget(.approach)
        XCTAssertEqual(h.engine.state.result?.verification, .observed)
    }

    func testUnknownDepthAllowsOnlyHandAlignmentNotApproach() {
        var h = Harness(); h.start(); h.resolve()
        let effects = h.acquire(TaskFixtures.physical(nowMs: 100, depthUsable: false))
        XCTAssertEqual(h.engine.state.phase, .guiding)
        XCTAssertTrue(effects.contains { if case .speak(let text, _) = $0 { return text.contains("image plane") }; return false })
        let unknown = PhysicalEvidence(targetID: "simulated-button", frameID: "f", observedAtMs: 100)
        XCTAssertFalse(h.engine.isGuidanceDispatchAllowed(context: h.engine.state.pendingRequest!, evidence: unknown, nowMs: 100))
    }

    func testObjectCoMotionAndOcclusionDoesNotCountAsGrasp() {
        var h = Harness(); h.toVerification(.object, target: TaskFixtures.target(kind: .object))
        h.send(.verification(.observed(h.observation(.objectHandCoMotion))))
        XCTAssertEqual(h.engine.state.result?.verification, .observed)
        var lost = Harness(); lost.start(.object); lost.resolve(TaskFixtures.target(kind: .object)); lost.acquire()
        lost.send(.guidance(mode: .hand, targetID: "simulated-button", status: .lost, evidence: nil))
        XCTAssertEqual(lost.engine.state.phase, .paused); XCTAssertNil(lost.engine.state.result)
    }

    func testDoorOpeningRequiresR1PassageAuthorizationEvenAfterUserConfirmation() {
        var h = Harness()
        h.toVerification(.door, target: TaskFixtures.target(kind: .handle, doorAction: .lever, doorActionEvidence: true))
        h.send(.verification(.unavailable)); h.confirm()
        XCTAssertEqual(h.engine.state.phase, .awaitingPassage); XCTAssertNil(h.engine.state.result)
        XCTAssertEqual(h.send(.passageAuthorized(TaskFixtures.physical(nowMs: 100)), source: .r3), [.rejected(.wrongSource)])
        let effects = h.send(.passageAuthorized(TaskFixtures.physical(nowMs: 100)))
        XCTAssertEqual(h.engine.state.result?.verification, .userConfirmed)
        XCTAssertFalse(effects.contains { if case .speak(let text, _) = $0 { return text.lowercased().contains("walk through") }; return false })
    }

    func testDoorUnknownActionAsksAndReacquires() {
        var h = Harness(); h.start(.door)
        h.resolve(TaskFixtures.target(kind: .handle, doorAction: .push, doorActionEvidence: false))
        XCTAssertEqual(h.engine.state.confirmation?.purpose, .doorAction)
        XCTAssertEqual(h.confirm(.yes), [.rejected(.malformed)])
        h.confirm(.doorAction(.pull)); XCTAssertEqual(h.engine.state.phase, .reacquiring)
        XCTAssertEqual(h.engine.state.selectedDoorAction, .pull)
    }

    func testAutomaticDoorCompletesRequiredApproachBeforeVerifyingWithoutHandGuidance() {
        var h = Harness(); h.start(.door)
        h.resolve(TaskFixtures.target(kind: .door, requiresApproach: true, doorAction: .automatic))
        let approach = h.acquire(TaskFixtures.physical(nowMs: h.now, handVisible: false))
        XCTAssertEqual(h.engine.state.phase, .approaching)
        XCTAssertTrue(approach.contains { if case .requestGuidance(_, .approach, _, _) = $0 { return true }; return false })
        XCTAssertFalse(approach.contains { if case .requestVerification = $0 { return true }; return false })
        h.onTarget(.approach)
        let opening = h.acquire(TaskFixtures.physical(nowMs: h.now, handVisible: false))
        XCTAssertEqual(h.engine.state.phase, .verifying)
        XCTAssertTrue(opening.contains { if case .requestVerification(_, .openDoor, _) = $0 { return true }; return false })
        XCTAssertFalse(opening.contains { if case .requestGuidance = $0 { return true }; return false })
        h.send(.verification(.observed(h.observation(.doorOpening))))
        XCTAssertEqual(h.engine.state.phase, .awaitingPassage)
        XCTAssertNil(h.engine.state.result)
        h.send(.passageAuthorized(TaskFixtures.physical(nowMs: h.now, handVisible: false)))
        XCTAssertEqual(h.engine.state.result?.verification, .observed)
    }

    func testAutomaticDoorStillRequiresFreshSafeTargetEvidence() {
        for evidence in [TaskFixtures.physical(nowMs: 0, handVisible: false),
                         TaskFixtures.physical(nowMs: 500, handVisible: false, targetVisible: false),
                         TaskFixtures.physical(nowMs: 500, handVisible: false, hazardPresent: true)] {
            var h = Harness(); h.now = 500; h.start(.door)
            h.resolve(TaskFixtures.target(kind: .door, doorAction: .automatic))
            let effects = h.acquire(evidence)
            XCTAssertEqual(h.engine.state.phase, .paused)
            XCTAssertTrue(effects.contains { if case .allStop = $0 { return true }; return false })
            XCTAssertFalse(effects.contains { if case .requestVerification = $0 { return true }; return false })
        }
    }

    func testDoorBlockedAndUnknownPathPauseWithoutRetry() {
        var h = Harness(); h.toVerification(.door, target: TaskFixtures.target(kind: .handle, doorAction: .lever))
        h.send(.verification(.doorBlocked))
        XCTAssertEqual(h.engine.state.phase, .paused); XCTAssertEqual(h.engine.state.retries, 0)
        var path = Harness(); path.toVerification(.door, target: TaskFixtures.target(kind: .handle, doorAction: .lever))
        path.send(.verification(.observed(path.observation(.doorOpening))))
        path.send(.passageAuthorized(TaskFixtures.physical(nowMs: 100, depthUsable: false)))
        XCTAssertEqual(path.engine.state.phase, .paused); XCTAssertNil(path.engine.state.result)
    }

    func testTimeoutNeverSucceedsAndClearsConfirmation() {
        var h = Harness(); h.toVerification()
        h.now = h.engine.state.pendingRequest!.expiresAtMs
        _ = h.engine.reduce(.tick, nowMs: h.now)
        XCTAssertEqual(h.engine.state.phase, .awaitingConfirmation)
        h.now = h.engine.state.pendingRequest!.expiresAtMs
        _ = h.engine.reduce(.tick, nowMs: h.now)
        XCTAssertEqual(h.engine.state.result?.status, .failed)
        XCTAssertEqual(h.engine.state.result?.verification, .unverified)
        XCTAssertNil(h.engine.state.confirmation)
    }

    func testReplacementStopsBeforeResolvingAndRejectsLateWork() {
        var h = Harness(); h.start(); let old = h.engine.state.pendingRequest!
        let effects = h.engine.reduce(.start(TaskRequest(id: "next", kind: .object, query: "Cup")), nowMs: h.now)
        XCTAssertEqual(effects.count, 3)
        if case .cancelWork = effects[0] {} else { XCTFail("Cancel must come first") }
        if case .allStop = effects[1] {} else { XCTFail("Stop must precede next task") }
        if case .resolveTarget = effects[2] {} else { XCTFail("Resolve must follow stop") }
        XCTAssertEqual(h.send(.target(TaskFixtures.target()), source: .r3, context: old), [.rejected(.wrongContext)])
    }

    func testGenerationExhaustionFailsClosedAndIdleStopWorks() {
        var engine = TaskEngine(sessionID: "s", initialGeneration: UInt64.max)
        XCTAssertEqual(engine.reduce(.start(TaskRequest(id: "t", kind: .button, query: "Up")), nowMs: 0), [.rejected(.invalidRequest)])
        XCTAssertTrue(engine.reduce(.stop(.ended), nowMs: 0).contains { if case .allStop = $0 { return true }; return false })
        var idle = TaskEngine(sessionID: "s", initialGeneration: 7)
        _ = idle.reduce(.stop(.userPause), nowMs: 0)
        XCTAssertEqual(idle.state.context.generation, 8)
    }
}
