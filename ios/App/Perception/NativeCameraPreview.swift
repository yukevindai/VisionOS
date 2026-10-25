import CoreImage
import UIKit

struct CameraPreviewFrame {
    let context: SessionContext
    let geometryID: String
    let capturedAtMs: Int64
    let image: UIImage
}

@MainActor
protocol CameraPreviewSource: AnyObject {
    var onPreview: ((CameraPreviewFrame) -> Void)? { get set }
}

struct NativeCameraPreviewInput {
    let context: SessionContext
    let geometryID: String
    let video: NativeMediaVideoFrame
}

/// Display pixels have their own bounded worker; Vision and JPEG encoding cannot stall it.
final class NativeCameraPreviewPipeline: @unchecked Sendable {
    typealias Renderer = (NativeCameraPreviewInput) -> UIImage?
    private let lock = NSLock()
    private let queue = DispatchQueue(label: "VisionOS.NativePerception.Preview", qos: .userInitiated)
    private let render: Renderer
    private let deliver: (CameraPreviewFrame) -> Void
    private var pending: NativeCameraPreviewInput?
    private var running = false
    private var open = true
    private var lastOfferedAtMs: Int64?

    init(render: Renderer? = nil, deliver: @escaping (CameraPreviewFrame) -> Void) {
        let context = CIContext(options: [.cacheIntermediates: false])
        self.render = render ?? { input in Self.render(input, using: context) }
        self.deliver = deliver
    }

    func offer(_ input: NativeCameraPreviewInput) {
        lock.lock()
        guard open, input.video.capturedAtMs >= 0,
              lastOfferedAtMs.map({ input.video.capturedAtMs - $0 >= 66 }) ?? true else {
            lock.unlock(); return
        }
        lastOfferedAtMs = input.video.capturedAtMs
        pending = input
        let schedule = !running
        running = true
        lock.unlock()
        if schedule { queue.async { [self] in drain() } }
    }

    func close() {
        lock.lock(); open = false; pending = nil; lock.unlock()
    }

    private func drain() {
        lock.lock()
        guard open, let input = pending else {
            running = false; lock.unlock(); return
        }
        pending = nil
        lock.unlock()
        if let image = render(input) {
            lock.lock(); let allowed = open; lock.unlock()
            if allowed {
                deliver(CameraPreviewFrame(context: input.context, geometryID: input.geometryID,
                                           capturedAtMs: input.video.capturedAtMs, image: image))
            }
        }
        queue.async { [self] in drain() }
    }

    static func render(_ input: NativeCameraPreviewInput, using context: CIContext) -> UIImage? {
        let upright = CIImage(cvPixelBuffer: input.video.pixelBuffer)
            .oriented(NativePerceptionGeometry.imageOrientation(input.video.rotation))
        let longest = max(upright.extent.width, upright.extent.height)
        guard longest.isFinite, longest > 0 else { return nil }
        let resized = upright.transformed(by: .init(scaleX: min(1, 960 / longest), y: min(1, 960 / longest)))
        guard let image = context.createCGImage(resized, from: resized.extent) else { return nil }
        return UIImage(cgImage: image)
    }
}

final class NativeCameraPreviewMailbox: @unchecked Sendable {
    private let lock = NSLock()
    private var latest: CameraPreviewFrame?
    private var queued = false

    func offer(_ frame: CameraPreviewFrame) -> Bool {
        lock.lock(); defer { lock.unlock() }
        latest = frame
        guard !queued else { return false }
        queued = true
        return true
    }

    func take() -> CameraPreviewFrame? {
        lock.lock(); defer { lock.unlock() }
        defer { latest = nil; queued = false }
        return latest
    }
}
