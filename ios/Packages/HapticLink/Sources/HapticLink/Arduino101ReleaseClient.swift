#if canImport(CoreBluetooth)
import Combine
import Foundation
@preconcurrency import CoreBluetooth

public struct Arduino101Device: Identifiable, Equatable, Sendable {
    public let id: UUID
    public let name: String
}

public enum Arduino101ReleaseState: Equatable, Sendable {
    case idle, scanning, connecting, discovering, ready, sending, holding(String), releasing(String), sent(String), failed(String)

    public var message: String {
        switch self {
        case .idle: return "Not connected"
        case .scanning: return "Scanning for ServoController"
        case .connecting: return "Connecting to Arduino 101"
        case .discovering: return "Checking the Arduino command characteristic"
        case .ready: return "Connected. Safe release is ready."
        case .sending: return "Sending reset"
        case .holding(let direction): return "Pulsing \(direction); Arduino watchdog is active"
        case .releasing(let direction): return "Releasing \(direction) servo"
        case .sent(let message): return message
        case .failed(let reason): return reason
        }
    }
}

/// Compatibility path for the supplied ServoController sketch.
/// Commands are UTF-8 text with GATT write responses; directional pulses are
/// manual hardware tests and are not task-guidance releases.
@MainActor public final class Arduino101ReleaseClient: NSObject, ObservableObject {
    public static let serviceUUID = CBUUID(string: "19B10000-E8F2-537E-4F6C-D104768A1214")
    public static let commandUUID = CBUUID(string: "19B10001-E8F2-537E-4F6C-D104768A1214")

    @Published public private(set) var state: Arduino101ReleaseState = .idle
    @Published public private(set) var devices: [Arduino101Device] = []

    private var central: CBCentralManager!
    private var peripherals: [UUID: CBPeripheral] = [:]
    private var commandCharacteristics: [UUID: CBCharacteristic] = [:]
    private var selectedID: UUID?
    private var wantsScan = false
    private var pendingWrite: PendingWrite?
    private var releaseTask: Task<Void, Never>?

    private enum PendingWrite {
        case reset
        case press(String, releaseCommand: String)
        case release(String)
    }
    private static let testPulseNanoseconds: UInt64 = 180_000_000

    public override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: .main)
    }

    public func scan() {
        wantsScan = true
        guard central.state == .poweredOn else {
            if central.state == .unauthorized { state = .failed("Bluetooth permission is unavailable") }
            else if central.state == .unsupported { state = .failed("Bluetooth LE is unsupported on this device") }
            else if central.state == .poweredOff { state = .failed("Turn on Bluetooth, then scan again") }
            return
        }
        devices.removeAll()
        peripherals.removeAll()
        state = .scanning
        central.scanForPeripherals(withServices: [Self.serviceUUID], options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
    }

    public func connect(_ device: Arduino101Device) {
        guard central.state == .poweredOn, let peripheral = peripherals[device.id] else {
            state = .failed("Scan again and select the Arduino")
            return
        }
        central.stopScan()
        wantsScan = false
        selectedID = device.id
        state = .connecting
        peripheral.delegate = self
        central.connect(peripheral)
    }

    /// The supplied sketch maps `reset` to RELEASE_ANGLE on all four servos.
    /// BLE reports completion; this is not position or motion feedback.
    public func sendSafeRelease() {
        guard state == .ready || isSent,
              let id = selectedID,
              let peripheral = peripherals[id],
              let characteristic = commandCharacteristics[id],
              characteristic.properties.contains(.write) else {
            state = .failed("Arduino is not ready for a write-with-response command")
            return
        }
        releaseTask?.cancel(); releaseTask = nil
        pendingWrite = .reset
        state = .sending
        peripheral.writeValue(Data("reset".utf8), for: characteristic, type: .withResponse)
    }

    public var canSendCommand: Bool { state == .ready || isSent }

    /// Manual hardware-test pulse. The Arduino independently releases at its
    /// two-second ceiling if this phone timer or BLE link fails.
    public func pulse(_ direction: String) {
        guard let commands = Self.directionCommands[direction],
              state == .ready || isSent,
              let id = selectedID,
              let peripheral = peripherals[id],
              let characteristic = commandCharacteristics[id] else {
            state = .failed("Arduino is not ready for a directional test")
            return
        }
        pendingWrite = .press(direction, releaseCommand: commands.release)
        state = .sending
        peripheral.writeValue(Data(commands.press.utf8), for: characteristic, type: .withResponse)
    }

    public func disconnect() {
        releaseTask?.cancel(); releaseTask = nil
        pendingWrite = nil
        wantsScan = false
        central.stopScan()
        if let selectedID, let peripheral = peripherals[selectedID] {
            central.cancelPeripheralConnection(peripheral)
        }
        selectedID = nil
        commandCharacteristics.removeAll()
        state = .idle
    }

    public func shutdown() {
        disconnect()
        central.delegate = nil
    }

    private var isSent: Bool {
        if case .sent = state { return true }
        return false
    }

    private static let directionCommands: [String: (press: String, release: String)] = [
        "Up": ("up0", "up80"), "Down": ("down0", "down80"),
        "Left": ("left0", "left80"), "Right": ("right0", "right80")
    ]

    private func writeRelease(_ direction: String, command: String) {
        guard let id = selectedID, let peripheral = peripherals[id],
              let characteristic = commandCharacteristics[id] else {
            state = .failed("Arduino link lost; firmware auto-release will handle the servo")
            return
        }
        pendingWrite = .release(direction)
        state = .releasing(direction)
        peripheral.writeValue(Data(command.utf8), for: characteristic, type: .withResponse)
    }
}

extension Arduino101ReleaseClient: CBCentralManagerDelegate {
    nonisolated public func centralManagerDidUpdateState(_ central: CBCentralManager) {
        MainActor.assumeIsolated {
            if central.state == .poweredOn, wantsScan { scan() }
            else if central.state == .poweredOff { state = .failed("Turn on Bluetooth, then scan again") }
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
            releaseTask?.cancel(); releaseTask = nil
            pendingWrite = nil
            selectedID = nil
            commandCharacteristics.removeAll()
            state = .failed("Arduino disconnected. Scan and connect again.")
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
            state = .ready
        }
    }

    nonisolated public func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic,
                                       error: Error?) {
        MainActor.assumeIsolated {
            guard characteristic.uuid == Self.commandUUID else { return }
            if let error {
                releaseTask?.cancel(); releaseTask = nil
                pendingWrite = nil
                state = .failed("Write failed: \(error.localizedDescription). Arduino auto-release remains active.")
                return
            }
            guard let completed = pendingWrite else { return }
            pendingWrite = nil
            switch completed {
            case .reset:
                state = .sent("Reset acknowledged; all servos were commanded to release")
            case .press(let direction, let releaseCommand):
                state = .holding(direction)
                releaseTask?.cancel()
                releaseTask = Task { @MainActor [weak self] in
                    do { try await Task.sleep(nanoseconds: Self.testPulseNanoseconds) } catch { return }
                    guard let self, self.state == .holding(direction) else { return }
                    self.writeRelease(direction, command: releaseCommand)
                }
            case .release(let direction):
                state = .sent("\(direction) pulse released")
            }
        }
    }
}
#endif
