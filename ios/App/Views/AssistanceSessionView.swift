import SwiftUI

struct AssistanceSessionView: View {
    @ObservedObject var session: SessionCoordinator
    @ObservedObject var preferences: UserPreferences
    @State private var fixtureInput = ""
    @AccessibilityFocusState private var statusFocused: Bool
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if session.simulated { SimulationLabel() }
                Text(session.activity.rawValue).font(.largeTitle.bold()).accessibilityAddTraits(.isHeader)
                    .accessibilityIdentifier("session.status").accessibilityFocused($statusFocused)
                Text(session.message).font(.body).fixedSize(horizontal: false, vertical: true)
                if let preview = session.preview {
                    Image(uiImage: preview).resizable().scaledToFit().clipShape(RoundedRectangle(cornerRadius: 20))
                        .accessibilityHidden(true)
                } else {
                    Label(session.simulated ? "Simulated camera scene" : "Waiting for the camera view", systemImage: "camera")
                        .frame(maxWidth: .infinity, minHeight: 130)
                        .background(.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 20))
                        .accessibilityHidden(true)
                }
                if preferences.captions, !session.caption.isEmpty {
                    Text(session.caption).font(.title3).textSelection(.enabled)
                        .accessibilityIdentifier("session.caption")
                }
                if !session.simulated {
                    Toggle("Mute microphone", isOn: Binding(get: { session.microphoneMuted }, set: session.setMuted))
                        .accessibilityHint("Muting pauses guidance. Spoken Stop cannot be heard while muted.")
                }
                if let confirmation = session.taskState?.confirmation {
                    VStack(alignment: .leading, spacing: 12) {
                        Text(confirmation.question).font(.headline)
                        HStack {
                            Button("Yes") { session.confirmCurrent(true) }.frame(minWidth: 70, minHeight: 44)
                            Button("No") { session.confirmCurrent(false) }.frame(minWidth: 70, minHeight: 44)
                        }.buttonStyle(.bordered)
                    }
                }
                if session.simulated {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Simulated voice input").font(.headline)
                        Text("Type a request such as “press the blue button”, “pick up the cup”, “read the menu”, or “open the door”. This replaces microphone input in the fixture.")
                            .font(.callout).foregroundStyle(.secondary)
                        TextField("Simulated request", text: $fixtureInput, axis: .vertical)
                            .textFieldStyle(.roundedBorder).accessibilityIdentifier("fixture.input")
                            .onSubmit(sendFixtureInput)
                        Button("Send simulated request", action: sendFixtureInput)
                            .buttonStyle(.bordered).frame(minHeight: 44).accessibilityIdentifier("fixture.send")
                    }
                }
            }.padding(24).frame(maxWidth: 650)
        }
        .safeAreaInset(edge: .bottom) {
            sessionControls.padding().background(.regularMaterial)
        }
        .onAppear { statusFocused = true }
    }
    private var sessionControls: some View {
                VStack(spacing: 12) {
                    Button {
                        if session.phase == .paused { session.resume() } else { session.stop(.userPause) }
                    } label: {
                        Label(session.phase == .paused ? "Resume assistance" : "Pause assistance",
                              systemImage: session.phase == .paused ? "play.fill" : "pause.fill")
                            .frame(maxWidth: .infinity, minHeight: 52)
                    }.buttonStyle(.borderedProminent).controlSize(.large)
                        .accessibilityIdentifier("session.pause")
                    Button(role: .destructive) { session.stop(.userEnd) } label: {
                        Label("End assistance", systemImage: "phone.down.fill").frame(maxWidth: .infinity, minHeight: 52)
                    }.buttonStyle(.bordered).controlSize(.large).accessibilityIdentifier("session.end")
                }
    }
    private func sendFixtureInput() {
        session.submitFixtureText(fixtureInput); fixtureInput = ""
    }
}
