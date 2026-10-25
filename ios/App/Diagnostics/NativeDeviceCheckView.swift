#if DEBUG
import AVFoundation
import SwiftUI

/// Explicit device diagnostics: real sensors, no guidance, no audio, no cloud uploads.
@MainActor
final class NativeDeviceCheckModel: ObservableObject {
    @Published private(set) var frames = 0
    @Published private(set) var depthFrames = 0
    @Published private(set) var handFrames = 0
    @Published private(set) var measuredHandFrames = 0
    @Published private(set) var tracking = "Not started"
    @Published private(set) var message = "Camera and LiDAR check. No movement cues or audio."
    @Published private(set) var processingMs = 0.0
    @Published private(set) var preview: UIImage?
    private var context = SessionContext(sessionID: UUID().uuidString, generation: 0)
    private var running = false
    private lazy var perception = NativePerceptionGuidanceClient(
        startAudio: { _ in SharedAudioInput(identifier: "DIAGNOSTIC-NO-AUDIO") },
        stopAudio: { _ in }, supportsOfflineStop: { false }, preferredHand: { .right },
        onCue: { _, _, _ in }, stopOutput: { _ in })

    func start() async {
        guard !running else { return }
        running = true
        let started = context
        if AVCaptureDevice.authorizationStatus(for: .video) == .notDetermined {
            _ = await AVCaptureDevice.requestAccess(for: .video)
        }
        guard running, context == started else { return }
        perception.onFrame = { [weak self] in self?.preview = $0.preview }
        perception.onDiagnostics = { [weak self] data in
            guard let self, self.running else { return }
            self.frames += 1
            if data.depthAvailable { self.depthFrames += 1 }
            if data.handVisible { self.handFrames += 1 }
            if data.measuredHand { self.measuredHandFrames += 1 }
            self.processingMs = data.processingMs
            self.tracking = data.trackingNormal ? "Normal" : "Limited"
            if self.frames == 1 || self.frames % 30 == 0 {
                print("VISIONOS_DEVICE_CHECK frames=\(self.frames) depth=\(self.depthFrames) hand=\(self.handFrames) measuredHand=\(self.measuredHandFrames) processingMs=\(data.processingMs)")
            }
        }
        do {
            let ready = try await perception.start(context: context, policy: CameraPolicy())
            guard running, context == started else { return }
            message = ready.camera.depthSupported
                ? "Real camera and LiDAR started. Hold your right hand in view. No guidance is active."
                : "Camera started. This device does not provide ARKit scene depth. No guidance is active."
        } catch {
            message = error.localizedDescription
            running = false
        }
    }
    func stop() {
        perception.stop(context: context)
        context = SessionContext(sessionID: UUID().uuidString, generation: 0)
        running = false
        preview = nil
        tracking = "Stopped"
        message = "Sensors stopped. Recorded counts remain above."
        print("VISIONOS_DEVICE_CHECK stopped frames=\(frames) depth=\(depthFrames) hand=\(handFrames) measuredHand=\(measuredHandFrames)")
    }
}

struct NativeDeviceCheckView: View {
    @StateObject private var model = NativeDeviceCheckModel()
    @Environment(\.scenePhase) private var phase
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("REAL DEVICE SENSOR CHECK").font(.headline)
                    Text("Camera and LiDAR only. No guidance, audio, or uploads.")
                    Text(model.message).accessibilityIdentifier("deviceCheck.message")
                    Text("Frames: \(model.frames)").accessibilityIdentifier("deviceCheck.frames")
                    Text("Depth frames: \(model.depthFrames)").accessibilityIdentifier("deviceCheck.depth")
                    Text("Right-hand frames: \(model.handFrames)").accessibilityIdentifier("deviceCheck.hand")
                    Text("Measured fingertip frames: \(model.measuredHandFrames)").accessibilityIdentifier("deviceCheck.measuredHand")
                    Text("Tracking: \(model.tracking)")
                    Text("Last processing: \(model.processingMs, specifier: "%.1f") ms")
                    if let preview = model.preview { Image(uiImage: preview).resizable().scaledToFit() }
                    Button("Start sensor check") { Task { await model.start() } }
                        .buttonStyle(.borderedProminent).frame(minHeight: 52)
                        .accessibilityIdentifier("deviceCheck.start")
                    Button("Stop sensors", role: .destructive) { model.stop() }
                        .frame(minHeight: 52).accessibilityIdentifier("deviceCheck.stop")
                }.padding()
            }.navigationTitle("VisionOS device check")
        }
        .onChange(of: phase) { _, phase in if phase == .background { model.stop() } }
        .onDisappear { model.stop() }
    }
}
#endif
