import SwiftUI

struct HomeView: View {
    private enum Role { case assisted, helper }

    @ObservedObject var dependencies: AppDependencies
    @ObservedObject var session: SessionCoordinator
    @State private var role: Role?
    @State private var settingsPresented = false
    @State private var toolsPresented = false
    @AccessibilityFocusState private var callFocused: Bool
    @ScaledMetric(relativeTo: .largeTitle) private var callTitleSize = 48

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
            .background((role == .assisted ? Color.black : AssistanceColors.canvas).ignoresSafeArea())
            .toolbarColorScheme(role == .assisted ? .dark : nil, for: .navigationBar)
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
        GeometryReader { geometry in
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    brand
                    Text("Choose your view")
                        .font(.title.bold())
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader)
                    roleChoice("Blind", explanation: "Get voice guidance for everyday tasks.",
                               primary: true, height: geometry.size.height * 0.56) {
                        role = .assisted
                    }
                    roleChoice("Not blind", explanation: "Join a shared session and help someone.",
                               primary: false, height: geometry.size.height * 0.17) {
                        role = .helper
                    }
                }
                .padding(20)
                .frame(maxWidth: 650, minHeight: geometry.size.height, alignment: .center)
                .frame(maxWidth: .infinity)
            }
        }
        .accessibilityIdentifier("role.selection")
    }

    private func roleChoice(_ title: String, explanation: String, primary: Bool,
                            height: CGFloat, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.largeTitle.bold())
                .fixedSize(horizontal: false, vertical: true)
                .padding(20)
                .frame(maxWidth: .infinity, minHeight: max(88, height))
                .foregroundStyle(primary ? Color.white : Color.primary)
                .background(primary ? AssistanceColors.primaryActionFill : Color(uiColor: .secondarySystemGroupedBackground),
                            in: RoundedRectangle(cornerRadius: 24))
                .overlay {
                    if !primary {
                        RoundedRectangle(cornerRadius: 24).stroke(.primary, lineWidth: 2)
                    }
                }
                .contentShape(RoundedRectangle(cornerRadius: 24))
        }
        .buttonStyle(.plain)
        .accessibilityHint(explanation)
        .accessibilityIdentifier(primary ? "role.blind" : "role.helper")
    }

    private var callLanding: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if dependencies.simulated { SimulationLabel() }
                    if session.phase == .failed || session.phase == .ended {
                        Text(session.message).font(.body)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityIdentifier("session.error")
                        if session.requiresPermissions && session.permissionState.needsSettings {
                            permissionRecovery
                        }
                    }
                    Button(action: session.start) {
                        Text("Call")
                            .font(.system(size: callTitleSize, weight: .bold))
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(24)
                            .frame(maxWidth: .infinity,
                                   minHeight: max(260, geometry.size.height - (showsCallNotice ? 180 : 32)))
                            .foregroundStyle(.white)
                            .background(AssistanceColors.primaryActionFill,
                                        in: RoundedRectangle(cornerRadius: 24))
                            .contentShape(RoundedRectangle(cornerRadius: 24))
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Call")
                    .accessibilityHint(dependencies.simulated
                                       ? "Starts simulated assistance. The microphone is not listening."
                                       : "Starts voice assistance and requests camera, microphone and speech recognition access if needed.")
                    .accessibilityIdentifier("home.start")
                    .accessibilityFocused($callFocused)
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 16)
                .frame(maxWidth: 650, minHeight: geometry.size.height, alignment: .topLeading)
                .frame(maxWidth: .infinity)
            }
        }
        .environment(\.colorScheme, .dark)
        .onAppear { callFocused = true }
    }

    private var showsCallNotice: Bool {
        dependencies.simulated || session.phase == .failed || session.phase == .ended
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
        Text("VisionOS").font(.subheadline.weight(.semibold))
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
