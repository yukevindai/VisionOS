import SwiftUI

struct HomeView: View {
    @ObservedObject var dependencies: AppDependencies
    @ObservedObject var session: SessionCoordinator
    @State private var settingsPresented = false
    @AccessibilityFocusState private var startFocused: Bool
    var body: some View {
        NavigationStack {
            Group {
                if session.phase.canEnd && session.phase != .failed {
                    AssistanceSessionView(session: session, preferences: dependencies.preferences)
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 28) {
                            Image(systemName: "hand.raised.fingers.spread.fill")
                                .font(.system(size: 64)).foregroundStyle(.tint).accessibilityHidden(true)
                            Text("A little guidance.\nMore independence.").font(.largeTitle.bold()).fixedSize(horizontal: false, vertical: true)
                            Text("Talk with an assistant about what’s in front of you. Get help with everyday tasks, at your pace.")
                                .font(.title3).foregroundStyle(.secondary)
                            if dependencies.simulated { SimulationLabel() }
                            if session.phase == .failed {
                                Text(session.message).foregroundStyle(.primary).accessibilityIdentifier("session.error")
                            }
                            Text("You can pause or end at any time.").font(.callout).foregroundStyle(.secondary)
                        }.padding(24).frame(maxWidth: 650)
                    }
                    .safeAreaInset(edge: .bottom) {
                        Button(action: session.start) {
                            Label("Start assistance", systemImage: "waveform")
                                .frame(maxWidth: .infinity, minHeight: 52)
                        }
                        .buttonStyle(.borderedProminent).controlSize(.large)
                        .accessibilityIdentifier("home.start")
                        .accessibilityHint("Starts an assistance session using the rear camera.")
                        .accessibilityFocused($startFocused)
                        .padding().background(.regularMaterial)
                    }
                }
            }
            .navigationTitle("Reflex")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { settingsPresented = true } label: { Label("Settings", systemImage: "gearshape") }
                        .accessibilityIdentifier("home.settings")
                }
            }
            .sheet(isPresented: $settingsPresented) { SettingsView(dependencies: dependencies) }
            .sheet(isPresented: Binding(get: { session.showSetup }, set: { if !$0 { session.dismissSetup() } })) {
                SetupView(session: session, preferences: dependencies.preferences)
            }
            .onAppear { startFocused = true }
        }
    }
}

struct SimulationLabel: View {
    var body: some View {
        Label("SIMULATED", systemImage: "testtube.2")
            .font(.headline).padding(12).frame(maxWidth: .infinity, alignment: .leading)
            .background(.yellow.opacity(0.15), in: RoundedRectangle(cornerRadius: 12))
            .accessibilityLabel("Simulated mode. No real camera or wristbands.")
            .accessibilityIdentifier("simulation.banner")
    }
}
