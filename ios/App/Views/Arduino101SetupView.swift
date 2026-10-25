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

            Section("Servo holds") {
                Text("Turn on one or more servos to send commands such as `up0` and `left0`. Turn each one off to send its matching release command, such as `up80`. Each toggle sends release by 1.8 seconds; the Arduino independently releases by 2 seconds. These manual controls are not connected to live hand-guidance cues.")
                    .fixedSize(horizontal: false, vertical: true)
                ForEach(["Up", "Down", "Left", "Right"], id: \.self) { direction in
                    Toggle("\(direction) servo", isOn: Binding(
                        get: { client.activeServos.contains(direction) },
                        set: { client.setServoHeld(direction, held: $0) }
                    ))
                    .disabled(!client.canSendCommand)
                    .accessibilityIdentifier("arduino101.servo.\(direction.lowercased())")
                }
            }

            Section("Current limitation") {
                Text("The repository hardware safety limit caps each servo hold at 2 seconds. The sketch’s legacy `up80` command releases to its configured 70°, not 80°.")
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
