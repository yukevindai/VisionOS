import Foundation

struct SessionContext: Equatable, Sendable {
    let sessionID: String
    let generation: UInt64
}

enum SessionPhase: String, Equatable {
    case idle, preparing, active, paused, recovering, ending, ended, failed
    var canEnd: Bool { ![.idle, .ended].contains(self) }
}

enum ActivityStatus: String {
    case preparing = "Preparing"
    case listening = "Listening"
    case thinking = "Thinking"
    case speaking = "Speaking"
    case guiding = "Guiding"
    case paused = "Paused"
    case reconnecting = "Reconnecting"
    case ended = "Ended"
}

enum Capability: Equatable {
    case unavailable(String), preparing, ready, simulated
    var usable: Bool { self == .ready || self == .simulated }
    var label: String {
        switch self {
        case .unavailable(let reason): return reason
        case .preparing: return "Preparing"
        case .ready: return "Ready"
        case .simulated: return "SIMULATED"
        }
    }
}

struct SessionCapabilities {
    var perception: Capability = .unavailable("Camera guidance adapter unavailable")
    var conversation: Capability = .unavailable("Conversation adapter unavailable")
    var microphone: Capability = .unavailable("Shared audio input unavailable")
    var offlineStop: Capability = .unavailable("Offline spoken Stop is not integrated")
    var leftBand: Capability = .unavailable("Left band unavailable")
    var rightBand: Capability = .unavailable("Right band unavailable")
}

enum LocalCommand: String, Sendable {
    case stop, pause, resume, repeatLast, cancelTask, end
}

enum SessionStopCause: String {
    case userPause, userEnd, localStop, cancelTask, trackingLost, hazard, targetLost
    case bandFailure, background, audioInterruption, audioRouteLost, microphoneMuted
    case cameraChanged, providerFailure, taskReplacement, calibration, generationExhausted
}

struct IntegrationUnavailable: LocalizedError {
    let component: String
    var errorDescription: String? { "\(component) is not connected to this build. Supply the teammate adapter to enable live assistance." }
}
