import XCTest
@testable import HapticLink

private final class TestClock: HapticClock {
    var now: TimeInterval = 100
    func advance(_ value: TimeInterval) { now += value }
}

@MainActor private final class TestTransport: BandTransport {
    var onEvent: ((BandTransportEvent) -> Void)?
    var isSimulated = true
    var connected: Set<UUID> = []
    var blocked: Set<UUID> = []
    var failedWrites: Set<UUID> = []
    var writes: [(UUID, Data)] = []
    var afterWrite: ((Data) -> Void)?
    var isShutdown = false
    var connectionRequests: [UUID] = []
    func startDiscovery() { isShutdown = false; onEvent?(.availability(.poweredOn)) }
    func stopDiscovery() {}
    func connect(_ peripheralID: UUID) { connected.insert(peripheralID); connectionRequests.append(peripheralID) }
    func disconnect(_ peripheralID: UUID) {
        connected.remove(peripheralID); onEvent?(.state(peripheralID, .disconnected))
    }
    func canWrite(_ peripheralID: UUID) -> Bool { connected.contains(peripheralID) && !blocked.contains(peripheralID) }
    func write(_ data: Data, to peripheralID: UUID) -> Bool {
        guard canWrite(peripheralID), !failedWrites.contains(peripheralID) else { return false }
        writes.append((peripheralID, data)); afterWrite?(data); return true
    }
    func shutdown() { isShutdown = true; onEvent = nil; connected.removeAll() }
    func commands() throws -> [(UUID, HapticCommand)] {
        try writes.map { ($0.0, try HapticCommand(data: $0.1, expectedVersion: 1)) }
    }
}

@MainActor private struct Harness {
    let left = UUID(), right = UUID()
    let clock = TestClock()
    let transport = TestTransport()
    let controller: HapticLinkController
    let contract: H1Contract
    let context: HapticContext
    let store: MemoryPairingMetadataStore

    init(nextSequence: UInt16 = 0, synchronized: Bool = true, calibrated: Bool = true, generation: UInt64 = 1, simulated: Bool = true) throws {
        var patterns: [UInt8: PatternParameters] = [:]
        for id: UInt8 in 1...9 {
            patterns[id] = PatternParameters(patternID: id, intensity: 80, rateHz: 5, ttlMs: id == 6 ? 600 : 1000)
        }
        contract = try H1Contract(serviceUUID: UUID(), commandUUID: UUID(), statusUUID: UUID(), protocolVersion: 1,
            supportedFirmwareVersions: [1, 2], stop: PatternParameters(patternID: 0, intensity: 0, rateHz: 0, ttlMs: 0),
            heartbeat: PatternParameters(patternID: 255, intensity: 0, rateHz: 0, ttlMs: 0), patterns: patterns,
            identityDescription: "SIMULATED fixture identity", generationPolicyDescription: "SIMULATED fixture handshake",
            supportsStoppedNotifications: true)
        context = try HapticContext(sessionID: "s", taskID: "t", generation: generation)
        transport.isSimulated = simulated
        store = MemoryPairingMetadataStore(metadata: [
            PairingMetadata(peripheralID: left, side: .left, calibratedFirmwareVersion: 1, calibrationCompleted: calibrated),
            PairingMetadata(peripheralID: right, side: .right, calibratedFirmwareVersion: 1, calibrationCompleted: calibrated)
        ])
        controller = try HapticLinkController(configuration: .confirmed(contract), transport: transport, clock: clock, metadataStore: store)
        transport.connected = [left, right]
        transport.onEvent?(.availability(.poweredOn))
        for id in [left, right] {
            transport.onEvent?(.state(id, .ready)); transport.onEvent?(.status(id, Data([80, 1, 0, 0, 0])))
        }
        if synchronized {
            try controller.acceptSynchronization(GenerationSynchronizationProof(source: .simulatedFixture, challengeID: controller.synchronizationChallengeID,
                peripheralIDs: [.left: left, .right: right], generation: context.generation,
                nextSequences: [.left: nextSequence, .right: nextSequence]))
            try controller.beginGeneration(context)
        }
    }

    func release(pattern: UInt8 = 1, sides: [BandSide] = [.left], kind: HapticReleaseKind = .movement,
                 evidenceAge: TimeInterval = 0, expiration: TimeInterval = 0.3,
                 context: HapticContext? = nil, id: UUID = UUID()) -> ScheduledHapticRelease {
        ScheduledHapticRelease(id: id, context: context ?? self.context, kind: kind,
            evidenceTime: clock.now - evidenceAge, releasedAt: clock.now, expiresAt: clock.now + expiration,
            commands: Dictionary(uniqueKeysWithValues: sides.map { ($0, contract.patterns[pattern]!) }))
    }
}

final class SafetyTests: XCTestCase {
    @MainActor func testMissingH1ConfigNeverDiscoversOrWritesAndStopClearsLocally() async throws {
        let transport = TestTransport()
        let controller = try HapticLinkController(configuration: .unavailableH1, transport: transport,
                                                   metadataStore: MemoryPairingMetadataStore())
        controller.start()
        XCTAssertEqual(controller.allStop(reason: "end").sides[.left], .unavailable)
        XCTAssertTrue(transport.writes.isEmpty)
        XCTAssertEqual(controller.pendingCommandCount, 0)
        XCTAssertFalse(controller.isSynchronized)
    }

    @MainActor func testConnectedDoesNotMeanGenerationSynchronizedOrAllowOutput() async throws {
        let h = try Harness(synchronized: false)
        XCTAssertThrowsError(try h.controller.beginGeneration(h.context))
        XCTAssertEqual(h.controller.submit(h.release()), .rejected(.generationNotSynchronized))
        XCTAssertTrue(h.transport.writes.isEmpty)
    }

    @MainActor func testDispatchBoundsMovementTTLToEvidenceAndReleaseExpiry() async throws {
        let h = try Harness()
        XCTAssertEqual(h.controller.submit(h.release(evidenceAge: 0.2)), .sentToTransport)
        let packet = try XCTUnwrap(h.transport.commands().first?.1)
        XCTAssertGreaterThan(packet.ttlMs, 0); XCTAssertLessThanOrEqual(packet.ttlMs, 100)
        XCTAssertEqual(packet.generation, 1)
        h.clock.advance(0.101); h.controller.tick()
        XCTAssertFalse(h.controller.hasEligibleOutput)
        XCTAssertEqual(try h.transport.commands().last?.1.patternID, 0)
    }

    @MainActor func testBackpressureReplacesLatestAndExpiresAtDispatch() async throws {
        let h = try Harness()
        h.transport.blocked = [h.left]
        XCTAssertEqual(h.controller.submit(h.release(pattern: 1)), .queued)
        let newer = h.release(pattern: 3)
        XCTAssertEqual(h.controller.submit(newer), .queued)
        XCTAssertEqual(h.controller.pendingCommandCount, 1)
        h.clock.advance(0.301); h.transport.blocked.removeAll()
        h.transport.onEvent?(.writeReady(h.left))
        XCTAssertTrue(h.transport.writes.isEmpty)
        XCTAssertEqual(h.controller.pendingCommandCount, 0)
        XCTAssertEqual(h.controller.submit(h.release(pattern: 4)), .sentToTransport)
        XCTAssertEqual(try h.transport.commands().map { $0.1.patternID }, [4])
    }

    @MainActor func testAllStopPreemptsBacklogAndRepeatedStopIsIdempotent() async throws {
        let h = try Harness()
        h.transport.blocked = [h.left, h.right]
        XCTAssertEqual(h.controller.submit(h.release(sides: [.left, .right])), .queued)
        let report = h.controller.allStop(reason: "hazard")
        XCTAssertEqual(report.sides[.left], .pendingBackpressure)
        XCTAssertEqual(h.controller.pendingCommandCount, 2)
        h.transport.blocked.removeAll(); h.transport.onEvent?(.writeReady(h.left))
        XCTAssertEqual(try h.transport.commands().map { $0.1.patternID }, [0, 0])
        let count = h.transport.writes.count
        _ = h.controller.allStop(reason: "hazard")
        XCTAssertEqual(h.transport.writes.count, count)
        XCTAssertEqual(h.controller.submit(h.release()), .rejected(.generationNotSynchronized))
    }

    @MainActor func testHeartbeatOnlyWhileEligibleAndCannotReviveExpiredMovement() async throws {
        let h = try Harness()
        _ = h.controller.submit(h.release())
        h.clock.advance(0.201); h.controller.tick()
        XCTAssertEqual(try h.transport.commands().map { $0.1.patternID }, [1, 255])
        h.clock.advance(0.101); h.controller.tick()
        XCTAssertEqual(try h.transport.commands().last?.1.patternID, 0)
        let count = h.transport.writes.count
        h.clock.advance(50); h.controller.tick()
        XCTAssertEqual(h.transport.writes.count, count)
    }

    @MainActor func testFiniteActionReadyKeepsFullSixHundredMillisecondsWithoutExtendingMovement() async throws {
        let h = try Harness()
        _ = h.controller.submit(h.release(pattern: 6, sides: [.left, .right], kind: .finiteNotification))
        XCTAssertEqual(try h.transport.commands().first?.1.ttlMs, 600)
        h.clock.advance(0.4); h.controller.tick()
        XCTAssertTrue(h.controller.hasEligibleOutput)
        XCTAssertEqual(try h.transport.commands().last?.1.patternID, 255)
        h.clock.advance(0.201); h.controller.tick()
        XCTAssertFalse(h.controller.hasEligibleOutput)
        XCTAssertEqual(try h.transport.commands().last?.1.patternID, 0)
    }

    @MainActor func testEitherBandDisconnectStopsRemainingBandAndRequiresNewProof() async throws {
        let h = try Harness()
        var pauseCount = 0; h.controller.onSafetyPause = { _ in pauseCount += 1 }
        _ = h.controller.submit(h.release(pattern: 3, sides: [.left, .right]))
        h.transport.disconnect(h.right)
        XCTAssertEqual(try h.transport.commands().last?.0, h.left)
        XCTAssertEqual(try h.transport.commands().last?.1.patternID, 0)
        XCTAssertFalse(h.controller.isSynchronized); XCTAssertNil(h.controller.context)
        XCTAssertEqual(pauseCount, 1)
        h.transport.connected.insert(h.right); h.transport.onEvent?(.state(h.right, .ready))
        h.transport.onEvent?(.status(h.right, Data([80, 1, 0, 0, 0])))
        h.controller.tick()
        XCTAssertEqual(h.controller.submit(h.release()), .rejected(.generationNotSynchronized))
    }

    @MainActor func testDirectionChangeStopsNonparticipatingBandBeforeNewSide() async throws {
        let h = try Harness()
        _ = h.controller.submit(h.release(pattern: 1))
        _ = h.controller.submit(h.release(pattern: 2, sides: [.right]))
        let commands = try h.transport.commands()
        XCTAssertEqual(commands.map { $0.1.patternID }, [1, 0, 2])
        XCTAssertEqual(commands[1].0, h.left); XCTAssertEqual(commands[2].0, h.right)
    }

    @MainActor func testBilateralBackpressureSendsNeitherHalfUntilBothWritable() async throws {
        let h = try Harness()
        h.transport.blocked = [h.right]
        XCTAssertEqual(h.controller.submit(h.release(pattern: 3, sides: [.left, .right])), .queued)
        XCTAssertTrue(h.transport.writes.isEmpty)
        h.transport.blocked.removeAll(); h.transport.onEvent?(.writeReady(h.right))
        XCTAssertEqual(try h.transport.commands().map { $0.1.patternID }, [3, 3])
    }

    @MainActor func testSequenceExhaustionReservesFinalValueForStopAndNeverWraps() async throws {
        let h = try Harness(nextSequence: UInt16.max - 1)
        XCTAssertEqual(h.controller.submit(h.release()), .sentToTransport)
        XCTAssertEqual(h.controller.submit(h.release()), .rejected(.sequenceExhausted(.left)))
        _ = h.controller.allStop(reason: "sequence_exhaustion")
        let left = try h.transport.commands().filter { $0.0 == h.left }.map(\.1)
        XCTAssertEqual(left.map(\.sequence), [UInt16.max - 1, UInt16.max])
        XCTAssertEqual(left.map(\.patternID), [1, 0])
        _ = h.controller.allStop(reason: "again")
        XCTAssertEqual(try h.transport.commands().filter { $0.0 == h.left }.count, 2)
    }

    @MainActor func testGenerationExhaustionAndRollbackCannotResume() async throws {
        let h = try Harness()
        _ = h.controller.allStop(reason: "pause")
        XCTAssertThrowsError(try h.controller.beginGeneration(h.context))
        let next = try HapticContext(sessionID: "s", taskID: "t", generation: 2)
        try h.controller.beginGeneration(next)
        _ = h.controller.allStop(reason: "pause")
        XCTAssertThrowsError(try h.controller.beginGeneration(try HapticContext(sessionID: "s", taskID: "t", generation: UInt64(UInt16.max))))
        XCTAssertThrowsError(try h.controller.acceptSynchronization(GenerationSynchronizationProof(source: .simulatedFixture, challengeID: h.controller.synchronizationChallengeID,
            peripheralIDs: [.left: h.left, .right: h.right], generation: 1, nextSequences: [.left: 0, .right: 0])))
    }

    @MainActor func testStaleWrongGenerationWrongTaskAndDuplicateReleasesNeverWrite() async throws {
        let h = try Harness()
        XCTAssertNotEqual(h.controller.submit(h.release(evidenceAge: 0.301)), .sentToTransport)
        let wrongTask = try HapticContext(sessionID: "s", taskID: "other", generation: 1)
        XCTAssertEqual(h.controller.submit(h.release(context: wrongTask)), .rejected(.generationNotSynchronized))
        let wrongGeneration = try HapticContext(sessionID: "s", taskID: "t", generation: 2)
        XCTAssertEqual(h.controller.submit(h.release(context: wrongGeneration)), .rejected(.generationNotSynchronized))
        XCTAssertTrue(h.transport.writes.isEmpty)
        let release = h.release(); _ = h.controller.submit(release)
        XCTAssertNotEqual(h.controller.submit(release), .sentToTransport)
        XCTAssertEqual(h.transport.writes.count, 1)
    }

    @MainActor func testMalformedStatusAndAnyUnmappedFirmwareFaultFailClosed() async throws {
        for data in [Data([80, 1]), Data([80, 1, 0, 0, 128])] {
            let h = try Harness()
            _ = h.controller.submit(h.release())
            h.transport.onEvent?(.status(h.left, data))
            XCTAssertFalse(h.controller.isSynchronized)
            XCTAssertFalse(h.controller.hasEligibleOutput)
            XCTAssertEqual(try h.transport.commands().last?.1.patternID, 0)
        }
    }

    @MainActor func testPowerAndAuthorizationLossClearGuidanceWithoutReplay() async throws {
        for state: BluetoothAvailability in [.unauthorized, .poweredOff, .resetting] {
            let h = try Harness(); _ = h.controller.submit(h.release())
            h.transport.onEvent?(.availability(state))
            XCTAssertFalse(h.controller.hasEligibleOutput); XCTAssertFalse(h.controller.isSynchronized)
            h.transport.onEvent?(.availability(.poweredOn)); h.controller.tick()
            XCTAssertEqual(h.controller.submit(h.release()), .rejected(.generationNotSynchronized))
        }
    }

    @MainActor func testPowerRecoveryRequiresFreshConnectionReadinessAndStatusBeforeProof() async throws {
        let h = try Harness()
        h.transport.onEvent?(.availability(.poweredOff))
        h.transport.onEvent?(.availability(.poweredOn))
        let proof = GenerationSynchronizationProof(source: .simulatedFixture,
            challengeID: h.controller.synchronizationChallengeID,
            peripheralIDs: [.left: h.left, .right: h.right], generation: 2,
            nextSequences: [.left: 10, .right: 10])
        XCTAssertThrowsError(try h.controller.acceptSynchronization(proof))
        for id in [h.left, h.right] { h.transport.onEvent?(.state(id, .ready)) }
        XCTAssertThrowsError(try h.controller.acceptSynchronization(proof))
        for id in [h.left, h.right] { h.transport.onEvent?(.status(id, Data([80, 1, 0, 0, 0]))) }
        XCTAssertNoThrow(try h.controller.acceptSynchronization(proof))
    }

    @MainActor func testSideAssignmentExplicitNoDuplicatesNoSilentSwapsAndRePairClearsCalibration() async throws {
        let h = try Harness()
        XCTAssertThrowsError(try h.controller.assign(h.left, to: .right, confirmedByUser: true))
        XCTAssertThrowsError(try h.controller.assign(UUID(), to: .left, confirmedByUser: true))
        XCTAssertThrowsError(try h.controller.assign(h.left, to: .left, confirmedByUser: false))
        try h.controller.forget(.left)
        let replacement = UUID(); try h.controller.assign(replacement, to: .left, confirmedByUser: true)
        XCTAssertEqual(h.controller.pairings[.left]?.peripheralID, replacement)
        XCTAssertEqual(h.controller.pairings[.left]?.calibrationCompleted, false)
    }

    @MainActor func testConfirmingExistingReadyPairingDoesNotRestartConnectionDuringOutput() async throws {
        let h = try Harness()
        XCTAssertEqual(h.controller.submit(h.release()), .sentToTransport)
        let requests = h.transport.connectionRequests
        let challenge = h.controller.synchronizationChallengeID
        try h.controller.assign(h.left, to: .left, confirmedByUser: true)
        XCTAssertEqual(h.transport.connectionRequests, requests)
        XCTAssertEqual(h.controller.synchronizationChallengeID, challenge)
        XCTAssertEqual(h.controller.context, h.context)
        XCTAssertTrue(h.controller.hasEligibleOutput)
    }

    @MainActor func testConfirmingExistingPairingRetriesOnlyAfterConnectionFailure() async throws {
        let h = try Harness()
        for state: BandLinkState in [.connecting, .discoveringServices, .subscribing, .reconnecting(attempt: 1)] {
            h.transport.onEvent?(.state(h.left, state))
            let requests = h.transport.connectionRequests
            try h.controller.assign(h.left, to: .left, confirmedByUser: true)
            XCTAssertEqual(h.transport.connectionRequests, requests, "Do not restart \(state)")
        }
        for state: BandLinkState in [.disconnected, .fault("Connection failed")] {
            h.transport.onEvent?(.state(h.left, state))
            let count = h.transport.connectionRequests.count
            try h.controller.assign(h.left, to: .left, confirmedByUser: true)
            XCTAssertEqual(h.transport.connectionRequests.count, count + 1)
            XCTAssertEqual(h.transport.connectionRequests.last, h.left)
        }
    }

    @MainActor func testNewProcessNeverLoadsGenerationAndFirmwareChangeInvalidatesCalibration() async throws {
        let h = try Harness()
        _ = h.controller.submit(h.release())
        h.transport.onEvent?(.status(h.left, Data([80, 2, 0, 0, 0])))
        XCTAssertFalse(h.controller.pairings[.left]!.calibrationCompleted)
        XCTAssertFalse(h.controller.isSynchronized)
        let fresh = try HapticLinkController(configuration: .confirmed(h.contract), transport: TestTransport(), clock: h.clock, metadataStore: h.store)
        XCTAssertFalse(fresh.isSynchronized); XCTAssertNil(fresh.context)
        XCTAssertThrowsError(try fresh.beginGeneration(h.context))
    }

    @MainActor func testCalibrationSuspendsPhysicalGuidanceAndConfirmationIsExplicit() async throws {
        let h = try Harness(calibrated: false)
        XCTAssertNotEqual(h.controller.submit(h.release()), .sentToTransport)
        _ = h.controller.allStop(reason: "start_calibration")
        let calibrationContext = try HapticContext(sessionID: "s", taskID: "calibration", generation: 2)
        try h.controller.beginGeneration(calibrationContext, calibration: true)
        XCTAssertEqual(h.controller.submit(h.release(kind: .calibration, context: calibrationContext)), .sentToTransport)
        XCTAssertNotEqual(h.controller.submit(h.release(context: calibrationContext)), .sentToTransport)
        XCTAssertThrowsError(try h.controller.confirmCalibration(for: .left, firmwareVersion: 1, confirmedByUser: false))
        try h.controller.confirmCalibration(for: .left, firmwareVersion: 1, confirmedByUser: true)
        XCTAssertEqual(h.controller.pairings[.left]?.calibrationCompleted, true)
    }

    @MainActor func testQueueAndDiagnosticsStayBoundedAndShutdownRemovesSubscription() async throws {
        let h = try Harness(); h.transport.blocked = [h.left]
        for _ in 0..<1000 { _ = h.controller.submit(h.release()) }
        XCTAssertEqual(h.controller.pendingCommandCount, 1)
        XCTAssertLessThanOrEqual(h.controller.diagnostics.count, 256)
        h.controller.shutdown()
        XCTAssertNil(h.transport.onEvent); XCTAssertTrue(h.transport.isShutdown)
        XCTAssertFalse(h.controller.hasEligibleOutput); XCTAssertEqual(h.controller.pendingCommandCount, 0)
    }

    @MainActor func testLiveTransportRejectsSimulatedSynchronizationProof() async throws {
        let h = try Harness(synchronized: false, simulated: false)
        XCTAssertThrowsError(try h.controller.acceptSynchronization(GenerationSynchronizationProof(source: .simulatedFixture, challengeID: h.controller.synchronizationChallengeID,
            peripheralIDs: [.left: h.left, .right: h.right], generation: 1, nextSequences: [.left: 0, .right: 0])))
    }

    @MainActor func testOutOfOrderReleasesCannotReplaceCurrentCueEvenWhenFresh() async throws {
        let h = try Harness()
        let old = h.release()
        h.clock.advance(0.01)
        _ = h.controller.submit(h.release(pattern: 3))
        XCTAssertNotEqual(h.controller.submit(old), .sentToTransport)
        XCTAssertEqual(try h.transport.commands().map { $0.1.patternID }, [3])
    }

    @MainActor func testStopAfterDirectionOrExpiryStopStillCancelsNewOutputInSameGeneration() async throws {
        let h = try Harness()
        _ = h.controller.submit(h.release())
        h.clock.advance(0.301); h.controller.tick() // implicit per-band expiry stop, same generation
        _ = h.controller.submit(h.release())
        _ = h.controller.allStop(reason: "user_stop")
        XCTAssertEqual(try h.transport.commands().filter { $0.0 == h.left }.map { $0.1.patternID }, [1, 0, 1, 0])
    }

    @MainActor func testRepeatedStartEndRebindsSubscriptionsWithoutResumingOldContext() async throws {
        let h = try Harness()
        _ = h.controller.submit(h.release())
        h.controller.shutdown()
        let count = h.transport.writes.count
        h.controller.start()
        XCTAssertNotNil(h.transport.onEvent)
        XCTAssertFalse(h.transport.isShutdown)
        XCTAssertTrue(h.transport.connectionRequests.contains(h.left))
        XCTAssertTrue(h.transport.connectionRequests.contains(h.right))
        XCTAssertFalse(h.controller.isSynchronized); XCTAssertNil(h.controller.context)
        XCTAssertEqual(h.transport.writes.count, count)
        h.controller.shutdown()
    }

    @MainActor func testFirmwareRebootSequenceRegressionStopsAndInvalidatesSynchronization() async throws {
        let h = try Harness()
        h.transport.onEvent?(.status(h.left, Data([80, 1, 10, 0, 0])))
        _ = h.controller.submit(h.release())
        h.transport.onEvent?(.status(h.left, Data([80, 1, 0, 0, 0])))
        XCTAssertFalse(h.controller.isSynchronized)
        XCTAssertEqual(try h.transport.commands().last?.1.patternID, 0)
    }

    @MainActor func testProofCannotDiscardActiveOutputAndMissingSideProofCannotSynchronize() async throws {
        let h = try Harness()
        _ = h.controller.submit(h.release())
        XCTAssertThrowsError(try h.controller.acceptSynchronization(GenerationSynchronizationProof(source: .simulatedFixture, challengeID: h.controller.synchronizationChallengeID,
            peripheralIDs: [.left: h.left, .right: h.right], generation: 2, nextSequences: [.left: 10, .right: 10])))
        let unready = try Harness(synchronized: false)
        XCTAssertThrowsError(try unready.controller.acceptSynchronization(GenerationSynchronizationProof(source: .simulatedFixture, challengeID: unready.controller.synchronizationChallengeID,
            peripheralIDs: [.left: unready.left], generation: 1, nextSequences: [.left: 0])))
    }

    @MainActor func testStoppedNotificationsRequireStopThenFreshGenerationAndNeverDelayStop() async throws {
        let h = try Harness()
        XCTAssertNotEqual(h.controller.submit(h.release(pattern: 8, kind: .stoppedNotification)), .sentToTransport)
        _ = h.controller.allStop(reason: "hazard")
        let fresh = try HapticContext(sessionID: "s", taskID: "t", generation: 2)
        try h.controller.beginGeneration(fresh)
        XCTAssertEqual(h.controller.submit(h.release(pattern: 8, kind: .stoppedNotification, context: fresh)), .sentToTransport)
        _ = h.controller.allStop(reason: "stop_notification")
        XCTAssertEqual(try h.transport.commands().last?.1.patternID, 0)
    }

    @MainActor func testUnconfirmedSideAssignmentDoesNotConnectAndConfirmedAssignmentDoes() async throws {
        let transport = TestTransport()
        let controller = try HapticLinkController(configuration: .unavailableH1, transport: transport,
            metadataStore: MemoryPairingMetadataStore())
        let id = UUID()
        XCTAssertThrowsError(try controller.assign(id, to: .left, confirmedByUser: false))
        XCTAssertTrue(transport.connectionRequests.isEmpty)
        try controller.assign(id, to: .left, confirmedByUser: true)
        XCTAssertEqual(transport.connectionRequests, [id])
    }

    @MainActor func testDelayedHandshakeFromPriorConnectionIsRejectedByChallenge() async throws {
        let h = try Harness(synchronized: false)
        let delayed = GenerationSynchronizationProof(source: .simulatedFixture,
            challengeID: h.controller.synchronizationChallengeID,
            peripheralIDs: [.left: h.left, .right: h.right], generation: 10, nextSequences: [.left: 10, .right: 10])
        h.transport.disconnect(h.right)
        h.transport.connected.insert(h.right)
        h.transport.onEvent?(.state(h.right, .ready))
        h.transport.onEvent?(.status(h.right, Data([80, 1, 0, 0, 0])))
        XCTAssertThrowsError(try h.controller.acceptSynchronization(delayed))
        XCTAssertFalse(h.controller.isSynchronized)
    }

    @MainActor func testFailedBilateralWriteReportsRejectionAndStopsSuccessfulHalf() async throws {
        let h = try Harness()
        h.transport.failedWrites = [h.right]
        let result = h.controller.submit(h.release(pattern: 3, sides: [.left, .right]))
        if case .rejected = result {} else { XCTFail("A failed write must not be reported as sent") }
        XCTAssertEqual(try h.transport.commands().filter { $0.0 == h.left }.map { $0.1.patternID }, [3, 0])
        XCTAssertFalse(h.controller.isSynchronized)
    }

    @MainActor func testEvidenceRecheckedForEachBandAndCrossingDeadlineStopsFirstHalf() async throws {
        let h = try Harness()
        h.transport.afterWrite = { data in if Array(data)[5] == 3 { h.clock.advance(0.301) } }
        let result = h.controller.submit(h.release(pattern: 3, sides: [.left, .right]))
        if case .rejected = result {} else { XCTFail("Expired second-band dispatch must be rejected") }
        let commands = try h.transport.commands()
        XCTAssertEqual(commands.filter { $0.1.patternID == 3 }.count, 1)
        XCTAssertTrue(commands.filter { $0.0 == h.right }.allSatisfy { $0.1.patternID == 0 })
        XCTAssertFalse(h.controller.hasEligibleOutput)
    }
    @MainActor func testSynchronizationCannotRollbackPreviouslyAcceptedFloorBeforeFirstOutput() async throws {
        let h = try Harness(synchronized: false)
        func proof(_ generation: UInt16) -> GenerationSynchronizationProof {
            GenerationSynchronizationProof(source: .simulatedFixture,
                challengeID: h.controller.synchronizationChallengeID,
                peripheralIDs: [.left: h.left, .right: h.right], generation: generation,
                nextSequences: [.left: 0, .right: 0])
        }
        try h.controller.acceptSynchronization(proof(10))
        XCTAssertThrowsError(try h.controller.acceptSynchronization(proof(1)))
        XCTAssertThrowsError(try h.controller.beginGeneration(h.context))
        try h.controller.beginGeneration(try HapticContext(sessionID: "s", taskID: "t", generation: 10))
    }

    @MainActor func testCalibrationModeCannotChangeWithinActiveGeneration() async throws {
        let h = try Harness()
        _ = h.controller.submit(h.release())
        XCTAssertThrowsError(try h.controller.beginGeneration(h.context, calibration: true))
        XCTAssertFalse(h.controller.isCalibrating)
        _ = h.controller.allStop(reason: "start_calibration")
        let calibration = try HapticContext(sessionID: "s", taskID: "calibration", generation: 2)
        try h.controller.beginGeneration(calibration, calibration: true)
        XCTAssertThrowsError(try h.controller.beginGeneration(calibration, calibration: false))
        XCTAssertTrue(h.controller.isCalibrating)
    }

    @MainActor func testStopDeliveredDuringFirstBandWritePreventsSecondBandAndHeartbeat() async throws {
        let h = try Harness()
        h.transport.afterWrite = { data in
            if Array(data)[5] == 3 { _ = h.controller.allStop(reason: "stop_during_transport_write") }
        }
        let result = h.controller.submit(h.release(pattern: 3, sides: [.left, .right]))
        if case .rejected = result {} else { XCTFail("Stopped release must not be reported as sent") }
        let left = try h.transport.commands().filter { $0.0 == h.left }.map(\.1)
        XCTAssertEqual(left.map(\.patternID), [3, 0])
        XCTAssertEqual(left.map(\.sequence), [0, 1])
        XCTAssertTrue(try h.transport.commands().filter { $0.0 == h.right }.allSatisfy { $0.1.patternID == 0 })
        XCTAssertFalse(h.controller.hasEligibleOutput)
        let count = h.transport.writes.count
        h.clock.advance(0.201); h.controller.tick()
        XCTAssertEqual(h.transport.writes.count, count)
    }

    @MainActor func testStopDeliveredDuringHeartbeatPreventsOtherWristKeepalive() async throws {
        let h = try Harness()
        _ = h.controller.submit(h.release(pattern: 3, sides: [.left, .right]))
        h.transport.afterWrite = { data in
            if Array(data)[5] == 255 { _ = h.controller.allStop(reason: "stop_during_heartbeat") }
        }
        h.clock.advance(0.201); h.controller.tick()
        let commands = try h.transport.commands()
        XCTAssertEqual(commands.filter { $0.0 == h.left }.map { $0.1.patternID }, [3, 255, 0])
        XCTAssertEqual(commands.filter { $0.0 == h.right }.map { $0.1.patternID }, [3, 0])
        XCTAssertFalse(h.controller.hasEligibleOutput)
        XCTAssertNil(h.controller.context)
    }

    @MainActor func testOutputExpiryDuringFirstHeartbeatPreventsOtherWristKeepalive() async throws {
        let h = try Harness()
        _ = h.controller.submit(h.release(pattern: 3, sides: [.left, .right]))
        h.transport.afterWrite = { data in
            if Array(data)[5] == 255 { h.clock.advance(0.101) }
        }
        h.clock.advance(0.201); h.controller.tick()
        let commands = try h.transport.commands()
        XCTAssertEqual(commands.filter { $0.0 == h.left }.map { $0.1.patternID }, [3, 255, 0])
        XCTAssertEqual(commands.filter { $0.0 == h.right }.map { $0.1.patternID }, [3, 0])
        XCTAssertFalse(h.controller.hasEligibleOutput)
        let count = h.transport.writes.count
        h.controller.tick()
        XCTAssertEqual(h.transport.writes.count, count)
    }

    @MainActor func testStopDuringDirectionChangeStopPreventsNewMovement() async throws {
        let h = try Harness()
        _ = h.controller.submit(h.release(pattern: 1))
        h.transport.afterWrite = { data in
            if Array(data)[5] == 0 { _ = h.controller.allStop(reason: "stop_during_direction_change") }
        }
        let result = h.controller.submit(h.release(pattern: 2, sides: [.right]))
        if case .rejected = result {} else { XCTFail("Stopped direction change must not be reported as sent") }
        let commands = try h.transport.commands()
        XCTAssertEqual(commands.filter { $0.0 == h.left }.map { $0.1.patternID }, [1, 0])
        XCTAssertTrue(commands.filter { $0.0 == h.right }.allSatisfy { $0.1.patternID == 0 })
        XCTAssertFalse(h.controller.hasEligibleOutput)
        XCTAssertNil(h.controller.context)
    }

}
