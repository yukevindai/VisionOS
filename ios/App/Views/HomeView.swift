import SwiftUI

struct HomeView: View {
    @ObservedObject var dependencies: AppDependencies
    @ObservedObject var session: SessionCoordinator
    @State private var settingsPresented = false
    @State private var videoPresented = false
    @State private var targetReaderPresented = false
    @AccessibilityFocusState private var startFocused: Bool
    var body: some View {
        NavigationStack {
            Group {
                if session.phase.canEnd && session.phase != .failed {
                    AssistanceSessionView(session: session, preferences: dependencies.preferences)
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 28) {
                            if dependencies.simulated { SimulationLabel() }
                            Text("Help at your pace.").font(.title2.bold())
                                .fixedSize(horizontal: false, vertical: true)
                                .accessibilityAddTraits(.isHeader)
                            Text("Ask about what’s in front of you.").font(.body)
                                .fixedSize(horizontal: false, vertical: true)
                            if session.phase == .failed || session.phase == .ended {
                                Text(session.message).foregroundStyle(.primary).accessibilityIdentifier("session.error")
                            }
                        }.padding(24).frame(maxWidth: 650)
                    }
                    .safeAreaInset(edge: .bottom) {
                        VStack(spacing: 10) {
                            Button(action: session.start) {
                                Label("Start assistance", systemImage: "waveform")
                                    .frame(maxWidth: .infinity, minHeight: 52)
                            }
                            .buttonStyle(.borderedProminent).controlSize(.large)
                            .tint(AssistanceColors.primaryActionFill).foregroundStyle(.white)
                            .accessibilityIdentifier("home.start")
                            .accessibilityHint(dependencies.simulated ? "Starts simulated assistance with typed requests. No real camera or microphone." : "Starts an assistance session using the rear camera.")
                            .accessibilityFocused($startFocused)
                            if !dependencies.simulated && (session.phase == .idle || session.phase == .ended || session.phase == .failed) {
                                Button { videoPresented = true } label: {
                                    Label("Share live video with a friend", systemImage: "video")
                                        .frame(maxWidth: .infinity, minHeight: 48)
                                }
                                .buttonStyle(.bordered)
                                .accessibilityIdentifier("home.friendVideo")
                                Button { targetReaderPresented = true } label: {
                                    Label("Read a camera image", systemImage: "viewfinder")
                                        .frame(maxWidth: .infinity, minHeight: 48)
                                }
                                .buttonStyle(.bordered)
                                .accessibilityIdentifier("home.targetReader")
                            }
                        }
                        .padding().background(.regularMaterial)
                    }
                }
            }
            .navigationTitle("Reflex")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { settingsPresented = true } label: { Label("Settings", systemImage: "gearshape") }
                        .accessibilityIdentifier("home.settings")
                }
            }
            .sheet(isPresented: $settingsPresented) { SettingsView(dependencies: dependencies) }
            .fullScreenCover(isPresented: $videoPresented) { OwnerVideoDemoView() }
            .fullScreenCover(isPresented: $targetReaderPresented) { TargetReaderView() }
            .sheet(isPresented: Binding(get: { session.showSetup }, set: { if !$0 { session.dismissSetup() } })) {
                SetupView(session: session, preferences: dependencies.preferences)
            }
            .onAppear { startFocused = true }
            .onChange(of: session.phase) { _, phase in
                if phase != .idle && phase != .ended && phase != .failed {
                    videoPresented = false
                    targetReaderPresented = false
                }
            }
        }
        .preferredColorScheme(uiTestingAppearance)
    }

    private var uiTestingAppearance: ColorScheme? {
        #if DEBUG
        let arguments = ProcessInfo.processInfo.arguments
        if arguments.contains("--uitesting"), arguments.contains("--simulate"),
           let option = arguments.firstIndex(of: "--ui-appearance"),
           arguments.indices.contains(option + 1) {
            switch arguments[option + 1] {
            case "dark": return .dark
            case "light": return .light
            default: return nil
            }
        }
        #endif
        return nil
    }
}

enum AssistanceColors {
    static let primaryActionFill = Color(red: 0, green: 90.0 / 255, blue: 190.0 / 255)
}

struct SimulationLabel: View {
    var body: some View {
        Text("SIMULATED")
        .font(.headline).padding(12).frame(maxWidth: .infinity, alignment: .leading)
        .background(.yellow.opacity(0.15), in: RoundedRectangle(cornerRadius: 12))
        .accessibilityLabel("Simulated mode. No real camera or wristbands.")
        .accessibilityIdentifier("simulation.banner")
    }
}
