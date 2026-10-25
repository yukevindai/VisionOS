import ARKit
import AVFoundation
import Combine
import CoreVideo
import UIKit

/// Camera ownership for the explicitly selected video demo. Do not run alongside R1 capture.
/// This source supplies camera images only; it makes no guidance or depth-readiness claim.
@MainActor
final class NativeCameraFrameSource: NSObject, ObservableObject, NativeMediaCaptureClient {
    @Published private(set) var running = false
    @Published private(set) var errorMessage: String?
    var onStopped: (() -> Void)?

    private var session: ARSession?
    private var frameDelegate: CameraFrameDelegate?
    private var mailbox: CameraFrameMailbox?
    private var attempt: UUID?
    private var localSessionID: String?
    private var receiver: ((NativeMediaVideoFrame) -> Void)?
    private var attachedSessionID: String?
    private var interfaceOrientation: UIInterfaceOrientation = .portrait

    override init() {
        super.init()
        NotificationCenter.default.addObserver(self, selector: #selector(backgrounded),
                                               name: UIApplication.didEnterBackgroundNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(resignedActive),
                                               name: UIApplication.willResignActiveNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(orientationChanged),
                                               name: UIDevice.orientationDidChangeNotification, object: nil)
    }

    deinit {
        mailbox?.close()
        session?.pause()
        NotificationCenter.default.removeObserver(self)
    }

    func start(localSessionID: String) async throws {
        stop()
        errorMessage = nil
        guard !localSessionID.isEmpty else { throw CameraError.invalidSession }
        guard ARWorldTrackingConfiguration.isSupported else {
            errorMessage = CameraError.unsupported.localizedDescription
            throw CameraError.unsupported
        }
        let id = UUID()
        attempt = id
        do {
            switch AVCaptureDevice.authorizationStatus(for: .video) {
            case .authorized: break
            case .notDetermined:
                guard await AVCaptureDevice.requestAccess(for: .video) else { throw CameraError.permissionDenied }
            default: throw CameraError.permissionDenied
            }
            try Task.checkCancellation()
            guard attempt == id else { throw CancellationError() }
            guard UIApplication.shared.applicationState == .active else { throw CameraError.inactive }
            orientationChanged()
            let mailbox = CameraFrameMailbox(minimumTimestamp: ProcessInfo.processInfo.systemUptime,
                                              rotation: rotation(for: interfaceOrientation))
            let delegate = CameraFrameDelegate(mailbox: mailbox, delivery: { [weak self] in
                Task { @MainActor [weak self] in self?.deliver(attempt: id) }
            }, stopped: { [weak self] message in
                Task { @MainActor [weak self] in self?.unexpectedStop(attempt: id, message: message) }
            })
            let session = ARSession()
            session.delegate = delegate
            session.delegateQueue = DispatchQueue(label: "VisionOS.VideoDemo.Camera", qos: .userInitiated)
            self.localSessionID = localSessionID
            self.mailbox = mailbox
            self.frameDelegate = delegate
            self.session = session
            let configuration = ARWorldTrackingConfiguration()
            configuration.planeDetection = []
            configuration.frameSemantics = []
            configuration.environmentTexturing = .none
            configuration.providesAudioData = false
            configuration.isAutoFocusEnabled = true
            session.run(configuration)
            running = true
        } catch {
            if attempt == id {
                stop()
                if !(error is CancellationError) { errorMessage = error.localizedDescription }
            }
            throw error
        }
    }

    func stop() {
        // Invalidate before pausing ARKit; queued callbacks can no longer deliver a frame.
        attempt = nil
        mailbox?.close()
        detach()
        localSessionID = nil
        running = false
        session?.pause()
        session?.delegate = nil
        session = nil
        frameDelegate = nil
        mailbox = nil
    }

    func attach(localSessionID: String, receive: @escaping (NativeMediaVideoFrame) -> Void) {
        guard running, self.localSessionID == localSessionID else { return }
        attachedSessionID = localSessionID
        receiver = receive
    }

    func detach() {
        receiver = nil
        attachedSessionID = nil
    }

    func updateInterfaceOrientation(_ orientation: UIInterfaceOrientation) {
        guard orientation != .unknown else { return }
        interfaceOrientation = orientation
        mailbox?.setRotation(rotation(for: orientation))
    }

    private func rotation(for orientation: UIInterfaceOrientation) -> NativeMediaVideoRotation {
        switch orientation {
        case .portrait: return .degrees90
        case .portraitUpsideDown: return .degrees270
        case .landscapeLeft: return .degrees180
        case .landscapeRight: return .degrees0
        default: return .degrees90
        }
    }

    @objc private func orientationChanged() {
        if let scene = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene })
            .first(where: { $0.activationState == .foregroundActive }) {
            updateInterfaceOrientation(scene.interfaceOrientation)
        }
    }

    private func deliver(attempt id: UUID) {
        guard attempt == id, let mailbox else { return }
        // Taking the newest frame also releases the sole queued-delivery slot. At most
        // one more delivery can be queued while this synchronous receiver is executing.
        guard let frame = mailbox.take(), running, let localSessionID,
              attachedSessionID == localSessionID, let receiver else { return }
        receiver(NativeMediaVideoFrame(localSessionID: localSessionID, pixelBuffer: frame.pixelBuffer,
                                       capturedAtMs: frame.capturedAtMs, rotation: frame.rotation))
    }

    @objc private func backgrounded() {
        guard let attempt else { return }
        unexpectedStop(attempt: attempt, message: "Camera stopped because VisionOS entered the background. Start again to share video.")
    }

    @objc private func resignedActive() {
        // A first-time permission sheet also resigns active; no camera exists yet then.
        guard session != nil, let attempt else { return }
        unexpectedStop(attempt: attempt, message: "Camera stopped because VisionOS is no longer active. Start again to share video.")
    }

    private func unexpectedStop(attempt id: UUID, message: String) {
        guard attempt == id else { return }
        stop()
        errorMessage = message
        onStopped?()
    }

    private enum CameraError: LocalizedError {
        case unsupported, permissionDenied, inactive, invalidSession
        var errorDescription: String? {
            switch self {
            case .unsupported: return "Live camera requires an ARKit-capable iPhone. The simulator cannot provide this camera feed."
            case .permissionDenied: return "Camera access is required to share video. Enable it for VisionOS in Settings."
            case .inactive: return "Return to VisionOS and start again to share video."
            case .invalidSession: return "A new video session is required before starting the camera."
            }
        }
    }
}

/// The lock owns buffer retention and the single pending-delivery flag across ARKit/MainActor.
private final class CameraFrameMailbox: @unchecked Sendable {
    struct Frame {
        let pixelBuffer: CVPixelBuffer
        let capturedAtMs: Int64
        let rotation: NativeMediaVideoRotation
    }
    private let lock = NSLock()
    private let minimumTimestamp: TimeInterval
    private var rotation: NativeMediaVideoRotation
    private var open = true
    private var pending: Frame?
    private var deliveryQueued = false

    init(minimumTimestamp: TimeInterval, rotation: NativeMediaVideoRotation) {
        self.minimumTimestamp = minimumTimestamp
        self.rotation = rotation
    }

    func setRotation(_ rotation: NativeMediaVideoRotation) {
        lock.lock(); defer { lock.unlock() }
        self.rotation = rotation
    }

    func offer(_ frame: ARFrame) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard open, frame.timestamp.isFinite, frame.timestamp >= minimumTimestamp,
              frame.timestamp < Double(Int64.max) / 1_000 else { return false }
        pending = Frame(pixelBuffer: frame.capturedImage, capturedAtMs: Int64(frame.timestamp * 1_000), rotation: rotation)
        guard !deliveryQueued else { return false }
        deliveryQueued = true
        return true
    }

    func take() -> Frame? {
        lock.lock(); defer { lock.unlock() }
        defer { pending = nil; deliveryQueued = false }
        return open ? pending : nil
    }

    @discardableResult func close() -> Bool {
        lock.lock(); defer { lock.unlock() }
        let wasOpen = open
        open = false
        pending = nil
        return wasOpen
    }
}

private final class CameraFrameDelegate: NSObject, ARSessionDelegate, @unchecked Sendable {
    private let mailbox: CameraFrameMailbox
    private let delivery: @Sendable () -> Void
    private let stopped: @Sendable (String) -> Void

    init(mailbox: CameraFrameMailbox, delivery: @escaping @Sendable () -> Void,
         stopped: @escaping @Sendable (String) -> Void) {
        self.mailbox = mailbox
        self.delivery = delivery
        self.stopped = stopped
    }

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        if mailbox.offer(frame) { delivery() }
    }

    func sessionWasInterrupted(_ session: ARSession) {
        if mailbox.close() { stopped("Camera was interrupted. Start again to share video.") }
    }

    func session(_ session: ARSession, didFailWithError error: Error) {
        if mailbox.close() { stopped("Camera stopped: \(error.localizedDescription)") }
    }
}
