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
                Text("This sends the exact text `reset` to release all four servos. The sketch sets them to 70°. This screen does not send task guidance or direction commands.")
                    .fixedSize(horizontal: false, vertical: true)
                Button("Release all servos") { client.sendSafeRelease() }
                    .frame(minHeight: 48)
                    .disabled(client.state != .ready && client.state != .sent)
                    .accessibilityIdentifier("arduino101.reset")
            }

            Section("Current limitation") {
                Text("The sketch holds each commanded position and has no disconnect timeout. Direction commands stay disabled for app guidance until the firmware has an independent stop failsafe.")
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
