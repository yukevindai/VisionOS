#if canImport(CoreBluetooth)
import Foundation
@preconcurrency import CoreBluetooth

public struct BandAdvertisement: Equatable, Sendable {
    public let peripheralID: UUID
    public let localName: String?
    public let manufacturerData: Data?
    public let serviceData: [String: Data]
    public let advertisedServiceUUIDs: [String]
}

/// Configurable app reconnect policy, not a claim about firmware timings.
public struct BandReconnectPolicy: Equatable, Sendable {
    public let maximumRetries: Int
    public let retryDelay: TimeInterval
    public let connectionTimeout: TimeInterval
    public init(maximumRetries: Int = 3, retryDelay: TimeInterval = 1, connectionTimeout: TimeInterval = 10) throws {
        guard (0...3).contains(maximumRetries), retryDelay.isFinite, retryDelay >= 0,
              retryDelay * 1_000_000_000 < Double(UInt64.max),
              connectionTimeout.isFinite, connectionTimeout > 0, connectionTimeout <= 120 else {
            throw HapticLinkError.invalidConfiguration("Reconnect requires at most 3 retries, a representable nanosecond delay and a finite connection deadline")
        }
        self.maximumRetries = maximumRetries; self.retryDelay = retryDelay; self.connectionTimeout = connectionTimeout
    }
}

/// Real main-queue CoreBluetooth central. Never creates commands or retains a movement queue.
/// A service UUID + display name alone cannot prove hardware identity; supply H1's confirmed verifier.
@MainActor public final class CoreBluetoothBandTransport: NSObject, BandTransport {
    public var onEvent: ((BandTransportEvent) -> Void)?
    public let isSimulated = false
    private let contract: H1Contract
    private let identityVerifier: (BandAdvertisement) -> Bool
    private let knownPeripheralIDs: Set<UUID>
    private let reconnectPolicy: BandReconnectPolicy
    private var central: CBCentralManager!
    private var peripherals: [UUID: CBPeripheral] = [:]
    private var advertisements: [UUID: BandAdvertisement] = [:]
    private var commandCharacteristics: [UUID: CBCharacteristic] = [:]
    private var statusCharacteristics: [UUID: CBCharacteristic] = [:]
    private var notificationsEnabled: Set<UUID> = []
    private var validatedStatus: Set<UUID> = []
    private var requested: Set<UUID> = []
    private var retryCounts: [UUID: Int] = [:]
    private var retryTasks: [UUID: Task<Void, Never>] = [:]
    private var deadlineTasks: [UUID: Task<Void, Never>] = [:]
    private var scanningRequested = false
    private var stopped = false

    public init(configuration: HapticLinkConfiguration, knownPeripheralIDs: Set<UUID> = [],
                reconnectPolicy: BandReconnectPolicy? = nil,
                identityVerifier: @escaping (BandAdvertisement) -> Bool) throws {
        guard let contract = configuration.contract else {
            throw HapticLinkError.unavailable("H1 UUIDs/protocol/identity policy are required before creating the Bluetooth central")
        }
        self.contract = contract; self.knownPeripheralIDs = knownPeripheralIDs
        self.identityVerifier = identityVerifier
        self.reconnectPolicy = try reconnectPolicy ?? BandReconnectPolicy()
        super.init()
        central = CBCentralManager(delegate: self, queue: .main)
    }

    public func startDiscovery() {
        if stopped { stopped = false; central.delegate = self }
        scanningRequested = true
        onEvent?(.availability(currentAvailability))
        if central.state == .poweredOn {
            central.scanForPeripherals(withServices: [CBUUID(nsuuid: contract.serviceUUID)], options: nil)
        }
    }

    private var currentAvailability: BluetoothAvailability {
        switch central.state {
        case .unknown: return .unknown
        case .resetting: return .resetting
        case .unsupported: return .unsupported
        case .unauthorized: return .unauthorized
        case .poweredOff: return .poweredOff
        case .poweredOn: return .poweredOn
        @unknown default: return .unknown
        }
    }
    public func stopDiscovery() { scanningRequested = false; central.stopScan() }

    public func connect(_ peripheralID: UUID) {
        guard !stopped, central.state == .poweredOn else {
            onEvent?(.state(peripheralID, .fault("Bluetooth is not powered on"))); return
        }
        let identityValid = knownPeripheralIDs.contains(peripheralID) || advertisements[peripheralID].map(identityVerifier) == true
        guard identityValid else {
            onEvent?(.state(peripheralID, .fault("Device identity has not been confirmed by H1's identity policy"))); return
        }
        requested.insert(peripheralID); retryCounts[peripheralID] = 0
        retryTasks.removeValue(forKey: peripheralID)?.cancel()
        if peripherals[peripheralID] == nil {
            peripherals[peripheralID] = central.retrievePeripherals(withIdentifiers: [peripheralID]).first
        }
        guard let peripheral = peripherals[peripheralID] else {
            onEvent?(.state(peripheralID, .fault("Known peripheral is unavailable; discover it before connecting"))); return
        }
        attemptConnection(peripheral)
    }

    public func disconnect(_ peripheralID: UUID) {
        requested.remove(peripheralID)
        retryTasks.removeValue(forKey: peripheralID)?.cancel()
        deadlineTasks.removeValue(forKey: peripheralID)?.cancel()
        if let peripheral = peripherals[peripheralID] { central.cancelPeripheralConnection(peripheral) }
        clearValidatedConnection(peripheralID)
        onEvent?(.state(peripheralID, .disconnected))
    }

    public func canWrite(_ peripheralID: UUID) -> Bool {
        guard !stopped, central.state == .poweredOn, let peripheral = peripherals[peripheralID],
              peripheral.state == .connected, commandCharacteristics[peripheralID] != nil else { return false }
        return peripheral.canSendWriteWithoutResponse
    }

    public func write(_ data: Data, to peripheralID: UUID) -> Bool {
        guard data.count == 10, canWrite(peripheralID), let peripheral = peripherals[peripheralID],
              let characteristic = commandCharacteristics[peripheralID] else { return false }
        peripheral.writeValue(data, for: characteristic, type: .withoutResponse)
        return true
    }

    public func shutdown() {
        guard !stopped else { return }
        stopped = true; stopDiscovery()
        for task in retryTasks.values { task.cancel() }
        for task in deadlineTasks.values { task.cancel() }
        retryTasks.removeAll(); deadlineTasks.removeAll(); requested.removeAll()
        for peripheral in peripherals.values {
            peripheral.delegate = nil
            central.cancelPeripheralConnection(peripheral)
        }
        commandCharacteristics.removeAll(); statusCharacteristics.removeAll()
        central.delegate = nil; onEvent = nil
    }

    private func attemptConnection(_ peripheral: CBPeripheral) {
        guard !stopped, requested.contains(peripheral.identifier) else { return }
        let id = peripheral.identifier
        clearValidatedConnection(id)
        peripheral.delegate = self
        onEvent?(.state(id, .connecting))
        if peripheral.state == .connected { discover(peripheral) }
        else { central.connect(peripheral, options: nil) }
        deadlineTasks[id]?.cancel()
        let timeout = UInt64(reconnectPolicy.connectionTimeout * 1_000_000_000)
        deadlineTasks[id] = Task { @MainActor [weak self, weak peripheral] in
            do { try await Task.sleep(nanoseconds: timeout) } catch { return }
            guard let self, let peripheral, self.requested.contains(id) else { return }
            self.onEvent?(.diagnostic("band_connection_or_discovery_timeout"))
            self.central.cancelPeripheralConnection(peripheral)
        }
    }

    private func discover(_ peripheral: CBPeripheral) {
        onEvent?(.state(peripheral.identifier, .discoveringServices))
        peripheral.discoverServices([CBUUID(nsuuid: contract.serviceUUID)])
    }

    private func reconnect(_ peripheral: CBPeripheral) {
        let id = peripheral.identifier
        guard requested.contains(id), !stopped, central.state == .poweredOn else { return }
        let count = retryCounts[id] ?? 0
        guard count < reconnectPolicy.maximumRetries else {
            requested.remove(id)
            onEvent?(.state(id, .fault("Reconnect exhausted after initial attempt and \(count) retries"))); return
        }
        retryCounts[id] = count + 1
        onEvent?(.state(id, .reconnecting(attempt: count + 1)))
        retryTasks[id]?.cancel()
        let delay = UInt64(reconnectPolicy.retryDelay * 1_000_000_000)
        retryTasks[id] = Task { @MainActor [weak self, weak peripheral] in
            do { try await Task.sleep(nanoseconds: delay) } catch { return }
            guard let self, let peripheral, !Task.isCancelled else { return }
            self.attemptConnection(peripheral)
        }
    }

    private func clearValidatedConnection(_ id: UUID) {
        commandCharacteristics.removeValue(forKey: id); statusCharacteristics.removeValue(forKey: id)
        notificationsEnabled.remove(id); validatedStatus.remove(id)
        deadlineTasks.removeValue(forKey: id)?.cancel()
    }

    private func fail(_ peripheral: CBPeripheral, _ message: String) {
        // Emit first so HapticLink can attempt an emergency stop while the command characteristic is usable.
        onEvent?(.state(peripheral.identifier, .fault(message)))
        central.cancelPeripheralConnection(peripheral)
    }

    private func markReadyIfValidated(_ peripheral: CBPeripheral) {
        let id = peripheral.identifier
        guard notificationsEnabled.contains(id), validatedStatus.contains(id), commandCharacteristics[id] != nil else { return }
        deadlineTasks.removeValue(forKey: id)?.cancel(); retryCounts[id] = 0
        onEvent?(.state(id, .ready))
        onEvent?(.writeReady(id))
    }
}

extension CoreBluetoothBandTransport: CBCentralManagerDelegate {
    nonisolated public func centralManagerDidUpdateState(_ central: CBCentralManager) {
        MainActor.assumeIsolated {
            let value: BluetoothAvailability
            switch central.state {
            case .unknown: value = .unknown
            case .resetting: value = .resetting
            case .unsupported: value = .unsupported
            case .unauthorized: value = .unauthorized
            case .poweredOff: value = .poweredOff
            case .poweredOn: value = .poweredOn
            @unknown default: value = .unknown
            }
            onEvent?(.availability(value))
            if value == .poweredOn {
                if scanningRequested { startDiscovery() }
            } else {
                for task in retryTasks.values { task.cancel() }
                for task in deadlineTasks.values { task.cancel() }
                retryTasks.removeAll(); deadlineTasks.removeAll()
                for id in peripherals.keys {
                    clearValidatedConnection(id)
                    onEvent?(.state(id, .disconnected))
                }
            }
        }
    }

    nonisolated public func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                               advertisementData: [String: Any], rssi RSSI: NSNumber) {
        MainActor.assumeIsolated {
            guard !stopped else { return }
            let rawServiceData = advertisementData[CBAdvertisementDataServiceDataKey] as? [CBUUID: Data] ?? [:]
            let advertisement = BandAdvertisement(peripheralID: peripheral.identifier,
                localName: advertisementData[CBAdvertisementDataLocalNameKey] as? String ?? peripheral.name,
                manufacturerData: advertisementData[CBAdvertisementDataManufacturerDataKey] as? Data,
                serviceData: Dictionary(uniqueKeysWithValues: rawServiceData.map { ($0.key.uuidString, $0.value) }),
                advertisedServiceUUIDs: (advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] ?? []).map(\.uuidString))
            guard identityVerifier(advertisement) || knownPeripheralIDs.contains(peripheral.identifier) else {
                onEvent?(.diagnostic("discovery_identity_not_confirmed")); return
            }
            guard peripherals[peripheral.identifier] != nil || peripherals.count < 64 else {
                onEvent?(.diagnostic("discovery_capacity_reached")); return
            }
            advertisements[peripheral.identifier] = advertisement; peripherals[peripheral.identifier] = peripheral
            onEvent?(.discovered(DiscoveredBand(id: peripheral.identifier, nameHint: advertisement.localName)))
            if peripheral.state != .connected { onEvent?(.state(peripheral.identifier, .discovered)) }
        }
    }

    nonisolated public func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        MainActor.assumeIsolated {
            guard requested.contains(peripheral.identifier), !stopped else { central.cancelPeripheralConnection(peripheral); return }
            discover(peripheral)
        }
    }

    nonisolated public func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        MainActor.assumeIsolated {
            clearValidatedConnection(peripheral.identifier)
            onEvent?(.state(peripheral.identifier, .disconnected)); reconnect(peripheral)
        }
    }

    nonisolated public func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        MainActor.assumeIsolated {
            clearValidatedConnection(peripheral.identifier)
            onEvent?(.state(peripheral.identifier, .disconnected)); reconnect(peripheral)
        }
    }
}

extension CoreBluetoothBandTransport: CBPeripheralDelegate {
    nonisolated public func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        MainActor.assumeIsolated {
            guard error == nil, let service = peripheral.services?.first(where: { $0.uuid == CBUUID(nsuuid: contract.serviceUUID) }) else {
                fail(peripheral, "Required H1 service missing"); return
            }
            peripheral.discoverCharacteristics([CBUUID(nsuuid: contract.commandUUID), CBUUID(nsuuid: contract.statusUUID)], for: service)
        }
    }

    nonisolated public func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        MainActor.assumeIsolated {
            guard error == nil,
                  let command = service.characteristics?.first(where: { $0.uuid == CBUUID(nsuuid: contract.commandUUID) }),
                  let status = service.characteristics?.first(where: { $0.uuid == CBUUID(nsuuid: contract.statusUUID) }),
                  command.properties.contains(.writeWithoutResponse), status.properties.contains(.notify) else {
                fail(peripheral, "H1 requires command writeWithoutResponse and status notify properties"); return
            }
            commandCharacteristics[peripheral.identifier] = command; statusCharacteristics[peripheral.identifier] = status
            onEvent?(.state(peripheral.identifier, .subscribing))
            peripheral.setNotifyValue(true, for: status)
            if status.properties.contains(.read) { peripheral.readValue(for: status) }
        }
    }

    nonisolated public func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        MainActor.assumeIsolated {
            guard !stopped, requested.contains(peripheral.identifier),
                  characteristic === statusCharacteristics[peripheral.identifier] else { return }
            guard characteristic.uuid == CBUUID(nsuuid: contract.statusUUID) else { return }
            guard error == nil, characteristic.isNotifying else { fail(peripheral, "Status notifications unavailable"); return }
            notificationsEnabled.insert(peripheral.identifier); markReadyIfValidated(peripheral)
        }
    }

    nonisolated public func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        MainActor.assumeIsolated {
            guard !stopped, requested.contains(peripheral.identifier),
                  characteristic === statusCharacteristics[peripheral.identifier] else { return }
            guard characteristic.uuid == CBUUID(nsuuid: contract.statusUUID) else { return }
            guard error == nil, let data = characteristic.value,
                  (try? BandStatus(data: data, supportedFirmwareVersions: contract.supportedFirmwareVersions)) != nil else {
                fail(peripheral, "Malformed or unsupported status notification"); return
            }
            onEvent?(.status(peripheral.identifier, data))
            validatedStatus.insert(peripheral.identifier); markReadyIfValidated(peripheral)
        }
    }

    nonisolated public func peripheralIsReady(toSendWriteWithoutResponse peripheral: CBPeripheral) {
        MainActor.assumeIsolated {
            onEvent?(.writeReady(peripheral.identifier))
        }
    }

    nonisolated public func peripheral(_ peripheral: CBPeripheral, didModifyServices invalidatedServices: [CBService]) {
        MainActor.assumeIsolated {
            if invalidatedServices.contains(where: { $0.uuid == CBUUID(nsuuid: contract.serviceUUID) }) {
                fail(peripheral, "H1 GATT service changed; reconnect and synchronize again")
            }
        }
    }
}
#endif
