import Foundation

/// Serializes BLE safety decisions. All timestamps are monotonic device time.
@MainActor public final class HapticLinkController {
    public let configuration: HapticLinkConfiguration
    public let transport: any BandTransport
    public let isSimulated: Bool
    public private(set) var availability: BluetoothAvailability = .unknown
    public private(set) var discoveredBands: [UUID: DiscoveredBand] = [:]
    public private(set) var linkStates: [UUID: BandLinkState] = [:]
    public private(set) var statuses: [BandSide: BandStatus] = [:]
    public private(set) var pairings: [BandSide: PairingMetadata] = [:]
    public private(set) var diagnostics: [HapticDiagnostic] = []
    public private(set) var context: HapticContext?
    public private(set) var isSynchronized = false
    public private(set) var synchronizationChallengeID = UUID()
    public private(set) var isCalibrating = false
    public var onEvent: ((BandTransportEvent) -> Void)?
    public var onSafetyPause: ((String) -> Void)?
    public let evidenceMaxAge: TimeInterval
    public let heartbeatInterval: TimeInterval

    private let clock: any HapticClock
    private let metadataStore: any PairingMetadataStore
    private var nextSequences: [BandSide: UInt32] = [:]
    private var synchronizedFloor: UInt16?
    private var stoppedFloor: UInt16?
    private var pendingRelease: ScheduledHapticRelease?
    private var pendingStops: [BandSide: UInt16] = [:]
    private var sentStops: [BandSide: UInt16] = [:]
    private struct ActiveOutput { let expiresAt: TimeInterval; let movement: Bool }
    private var activeOutputs: [BandSide: ActiveOutput] = [:]
    private var seenReleases: [(id: UUID, time: TimeInterval)] = []
    private var latestReleaseTime: TimeInterval?
    private var lastDispatchedReleaseID: UUID?
    private var lastHeartbeat: TimeInterval?
    private var tickingTask: Task<Void, Never>?
    private var isStopping = false

    public var pendingCommandCount: Int { (pendingRelease == nil ? 0 : 1) + pendingStops.count }
    public var hasEligibleOutput: Bool { activeOutputs.values.contains { $0.expiresAt > clock.now } }

    public init(configuration: HapticLinkConfiguration, transport: any BandTransport,
                clock: any HapticClock = SystemHapticClock(),
                metadataStore: any PairingMetadataStore,
                evidenceMaxAge: TimeInterval = 0.3, heartbeatInterval: TimeInterval = 0.2) throws {
        guard evidenceMaxAge.isFinite, evidenceMaxAge > 0, evidenceMaxAge <= 0.3,
              heartbeatInterval.isFinite, heartbeatInterval > 0, heartbeatInterval <= 0.2 else {
            throw HapticLinkError.invalidConfiguration("Evidence may not exceed 300 ms; heartbeat interval may not exceed 200 ms")
        }
        self.configuration = configuration; self.transport = transport; self.clock = clock
        self.metadataStore = metadataStore; self.evidenceMaxAge = evidenceMaxAge
        self.heartbeatInterval = heartbeatInterval; isSimulated = transport.isSimulated
        let loaded = try metadataStore.load()
        guard Set(loaded.map(\.side)).count == loaded.count,
              Set(loaded.map(\.peripheralID)).count == loaded.count else {
            throw HapticLinkError.duplicatePeripheral
        }
        pairings = Dictionary(uniqueKeysWithValues: loaded.map { ($0.side, $0) })
        transport.onEvent = { [weak self] event in self?.handle(event) }
    }

    deinit { tickingTask?.cancel() }

    /// Call in production to discover and perform heartbeat/expiry work. Tests drive tick() directly.
    public func start() {
        transport.onEvent = { [weak self] event in self?.handle(event) }
        guard configuration.contract != nil else { record("discovery_unavailable_h1"); return }
        transport.startDiscovery()
        guard tickingTask == nil else { return }
        tickingTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                do { try await Task.sleep(nanoseconds: 20_000_000) } catch { return }
                self?.tick()
            }
        }
    }

    public func shutdown() {
        _ = allStop(reason: "shutdown")
        tickingTask?.cancel(); tickingTask = nil
        transport.shutdown(); isSynchronized = false; context = nil
        pendingStops.removeAll(); activeOutputs.removeAll(); pendingRelease = nil
        availability = .unknown; statuses.removeAll(); linkStates.removeAll()
        synchronizationChallengeID = UUID()
    }

    public func assign(_ peripheralID: UUID, to side: BandSide, confirmedByUser: Bool) throws {
        guard confirmedByUser else { throw HapticLinkError.sideNotConfirmed }
        guard !pairings.contains(where: { $0.key != side && $0.value.peripheralID == peripheralID }) else {
            throw HapticLinkError.duplicatePeripheral
        }
        if let existing = pairings[side], existing.peripheralID != peripheralID {
            throw HapticLinkError.sideAlreadyAssigned(side)
        }
        if pairings[side]?.peripheralID == peripheralID { transport.connect(peripheralID); return }
        _ = allStop(reason: "pairing_changed")
        var updated = pairings
        updated[side] = PairingMetadata(peripheralID: peripheralID, side: side)
        try metadataStore.save(Array(updated.values)); pairings = updated
        invalidateSynchronization(reason: "pairing_changed")
        transport.connect(peripheralID)
    }

    public func forget(_ side: BandSide) throws {
        _ = allStop(reason: "forget_band")
        let id = pairings[side]?.peripheralID
        var updated = pairings; updated.removeValue(forKey: side)
        try metadataStore.save(Array(updated.values)); pairings = updated
        statuses.removeValue(forKey: side); pendingStops.removeValue(forKey: side)
        sentStops.removeValue(forKey: side); nextSequences.removeValue(forKey: side)
        if let id { transport.disconnect(id) }
        invalidateSynchronization(reason: "pairing_forgotten")
    }

    /// Call after the complete explicit calibration sequence, never merely on connection.
    public func confirmCalibration(for side: BandSide, firmwareVersion: UInt8, confirmedByUser: Bool) throws {
        guard confirmedByUser, var metadata = pairings[side],
              statuses[side]?.firmwareVersion == firmwareVersion else { throw HapticLinkError.sideNotConfirmed }
        metadata.calibrationCompleted = true; metadata.calibratedFirmwareVersion = firmwareVersion
        var updated = pairings; updated[side] = metadata
        try metadataStore.save(Array(updated.values)); pairings = updated
    }

    /// This is an adapter seam, not an invented firmware handshake. Every reconnect/relaunch needs a new proof.
    public func acceptSynchronization(_ proof: GenerationSynchronizationProof) throws {
        guard configuration.contract != nil, !pairings.isEmpty,
              proof.challengeID == synchronizationChallengeID,
              context == nil, activeOutputs.isEmpty, pendingRelease == nil,
              Set(proof.peripheralIDs.keys) == Set(pairings.keys),
              Set(proof.nextSequences.keys) == Set(pairings.keys),
              pairings.allSatisfy({ proof.peripheralIDs[$0.key] == $0.value.peripheralID && ready($0.key) }) else {
            throw HapticLinkError.generationNotSynchronized
        }
        switch proof.source {
        case .simulatedFixture:
            guard isSimulated else { throw HapticLinkError.generationNotSynchronized }
        case .confirmedH1Handshake(let reference):
            guard !reference.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                throw HapticLinkError.generationNotSynchronized
            }
        }
        // A same-process rollback cannot be made safe by relabeling it a new handshake.
        if let floor = stoppedFloor, proof.generation <= floor { throw HapticLinkError.generationNotSynchronized }
        for (side, previous) in nextSequences {
            if let supplied = proof.nextSequences[side], UInt32(supplied) < previous {
                throw HapticLinkError.generationNotSynchronized
            }
        }
        pendingRelease = nil; activeOutputs.removeAll(); pendingStops.removeAll()
        synchronizedFloor = proof.generation; nextSequences = proof.nextSequences.mapValues(UInt32.init)
        isSynchronized = true; context = nil; record("generation_synchronized")
    }

    public func beginGeneration(_ context: HapticContext, calibration: Bool = false) throws {
        guard isSynchronized, let floor = synchronizedFloor, context.generation >= floor else {
            throw HapticLinkError.generationNotSynchronized
        }
        if let stopped = stoppedFloor, context.generation <= stopped {
            throw HapticLinkError.generationNotSynchronized
        }
        if let current = self.context {
            guard context == current else {
                throw HapticLinkError.invalidRelease("Stop the current generation before changing task context")
            }
        }
        guard context.generation < UInt16.max else { throw HapticLinkError.generationExhausted }
        self.context = context; isCalibrating = calibration; record("generation_started")
    }

    @discardableResult public func submit(_ release: ScheduledHapticRelease) -> HapticDispatchResult {
        do { try validate(release) } catch let error as HapticLinkError { record("release_rejected"); return .rejected(error) }
        catch { return .rejected(.invalidRelease("Unknown validation error")) }
        seenReleases.removeAll { clock.now - $0.time > evidenceMaxAge }
        guard !seenReleases.contains(where: { $0.id == release.id }) else { return .rejected(.invalidRelease("Duplicate release")) }
        guard seenReleases.count < 256 else { return .rejected(.invalidRelease("Scheduler release rate exceeds the bounded deduplication window")) }
        seenReleases.append((release.id, release.releasedAt)); latestReleaseTime = release.releasedAt
        if pendingRelease != nil { record("superseded_release_dropped") }
        pendingRelease = release
        flush()
        if lastDispatchedReleaseID == release.id { return .sentToTransport }
        if pendingRelease?.id == release.id { return .queued }
        return .rejected(.invalidRelease("Release expired or transport failed during dispatch"))
    }

    /// Bypasses R1 scheduling. Clears all non-stop work before touching the transport.
    @discardableResult public func allStop(reason: String) -> StopDispatchReport {
        pendingRelease = nil; activeOutputs.removeAll(); lastHeartbeat = nil
        let generation = [context?.generation, synchronizedFloor, stoppedFloor].compactMap { $0 }.max()
        if let generation {
            stoppedFloor = max(stoppedFloor ?? generation, generation)
            for side in pairings.keys where sentStops[side] != generation { pendingStops[side] = generation }
        }
        record("all_stop:\(reason)")
        context = nil; isCalibrating = false
        flushStops()
        var result: [BandSide: StopDispatchReport.Delivery] = [:]
        for side in BandSide.allCases {
            if let generation, sentStops[side] == generation {
                result[side] = .sentToTransport; continue
            }
            guard let id = pairings[side]?.peripheralID,
                  configuration.contract != nil, generation != nil,
                  (nextSequences[side] ?? UInt32.max) <= UInt32(UInt16.max),
                  linkStates[id] != .disconnected else { result[side] = .unavailable; continue }
            result[side] = pendingStops[side] == nil ? .sentToTransport : .pendingBackpressure
        }
        return StopDispatchReport(sides: result, reason: reason)
    }

    public func tick() {
        let now = clock.now
        for (side, output) in activeOutputs where output.expiresAt <= now {
            activeOutputs.removeValue(forKey: side)
            if let generation = context?.generation { pendingStops[side] = generation }
            record("output_expired", side: side)
        }
        flush()
        guard isSynchronized, let generation = context?.generation,
              pendingStops.isEmpty, !activeOutputs.isEmpty,
              lastHeartbeat.map({ now - $0 >= heartbeatInterval }) ?? true,
              let contract = configuration.contract else { return }
        let sides = activeOutputs.keys.sorted { $0.rawValue < $1.rawValue }
        guard sides.allSatisfy({ ready($0) && transport.canWrite(pairings[$0]!.peripheralID) }) else { return }
        for side in sides {
            guard send(contract.heartbeat, generation: generation, side: side, reserveStop: true) else {
                pauseForSafety("heartbeat_sequence_or_transport_failure"); return
            }
        }
        lastHeartbeat = now
    }

    private func validate(_ release: ScheduledHapticRelease) throws {
        guard let contract = configuration.contract else { throw HapticLinkError.unavailable("H1 contract unavailable") }
        guard isSynchronized, let context, release.context == context else { throw HapticLinkError.generationNotSynchronized }
        let now = clock.now
        guard now.isFinite, release.evidenceTime.isFinite, release.releasedAt.isFinite, release.expiresAt.isFinite,
              release.evidenceTime >= 0, release.evidenceTime <= release.releasedAt,
              release.releasedAt <= now, release.expiresAt > now,
              latestReleaseTime.map({ release.releasedAt >= $0 }) ?? true,
              now - release.evidenceTime <= evidenceMaxAge,
              !release.commands.isEmpty else { throw HapticLinkError.invalidRelease("Stale, future or empty scheduler release") }
        if release.kind == .calibration {
            guard isCalibrating else { throw HapticLinkError.invalidRelease("Calibration is not active") }
        } else if isCalibrating { throw HapticLinkError.invalidRelease("Physical guidance suspended during calibration") }
        for (side, parameters) in release.commands {
            guard let agreed = contract.patterns[parameters.patternID], parameters.intensity == agreed.intensity,
                  parameters.rateHz == agreed.rateHz, parameters.ttlMs > 0, parameters.ttlMs <= agreed.ttlMs else {
                throw HapticLinkError.invalidRelease("Parameters do not match confirmed H1 pattern")
            }
            switch release.kind {
            case .movement:
                guard (1...5).contains(parameters.patternID) else { throw HapticLinkError.invalidPattern(parameters.patternID) }
            case .finiteNotification:
                guard parameters.patternID == 6 || parameters.patternID == 7,
                      parameters.ttlMs == agreed.ttlMs else { throw HapticLinkError.invalidPattern(parameters.patternID) }
            case .stoppedNotification:
                guard (parameters.patternID == 8 || parameters.patternID == 9), contract.supportsStoppedNotifications,
                      activeOutputs.isEmpty, pendingStops.isEmpty, stoppedFloor != nil else {
                    throw HapticLinkError.invalidRelease("Stopped notifications require a delivered ALL_STOP and confirmed H1 semantics")
                }
            case .calibration: break
            }
            guard ready(side) else { throw HapticLinkError.linkNotReady(side) }
            if release.kind != .calibration, pairings[side]?.calibrationCompleted != true {
                throw HapticLinkError.invalidRelease("Band calibration is incomplete")
            }
            guard (nextSequences[side] ?? UInt32.max) < UInt32(UInt16.max) else { throw HapticLinkError.sequenceExhausted(side) }
        }
    }

    private func flush() {
        flushStops()
        guard let release = pendingRelease else { return }
        do { try validate(release) } catch { pendingRelease = nil; record("expired_or_invalid_queued_release_dropped"); return }
        guard pendingStops.isEmpty else { return }
        let oldSides = Set(activeOutputs.keys).subtracting(release.commands.keys)
        let allSides = Set(release.commands.keys).union(oldSides)
        // Hold the latest release under either band's backpressure; never send half a bilateral cue.
        guard allSides.allSatisfy({ ready($0) && transport.canWrite(pairings[$0]!.peripheralID) }) else { return }
        for side in oldSides {
            pendingStops[side] = release.context.generation
            activeOutputs.removeValue(forKey: side)
        }
        flushStops()
        guard pendingStops.isEmpty else { return }
        for side in release.commands.keys.sorted(by: { $0.rawValue < $1.rawValue }) {
            let now = clock.now
            guard now < release.expiresAt, now - release.evidenceTime <= evidenceMaxAge else {
                pendingRelease = nil; pauseForSafety("release_expired_during_dispatch"); return
            }
            guard let parameters = release.commands[side] else { continue }
            let movement = release.kind == .movement
            let remaining = min(release.expiresAt, release.evidenceTime + evidenceMaxAge) - now
            let ttl = movement ? min(parameters.ttlMs, UInt16(max(0, min(Double(UInt16.max), floor(remaining * 1000))))): parameters.ttlMs
            guard ttl > 0 else { pendingRelease = nil; pauseForSafety("submillisecond_release_dropped"); return }
            let bounded = PatternParameters(patternID: parameters.patternID, intensity: parameters.intensity,
                                            rateHz: parameters.rateHz, ttlMs: ttl)
            guard send(bounded, generation: release.context.generation, side: side, reserveStop: true) else {
                pendingRelease = nil; pauseForSafety("movement_transport_failure"); return
            }
            activeOutputs[side] = ActiveOutput(expiresAt: now + Double(ttl) / 1000, movement: movement)
        }
        lastDispatchedReleaseID = release.id
        pendingRelease = nil; lastHeartbeat = clock.now; record("release_sent_to_transport")
    }

    private func flushStops() {
        guard !isStopping, let contract = configuration.contract else { return }
        isStopping = true; defer { isStopping = false }
        for side in BandSide.allCases {
            guard let generation = pendingStops[side], let id = pairings[side]?.peripheralID,
                  transport.canWrite(id) else { continue }
            if send(contract.stop, generation: generation, side: side, reserveStop: false) {
                pendingStops.removeValue(forKey: side)
                sentStops[side] = generation
                record("all_stop_sent_to_transport", side: side)
            }
        }
    }

    private func send(_ parameters: PatternParameters, generation: UInt16, side: BandSide, reserveStop: Bool) -> Bool {
        guard let contract = configuration.contract, let id = pairings[side]?.peripheralID,
              let next = nextSequences[side], next <= UInt32(UInt16.max),
              !reserveStop || next < UInt32(UInt16.max),
              let command = try? HapticCommand(version: contract.protocolVersion, sequence: UInt16(next),
                                               generation: generation, patternID: parameters.patternID,
                                               intensity: parameters.intensity, rateHz: parameters.rateHz, ttlMs: parameters.ttlMs),
              transport.write(command.data, to: id) else { return false }
        nextSequences[side] = next + 1 // UInt32 tracks exhaustion; no UInt16 wrapping.
        if parameters.patternID != 0 { sentStops.removeValue(forKey: side) }
        return true
    }

    private func ready(_ side: BandSide) -> Bool {
        guard let id = pairings[side]?.peripheralID else { return false }
        return availability == .poweredOn && linkStates[id] == .ready && statuses[side] != nil && statuses[side]?.faults == 0
    }

    private func invalidateSynchronization(reason: String) {
        isSynchronized = false; pendingRelease = nil; activeOutputs.removeAll(); context = nil
        synchronizationChallengeID = UUID()
        record(reason)
    }

    private func pauseForSafety(_ reason: String) {
        _ = allStop(reason: reason)
        invalidateSynchronization(reason: reason)
        onSafetyPause?(reason)
    }

    private func handle(_ event: BandTransportEvent) {
        switch event {
        case .availability(let value):
            let previous = availability
            availability = value
            if value != .poweredOn { pauseForSafety("bluetooth_unavailable") }
            else if previous != .poweredOn {
                for metadata in pairings.values { transport.connect(metadata.peripheralID) }
            }
        case .discovered(let band):
            if discoveredBands[band.id] != nil || discoveredBands.count < 64 { discoveredBands[band.id] = band }
        case .state(let id, let state):
            let previous = linkStates[id]
            linkStates[id] = state
            if state == .disconnected || (previous == .ready && state != .ready) || { if case .fault = state { return true }; return false }() {
                if let side = pairings.first(where: { $0.value.peripheralID == id })?.key {
                    statuses.removeValue(forKey: side)
                    pauseForSafety("band_disconnected_or_fault")
                }
            }
        case .status(let id, let data):
            guard let side = pairings.first(where: { $0.value.peripheralID == id })?.key,
                  let contract = configuration.contract else { break }
            do {
                let status = try BandStatus(data: data, supportedFirmwareVersions: contract.supportedFirmwareVersions)
                if let previous = statuses[side], previous.firmwareVersion != status.firmwareVersion {
                    pauseForSafety("firmware_changed")
                }
                if let previous = statuses[side], status.lastSequence < previous.lastSequence {
                    pauseForSafety("firmware_sequence_regressed_reboot_or_stale_status")
                }
                if var metadata = pairings[side], metadata.calibratedFirmwareVersion != status.firmwareVersion {
                    metadata.calibrationCompleted = false; metadata.calibratedFirmwareVersion = nil
                    pairings[side] = metadata
                    do { try metadataStore.save(Array(pairings.values)) } catch { record("pairing_persistence_failed") }
                }
                statuses[side] = status
                record("status_battery_\(status.batteryPct)_firmware_\(status.firmwareVersion)_sequence_\(status.lastSequence)_faults_\(status.faults)", side: side)
                if status.faults != 0 { pauseForSafety("firmware_fault") }
            } catch {
                linkStates[id] = .fault("Malformed or unsupported status")
                pauseForSafety("status_protocol_error")
            }
        case .writeReady: tick()
        case .diagnostic(let value): record(value)
        }
        onEvent?(event)
    }

    private func record(_ event: String, side: BandSide? = nil) {
        diagnostics.append(HapticDiagnostic(time: clock.now, event: event, context: context, side: side))
        if diagnostics.count > 256 { diagnostics.removeFirst(diagnostics.count - 256) }
    }
}
