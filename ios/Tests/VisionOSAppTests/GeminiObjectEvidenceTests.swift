import XCTest
import Targets
import Tasks
@testable import VisionOS

@MainActor
private final class ControlledObjectReader: R3ObjectReading {
    struct Read {
        let query: String
        let frameID: String
        let utc: Int64
        let requestID: String
    }
    var requests: [Read] = []
    var pending: [CheckedContinuation<GeminiObjectRecognition, Error>] = []

    func findObject(image: Data, query: String, frameID: String,
                    capturedAtUTCms: Int64, requestID: String) async throws -> GeminiObjectRecognition {
        requests.append(Read(query: query, frameID: frameID, utc: capturedAtUTCms, requestID: requestID))
        // Deliberately ignores cancellation, like a provider that finishes a cancelled request.
        return try await withCheckedThrowingContinuation { pending.append($0) }
    }
    func complete(_ result: GeminiObjectRecognition) { pending.removeFirst().resume(returning: result) }
}

@MainActor
final class GeminiObjectEvidenceTests: XCTestCase {
    private let utc: Int64 = 1_700_000_000_000
    private let context = SessionContext(sessionID: "object-session", generation: 1)

    private func recognition(label: String = "silver keys", confidence: Double? = 0.9,
                             safety: Targets.ControlSafety? = .ordinary, frameID: String = "source-frame",
                             status: GeminiObjectRecognition.Status = .selected) -> GeminiObjectRecognition {
        let model = Targets.ScreenModel(frameId: frameID, capturedAtMs: utc, panelType: .other,
            source: .visionModel, controls: [.init(id: "object", label: label, type: .other,
                region: .init(x: 0.2, y: 0.3, width: 0.1, height: 0.2), confidence: confidence,
                legibility: .clear, safety: safety)], image: .init(widthPx: 640, heightPx: 480),
            evidence: .init(provider: "gemini", model: "synthetic-test-only", live: true))
        return .init(status: status, selectedObjectId: status == .selected ? "object" : nil, screenModel: model)
    }
    private func frame(_ context: SessionContext, id: String = "source-frame", time: Int64 = 1_000,
                       geometry: String = "geometry", transform: FrameTransform = .identity) -> FrameSnapshot {
        .init(context: context, frameID: id, capturedAtMs: time, expiresAtMs: time + 300,
              geometryID: geometry, transform: transform, preview: nil, encodedImage: Data([1]),
              trackingNormal: true, depthUsable: false)
    }
    private func request(_ context: SessionContext, expiry: Int64 = 6_000) -> RequestContext {
        .init(task: .init(sessionID: context.sessionID, taskID: "object-task", generation: context.generation),
              requestID: "object-request", issuedAtMs: 1_000, expiresAtMs: expiry)
    }
    private func waitForRequests(_ reader: ControlledObjectReader, count: Int = 1) async throws {
        for _ in 0..<100 {
            if reader.requests.count == count { return }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTFail("Expected \(count) provider reads; got \(reader.requests.count)")
        throw R3EvidenceError.missingFrame
    }

    func testLowOrInvalidConfidenceNeverAnnouncesConfidentIdentification() {
        for confidence in [0.1, -1, 2, Double.nan] {
            XCTAssertThrowsError(try GeminiObjectEvidenceClient.target(
                recognition(confidence: confidence), transform: .identity)) { error in
                XCTAssertEqual(error as? GeminiObjectError, confidence == 0.1 ? .notFound : .invalidResponse)
            }
        }
    }

    func testArbitraryObjectsPreserveSourceFrameAndCropAfterNewFramesArrive() async throws {
        for label in ["silver keys", "blue phone", "red book"] {
            let reader = ControlledObjectReader(), clock = ManualAppClock()
            let client = GeminiObjectEvidenceClient(reader: reader, clock: clock)
            client.updateContext(context)
            client.receive(frame(context, transform: .init(originX: 0.1, originY: 0.2, scaleX: 0.5, scaleY: 0.5)),
                           capturedAtUTCms: utc)
            let operation = Task { try await client.resolve(request(context), query: "what is this", kind: .object) }
            try await waitForRequests(reader)
            clock.advance(100)
            client.receive(frame(context, id: "newer-frame", time: 1_100), capturedAtUTCms: utc + 100)
            reader.complete(recognition(label: label))
            let target = try await operation.value
            XCTAssertEqual(target.label, label)
            XCTAssertEqual(target.kind, .object)
            XCTAssertEqual(target.safety, .unassessedObject)
            XCTAssertEqual(target.frameID, "source-frame")
            XCTAssertEqual(target.region.x, 0.2, accuracy: 0.0001)
            XCTAssertEqual(target.region.y, 0.35, accuracy: 0.0001)
            XCTAssertEqual(target.region.width, 0.05, accuracy: 0.0001)
            XCTAssertEqual(target.region.height, 0.1, accuracy: 0.0001)
            XCTAssertFalse(target.requiresApproach)
            XCTAssertEqual(reader.requests.first?.query, "what is this")
            XCTAssertEqual(reader.requests.first?.frameID, "source-frame")
            XCTAssertEqual(reader.requests.first?.utc, utc)
        }
    }

    func testMissingConfidenceOrSafetyProducesOnlyUnassessedCandidate() async throws {
        let cases: [(Double?, Targets.ControlSafety?)] = [(nil, nil), (0.9, nil), (nil, .ordinary)]
        for (confidence, safety) in cases {
            let reader = ControlledObjectReader(), clock = ManualAppClock()
            let client = GeminiObjectEvidenceClient(reader: reader, clock: clock)
            client.updateContext(context); client.receive(frame(context), capturedAtUTCms: utc)
            let operation = Task { try await client.resolve(request(context), query: "find my keys", kind: .object) }
            try await waitForRequests(reader)
            reader.complete(recognition(confidence: confidence, safety: safety))
            let target = try await operation.value
            XCTAssertEqual(target.safety, .unassessedObject)
            XCTAssertEqual(target.frameID, "source-frame")
            XCTAssertFalse(target.requiresApproach)
        }
    }

    func testExplicitUnsafeSafetyCannotBecomeGuidanceCandidate() {
        for safety in [Targets.ControlSafety.emergency, .payment, .purchaseAuthorization, .unknownCheckout, .unknown] {
            XCTAssertThrowsError(try GeminiObjectEvidenceClient.target(
                recognition(safety: safety), transform: .identity)) { error in
                XCTAssertEqual(error as? GeminiObjectError, .identificationOnly("silver keys"))
            }
        }
    }

    func testSemanticAmbiguityAndNotFoundDoNotChooseFirstCandidate() async throws {
        for (status, expected) in [(GeminiObjectRecognition.Status.ambiguous, GeminiObjectError.ambiguous),
                                    (.notFound, .notFound), (.providerUnavailable, .unavailable)] {
            let reader = ControlledObjectReader(), clock = ManualAppClock()
            let client = GeminiObjectEvidenceClient(reader: reader, clock: clock)
            client.updateContext(context); client.receive(frame(context), capturedAtUTCms: utc)
            let operation = Task { try await client.resolve(request(context), query: "which book", kind: .object) }
            try await waitForRequests(reader)
            reader.complete(recognition(status: status))
            do { _ = try await operation.value; XCTFail("Nonselected response authorized target") }
            catch { XCTAssertEqual(error as? GeminiObjectError, expected) }
        }
    }

    func testMissingConfigurationReturnsExplicitErrorWithoutWaitingForCamera() async throws {
        let client = GeminiObjectEvidenceClient(reader: nil, clock: ManualAppClock())
        client.updateContext(context)
        XCTAssertFalse(client.isConfigured)
        do { _ = try await client.resolve(request(context), query: "keys", kind: .object); XCTFail("Missing service accepted") }
        catch { XCTAssertEqual(error as? GeminiObjectError, .notConfigured) }
    }

    func testLateCancelledResponseCannotRestartCancelledGeneration() async throws {
        let reader = ControlledObjectReader(), clock = ManualAppClock()
        let client = GeminiObjectEvidenceClient(reader: reader, clock: clock)
        client.updateContext(context); client.receive(frame(context), capturedAtUTCms: utc)
        let operation = Task { try await client.resolve(request(context), query: "keys", kind: .object) }
        try await waitForRequests(reader)
        client.cancel(context: context)
        reader.complete(recognition())
        do { _ = try await operation.value; XCTFail("Cancelled result returned") }
        catch { XCTAssertTrue(error is CancellationError) }
        client.updateContext(context); client.receive(frame(context), capturedAtUTCms: utc)
        do { _ = try await client.resolve(request(context), query: "keys", kind: .object); XCTFail("Cancelled generation restarted") }
        catch { XCTAssertEqual(error as? R3EvidenceError, .wrongContext) }
    }

    func testGenerationAndGeometryChangesDiscardLateResults() async throws {
        for changeGeneration in [true, false] {
            let reader = ControlledObjectReader(), clock = ManualAppClock()
            let client = GeminiObjectEvidenceClient(reader: reader, clock: clock)
            client.updateContext(context); client.receive(frame(context), capturedAtUTCms: utc)
            let operation = Task { try await client.resolve(request(context), query: "phone", kind: .object) }
            try await waitForRequests(reader)
            if changeGeneration {
                client.updateContext(.init(sessionID: context.sessionID, generation: 2))
            } else {
                client.receive(frame(context, id: "rotated", geometry: "rotated-geometry"), capturedAtUTCms: utc)
            }
            reader.complete(recognition())
            do { _ = try await operation.value; XCTFail("Changed capture scope accepted late evidence") }
            catch { XCTAssertTrue(error is CancellationError) }
        }
    }

    func testCallerCancellationDiscardsLateProviderResult() async throws {
        let reader = ControlledObjectReader(), clock = ManualAppClock()
        let client = GeminiObjectEvidenceClient(reader: reader, clock: clock)
        client.updateContext(context); client.receive(frame(context), capturedAtUTCms: utc)
        let operation = Task { try await client.resolve(request(context), query: "phone", kind: .object) }
        try await waitForRequests(reader)
        operation.cancel()
        reader.complete(recognition())
        do { _ = try await operation.value; XCTFail("Cancelled caller received result") }
        catch { XCTAssertTrue(error is CancellationError) }
    }

    func testExpiredRequestRejectsLateProviderResult() async throws {
        let reader = ControlledObjectReader(), clock = ManualAppClock()
        let client = GeminiObjectEvidenceClient(reader: reader, clock: clock)
        client.updateContext(context); client.receive(frame(context), capturedAtUTCms: utc)
        let operation = Task { try await client.resolve(request(context), query: "book", kind: .object) }
        try await waitForRequests(reader)
        clock.advance(5_000)
        reader.complete(recognition())
        do { _ = try await operation.value; XCTFail("Expired request returned target") }
        catch { XCTAssertEqual(error as? R3EvidenceError, .staleFrame) }
    }

    func testStaleSourceWaitsForFreshFrameAndStopsAtRequestExpiry() async throws {
        let reader = ControlledObjectReader(), clock = ManualAppClock()
        let client = GeminiObjectEvidenceClient(reader: reader, clock: clock)
        client.updateContext(context); client.receive(frame(context), capturedAtUTCms: utc)
        clock.advance(301)
        let operation = Task { try await client.resolve(request(context, expiry: 1_500), query: "book", kind: .object) }
        try await Task.sleep(nanoseconds: 40_000_000)
        XCTAssertTrue(reader.requests.isEmpty)
        clock.advance(199)
        do { _ = try await operation.value; XCTFail("Stale frame reached provider") }
        catch { XCTAssertEqual(error as? R3EvidenceError, .staleFrame) }
        XCTAssertTrue(reader.requests.isEmpty)
    }

    func testNewGenerationWaitsForItsOwnFreshFrame() async throws {
        let reader = ControlledObjectReader(), clock = ManualAppClock()
        let client = GeminiObjectEvidenceClient(reader: reader, clock: clock)
        client.updateContext(context); client.receive(frame(context), capturedAtUTCms: utc)
        let next = SessionContext(sessionID: context.sessionID, generation: 2)
        client.updateContext(next)
        let operation = Task { try await client.resolve(request(next), query: "book", kind: .object) }
        client.receive(frame(context), capturedAtUTCms: utc)
        try await Task.sleep(nanoseconds: 40_000_000)
        XCTAssertTrue(reader.requests.isEmpty)
        client.receive(frame(next, id: "next-frame"), capturedAtUTCms: utc)
        try await waitForRequests(reader)
        XCTAssertEqual(reader.requests.first?.frameID, "next-frame")
        reader.complete(recognition(label: "red book", frameID: "next-frame"))
        let target = try await operation.value
        XCTAssertEqual(target.frameID, "next-frame")
    }
}
