import SwiftUI

struct SetupView: View {
    @ObservedObject var session: SessionCoordinator
    @ObservedObject var preferences: UserPreferences
    var body: some View {
        NavigationStack {
            Form {
                Section("How assistance works") {
                    Text("Reflex uses the rear camera to see your surroundings and the microphone to hear your requests. Keep your working hand and target in view.")
                    Text("Locking the phone or leaving the app pauses assistance.")
                }
                Section("Your guiding hand") {
                    Picker("Guiding hand", selection: $preferences.guidingHand) {
                        ForEach(UserPreferences.Hand.allCases, id: \.self) { Text($0.rawValue.capitalized).tag($0) }
                    }.pickerStyle(.segmented)
                }
                Section("Phone position") {
                    Picker("Position", selection: $preferences.phonePosition) {
                        ForEach(UserPreferences.PhonePosition.allCases, id: \.self) { Text($0.label).tag($0) }
                    }
                    Text("If handheld, use your other hand for the phone. Tasks needing both hands require a mount or help holding the phone.")
                }
                if !session.simulated {
                    Section("Camera and microphone") {
                        Text(session.permissionState.explanation)
                        Button(session.requestingPermissions ? "Requesting access…" : "Allow camera and microphone") { session.requestPermissions() }
                            .disabled(session.requestingPermissions)
                            .accessibilityIdentifier("setup.permissions")
                        if !session.permissionState.ready {
                            Link("Open Settings", destination: URL(string: UIApplication.openSettingsURLString)!)
                        }
                    }
                }
                Section("Wristbands are optional") {
                    Text("Start with speech-only assistance. Pair and calibrate bands in Settings when ready. Connecting a band does not automatically enable wristband guidance.")
                }
                Button("Continue with speech-only assistance") { session.finishSetup() }
                    .disabled(!session.simulated && !session.permissionState.ready)
                    .frame(minHeight: 44).accessibilityIdentifier("setup.continue")
            }
            .navigationTitle("Set up assistance")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel", action: session.dismissSetup) } }
        }
    }
}
