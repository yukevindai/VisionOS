import HapticLink
import SwiftUI

struct Arduino101SetupView: View {
    @StateObject private var client = Arduino101ReleaseClient()

    var body: some View {
        Form {
            Section("Board connection") {
                Text(client.state.message)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("arduino101.status")
                Button("Scan for ServoController") { client.scan() }
                    .frame(minHeight: 44)
                    .accessibilityIdentifier("arduino101.scan")
                ForEach(client.devices) { device in
                    Button("Connect to \(device.name)") { client.connect(device) }
                        .frame(minHeight: 44)
                }
            }

            Section("Safe release test") {
                Text("This sends the exact text `reset` to release all four servos to 70°.")
                    .fixedSize(horizontal: false, vertical: true)
                Button("Release all servos") { client.sendSafeRelease() }
                    .frame(minHeight: 48)
                    .disabled(!client.canSendCommand)
                    .accessibilityIdentifier("arduino101.reset")
            }

            Section("Directional hardware test") {
                Text("Each test sends a press command such as `up0`, waits 180 ms, then sends its matching release command such as `up80`. The Arduino also auto-releases after 2 seconds or immediately on BLE disconnect. These are manual hardware tests; they are not connected to live hand-guidance cues.")
                    .fixedSize(horizontal: false, vertical: true)
                ForEach(["Up", "Down", "Left", "Right"], id: \.self) { direction in
                    Button("Pulse \(direction)") { client.pulse(direction) }
                        .frame(minHeight: 48)
                        .disabled(!client.canSendCommand)
                        .accessibilityIdentifier("arduino101.direction.\(direction.lowercased())")
                }
            }

            Section("Current limitation") {
                Text("The repository hardware safety limit caps servo contact at 2 seconds. Your requested 6-second hold exceeds that limit. The sketch’s legacy `up80` command releases to its configured 70°, not 80°.")
                    .fixedSize(horizontal: false, vertical: true)
                Button("Disconnect") { client.disconnect() }
                    .frame(minHeight: 44)
            }
        }
        .navigationTitle("Arduino 101")
        .navigationBarTitleDisplayMode(.inline)
        .onDisappear { client.shutdown() }
    }
}
