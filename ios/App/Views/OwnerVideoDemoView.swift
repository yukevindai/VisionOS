import SwiftUI

/// A separate, foreground-only owner video flow. The normal assistance screen is
/// unavailable while this full-screen cover owns the camera.
struct OwnerVideoDemoView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var model = OwnerVideoDemoModel()
    @State private var requestToApprove: NativePendingFriendRequest?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    Text("Share your camera with a trusted friend")
                        .font(.title2.bold())
                        .accessibilityAddTraits(.isHeader)
                    Text("Your friend can watch your rear camera while this screen is open. Audio and guidance aren't available in this demo.")
                        .fixedSize(horizontal: false, vertical: true)

                    if let setupError = model.setupError {
                        GroupBox("Service setup required") {
                            Text(setupError).frame(maxWidth: .infinity, alignment: .leading)
                        }
                    } else {
                        accountSection
                        if model.isSignedIn {
                            invitationSection
                            friendRequestsSection
                            videoSection
                        }
                    }

                    Text(model.message)
                        .font(.callout)
                        .accessibilityIdentifier("video.message")
                }
                .frame(maxWidth: 650, alignment: .leading)
                .padding(20)
            }
            .navigationTitle("Friend video")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Close") {
                        model.closeVideo()
                        dismiss()
                    }
                    .accessibilityIdentifier("video.close")
                }
            }
            .alert("Approve friend request?", isPresented: Binding(
                get: { requestToApprove != nil },
                set: { if !$0 { requestToApprove = nil } }
            )) {
                Button("Cancel", role: .cancel) { requestToApprove = nil }
                Button("Approve") {
                    guard let request = requestToApprove else { return }
                    requestToApprove = nil
                    Task { await model.approve(request) }
                }
            } message: {
                if let request = requestToApprove {
                    Text("Allow \(request.displayName) (account \(request.requesterID)) to join your video only while you choose to share?")
                }
            }
            .task { await model.load() }
            .onChange(of: scenePhase) { _, phase in
                if (phase == .background && (model.sessionRequested || model.startInProgress)) ||
                    (phase == .inactive && model.sessionRequested) {
                    model.closeVideo()
                }
            }
            .onDisappear { model.closeVideo() }
        }
    }

    private var accountSection: some View {
        GroupBox("Account") {
            VStack(alignment: .leading, spacing: 12) {
                switch model.accountStatus {
                case .signedOut: Text("Signed out")
                case .loading: Text("Restoring sign-in…")
                case .signingIn: Text("Opening secure sign-in…")
                case .signingOut: Text("Signing out…")
                case .signedIn(let accountID): Text("Signed in as \(accountID)")
                case .failed(let message): Text(message)
                }
                if model.isSignedIn {
                    Button("Sign out") { Task { await model.signOut() } }
                        .accessibilityIdentifier("video.signOut")
                } else {
                    Button("Sign in with Auth0") { Task { await model.signIn() } }
                        .disabled(!model.canSignIn)
                        .accessibilityIdentifier("video.signIn")
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var invitationSection: some View {
        GroupBox("Invite a friend") {
            VStack(alignment: .leading, spacing: 12) {
                Text("Send the code to a friend you know. After they redeem it, review and approve their request here.")
                Button("Create invitation code") { Task { await model.createInvite() } }
                    .disabled(model.busy)
                    .accessibilityIdentifier("video.createInvite")
                if let invite = model.invite {
                    Text(invite.code)
                        .font(.system(.body, design: .monospaced))
                        .textSelection(.enabled)
                        .accessibilityLabel("Invitation code: \(invite.code)")
                    Text("Expires \(Date(timeIntervalSince1970: TimeInterval(invite.expiresAtMs) / 1_000), style: .relative)")
                    ShareLink(item: invite.code) { Label("Share invitation code", systemImage: "square.and.arrow.up") }
                        .accessibilityIdentifier("video.shareInvite")
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var friendRequestsSection: some View {
        GroupBox("Friend requests") {
            VStack(alignment: .leading, spacing: 12) {
                Button("Refresh requests") { Task { await model.refreshRequests() } }
                    .disabled(model.busy)
                    .accessibilityIdentifier("video.refreshRequests")
                if model.requests.isEmpty {
                    Text("No pending requests on this page.")
                }
                ForEach(model.requests) { request in
                    VStack(alignment: .leading, spacing: 6) {
                        Text(request.displayName).font(.headline)
                        Text("Account: \(request.requesterID)")
                            .font(.caption)
                            .textSelection(.enabled)
                        Button("Review \(request.displayName)") { requestToApprove = request }
                            .disabled(model.busy)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 4)
                }
                if model.nextRequestCursor != nil {
                    Button("Load more requests") { Task { await model.loadMoreRequests() } }
                        .disabled(model.busy)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var videoSection: some View {
        GroupBox("Live video") {
            VStack(alignment: .leading, spacing: 14) {
                Text(mediaDescription)
                    .accessibilityIdentifier("video.status")
                if let cameraError = model.camera.errorMessage {
                    Text(cameraError).foregroundStyle(.red)
                }
                Toggle("I agree to share my camera with an accepted friend for this session", isOn: $model.sharingConsent)
                    .disabled(model.sessionRequested)
                    .accessibilityIdentifier("video.consent")
                if model.sessionRequested || model.camera.running {
                    Button(role: .destructive) { model.closeVideo() } label: {
                        Label("End video now", systemImage: "stop.fill")
                            .frame(maxWidth: .infinity, minHeight: 52)
                    }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("video.end")
                } else {
                    Button { Task { await model.startVideo() } } label: {
                        Label("Start sharing video", systemImage: "video.fill")
                            .frame(maxWidth: .infinity, minHeight: 52)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!model.canStartVideo)
                    .accessibilityIdentifier("video.start")
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var mediaDescription: String {
        switch model.mediaStatus {
        case .idle: return "Video has not started."
        case .connecting: return "Connecting your video…"
        case .privateVideoAdmitted: return "Video is connected, but friend sharing is off."
        case .sharedVideoAdmitted: return "Video sharing is active. An accepted friend can view your camera."
        case .closed: return "Video is closed."
        }
    }
}
