import Foundation
import Targets
import Tasks

@MainActor
protocol R3ScreenReading: AnyObject {
    func readScreen(image: Data, frameID: String, capturedAtUTCms: Int64,
                    requestID: String) async throws -> Targets.ScreenModel
}

@MainActor
protocol FrameTargetEvidenceClient: TargetEvidenceClient {
    func receive(_ frame: FrameSnapshot, capturedAtUTCms: Int64)
    func updateContext(_ context: SessionContext)
}

@MainActor
final class R3TargetEvidenceClient: FrameTargetEvidenceClient {
    private let reader: any R3ScreenReading
    private let clock: any AppClock
    private var context: SessionContext?
    private var latest: (frame: FrameSnapshot, utc: Int64)?
    private var epoch = UUID()
    private var cancelledContext: SessionContext?
    private var work: [UUID: Task<Targets.ScreenModel, Error>] = [:]

    init(reader: any R3ScreenReading, clock: any AppClock) {
        self.reader = reader
        self.clock = clock
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
        guard frame.context == context, capturedAtUTCms >= 0, fresh(frame),
              let data = frame.encodedImage, !data.isEmpty, data.count <= 512 * 1_024 else { return }
        latest = (frame, capturedAtUTCms)
    }

    func resolve(_ request: RequestContext, query: String, kind: TaskKind) async throws -> ResolvedTarget {
        let (model, transform) = try await read(request)
        return try R3TargetMapping.resolve(query, kind: kind, model: model, transform: transform)
    }

    func readScreen(_ request: RequestContext) async throws -> TaskScreen {
        let (model, transform) = try await read(request)
        return try R3TargetMapping.taskScreen(model, transform: transform)
    }

    func readMenu(_ request: RequestContext, query: String) async throws -> MenuContent {
        let (model, _) = try await read(request)
        return try R3TargetMapping.menu(model)
    }

    func answerMenu(_ request: RequestContext, question: String, menu: MenuContent) async throws -> String {
        throw R3EvidenceError.unsupportedTask
    }

    func verify(_ request: RequestContext, action: ActionKind, target: ResolvedTarget) async throws -> VerificationOutcome {
        .unavailable
    }

    func cancel(context: SessionContext) {
        guard let current = self.context, current.sessionID == context.sessionID,
              context.generation >= current.generation else { return }
        cancelledContext = context
        invalidate()
        self.context = nil
    }

    private func invalidate() {
        epoch = UUID()
        latest = nil
        work.values.forEach { $0.cancel() }
        // Keep cancelled operations counted until they actually complete.
    }

    private func fresh(_ frame: FrameSnapshot) -> Bool {
        let now = clock.nowMs
        return frame.trackingNormal && frame.transform.isValid && frame.capturedAtMs >= 0 &&
            frame.capturedAtMs <= now && frame.expiresAtMs > now && now - frame.capturedAtMs <= 300
    }

    private func read(_ request: RequestContext) async throws -> (Targets.ScreenModel, FrameTransform) {
        try Task.checkCancellation()
        guard let context, request.task.sessionID == context.sessionID,
              request.task.generation == context.generation else { throw R3EvidenceError.wrongContext }
        guard request.issuedAtMs <= clock.nowMs, request.expiresAtMs > clock.nowMs else {
            throw R3EvidenceError.staleFrame
        }
        guard let latest, let data = latest.frame.encodedImage else { throw R3EvidenceError.missingFrame }
        guard fresh(latest.frame) else { throw R3EvidenceError.staleFrame }
        guard work.count < 8 else { throw R3EvidenceError.providerUnavailable }
        let expectedEpoch = epoch
        let id = UUID()
        let reader = self.reader
        let operation = Task {
            try await reader.readScreen(image: data, frameID: latest.frame.frameID,
                                        capturedAtUTCms: latest.utc, requestID: request.requestID)
        }
        work[id] = operation
        defer { work.removeValue(forKey: id) }
        let model = try await withTaskCancellationHandler {
            try await operation.value
        } onCancel: {
            operation.cancel()
        }
        try Task.checkCancellation()
        guard expectedEpoch == epoch, self.context == context else { throw R3EvidenceError.wrongContext }
        guard request.expiresAtMs > clock.nowMs else { throw R3EvidenceError.staleFrame }
        try R3TargetMapping.validateRecognition(model, frameID: latest.frame.frameID, capturedAtUTCms: latest.utc)
        return (model, latest.frame.transform)
    }
}
