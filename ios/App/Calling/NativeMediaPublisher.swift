import Foundation
import CoreVideo

enum NativeMediaVideoRotation { case degrees0, degrees90, degrees180, degrees270 }

struct NativeMediaVideoFrame {
    let localSessionID: String
    let pixelBuffer: CVPixelBuffer
    /// Original R1 capture instant mapped to the app monotonic clock.
    let capturedAtMs: Int64
    let rotation: NativeMediaVideoRotation
}

struct NativeMediaMembershipChange: Sendable {
    let identity: String
    let participantSID: String
    let joined: Bool
    let gateRevision: UInt64
}

/// R1 supplies a bounded, coalesced native frame tap. No camera is opened here.
@MainActor
protocol NativeMediaCaptureClient: AnyObject {
    func attach(localSessionID: String, receive: @escaping (NativeMediaVideoFrame) -> Void)
    func detach()
}

@MainActor
protocol NativeMediaDriver: AnyObject {
    var onInvalidated: ((UUID) -> Void)? { get set }
    var onMembershipChanged: ((UUID, NativeMediaMembershipChange) -> Void)? { get set }
    func connect(token: NativeMediaToken, attempt: UUID, gate: NativeMediaSampleGate) async throws
    func disconnect(attempt: UUID) async
    func sendVideo(_ pixelBuffer: CVPixelBuffer, capturedAtMs: Int64,
                   rotation: NativeMediaVideoRotation, attempt: UUID)
}

/// Per-sample admission is independent of MainActor/watchdog scheduling.
/// The clock must be thread safe; body must be synchronous nonblocking SDK ingress.
final class NativeMediaSampleGate: @unchecked Sendable {
    let attempt: UUID
    private let lock = NSLock()
    private let nowMs: @Sendable () -> Int64
    private var deadline: Int64?
    private var terminal = false
    private var suspended = true
    private var authorizationRevision: UInt64 = 0

    init(attempt: UUID, nowMs: @escaping @Sendable () -> Int64 = {
        Int64(ProcessInfo.processInfo.systemUptime * 1_000)
    }) { self.attempt = attempt; self.nowMs = nowMs }

    @discardableResult
    func authorize(untilMs: Int64, expectedRevision: UInt64? = nil) -> Bool {
        lock.lock(); defer { lock.unlock() }
        let now = nowMs()
        if let deadline, deadline <= now { terminal = true }
        guard !terminal, untilMs > now, untilMs - now <= 15_000,
              expectedRevision.map({ $0 == authorizationRevision }) ?? true
        else { return false }
        deadline = untilMs
        suspended = false
        return true
    }

    @discardableResult
    func suspend(expectedRevision: UInt64? = nil) -> UInt64? {
        lock.lock(); defer { lock.unlock() }
        guard !terminal, expectedRevision.map({ $0 == authorizationRevision }) ?? true else { return nil }
        suspended = true
        authorizationRevision &+= 1
        return authorizationRevision
    }
    func close() {
        lock.lock(); terminal = true; deadline = nil; authorizationRevision &+= 1; lock.unlock()
    }

    var revision: UInt64 {
        lock.lock(); defer { lock.unlock() }
        return authorizationRevision
    }

    func isOpen(attempt: UUID) -> Bool {
        lock.lock(); defer { lock.unlock() }
        return permits(attempt: attempt, now: nowMs())
    }

    @discardableResult
    func withAdmission(attempt: UUID, capturedAtMs: Int64,
                       expectedRevision: UInt64? = nil, body: () -> Void) -> Bool {
        lock.lock(); defer { lock.unlock() }
        let now = nowMs()
        guard permits(attempt: attempt, now: now), capturedAtMs >= 0,
              capturedAtMs <= now, now - capturedAtMs <= 300,
              expectedRevision.map({ $0 == authorizationRevision }) ?? true
        else { return false }
        body()
        return true
    }

    private func permits(attempt: UUID, now: Int64) -> Bool {
        if let deadline, deadline <= now { terminal = true }
        return !terminal && !suspended && self.attempt == attempt && deadline.map { $0 > now } == true
    }
}

@MainActor
struct NativeMediaIntegration {
    let backend: any NativeMediaBackend
    let driver: any NativeMediaDriver
    let capture: any NativeMediaCaptureClient
}
