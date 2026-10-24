import Foundation
import Tasks

/// R3-local integration boundary, not an implemented R1 sensor API.
/// Supplied by the capture owner and retained with the upload; never supplied by Gemini.
public struct TargetCapture: Equatable, Sendable {
    public let frameID: String
    public let capturedAtUTCms: Int64
    public let geometryID: String
    public let uprightImage: ImageInfo
    /// Uploaded upright crop in the full upright camera image. No rotation/mirroring is inferred.
    public let crop: BoundingBox
    public init(frameID: String, capturedAtUTCms: Int64, geometryID: String,
                uprightImage: ImageInfo, crop: BoundingBox) {
        self.frameID = frameID; self.capturedAtUTCms = capturedAtUTCms
        self.geometryID = geometryID; self.uprightImage = uprightImage; self.crop = crop
    }
}

public enum HandoffFailure: String, Error, Sendable {
    case unsafeControl, invalidSelection, geometryUnavailable, frameMismatch
    case reacquisitionRequired, staleEvidence, missingDepth, trackingLost, lowConfidence
}

/// Historical search hint. Contains no 3D pose and cannot authorize guidance.
public struct TargetHandoff: Sendable {
    public let target: ResolvedTarget
    public let capture: TargetCapture
    public let source: EvidenceSource
    fileprivate init(target: ResolvedTarget, capture: TargetCapture, source: EvidenceSource) {
        self.target = target; self.capture = capture; self.source = source
    }
}

public enum TargetHandoffAdapter {
    /// Safety must come from an independent R2/local policy. Unknown controls fail closed.
    /// Re-runs selection against the complete model so an ambiguous decoded reply cannot bypass it.
    public static func prepare(selection: TargetSelection, model: ScreenModel,
                               capture: TargetCapture, safety: ControlSafety) throws -> TargetHandoff {
        guard safety == .ordinary, !model.isPaymentScreen else { throw HandoffFailure.unsafeControl }
        guard selection.status == .selected, let selected = selection.target,
              selection == TargetSelector().select(selection.request, in: model),
              !selected.label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              selected.elementType == .button || selected.elementType == .screenControl else {
            throw HandoffFailure.invalidSelection
        }
        // Defense in depth; this deny list does not replace the independent safety policy.
        let words = Set(TextMatcher.tokens(selected.label))
        // "bell" is how the R3 prompt names a symbol-only alarm button; "stop"/"fire" cover emergency stops.
        guard words.isDisjoint(with: ["emergency", "alarm", "bell", "stop", "fire", "sos", "help", "pay", "payment",
                                     "checkout", "purchase", "authorize", "confirm", "buy"]) else {
            throw HandoffFailure.unsafeControl
        }
        guard capture.frameID == selected.frameId, capture.capturedAtUTCms == selected.capturedAtMs else {
            throw HandoffFailure.frameMismatch
        }
        guard !capture.geometryID.isEmpty, capture.crop.isValid,
              capture.uprightImage.widthPx > 0, capture.uprightImage.heightPx > 0,
              capture.uprightImage.orientationApplied,
              (1...8).contains(capture.uprightImage.exifOrientation),
              model.image == capture.uprightImage else { throw HandoffFailure.geometryUnavailable }
        let r = selected.imageRegion, c = capture.crop
        let region = ImageRegion(x: c.x + r.x * c.width, y: c.y + r.y * c.height,
                                 width: r.width * c.width, height: r.height * c.height)
        guard region.isValid else { throw HandoffFailure.geometryUnavailable }
        return TargetHandoff(target: ResolvedTarget(id: selected.targetId,
            kind: selected.elementType == .button ? .button : .screenControl,
            label: selected.label, region: region, frameID: selected.frameId, safety: safety),
            capture: capture, source: selected.source)
    }
}

/// R1 alone constructs this from local measurements. All positions are in one R1 coordinate frame.
/// A region center, estimated object size, or model reply must never populate positionM.
public struct R1TargetMeasurement: Sendable {
    public let evidence: PhysicalEvidence
    public let geometryID: String
    public let depthFrameID: String
    public let handFrameID: String
    public let depthObservedAtMs: Int64
    public let handObservedAtMs: Int64
    public let coordinateFrameID: String
    public let positionM: [Double]?
    /// Minimum calibrated confidence across target identity, hand, depth and tracking.
    /// R1 must report unknown confidence as non-finite; never substitute model confidence.
    public let confidence: Double
    public let identityRevalidated: Bool
    public let safety: ControlSafety
    public init(evidence: PhysicalEvidence, geometryID: String, depthFrameID: String,
                handFrameID: String, depthObservedAtMs: Int64, handObservedAtMs: Int64,
                coordinateFrameID: String, positionM: [Double]?, confidence: Double,
                identityRevalidated: Bool, safety: ControlSafety) {
        self.evidence = evidence; self.geometryID = geometryID; self.depthFrameID = depthFrameID
        self.handFrameID = handFrameID; self.depthObservedAtMs = depthObservedAtMs
        self.handObservedAtMs = handObservedAtMs
        self.coordinateFrameID = coordinateFrameID; self.positionM = positionM
        self.confidence = confidence; self.identityRevalidated = identityRevalidated; self.safety = safety
    }
}

public enum R1TargetDecision: Sendable {
    case accepted(R1TargetMeasurement)
    case reacquire(HandoffFailure)
    case rejected(HandoffFailure)
}

/// Stateless: call for EVERY fresh R1 update. An acceptance is not a durable guidance permit.
/// R2 still applies its request/session/generation/expiry checks; R1 alone produces cues.
public enum TargetEvidenceGate {
    /// nowMs and PhysicalEvidence.observedAtMs use the SAME local monotonic clock (R2 convention).
    /// Never compare them to the UTC timestamp in TargetCapture.
    public static func evaluate(_ handoff: TargetHandoff, measurement: R1TargetMeasurement?,
                                nowMs: Int64) -> R1TargetDecision {
        guard let m = measurement else { return .reacquire(.reacquisitionRequired) }
        let e = m.evidence
        guard m.safety == .ordinary else { return .rejected(.unsafeControl) }
        guard e.targetID == handoff.target.id, !e.frameID.isEmpty,
              e.frameID != handoff.capture.frameID, m.depthFrameID == e.frameID,
              m.handFrameID == e.frameID,
              m.depthObservedAtMs == e.observedAtMs, m.handObservedAtMs == e.observedAtMs,
              m.geometryID == handoff.capture.geometryID, !m.coordinateFrameID.isEmpty else {
            return .reacquire(.frameMismatch)
        }
        guard e.observedAtMs >= 0, nowMs >= e.observedAtMs,
              nowMs.subtractingReportingOverflow(e.observedAtMs).overflow == false,
              nowMs - e.observedAtMs <= 300 else { return .reacquire(.staleEvidence) }
        guard m.identityRevalidated else { return .reacquire(.reacquisitionRequired) }
        guard e.targetVisible, e.handVisible, e.trackingNormal, e.outputReady,
              !e.hazardPresent, !e.suspectedStep else { return .reacquire(.trackingLost) }
        guard e.depthUsable, let p = m.positionM, p.count == 3, p.allSatisfy(\.isFinite) else {
            return .reacquire(.missingDepth)
        }
        guard m.confidence.isFinite, (0.6...1).contains(m.confidence) else {
            return .reacquire(.lowConfidence)
        }
        return .accepted(m)
    }
}
