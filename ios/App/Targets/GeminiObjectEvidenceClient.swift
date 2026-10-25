import Foundation
import Targets
import Tasks

enum GeminiObjectError: Error, Equatable, LocalizedError {
    case notConfigured, signInRequired, unavailable, invalidResponse, ambiguous, notFound, busy
    case identificationOnly(String)
    case answerUnavailable

    var errorDescription: String? {
        switch self {
        case .notConfigured: return "Online object recognition is not configured yet. Camera and local Stop remain available."
        case .signInRequired: return "Sign in through the image reader or friend tools before using online object recognition."
        case .unavailable: return "Online object recognition is unavailable. Please try again."
        case .invalidResponse: return "The object response could not be verified. Please try again."
        case .ambiguous: return "Several visible objects match that request, so I cannot safely select one for hand guidance."
        case .answerUnavailable: return "I could not obtain a reliable answer to that question from the current camera image."
        case .notFound: return "I could not identify that object in this image. Reposition the phone and try again."
        case .busy: return "The previous object request is still finishing. Please try again."
        case .identificationOnly(let label):
            return "Gemini identified \(label), but there is not enough verified evidence for hand guidance."
        }
    }
}

enum ObjectRequestIntent {
    static func isQuestion(_ query: String) -> Bool {
        let text = query.lowercased().replacingOccurrences(of: "’", with: "'").trimmingCharacters(in: .whitespacesAndNewlines)
        let actions = ["guide my hand", "guide me", "help me pick", "help me grab", "help me reach", "help me find"]
        if actions.contains(where: text.contains) { return false }
        if text.hasSuffix("?") { return true }
        return ["what", "where", "which", "who", "why", "when", "how", "is", "are", "am", "can", "could", "would", "will", "should", "do", "does", "did", "has", "have", "tell", "describe", "explain"].contains {
            text == $0 || text.hasPrefix($0 + " ") || text.hasPrefix($0 + "'")
        }
    }
}

@MainActor
protocol QuestionAnswering: AnyObject {
    func answer(_ request: RequestContext, question: String) async throws -> String
}

@MainActor
final class GeminiObjectEvidenceClient: FrameTargetEvidenceClient, QuestionAnswering {
    private let reader: (any R3ObjectReading)?
    private let clock: any AppClock
    private var context: SessionContext?
    private var cancelledContext: SessionContext?
    private var latest: (frame: FrameSnapshot, utc: Int64)?
    private var epoch = UUID()
    private var work: [UUID: Task<(GeminiObjectRecognition, FrameTransform), Error>] = [:]

    var isConfigured: Bool { reader != nil }
    var introduction: String {
        isConfigured ? "Ask a question about what is around you, or ask me to find an object. Say Stop to pause." :
            "Camera and voice are ready. Online object recognition is not configured yet. Say Stop to pause."
    }

    init(reader: (any R3ObjectReading)?, clock: any AppClock) {
        self.reader = reader; self.clock = clock
    }

    func updateContext(_ context: SessionContext) {
        guard self.context != context else { return }
        if let cancelledContext, cancelledContext.sessionID == context.sessionID,
           context.generation <= cancelledContext.generation { return }
        if let current = self.context, current.sessionID == context.sessionID,
           context.generation < current.generation { return }
        invalidate()
        self.context = context
    }

    func receive(_ frame: FrameSnapshot, capturedAtUTCms: Int64) {
        guard frame.context == context, !frame.frameID.isEmpty, !frame.geometryID.isEmpty,
              capturedAtUTCms >= 0, fresh(frame), let data = frame.encodedImage,
              !data.isEmpty, data.count <= 512 * 1_024 else { return }
        if let latest {
            guard frame.capturedAtMs >= latest.frame.capturedAtMs else { return }
            if latest.frame.geometryID != frame.geometryID { invalidate() }
        }
        latest = (frame, capturedAtUTCms)
    }

    func resolve(_ request: RequestContext, query: String, kind: TaskKind) async throws -> ResolvedTarget {
        guard kind == .object else { throw R3EvidenceError.unsupportedTask }
        let (result, transform) = try await recognize(request, query: query, answering: false)
        return try Self.target(result, transform: transform)
    }

    func answer(_ request: RequestContext, question: String) async throws -> String {
        do {
            let (result, _) = try await recognize(request, query: question, answering: true)
            guard result.status == .notFound, result.selectedObjectId == nil, result.screenModel.controls.isEmpty,
                  let answer = result.answer, !answer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  answer.utf16.count <= 2_000, !answer.contains("?"), !answer.contains("？"), !answer.contains("؟"),
                  !answer.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
                throw GeminiObjectError.answerUnavailable
            }
            return answer
        } catch is CancellationError { throw CancellationError() }
        catch GeminiObjectError.signInRequired { throw GeminiObjectError.signInRequired }
        catch GeminiObjectError.notConfigured { throw GeminiObjectError.notConfigured }
        catch { throw GeminiObjectError.answerUnavailable }
    }

    private func recognize(_ request: RequestContext, query: String, answering: Bool) async throws -> (GeminiObjectRecognition, FrameTransform) {
        let current = try validate(request)
        guard let reader else { throw GeminiObjectError.notConfigured }
        guard work.count < 8 else { throw GeminiObjectError.busy }
        let expectedEpoch = epoch
        let id = UUID()
        let operation = Task { [weak self] () throws -> (GeminiObjectRecognition, FrameTransform) in
            guard let self else { throw CancellationError() }
            // A new task changes generation before its next camera frame arrives.
            // Wait for that actual frame; never relabel an older frame as current.
            let source = try await self.waitForFrame(request, context: current, epoch: expectedEpoch)
            let result: GeminiObjectRecognition
            do {
                if answering {
                    guard let questionReader = reader as? any R3QuestionReading else { throw GeminiObjectError.unavailable }
                    result = try await questionReader.answerQuestion(image: source.frame.encodedImage!, query: query,
                        frameID: source.frame.frameID, capturedAtUTCms: source.utc, requestID: request.requestID)
                } else {
                result = try await reader.findObject(image: source.frame.encodedImage!, query: query,
                    frameID: source.frame.frameID, capturedAtUTCms: source.utc, requestID: request.requestID)
                }
            } catch is CancellationError { throw CancellationError() }
            catch NativeMediaTransportError.unauthenticated { throw GeminiObjectError.signInRequired }
            catch NativeMediaTransportError.invalidResponse { throw GeminiObjectError.invalidResponse }
            catch { throw GeminiObjectError.unavailable }
            try Task.checkCancellation()
            guard self.epoch == expectedEpoch, self.context == current else { throw R3EvidenceError.wrongContext }
            _ = try self.validate(request)
            try R3TargetMapping.validateRecognition(result.screenModel, frameID: source.frame.frameID,
                                                     capturedAtUTCms: source.utc)
            return (result, source.frame.transform)
        }
        work[id] = operation
        defer { work.removeValue(forKey: id) }
        return try await withTaskCancellationHandler {
            try await operation.value
        } onCancel: { operation.cancel() }
    }

    private func waitForFrame(_ request: RequestContext, context: SessionContext,
                              epoch: UUID) async throws -> (frame: FrameSnapshot, utc: Int64) {
        while true {
            _ = try validate(request)
            guard self.context == context, self.epoch == epoch else { throw R3EvidenceError.wrongContext }
            if let latest, fresh(latest.frame) { return latest }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
    }

    static func target(_ result: GeminiObjectRecognition, transform: FrameTransform) throws -> ResolvedTarget {
        let model = result.screenModel
        guard model.panelType == .other, !model.isPaymentScreen,
              model.evidence?.provider.lowercased() == "gemini" else { throw GeminiObjectError.invalidResponse }
        switch result.status {
        case .notFound: throw GeminiObjectError.notFound
        case .ambiguous: throw GeminiObjectError.ambiguous
        case .providerUnavailable: throw GeminiObjectError.unavailable
        case .selected: break
        }
        guard let id = result.selectedObjectId,
              model.controls.filter({ $0.id == id }).count == 1,
              let object = model.controls.first(where: { $0.id == id }), object.type == .other,
              !object.label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              object.label.utf16.count <= 160,
              !object.label.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }),
              object.legibility == .clear else { throw GeminiObjectError.invalidResponse }
        if let confidence = object.confidence {
            guard confidence.isFinite, (0...1).contains(confidence) else { throw GeminiObjectError.invalidResponse }
            guard confidence >= 0.6 else { throw GeminiObjectError.notFound }
        }
        guard object.safety == nil || object.safety == .ordinary else {
            throw GeminiObjectError.identificationOnly(object.label)
        }
        guard let box = object.region, box.isValid, transform.isValid else {
            throw GeminiObjectError.identificationOnly(object.label)
        }
        let region = ImageRegion(x: transform.originX + box.x * transform.scaleX,
                                 y: transform.originY + box.y * transform.scaleY,
                                 width: box.width * transform.scaleX, height: box.height * transform.scaleY)
        guard region.isValid else { throw GeminiObjectError.invalidResponse }
        return ResolvedTarget(id: object.id, kind: .object, label: object.label, region: region,
                              frameID: model.frameId, safety: .unassessedObject, requiresApproach: false,
                              caution: "Object recognition does not verify temperature, sharp edges, contents or weight.")
    }

    func readScreen(_ request: RequestContext) async throws -> TaskScreen { throw R3EvidenceError.unsupportedTask }
    func readMenu(_ request: RequestContext, query: String) async throws -> MenuContent { throw R3EvidenceError.unsupportedTask }
    func answerMenu(_ request: RequestContext, question: String, menu: MenuContent) async throws -> String {
        throw R3EvidenceError.unsupportedTask
    }
    func verify(_ request: RequestContext, action: ActionKind, target: ResolvedTarget) async throws -> VerificationOutcome {
        _ = try validate(request)
        return .unavailable
    }

    func cancel(context: SessionContext) {
        guard let current = self.context, current.sessionID == context.sessionID,
              context.generation >= current.generation else { return }
        cancelledContext = context
        invalidate()
        self.context = nil
    }
    private func invalidate() {
        epoch = UUID(); latest = nil
        work.values.forEach { $0.cancel() }
    }
    private func validate(_ request: RequestContext) throws -> SessionContext {
        try Task.checkCancellation()
        guard let context, request.task.sessionID == context.sessionID,
              request.task.generation == context.generation else { throw R3EvidenceError.wrongContext }
        guard request.issuedAtMs <= clock.nowMs, request.expiresAtMs > clock.nowMs else {
            throw R3EvidenceError.staleFrame
        }
        return context
    }
    private func fresh(_ frame: FrameSnapshot) -> Bool {
        frame.trackingNormal && frame.transform.isValid && frame.capturedAtMs >= 0 &&
            frame.capturedAtMs <= clock.nowMs && frame.expiresAtMs > clock.nowMs && clock.nowMs - frame.capturedAtMs <= 300
    }
}
