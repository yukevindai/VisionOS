import CoreVideo
import Foundation
import LiveKit
import XCTest
@testable import VisionOS

private final class FrameTestClock: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Int64

    init(_ value: Int64) { self.value = value }
    var now: Int64 {
        lock.lock(); defer { lock.unlock() }
        return value
    }
    func set(_ newValue: Int64) {
        lock.lock(); value = newValue; lock.unlock()
    }
}

final class LiveKitFrameAdmissionTests: XCTestCase {
    private func frame(atMs time: Int64) throws -> VideoFrame {
        var buffer: CVPixelBuffer?
        let status = CVPixelBufferCreate(kCFAllocatorDefault, 2, 2,
                                         kCVPixelFormatType_32BGRA, nil, &buffer)
        XCTAssertEqual(status, kCVReturnSuccess)
        let pixelBuffer = try XCTUnwrap(buffer)
        return VideoFrame(dimensions: Dimensions(width: 2, height: 2),
                          rotation: ._0,
                          timeStampNs: time * 1_000_000,
                          buffer: CVPixelVideoBuffer(pixelBuffer: pixelBuffer))
    }

    func testOnePendingFrameAndCadence() throws {
        let clock = FrameTestClock(1_000)
        let attempt = UUID()
        let gate = NativeMediaSampleGate(attempt: attempt, nowMs: { clock.now })
        XCTAssertTrue(gate.authorize(untilMs: 10_000))
        let revision = gate.revision
        let processor = FrameAdmissionProcessor(gate: gate, attempt: attempt)

        XCTAssertTrue(processor.reserve(timeStampNs: 1_000_000_000, capturedAtMs: 1_000,
                                        authorizationRevision: revision))
        XCTAssertFalse(processor.reserve(timeStampNs: 1_100_000_000, capturedAtMs: 1_100,
                                         authorizationRevision: revision))
        XCTAssertNil(processor.process(frame: try frame(atMs: 1_001)))
        XCTAssertFalse(processor.reserve(timeStampNs: 1_100_000_000, capturedAtMs: 1_100,
                                         authorizationRevision: revision))
        XCTAssertNotNil(processor.process(frame: try frame(atMs: 1_000)))
        XCTAssertFalse(processor.reserve(timeStampNs: 1_060_000_000, capturedAtMs: 1_060,
                                         authorizationRevision: revision))
        XCTAssertTrue(processor.reserve(timeStampNs: 1_067_000_000, capturedAtMs: 1_067,
                                        authorizationRevision: revision))
        clock.set(1_067)
        XCTAssertNotNil(processor.process(frame: try frame(atMs: 1_067)))
    }

    func testSuspendedFrameDoesNotPassAfterReauthorization() throws {
        let clock = FrameTestClock(2_000)
        let attempt = UUID()
        let gate = NativeMediaSampleGate(attempt: attempt, nowMs: { clock.now })
        XCTAssertTrue(gate.authorize(untilMs: 10_000))
        let priorRevision = gate.revision
        let processor = FrameAdmissionProcessor(gate: gate, attempt: attempt)
        XCTAssertTrue(processor.reserve(timeStampNs: 2_000_000_000, capturedAtMs: 2_000,
                                        authorizationRevision: priorRevision))
        gate.suspend()
        XCTAssertTrue(gate.authorize(untilMs: 10_000, expectedRevision: gate.revision))
        XCTAssertNotEqual(gate.revision, priorRevision)
        XCTAssertNil(processor.process(frame: try frame(atMs: 2_000)))
    }

    func testExpiredFrameIsRejectedOnSDKProcessingQueue() throws {
        let clock = FrameTestClock(3_000)
        let attempt = UUID()
        let gate = NativeMediaSampleGate(attempt: attempt, nowMs: { clock.now })
        XCTAssertTrue(gate.authorize(untilMs: 3_100))
        let processor = FrameAdmissionProcessor(gate: gate, attempt: attempt)
        XCTAssertTrue(processor.reserve(timeStampNs: 3_000_000_000, capturedAtMs: 3_000,
                                        authorizationRevision: gate.revision))
        clock.set(3_301)
        XCTAssertNil(processor.process(frame: try frame(atMs: 3_000)))
    }
}
