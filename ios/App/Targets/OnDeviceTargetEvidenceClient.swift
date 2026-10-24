import CoreML
import Foundation
import ImageIO
import Tasks
import UIKit
import Vision

struct OnDeviceObjectObservation: Equatable, Sendable {
    let identifier: String
    let confidence: Float
    /// Vision's normalized bottom-left coordinates, before the adapter maps them.
    let boundingBox: CGRect
}

protocol OnDeviceObjectDetecting: Sendable {
    func detect(image: Data) async throws -> [OnDeviceObjectObservation]
}

enum OnDeviceTargetError: Error, Equatable, LocalizedError {
    case modelUnavailable, unsupportedObject, ambiguous, notFound, inferenceBusy
    var errorDescription: String? {
        switch self {
        case .modelUnavailable: return "The on-device object model is unavailable in this build."
        case .unsupportedObject: return "On-device recognition supports cups or cans, bottles, and bowls."
        case .ambiguous: return "More than one matching object is visible. Point at one and try again."
        case .notFound: return "I could not reliably find that object. Reposition the phone and try again."
        case .inferenceBusy: return "The previous object read is still finishing. Try again."
        }
    }
}

/// The teammate's YOLO11s model, converted with its original COCO class labels and NMS.
/// A separate actor keeps synchronous Vision/Core ML work off the main actor.
actor VisionObjectDetector: OnDeviceObjectDetecting {
    private let modelURL: URL?
    private var model: VNCoreMLModel?
    init(modelURL: URL? = Bundle.main.url(forResource: "yolo11s", withExtension: "mlmodelc", subdirectory: "Models")) {
        self.modelURL = modelURL
    }

    func detect(image: Data) async throws -> [OnDeviceObjectObservation] {
        try Task.checkCancellation()
        if model == nil {
            guard let modelURL else { throw OnDeviceTargetError.modelUnavailable }
            let configuration = MLModelConfiguration()
            configuration.computeUnits = .all
            model = try VNCoreMLModel(for: MLModel(contentsOf: modelURL, configuration: configuration))
        }
        guard let model else { throw OnDeviceTargetError.modelUnavailable }
        let request = VNCoreMLRequest(model: model)
        request.imageCropAndScaleOption = .scaleFill
        return try await withTaskCancellationHandler {
            try VNImageRequestHandler(data: image, orientation: .up).perform([request])
            try Task.checkCancellation()
            guard let results = request.results as? [VNRecognizedObjectObservation] else {
                throw OnDeviceTargetError.modelUnavailable
            }
            return results.compactMap { observation in
                guard let label = observation.labels.first else { return nil }
                return OnDeviceObjectObservation(identifier: label.identifier,
                                                  confidence: label.confidence, boundingBox: observation.boundingBox)
            }
        } onCancel: { request.cancel() }
    }
}

@MainActor
final class OnDeviceTargetEvidenceClient: FrameTargetEvidenceClient {
    private let detector: any OnDeviceObjectDetecting
    private let clock: any AppClock
    private let fallback: (any TargetEvidenceClient)?
    private var context: SessionContext?
    private var cancelledContext: SessionContext?
    private var latest: FrameSnapshot?
    private var epoch = UUID()
    private var work: [UUID: Task<[OnDeviceObjectObservation], Error>] = [:]

    init(clock: any AppClock, detector: any OnDeviceObjectDetecting = VisionObjectDetector(),
         fallback: (any TargetEvidenceClient)? = nil) {
        self.clock = clock
        self.detector = detector
        self.fallback = fallback
    }

    func updateContext(_ context: SessionContext) {
        guard self.context != context else { return }
        if let cancelledContext, cancelledContext.sessionID == context.sessionID,
           context.generation <= cancelledContext.generation { return }
        if let current = self.context, current.sessionID == context.sessionID,
           context.generation < current.generation { return }
        invalidate()
        self.context = context
        (fallback as? any FrameTargetEvidenceClient)?.updateContext(context)
    }

    func receive(_ frame: FrameSnapshot, capturedAtUTCms: Int64) {
        guard frame.context == context, !frame.frameID.isEmpty, !frame.geometryID.isEmpty,
              fresh(frame), imageData(frame) != nil else { return }
        if let latest {
            guard frame.capturedAtMs >= latest.capturedAtMs else { return }
            if latest.geometryID != frame.geometryID { invalidate() }
        }
        latest = frame
        (fallback as? any FrameTargetEvidenceClient)?.receive(frame, capturedAtUTCms: capturedAtUTCms)
    }

    func resolve(_ request: RequestContext, query: String, kind: TaskKind) async throws -> ResolvedTarget {
        guard kind == .object else {
            return try await delegated(request) { try await $0.resolve(request, query: query, kind: kind) }
        }
        let wanted: String
        do { wanted = try Self.objectClass(query) }
        catch OnDeviceTargetError.unsupportedObject where fallback != nil {
            return try await delegated(request) { try await $0.resolve(request, query: query, kind: kind) }
        }
        let current = try validate(request)
        guard let frame = latest, let data = imageData(frame) else { throw R3EvidenceError.missingFrame }
        guard fresh(frame) else { throw R3EvidenceError.staleFrame }
        guard work.isEmpty else { throw OnDeviceTargetError.inferenceBusy }
        let expectedEpoch = epoch
        let id = UUID()
        let detector = self.detector
        let operation = Task { try await detector.detect(image: data) }
        work[id] = operation
        defer { work.removeValue(forKey: id) }
        let observations = try await withTaskCancellationHandler {
            try await operation.value
        } onCancel: { operation.cancel() }
        try Task.checkCancellation()
        guard expectedEpoch == epoch, context == current else { throw R3EvidenceError.wrongContext }
        _ = try validate(request)
        return try Self.target(in: observations, wanted: wanted, frame: frame)
    }

    func readScreen(_ request: RequestContext) async throws -> TaskScreen {
        try await delegated(request) { try await $0.readScreen(request) }
    }
    func readMenu(_ request: RequestContext, query: String) async throws -> MenuContent {
        try await delegated(request) { try await $0.readMenu(request, query: query) }
    }
    func answerMenu(_ request: RequestContext, question: String, menu: MenuContent) async throws -> String {
        try await delegated(request) { try await $0.answerMenu(request, question: question, menu: menu) }
    }
    func verify(_ request: RequestContext, action: ActionKind, target: ResolvedTarget) async throws -> VerificationOutcome {
        guard fallback != nil else { _ = try validate(request); return .unavailable }
        return try await delegated(request) { try await $0.verify(request, action: action, target: target) }
    }

    func cancel(context: SessionContext) {
        guard let current = self.context, current.sessionID == context.sessionID,
              context.generation >= current.generation else { return }
        cancelledContext = context
        invalidate()
        self.context = nil
        fallback?.cancel(context: context)
    }

    private func invalidate() {
        epoch = UUID()
        latest = nil
        work.values.forEach { $0.cancel() }
    }
    private func validate(_ request: RequestContext) throws -> SessionContext {
        try Task.checkCancellation()
        guard let context, request.task.sessionID == context.sessionID,
              request.task.generation == context.generation else { throw R3EvidenceError.wrongContext }
        guard request.issuedAtMs <= clock.nowMs, request.expiresAtMs > clock.nowMs else { throw R3EvidenceError.staleFrame }
        return context
    }
    private func fresh(_ frame: FrameSnapshot) -> Bool {
        frame.trackingNormal && frame.transform.isValid && frame.capturedAtMs >= 0 &&
            frame.capturedAtMs <= clock.nowMs && frame.expiresAtMs > clock.nowMs && clock.nowMs - frame.capturedAtMs <= 300
    }
    private func imageData(_ frame: FrameSnapshot) -> Data? {
        let data: Data?
        if let encoded = frame.encodedImage { data = encoded }
        else if let preview = frame.preview, preview.imageOrientation == .up { data = preview.jpegData(compressionQuality: 0.85) }
        else { data = nil }
        guard let data, !data.isEmpty, data.count <= 512 * 1_024 else { return nil }
        return data
    }
    private func delegated<T>(_ request: RequestContext, operation: (any TargetEvidenceClient) async throws -> T) async throws -> T {
        let current = try validate(request)
        guard let fallback else { throw R3EvidenceError.unsupportedTask }
        let expectedEpoch = epoch
        let result = try await operation(fallback)
        try Task.checkCancellation()
        guard expectedEpoch == epoch, context == current else { throw R3EvidenceError.wrongContext }
        _ = try validate(request)
        return result
    }

    static func objectClass(_ query: String) throws -> String {
        var words = query.lowercased().components(separatedBy: CharacterSet.alphanumerics.inverted).filter { !$0.isEmpty }
        if words.starts(with: ["can", "you"]) { words.removeFirst(2) }
        let filler: Set<String> = ["a", "an", "the", "please", "find", "locate", "get", "pick", "up", "grab", "reach", "for",
                                   "help", "me", "my", "where", "is", "you", "see", "i", "want", "to", "that", "this"]
        let nouns: Set<String> = ["cup", "cups", "can", "cans", "bottle", "bottles", "bowl", "bowls"]
        guard words.allSatisfy({ filler.contains($0) || nouns.contains($0) }) else {
            throw OnDeviceTargetError.unsupportedObject
        }
        let classes = Set(words.compactMap { word -> String? in
            switch word {
            case "cup", "cups", "can", "cans": return "cup"
            case "bottle", "bottles": return "bottle"
            case "bowl", "bowls": return "bowl"
            default: return nil
            }
        })
        guard !classes.isEmpty else { throw OnDeviceTargetError.unsupportedObject }
        guard classes.count == 1, let value = classes.first else { throw OnDeviceTargetError.ambiguous }
        return value
    }

    static func target(in observations: [OnDeviceObjectObservation], wanted: String, frame: FrameSnapshot) throws -> ResolvedTarget {
        let matching = observations.filter { $0.identifier == wanted }
        guard !matching.isEmpty else { throw OnDeviceTargetError.notFound }
        guard matching.count == 1, let observation = matching.first else { throw OnDeviceTargetError.ambiguous }
        guard observation.confidence.isFinite, (0.6...1).contains(observation.confidence) else { throw OnDeviceTargetError.notFound }
        let box = observation.boundingBox
        let source = ImageRegion(x: box.minX, y: 1 - box.maxY, width: box.width, height: box.height)
        guard source.isValid, frame.transform.isValid else { throw OnDeviceTargetError.notFound }
        let region = ImageRegion(x: frame.transform.originX + source.x * frame.transform.scaleX,
                                 y: frame.transform.originY + source.y * frame.transform.scaleY,
                                 width: source.width * frame.transform.scaleX, height: source.height * frame.transform.scaleY)
        guard region.isValid else { throw OnDeviceTargetError.notFound }
        let caution = wanted == "cup" ? "The cup or can may contain liquid. Its temperature is unknown." :
            "The object's contents and weight are unknown."
        return ResolvedTarget(id: "\(frame.frameID):\(wanted)", kind: .object,
                              label: wanted == "cup" ? "cup or can" : wanted, region: region,
                              frameID: frame.frameID, safety: .ordinary, requiresApproach: false, caution: caution)
    }
}
