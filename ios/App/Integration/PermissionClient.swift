import AVFoundation
import UIKit
import Speech

enum PermissionAuthorization: String, Equatable {
    case notDetermined = "Not requested"
    case authorized = "Allowed"
    case denied = "Denied"
    case restricted = "Restricted"
}

struct PermissionState: Equatable {
    var cameraAuthorization: PermissionAuthorization
    var microphoneAuthorization: PermissionAuthorization
    var speechAuthorization: PermissionAuthorization = .authorized
    var camera: Bool { cameraAuthorization == .authorized }
    var microphone: Bool { microphoneAuthorization == .authorized }
    var ready: Bool { camera && microphone && speechAuthorization == .authorized }
    var canRequest: Bool { cameraAuthorization == .notDetermined || microphoneAuthorization == .notDetermined || speechAuthorization == .notDetermined }
    var needsSettings: Bool { cameraAuthorization == .denied || microphoneAuthorization == .denied || speechAuthorization == .denied }

    init(camera: Bool, microphone: Bool) {
        self.init(cameraAuthorization: camera ? .authorized : .denied,
                  microphoneAuthorization: microphone ? .authorized : .denied)
    }

    init(cameraAuthorization: PermissionAuthorization, microphoneAuthorization: PermissionAuthorization) {
        self.cameraAuthorization = cameraAuthorization
        self.microphoneAuthorization = microphoneAuthorization
    }

    var explanation: String {
        guard !ready else { return "Camera and microphone access are enabled." }
        return [reason("Camera", cameraAuthorization), reason("Microphone", microphoneAuthorization), reason("Speech recognition", speechAuthorization)]
            .compactMap { $0 }.joined(separator: " ")
    }

    private func reason(_ resource: String, _ authorization: PermissionAuthorization) -> String? {
        switch authorization {
        case .authorized: return nil
        case .notDetermined: return "\(resource) access has not been requested. Allow access to start assistance."
        case .denied: return "\(resource) access is denied. Enable it in Settings to start assistance."
        case .restricted: return "\(resource) access is restricted by device policy. Check device restrictions or contact your administrator."
        }
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
        let camera: PermissionAuthorization
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: camera = .authorized
        case .notDetermined: camera = .notDetermined
        case .restricted: camera = .restricted
        case .denied: camera = .denied
        @unknown default: camera = .restricted
        }
        let microphone: PermissionAuthorization
        switch AVAudioApplication.shared.recordPermission {
        case .granted: microphone = .authorized
        case .undetermined: microphone = .notDetermined
        case .denied: microphone = .denied
        @unknown default: microphone = .restricted
        }
        var state = PermissionState(cameraAuthorization: camera, microphoneAuthorization: microphone)
        switch SFSpeechRecognizer.authorizationStatus() {
        case .authorized: state.speechAuthorization = .authorized
        case .notDetermined: state.speechAuthorization = .notDetermined
        case .denied: state.speechAuthorization = .denied
        case .restricted: state.speechAuthorization = .restricted
        @unknown default: state.speechAuthorization = .restricted
        }
        return state
    }
    func request() async -> PermissionState {
        if AVCaptureDevice.authorizationStatus(for: .video) == .notDetermined {
            _ = await AVCaptureDevice.requestAccess(for: .video)
        }
        if AVAudioApplication.shared.recordPermission == .undetermined {
            _ = await AVAudioApplication.requestRecordPermission()
        }
        if SFSpeechRecognizer.authorizationStatus() == .notDetermined {
            _ = await withCheckedContinuation { continuation in
                SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0) }
            }
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

#if DEBUG
/// UI automation only. Never asks iOS for protected-resource access.
@MainActor
final class UITestPermissionClient: PermissionClient {
    enum Scenario: String {
        case denied, cameraDenied = "camera-denied", microphoneDenied = "microphone-denied"
        case restricted, requestable
    }
    private var state: PermissionState

    init(scenario: Scenario) {
        switch scenario {
        case .denied: state = .init(camera: false, microphone: false)
        case .cameraDenied: state = .init(camera: false, microphone: true)
        case .microphoneDenied: state = .init(camera: true, microphone: false)
        case .restricted: state = .init(cameraAuthorization: .restricted, microphoneAuthorization: .authorized)
        case .requestable: state = .init(cameraAuthorization: .notDetermined, microphoneAuthorization: .notDetermined)
        }
    }

    func current() -> PermissionState { state }
    func request() async -> PermissionState {
        if state.cameraAuthorization == .notDetermined { state.cameraAuthorization = .authorized }
        if state.microphoneAuthorization == .notDetermined { state.microphoneAuthorization = .authorized }
        return state
    }
    func simulateSettingsRecovery() { state = .init(camera: true, microphone: true) }
}
#endif
