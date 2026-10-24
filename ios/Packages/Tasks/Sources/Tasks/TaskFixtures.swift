import Foundation

/// Explicitly SIMULATED values for previews and deterministic tests. Never use as live observations.
public enum TaskFixtures {
    public static func target(id: String = "simulated-button", kind: TargetKind = .button,
                              label: String = "SIMULATED Up", requiresApproach: Bool = false,
                              safety: ControlSafety = .ordinary, screenID: String? = nil,
                              frameID: String = "simulated-frame", expectedDestination: String? = nil,
                              doorAction: DoorAction? = nil, doorActionEvidence: Bool = false) -> ResolvedTarget {
        ResolvedTarget(id: id, kind: kind, label: label,
                       region: ImageRegion(x: 0.4, y: 0.4, width: 0.1, height: 0.1), frameID: frameID,
                       safety: safety, requiresApproach: requiresApproach, doorAction: doorAction,
                       doorActionEvidence: doorActionEvidence, sourceScreenID: screenID,
                       expectedDestination: expectedDestination)
    }

    public static func physical(targetID: String = "simulated-button", nowMs: Int64,
                                depthUsable: Bool = true, handVisible: Bool = true,
                                targetVisible: Bool = true, hazardPresent: Bool = false,
                                distanceM: Double? = 1, suspectedStep: Bool = false) -> PhysicalEvidence {
        PhysicalEvidence(targetID: targetID, frameID: "simulated-frame-\(nowMs)", observedAtMs: nowMs,
                         targetVisible: targetVisible, handVisible: handVisible, trackingNormal: true,
                         depthUsable: depthUsable, freeSpaceObserved: depthUsable, outputReady: true,
                         hazardPresent: hazardPresent, distanceM: distanceM, suspectedStep: suspectedStep)
    }

    public static func envelope(context: RequestContext, eventID: String, nowMs: Int64,
                                source: EventSource = .r1) -> EventEnvelope {
        EventEnvelope(eventID: eventID, context: context, monotonicMs: nowMs,
                      expiresAtMs: context.expiresAtMs, source: source)
    }
}
