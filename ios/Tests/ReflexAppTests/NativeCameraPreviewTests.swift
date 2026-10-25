import CoreImage
import CoreVideo
import UIKit
import XCTest
@testable import Reflex

final class NativeCameraPreviewTests: XCTestCase {
    private func input(_ time: Int64, rotation: NativeMediaVideoRotation = .degrees0) throws -> NativeCameraPreviewInput {
        var buffer: CVPixelBuffer?
        XCTAssertEqual(CVPixelBufferCreate(kCFAllocatorDefault, 12, 8, kCVPixelFormatType_32BGRA,
            [kCVPixelBufferIOSurfacePropertiesKey: [:]] as CFDictionary, &buffer), kCVReturnSuccess)
        return .init(context: .init(sessionID: "preview-test", generation: 1), geometryID: "test-geometry",
                     video: .init(localSessionID: "preview-test", pixelBuffer: try XCTUnwrap(buffer),
                                  capturedAtMs: time, rotation: rotation))
    }

    func testSlowRenderingKeepsOnlyTheNewestPendingCapture() throws {
        let entered = expectation(description: "first render")
        let delivered = expectation(description: "two frames")
        delivered.expectedFulfillmentCount = 2
        let release = DispatchSemaphore(value: 0)
        let lock = NSLock()
        var captures: [Int64] = []
        let pipeline = NativeCameraPreviewPipeline(render: { frame in
            if frame.video.capturedAtMs == 0 {
                entered.fulfill()
                _ = release.wait(timeout: .now() + 3)
            }
            return UIImage()
        }, deliver: { frame in
            lock.lock(); captures.append(frame.capturedAtMs); lock.unlock()
            delivered.fulfill()
        })
        pipeline.offer(try input(0))
        wait(for: [entered], timeout: 2)
        for time in [66, 132, 198, 264] { pipeline.offer(try input(Int64(time))) }
        pipeline.offer(try input(198)) // A delayed older callback must not replace the newest capture.
        release.signal()
        wait(for: [delivered], timeout: 2)
        pipeline.close()
        lock.lock(); let result = captures; lock.unlock()
        XCTAssertEqual(result, [0, 264], "Slow consumers must never build an old-frame queue.")
    }

    func testClosingDropsInFlightAndPendingImages() throws {
        let entered = expectation(description: "rendering")
        let finished = expectation(description: "render finished")
        let delivered = expectation(description: "no delivery after close")
        delivered.isInverted = true
        let release = DispatchSemaphore(value: 0)
        let pipeline = NativeCameraPreviewPipeline(render: { _ in
            entered.fulfill()
            _ = release.wait(timeout: .now() + 3)
            finished.fulfill()
            return UIImage()
        }, deliver: { _ in delivered.fulfill() })
        pipeline.offer(try input(0))
        wait(for: [entered], timeout: 2)
        pipeline.offer(try input(66))
        pipeline.close()
        release.signal()
        wait(for: [finished, delivered], timeout: 0.2)
    }

    func testPreviewRotationPreservesUncroppedDimensions() throws {
        let context = CIContext()
        for rotation in [NativeMediaVideoRotation.degrees0, .degrees90, .degrees180, .degrees270] {
            let image = try XCTUnwrap(NativeCameraPreviewPipeline.render(try input(0, rotation: rotation), using: context))
            let portrait = rotation == .degrees90 || rotation == .degrees270
            XCTAssertEqual(image.size, portrait ? CGSize(width: 8, height: 12) : CGSize(width: 12, height: 8))
        }
    }

    func testMainActorMailboxCoalescesWithoutEnqueuingMoreDeliveries() {
        let mailbox = NativeCameraPreviewMailbox()
        let context = SessionContext(sessionID: "preview-test", generation: 1)
        XCTAssertTrue(mailbox.offer(.init(context: context, geometryID: "g", capturedAtMs: 0, image: UIImage())))
        XCTAssertFalse(mailbox.offer(.init(context: context, geometryID: "g", capturedAtMs: 66, image: UIImage())))
        XCTAssertFalse(mailbox.offer(.init(context: context, geometryID: "g", capturedAtMs: 132, image: UIImage())))
        XCTAssertEqual(mailbox.take()?.capturedAtMs, 132)
        XCTAssertNil(mailbox.take())
        XCTAssertTrue(mailbox.offer(.init(context: context, geometryID: "g", capturedAtMs: 198, image: UIImage())))
    }
}
