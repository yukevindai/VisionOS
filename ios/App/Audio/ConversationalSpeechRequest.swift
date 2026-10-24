import Foundation

/// A deadline releases local fallback even if a provider ignores task cancellation.
/// Cancellation itself never speaks: only a still-current request can complete.
@MainActor
final class ConversationalSpeechRequest {
    private let provider: any ConversationalSpeechSynthesizing
    private let clock: any AppClock
    private var requestID: String?
    private var expiresAtMs: Int64?
    private var work: Task<Void, Never>?
    private var deadline: ScheduledWork?
    private var completion: ((Data?) -> Void)?
    var isPending: Bool { requestID != nil }

    init(provider: any ConversationalSpeechSynthesizing, clock: any AppClock) {
        self.provider = provider
        self.clock = clock
    }

    func start(text: String, completion: @escaping (Data?) -> Void) {
        cancel()
        let id = UUID().uuidString
        requestID = id
        expiresAtMs = NativeAudioTiming.deadline(nowMs: clock.nowMs, durationMs: 5_000)
        self.completion = completion
        deadline = clock.schedule(afterMs: 5_000) { [weak self] in
            self?.finish(id: id, audio: nil)
        }
        let provider = provider
        work = Task { [weak self] in
            guard !Task.isCancelled else { return }
            let audio: Data?
            do { audio = try await provider.synthesize(text: text, requestID: id) }
            catch { audio = nil }
            guard !Task.isCancelled else { return }
            self?.finish(id: id, audio: audio)
        }
    }

    func cancel() {
        requestID = nil
        expiresAtMs = nil
        completion = nil
        deadline?.cancel(); deadline = nil
        work?.cancel(); work = nil
    }

    private func finish(id: String, audio: Data?) {
        guard requestID == id else { return }
        let completed = completion
        // The main actor may service a provider continuation before an overdue timer.
        let timelyAudio = expiresAtMs.map { clock.nowMs < $0 } == true ? audio : nil
        cancel()
        completed?(timelyAudio)
    }
}
