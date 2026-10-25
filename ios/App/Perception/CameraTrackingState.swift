import Foundation

/// Capture metadata proves camera liveness only; it carries no target, hand, or depth evidence.
struct CameraTrackingState {
    let context: SessionContext
    let geometryID: String
    let capturedAtMs: Int64
    let status: Status

    enum Status: String {
        case normal, initializing, excessiveMotion, insufficientFeatures, relocalizing, unavailable
    }

    func isActionable(at now: Int64) -> Bool {
        capturedAtMs >= 0 && capturedAtMs <= now && (status != .normal || isFresh(at: now))
    }

    func isFresh(at now: Int64) -> Bool {
        capturedAtMs >= 0 && capturedAtMs <= now && now - capturedAtMs < 300
    }
}

@MainActor
protocol CameraTrackingSource: AnyObject {
    var onTrackingState: ((CameraTrackingState) -> Void)? { get set }
    var onTrackingIssue: ((SessionContext, String) -> Void)? { get set }
}

/// Keep the latest heartbeat, plus an observed loss so recovery cannot overwrite a stop.
final class CameraTrackingMailbox: @unchecked Sendable {
    private let lock = NSLock()
    private var latest: CameraTrackingState?
    private var loss: CameraTrackingState?
    private var queued = false

    func offer(_ state: CameraTrackingState) -> Bool {
        lock.lock(); defer { lock.unlock() }
        if let latest, latest.context != state.context || latest.geometryID != state.geometryID {
            loss = nil
        }
        if state.status != .normal, loss == nil { loss = state }
        latest = state
        guard !queued else { return false }
        queued = true
        return true
    }

    func take() -> [CameraTrackingState] {
        lock.lock(); defer { lock.unlock() }
        defer { latest = nil; loss = nil; queued = false }
        guard let latest else { return [] }
        if let loss, loss.capturedAtMs != latest.capturedAtMs || loss.status != latest.status { return [loss, latest] }
        return [latest]
    }
}
