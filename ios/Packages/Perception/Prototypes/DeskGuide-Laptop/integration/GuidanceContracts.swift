// Independently authored integration proposal. Not linked to the app or BLE transport.
import Foundation

public enum VisionOSDevelopment {
    public enum TrackingState: String, Codable, Sendable {
        case observed, predicted, lost
    }

    public enum DepthSource: String, Codable, Sendable {
        case lidar, lidarSupportedFusion, sizeEstimated, unavailable
    }

    public enum GuidanceState: String, Codable, Sendable {
        case active, stop
    }

    public enum AxisDirection: String, Codable, Sendable {
        case left, right, up, down, forward, back, aligned
    }

    public struct XYZ: Codable, Sendable {
        public let x: Double
        public let y: Double
        public let z: Double

        public init(x: Double, y: Double, z: Double) {
            self.x = x
            self.y = y
            self.z = z
        }
    }

    public struct PositionObservation: Codable, Sendable {
        public let xyzMeters: XYZ?
        public let tracking: TrackingState
        public let depthSource: DepthSource
        public let confidence: Double?
        public let confidenceSource: String?
        public let observedAtMonotonic: Double
        public let frameID: String
        public let coordinateFrameID: String

        public init(xyzMeters: XYZ?, tracking: TrackingState, depthSource: DepthSource,
                    confidence: Double?, confidenceSource: String?, observedAtMonotonic: Double,
                    frameID: String, coordinateFrameID: String) {
            self.xyzMeters = xyzMeters
            self.tracking = tracking
            self.depthSource = depthSource
            self.confidence = confidence
            self.confidenceSource = confidenceSource
            self.observedAtMonotonic = observedAtMonotonic
            self.frameID = frameID
            self.coordinateFrameID = coordinateFrameID
        }
    }

    public struct GuidanceObservation: Codable, Sendable {
        public let schemaVersion: Int
        public let sequence: UInt64
        public let taskGeneration: UInt64
        public let clockID: String
        public let hand: PositionObservation
        public let targetSurface: PositionObservation
        public let displacementMeters: XYZ?
        public let distanceMeters: Double?
        public let directions: [AxisDirection]
        public let state: GuidanceState
        public let stopReason: String?
        public let publishedAtMonotonic: Double
        public let expiresAtMonotonic: Double?
        public let captureAgeSeconds: Double?
        public let synchronizationUncertaintySeconds: Double?

        public init(sequence: UInt64, taskGeneration: UInt64, clockID: String,
                    hand: PositionObservation, targetSurface: PositionObservation,
                    displacementMeters: XYZ?, distanceMeters: Double?, directions: [AxisDirection],
                    state: GuidanceState = .stop, stopReason: String?,
                    publishedAtMonotonic: Double, expiresAtMonotonic: Double?,
                    captureAgeSeconds: Double?, synchronizationUncertaintySeconds: Double?) {
            self.schemaVersion = 1
            self.sequence = sequence
            self.taskGeneration = taskGeneration
            self.clockID = clockID
            self.hand = hand
            self.targetSurface = targetSurface
            self.displacementMeters = displacementMeters
            self.distanceMeters = distanceMeters
            self.directions = directions
            self.state = state
            self.stopReason = stopReason
            self.publishedAtMonotonic = publishedAtMonotonic
            self.expiresAtMonotonic = expiresAtMonotonic
            self.captureAgeSeconds = captureAgeSeconds
            self.synchronizationUncertaintySeconds = synchronizationUncertaintySeconds
        }
    }

    public protocol SpeechProvider: Sendable {
        func speak(_ text: String, taskGeneration: UInt64, expiresAtMonotonic: Double) async throws
        func cancel(taskGeneration: UInt64) async
    }

    public struct TargetIntent: Sendable {
        public let label: String?
        public let explanation: String

        public init(label: String?, explanation: String) {
            self.label = label
            self.explanation = explanation
        }
    }

    public protocol InterpretationProvider: Sendable {
        func interpret(_ text: String) async throws -> TargetIntent
    }

    // Implemented by R2 after agreement with R1/H1; this is not a BLE wire format.
    public protocol HapticOutput: Sendable {
        func releaseValidatedGuidance(_ observation: GuidanceObservation) async throws
        func allStop(taskGeneration: UInt64, reason: String) async
    }

    public protocol DataService: Sendable {
        func store(key: String, data: Data) async throws
    }
}
