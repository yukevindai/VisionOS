import XCTest
import HapticLink
@testable import VisionOS

/// All values in this file are synthetic test contracts. No CoreBluetooth central is created.
private final class BandServiceHapticClock: HapticClock {
    var now: TimeInterval = 100
}

@MainActor
private final class BandServiceTestTransport: BandTransport {
    var onEvent: ((BandTransportEvent) -> Void)?
    // Exercise the live adapter path using a test-only transport; no physical hardware is accessed.
    let isSimulated = false
    var connected: Set<UUID> = []
    var connectionRequests: [UUID] = []
    var writes: [(UUID, Data)] = []

    func startDiscovery() { onEvent?(.availability(.poweredOn)) }
    func stopDiscovery() {}
    func connect(_ peripheralID: UUID) { connectionRequests.append(peripheralID) }
    func disconnect(_ peripheralID: UUID) {
        connected.remove(peripheralID)
        onEvent?(.state(peripheralID, .disconnected))
    }
    func canWrite(_ peripheralID: UUID) -> Bool { connected.contains(peripheralID) }
    func write(_ data: Data, to peripheralID: UUID) -> Bool {
        guard canWrite(peripheralID) else { return false }
        writes.append((peripheralID, data)); return true
    }
    func shutdown() { connected.removeAll(); onEvent = nil }

    func ready(_ peripheralID: UUID, firmware: UInt8 = 1) {
        connected.insert(peripheralID)
        onEvent?(.state(peripheralID, .ready))
        onEvent?(.status(peripheralID, Data([80, firmware, 0, 0, 0])))
    }
    func commands() throws -> [(UUID, HapticCommand)] {
        try writes.map { ($0.0, try HapticCommand(data: $0.1, expectedVersion: 1)) }
    }
}

@MainActor
private struct BandServiceHarness {
    let left = UUID()
    let right = UUID()
    let hapticClock = BandServiceHapticClock()
    let transport = BandServiceTestTransport()
    let store: MemoryPairingMetadataStore
    let controller: HapticLinkController
    let service: BandService
    let contract: H1Contract

    init(paired: Bool = true, calibrated: Bool = false) throws {
        var patterns: [UInt8: PatternParameters] = [:]
        for id: UInt8 in 1...9 {
            patterns[id] = PatternParameters(patternID: id, intensity: 80, rateHz: 5,
                                             ttlMs: id == 6 ? 600 : 1_000)
        }
        contract = try H1Contract(serviceUUID: UUID(), commandUUID: UUID(), statusUUID: UUID(),
            protocolVersion: 1, supportedFirmwareVersions: [1],
            stop: PatternParameters(patternID: 0, intensity: 0, rateHz: 0, ttlMs: 0),
            heartbeat: PatternParameters(patternID: 255, intensity: 0, rateHz: 0, ttlMs: 0),
            patterns: patterns, identityDescription: "TEST ONLY synthetic identity",
            generationPolicyDescription: "TEST ONLY synthetic handshake", supportsStoppedNotifications: true)
        store = MemoryPairingMetadataStore(metadata: paired ? [
            PairingMetadata(peripheralID: left, side: .left, calibratedFirmwareVersion: 1,
                            calibrationCompleted: calibrated),
            PairingMetadata(peripheralID: right, side: .right, calibratedFirmwareVersion: 1,
                            calibrationCompleted: calibrated)
        ] : [])
        controller = try HapticLinkController(configuration: .confirmed(contract), transport: transport,
            clock: hapticClock, metadataStore: store)
        service = BandService(controller: controller, simulated: false, clock: ManualAppClock())
    }

    func makeReady() {
        transport.onEvent?(.availability(.poweredOn))
        transport.ready(left); transport.ready(right)
    }
    func proof(generation: UInt16, nextSequence: UInt16 = 0,
               leftID: UUID? = nil, challenge: UUID? = nil) -> GenerationSynchronizationProof {
        GenerationSynchronizationProof(source: .confirmedH1Handshake("TEST ONLY fixture handshake"),
            challengeID: challenge ?? controller.synchronizationChallengeID,
            peripheralIDs: [.left: leftID ?? left, .right: right], generation: generation,
            nextSequences: [.left: nextSequence, .right: nextSequence])
    }
    func projection(generation: UInt64, taskID: String = "task") -> TasksContextProjection {
        TasksContextProjection(sessionID: "session", taskID: taskID, generation: generation)
    }
    func release(generation: UInt64, side: BandSide = .left, taskID: String = "task") throws -> ScheduledHapticRelease {
        ScheduledHapticRelease(context: try HapticContext(sessionID: "session", taskID: taskID,
                                                         generation: generation),
            kind: .movement, evidenceTime: hapticClock.now, releasedAt: hapticClock.now,
            expiresAt: hapticClock.now + 0.3, commands: [side: contract.patterns[1]!])
    }
}

@MainActor
final class BandServiceTests: XCTestCase {
    func testDesiredContextBeginsLazilyAfterTransitionAllStop() async throws {
        let h = try BandServiceHarness(calibrated: true)
        h.makeReady(); try h.service.acceptSynchronization(h.proof(generation: 10))

        try h.service.setContext(h.projection(generation: 11))
        XCTAssertNil(h.controller.context, "Setting desired context must not start output before scheduler release")
        XCTAssertTrue(h.transport.writes.isEmpty)
        h.service.allStop(reason: "transition_to_hand_guidance")
        XCTAssertNil(h.controller.context)
        h.service.dispatch(try h.release(generation: 11))

        XCTAssertEqual(h.controller.context?.generation, 11)
        XCTAssertEqual(try h.transport.commands().map { $0.1.patternID }, [0, 0, 1])
        XCTAssertEqual(h.service.lastStopReport?.sides[.left], .sentToTransport)

        h.service.allStop(reason: "cancel_hand_guidance")
        try h.service.setContext(h.projection(generation: 12))
        let count = h.transport.writes.count
        h.service.dispatch(try h.release(generation: 11))
        XCTAssertEqual(h.transport.writes.count, count, "Old scheduler context must not reach the transport")
        h.service.dispatch(try h.release(generation: 12))
        XCTAssertEqual(h.controller.context?.generation, 12)
        XCTAssertEqual(try h.transport.commands().last?.1.patternID, 1)
    }

    func testDiscoveryDoesNotAssignFromNameAndExplicitAssignmentConnectsAndPersists() async throws {
        let h = try BandServiceHarness(paired: false)
        // A misleading name must never override the explicitly confirmed side.
        h.transport.onEvent?(.discovered(DiscoveredBand(id: h.left, nameHint: "HG-R")))
        XCTAssertEqual(h.service.discovered.map(\.id), [h.left])
        XCTAssertTrue(h.service.pairings.isEmpty)
        XCTAssertTrue(h.transport.connectionRequests.isEmpty)

        try h.service.assign(h.left, side: .left)
        XCTAssertEqual(h.transport.connectionRequests, [h.left])
        XCTAssertEqual(h.service.pairings.first?.side, .left)
        XCTAssertEqual(h.store.metadata.first?.peripheralID, h.left)
        XCTAssertFalse(h.store.metadata.first!.calibrationCompleted)
        XCTAssertThrowsError(try h.service.assign(h.left, side: .right))
        XCTAssertThrowsError(try h.service.assign(h.right, side: .left))
        XCTAssertEqual(h.store.metadata.count, 1)
    }

    func testHandshakeChallengeIsForwardedAndAcceptedFloorAdvancesTaskGeneration() async throws {
        let h = try BandServiceHarness()
        var challenges: [UUID] = []
        var acceptedFloors: [UInt64] = []
        h.service.onSynchronizationNeeded = { challenges.append($0) }
        h.service.onGenerationSynchronized = { acceptedFloors.append($0) }
        h.makeReady()

        XCTAssertFalse(h.service.isReady)
        XCTAssertEqual(challenges.last, h.service.synchronizationChallengeID)
        try h.service.acceptSynchronization(h.proof(generation: 400))
        XCTAssertTrue(h.service.isReady)
        XCTAssertEqual(h.service.minimumNextGeneration, 401)
        XCTAssertEqual(acceptedFloors, [401])

        let oldChallenge = try XCTUnwrap(h.service.synchronizationChallengeID)
        let delayedProof = h.proof(generation: 500, nextSequence: 10, challenge: oldChallenge)
        h.transport.disconnect(h.right)
        h.transport.ready(h.right)
        XCTAssertFalse(h.service.isReady)
        XCTAssertNotEqual(h.service.synchronizationChallengeID, oldChallenge)
        XCTAssertEqual(challenges.last, h.service.synchronizationChallengeID)
        XCTAssertThrowsError(try h.service.acceptSynchronization(delayedProof))
        XCTAssertEqual(acceptedFloors, [401], "A stale proof must not publish a task generation")

        try h.service.acceptSynchronization(h.proof(generation: 500, nextSequence: 10))
        XCTAssertEqual(h.service.minimumNextGeneration, 501)
        XCTAssertEqual(acceptedFloors, [401, 501])
    }

    func testLiveAdapterCalibrationCompletionEnablesTransportOutput() async throws {
        let h = try BandServiceHarness()
        h.makeReady(); try h.service.acceptSynchronization(h.proof(generation: 1))
        XCTAssertFalse(h.service.simulated)
        XCTAssertTrue(h.service.isReady)
        XCTAssertFalse(h.service.pairings.contains { $0.calibrationCompleted })

        try h.service.setContext(h.projection(generation: 2))
        h.service.dispatch(try h.release(generation: 2))
        XCTAssertTrue(h.transport.writes.isEmpty, "Synchronization alone cannot enable uncalibrated movement")

        // SessionCoordinator calls this only after its full explicit side/pattern answer sequence.
        try h.service.completeCalibration()
        XCTAssertTrue(h.service.pairings.allSatisfy { $0.calibrationCompleted && $0.calibratedFirmwareVersion == 1 })
        XCTAssertTrue(h.store.metadata.allSatisfy(\.calibrationCompleted))
        h.service.dispatch(try h.release(generation: 2))
        XCTAssertEqual(try h.transport.commands().map { $0.1.patternID }, [1])
    }

    func testRePairInvalidatesStoredCalibrationAndPreviouslyCalibratedPairSignature() async throws {
        let h = try BandServiceHarness(calibrated: true)
        h.makeReady(); try h.service.acceptSynchronization(h.proof(generation: 1))
        let previousSignature = try XCTUnwrap(h.service.pairSignature)
        XCTAssertTrue(h.store.metadata.allSatisfy(\.calibrationCompleted))

        h.service.forget(.left)
        XCTAssertNil(h.service.pairSignature)
        XCTAssertFalse(h.store.metadata.contains { $0.side == .left })
        let replacement = UUID()
        try h.service.assign(replacement, side: .left)
        h.transport.ready(replacement)
        try h.service.acceptSynchronization(h.proof(generation: 2, nextSequence: 10, leftID: replacement))

        let replacementSignature = try XCTUnwrap(h.service.pairSignature)
        XCTAssertNotEqual(replacementSignature, previousSignature,
                          "A persisted prior-pair calibration signature cannot authorize the replacement pair")
        XCTAssertEqual(h.store.metadata.first { $0.side == .left }?.calibrationCompleted, false)
        XCTAssertEqual(h.store.metadata.first { $0.side == .right }?.calibrationCompleted, true)
        let count = h.transport.writes.count
        try h.service.setContext(h.projection(generation: 3))
        h.service.dispatch(try h.release(generation: 3))
        XCTAssertEqual(h.transport.writes.count, count, "Re-pairing cannot inherit the old band's calibration")
    }
}
