import CoreVideo
import Foundation
import LiveKit
import ObjectiveC

/// Publishes only R1-owned camera buffers. R1's processed microphone transport and
/// remote-audio playback gate are separate integration work; this adapter never
/// creates a microphone, camera capturer, or audio player.
@MainActor
final class LiveKitNativeMediaDriver: NativeMediaDriver {
    var onInvalidated: ((UUID) -> Void)?
    var onMembershipChanged: ((UUID, NativeMediaMembershipChange) -> Void)?

    private final class Connection {
        let attempt: UUID
        let gate: NativeMediaSampleGate
        let room: Room
        let delegate: ConnectionDelegate
        var track: LocalVideoTrack?
        var capturer: BufferCapturer?
        var processor: FrameAdmissionProcessor?
        var publishStarted = false
        var publishTask: Task<Void, Never>?
        var disconnectTask: Task<Void, Never>?

        init(attempt: UUID, gate: NativeMediaSampleGate, room: Room, delegate: ConnectionDelegate) {
            self.attempt = attempt
            self.gate = gate
            self.room = room
            self.delegate = delegate
        }
    }

    private var connection: Connection?

    func connect(token: NativeMediaToken, attempt: UUID, gate: NativeMediaSampleGate) async throws {
        guard connection == nil else { throw DriverError.busy }
        guard !token.token.isEmpty,
              !token.room.roomName.isEmpty,
              !token.room.roomSID.isEmpty,
              !token.identity.isEmpty
        else { throw DriverError.invalidToken }

        let delegate = ConnectionDelegate(
            attempt: attempt,
            gate: gate,
            notifyInvalidation: { [weak self] source in
                Task { @MainActor [weak self] in
                    self?.handleInvalidation(source)
                }
            },
            notifyMembership: { [weak self] source, change in
                Task { @MainActor [weak self] in
                    guard let self, self.connection?.delegate === source else { return }
                    self.onMembershipChanged?(source.attempt, change)
                }
            }
        )
        let room = Room(delegate: delegate,
                        connectOptions: ConnectOptions(autoSubscribe: false,
                                                       reconnectAttempts: 0,
                                                       enableMicrophone: false),
                        roomOptions: RoomOptions(suspendLocalVideoTracksInBackground: false))
        let current = Connection(attempt: attempt, gate: gate, room: room, delegate: delegate)
        connection = current

        do {
            try await room.connect(url: token.serverURL.absoluteString, token: token.token)
            let roomSID = (try await room.sid()).stringValue
            guard connection === current, !delegate.isInvalidated else { throw DriverError.invalidated }
            guard room.connectionState == .connected,
                  room.name == token.room.roomName,
                  roomSID == token.room.roomSID,
                  room.localParticipant.identity?.stringValue == token.identity
            else { throw DriverError.roomMismatch }

            // Participants already present in JoinResponse need not emit a connect
            // callback. Report them before returning, so the coordinator cannot
            // treat an unseen occupant as an empty room.
            for participant in room.remoteParticipants.values {
                guard let identity = participant.identity?.stringValue, !identity.isEmpty,
                      let participantSID = participant.sid?.stringValue, !participantSID.isEmpty
                else { throw DriverError.invalidParticipant }
                guard let gateRevision = gate.suspend() else { throw DriverError.invalidated }
                onMembershipChanged?(attempt,
                                     NativeMediaMembershipChange(identity: identity,
                                                                 participantSID: participantSID,
                                                                 joined: true, gateRevision: gateRevision))
            }
            guard connection === current, !delegate.isInvalidated else { throw DriverError.invalidated }

            // BufferCapturer cannot publish until it has dimensions from a first frame.
            // Creation is safe while the parent's sample gate remains closed; the first
            // admitted R1 frame starts publication below.
            let track = await LocalVideoTrack.createBufferTrack(name: Track.cameraName,
                                                                source: .camera,
                                                                options: BufferCaptureOptions(dimensions: .h720_169,
                                                                                              fps: 15))
            guard connection === current, !delegate.isInvalidated else { throw DriverError.invalidated }
            guard let capturer = track.capturer as? BufferCapturer else { throw DriverError.invalidCapturer }
            let processor = FrameAdmissionProcessor(gate: gate, attempt: attempt)
            // The SDK stores `processor` weakly. Tie its lifetime to the capturer so
            // a queued SDK frame can never bypass validation after disconnect.
            objc_setAssociatedObject(capturer,
                                     UnsafeRawPointer(Unmanaged.passUnretained(capturer).toOpaque()),
                                     processor,
                                     .OBJC_ASSOCIATION_RETAIN_NONATOMIC)
            track.processor = processor
            current.track = track
            current.capturer = capturer
            current.processor = processor
        } catch {
            await disconnect(attempt: attempt)
            throw error
        }
    }

    func disconnect(attempt: UUID) async {
        guard let current = connection, current.attempt == attempt else { return }
        current.gate.close()
        current.delegate.suppress()
        if current.disconnectTask == nil {
            current.publishTask?.cancel()
            let publishing = current.publishTask
            current.disconnectTask = Task {
                await current.room.disconnect()
                await publishing?.value
            }
        }
        await current.disconnectTask?.value
        if connection === current { connection = nil }
    }

    func sendVideo(_ pixelBuffer: CVPixelBuffer, capturedAtMs: Int64,
                   rotation: NativeMediaVideoRotation, attempt: UUID) {
        guard let current = connection,
              current.attempt == attempt,
              !current.delegate.isInvalidated,
              current.room.connectionState == .connected,
              let capturer = current.capturer,
              let processor = current.processor,
              let track = current.track,
              capturedAtMs >= 0
        else { return }
        let (timestampNs, overflow) = capturedAtMs.multipliedReportingOverflow(by: 1_000_000)
        let videoRotation: VideoRotation
        switch rotation {
        case .degrees0: videoRotation = ._0
        case .degrees90: videoRotation = ._90
        case .degrees180: videoRotation = ._180
        case .degrees270: videoRotation = ._270
        }
        guard !overflow,
              VideoCapturer.supportedPixelFormats.contains(where: {
                  $0.uint32Value == CVPixelBufferGetPixelFormatType(pixelBuffer)
              })
        else { return }

        let authorizationRevision = current.gate.revision
        var submitted = false
        let admitted = current.gate.withAdmission(attempt: attempt,
                                                  capturedAtMs: capturedAtMs,
                                                  expectedRevision: authorizationRevision) {
            guard processor.reserve(timeStampNs: timestampNs, capturedAtMs: capturedAtMs,
                                    authorizationRevision: authorizationRevision) else { return }
            submitted = true
            capturer.capture(pixelBuffer, timeStampNs: timestampNs, rotation: videoRotation)
        }
        guard admitted, submitted, !current.publishStarted,
              current.gate.revision == authorizationRevision,
              current.gate.isOpen(attempt: attempt)
        else { return }
        current.publishStarted = true
        current.publishTask = Task { [weak self] in
            await self?.publish(track: track, for: current,
                                authorizationRevision: authorizationRevision)
        }
    }

    private func publish(track: LocalVideoTrack, for current: Connection,
                         authorizationRevision: UInt64) async {
        guard connection === current, !current.delegate.isInvalidated,
              current.room.connectionState == .connected,
              current.gate.isOpen(attempt: current.attempt),
              current.gate.revision == authorizationRevision
        else {
            current.publishStarted = false
            return
        }
        do {
            let publication = try await current.room.localParticipant.publish(videoTrack: track)
            guard connection === current, !current.delegate.isInvalidated,
                  current.gate.isOpen(attempt: current.attempt),
                  current.gate.revision == authorizationRevision
            else {
                try? await current.room.localParticipant.unpublish(publication: publication)
                current.publishStarted = false
                return
            }
        } catch {
            current.delegate.invalidate()
        }
    }

    private func handleInvalidation(_ source: ConnectionDelegate) {
        guard let current = connection, current.delegate === source,
              source.isInvalidated else { return }
        onInvalidated?(source.attempt)
        Task { await disconnect(attempt: current.attempt) }
    }

    private enum DriverError: Error {
        case busy, invalidToken, invalidated, roomMismatch, invalidCapturer, invalidParticipant
    }
}

/// LiveKit invokes RoomDelegate off-main. Close the exact attempt's shared gate
/// synchronously before scheduling any MainActor work. An old room's callbacks
/// can never close a later connection's gate.
private final class ConnectionDelegate: NSObject, RoomDelegate, @unchecked Sendable {
    private let lock = NSLock()
    let attempt: UUID
    private let gate: NativeMediaSampleGate
    private let notifyInvalidation: @Sendable (ConnectionDelegate) -> Void
    private let notifyMembership: @Sendable (ConnectionDelegate, NativeMediaMembershipChange) -> Void
    private var invalidated = false
    private var suppressed = false

    init(attempt: UUID, gate: NativeMediaSampleGate,
         notifyInvalidation: @escaping @Sendable (ConnectionDelegate) -> Void,
         notifyMembership: @escaping @Sendable (ConnectionDelegate, NativeMediaMembershipChange) -> Void) {
        self.attempt = attempt
        self.gate = gate
        self.notifyInvalidation = notifyInvalidation
        self.notifyMembership = notifyMembership
    }

    var isInvalidated: Bool {
        lock.lock()
        defer { lock.unlock() }
        return invalidated
    }

    func suppress() {
        lock.lock()
        suppressed = true
        gate.close()
        lock.unlock()
    }

    func invalidate() {
        lock.lock()
        gate.close()
        let shouldNotify = !invalidated && !suppressed
        invalidated = true
        lock.unlock()
        if shouldNotify { notifyInvalidation(self) }
    }

    private func reportMembershipChange(_ participant: RemoteParticipant, joined: Bool) {
        guard let identity = participant.identity?.stringValue, !identity.isEmpty,
              let participantSID = participant.sid?.stringValue, !participantSID.isEmpty
        else {
            invalidate()
            return
        }
        lock.lock()
        let gateRevision = gate.suspend()
        let shouldNotify = !invalidated && !suppressed
        lock.unlock()
        if shouldNotify, let gateRevision {
            notifyMembership(self, NativeMediaMembershipChange(identity: identity,
                                                               participantSID: participantSID,
                                                               joined: joined, gateRevision: gateRevision))
        }
    }

    func room(_ room: Room, didStartReconnectWithMode reconnectMode: ReconnectMode) {
        invalidate()
    }

    func roomIsReconnecting(_ room: Room) {
        invalidate()
    }

    func room(_ room: Room, didUpdateConnectionState state: ConnectionState,
              from oldState: ConnectionState) {
        if state == .reconnecting || (state == .disconnected && oldState != .connecting) {
            invalidate()
        }
    }

    func room(_ room: Room, didDisconnectWithError error: LiveKitError?) {
        invalidate()
    }

    func room(_ room: Room, didMoveToRoomNamed roomName: String) {
        invalidate()
    }

    func room(_ room: Room, participantDidConnect participant: RemoteParticipant) {
        reportMembershipChange(participant, joined: true)
    }

    func room(_ room: Room, participantDidDisconnect participant: RemoteParticipant) {
        reportMembershipChange(participant, joined: false)
    }
}

/// One SDK processing-queue frame may be outstanding. An SDK preprocessor drop
/// leaves the permit occupied, which deliberately stops ingress until a new
/// connection; no timeout can safely prove the old queued buffer was discarded.
final class FrameAdmissionProcessor: NSObject, VideoProcessor, @unchecked Sendable {
    private struct Pending {
        let timeStampNs: Int64
        let authorizationRevision: UInt64
    }

    private let lock = NSLock()
    private let gate: NativeMediaSampleGate
    private let attempt: UUID
    private var pending: Pending?
    private var lastCapturedAtMs: Int64?

    init(gate: NativeMediaSampleGate, attempt: UUID) {
        self.gate = gate
        self.attempt = attempt
    }

    func reserve(timeStampNs: Int64, capturedAtMs: Int64,
                 authorizationRevision: UInt64) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard pending == nil,
              lastCapturedAtMs.map({ capturedAtMs > $0 && capturedAtMs - $0 >= 67 }) ?? true
        else { return false }
        pending = Pending(timeStampNs: timeStampNs,
                          authorizationRevision: authorizationRevision)
        lastCapturedAtMs = capturedAtMs
        return true
    }

    func process(frame: VideoFrame) -> VideoFrame? {
        lock.lock()
        guard let admitted = pending, admitted.timeStampNs == frame.timeStampNs else {
            lock.unlock()
            return nil
        }
        pending = nil
        lock.unlock()

        guard frame.timeStampNs >= 0,
              frame.timeStampNs % 1_000_000 == 0
        else { return nil }
        let capturedAtMs = frame.timeStampNs / 1_000_000
        return gate.withAdmission(attempt: attempt,
                                  capturedAtMs: capturedAtMs,
                                  expectedRevision: admitted.authorizationRevision,
                                  body: {}) ? frame : nil
    }
}
