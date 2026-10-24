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

#if DEBUG
/// A logical clock for simulated UI automation only. XCTest can stall the main
/// actor while typing or inspecting accessibility. Advance fixture time in
/// ordered steps, so that delay cannot age a fake camera frame ahead of its
/// producer. Live adapters always use SystemAppClock and its real deadlines.
@MainActor
final class UITestAppClock: AppClock {
    private(set) var nowMs: Int64 = 1_000
    private var nextID = 0
    private var work: [Int: (deadline: Int64, action: @MainActor () -> Void)] = [:]
    private var ticker: Task<Void, Never>?

    init(automaticallyAdvances: Bool = true) {
        if automaticallyAdvances {
            ticker = Task { @MainActor [weak self] in
                while !Task.isCancelled {
                    do { try await Task.sleep(nanoseconds: 50_000_000) }
                    catch { return }
                    guard let self else { return }
                    self.advance(50)
                }
            }
        }
    }

    deinit { ticker?.cancel() }

    func schedule(afterMs: Int64, _ action: @escaping @MainActor () -> Void) -> ScheduledWork {
        nextID += 1
        let id = nextID
        work[id] = (nowMs + max(1, afterMs), action)
        return ScheduledWork { [weak self] in self?.work.removeValue(forKey: id) }
    }

    func advance(_ milliseconds: Int64) {
        precondition(milliseconds >= 0)
        let target = nowMs + milliseconds
        while let first = work.filter({ $0.value.deadline <= target }).min(by: {
            ($0.value.deadline, $0.key) < ($1.value.deadline, $1.key)
        }) {
            nowMs = first.value.deadline
            work.removeValue(forKey: first.key)
            first.value.action()
        }
        nowMs = target
    }
}
#endif
