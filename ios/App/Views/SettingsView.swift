import SwiftUI
import HapticLink

struct SettingsView: View {
    @ObservedObject var dependencies: AppDependencies
    var body: some View {
        SettingsNavigationView(dependencies: dependencies, session: dependencies.session)
    }
}

private struct SettingsNavigationView: View {
    @ObservedObject var dependencies: AppDependencies
    @ObservedObject var session: SessionCoordinator
    @Environment(\.dismiss) private var dismiss
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    var body: some View {
        VStack(spacing: 0) {
            NavigationStack {
                VStack(spacing: 0) {
                    SettingsContents(dependencies: dependencies, session: session, preferences: dependencies.preferences)
                        .clipped()
                    if dynamicTypeSize.isAccessibilitySize {
                        Button { dismiss() } label: {
                            Label("Done", systemImage: "checkmark")
                                .frame(maxWidth: .infinity, minHeight: 52)
                        }
                        .buttonStyle(.plain).foregroundStyle(Color.primary)
                        .padding(12)
                        .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
                        .accessibilityIdentifier("settings.done")
                        .padding().background(Color(uiColor: .systemBackground))
                    }
                }
                .navigationTitle("Settings")
                .navigationBarTitleDisplayMode(.inline)
                .toolbarBackground(Color(uiColor: .systemBackground), for: .navigationBar)
                .toolbarBackground(.visible, for: .navigationBar)
                .toolbar {
                    if !dynamicTypeSize.isAccessibilitySize {
                        ToolbarItem(placement: .confirmationAction) {
                            Button("Done") { dismiss() }
                                .accessibilityIdentifier("settings.done")
                        }
                    }
                }
            }
            if session.phase == .active || session.phase == .preparing || session.phase == .recovering {
                Button { session.stop(.userPause) } label: {
                    Label("Pause assistance", systemImage: "pause.fill")
                        .frame(maxWidth: .infinity, minHeight: 52)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .buttonStyle(.borderedProminent).controlSize(.large)
                .tint(AssistanceColors.primaryActionFill).foregroundStyle(.white)
                .accessibilityIdentifier("settings.pause")
                .padding().background(Color(uiColor: .systemBackground))
            }
        }
    }
}

private struct SettingsContents: View {
    @ObservedObject var dependencies: AppDependencies
    @ObservedObject var session: SessionCoordinator
    @ObservedObject var preferences: UserPreferences
    var body: some View {
        Form {
            Section("Preferences") {
                Picker("Guiding hand", selection: $preferences.guidingHand) {
                    ForEach(UserPreferences.Hand.allCases, id: \.self) { Text($0.rawValue.capitalized).tag($0) }
                }.pickerStyle(.inline).disabled(session.phase.canEnd && session.phase != .failed)
                Picker("Phone position", selection: $preferences.phonePosition) {
                    ForEach(UserPreferences.PhonePosition.allCases, id: \.self) { Text($0.label).tag($0) }
                }.pickerStyle(.inline).disabled(session.phase.canEnd && session.phase != .failed)
                Toggle("Show captions", isOn: $preferences.captions)
            }
            Section("Wristbands") {
                NavigationLink("Set up wristbands") { BandSetupView(bands: session.bands, preferences: preferences) }
                    .accessibilityIdentifier("settings.bands")
                NavigationLink("Calibrate patterns") { CalibrationView(session: session) }
                    .accessibilityIdentifier("settings.calibration")
            }
            Section("Hardware test") {
                NavigationLink("Arduino 101") { Arduino101SetupView() }
                    .accessibilityIdentifier("settings.arduino101")
            }
            Section("Simulation") {
                Toggle("Use simulated assistance", isOn: Binding(get: { dependencies.simulated }, set: dependencies.setSimulation))
                    .accessibilityIdentifier("settings.simulation")
                    .disabled(session.phase.canEnd && session.phase != .failed)
                Text("Simulation uses sample scenes and typed requests. No physical camera or wristband guidance is active. End the current session before changing modes.")
            }
            Section("Availability") {
                LabeledContent("Camera", value: session.capabilities.perception.label)
                LabeledContent("Conversation", value: session.capabilities.conversation.label)
                LabeledContent("Microphone", value: session.capabilities.microphone.label)
                LabeledContent("Spoken offline Stop", value: session.capabilities.offlineStop.label)
                LabeledContent("Left band", value: session.capabilities.leftBand.label)
                LabeledContent("Right band", value: session.capabilities.rightBand.label)
            }
            #if DEBUG
            Section("Developer diagnostics") {
                NavigationLink("Integration diagnostics") { DiagnosticsView(session: session, diagnostics: session.diagnostics) }
            }
            #endif
        }
    }
}

struct BandSetupView: View {
    @ObservedObject var bands: BandService
    @ObservedObject var preferences: UserPreferences
    @State private var error: String?
    var body: some View {
        Form {
            Section {
                Text(bands.summary)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("bands.summary")
                Text("Use the tactile marker to identify the left band. Advertised names are hints; confirm each physical side before assignment.")
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("bands.instructions")
                Button("Discover wristbands") { bands.discover() }.foregroundStyle(Color.primary).frame(minHeight: 44).accessibilityIdentifier("bands.discover")
            }
            if let error { Section { Text(error) } }
            ForEach(bands.discovered, id: \.id) { band in
                Section(band.nameHint ?? "Wristband") {
                    Text(band.id.uuidString).font(.caption).textSelection(.enabled)
                    Button("Confirm this is my left band") { assign(band.id, .left) }.foregroundStyle(Color.primary).accessibilityIdentifier("bands.confirm.left")
                    Button("Confirm this is my right band") { assign(band.id, .right) }.foregroundStyle(Color.primary).accessibilityIdentifier("bands.confirm.right")
                }
            }
            ForEach(bands.pairings, id: \.side) { pairing in
                Section("Confirmed \(pairing.side.rawValue) band") {
                    Button("Forget \(pairing.side.rawValue) band", role: .destructive) {
                        bands.forget(pairing.side); preferences.invalidateCalibration()
                    }
                }
            }
        }.navigationTitle("Wristbands")
    }
    private func assign(_ id: UUID, _ side: BandSide) {
        do { try bands.assign(id, side: side); preferences.invalidateCalibration(); error = nil }
        catch { self.error = String(describing: error) }
    }
}

struct CalibrationView: View {
    @ObservedObject var session: SessionCoordinator
    private let answers = ["Left", "Right", "Move left", "Move right", "Move up", "Move down", "Closer", "On target", "Success", "Stop", "Lost target or hand"]
    var body: some View {
        VStack(spacing: 0) {
            calibrationForm
            if session.calibrationIndex != nil {
                stopButton
            }
        }
        .navigationTitle("Calibration")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Color(uiColor: .systemBackground), for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .onDisappear { if session.calibrationIndex != nil { session.stopCalibration() } }
    }

    private var calibrationForm: some View {
        Form {
            if session.simulated { SimulationLabel() }
            Section {
                Text("Practice each side and all nine patterns. Identify the side or meaning after each scheduled pattern. Assistance remains paused during calibration.")
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("calibration.instructions")
                if !session.calibrationMessage.isEmpty {
                    Text(session.calibrationMessage)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("calibration.message")
                }
                if session.calibrationIndex == nil {
                    Button(action: session.beginCalibration) { Text("Begin calibration").foregroundStyle(Color.primary) }.frame(minHeight: 44).accessibilityIdentifier("calibration.start")
                } else {
                    Text("Check \((session.calibrationIndex ?? 0) + 1) of 11")
                        .accessibilityIdentifier("calibration.progress")
                    Button(action: session.playCalibration) { Text("Repeat pattern").foregroundStyle(Color.primary) }
                        .frame(minHeight: 44)
                        .accessibilityIdentifier("calibration.repeat")
                    ForEach(answers, id: \.self) { answer in
                        Button { session.answerCalibration(answer) } label: { Text(answer).foregroundStyle(Color.primary) }
                            .frame(minHeight: 44)
                            .accessibilityIdentifier("calibration.answer.\(answer)")
                    }
                }
            }
        }
        .clipped()
    }

    private var stopButton: some View {
        Button(role: .destructive, action: session.stopCalibration) {
            Label("Stop calibration", systemImage: "stop.circle")
                .frame(maxWidth: .infinity, minHeight: 52)
                .fixedSize(horizontal: false, vertical: true)
        }
        .buttonStyle(.borderedProminent).controlSize(.large)
        .tint(AssistanceColors.primaryActionFill).foregroundStyle(.white)
        .accessibilityIdentifier("calibration.stop")
        .padding().background(Color(uiColor: .systemBackground))
    }
}

struct DiagnosticsView: View {
    @ObservedObject var session: SessionCoordinator
    @ObservedObject var diagnostics: Diagnostics
    var body: some View {
        List {
            Section("Integration") {
                Text("R2 adapter projections require mapping to R1/R3/H1 implementations.")
                if let camera = session.camera {
                    Text(camera.selectedLens)
                    Text(camera.fallbackReason ?? "No camera fallback reported")
                }
                Text("\(diagnostics.events.count) bounded metadata events. No raw images, audio, transcripts or credentials are exported.")
                ShareLink("Export diagnostics", item: diagnostics.export())
            }
            if session.simulated {
                Section("Fixture failures") {
                    Button("Simulate tracking loss") { (session.perception as? FixturePerception)?.inject(.trackingLost) }
                    Button("Simulate hazard") { (session.perception as? FixturePerception)?.inject(.hazard) }
                    Button("Restore fixture tracking") { (session.perception as? FixturePerception)?.recover() }
                    Button("Simulate band dropout") { session.stop(.bandFailure) }
                }
            }
        }.navigationTitle("Diagnostics")
    }
}
