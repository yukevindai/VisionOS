import XCTest
import Targets
import Tasks
@testable import Reflex

@MainActor
private final class R3TestReader: R3ScreenReading {
    var pending: [CheckedContinuation<Targets.ScreenModel, Error>] = []
    var requests: [(String, Int64)] = []
    func readScreen(image: Data, frameID: String, capturedAtUTCms: Int64, requestID: String) async throws -> Targets.ScreenModel {
        requests.append((frameID, capturedAtUTCms))
        return try await withCheckedThrowingContinuation { pending.append($0) }
    }
    func complete(_ model: Targets.ScreenModel) { pending.removeFirst().resume(returning: model) }
}

@MainActor
final class R3TargetEvidenceTests: XCTestCase {
    private let utc: Int64 = 1_700_000_000_000
    private func model(confidence: Double? = nil, safety: Targets.ControlSafety? = nil,
                       frame: String = "frame", payment: Bool = false) -> Targets.ScreenModel {
        .init(frameId: frame, capturedAtMs: utc, panelType: payment ? .orderingKiosk : .elevatorPanel,
              source: .visionModel, controls: [.init(id: "five", label: "5", type: .button,
                region: .init(x: 0.2, y: 0.3, width: 0.1, height: 0.1), confidence: confidence,
                legibility: .clear, safety: safety)], isPaymentScreen: payment,
              image: .init(widthPx: 100, heightPx: 100), evidence: .init(provider: "gemini", model: "test", live: true))
    }
    private func frame(_ context: SessionContext, time: Int64 = 1_000) -> FrameSnapshot {
        .init(context: context, frameID: "frame", capturedAtMs: time, expiresAtMs: time + 300,
              geometryID: "geometry", transform: .identity, preview: nil, encodedImage: Data([1]),
              trackingNormal: true, depthUsable: false)
    }
    private func request(_ context: SessionContext) -> RequestContext {
        .init(task: .init(sessionID: context.sessionID, taskID: "task", generation: context.generation),
              requestID: "request", issuedAtMs: 1_000, expiresAtMs: 6_000)
    }
    private func waitForRequests(_ reader: R3TestReader, count: Int) async {
        for _ in 0..<100 where reader.requests.count < count { await Task.yield() }
        XCTAssertEqual(reader.requests.count, count)
    }
    func testRecognitionAcceptsMissingConfidenceButGuidanceDoesNot() throws {
        let observed = model()
        try R3TargetMapping.validateRecognition(observed, frameID: "frame", capturedAtUTCms: utc)
        XCTAssertThrowsError(try R3TargetMapping.resolve("five", kind: .button, model: observed, transform: .identity)) {
            XCTAssertEqual($0 as? R3EvidenceError, .lowConfidence)
        }
        XCTAssertThrowsError(try R3TargetMapping.resolve("five", kind: .button,
            model: model(confidence: 0.9), transform: .identity)) {
            XCTAssertEqual($0 as? R3EvidenceError, .unsafeControl)
        }
    }
    func testConfidentOrdinaryTargetPreservesFrameAndMapsCrop() throws {
        let target = try R3TargetMapping.resolve("fifth", kind: .button,
            model: model(confidence: 0.9, safety: .ordinary),
            transform: .init(originX: 0.1, originY: 0.2, scaleX: 0.5, scaleY: 0.5))
        XCTAssertEqual(target.frameID, "frame")
        XCTAssertEqual(target.region.x, 0.2, accuracy: 0.0001)
        XCTAssertEqual(target.region.y, 0.35, accuracy: 0.0001)
        XCTAssertFalse(target.requiresApproach)
    }
    func testPaymentScreenNeedsNoInventedConfidenceAndHasNoControls() throws {
        let screen = try R3TargetMapping.taskScreen(model(payment: true), transform: .identity)
        XCTAssertTrue(screen.isPaymentScreen)
        XCTAssertTrue(screen.controls.isEmpty)
        XCTAssertNil(screen.transitionEvidence)
    }
    func testMismatchedUTCAndFrameRejected() {
        XCTAssertThrowsError(try R3TargetMapping.validateRecognition(model(), frameID: "other", capturedAtUTCms: utc))
        XCTAssertThrowsError(try R3TargetMapping.validateRecognition(model(), frameID: "frame", capturedAtUTCms: 1_000))
    }
    func testCancelledGenerationCannotReturnOrRestart() async throws {
        let reader = R3TestReader(), clock = ManualAppClock()
        let client = R3TargetEvidenceClient(reader: reader, clock: clock)
        let context = SessionContext(sessionID: "session", generation: 1)
        client.updateContext(context); client.receive(frame(context), capturedAtUTCms: utc)
        let operation = Task { try await client.resolve(request(context), query: "5", kind: .button) }
        await waitForRequests(reader, count: 1)
        client.cancel(context: context)
        reader.complete(model(confidence: 0.9, safety: .ordinary))
        do { _ = try await operation.value; XCTFail("Cancelled result returned") } catch {}
        client.updateContext(context); client.receive(frame(context), capturedAtUTCms: utc)
        do { _ = try await client.resolve(request(context), query: "5", kind: .button); XCTFail("Restarted cancelled generation") }
        catch { XCTAssertEqual(error as? R3EvidenceError, .wrongContext) }
    }
    func testOlderCancellationDoesNotInvalidateNewGeneration() async throws {
        let reader = R3TestReader(), clock = ManualAppClock()
        let client = R3TargetEvidenceClient(reader: reader, clock: clock)
        let old = SessionContext(sessionID: "session", generation: 1)
        let current = SessionContext(sessionID: "session", generation: 2)
        client.updateContext(old); client.updateContext(current); client.cancel(context: old)
        client.receive(frame(current), capturedAtUTCms: utc)
        let operation = Task { try await client.resolve(request(current), query: "5", kind: .button) }
        await waitForRequests(reader, count: 1)
        reader.complete(model(confidence: 0.9, safety: .ordinary))
        let result = try await operation.value
        XCTAssertEqual(result.id, "five")
        XCTAssertEqual(reader.requests[0].1, utc)
    }
    func testStaleFrameRejectedWithoutNetwork() async throws {
        let reader = R3TestReader(), clock = ManualAppClock()
        let client = R3TargetEvidenceClient(reader: reader, clock: clock)
        let context = SessionContext(sessionID: "session", generation: 1)
        client.updateContext(context); client.receive(frame(context), capturedAtUTCms: utc)
        clock.advance(301)
        do { _ = try await client.resolve(request(context), query: "5", kind: .button); XCTFail("Stale image sent") }
        catch { XCTAssertEqual(error as? R3EvidenceError, .staleFrame) }
        XCTAssertTrue(reader.requests.isEmpty)
    }
    func testCancelledTransportStillCountsTowardBound() async throws {
        let reader = R3TestReader(), clock = ManualAppClock()
        let client = R3TargetEvidenceClient(reader: reader, clock: clock)
        var operations: [Task<ResolvedTarget, Error>] = []
        for generation in 1...8 {
            let context = SessionContext(sessionID: "session", generation: UInt64(generation))
            client.updateContext(context); client.receive(frame(context), capturedAtUTCms: utc)
            operations.append(Task { try await client.resolve(request(context), query: "5", kind: .button) })
            await waitForRequests(reader, count: generation)
        }
        let context = SessionContext(sessionID: "session", generation: 9)
        client.updateContext(context); client.receive(frame(context), capturedAtUTCms: utc)
        do { _ = try await client.resolve(request(context), query: "5", kind: .button); XCTFail("Unbounded transport") }
        catch { XCTAssertEqual(error as? R3EvidenceError, .providerUnavailable) }
        for _ in 0..<8 { reader.complete(model(confidence: 0.9, safety: .ordinary)) }
        for operation in operations { _ = try? await operation.value }
    }
}
