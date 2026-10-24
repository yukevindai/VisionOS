import Foundation

public enum GuideTrackingState: String, Sendable { case visible, tracked, lost, predicted }
public enum GuideDirection: String, Sendable { case left, right, up, down, forward, backward, stop }
public enum GuideReason: String, Sendable {
    case measured, unavailable, hazardStop, generationMismatch, staleFrame, objectUnverified
    case missingMeasurement, invalidGeometry, unmeasuredDepth, withinTolerance, expired
}
public struct GuideCue: Equatable, Sendable {
    public let direction: GuideDirection
    public let reason: GuideReason
    public let delta: GuideVector3?
    public let distance: Double?
    public let expiresAtMs: Int64?
    public let sequence: UInt64
    /// A measured alignment dwell only; never a press, grasp, passage or task completion.
    public let onTarget: Bool
}

/// Port of the teammate's pure guidance_core/guidance reference, with stricter measured-depth gates.
public struct GuideController: Sendable {
    public static let toleranceM = 0.025
    public static let maximumFrameAgeMs: Int64 = 300
    public static let cueHoldMs: Int64 = 180
    private var axis = 0
    private var sequence: UInt64 = 0
    private var generation: UInt64?
    private var dwellStart: Int64?
    private var lastObservation: Int64?
    private var event = GuideCue(direction: .stop, reason: .unavailable, delta: nil, distance: nil,
                                 expiresAtMs: nil, sequence: 0, onTarget: false)
    public init() {}

    public mutating func reset() {
        axis = 0; generation = nil; dwellStart = nil; lastObservation = nil
        sequence = sequence == UInt64.max ? sequence : sequence + 1
        event = stopped(.unavailable)
    }

    public func current(nowMs: Int64) -> GuideCue {
        if let expiry = event.expiresAtMs, nowMs >= expiry { return stopped(.expired) }
        return event
    }

    public mutating func update(target: GuideMeasurement?, finger: GuideMeasurement?,
                                targetState: GuideTrackingState, observedAtMs: Int64, nowMs: Int64,
                                generation: UInt64, evidenceGeneration: UInt64, hazard: Bool = false) -> GuideCue {
        sequence = sequence == UInt64.max ? sequence : sequence + 1
        if self.generation != generation {
            dwellStart = nil; lastObservation = nil; axis = 0
            event = stopped(.unavailable)
            self.generation = generation
        }
        func rejected(_ reason: GuideReason) -> GuideCue { stopped(reason) }
        let failure: GuideReason?
        if hazard { failure = .hazardStop }
        else if generation != evidenceGeneration { failure = .generationMismatch }
        else if observedAtMs < 0 || nowMs < observedAtMs || nowMs-observedAtMs >= Self.maximumFrameAgeMs {
            failure = .staleFrame
        } else if targetState != .visible && targetState != .tracked { failure = .objectUnverified }
        else if target == nil || finger == nil { failure = .missingMeasurement }
        else { failure = nil }
        if let failure {
            dwellStart = nil; lastObservation = nil; event = rejected(failure); return event
        }
        guard let target, let finger else { return stopped(.missingMeasurement) }
        guard target.source == .measuredDepth, finger.source == .measuredDepth else {
            dwellStart = nil; lastObservation = nil; event = stopped(.unmeasuredDepth); return event
        }
        guard [target, finger].allSatisfy({
            $0.xyz.isFinite && $0.xyz.z > 0 && $0.sigma.isFinite && $0.sigma > 0 && $0.support >= 3
        }) else {
            dwellStart = nil; lastObservation = nil; event = stopped(.invalidGeometry); return event
        }
        let delta = GuideVector3(x: target.xyz.x-finger.xyz.x,
                                 y: target.xyz.y-finger.xyz.y, z: target.xyz.z-finger.xyz.z)
        guard delta.isFinite, delta.length.isFinite else {
            dwellStart = nil; lastObservation = nil; event = stopped(.invalidGeometry); return event
        }
        let magnitudes = delta.components.map(abs)
        var strongest = 0
        for index in 1..<3 where magnitudes[index] > magnitudes[strongest] { strongest = index }
        if event.direction == .stop || magnitudes[axis] < Self.toleranceM ||
            magnitudes[strongest] > 1.25 * magnitudes[axis] { axis = strongest }
        let aligned = magnitudes[axis] < Self.toleranceM
        if let lastObservation, observedAtMs < lastObservation || observedAtMs-lastObservation > Self.maximumFrameAgeMs {
            dwellStart = nil
        }
        if aligned {
            if dwellStart == nil { dwellStart = observedAtMs }
        } else { dwellStart = nil }
        lastObservation = observedAtMs
        let onTarget = aligned && dwellStart.map { observedAtMs >= $0 && observedAtMs-$0 >= 300 } == true
        let directions: [(GuideDirection, GuideDirection)] = [(.right, .left), (.down, .up), (.forward, .backward)]
        let direction = aligned ? GuideDirection.stop : (delta.components[axis] > 0 ? directions[axis].0 : directions[axis].1)
        let frameExpiry = observedAtMs.addingReportingOverflow(Self.maximumFrameAgeMs)
        let holdExpiry = nowMs.addingReportingOverflow(Self.cueHoldMs)
        let expires = min(frameExpiry.overflow ? Int64.max : frameExpiry.partialValue,
                          holdExpiry.overflow ? Int64.max : holdExpiry.partialValue)
        event = GuideCue(direction: direction, reason: aligned ? .withinTolerance : .measured,
                         delta: delta, distance: delta.length, expiresAtMs: expires,
                         sequence: sequence, onTarget: onTarget)
        return event
    }

    private func stopped(_ reason: GuideReason) -> GuideCue {
        GuideCue(direction: .stop, reason: reason, delta: nil, distance: nil,
                 expiresAtMs: nil, sequence: sequence, onTarget: false)
    }
}
