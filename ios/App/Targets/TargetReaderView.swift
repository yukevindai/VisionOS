import SwiftUI

struct TargetReaderView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var model = TargetReaderModel()

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text("Read a button panel or screen")
                        .font(.title2.bold())
                        .accessibilityAddTraits(.isHeader)
                    Text("Capture one rear-camera image and describe the control you want to find. The image is sent to the vision service. This screen reads images; it does not guide your hands.")
                        .fixedSize(horizontal: false, vertical: true)
                    if let setupError = model.setupError {
                        GroupBox("Service setup required") {
                            Text(setupError).frame(maxWidth: .infinity, alignment: .leading)
                        }
                    } else {
                        account
                        if model.isSignedIn { captureControls }
                    }
                    if let image = model.snapshot {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Captured snapshot — not live video")
                                .font(.headline)
                                .accessibilityIdentifier("targetReader.snapshotLabel")
                            Image(uiImage: image)
                                .resizable()
                                .scaledToFit()
                                .clipShape(RoundedRectangle(cornerRadius: 12))
                                .accessibilityLabel("Captured camera image used for this reading")
                            if let query = model.submittedQuery {
                                Text("Request: \(query)").font(.callout)
                            }
                        }
                    }
                    Text(model.message)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("targetReader.message")
                    if model.busy { ProgressView().accessibilityLabel("Working") }
                }
                .frame(maxWidth: 650, alignment: .leading)
                .padding(20)
            }
            .navigationTitle("Read an image")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Close") {
                        model.close()
                        dismiss()
                    }
                    .accessibilityIdentifier("targetReader.close")
                }
            }
            .task { await model.load() }
            .onChange(of: scenePhase) { _, phase in
                if phase == .background || (phase == .inactive && (model.cameraRunning || model.snapshot != nil)) {
                    model.close()
                }
            }
            .onDisappear { model.close() }
        }
    }

    private var account: some View {
        GroupBox("Account") {
            VStack(alignment: .leading, spacing: 12) {
                if model.isSignedIn {
                    Text("Signed in")
                    Button("Sign out") { Task { await model.signOut() } }
                        .disabled(model.busy)
                } else {
                    Text(accountDescription)
                    Button("Sign in") { Task { await model.signIn() } }
                        .disabled(model.busy)
                        .accessibilityIdentifier("targetReader.signIn")
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var captureControls: some View {
        VStack(alignment: .leading, spacing: 16) {
            TextField("For example, find floor five", text: $model.query, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(1...3)
                .disabled(model.busy)
                .accessibilityLabel("Control to find")
                .accessibilityIdentifier("targetReader.query")
            if model.query.count > 240 {
                Text("Use a request of 240 characters or fewer.").font(.callout)
            }
            if model.cameraRunning {
                Button { model.captureAndRead() } label: {
                    Label("Capture and find", systemImage: "camera.viewfinder")
                        .frame(maxWidth: .infinity, minHeight: 52)
                }
                .buttonStyle(.borderedProminent)
                .disabled(!model.canRead)
                .accessibilityIdentifier("targetReader.capture")
                Button("Stop camera") { model.close() }
                    .frame(minHeight: 44)
            } else {
                Button { Task { await model.startCamera() } } label: {
                    Label("Start camera", systemImage: "camera")
                        .frame(maxWidth: .infinity, minHeight: 52)
                }
                .buttonStyle(.borderedProminent)
                .disabled(!model.canStartCamera)
                .accessibilityIdentifier("targetReader.start")
            }
            if model.busy && model.snapshot != nil {
                Button("Cancel reading") { model.close() }
                    .frame(minHeight: 44)
            }
        }
    }

    private var accountDescription: String {
        switch model.accountStatus {
        case .signedOut: return "Signed out"
        case .loading: return "Restoring sign-in…"
        case .signingIn: return "Opening secure sign-in…"
        case .signingOut: return "Signing out…"
        case .signedIn: return "Signed in"
        case .failed(let message): return message
        }
    }
}
