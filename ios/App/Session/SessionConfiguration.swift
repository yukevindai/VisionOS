import Foundation

struct SessionConfiguration {
    let startupTimeoutMs: Int64
    let reconnectDelaysMs: [Int64]
    init(startupTimeoutMs: Int64 = 5_000, reconnectDelaysMs: [Int64] = [250, 500, 1_000]) {
        precondition(startupTimeoutMs > 0 && reconnectDelaysMs.count <= 3 && reconnectDelaysMs.allSatisfy { $0 >= 0 })
        self.startupTimeoutMs = startupTimeoutMs
        self.reconnectDelaysMs = reconnectDelaysMs
    }
}

/// Supplied by the R3 adapter from its measured/supported transport constraints.
struct FrameDeliveryPolicy {
    let minimumIntervalMs: Int64
    let maximumEncodedBytes: Int
    init(minimumIntervalMs: Int64, maximumEncodedBytes: Int) {
        precondition(minimumIntervalMs > 0 && maximumEncodedBytes > 0)
        self.minimumIntervalMs = minimumIntervalMs; self.maximumEncodedBytes = maximumEncodedBytes
    }
}
