import SwiftUI
import AVFoundation

@main
struct ReflexApp: App {
    @StateObject private var dependencies = AppDependencies()
    @Environment(\.scenePhase) private var scenePhase
    var body: some Scene {
        WindowGroup {
            #if DEBUG
            if ProcessInfo.processInfo.arguments.contains("--native-device-check") {
                NativeDeviceCheckView()
            } else {
                normalContent
            }
            #else
            normalContent
            #endif
        }
    }
    private var normalContent: some View {
            HomeView(dependencies: dependencies, session: dependencies.session)
                .onChange(of: scenePhase) { _, new in
                    if new != .active { dependencies.session.backgrounded() }
                    else { dependencies.session.refreshPermissions() }
                }
                .onReceive(NotificationCenter.default.publisher(for: AVAudioSession.interruptionNotification)) { notification in
                    if let raw = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                       AVAudioSession.InterruptionType(rawValue: raw) == .began {
                        dependencies.session.stop(.audioInterruption)
                    }
                }
                .onReceive(NotificationCenter.default.publisher(for: AVAudioSession.routeChangeNotification)) { notification in
                    if let raw = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
                       AVAudioSession.RouteChangeReason(rawValue: raw) == .oldDeviceUnavailable {
                        dependencies.session.stop(.audioRouteLost)
                    }
                }
    }
}
