import Foundation

@MainActor
final class ScheduledWork {
    private var cancellation: (() -> Void)?
    init(_ cancellation: @escaping () -> Void) { self.cancellation = cancellation }
    func cancel() { cancellation?(); cancellation = nil }
    deinit { cancellation?() }
}

@MainActor
protocol AppClock: AnyObject {
    var nowMs: Int64 { get }
    func schedule(afterMs: Int64, _ action: @escaping @MainActor () -> Void) -> ScheduledWork
}

@MainActor
final class SystemAppClock: AppClock {
    var nowMs: Int64 { Int64(ProcessInfo.processInfo.systemUptime * 1_000) }
    func schedule(afterMs: Int64, _ action: @escaping @MainActor () -> Void) -> ScheduledWork {
        let task = Task { @MainActor in
            do { try await Task.sleep(nanoseconds: UInt64(max(0, afterMs)) * 1_000_000) }
            catch { return }
            guard !Task.isCancelled else { return }
            action()
        }
        return ScheduledWork { task.cancel() }
    }
}
