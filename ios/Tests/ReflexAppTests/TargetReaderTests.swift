import Combine
import CoreVideo
import Targets
import XCTest
@testable import Reflex

@MainActor
private final class ReaderIdentity: TargetReaderIdentity {
    let status = CurrentValueSubject<NativeIdentityStatus, Never>(.signedIn(accountID: "test-owner"))
    var statusUpdates: AnyPublisher<NativeIdentityStatus, Never> { status.eraseToAnyPublisher() }
    func loadStoredAccount() async {}
    func signIn() async throws { status.send(.signedIn(accountID: "test-owner")) }
    func signOut() async throws { status.send(.signedOut) }
}

@MainActor
private final class ReaderCamera: TargetReaderCamera {
    var onStopped: (() -> Void)?
    var receiver: ((NativeMediaVideoFrame) -> Void)?
    var sessionID = ""
    var starts = 0
    var stops = 0
    var suspendStart = false
    var continuation: CheckedContinuation<Void, Never>?
    func start(localSessionID: String) async throws {
        starts += 1
        sessionID = localSessionID
        if suspendStart { await withCheckedContinuation { continuation = $0 } }
    }
    func stop() { stops += 1; receiver = nil }
    func attach(localSessionID: String, receive: @escaping (NativeMediaVideoFrame) -> Void) { receiver = receive }
    func detach() { receiver = nil }
    func emit(at: Int64 = 1_000) throws {
        receiver?(NativeMediaVideoFrame(localSessionID: sessionID, pixelBuffer: try readerPixelBuffer(),
                                       capturedAtMs: at, rotation: .degrees90))
    }
}

@MainActor
private final class ReaderBackend: R3ScreenReading {
    var continuation: CheckedContinuation<Targets.ScreenModel, Error>?
    var frameID: String?
    var capturedAtMs: Int64?
    var calls = 0
    func readScreen(image: Data, frameID: String, capturedAtUTCms: Int64,
                    requestID: String) async throws -> Targets.ScreenModel {
        calls += 1
        self.frameID = frameID
        capturedAtMs = capturedAtUTCms
        return try await withCheckedThrowingContinuation { continuation = $0 }
    }
    func finish(simulated: Bool = false) {
        continuation?.resume(returning: Targets.ScreenModel(
            frameId: frameID!, capturedAtMs: capturedAtMs!, panelType: .elevatorPanel,
            source: simulated ? .simulated : .visionModel,
            controls: [.init(id: "5", label: "5", type: .button,
                             region: .init(x: 0.2, y: 0.2, width: 0.1, height: 0.1), legibility: .clear)],
            image: .init(widthPx: 80, heightPx: 160),
            evidence: .init(provider: "gemini", model: "test-model", live: !simulated)))
        continuation = nil
    }
}

private func readerPixelBuffer() throws -> CVPixelBuffer {
    var buffer: CVPixelBuffer?
    let status = CVPixelBufferCreate(kCFAllocatorDefault, 160, 80, kCVPixelFormatType_32BGRA,
                                    [kCVPixelBufferCGImageCompatibilityKey: true,
                                     kCVPixelBufferCGBitmapContextCompatibilityKey: true] as CFDictionary, &buffer)
    guard status == kCVReturnSuccess, let buffer else { throw NSError(domain: "TestBuffer", code: Int(status)) }
    CVPixelBufferLockBaseAddress(buffer, [])
    memset(CVPixelBufferGetBaseAddress(buffer), 255, CVPixelBufferGetDataSize(buffer))
    CVPixelBufferUnlockBaseAddress(buffer, [])
    return buffer
}

@MainActor
final class TargetReaderTests: XCTestCase {
    private func model(camera: ReaderCamera, identity: ReaderIdentity? = nil,
                       backend: ReaderBackend) -> TargetReaderModel {
        TargetReaderModel(camera: camera, identity: identity ?? ReaderIdentity(), reader: backend, ensureAccount: {},
                          monotonicMs: { 1_000 }, utcMs: { 10_000 })
    }

    func testSnapshotUsesCanonicalSelectorAndCaptureClock() async throws {
        let camera = ReaderCamera(), backend = ReaderBackend()
        let subject = model(camera: camera, backend: backend)
        subject.query = "find floor five"
        await subject.startCamera()
        try camera.emit(at: 900)
        subject.captureAndRead()
        for _ in 0..<100 where backend.continuation == nil { await Task.yield() }
        XCTAssertEqual(backend.capturedAtMs, 9_900)
        XCTAssertFalse(subject.cameraRunning)
        XCTAssertNotNil(subject.snapshot)
        backend.finish()
        for _ in 0..<100 where subject.busy { await Task.yield() }
        XCTAssertEqual(subject.selection?.target?.label, "5")
        XCTAssertTrue(subject.message.contains("snapshot"))
        subject.close()
    }

    func testCloseRejectsLateResultAndBlocksAnotherRequestUntilCompletion() async throws {
        let camera = ReaderCamera(), backend = ReaderBackend()
        let subject = model(camera: camera, backend: backend)
        subject.query = "5"
        await subject.startCamera()
        try camera.emit()
        subject.captureAndRead()
        for _ in 0..<100 where backend.continuation == nil { await Task.yield() }
        subject.close()
        XCTAssertFalse(subject.canStartCamera)
        await subject.startCamera()
        XCTAssertEqual(camera.starts, 1)
        backend.finish()
        for _ in 0..<100 where subject.busy { await Task.yield() }
        XCTAssertNil(subject.selection)
        XCTAssertNil(subject.snapshot)
        XCTAssertTrue(subject.canStartCamera)
    }

    func testCloseWhileCameraStartsCannotStopANewerCamera() async throws {
        let camera = ReaderCamera(), backend = ReaderBackend()
        camera.suspendStart = true
        let subject = model(camera: camera, backend: backend)
        let first = Task { await subject.startCamera() }
        for _ in 0..<100 where camera.continuation == nil { await Task.yield() }
        subject.close()
        await subject.startCamera()
        XCTAssertEqual(camera.starts, 1)
        XCTAssertFalse(subject.cameraRunning)
        camera.continuation?.resume()
        await first.value
        XCTAssertFalse(subject.cameraRunning)
        XCTAssertTrue(subject.canStartCamera)
        camera.suspendStart = false
        await subject.startCamera()
        XCTAssertEqual(camera.starts, 2)
        XCTAssertTrue(subject.cameraRunning)
        subject.close()
    }

    func testStaleFramesAndSimulatedRepliesCannotBecomeLiveRecognition() async throws {
        let camera = ReaderCamera(), backend = ReaderBackend()
        let subject = model(camera: camera, backend: backend)
        subject.query = "5"
        await subject.startCamera()
        try camera.emit(at: 699)
        subject.captureAndRead()
        XCTAssertEqual(backend.calls, 0)
        XCTAssertNil(subject.snapshot)
        try camera.emit()
        subject.captureAndRead()
        for _ in 0..<100 where backend.continuation == nil { await Task.yield() }
        backend.finish(simulated: true)
        for _ in 0..<100 where subject.busy { await Task.yield() }
        XCTAssertNil(subject.selection)
        subject.close()
    }

    func testImageEncoderAppliesRotationOnceAndBoundsUpload() throws {
        let buffer = try readerPixelBuffer()
        for (rotation, width, height) in [(NativeMediaVideoRotation.degrees0, 160, 80),
                                           (.degrees90, 80, 160), (.degrees180, 160, 80), (.degrees270, 80, 160)] {
            let encoded = try TargetReaderImageEncoder.encode(.init(localSessionID: "test", pixelBuffer: buffer,
                                                                    capturedAtMs: 1_000, rotation: rotation))
            XCTAssertEqual(encoded.image.cgImage?.width, width)
            XCTAssertEqual(encoded.image.cgImage?.height, height)
            XCTAssertEqual(encoded.image.imageOrientation, .up)
            XCTAssertLessThanOrEqual(encoded.jpeg.count, 512 * 1_024)
        }
    }
}
