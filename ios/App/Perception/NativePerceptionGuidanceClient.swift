import ARKit
import AVFoundation
import CoreVideo
import Guidance
import HapticLink
import ImageIO
import Tasks
import UIKit
import Vision

struct NativePerceptionDiagnostics {
    let capturedAtMs: Int64
    let geometryID: String
    let trackingNormal: Bool
    let depthAvailable: Bool
    let handVisible: Bool
    let measuredHand: Bool
    let processingMs: Double
}

@MainActor
final class NativePerceptionGuidanceClient: NSObject, PerceptionGuidanceClient, NativeMediaCaptureClient, CameraPreviewSource, CameraTrackingSource {
    var onEvent: ((PerceptionEvent) -> Void)?
    var onTrackingState: ((CameraTrackingState) -> Void)?
    var onTrackingIssue: ((SessionContext, String) -> Void)?
    var onFrame: ((FrameSnapshot) -> Void)?
    var onPreview: ((CameraPreviewFrame) -> Void)?
    var onLocalCommand: ((SessionContext, LocalCommand) -> Void)?
    var onHapticRelease: ((ScheduledHapticRelease) -> Void)?
    var onDiagnostics: ((NativePerceptionDiagnostics) -> Void)?

    private let startAudio: (SessionContext) async throws -> SharedAudioInput
    private let stopAudio: (SessionContext) -> Void
    private let supportsOfflineStop: () -> Bool
    private let preferredHand: () -> GuidingHand
    private let onCue: (String, SessionContext, Int64) -> Void
    private let stopOutput: (SessionContext) -> Void
    private var session: ARSession?
    private var selectedCameraType: AVCaptureDevice.DeviceType?
    private var processor: NativePerceptionProcessor?
    private var context: SessionContext?
    private var startID = UUID()
    private var startInProgress = false
    private var captureReceiver: ((NativeMediaVideoFrame) -> Void)?
    private var attachedSessionID: String?
    private var controller = GuideController()
    private var guidance: (request: RequestContext, target: ResolvedTarget,
                           continuation: CheckedContinuation<TaskResponse, Error>)?
    private var lastObservation: NativePerceptionObservation?
    private var lastGeometryID: String?
    private var wasTracking = false
    private var watchdog: Task<Void, Never>?

    init(startAudio: @escaping (SessionContext) async throws -> SharedAudioInput,
         stopAudio: @escaping (SessionContext) -> Void,
         supportsOfflineStop: @escaping () -> Bool,
         preferredHand: @escaping () -> GuidingHand,
         onCue: @escaping (String, SessionContext, Int64) -> Void,
         stopOutput: @escaping (SessionContext) -> Void) {
        self.startAudio = startAudio; self.stopAudio = stopAudio
        self.supportsOfflineStop = supportsOfflineStop; self.preferredHand = preferredHand
        self.onCue = onCue; self.stopOutput = stopOutput
        super.init()
        NotificationCenter.default.addObserver(self, selector: #selector(backgrounded),
                                               name: UIApplication.didEnterBackgroundNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(orientationChanged),
                                               name: UIDevice.orientationDidChangeNotification, object: nil)
    }

    deinit {
        watchdog?.cancel()
        processor?.close()
        session?.pause()
        NotificationCenter.default.removeObserver(self)
    }

    func start(context: SessionContext, policy: CameraPolicy) async throws -> PerceptionReadiness {
        guard !startInProgress, session == nil else { throw NativePerceptionError.alreadyRunning }
        guard ARWorldTrackingConfiguration.isSupported else { throw NativePerceptionError.unsupportedDevice }
        startInProgress = true
        defer { startInProgress = false }
        let attempt = UUID()
        startID = attempt
        self.context = context
        do {
            switch AVCaptureDevice.authorizationStatus(for: .video) {
            case .authorized: break
            case .notDetermined:
                guard await AVCaptureDevice.requestAccess(for: .video) else { throw NativePerceptionError.cameraDenied }
            default: throw NativePerceptionError.cameraDenied
            }
            try Task.checkCancellation()
            guard startID == attempt, self.context == context,
                  UIApplication.shared.applicationState == .active else { throw CancellationError() }
            let audio = try await startAudio(context)
            try Task.checkCancellation()
            guard startID == attempt, self.context == context else { throw CancellationError() }
            let depthAvailable = ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)
            let mailbox = NativePerceptionObservationMailbox()
            let previewMailbox = NativeCameraPreviewMailbox()
            let trackingMailbox = CameraTrackingMailbox()
            let processor = NativePerceptionProcessor(context: context, rotation: currentRotation(),
                                                       preferredHand: preferredHand(), delivery: { [weak self] observation in
                guard mailbox.offer(observation) else { return }
                Task { @MainActor [weak self] in
                    guard let observation = mailbox.take() else { return }
                    self?.receive(observation, attempt: attempt)
                }
            }, previewDelivery: { [weak self] preview in
                guard previewMailbox.offer(preview) else { return }
                Task { @MainActor [weak self] in
                    guard let preview = previewMailbox.take() else { return }
                    self?.receivePreview(preview, attempt: attempt)
                }
            }, trackingDelivery: { [weak self] state in
                guard trackingMailbox.offer(state) else { return }
                Task { @MainActor [weak self] in
                    for state in trackingMailbox.take() { self?.receiveTracking(state, attempt: attempt) }
                }
            }, failure: { [weak self] reason in
                Task { @MainActor [weak self] in
                    guard let self, self.startID == attempt, let context = self.context else { return }
                    self.reportTrackingLoss(context, reason: reason)
                }
            })
            let session = ARSession()
            session.delegate = processor
            session.delegateQueue = DispatchQueue(label: "Reflex.NativePerception.Capture", qos: .userInitiated)
            let configuration = ARWorldTrackingConfiguration()
            configuration.frameSemantics = depthAvailable ? [.sceneDepth] : []
            configuration.planeDetection = []
            configuration.environmentTexturing = .none
            configuration.providesAudioData = false
            configuration.isAutoFocusEnabled = true
            let formats = ARWorldTrackingConfiguration.supportedVideoFormats
            guard let formatIndex = NativeRearCameraFormatPolicy.preferredIndex(in: formats.map {
                .init(position: $0.captureDevicePosition, deviceType: $0.captureDeviceType)
            }) else { throw NativePerceptionError.unsupportedDevice }
            configuration.videoFormat = formats[formatIndex]
            selectedCameraType = configuration.videoFormat.captureDeviceType
            self.processor = processor
            self.session = session
            session.run(configuration)
            startWatchdog(attempt: attempt)
            return PerceptionReadiness(camera: cameraCapabilities(geometryID: processor.geometryID, depth: depthAvailable),
                                       audioInput: audio, supportsOfflineStop: supportsOfflineStop(),
                                       supportsSpeechOnlyGuidance: depthAvailable)
        } catch {
            if startID == attempt { stop(context: context) }
            throw error
        }
    }

    func updateContext(_ context: SessionContext) {
        guard let current = self.context, current.sessionID == context.sessionID,
              context.generation >= current.generation else { return }
        if current != context {
            finishGuidance(throwing: CancellationError())
            controller.reset()
            lastObservation = nil
            stopOutput(current)
        }
        self.context = context
        processor?.updateContext(context, preferredHand: preferredHand())
    }

    func reacquire(_ request: RequestContext, target: ResolvedTarget) async throws -> PhysicalEvidence {
        guard valid(request), let processor else { throw NativePerceptionError.staleRequest }
        let attempt = startID
        let observation = try await processor.bind(target: target, request: request)
        try Task.checkCancellation()
        guard startID == attempt, valid(request), observation.context == context else { throw CancellationError() }
        let evidence = physical(observation, targetID: target.id)
        guard evidence.targetVisible, evidence.handVisible, evidence.trackingNormal,
              fresh(evidence.observedAtMs) else { throw NativePerceptionError.targetNotObserved }
        lastObservation = observation
        return evidence
    }

    func guide(_ request: RequestContext, mode: GuidanceMode, target: ResolvedTarget,
               hand: GuidingHand) async throws -> TaskResponse {
        guard mode == .hand else { throw NativePerceptionError.approachUnavailable }
        guard valid(request), supportsOfflineStop(), let lastObservation,
              lastObservation.targetID == target.id, lastObservation.hand == hand,
              fresh(lastObservation.capturedAtMs), lastObservation.target != nil,
              lastObservation.finger != nil else { throw NativePerceptionError.depthUnavailable }
        finishGuidance(throwing: CancellationError())
        controller.reset()
        return try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { continuation in
                guard valid(request), !Task.isCancelled else {
                    continuation.resume(throwing: CancellationError())
                    return
                }
                guidance = (request, target, continuation)
            }
        }, onCancel: { [weak self] in
            Task { @MainActor [weak self] in
                guard self?.guidance?.request == request else { return }
                self?.cancelGuidance(context: SessionContext(sessionID: request.task.sessionID,
                                                            generation: request.task.generation))
            }
        })
    }

    func authorizePassage(_ request: RequestContext, target: ResolvedTarget) async throws -> PhysicalEvidence {
        throw NativePerceptionError.approachUnavailable
    }

    func cancelGuidance(context: SessionContext) {
        guard self.context?.sessionID == context.sessionID else { return }
        finishGuidance(throwing: CancellationError())
        controller.reset()
        processor?.clearTarget()
        stopOutput(context)
    }

    func stop(context: SessionContext) {
        guard self.context?.sessionID == context.sessionID else { return }
        startID = UUID()
        cancelGuidance(context: context)
        watchdog?.cancel(); watchdog = nil
        processor?.close(); processor = nil
        session?.pause(); session?.delegate = nil; session = nil
        selectedCameraType = nil
        detach()
        self.context = nil
        lastObservation = nil; lastGeometryID = nil; wasTracking = false
        stopAudio(context)
    }

    func attach(localSessionID: String, receive: @escaping (NativeMediaVideoFrame) -> Void) {
        guard context?.sessionID == localSessionID, session != nil else { return }
        attachedSessionID = localSessionID; captureReceiver = receive
    }
    func detach() { attachedSessionID = nil; captureReceiver = nil }

    private func receivePreview(_ preview: CameraPreviewFrame, attempt: UUID) {
        guard startID == attempt, preview.context == context, let context,
              preview.geometryID == processor?.geometryID, fresh(preview.capturedAtMs) else { return }
        if lastGeometryID != nil, lastGeometryID != preview.geometryID {
            cancelGuidance(context: context)
            lastGeometryID = preview.geometryID
            onEvent?(.cameraChanged(context, cameraCapabilities(geometryID: preview.geometryID,
                depth: ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth))))
        }
        guard preview.context == self.context else { return }
        lastGeometryID = preview.geometryID
        onPreview?(preview)
    }

    private func receiveTracking(_ state: CameraTrackingState, attempt: UUID) {
        guard startID == attempt, state.context == context,
              state.geometryID == processor?.geometryID, state.isActionable(at: nowMs) else { return }
        onTrackingState?(state)
        if state.status == .normal {
            if !wasTracking { onEvent?(.recovered(state.context)) }
            wasTracking = true
        } else {
            let notify = wasTracking
            wasTracking = false
            if notify || guidance != nil {
                reportTrackingLoss(state.context, reason: "AR_STATE_" + state.status.rawValue)
            }
        }
    }

    private func reportTrackingLoss(_ context: SessionContext, reason: String) {
        onTrackingIssue?(context, reason)
        cancelGuidance(context: context)
        onEvent?(.trackingLost(context))
    }

    private func receive(_ observation: NativePerceptionObservation, attempt: UUID) {
        guard startID == attempt, observation.context == context, let context,
              observation.geometryID == processor?.geometryID, fresh(observation.capturedAtMs) else { return }
        if let lastGeometryID, lastGeometryID != observation.geometryID {
            cancelGuidance(context: context)
            self.lastGeometryID = observation.geometryID
            onEvent?(.cameraChanged(context, cameraCapabilities(geometryID: observation.geometryID,
                                                                 depth: observation.hasDepth)))
            return
        }
        lastGeometryID = observation.geometryID
        lastObservation = observation
        onDiagnostics?(NativePerceptionDiagnostics(capturedAtMs: observation.capturedAtMs,
                                                   geometryID: observation.geometryID,
                                                   trackingNormal: observation.trackingNormal,
                                                   depthAvailable: observation.hasDepth,
                                                   handVisible: observation.handVisible,
                                                   measuredHand: observation.finger != nil,
                                                   processingMs: observation.processingMs))
        if attachedSessionID == context.sessionID { captureReceiver?(observation.video) }
        if let snapshot = observation.snapshot { onFrame?(snapshot) }
        guard let guidance else { return }
        guard valid(guidance.request), observation.targetID == guidance.target.id,
              observation.trackingNormal, observation.targetVisible, observation.handVisible,
              supportsOfflineStop() else {
            stopOutput(context)
            let pending = self.guidance
            self.guidance = nil
            pending?.continuation.resume(returning: .guidance(mode: .hand, targetID: guidance.target.id,
                                                              status: .lost, evidence: physical(observation, targetID: guidance.target.id)))
            processor?.clearTarget(); controller.reset()
            return
        }
        let cue = controller.update(target: observation.target, finger: observation.finger, targetState: .tracked,
                                    observedAtMs: observation.capturedAtMs, nowMs: nowMs,
                                    generation: context.generation, evidenceGeneration: observation.context.generation)
        if cue.onTarget {
            stopOutput(context)
            self.guidance = nil
            guidance.continuation.resume(returning: .guidance(mode: .hand, targetID: guidance.target.id,
                                                              status: .onTarget, evidence: physical(observation, targetID: guidance.target.id)))
            controller.reset(); processor?.clearTarget()
        } else if cue.direction == .stop {
            stopOutput(context)
            if cue.reason != .withinTolerance {
                self.guidance = nil
                guidance.continuation.resume(returning: .guidance(mode: .hand, targetID: guidance.target.id,
                                                                  status: .lost, evidence: physical(observation, targetID: guidance.target.id)))
                controller.reset(); processor?.clearTarget()
            }
        } else if let expiresAt = cue.expiresAtMs {
            onCue(Self.words(cue.direction), context, expiresAt)
        }
    }

    private func physical(_ observation: NativePerceptionObservation, targetID: String) -> PhysicalEvidence {
        PhysicalEvidence(targetID: targetID, frameID: observation.frameID, observedAtMs: observation.capturedAtMs,
                         targetVisible: observation.targetID == targetID && observation.targetVisible,
                         handVisible: observation.handVisible, trackingNormal: observation.trackingNormal,
                         depthUsable: observation.target != nil && observation.finger != nil,
                         freeSpaceObserved: false, outputReady: supportsOfflineStop(),
                         distanceM: observation.target?.xyz.z)
    }

    private var nowMs: Int64 { Int64(ProcessInfo.processInfo.systemUptime * 1_000) }
    private func fresh(_ time: Int64) -> Bool { time >= 0 && time <= nowMs && nowMs - time < 300 }
    private func valid(_ request: RequestContext) -> Bool {
        context?.sessionID == request.task.sessionID && context?.generation == request.task.generation &&
        request.issuedAtMs <= nowMs && request.expiresAtMs > nowMs
    }
    private func finishGuidance(throwing error: Error) {
        let pending = guidance; guidance = nil
        pending?.continuation.resume(throwing: error)
    }
    private func startWatchdog(attempt: UUID) {
        watchdog?.cancel()
        watchdog = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 75_000_000)
                guard !Task.isCancelled, let self, self.startID == attempt, let context = self.context else { return }
                if self.guidance != nil && (self.lastObservation.map { !self.fresh($0.capturedAtMs) } ?? true) {
                    self.reportTrackingLoss(context, reason: "GUIDANCE_OBSERVATION_EXPIRED")
                }
            }
        }
    }
    private func cameraCapabilities(geometryID: String, depth: Bool) -> CameraCapabilities {
        let camera = NativeRearCameraFormatPolicy.description(for: selectedCameraType)
        let reasons = [camera.fallbackReason,
                       depth ? nil : "LiDAR depth is unavailable; metric hand guidance is disabled."]
            .compactMap { $0 }
        return CameraCapabilities(selectedLens: camera.lens, depthSupported: depth,
                           fallbackReason: reasons.isEmpty ? nil : reasons.joined(separator: " "),
                           geometryID: geometryID)
    }
    @objc private func backgrounded() {
        guard let context else { return }
        stop(context: context)
        onEvent?(.trackingLost(context))
    }
    @objc private func orientationChanged() { processor?.setRotation(currentRotation()) }
    private func currentRotation() -> NativeMediaVideoRotation {
        let orientation = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
            .first { $0.activationState == .foregroundActive }?.interfaceOrientation ?? .portrait
        switch orientation {
        case .landscapeRight: return .degrees0
        case .landscapeLeft: return .degrees180
        case .portraitUpsideDown: return .degrees270
        default: return .degrees90
        }
    }
    private static func words(_ direction: GuideDirection) -> String {
        switch direction {
        case .left: return "Move your hand left."
        case .right: return "Move your hand right."
        case .up: return "Move your hand up."
        case .down: return "Move your hand down."
        case .forward: return "Move your hand closer to the target."
        case .backward: return "Move your hand back."
        case .stop: return "Stop."
        }
    }
}

enum NativeRearCameraFormatPolicy {
    struct Format {
        let position: AVCaptureDevice.Position
        let deviceType: AVCaptureDevice.DeviceType
    }

    // Only pass the active AR configuration's supported formats, in ARKit quality order.
    // Selecting once before run keeps the same RGB/depth geometry throughout guidance.
    static func preferredIndex(in formats: [Format]) -> Int? {
        let rear = formats.indices.filter { formats[$0].position == .back }
        for lens in [AVCaptureDevice.DeviceType.builtInUltraWideCamera, .builtInWideAngleCamera] {
            if let index = rear.first(where: { formats[$0].deviceType == lens }) { return index }
        }
        return rear.first
    }

    static func description(for deviceType: AVCaptureDevice.DeviceType?) -> (lens: String, fallbackReason: String?) {
        switch deviceType {
        case .builtInUltraWideCamera:
            return ("Ultra-wide rear camera", nil)
        case .builtInWideAngleCamera:
            return ("Wide rear camera", "Ultra-wide is unavailable in this ARKit configuration; using the wide rear camera.")
        case .some(let type):
            return ("ARKit rear camera (\(type.rawValue))",
                    "Ultra-wide and wide formats are unavailable; using ARKit's preferred rear format.")
        case .none:
            return ("Rear camera unavailable", "No supported rear-camera format is selected.")
        }
    }
}

enum NativePerceptionError: LocalizedError {
    case alreadyRunning, unsupportedDevice, cameraDenied, staleRequest, targetNotObserved, depthUnavailable, approachUnavailable
    var errorDescription: String? {
        switch self {
        case .alreadyRunning: return "The camera is already starting or running."
        case .unsupportedDevice: return "Native perception requires an ARKit-capable iPhone."
        case .cameraDenied: return "Enable camera access in Settings to use assistance."
        case .staleRequest: return "The task changed or its camera request expired."
        case .targetNotObserved: return "The target and guiding hand could not be located in the current camera image."
        case .depthUnavailable: return "Measured depth and a visible hand are required for hand guidance."
        case .approachUnavailable: return "Approach and passage guidance need validated free-space and hazard detection. Stay in place and ask for help."
        }
    }
}

private struct NativePerceptionObservation {
    let context: SessionContext
    let frameID: String
    let geometryID: String
    let capturedAtMs: Int64
    let trackingNormal: Bool
    let hasDepth: Bool
    let targetID: String?
    let targetVisible: Bool
    let handVisible: Bool
    let hand: GuidingHand
    let target: GuideMeasurement?
    let finger: GuideMeasurement?
    let video: NativeMediaVideoFrame
    let snapshot: FrameSnapshot?
    let processingMs: Double
}

/// Capture offers one latest frame; Vision and JPEG work run on a separate serial queue.
/// Historical frames are bounded JPEGs that exactly match the images sent to R3.
private final class NativePerceptionProcessor: NSObject, ARSessionDelegate, @unchecked Sendable {
    private struct Capture {
        let frame: ARFrame
        let context: SessionContext
        let rotation: NativeMediaVideoRotation
        let hand: GuidingHand
        let geometryID: String
    }
    private struct StoredFrame {
        let id: String
        let jpeg: Data
        let context: SessionContext
        let geometryID: String
        let capturedAtMs: Int64
    }
    private let lock = NSLock()
    private let queue = DispatchQueue(label: "Reflex.NativePerception.Vision", qos: .userInitiated)
    private let delivery: (NativePerceptionObservation) -> Void
    private let failure: (String) -> Void
    private let previewPipeline: NativeCameraPreviewPipeline
    private var capturedDimensions: CGSize?
    private let trackingDelivery: (CameraTrackingState) -> Void
    private var open = true
    private var pending: Capture?
    private var processing = false
    private var context: SessionContext
    private var rotation: NativeMediaVideoRotation
    private var preferredHand: GuidingHand
    private let geometryPrefix = UUID().uuidString
    private var latestFrame: Capture?
    private var history: [StoredFrame] = []
    private var lastProcessedAt: TimeInterval = -.infinity
    private var lastPublishedAt: TimeInterval = -.infinity
    private var trackedTarget: ResolvedTarget?
    private var tracker: VNTrackObjectRequest?
    private var trackedBox: CGRect?
    private var imageDimensions: CGSize?
    private var geometryRevision: UInt64 = 0
    private var sequence = VNSequenceRequestHandler()

    init(context: SessionContext, rotation: NativeMediaVideoRotation, preferredHand: GuidingHand,
         delivery: @escaping (NativePerceptionObservation) -> Void,
         previewDelivery: @escaping (CameraPreviewFrame) -> Void,
         trackingDelivery: @escaping (CameraTrackingState) -> Void, failure: @escaping (String) -> Void) {
        self.context = context; self.rotation = rotation; self.preferredHand = preferredHand
        self.delivery = delivery; self.failure = failure; self.trackingDelivery = trackingDelivery
        self.previewPipeline = NativeCameraPreviewPipeline(deliver: previewDelivery)
    }

    var geometryID: String {
        lock.lock(); let rotation = self.rotation; lock.unlock()
        return geometry(rotation)
    }
    private func geometry(_ rotation: NativeMediaVideoRotation) -> String {
        lock.lock(); let revision = geometryRevision; lock.unlock()
        return "\(geometryPrefix):rear:\(NativePerceptionGeometry.imageOrientation(rotation).rawValue):\(revision)"
    }
    func updateContext(_ context: SessionContext, preferredHand: GuidingHand) {
        lock.lock()
        let changed = self.context != context
        self.context = context; self.preferredHand = preferredHand
        lock.unlock()
        if changed { clearTarget() }
    }
    func setRotation(_ rotation: NativeMediaVideoRotation) {
        lock.lock(); self.rotation = rotation; lock.unlock()
    }
    func clearTarget() {
        queue.async { [self] in trackedTarget = nil; tracker = nil; trackedBox = nil; sequence = VNSequenceRequestHandler() }
    }
    func close() {
        lock.lock(); open = false; pending = nil; lock.unlock()
        previewPipeline.close()
        queue.async { [self] in
            history.removeAll(); latestFrame = nil; trackedTarget = nil; tracker = nil
        }
    }

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        lock.lock()
        guard open else { lock.unlock(); return }
        let dimensions = CGSize(width: CVPixelBufferGetWidth(frame.capturedImage),
                                height: CVPixelBufferGetHeight(frame.capturedImage))
        if let capturedDimensions, capturedDimensions != dimensions { geometryRevision &+= 1 }
        capturedDimensions = dimensions
        let context = self.context, rotation = self.rotation
        let geometryID = "\(geometryPrefix):rear:\(NativePerceptionGeometry.imageOrientation(rotation).rawValue):\(geometryRevision)"
        pending = Capture(frame: frame, context: context, rotation: rotation, hand: preferredHand, geometryID: geometryID)
        let schedule = !processing
        processing = true
        lock.unlock()
        if frame.timestamp.isFinite, frame.timestamp >= 0, frame.timestamp < Double(Int64.max) / 1_000 {
            previewPipeline.offer(.init(context: context, geometryID: geometryID,
                video: .init(localSessionID: context.sessionID, pixelBuffer: frame.capturedImage,
                             capturedAtMs: Int64(frame.timestamp * 1_000), rotation: rotation)))
            trackingDelivery(.init(context: context, geometryID: geometryID,
                capturedAtMs: Int64(frame.timestamp * 1_000), status: Self.trackingStatus(frame.camera.trackingState)))
        }
        if schedule { queue.async { [self] in drain() } }
    }
    func sessionWasInterrupted(_ session: ARSession) { failure("AR_SESSION_INTERRUPTED") }
    func session(_ session: ARSession, didFailWithError error: Error) { failure("AR_SESSION_FAILED") }

    private static func trackingStatus(_ state: ARCamera.TrackingState) -> CameraTrackingState.Status {
        switch state {
        case .normal: return .normal
        case .notAvailable: return .unavailable
        case .limited(let reason):
            switch reason {
            case .initializing: return .initializing
            case .excessiveMotion: return .excessiveMotion
            case .insufficientFeatures: return .insufficientFeatures
            case .relocalizing: return .relocalizing
            @unknown default: return .unavailable
            }
        }
    }

    private func drain() {
        lock.lock()
        guard open, let capture = pending else {
            processing = false; lock.unlock(); return
        }
        pending = nil
        lock.unlock()
        let frame = capture.frame
        if frame.timestamp.isFinite, frame.timestamp >= 0,
           frame.timestamp < Double(Int64.max) / 1_000,
           frame.timestamp - lastProcessedAt >= 0.075 {
            lastProcessedAt = frame.timestamp
            latestFrame = capture
            delivery(observe(capture, publish: true))
        }
        // Yield the worker between frames so bind/cancel commands cannot starve
        // when capture runs faster than Vision can process it.
        queue.async { [self] in drain() }
    }

    func bind(target: ResolvedTarget, request: RequestContext) async throws -> NativePerceptionObservation {
        try await withCheckedThrowingContinuation { continuation in
            queue.async { [self] in
                lock.lock()
                let allowed = open && context.sessionID == request.task.sessionID && context.generation == request.task.generation
                let context = self.context, rotation = self.rotation
                lock.unlock()
                let now = Int64(ProcessInfo.processInfo.systemUptime * 1_000)
                guard allowed, request.expiresAtMs > now, target.region.isValid,
                      let source = history.first(where: { $0.id == target.frameID }),
                      source.context == context, source.geometryID == geometry(rotation),
                      source.capturedAtMs <= now, now - source.capturedAtMs <= 5_000,
                      let current = latestFrame, current.context == context, current.geometryID == geometry(rotation),
                      current.frame.timestamp * 1_000 <= Double(now),
                      Double(now) - current.frame.timestamp * 1_000 < 300,
                      let imageSource = CGImageSourceCreateWithData(source.jpeg as CFData, nil),
                      let image = CGImageSourceCreateImageAtIndex(imageSource, 0, nil) else {
                    continuation.resume(throwing: NativePerceptionError.targetNotObserved)
                    return
                }
                let box = CGRect(x: target.region.x, y: 1 - target.region.y - target.region.height,
                                 width: target.region.width, height: target.region.height)
                let tracker = VNTrackObjectRequest(detectedObjectObservation: VNDetectedObjectObservation(boundingBox: box))
                tracker.trackingLevel = .accurate
                let sequence = VNSequenceRequestHandler()
                do {
                    try sequence.perform([tracker], on: image, orientation: .up)
                    guard let first = tracker.results?.first as? VNDetectedObjectObservation,
                          NativePerceptionGeometry.validTrackedBox(first.boundingBox, confidence: first.confidence) else {
                        throw NativePerceptionError.targetNotObserved
                    }
                    tracker.inputObservation = first
                    self.sequence = sequence
                    self.tracker = tracker
                    trackedBox = first.boundingBox
                    trackedTarget = target
                    let observation = observe(current, publish: false)
                    guard observation.targetVisible else { throw NativePerceptionError.targetNotObserved }
                    continuation.resume(returning: observation)
                } catch {
                    self.tracker = nil; trackedTarget = nil
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    private func observe(_ capture: Capture, publish: Bool) -> NativePerceptionObservation {
        let frame = capture.frame, context = capture.context, rotation = capture.rotation, hand = capture.hand
        let processingStarted = ProcessInfo.processInfo.systemUptime
        let captured = Int64(frame.timestamp * 1_000)
        let dimensions = CGSize(width: CVPixelBufferGetWidth(frame.capturedImage), height: CVPixelBufferGetHeight(frame.capturedImage))
        if let imageDimensions, imageDimensions != dimensions {
            tracker = nil; trackedTarget = nil; trackedBox = nil; history.removeAll()
        }
        imageDimensions = dimensions
        let frameID = "native-\(UUID().uuidString)"
        let geometryID = capture.geometryID
        let video = NativeMediaVideoFrame(localSessionID: context.sessionID, pixelBuffer: frame.capturedImage,
                                          capturedAtMs: captured, rotation: rotation)
        let normal: Bool
        if case .normal = frame.camera.trackingState { normal = true } else { normal = false }
        let depth = Self.depthPlane(frame)
        var box: CGRect?
        let targetID = trackedTarget?.id
        if normal, let tracker {
            do {
                try sequence.perform([tracker], on: frame.capturedImage,
                                     orientation: NativePerceptionGeometry.imageOrientation(rotation))
                if let result = tracker.results?.first as? VNDetectedObjectObservation,
                   NativePerceptionGeometry.validTrackedBox(result.boundingBox, confidence: result.confidence),
                   trackedBox.map({ NativePerceptionGeometry.validTrackedTransition(from: $0, to: result.boundingBox) }) == true {
                    tracker.inputObservation = result
                    trackedBox = result.boundingBox
                    box = CGRect(x: result.boundingBox.minX, y: 1 - result.boundingBox.maxY,
                                 width: result.boundingBox.width, height: result.boundingBox.height)
                }
            } catch { box = nil }
            if box == nil { self.tracker = nil; trackedTarget = nil }
        }
        var fingertip: CGPoint?
        var handBoxes: [CGRect] = []
        if normal {
            let request = VNDetectHumanHandPoseRequest()
            request.maximumHandCount = 2
            do {
                try VNImageRequestHandler(cvPixelBuffer: frame.capturedImage,
                                          orientation: NativePerceptionGeometry.imageOrientation(rotation)).perform([request])
                for observation in request.results ?? [] {
                    let points = (try? observation.recognizedPoints(.all))?.values.filter { $0.confidence >= 0.3 } ?? []
                    if points.count >= 6 {
                        let xs = points.map { $0.location.x }, ys = points.map { 1 - $0.location.y }
                        handBoxes.append(CGRect(x: xs.min()!, y: ys.min()!, width: xs.max()! - xs.min()!, height: ys.max()! - ys.min()!).insetBy(dx: -0.015, dy: -0.015))
                    }
                    if let tip = try? observation.recognizedPoint(.indexTip), tip.confidence >= 0.3 {
                        handBoxes.append(CGRect(x: tip.location.x - 0.02, y: 1 - tip.location.y - 0.02, width: 0.04, height: 0.04))
                    }
                }
                let wanted: VNChirality = hand == .left ? .left : .right
                let matches = (request.results ?? []).filter { $0.chirality == wanted }
                if matches.count == 1, let observation = matches.first,
                   let tip = try? observation.recognizedPoint(.indexTip), tip.confidence >= 0.7 {
                    fingertip = CGPoint(x: tip.location.x, y: 1 - tip.location.y)
                }
            } catch { fingertip = nil }
        }
        if let targetBox = box, NativePerceptionGeometry.handOccludes(target: targetBox, hands: handBoxes) {
            box = nil; tracker = nil; trackedTarget = nil; trackedBox = nil
        }
        let k = frame.camera.intrinsics
        let intrinsics = GuideIntrinsics(fx: Double(k.columns.0.x), fy: Double(k.columns.1.y),
                                         cx: Double(k.columns.2.x), cy: Double(k.columns.2.y))
        var targetMeasurement: GuideMeasurement?
        var fingerMeasurement: GuideMeasurement?
        if let depth {
            if let box {
                let rawBox = NativePerceptionGeometry.rawBox(box, rotation: rotation,
                                                             width: depth.imageWidth, height: depth.imageHeight)
                targetMeasurement = NativePerceptionGeometry.upright(
                    GuideGeometry.surface(box: rawBox, depth: depth, intrinsics: intrinsics), rotation: rotation)
            }
            if let fingertip {
                let raw = NativePerceptionGeometry.rawPoint(fingertip, rotation: rotation)
                fingerMeasurement = NativePerceptionGeometry.upright(
                    GuideGeometry.fingertip(at: GuidePoint2(x: Double(raw.x) * Double(depth.imageWidth),
                                                           y: Double(raw.y) * Double(depth.imageHeight)),
                                           depth: depth, intrinsics: intrinsics), rotation: rotation)
            }
        }
        var snapshot: FrameSnapshot?
        if publish, normal, frame.timestamp - lastPublishedAt >= 0.2,
           let encoded = try? TargetReaderImageEncoder.encode(video) {
            lastPublishedAt = frame.timestamp
            history.append(StoredFrame(id: frameID, jpeg: encoded.jpeg, context: context,
                                       geometryID: geometryID, capturedAtMs: captured))
            while history.count > 32 || history.reduce(0, { $0 + $1.jpeg.count }) > 8 * 1_024 * 1_024 {
                history.removeFirst()
            }
            history.removeAll { $0.context != context || $0.geometryID != geometryID }
            snapshot = FrameSnapshot(context: context, frameID: frameID, capturedAtMs: captured,
                                     expiresAtMs: captured + 300, geometryID: geometryID, transform: .identity,
                                     preview: encoded.image, encodedImage: encoded.jpeg,
                                     trackingNormal: normal, depthUsable: depth != nil)
        }
        return NativePerceptionObservation(context: context, frameID: frameID, geometryID: geometryID,
                                           capturedAtMs: captured, trackingNormal: normal, hasDepth: depth != nil,
                                           targetID: targetID, targetVisible: box != nil, handVisible: fingertip != nil,
                                           hand: hand, target: targetMeasurement, finger: fingerMeasurement,
                                           video: video, snapshot: snapshot,
                                           processingMs: (ProcessInfo.processInfo.systemUptime - processingStarted) * 1_000)
    }

    private static func depthPlane(_ frame: ARFrame) -> GuideDepthPlane? {
        guard let scene = frame.sceneDepth, let confidence = scene.confidenceMap else { return nil }
        let depth = scene.depthMap
        guard CVPixelBufferGetPixelFormatType(depth) == kCVPixelFormatType_DepthFloat32,
              CVPixelBufferGetPixelFormatType(confidence) == kCVPixelFormatType_OneComponent8 else { return nil }
        let width = CVPixelBufferGetWidth(depth), height = CVPixelBufferGetHeight(depth)
        guard width > 0, height > 0, width <= 1_024, height <= 1_024,
              CVPixelBufferGetWidth(confidence) == width, CVPixelBufferGetHeight(confidence) == height else { return nil }
        CVPixelBufferLockBaseAddress(depth, .readOnly)
        CVPixelBufferLockBaseAddress(confidence, .readOnly)
        defer {
            CVPixelBufferUnlockBaseAddress(confidence, .readOnly)
            CVPixelBufferUnlockBaseAddress(depth, .readOnly)
        }
        guard let depthBase = CVPixelBufferGetBaseAddress(depth),
              let confidenceBase = CVPixelBufferGetBaseAddress(confidence) else { return nil }
        var values: [Float] = [], confidenceValues: [UInt8] = []
        values.reserveCapacity(width * height); confidenceValues.reserveCapacity(width * height)
        for row in 0..<height {
            let pixels = depthBase.advanced(by: row * CVPixelBufferGetBytesPerRow(depth)).assumingMemoryBound(to: Float.self)
            let quality = confidenceBase.advanced(by: row * CVPixelBufferGetBytesPerRow(confidence)).assumingMemoryBound(to: UInt8.self)
            values.append(contentsOf: UnsafeBufferPointer(start: pixels, count: width))
            confidenceValues.append(contentsOf: UnsafeBufferPointer(start: quality, count: width))
        }
        return GuideDepthPlane(width: width, height: height,
                               imageWidth: CVPixelBufferGetWidth(frame.capturedImage),
                               imageHeight: CVPixelBufferGetHeight(frame.capturedImage),
                               values: values, confidence: confidenceValues)
    }
}


private final class NativePerceptionObservationMailbox: @unchecked Sendable {
    private let lock = NSLock()
    private var latest: NativePerceptionObservation?
    private var queued = false
    func offer(_ observation: NativePerceptionObservation) -> Bool {
        lock.lock(); defer { lock.unlock() }
        latest = observation
        guard !queued else { return false }
        queued = true
        return true
    }
    func take() -> NativePerceptionObservation? {
        lock.lock(); defer { lock.unlock() }
        defer { latest = nil; queued = false }
        return latest
    }
}
