import SwiftUI

struct SetupView: View {
    @ObservedObject var session: SessionCoordinator
    @ObservedObject var preferences: UserPreferences
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                setupForm
                if dynamicTypeSize.isAccessibilitySize {
                    cancelButton
                }
            }
            .navigationTitle("Set up assistance")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Color(uiColor: .systemBackground), for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbar {
                if !dynamicTypeSize.isAccessibilitySize {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel", action: session.dismissSetup)
                            .accessibilityIdentifier("setup.cancel")
                    }
                }
            }
        }
    }

    private var setupForm: some View {
        Form {
            if session.simulated { SimulationLabel() }
            Section("How assistance works") {
                Text("The rear camera sees your surroundings.")
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("setup.instructions")
                Text("The microphone hears your requests.")
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("setup.instructions.microphone")
                Text("Keep your working hand and target in view.")
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("setup.instructions.position")
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
                            .foregroundStyle(Color.primary)
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
                }.pickerStyle(.inline)
                .accessibilityIdentifier("setup.guidingHand")
            }
            Section("Phone position") {
                Picker("Position", selection: $preferences.phonePosition) {
                    ForEach(UserPreferences.PhonePosition.allCases, id: \.self) { Text($0.label).tag($0) }
                }
                .pickerStyle(.inline)
                .accessibilityIdentifier("setup.phonePosition")
                Text("If handheld, use your other hand for the phone. Tasks needing both hands require a mount or help holding the phone.")
            }
            Section("Wristbands are optional") {
                Text("Start with speech-only assistance. Pair and calibrate bands in Settings when ready. Connecting a band does not automatically enable wristband guidance.")
            }
            Button("Continue with speech-only assistance") { session.finishSetup() }
                .foregroundStyle(Color.primary)
                .disabled(session.requiresPermissions && !session.permissionState.ready)
                .frame(minHeight: 44).accessibilityIdentifier("setup.continue")
        }
        // Keep the scrolling viewport separate from the fixed action. In
        // particular, large text must not render beneath the Cancel control.
        .clipped()
    }

    private var cancelButton: some View {
        Button(action: session.dismissSetup) {
            Label("Cancel", systemImage: "xmark")
                .frame(maxWidth: .infinity, minHeight: 52)
        }
        .buttonStyle(.plain).foregroundStyle(Color.primary)
        .padding(12)
        .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
        .accessibilityIdentifier("setup.cancel")
        .padding().background(Color(uiColor: .systemBackground))
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
            .foregroundStyle(Color.primary)
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
            .foregroundStyle(Color.primary)
            .accessibilityIdentifier("setup.permission.settings")
    }
}
