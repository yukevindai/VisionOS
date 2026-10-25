import SwiftUI

struct AssistanceSessionView: View {
    @ObservedObject var session: SessionCoordinator
    @ObservedObject var preferences: UserPreferences
    var openSettings: () -> Void
    @State private var fixtureInput = ""
    @State private var detailsPresented = false
    @State private var settingsRequested = false
    @FocusState private var fixtureInputFocused: Bool
    @AccessibilityFocusState private var statusFocused: Bool
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                cameraBackground(size: geometry.size)
                VStack(spacing: 12) {
                    statusIsland
                        .padding(.horizontal, 20)
                        .padding(.top, 12)
                    Spacer(minLength: 0)
                    ScrollView {
                        VStack(spacing: 12) {
                            Text(session.message)
                                .font(.body)
                                .fixedSize(horizontal: false, vertical: true)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(16)
                                .cameraIsland(opaque: true)
                                .accessibilityIdentifier("session.message")
                            if preferences.captions, !session.caption.isEmpty,
                               session.caption != session.message {
                                Text(session.caption)
                                    .font(.title3)
                                    .fixedSize(horizontal: false, vertical: true)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .textSelection(.enabled)
                                    .padding(16)
                                    .cameraIsland(opaque: true)
                                    .accessibilityIdentifier("session.caption")
                            }
                            confirmation
                            if session.simulated { fixtureControls }
                        }
                        .frame(maxWidth: 650)
                        .padding(.horizontal, 20)
                        .frame(maxWidth: .infinity)
                    }
                    .defaultScrollAnchor(.bottom)
                    .frame(maxHeight: geometry.size.height * 0.50)
                    .clipped()
                    .accessibilityIdentifier("session.content")
                    controlsIsland
                        .frame(maxWidth: 650)
                        .padding(.horizontal, 16)
                        .padding(.bottom, 12)
                        .frame(maxWidth: .infinity)
                }
            }
        }
        .environment(\.colorScheme, .dark)
        .sheet(isPresented: $detailsPresented, onDismiss: {
            if settingsRequested {
                settingsRequested = false
                openSettings()
            } else {
                statusFocused = true
            }
        }) {
            callDetails
        }
        .onAppear { statusFocused = true }
        .onChange(of: session.phase) { _, phase in
            if phase == .ended || phase == .failed { detailsPresented = false }
        }
    }

    private func cameraBackground(size: CGSize) -> some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if let preview = session.preview {
                Image(uiImage: preview)
                    .resizable()
                    .scaledToFit()
                    .frame(width: size.width, height: size.height)
            } else {
                VStack(spacing: 12) {
                    Image(systemName: "camera")
                        .font(.system(size: 38))
                }
                .foregroundStyle(.white)
                .padding(32)
            }
        }
        .accessibilityHidden(true)
        .allowsHitTesting(false)
    }

    private var statusIsland: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                if session.simulated {
                    Text("SIMULATED")
                        .font(.caption.weight(.semibold))
                        .fixedSize(horizontal: false, vertical: true)
                        .foregroundStyle(.yellow)
                        .accessibilityLabel("Simulated")
                        .accessibilityHint("No real camera or wristbands.")
                        .accessibilityIdentifier("simulation.banner")
                }
                Text(session.activity.rawValue)
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                    .accessibilityIdentifier("session.status")
                    .accessibilityFocused($statusFocused)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)
            .cameraIsland(cornerRadius: 24, opaque: true)
            Spacer(minLength: 0)
            Button { detailsPresented = true } label: {
                Image(systemName: "ellipsis")
                    .font(.title2.weight(.semibold))
                    .frame(width: 52, height: 52)
                    .cameraIsland(cornerRadius: 26, opaque: true)
                    .contentShape(Circle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Call details")
            .accessibilityHint("Microphone, captions, settings and connection status. Stop and End call remain available.")
            .accessibilityIdentifier("session.details")
        }
        .frame(maxWidth: 650)
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder
    private var confirmation: some View {
        if let confirmation = session.taskState?.confirmation {
            VStack(alignment: .leading, spacing: 12) {
                Text(confirmation.question)
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                adaptiveControlLayout {
                    Button { session.confirmCurrent(true) } label: {
                        Text("Yes").frame(maxWidth: .infinity, minHeight: 52)
                    }
                    Button { session.confirmCurrent(false) } label: {
                        Text("No").frame(maxWidth: .infinity, minHeight: 52)
                    }
                }
                .buttonStyle(.bordered)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
            .cameraIsland()
        }
    }

    private var controlsIsland: some View {
        VStack(alignment: .leading, spacing: 14) {
            if !session.simulated {
                Text(stopAvailability)
                    .font(.subheadline)
                    .fixedSize(horizontal: false, vertical: true)
            }
            sessionControls
        }
        .padding(16)
        .cameraIsland(cornerRadius: 28, opaque: true)
    }

    private var stopAvailability: String {
        if session.microphoneMuted { return "Microphone muted. Use Stop or End call." }
        return session.capabilities.offlineStop.usable
            ? "Say “Stop” at any time."
            : "Use Stop or End call at any time."
    }

    private var adaptiveControlLayout: AnyLayout {
        dynamicTypeSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(spacing: 12))
            : AnyLayout(HStackLayout(spacing: 12))
    }

    private var sessionControls: some View {
        adaptiveControlLayout {
            Button {
                fixtureInputFocused = false
                if session.phase == .paused { session.resume() } else { session.stop(.userPause) }
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: session.phase == .paused ? "play.fill" : "pause.fill")
                        .accessibilityHidden(true)
                    Text(session.phase == .paused ? "Resume" : "Stop")
                        .fixedSize(horizontal: false, vertical: true)
                }
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 12)
                    .frame(maxWidth: .infinity, minHeight: 60)
                    .foregroundStyle(.white)
                    .background(AssistanceColors.primaryActionFill,
                                in: RoundedRectangle(cornerRadius: 16))
                    .contentShape(RoundedRectangle(cornerRadius: 16))
            }
            .buttonStyle(.plain)
            .accessibilityLabel(session.phase == .paused ? "Resume assistance" : "Stop assistance")
            .accessibilityHint(session.phase == .paused ? "Checks current tracking before resuming guidance." : "Stops physical guidance immediately. The session remains open.")
            .accessibilityIdentifier("session.pause")

            Button(role: .destructive) { session.stop(.userEnd) } label: {
                Label("End call", systemImage: "phone.down.fill")
                    .font(.headline)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 12)
                    .frame(maxWidth: .infinity, minHeight: 60)
                    .foregroundStyle(.white)
                    .background(Color(red: 180.0 / 255, green: 35.0 / 255, blue: 50.0 / 255),
                                in: RoundedRectangle(cornerRadius: 16))
                    .contentShape(RoundedRectangle(cornerRadius: 16))
            }
            .buttonStyle(.plain)
            .accessibilityLabel("End call")
            .accessibilityHint("Stops assistance and closes the session immediately.")
            .accessibilityIdentifier("session.end")
        }
    }

    private var fixtureControls: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Simulated voice input").font(.headline)
            Text("Type a request such as “press the blue button”, “pick up the cup”, “read the menu”, or “open the door”. This replaces microphone input in the fixture.")
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("fixture.instructions")
            TextField("Simulated request", text: $fixtureInput,
                      prompt: Text("Simulated request").foregroundColor(Color(uiColor: .label)), axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .foregroundStyle(Color(uiColor: .label))
                .accessibilityLabel("Simulated request")
                .accessibilityIdentifier("fixture.input")
                .focused($fixtureInputFocused)
                .onSubmit(sendFixtureInput)
            Button(action: sendFixtureInput) {
                Text("Send simulated request")
                    .font(.headline)
                    .foregroundStyle(Color(uiColor: .label))
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 12)
                    .frame(minWidth: 44, minHeight: 44)
                    .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color(uiColor: .label), lineWidth: 1))
                    .contentShape(RoundedRectangle(cornerRadius: 12))
            }
            .buttonStyle(.plain)
            .accessibilityIdentifier("fixture.send")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .cameraIsland()
    }

    private var callDetails: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    Toggle("Mute microphone", isOn: Binding(get: { session.microphoneMuted }, set: session.setMuted))
                        .accessibilityHint("Muting pauses guidance. Spoken Stop cannot be heard while muted.")
                        .accessibilityIdentifier("session.mute")
                    if session.microphoneMuted {
                        Text("Guidance is paused. Spoken Stop is unavailable while the microphone is muted. Use Stop or End call.")
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Toggle("Show captions", isOn: $preferences.captions)
                    Button {
                        settingsRequested = true
                        detailsPresented = false
                    } label: {
                        Label("Settings and wristbands", systemImage: "gearshape")
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(minHeight: 44)
                    }
                    .accessibilityIdentifier("session.settings")
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Connection status").font(.headline).accessibilityAddTraits(.isHeader)
                        Text("Camera: \(session.capabilities.perception.label)")
                        Text("Voice assistance: \(session.capabilities.conversation.label)")
                        Text("Spoken Stop: \(session.capabilities.offlineStop.label)")
                    }
                    .fixedSize(horizontal: false, vertical: true)
                }
                .padding(24)
            }
            .accessibilityIdentifier("session.details.content")
            .safeAreaInset(edge: .bottom) {
                sessionControls.padding(16).cameraIsland().padding(16)
            }
            .navigationTitle("Call details")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { detailsPresented = false }
                        .accessibilityIdentifier("session.details.done")
                }
            }
        }
        .presentationDetents(dynamicTypeSize.isAccessibilitySize ? [.large] : [.medium, .large])
        .presentationDragIndicator(.visible)
    }

    private func sendFixtureInput() {
        fixtureInputFocused = false
        session.submitFixtureText(fixtureInput)
        fixtureInput = ""
    }
}
