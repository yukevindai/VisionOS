import Foundation

struct DiagnosticEvent: Codable {
    let name: String
    let sessionID: String?
    let taskID: String?
    let generation: UInt64?
    let requestID: String?
    let frameID: String?
    let monotonicMs: Int64
    let code: String?
}

/// Accept structured metadata only. Never pass speech, transcripts, image data, or credentials.
@MainActor
final class Diagnostics: ObservableObject {
    @Published private(set) var events: [DiagnosticEvent] = []
    private let capacity: Int
    init(capacity: Int = 256) { self.capacity = max(1, capacity) }
    func record(_ name: String, context: SessionContext?, now: Int64, taskID: String? = nil,
                requestID: String? = nil, frameID: String? = nil, code: String? = nil) {
        if events.count == capacity { events.removeFirst() }
        events.append(.init(name: name, sessionID: context?.sessionID, taskID: taskID,
                            generation: context?.generation, requestID: requestID,
                            frameID: frameID, monotonicMs: now, code: code))
    }
    func export() -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return (try? String(data: encoder.encode(events), encoding: .utf8)) ?? "[]"
    }
}
