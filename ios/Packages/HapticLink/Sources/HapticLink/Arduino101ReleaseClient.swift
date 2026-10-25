#if canImport(CoreBluetooth)
import Combine
import Foundation
@preconcurrency import CoreBluetooth

public struct Arduino101Device: Identifiable, Equatable, Sendable {
    public let id: UUID
    public let name: String
}

public enum Arduino101ReleaseState: Equatable, Sendable {
    case idle, scanning, connecting, discovering, ready, sending, sent, failed(String)

    public var message: String {
        switch self {
        case .idle: return "Not connected"
        case .scanning: return "Scanning for ServoController"
        case .connecting: return "Connecting to Arduino 101"
        case .discovering: return "Checking the Arduino command characteristic"
        case .ready: return "Connected. Safe release is ready."
        case .sending: return "Sending reset"
        case .sent: return "Reset command acknowledged by Bluetooth"
        case .failed(let reason): return reason
        }
    }
}

/// Narrow compatibility path for the supplied ServoController sketch.
/// It sends only `reset` (UTF-8, no terminator) with a GATT write response.
/// It is not a task-guidance transport.
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
        guard state == .ready || state == .sent,
              let id = selectedID,
              let peripheral = peripherals[id],
              let characteristic = commandCharacteristics[id],
              characteristic.properties.contains(.write) else {
            state = .failed("Arduino is not ready for a write-with-response command")
            return
        }
        state = .sending
        peripheral.writeValue(Data("reset".utf8), for: characteristic, type: .withResponse)
    }

    public func disconnect() {
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
            state = error.map { .failed("Write failed: \($0.localizedDescription)") } ?? .sent
        }
    }
}
#endif
