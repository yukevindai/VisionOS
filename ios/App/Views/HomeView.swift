import SwiftUI

struct HomeView: View {
    private enum Role { case assisted, helper }

    @ObservedObject var dependencies: AppDependencies
    @ObservedObject var session: SessionCoordinator
    @State private var role: Role?
    @State private var settingsPresented = false
    @State private var toolsPresented = false
    @AccessibilityFocusState private var callFocused: Bool
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    private var sessionVisible: Bool { session.phase.canEnd && session.phase != .failed }

    var body: some View {
        NavigationStack {
            Group {
                if sessionVisible {
                    AssistanceSessionView(session: session, preferences: dependencies.preferences,
                                          openSettings: { settingsPresented = true })
                } else {
                    switch role {
                    case .none: rolePicker
                    case .assisted: callLanding
                    case .helper: FriendSessionsView()
                    }
                }
            }
            .background(AssistanceColors.canvas.ignoresSafeArea())
            .navigationBarTitleDisplayMode(.inline)
            .toolbar(sessionVisible || role == nil ? .hidden : .visible, for: .navigationBar)
            .toolbar {
                if !sessionVisible, role != nil {
                    ToolbarItem(placement: .topBarLeading) {
                        Button {
                            role = nil
                        } label: {
                            Label("Change role", systemImage: "chevron.left")
                        }
                        .frame(minHeight: 44)
                        .accessibilityIdentifier("home.changeRole")
                    }
                    if role == .assisted {
                        ToolbarItem(placement: .topBarTrailing) {
                            Button { toolsPresented = true } label: {
                                Label("More tools", systemImage: "ellipsis.circle")
                            }
                            .frame(minWidth: 44, minHeight: 44)
                            .accessibilityIdentifier("home.tools")
                        }
                        ToolbarItem(placement: .topBarTrailing) {
                            Button { settingsPresented = true } label: {
                                Label("Settings", systemImage: "gearshape")
                            }
                            .frame(minWidth: 44, minHeight: 44)
                            .accessibilityIdentifier("home.settings")
                        }
                    }
                }
            }
            .sheet(isPresented: $settingsPresented) { SettingsView(dependencies: dependencies) }
            .sheet(isPresented: $toolsPresented) { AssistanceToolsView(simulated: dependencies.simulated) }
            .onChange(of: session.phase) { _, phase in
                if phase == .ended || phase == .failed { callFocused = true }
            }
        }
        .preferredColorScheme(uiTestingAppearance)
    }

    private var rolePicker: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                brand
                Text("How will you use Reflex?")
                    .font(.largeTitle.bold())
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                Text("Choose the experience that works for you.")
                    .font(.title3).foregroundStyle(AssistanceColors.secondaryText)
                roleChoice("Blind", explanation: "Get voice guidance for everyday tasks.", primary: true) {
                    role = .assisted
                }
                roleChoice("Not blind", explanation: "Join a shared session and help someone.", primary: false) {
                    role = .helper
                }
            }
            .padding(24)
            .frame(maxWidth: 650, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .accessibilityIdentifier("role.selection")
    }

    private func roleChoice(_ title: String, explanation: String, primary: Bool,
                            action: @escaping () -> Void) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Button(action: action) {
                Text(title)
                    .font(.title.bold())
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, minHeight: 88)
                    .padding(16)
                    .foregroundStyle(primary ? Color.white : Color.primary)
                    .background(primary ? AssistanceColors.primaryActionFill : Color(uiColor: .secondarySystemGroupedBackground),
                                in: RoundedRectangle(cornerRadius: 20))
                    .overlay {
                        if !primary {
                            RoundedRectangle(cornerRadius: 20).stroke(.primary.opacity(0.5), lineWidth: 1)
                        }
                    }
            }
            .buttonStyle(.plain)
            .accessibilityHint(explanation)
            .accessibilityIdentifier(primary ? "role.blind" : "role.helper")
            Text(explanation).font(.body).foregroundStyle(AssistanceColors.secondaryText)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var callLanding: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    brand
                    if dependencies.simulated { SimulationLabel() }
                    Text("Ready when you are.")
                        .font(.largeTitle.bold())
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader)
                    if session.phase == .failed || session.phase == .ended {
                        Text(session.message).font(.body)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityIdentifier("session.error")
                        if session.requiresPermissions && session.permissionState.needsSettings {
                            permissionRecovery
                        }
                    }
                    VStack(spacing: 28) {
                        Spacer(minLength: 24)
                        Button(action: session.start) {
                            VStack(spacing: 20) {
                                Image("CallGlyph").resizable().scaledToFit()
                                    .frame(width: 52, height: 52)
                                    .accessibilityHidden(true)
                                Text("Call").font(.largeTitle.bold())
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            .foregroundStyle(.white)
                            .frame(maxWidth: .infinity)
                            .frame(minHeight: dynamicTypeSize.isAccessibilitySize ? 160 : 252)
                            .padding(.vertical, dynamicTypeSize.isAccessibilitySize ? 24 : 0)
                            .background(AssistanceColors.primaryActionFill,
                                        in: RoundedRectangle(cornerRadius: dynamicTypeSize.isAccessibilitySize ? 36 : 126))
                        }
                        .buttonStyle(.plain)
                        .frame(maxWidth: dynamicTypeSize.isAccessibilitySize ? 420 : 252)
                        .accessibilityLabel("Call")
                        .accessibilityHint(dependencies.simulated
                                           ? "Starts simulated assistance. The microphone is not listening."
                                           : "Starts voice assistance and requests camera, microphone and speech recognition access if needed.")
                        .accessibilityIdentifier("home.start")
                        .accessibilityFocused($callFocused)
                        Text(dependencies.simulated ? "Try simulated assistance." : "Voice assistance starts right away.")
                            .font(.title3.weight(.semibold)).multilineTextAlignment(.center)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(dependencies.simulated ? "No real camera, microphone or wristbands." : "Ask to find a cup, can, bottle or bowl.")
                            .font(.body).foregroundStyle(AssistanceColors.secondaryText).multilineTextAlignment(.center)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 24)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
                .padding(24)
                .frame(maxWidth: 650, minHeight: geometry.size.height, alignment: .topLeading)
                .frame(maxWidth: .infinity)
            }
        }
        .onAppear { callFocused = true }
    }

    @ViewBuilder
    private var permissionRecovery: some View {
        #if DEBUG
        if let permissions = session.permissions as? UITestPermissionClient {
            Button("Simulate returning from Settings with access allowed") {
                permissions.simulateSettingsRecovery()
                session.start()
            }
            .frame(minHeight: 44)
            .accessibilityIdentifier("home.permission.simulatedRecovery")
        } else {
            openPermissionSettings
        }
        #else
        openPermissionSettings
        #endif
    }

    private var openPermissionSettings: some View {
        Link("Open Settings", destination: URL(string: UIApplication.openSettingsURLString)!)
            .frame(minHeight: 44)
            .accessibilityIdentifier("home.permission.settings")
    }

    private var brand: some View {
        Text("REFLEX").font(.subheadline.weight(.semibold))
            .foregroundStyle(AssistanceColors.accent)
            .accessibilityHidden(true)
    }

    private var uiTestingAppearance: ColorScheme? {
        #if DEBUG
        let arguments = ProcessInfo.processInfo.arguments
        if arguments.contains("--uitesting"), arguments.contains("--simulate"),
           let option = arguments.firstIndex(of: "--ui-appearance"), arguments.indices.contains(option + 1) {
            return arguments[option + 1] == "dark" ? .dark : .light
        }
        #endif
        return nil
    }
}

private struct AssistanceToolsView: View {
    let simulated: Bool
    @Environment(\.dismiss) private var dismiss
    @State private var videoPresented = false
    @State private var readerPresented = false

    var body: some View {
        NavigationStack {
            List {
                if !simulated {
                    Button { videoPresented = true } label: {
                        Label("Share live video with a friend", systemImage: "video")
                            .frame(minHeight: 44)
                    }
                    .accessibilityIdentifier("home.friendVideo")
                    Button { readerPresented = true } label: {
                        Label("Read a camera image", systemImage: "viewfinder")
                            .frame(minHeight: 44)
                    }
                    .accessibilityIdentifier("home.targetReader")
                    Text("These tools use the camera separately from an assistance call.")
                        .foregroundStyle(AssistanceColors.secondaryText)
                } else {
                    Text("Live camera tools are available when simulation is off in Settings.")
                }
            }
            .navigationTitle("More tools")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Close") { dismiss() }.accessibilityIdentifier("home.tools.close")
                }
            }
            .fullScreenCover(isPresented: $videoPresented) { OwnerVideoDemoView() }
            .fullScreenCover(isPresented: $readerPresented) { TargetReaderView() }
        }
    }
}

enum AssistanceColors {
    static let primaryActionFill = Color(red: 0, green: 90.0 / 255, blue: 190.0 / 255)
    static let canvas = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 17 / 255.0, green: 28 / 255.0, blue: 32 / 255.0, alpha: 1)
            : UIColor(red: 245 / 255.0, green: 245 / 255.0, blue: 242 / 255.0, alpha: 1)
    })
    static let secondaryText = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 190 / 255.0, green: 202 / 255.0, blue: 207 / 255.0, alpha: 1)
            : UIColor(red: 77 / 255.0, green: 92 / 255.0, blue: 96 / 255.0, alpha: 1)
    })
    static let accent = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 156 / 255.0, green: 217 / 255.0, blue: 199 / 255.0, alpha: 1)
            : UIColor(red: 18 / 255.0, green: 106 / 255.0, blue: 91 / 255.0, alpha: 1)
    })
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
