import SwiftUI

struct SetupView: View {
    @ObservedObject var session: SessionCoordinator
    @ObservedObject var preferences: UserPreferences
    var body: some View {
        NavigationStack {
            Form {
                if session.simulated { SimulationLabel() }
                Section("How assistance works") {
                    Text("Reflex uses the rear camera to see your surroundings and the microphone to hear your requests. Keep your working hand and target in view.")
                    Text("Locking the phone or leaving the app pauses assistance.")
                }
                if session.requiresPermissions {
                    Section("Camera and microphone") {
                        Text(session.permissionState.explanation)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityIdentifier("setup.permission.explanation")
                        LabeledContent("Camera access", value: session.permissionState.cameraAuthorization.rawValue)
                            .accessibilityIdentifier("setup.permission.camera")
                        LabeledContent("Microphone access", value: session.permissionState.microphoneAuthorization.rawValue)
                            .accessibilityIdentifier("setup.permission.microphone")
                        if session.permissionState.canRequest {
                            Button(session.requestingPermissions ? "Requesting access…" : "Allow camera and microphone") { session.requestPermissions() }
                                .disabled(session.requestingPermissions)
                                .frame(minHeight: 44)
                                .accessibilityIdentifier("setup.permissions")
                        }
                        if session.permissionState.needsSettings {
                            settingsRecovery
                        }
                    }
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
                Section("Wristbands are optional") {
                    Text("Start with speech-only assistance. Pair and calibrate bands in Settings when ready. Connecting a band does not automatically enable wristband guidance.")
                }
                Button("Continue with speech-only assistance") { session.finishSetup() }
                    .disabled(session.requiresPermissions && !session.permissionState.ready)
                    .frame(minHeight: 44).accessibilityIdentifier("setup.continue")
            }
            .navigationTitle("Set up assistance")
            .toolbar { ToolbarItem(placement: .cancellationAction) {
                Button("Cancel", action: session.dismissSetup).accessibilityIdentifier("setup.cancel")
            } }
        }
    }

    @ViewBuilder
    private var settingsRecovery: some View {
        #if DEBUG
        if let permissions = session.permissions as? UITestPermissionClient {
            Button("Simulate returning from Settings with access allowed") {
                permissions.simulateSettingsRecovery()
                session.refreshPermissions()
            }
            .frame(minHeight: 44)
            .accessibilityIdentifier("setup.permission.simulatedRecovery")
        } else {
            openSettings
        }
        #else
        openSettings
        #endif
    }

    private var openSettings: some View {
        Link("Open Settings", destination: URL(string: UIApplication.openSettingsURLString)!)
            .frame(minHeight: 44)
            .accessibilityIdentifier("setup.permission.settings")
    }
}
