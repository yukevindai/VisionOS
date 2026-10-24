import AVFoundation
import UIKit

struct PermissionState: Equatable {
    var camera: Bool
    var microphone: Bool
    var ready: Bool { camera && microphone }
    var explanation: String {
        if !camera && !microphone { return "Allow camera and microphone access in Settings to start live assistance." }
        if !camera { return "Camera access is unavailable. Enable it in Settings so the assistant can see the scene." }
        if !microphone { return "Microphone access is unavailable. Enable it in Settings for conversation and spoken commands." }
        return "Camera and microphone access are enabled."
    }
}

@MainActor
protocol PermissionClient {
    func current() -> PermissionState
    func request() async -> PermissionState
}

@MainActor
final class SystemPermissionClient: PermissionClient {
    func current() -> PermissionState {
        .init(camera: AVCaptureDevice.authorizationStatus(for: .video) == .authorized,
              microphone: AVAudioApplication.shared.recordPermission == .granted)
    }
    func request() async -> PermissionState {
        if AVCaptureDevice.authorizationStatus(for: .video) == .notDetermined {
            _ = await AVCaptureDevice.requestAccess(for: .video)
        }
        if AVAudioApplication.shared.recordPermission == .undetermined {
            _ = await AVAudioApplication.requestRecordPermission()
        }
        return current()
    }
}

@MainActor
struct FixturePermissionClient: PermissionClient {
    var state = PermissionState(camera: true, microphone: true)
    func current() -> PermissionState { state }
    func request() async -> PermissionState { state }
}
