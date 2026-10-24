import Combine
import Foundation
import Targets
import UIKit

@MainActor
protocol TargetReaderCamera: NativeMediaCaptureClient {
    var onStopped: (() -> Void)? { get set }
    func start(localSessionID: String) async throws
    func stop()
}

extension NativeCameraFrameSource: TargetReaderCamera {}

@MainActor
protocol TargetReaderIdentity: AnyObject {
    var statusUpdates: AnyPublisher<NativeIdentityStatus, Never> { get }
    func loadStoredAccount() async
    func signIn() async throws
    func signOut() async throws
}

extension Auth0NativeIdentity: TargetReaderIdentity {
    var statusUpdates: AnyPublisher<NativeIdentityStatus, Never> { $status.eraseToAnyPublisher() }
}

@MainActor
final class TargetReaderModel: ObservableObject {
    @Published var query = ""
    @Published private(set) var accountStatus: NativeIdentityStatus = .signedOut
    @Published private(set) var setupError: String?
    @Published private(set) var message = "Sign in to read a camera snapshot."
    @Published private(set) var busy = false
    @Published private(set) var cameraRunning = false
    @Published private(set) var hasFrame = false
    @Published private(set) var snapshot: UIImage?
    @Published private(set) var selection: TargetSelection?
    @Published private(set) var submittedQuery: String?

    private var camera: (any TargetReaderCamera)?
    private var identity: (any TargetReaderIdentity)?
    private var reader: (any R3ScreenReading)?
    private var ensureAccount: (() async throws -> Void)?
    private var statusSubscription: AnyCancellable?
    private var latestFrame: NativeMediaVideoFrame?
    private var captureSessionID: String?
    private var generation: UInt64 = 0
    private var requestTask: Task<Void, Never>?
    private var loaded = false
    private var cameraStartInProgress = false
    private var requestInProgress = false
    private let monotonicMs: () -> Int64
    private let utcMs: () -> Int64

    init() {
        monotonicMs = { Int64(ProcessInfo.processInfo.systemUptime * 1_000) }
        utcMs = { Int64(Date().timeIntervalSince1970 * 1_000) }
        do {
            let configuration = try LiveServiceConfiguration.load()
            let identity = Auth0NativeIdentity(configuration: configuration)
            let backend = try ConvexNativeMediaBackend(origin: configuration.convexURL, identity: identity)
            configure(camera: NativeCameraFrameSource(), identity: identity, reader: backend,
                      ensureAccount: { try await backend.ensureAccount() })
        } catch {
            setupError = "Configure Auth0 and Convex in the app, then configure the vision service in Convex."
            message = "Image reading requires service setup."
        }
    }

    init(camera: any TargetReaderCamera, identity: any TargetReaderIdentity, reader: any R3ScreenReading,
         ensureAccount: @escaping () async throws -> Void,
         monotonicMs: @escaping () -> Int64, utcMs: @escaping () -> Int64) {
        self.monotonicMs = monotonicMs
        self.utcMs = utcMs
        configure(camera: camera, identity: identity, reader: reader, ensureAccount: ensureAccount)
    }

    private func configure(camera: any TargetReaderCamera, identity: any TargetReaderIdentity,
                           reader: any R3ScreenReading, ensureAccount: @escaping () async throws -> Void) {
        self.camera = camera
        self.identity = identity
        self.reader = reader
        self.ensureAccount = ensureAccount
        camera.onStopped = { [weak self] in
            self?.close()
            self?.message = "Camera stopped. Start the camera again when ready."
        }
        statusSubscription = identity.statusUpdates.sink { [weak self] status in
            guard let self else { return }
            let previous = self.accountStatus
            self.accountStatus = status
            if previous != status, self.cameraRunning || self.requestTask != nil {
                self.close()
            }
        }
    }

    var isSignedIn: Bool {
        if case .signedIn = accountStatus { return true }
        return false
    }

    var canStartCamera: Bool { isSignedIn && !busy && !cameraRunning && !cameraStartInProgress && !requestInProgress }
    var canRead: Bool {
        isSignedIn && cameraRunning && hasFrame && !busy &&
        !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && query.count <= 240
    }

    func load() async {
        guard !loaded, let identity else { return }
        loaded = true
        await identity.loadStoredAccount()
        if isSignedIn { message = "Point the rear camera at a button panel or screen." }
    }

    func signIn() async {
        guard !busy, let identity else { return }
        close()
        busy = true
        let at = generation
        do {
            try await identity.signIn()
            try await ensureAccount?()
            guard at == generation else { return }
            message = "Point the rear camera at a button panel or screen."
        } catch {
            guard at == generation else { return }
            message = "Sign-in did not complete. Try again."
        }
        if at == generation { busy = false }
    }

    func signOut() async {
        close()
        guard let identity else { return }
        busy = true
        do {
            try await identity.signOut()
            message = "Signed out."
        } catch {
            message = "Camera and image reading are stopped. Sign out again to clear the account."
        }
        busy = false
    }

    func startCamera() async {
        guard canStartCamera, let camera else { return }
        close()
        busy = true
        cameraStartInProgress = true
        defer { cameraStartInProgress = false; busy = requestInProgress }
        let at = generation
        let sessionID = UUID().uuidString
        captureSessionID = sessionID
        do {
            try await ensureAccount?()
            guard at == generation else { return }
            try await camera.start(localSessionID: sessionID)
            guard at == generation, isSignedIn else {
                camera.stop()
                return
            }
            cameraRunning = true
            camera.attach(localSessionID: sessionID) { [weak self] frame in
                guard let self, self.generation == at, self.cameraRunning,
                      frame.localSessionID == self.captureSessionID else { return }
                self.latestFrame = frame
                self.hasFrame = true
            }
            message = "Camera is ready. Capture one image when the panel is in view."
        } catch {
            guard at == generation else { return }
            stopCapture()
            message = "Camera could not start. Check camera permission and use a supported iPhone."
        }
        if at == generation { busy = false }
    }

    func captureAndRead() {
        guard canRead, let frame = latestFrame, let reader else { return }
        let now = monotonicMs()
        guard frame.capturedAtMs >= 0, frame.capturedAtMs <= now, now - frame.capturedAtMs <= 300 else {
            hasFrame = false
            message = "The camera image is stale. Wait for a new frame and try again."
            return
        }
        let requested = query.trimmingCharacters(in: .whitespacesAndNewlines)
        let capturedAtUTCms = utcMs() - (now - frame.capturedAtMs)
        let frameID = UUID().uuidString
        let requestID = UUID().uuidString
        let at = generation
        stopCapture()
        selection = nil
        submittedQuery = requested
        do {
            let encoded = try TargetReaderImageEncoder.encode(frame)
            snapshot = encoded.image
            busy = true
            message = "Reading this snapshot…"
            requestInProgress = true
            requestTask = Task { [weak self] in
                defer {
                    self?.requestInProgress = false
                    self?.busy = self?.cameraStartInProgress ?? false
                    self?.requestTask = nil
                }
                do {
                    let model = try await reader.readScreen(image: encoded.jpeg, frameID: frameID,
                                                           capturedAtUTCms: capturedAtUTCms, requestID: requestID)
                    try Task.checkCancellation()
                    guard let self, self.generation == at, self.isSignedIn else { return }
                    try R3TargetMapping.validateRecognition(model, frameID: frameID,
                                                           capturedAtUTCms: capturedAtUTCms)
                    let result = TargetSelector().select(requested, in: model)
                    self.selection = result
                    self.message = Self.description(result, paymentScreen: model.isPaymentScreen)
                    self.busy = false
                    self.requestTask = nil
                } catch {
                    guard let self, self.generation == at else { return }
                    self.message = "This image could not be read. Check the vision service or capture another image."
                    self.busy = false
                    self.requestTask = nil
                }
            }
        } catch {
            message = "This image could not be prepared. Start the camera and try again."
        }
    }

    func close() {
        generation &+= 1
        requestTask?.cancel()
        requestTask = nil
        stopCapture()
        snapshot = nil
        selection = nil
        submittedQuery = nil
        busy = cameraStartInProgress || requestInProgress
    }

    private func stopCapture() {
        camera?.detach()
        camera?.stop()
        cameraRunning = false
        latestFrame = nil
        hasFrame = false
        captureSessionID = nil
    }

    private static func description(_ selection: TargetSelection, paymentScreen: Bool) -> String {
        if paymentScreen { return "A payment screen was detected. Stop here and ask a person for help." }
        switch selection.status {
        case .selected:
            return "Found in this snapshot: \(selection.target?.label ?? "control"). This is image recognition only; no hand or movement guidance is active."
        case .ambiguous:
            return "More than one control may match, or its label is unclear. Capture a clearer image or use a more specific request."
        case .notFound:
            return "No clear match was found in this snapshot. Try a clearer image or a different request."
        case .providerUnavailable:
            return "The image service is unavailable. Try again later."
        }
    }
}
