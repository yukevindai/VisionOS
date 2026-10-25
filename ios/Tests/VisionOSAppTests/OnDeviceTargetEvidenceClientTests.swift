import Foundation
import Tasks
import UIKit
import XCTest
@testable import VisionOS

@MainActor
final class OnDeviceTargetEvidenceClientTests: XCTestCase {
    private let context = SessionContext(sessionID: "SIMULATED-session", generation: 1)

    func testBundledModelLoadsAndRunsOnSyntheticImage() async throws {
        let url = try XCTUnwrap(Bundle.main.url(forResource: "yolo11s", withExtension: "mlmodelc", subdirectory: "Models"))
        let image = UIGraphicsImageRenderer(size: CGSize(width: 640, height: 640)).image { context in
            UIColor.white.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 640, height: 640))
        }
        let encoded = try XCTUnwrap(image.jpegData(compressionQuality: 0.8))
        let detector = VisionObjectDetector(modelURL: url)
        let result = try await detector.detect(image: encoded)
        // Synthetic blank image: proves bundled-model loading/inference, not object accuracy.
        XCTAssertTrue(result.allSatisfy { !$0.identifier.isEmpty && $0.confidence.isFinite })
    }

    func testObjectClassAliasesDoNotInventAttributesOrBrands() throws {
        XCTAssertEqual(try OnDeviceTargetEvidenceClient.objectClass("Find the can"), "cup")
        XCTAssertEqual(try OnDeviceTargetEvidenceClient.objectClass("Can you find the bottle?"), "bottle")
        XCTAssertThrowsError(try OnDeviceTargetEvidenceClient.objectClass("Find the red cup"))
        XCTAssertThrowsError(try OnDeviceTargetEvidenceClient.objectClass("Find the cup and bowl"))
    }

    func testVisionBoxMapsOnceToTopLeftAndOriginalFrame() async throws {
        let clock = ObjectTestClock()
        let detector = ObjectTestDetector(result: [observation()])
        let client = OnDeviceTargetEvidenceClient(clock: clock, detector: detector)
        client.updateContext(context)
        client.receive(frame(transform: FrameTransform(originX: 0.1, originY: 0.2, scaleX: 0.5, scaleY: 0.5)), capturedAtUTCms: 1000)
        let target = try await client.resolve(request(), query: "find cup", kind: .object)
        XCTAssertEqual(target.region.x, 0.2, accuracy: 0.0001)
        XCTAssertEqual(target.region.y, 0.45, accuracy: 0.0001)
        XCTAssertEqual(target.region.width, 0.1, accuracy: 0.0001)
        XCTAssertEqual(target.region.height, 0.15, accuracy: 0.0001)
        XCTAssertEqual(target.frameID, "SIMULATED-frame")
        XCTAssertEqual(target.kind, .object)
        XCTAssertFalse(target.requiresApproach)
        XCTAssertEqual(target.safety, .ordinary)
        XCTAssertNotNil(target.caution)
        let verification = try await client.verify(request(), action: .grasp, target: target)
        XCTAssertEqual(verification, .unavailable)
    }

    func testMultipleCandidatesStayAmbiguousEvenWhenOneHasLowConfidence() async throws {
        let client = OnDeviceTargetEvidenceClient(clock: ObjectTestClock(),
            detector: ObjectTestDetector(result: [observation(), observation(confidence: 0.3)]))
        client.updateContext(context); client.receive(frame(), capturedAtUTCms: 1000)
        do { _ = try await client.resolve(request(), query: "cup", kind: .object); XCTFail("Ambiguous target accepted") }
        catch { XCTAssertEqual(error as? OnDeviceTargetError, .ambiguous) }
    }

    func testLowConfidenceAndInvalidBoxesAreNotRepaired() throws {
        for candidate in [observation(confidence: 0.59), observation(confidence: .nan),
                          observation(box: CGRect(x: -0.1, y: 0.2, width: 0.2, height: 0.3))] {
            XCTAssertThrowsError(try OnDeviceTargetEvidenceClient.target(in: [candidate], wanted: "cup", frame: frame()))
        }
    }

    func testExpiredFrameNeverReachesDetector() async {
        let clock = ObjectTestClock()
        let detector = ObjectTestDetector(result: [observation()])
        let client = OnDeviceTargetEvidenceClient(clock: clock, detector: detector)
        client.updateContext(context); client.receive(frame(), capturedAtUTCms: 1000)
        clock.nowMs = 1401
        do { _ = try await client.resolve(request(), query: "cup", kind: .object); XCTFail("Stale frame accepted") }
        catch { XCTAssertEqual(error as? R3EvidenceError, .staleFrame) }
        let calls = await detector.calls
        XCTAssertEqual(calls, 0)
    }

    func testContextChangeRejectsLateInferenceAndKeepsWorkBounded() async throws {
        let detector = SuspendedObjectDetector()
        let client = OnDeviceTargetEvidenceClient(clock: ObjectTestClock(), detector: detector)
        client.updateContext(context); client.receive(frame(), capturedAtUTCms: 1000)
        let first = Task { try await client.resolve(request(), query: "cup", kind: .object) }
        while !(await detector.started) { await Task.yield() }
        let next = SessionContext(sessionID: context.sessionID, generation: 2)
        client.cancel(context: context); client.updateContext(next)
        client.receive(frame(context: next), capturedAtUTCms: 1000)
        do { _ = try await client.resolve(request(generation: 2), query: "cup", kind: .object); XCTFail("Overlapping inference admitted") }
        catch { XCTAssertEqual(error as? OnDeviceTargetError, .inferenceBusy) }
        await detector.finish([observation()])
        do { _ = try await first.value; XCTFail("Late inference accepted") } catch {}
    }

    func testGeometryChangeRejectsLateInference() async throws {
        let detector = SuspendedObjectDetector()
        let client = OnDeviceTargetEvidenceClient(clock: ObjectTestClock(), detector: detector)
        client.updateContext(context); client.receive(frame(), capturedAtUTCms: 1000)
        let first = Task { try await client.resolve(request(), query: "cup", kind: .object) }
        while !(await detector.started) { await Task.yield() }
        client.receive(frame(geometryID: "rotated-camera"), capturedAtUTCms: 1000)
        await detector.finish([observation()])
        do { _ = try await first.value; XCTFail("Old geometry accepted") } catch {}
    }

    func testUnsupportedTaskDelegatesAndForwardsFrameContextCancellation() async throws {
        let fallback = ObjectFallback()
        let client = OnDeviceTargetEvidenceClient(clock: ObjectTestClock(), detector: ObjectTestDetector(result: []), fallback: fallback)
        client.updateContext(context); client.receive(frame(), capturedAtUTCms: 1000)
        _ = try await client.readScreen(request())
        XCTAssertEqual(fallback.context, context)
        XCTAssertEqual(fallback.frameID, "SIMULATED-frame")
        XCTAssertEqual(fallback.screenReads, 1)
        client.cancel(context: context)
        XCTAssertEqual(fallback.cancellations, 1)
    }

    private func request(generation: UInt64 = 1) -> RequestContext {
        RequestContext(task: TaskContext(sessionID: context.sessionID, taskID: "task", generation: generation),
                       requestID: "request", issuedAtMs: 1000, expiresAtMs: 6000)
    }
    private func frame(context supplied: SessionContext? = nil, transform: FrameTransform = .identity,
                       geometryID: String = "camera") -> FrameSnapshot {
        FrameSnapshot(context: supplied ?? context, frameID: "SIMULATED-frame", capturedAtMs: 1000, expiresAtMs: 1300,
                      geometryID: geometryID, transform: transform, preview: nil, encodedImage: Data([1, 2, 3]),
                      trackingNormal: true, depthUsable: false)
    }
    private func observation(confidence: Float = 0.8, box: CGRect = CGRect(x: 0.2, y: 0.2, width: 0.2, height: 0.3)) -> OnDeviceObjectObservation {
        OnDeviceObjectObservation(identifier: "cup", confidence: confidence, boundingBox: box)
    }
}

private actor ObjectTestDetector: OnDeviceObjectDetecting {
    let result: [OnDeviceObjectObservation]
    private(set) var calls = 0
    init(result: [OnDeviceObjectObservation]) { self.result = result }
    func detect(image: Data) async throws -> [OnDeviceObjectObservation] { calls += 1; return result }
}
private actor SuspendedObjectDetector: OnDeviceObjectDetecting {
    private var continuation: CheckedContinuation<[OnDeviceObjectObservation], Error>?
    private(set) var started = false
    func detect(image: Data) async throws -> [OnDeviceObjectObservation] {
        try await withCheckedThrowingContinuation { continuation = $0; started = true }
    }
    func finish(_ result: [OnDeviceObjectObservation]) { continuation?.resume(returning: result); continuation = nil }
}
@MainActor
private final class ObjectTestClock: AppClock {
    var nowMs: Int64 = 1000
    func schedule(afterMs: Int64, _ action: @escaping @MainActor () -> Void) -> ScheduledWork { ScheduledWork {} }
}
@MainActor
private final class ObjectFallback: FrameTargetEvidenceClient {
    var context: SessionContext?
    var frameID: String?
    var screenReads = 0
    var cancellations = 0
    func updateContext(_ context: SessionContext) { self.context = context }
    func receive(_ frame: FrameSnapshot, capturedAtUTCms: Int64) { frameID = frame.frameID }
    func resolve(_ request: RequestContext, query: String, kind: TaskKind) async throws -> ResolvedTarget { throw R3EvidenceError.unsupportedTask }
    func readScreen(_ request: RequestContext) async throws -> TaskScreen { screenReads += 1; return TaskScreen(id: "screen", frameID: "frame", title: "Screen", controls: []) }
    func readMenu(_ request: RequestContext, query: String) async throws -> MenuContent { MenuContent(sections: []) }
    func answerMenu(_ request: RequestContext, question: String, menu: MenuContent) async throws -> String { "SIMULATED" }
    func verify(_ request: RequestContext, action: ActionKind, target: ResolvedTarget) async throws -> VerificationOutcome { .unavailable }
    func cancel(context: SessionContext) { cancellations += 1 }
}
