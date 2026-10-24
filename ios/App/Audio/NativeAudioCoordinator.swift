import AVFoundation
import Foundation
import Speech
import Tasks

/// One microphone engine and one priority-controlled output path for native assistance.
@MainActor
final class NativeAudioCoordinator: NSObject, SpeechSchedulerClient, ConversationClient, AVSpeechSynthesizerDelegate {
    var onCaption: ((SessionContext, String) -> Void)?
    var onEvent: ((ConversationEvent) -> Void)?
    var onFailure: ((SessionContext) -> Void)?
    private var inputReady = false
    var offlineStopReady: Bool {
        guard inputReady, !muted, engine?.isRunning == true, recognizer?.isAvailable == true,
              let recognition else { return false }
        return recognition.state == .starting || recognition.state == .running
    }
    private let clock: any AppClock
    private let synthesizer = AVSpeechSynthesizer()
    private var engine: AVAudioEngine?
    private var recognizer: SFSpeechRecognizer?
    private var recognition: SFSpeechRecognitionTask?
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var context: SessionContext?
    private var audioID: String?
    private var inputEpoch = UUID()
    private var utteranceWork: Task<Void, Never>?
    private var consumedWords = 0
    private var lastTranscription = ""
    private var muted = false
    private var output = NativeAudioOutputState()
    private var activeUtterance: AVSpeechUtterance?
    private var speechToken: UUID?
    private var echoUntilMs: Int64 = 0
    private var stopEchoUntilMs: Int64 = 0
    private var cueExpiry: ScheduledWork?
    private var latestCue: String?
    private var player: AVAudioPlayer?

    init(clock: any AppClock) {
        self.clock = clock
        super.init()
        synthesizer.delegate = self
    }

    func startInput(context: SessionContext) async throws -> SharedAudioInput {
        stopInput()
        var started = false
        defer { if !started { stopInput() } }
        guard SFSpeechRecognizer.authorizationStatus() == .authorized,
              AVAudioApplication.shared.recordPermission == .granted,
              let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US")),
              recognizer.supportsOnDeviceRecognition, recognizer.isAvailable else {
            throw AudioError.offlineRecognitionUnavailable
        }
        let audio = AVAudioSession.sharedInstance()
        try audio.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowBluetoothHFP])
        try audio.setActive(true)
        let engine = AVAudioEngine()
        try engine.inputNode.setVoiceProcessingEnabled(true)
        let format = engine.inputNode.outputFormat(forBus: 0)
        guard format.sampleRate.isFinite, format.sampleRate > 0, format.channelCount > 0 else {
            try? audio.setActive(false, options: .notifyOthersOnDeactivation)
            throw AudioError.microphoneUnavailable
        }
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = true
        request.taskHint = .dictation
        let epoch = UUID()
        inputEpoch = epoch
        self.context = context
        self.recognizer = recognizer
        recognitionRequest = request
        self.engine = engine
        muted = false
        consumedWords = 0
        lastTranscription = ""
        engine.inputNode.installTap(onBus: 0, bufferSize: 1_024, format: format) { buffer, _ in
            request.append(buffer)
        }
        recognition = recognizer.recognitionTask(with: request) { [weak self] result, error in
            let text = result?.bestTranscription.formattedString
            let final = result?.isFinal == true
            Task { @MainActor [weak self] in
                guard let self, self.inputEpoch == epoch else { return }
                if let text { self.receiveTranscript(text) }
                if error != nil || final {
                    let context = self.context
                    self.stopInput()
                    if let context { self.onFailure?(context) }
                }
            }
        }
        do {
            engine.prepare()
            try engine.start()
            try Task.checkCancellation()
            inputReady = true
            let id = UUID().uuidString
            audioID = id
            started = true
            return SharedAudioInput(identifier: id)
        } catch {
            stopInput()
            throw error
        }
    }

    func stopInput() {
        inputEpoch = UUID()
        inputReady = false
        audioID = nil
        utteranceWork?.cancel(); utteranceWork = nil
        recognitionRequest?.endAudio()
        recognition?.cancel(); recognition = nil
        recognitionRequest = nil
        if let engine { engine.stop(); engine.inputNode.removeTap(onBus: 0) }
        engine = nil
        recognizer = nil
        context = nil
        cancelOutput()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    func start(context: SessionContext, audioInput: SharedAudioInput) async throws {
        guard offlineStopReady, self.context == context, audioInput.identifier == audioID else { throw AudioError.offlineRecognitionUnavailable }
        self.context = context
    }
    func updateContext(_ context: SessionContext) {
        guard let current = self.context, current.sessionID == context.sessionID,
              context.generation >= current.generation else { return }
        self.context = context
        utteranceWork?.cancel(); utteranceWork = nil
        consumedWords = lastTranscription.split(separator: " ").count
        cancelOutput()
    }
    func sendLatestFrame(_ frame: FrameSnapshot) async throws {}
    func setMicrophoneMuted(_ muted: Bool) {
        self.muted = muted
        utteranceWork?.cancel(); utteranceWork = nil
        consumedWords = lastTranscription.split(separator: " ").count
        if muted {
            engine?.pause()
            inputReady = false
            cancelOutput()
        } else if let engine, recognition != nil {
            do { try engine.start(); inputReady = true }
            catch { inputReady = false; if let context { onFailure?(context) } }
        }
    }
    func interrupt(context: SessionContext) {
        guard self.context == context else { return }
        utteranceWork?.cancel(); utteranceWork = nil
        consumedWords = lastTranscription.split(separator: " ").count
        cancelOutput()
    }
    func end(context: SessionContext) {
        guard self.context == context else { return }
        stopInput()
    }

    private func receiveTranscript(_ text: String) {
        guard let context, !muted, offlineStopReady else { return }
        let words = text.split(separator: " ").map(String.init)
        if words.count < consumedWords { consumedWords = 0 }
        let fresh = words.dropFirst(consumedWords).joined(separator: " ")
        lastTranscription = text
        guard !fresh.isEmpty else { return }
        if NativeVoiceInput.command(fresh) == .stop {
            consumedWords = words.count
            utteranceWork?.cancel(); utteranceWork = nil
            emit(.command(.stop), context: context)
            return
        }
        if synthesizer.isSpeaking || player?.isPlaying == true || clock.nowMs < echoUntilMs {
            consumedWords = words.count
            return
        }
        utteranceWork?.cancel()
        utteranceWork = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 700_000_000)
            guard !Task.isCancelled, let self, self.context == context, !self.muted,
                  self.offlineStopReady, !self.synthesizer.isSpeaking,
                  self.player?.isPlaying != true, self.clock.nowMs >= self.echoUntilMs else { return }
            self.consumedWords = words.count
            self.emit(.localOwnerUtterance(fresh), context: context)
        }
    }

    private func emit(_ payload: ConversationPayload, context: SessionContext) {
        onEvent?(.init(context: context, eventID: UUID().uuidString, payload: payload))
    }

    func speak(_ text: String, priority: Int, context: SessionContext) {
        _ = beginSpeech(text, priority: priority, context: context, kind: .speech)
    }

    @discardableResult
    private func beginSpeech(_ text: String, priority: Int, context: SessionContext,
                             kind: NativeAudioOutputState.Kind) -> UUID? {
        guard self.context == context, !text.isEmpty, offlineStopReady,
              output.permits(priority: priority) else { return nil }
        cancelOutput()
        let token = output.begin(priority: priority, kind: kind)
        onCaption?(context, text)
        guard self.context == context, output.isCurrent(token), offlineStopReady else { return nil }
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        activeUtterance = utterance
        speechToken = token
        echoUntilMs = NativeAudioTiming.deadline(nowMs: clock.nowMs, durationMs: 500) ?? Int64.max
        synthesizer.speak(utterance)
        return token
    }

    func guidance(_ text: String, context: SessionContext, expiresAtMs: Int64) {
        guard self.context == context, offlineStopReady,
              let expiry = NativeAudioTiming.guidanceExpiry(requested: expiresAtMs, nowMs: clock.nowMs),
              output.permits(priority: 2) else { return }
        let token: UUID
        if latestCue == text, output.kind == .guidance, synthesizer.isSpeaking,
           let current = output.token {
            token = current
        } else {
            guard let started = beginSpeech(text, priority: 2, context: context, kind: .guidance) else { return }
            token = started
            latestCue = text
        }
        cueExpiry?.cancel()
        let remaining = expiry - clock.nowMs
        guard remaining > 0 else { cancelOutput(); return }
        cueExpiry = clock.schedule(afterMs: remaining) { [weak self] in
            guard let self, self.context == context, self.output.isCurrent(token) else { return }
            self.cancelOutput()
        }
    }

    func enqueueAudio(_ audio: Data, context: SessionContext) {
        guard self.context == context, offlineStopReady, output.permits(priority: 4),
              let decoded = try? AVAudioPlayer(data: audio),
              let duration = NativeAudioTiming.durationMilliseconds(seconds: decoded.duration) else { return }
        cancelOutput()
        let token = output.begin(priority: 4, kind: .audio)
        player = decoded
        echoUntilMs = NativeAudioTiming.deadline(nowMs: clock.nowMs, durationMs: duration + 500) ?? Int64.max
        guard decoded.play() else { cancelOutput(); return }
        cueExpiry = clock.schedule(afterMs: duration) { [weak self] in
            guard let self, self.context == context, self.output.isCurrent(token) else { return }
            self.cancelOutput()
        }
    }
    func cancel(context: SessionContext) {
        guard self.context == context else { return }
        cancelOutput()
    }
    func localStop(context: SessionContext) {
        guard self.context == context else { return }
        cancelOutput()
        // Always accept spoken Stop. Throttle only our own repeated announcement so
        // recognizing speaker echo cannot create an endless Stop/announcement loop.
        if clock.nowMs < stopEchoUntilMs { onCaption?(context, "Stop"); return }
        stopEchoUntilMs = NativeAudioTiming.deadline(nowMs: clock.nowMs, durationMs: 1_500) ?? Int64.max
        speak("Stop", priority: 0, context: context)
    }
    func requestSuccess(_ result: TaskResult, context: SessionContext) {
        guard result.verification != .unverified,
              result.context.sessionID == context.sessionID,
              result.context.generation == context.generation else { return }
        speak("Task complete", priority: 4, context: context)
    }
    func requestCalibration(patternID: UInt8, side: String?, task: TaskContext) throws {
        throw IntegrationUnavailable(component: "Confirmed wristband output scheduler")
    }
    private func cancelOutput() {
        cueExpiry?.cancel(); cueExpiry = nil
        latestCue = nil
        output.cancel()
        activeUtterance = nil
        speechToken = nil
        player?.stop(); player = nil
        synthesizer.stopSpeaking(at: .immediate)
    }
    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor [weak self] in
            guard let self, self.activeUtterance === utterance,
                  let token = self.speechToken, self.output.finish(token) else { return }
            self.activeUtterance = nil
            self.speechToken = nil
            self.latestCue = nil
            self.cueExpiry?.cancel(); self.cueExpiry = nil
            self.echoUntilMs = NativeAudioTiming.deadline(nowMs: self.clock.nowMs, durationMs: 500) ?? Int64.max
        }
    }

    enum AudioError: LocalizedError {
        case offlineRecognitionUnavailable, microphoneUnavailable
        var errorDescription: String? {
            switch self {
            case .offlineRecognitionUnavailable:
                return "On-device English speech recognition is unavailable. Enable Speech Recognition for Reflex and install on-device speech support, then start again."
            case .microphoneUnavailable: return "The microphone is unavailable. Check the audio route and start again."
            }
        }
    }
}

enum NativeVoiceInput {
    static func command(_ text: String) -> LocalCommand? {
        let words = text.lowercased().components(separatedBy: CharacterSet.alphanumerics.inverted).filter { !$0.isEmpty }
        if words.contains("stop") { return .stop }
        switch words.joined(separator: " ") {
        case "pause", "pause assistance": return .pause
        case "resume", "resume assistance": return .resume
        case "cancel", "cancel task": return .cancelTask
        case "end", "end assistance": return .end
        case "repeat", "repeat that": return .repeatLast
        default: return nil
        }
    }
}

/// Deterministic priority/token rules shared by speech and expiring movement output.
struct NativeAudioOutputState {
    enum Kind { case speech, guidance, audio }
    private(set) var token: UUID?
    private(set) var kind: Kind?
    private var priority: Int?
    func permits(priority: Int) -> Bool {
        guard (0...4).contains(priority) else { return false }
        return self.priority.map { priority <= $0 } ?? true
    }
    mutating func begin(priority: Int, kind: Kind) -> UUID {
        let token = UUID()
        self.token = token; self.priority = priority; self.kind = kind
        return token
    }
    func isCurrent(_ token: UUID) -> Bool { self.token == token }
    @discardableResult mutating func finish(_ token: UUID) -> Bool {
        guard isCurrent(token) else { return false }
        cancel(); return true
    }
    mutating func cancel() { token = nil; priority = nil; kind = nil }
}

enum NativeAudioTiming {
    static func deadline(nowMs: Int64, durationMs: Int64) -> Int64? {
        guard nowMs >= 0, durationMs >= 0 else { return nil }
        let result = nowMs.addingReportingOverflow(durationMs)
        return result.overflow ? Int64.max : result.partialValue
    }
    static func guidanceExpiry(requested: Int64, nowMs: Int64) -> Int64? {
        guard let hold = deadline(nowMs: nowMs, durationMs: 180), requested > nowMs else { return nil }
        return min(requested, hold)
    }
    static func durationMilliseconds(seconds: TimeInterval) -> Int64? {
        // Remote audio is not an unbounded queue; each independently scheduled clip is bounded.
        guard seconds.isFinite, seconds > 0, seconds <= 60 else { return nil }
        return max(1, Int64(ceil(seconds * 1_000)))
    }
}
