import Foundation
import HapticLink

@MainActor
final class BandService: ObservableObject {
    @Published private(set) var summary = "Wristbands unavailable. Speech-only assistance can be used."
    @Published private(set) var discovered: [DiscoveredBand] = []
    @Published private(set) var pairings: [PairingMetadata] = []
    @Published private(set) var lastStopReport: StopDispatchReport?
    @Published private(set) var leftStatus: Capability = .unavailable("Left band unavailable")
    @Published private(set) var rightStatus: Capability = .unavailable("Right band unavailable")
    var onAvailabilityChanged: ((Capability, Capability) -> Void)?
    var onSafetyPause: (() -> Void)?
    var onGenerationSynchronized: ((UInt64) -> Void)?
    var onSynchronizationNeeded: ((UUID) -> Void)?
    private(set) var minimumNextGeneration: UInt64 = 0
    var synchronizationChallengeID: UUID? { controller?.synchronizationChallengeID }
    private let controller: HapticLinkController?
    let simulated: Bool
    private var desiredContext: HapticContext?
    private var calibrationContext = false
    private let clock: any AppClock
    var isReady: Bool { pairSignature != nil }
    var pairSignature: String? {
        guard let controller, controller.isSynchronized, controller.pairings.count == 2 else { return nil }
        return controller.pairings.values.sorted { $0.side.rawValue < $1.side.rawValue }.map {
            "\($0.side.rawValue):\($0.peripheralID.uuidString):\(controller.statuses[$0.side]?.firmwareVersion.description ?? "unknown")"
        }.joined(separator: "|")
    }
    init(controller: HapticLinkController?, simulated: Bool, clock: any AppClock) {
        self.controller = controller; self.simulated = simulated; self.clock = clock
        if simulated {
            summary = "SIMULATED bands. No physical wristband is connected."
            leftStatus = .simulated; rightStatus = .simulated
        }
        controller?.onEvent = { [weak self] _ in self?.refresh() }
        controller?.onSafetyPause = { [weak self] _ in self?.onSafetyPause?() }
        refresh()
    }
    private func refresh() {
        guard let controller else { return }
        discovered = controller.discoveredBands.values.sorted { $0.id.uuidString < $1.id.uuidString }
        pairings = Array(controller.pairings.values)
        func status(_ side: BandSide) -> Capability {
            if controller.availability == .unauthorized { return .unavailable("Bluetooth permission denied. Open Settings.") }
            if controller.availability == .poweredOff { return .unavailable("Bluetooth is turned off.") }
            guard let pairing = controller.pairings[side] else { return .unavailable("No confirmed \(side.rawValue) band") }
            switch controller.linkStates[pairing.peripheralID] {
            case .ready: return controller.isSynchronized ? .ready : .unavailable("Connected; firmware synchronization required")
            case .connecting, .discoveringServices, .subscribing, .reconnecting: return .preparing
            case .fault: return .unavailable("Band fault. Reconnect before guidance.")
            default: return .unavailable("Band disconnected")
            }
        }
        leftStatus = status(.left); rightStatus = status(.right)
        onAvailabilityChanged?(leftStatus, rightStatus)
        if !controller.isSynchronized, !pairings.isEmpty,
           pairings.allSatisfy({ controller.linkStates[$0.peripheralID] == .ready && controller.statuses[$0.side] != nil }) {
            onSynchronizationNeeded?(controller.synchronizationChallengeID)
        }
        if controller.isSynchronized {
            summary = controller.pairings.count == 2 && controller.pairings.values.allSatisfy(\.calibrationCompleted)
                ? "Both bands synchronized and calibrated."
                : "Bands synchronized. Confirm both sides and complete calibration before guidance."
        } else if !pairings.isEmpty {
            summary = "Bands assigned. Firmware synchronization and status must be validated before output."
        }
    }
    func discover() {
        controller?.start()
    }
    func assign(_ id: UUID, side: BandSide) throws {
        try controller?.assign(id, to: side, confirmedByUser: true)
        refresh()
    }
    func forget(_ side: BandSide) {
        try? controller?.forget(side)
        refresh()
    }
    func setContext(_ context: TasksContextProjection, calibration: Bool = false) throws {
        desiredContext = try HapticContext(sessionID: context.sessionID, taskID: context.taskID, generation: context.generation)
        calibrationContext = calibration
    }
    func dispatch(_ release: ScheduledHapticRelease) {
        guard let controller, desiredContext == release.context else { return }
        do {
            if controller.context != release.context {
                try controller.beginGeneration(release.context, calibration: calibrationContext)
            }
            _ = controller.submit(release)
        } catch { summary = "Wristband output is blocked pending firmware generation synchronization." }
    }
    func acceptSynchronization(_ proof: GenerationSynchronizationProof) throws {
        guard let controller else { throw HapticLinkError.generationNotSynchronized }
        try controller.acceptSynchronization(proof)
        minimumNextGeneration = max(minimumNextGeneration, UInt64(proof.generation) + 1)
        onGenerationSynchronized?(minimumNextGeneration)
        refresh()
    }
    func completeCalibration() throws {
        guard let controller else { return }
        for side in BandSide.allCases {
            guard let status = controller.statuses[side] else { throw HapticLinkError.linkNotReady(side) }
            try controller.confirmCalibration(for: side, firmwareVersion: status.firmwareVersion, confirmedByUser: true)
        }
        refresh()
    }
    func allStop(reason: String) {
        lastStopReport = controller?.allStop(reason: reason)
    }
    func shutdown() {
        controller?.shutdown()
    }
}

struct TasksContextProjection {
    let sessionID: String
    let taskID: String
    let generation: UInt64
}
