import Foundation

public enum BandSide: String, Codable, CaseIterable, Hashable, Sendable { case left, right }

public enum HapticLinkError: Error, Equatable, Sendable {
    case unavailable(String), invalidConfiguration(String), malformedPacket(Int)
    case unsupportedVersion(UInt8), invalidPattern(UInt8), invalidBattery(UInt8)
    case invalidContext, generationOutOfRange, generationNotSynchronized, generationExhausted
    case sequenceExhausted(BandSide), invalidRelease(String), sideNotConfirmed
    case duplicatePeripheral, sideAlreadyAssigned(BandSide), linkNotReady(BandSide)
}

/// Field-by-field wire serialization. Firmware packet version is provided by H1.
public struct HapticCommand: Equatable, Sendable {
    public let version: UInt8
    public let sequence: UInt16
    public let generation: UInt16
    public let patternID: UInt8
    public let intensity: UInt8
    public let rateHz: UInt8
    public let ttlMs: UInt16

    public init(version: UInt8, sequence: UInt16, generation: UInt16, patternID: UInt8,
                intensity: UInt8, rateHz: UInt8, ttlMs: UInt16) throws {
        guard patternID == 0 || (1...9).contains(patternID) || patternID == 255 else {
            throw HapticLinkError.invalidPattern(patternID)
        }
        self.version = version; self.sequence = sequence; self.generation = generation
        self.patternID = patternID; self.intensity = intensity; self.rateHz = rateHz; self.ttlMs = ttlMs
    }

    public var data: Data {
        Data([version, UInt8(sequence & 0xff), UInt8(sequence >> 8),
              UInt8(generation & 0xff), UInt8(generation >> 8), patternID, intensity,
              rateHz, UInt8(ttlMs & 0xff), UInt8(ttlMs >> 8)])
    }

    public init(data: Data, expectedVersion: UInt8) throws {
        guard data.count == 10 else { throw HapticLinkError.malformedPacket(data.count) }
        let b = Array(data)
        guard b[0] == expectedVersion else { throw HapticLinkError.unsupportedVersion(b[0]) }
        try self.init(version: b[0], sequence: UInt16(b[1]) | UInt16(b[2]) << 8,
                      generation: UInt16(b[3]) | UInt16(b[4]) << 8, patternID: b[5],
                      intensity: b[6], rateHz: b[7], ttlMs: UInt16(b[8]) | UInt16(b[9]) << 8)
    }
}

public struct BandStatus: Equatable, Sendable {
    public let batteryPct: UInt8
    public let firmwareVersion: UInt8
    public let lastSequence: UInt16
    public let faults: UInt8

    /// Status byte 1 is firmware revision, not the command protocol version.
    public init(data: Data, supportedFirmwareVersions: Set<UInt8>) throws {
        guard data.count == 5 else { throw HapticLinkError.malformedPacket(data.count) }
        let b = Array(data)
        guard b[0] <= 100 else { throw HapticLinkError.invalidBattery(b[0]) }
        guard supportedFirmwareVersions.contains(b[1]) else { throw HapticLinkError.unsupportedVersion(b[1]) }
        batteryPct = b[0]; firmwareVersion = b[1]
        lastSequence = UInt16(b[2]) | UInt16(b[3]) << 8; faults = b[4]
    }
}

public struct PatternParameters: Equatable, Codable, Sendable {
    public let patternID: UInt8
    public let intensity: UInt8
    public let rateHz: UInt8
    public let ttlMs: UInt16
    public init(patternID: UInt8, intensity: UInt8, rateHz: UInt8, ttlMs: UInt16) {
        self.patternID = patternID; self.intensity = intensity; self.rateHz = rateHz; self.ttlMs = ttlMs
    }
}

public struct H1Contract: Equatable, Sendable {
    public let serviceUUID: UUID
    public let commandUUID: UUID
    public let statusUUID: UUID
    public let protocolVersion: UInt8
    public let supportedFirmwareVersions: Set<UInt8>
    public let stop: PatternParameters
    public let heartbeat: PatternParameters
    public let patterns: [UInt8: PatternParameters]
    public let identityDescription: String
    public let generationPolicyDescription: String
    public let supportsStoppedNotifications: Bool

    /// Populate only from H1's confirmed contract; this type provides no guessed hardware defaults.
    public init(serviceUUID: UUID, commandUUID: UUID, statusUUID: UUID, protocolVersion: UInt8,
                supportedFirmwareVersions: Set<UInt8>, stop: PatternParameters,
                heartbeat: PatternParameters, patterns: [UInt8: PatternParameters],
                identityDescription: String, generationPolicyDescription: String,
                supportsStoppedNotifications: Bool) throws {
        guard Set([serviceUUID, commandUUID, statusUUID]).count == 3,
              !supportedFirmwareVersions.isEmpty, stop.patternID == 0, heartbeat.patternID == 255,
              !identityDescription.isEmpty, !generationPolicyDescription.isEmpty,
              Set(patterns.keys) == Set(UInt8(1)...UInt8(9)),
              patterns.allSatisfy({ $0.key == $0.value.patternID && $0.value.ttlMs > 0 }),
              patterns[6]?.ttlMs == 600 else {
            throw HapticLinkError.invalidConfiguration("H1 UUIDs, all nine patterns (including 600 ms pattern 6), revisions, identity and generation policy are required")
        }
        self.serviceUUID = serviceUUID; self.commandUUID = commandUUID; self.statusUUID = statusUUID
        self.protocolVersion = protocolVersion; self.supportedFirmwareVersions = supportedFirmwareVersions
        self.stop = stop; self.heartbeat = heartbeat; self.patterns = patterns
        self.identityDescription = identityDescription; self.generationPolicyDescription = generationPolicyDescription
        self.supportsStoppedNotifications = supportsStoppedNotifications
    }
}

public enum HapticLinkConfiguration: Equatable, Sendable {
    case unavailable(reasons: [String])
    case confirmed(H1Contract)
    public static let unavailableH1 = HapticLinkConfiguration.unavailable(reasons: [
        "H1 service/characteristic UUIDs, packet version, pattern parameters, identity and generation handshake are not supplied. Live wristband output is disabled."
    ])
    public var contract: H1Contract? { if case .confirmed(let value) = self { return value }; return nil }
}

/// Exact conversion from app task context. String IDs are opaque; generations never truncate or wrap.
public struct HapticContext: Equatable, Sendable {
    public let sessionID: String
    public let taskID: String
    public let generation: UInt16
    public init(sessionID: String, taskID: String, generation: UInt64) throws {
        guard !sessionID.isEmpty, !taskID.isEmpty else { throw HapticLinkError.invalidContext }
        guard let exact = UInt16(exactly: generation) else { throw HapticLinkError.generationOutOfRange }
        self.sessionID = sessionID; self.taskID = taskID; self.generation = exact
    }
}

public enum HapticReleaseKind: Equatable, Sendable {
    case movement
    /// All nine patterns, through R1's scheduler, with physical task guidance suspended.
    case calibration
    /// Pattern 6 is an action-ready notification, never continued movement permission.
    case finiteNotification
    /// Patterns 8/9 require ALL_STOP first and explicit H1 support.
    case stoppedNotification
}

/// Accept only after R1 releases its priority scheduler, or from an explicitly labelled mock scheduler.
public struct ScheduledHapticRelease: Equatable, Sendable {
    public let id: UUID
    public let context: HapticContext
    public let kind: HapticReleaseKind
    public let evidenceTime: TimeInterval
    public let releasedAt: TimeInterval
    public let expiresAt: TimeInterval
    public let commands: [BandSide: PatternParameters]
    public init(id: UUID = UUID(), context: HapticContext, kind: HapticReleaseKind,
                evidenceTime: TimeInterval, releasedAt: TimeInterval, expiresAt: TimeInterval,
                commands: [BandSide: PatternParameters]) {
        self.id = id; self.context = context; self.kind = kind; self.evidenceTime = evidenceTime
        self.releasedAt = releasedAt; self.expiresAt = expiresAt; self.commands = commands
    }
}
