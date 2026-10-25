#if canImport(CoreBluetooth)
import Combine
import Foundation
@preconcurrency import CoreBluetooth

public struct Arduino101Device: Identifiable, Equatable, Sendable {
    public let id: UUID
    public let name: String
}

public enum Arduino101ReleaseState: Equatable, Sendable {
    case idle, scanning, connecting, discovering, ready
    case sending(String), holding([String]), releasing(String), sent(String), failed(String)

    public var message: String {
        switch self {
        case .idle: return "Not connected"
        case .scanning: return "Scanning for ServoController"
        case .connecting: return "Connecting to Arduino 101"
        case .discovering: return "Checking the Arduino command characteristic"
        case .ready: return "Connected. Servo controls are ready."
        case .sending(let command): return "Sending \(command)"
        case .holding(let directions): return "Holding: \(directions.joined(separator: ", "))"
        case .releasing(let direction): return "Releasing \(direction)"
        case .sent(let message): return message
        case .failed(let reason): return reason
        }
    }
}

/// Compatibility path for the supplied ServoController sketch.
/// Text writes are serialized with GATT write responses. Each servo can be
/// held independently, while the device enforces a two-second local cutoff.
@MainActor public final class Arduino101ReleaseClient: NSObject, ObservableObject {
    public static let serviceUUID = CBUUID(string: "19B10000-E8F2-537E-4F6C-D104768A1214")
    public static let commandUUID = CBUUID(string: "19B10001-E8F2-537E-4F6C-D104768A1214")

    @Published public private(set) var state: Arduino101ReleaseState = .idle
    @Published public private(set) var devices: [Arduino101Device] = []
    @Published public private(set) var activeServos: Set<String> = []
    @Published public private(set) var isConnected = false

    private var central: CBCentralManager!
    private var peripherals: [UUID: CBPeripheral] = [:]
    private var commandCharacteristics: [UUID: CBCharacteristic] = [:]
    private var selectedID: UUID?
    private var wantsScan = false
    private var pendingWrite: Command?
    private var commandQueue: [Command] = []
    private var safetyTasks: [String: Task<Void, Never>] = [:]

    private enum Command {
        case reset
        case press(String)
        case release(String)

        var wireText: String {
            switch self {
            case .reset: return "reset"
            case .press("Up"): return "up0"
            case .press("Down"): return "down0"
            case .press("Left"): return "left0"
            case .press("Right"): return "right0"
            case .release("Up"): return "up80"
            case .release("Down"): return "down80"
            case .release("Left"): return "left80"
            case .release("Right"): return "right80"
            case .press(_), .release(_): return ""
            }
        }

        var label: String {
            switch self {
            case .reset: return "reset"
            case .press(let direction): return "\(direction) press"
            case .release(let direction): return "\(direction) release"
            }
        }

    }

    private static let appHoldLimitNanoseconds: UInt64 = 1_800_000_000
    private static let maximumQueuedCommands = 16
    private static func supportsDirection(_ direction: String) -> Bool {
        ["Up", "Down", "Left", "Right"].contains(direction)
    }

    public override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: .main)
    }

    public var canSendCommand: Bool { isConnected && selectedID != nil }

    public func scan() {
        wantsScan = true
        guard central.state == .poweredOn else {
            if central.state == .unauthorized { state = .failed("Bluetooth permission is unavailable") }
            else if central.state == .unsupported { state = .failed("Bluetooth LE is unsupported on this device") }
            else if central.state == .poweredOff { state = .failed("Turn on Bluetooth, then scan again") }
            return
        }
        devices.removeAll(); peripherals.removeAll()
        state = .scanning
        central.scanForPeripherals(withServices: [Self.serviceUUID], options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
    }

    public func connect(_ device: Arduino101Device) {
        guard central.state == .poweredOn, let peripheral = peripherals[device.id] else {
            state = .failed("Scan again and select the Arduino")
            return
        }
        central.stopScan(); wantsScan = false
        selectedID = device.id
        state = .connecting
        peripheral.delegate = self
        central.connect(peripheral)
    }

    /// A toggle sends `up0` on activation and `up80` on release. The app sends
    /// its own release by 1.8 seconds; firmware has an independent 2-second cap.
    public func setServoHeld(_ direction: String, held: Bool) {
        guard canSendCommand, Self.supportsDirection(direction) else { return }
        if held {
            guard activeServos.insert(direction).inserted else { return }
            enqueue(.press(direction))
        } else {
            guard activeServos.remove(direction) != nil else { return }
            safetyTasks.removeValue(forKey: direction)?.cancel()
            enqueue(.release(direction))
        }
        updateVisibleState()
    }

    /// Sends `reset`, releasing all four servos on the Arduino.
    public func sendSafeRelease() {
        guard canSendCommand else {
            state = .failed("Arduino is not connected")
            return
        }
        activeServos.removeAll()
        for task in safetyTasks.values { task.cancel() }
        safetyTasks.removeAll()
        commandQueue.removeAll()
        enqueue(.reset)
        updateVisibleState()
    }

    public func disconnect() {
        safetyTasks.values.forEach { $0.cancel() }
        safetyTasks.removeAll(); activeServos.removeAll()
        commandQueue.removeAll(); pendingWrite = nil
        wantsScan = false; central.stopScan()
        if let selectedID, let peripheral = peripherals[selectedID] {
            central.cancelPeripheralConnection(peripheral)
        }
        selectedID = nil
        commandCharacteristics.removeAll()
        isConnected = false
        state = .idle
    }

    public func shutdown() {
        disconnect()
        central.delegate = nil
    }

    private func enqueue(_ command: Command) {
        guard commandQueue.count < Self.maximumQueuedCommands else {
            state = .failed("Too many queued servo commands; release all and reconnect")
            commandQueue.removeAll()
            return
        }
        commandQueue.append(command)
        sendNextIfReady()
    }

    private func sendNextIfReady() {
        guard pendingWrite == nil, canSendCommand, !commandQueue.isEmpty,
              let id = selectedID, let peripheral = peripherals[id],
              let characteristic = commandCharacteristics[id] else { return }
        let command = commandQueue.removeFirst()
        pendingWrite = command
        state = .sending(command.label)
        if case .press(let direction) = command { scheduleSafetyRelease(for: direction) }
        peripheral.writeValue(Data(command.wireText.utf8), for: characteristic, type: .withResponse)
    }

    private func scheduleSafetyRelease(for direction: String) {
        safetyTasks[direction]?.cancel()
        safetyTasks[direction] = Task { @MainActor [weak self] in
            do { try await Task.sleep(nanoseconds: Self.appHoldLimitNanoseconds) } catch { return }
            guard let self, self.activeServos.remove(direction) != nil else { return }
            self.safetyTasks.removeValue(forKey: direction)
            self.enqueue(.release(direction))
            self.updateVisibleState()
        }
    }

    private func updateVisibleState() {
        if !isConnected { return }
        if pendingWrite != nil || !commandQueue.isEmpty { return }
        if activeServos.isEmpty { state = .ready }
        else { state = .holding(activeServos.sorted()) }
    }
}

extension Arduino101ReleaseClient: CBCentralManagerDelegate {
    nonisolated public func centralManagerDidUpdateState(_ central: CBCentralManager) {
        MainActor.assumeIsolated {
            if central.state == .poweredOn {
                if wantsScan { scan() }
                return
            }
            safetyTasks.values.forEach { $0.cancel() }
            safetyTasks.removeAll(); activeServos.removeAll()
            commandQueue.removeAll(); pendingWrite = nil
            selectedID = nil; commandCharacteristics.removeAll(); isConnected = false
            if central.state == .poweredOff { state = .failed("Turn on Bluetooth, then scan again") }
            else if central.state == .unauthorized { state = .failed("Bluetooth permission is unavailable") }
            else if central.state == .unsupported { state = .failed("Bluetooth LE is unsupported on this device") }
        }
    }

    nonisolated public func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                                           advertisementData: [String: Any], rssi RSSI: NSNumber) {
        MainActor.assumeIsolated {
            guard wantsScan else { return }
            peripherals[peripheral.identifier] = peripheral
            let name = (advertisementData[CBAdvertisementDataLocalNameKey] as? String) ?? peripheral.name ?? "Arduino BLE device"
            let device = Arduino101Device(id: peripheral.identifier, name: name)
            if !devices.contains(device) { devices.append(device) }
        }
    }

    nonisolated public func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        MainActor.assumeIsolated {
            guard peripheral.identifier == selectedID else { central.cancelPeripheralConnection(peripheral); return }
            state = .discovering
            peripheral.discoverServices([Self.serviceUUID])
        }
    }

    nonisolated public func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        MainActor.assumeIsolated { state = .failed(error?.localizedDescription ?? "Could not connect to Arduino") }
    }

    nonisolated public func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        MainActor.assumeIsolated {
            guard peripheral.identifier == selectedID else { return }
            safetyTasks.values.forEach { $0.cancel() }
            safetyTasks.removeAll(); activeServos.removeAll()
            commandQueue.removeAll(); pendingWrite = nil
            selectedID = nil; commandCharacteristics.removeAll()
            isConnected = false
            state = .failed("Arduino disconnected. Its firmware releases all servos on link loss.")
        }
    }
}

extension Arduino101ReleaseClient: CBPeripheralDelegate {
    nonisolated public func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        MainActor.assumeIsolated {
            guard error == nil,
                  let service = peripheral.services?.first(where: { $0.uuid == Self.serviceUUID }) else {
                state = .failed(error?.localizedDescription ?? "Arduino service was not found")
                return
            }
            peripheral.discoverCharacteristics([Self.commandUUID], for: service)
        }
    }

    nonisolated public func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService,
                                       error: Error?) {
        MainActor.assumeIsolated {
            guard error == nil,
                  let characteristic = service.characteristics?.first(where: { $0.uuid == Self.commandUUID }),
                  characteristic.properties.contains(.write) else {
                state = .failed("Arduino command characteristic must support Write")
                return
            }
            commandCharacteristics[peripheral.identifier] = characteristic
            isConnected = true
            state = .ready
        }
    }

    nonisolated public func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic,
                                       error: Error?) {
        MainActor.assumeIsolated {
            guard characteristic.uuid == Self.commandUUID, let completed = pendingWrite else { return }
            pendingWrite = nil
            if let error {
                state = .failed("Write failed: \(error.localizedDescription). Arduino auto-release remains active.")
                commandQueue.removeAll()
                sendNextIfReady()
                return
            }
            switch completed {
            case .reset:
                activeServos.removeAll()
                for task in safetyTasks.values { task.cancel() }
                safetyTasks.removeAll()
                state = .sent("Reset acknowledged; all servos were commanded to release")
            case .press:
                updateVisibleState()
            case .release(let direction):
                safetyTasks.removeValue(forKey: direction)?.cancel()
                updateVisibleState()
            }
            sendNextIfReady()
        }
    }
}
#endif
