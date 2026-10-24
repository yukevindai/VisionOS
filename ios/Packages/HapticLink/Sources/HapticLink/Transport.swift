import Foundation

public protocol HapticClock: AnyObject {
    /// Monotonic seconds, from the same clock as scheduler evidence/release timestamps.
    var now: TimeInterval { get }
}
public final class SystemHapticClock: HapticClock {
    public init() {}
    public var now: TimeInterval { ProcessInfo.processInfo.systemUptime }
}

public enum BluetoothAvailability: Equatable, Sendable {
    case unknown, resetting, unsupported, unauthorized, poweredOff, poweredOn
}
public enum BandLinkState: Equatable, Sendable {
    case discovered, connecting, discoveringServices, subscribing, ready, reconnecting(attempt: Int), disconnected
    case fault(String)
}
public struct DiscoveredBand: Equatable, Sendable {
    public let id: UUID
    /// Advertised names are presentation hints, never side or device-identity proof.
    public let nameHint: String?
    public init(id: UUID, nameHint: String?) { self.id = id; self.nameHint = nameHint }
}
public enum BandTransportEvent: Equatable, Sendable {
    case availability(BluetoothAvailability)
    case discovered(DiscoveredBand)
    case state(UUID, BandLinkState)
    case status(UUID, Data)
    case writeReady(UUID)
    case diagnostic(String)
}

@MainActor public protocol BandTransport: AnyObject {
    var onEvent: ((BandTransportEvent) -> Void)? { get set }
    var isSimulated: Bool { get }
    func startDiscovery()
    func stopDiscovery()
    func connect(_ peripheralID: UUID)
    func disconnect(_ peripheralID: UUID)
    func canWrite(_ peripheralID: UUID) -> Bool
    /// True means handed to the local transport only, never measured actuator onset or acknowledgment.
    @discardableResult func write(_ data: Data, to peripheralID: UUID) -> Bool
    func shutdown()
}

@MainActor public final class UnavailableBandTransport: BandTransport {
    public var onEvent: ((BandTransportEvent) -> Void)?
    public let isSimulated = false
    public init() {}
    public func startDiscovery() { onEvent?(.diagnostic("H1 contract unavailable; discovery is disabled")) }
    public func stopDiscovery() {}
    public func connect(_ peripheralID: UUID) { onEvent?(.state(peripheralID, .fault("H1 contract unavailable"))) }
    public func disconnect(_ peripheralID: UUID) {}
    public func canWrite(_ peripheralID: UUID) -> Bool { false }
    public func write(_ data: Data, to peripheralID: UUID) -> Bool { false }
    public func shutdown() { onEvent = nil }
}

public struct PairingMetadata: Codable, Equatable, Sendable {
    public let peripheralID: UUID
    public let side: BandSide
    public var calibratedFirmwareVersion: UInt8?
    public var calibrationCompleted: Bool
    public init(peripheralID: UUID, side: BandSide, calibratedFirmwareVersion: UInt8? = nil,
                calibrationCompleted: Bool = false) {
        self.peripheralID = peripheralID; self.side = side
        self.calibratedFirmwareVersion = calibratedFirmwareVersion; self.calibrationCompleted = calibrationCompleted
    }
}

@MainActor public protocol PairingMetadataStore: AnyObject {
    func load() throws -> [PairingMetadata]
    func save(_ metadata: [PairingMetadata]) throws
}

@MainActor public final class MemoryPairingMetadataStore: PairingMetadataStore {
    public var metadata: [PairingMetadata]
    public init(metadata: [PairingMetadata] = []) { self.metadata = metadata }
    public func load() throws -> [PairingMetadata] { metadata }
    public func save(_ metadata: [PairingMetadata]) throws { self.metadata = metadata }
}

/// Pairing/calibration only. Never persists a generation or active physical task.
@MainActor public final class UserDefaultsPairingMetadataStore: PairingMetadataStore {
    private let defaults: UserDefaults
    private let key: String
    public init(defaults: UserDefaults = .standard, key: String = "reflex.hapticLink.pairing.v1") {
        self.defaults = defaults; self.key = key
    }
    public func load() throws -> [PairingMetadata] {
        guard let data = defaults.data(forKey: key) else { return [] }
        return try JSONDecoder().decode([PairingMetadata].self, from: data)
    }
    public func save(_ metadata: [PairingMetadata]) throws {
        defaults.set(try JSONEncoder().encode(metadata), forKey: key)
    }
}

public struct GenerationSynchronizationProof: Equatable, Sendable {
    public enum Source: Equatable, Sendable { case confirmedH1Handshake(String), simulatedFixture }
    public let source: Source
    public let challengeID: UUID
    public let peripheralIDs: [BandSide: UUID]
    public let generation: UInt16
    /// Next unused sequence for each band, as established by H1's agreed session/reset policy.
    public let nextSequences: [BandSide: UInt16]
    public init(source: Source, challengeID: UUID, peripheralIDs: [BandSide: UUID], generation: UInt16,
                nextSequences: [BandSide: UInt16]) {
        self.source = source; self.challengeID = challengeID; self.peripheralIDs = peripheralIDs
        self.generation = generation; self.nextSequences = nextSequences
    }
}

public enum HapticDispatchResult: Equatable, Sendable {
    case sentToTransport, queued, rejected(HapticLinkError)
}
public struct HapticDiagnostic: Equatable, Sendable {
    public let time: TimeInterval
    public let event: String
    public let context: HapticContext?
    public let side: BandSide?
}

public struct StopDispatchReport: Equatable, Sendable {
    public enum Delivery: Equatable, Sendable { case sentToTransport, pendingBackpressure, unavailable }
    public let sides: [BandSide: Delivery]
    public let reason: String
}
