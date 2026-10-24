import LiveKit
import SwiftUI

struct FriendSessionsView: View {
    @StateObject private var model = FriendSessionsModel()
    @State private var invitationCode = ""
    @State private var showingDetails = false
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        Group {
            if let session = model.selectedSession {
                camera(session: session)
            } else {
                sessionList
            }
        }
        .accessibilityIdentifier("helper.sessions")
        .task { await model.load() }
        .onDisappear { model.close("") }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background { model.close("The session closed when the app entered the background.") }
        }
    }

    private var sessionList: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("HELP SOMEONE").font(.subheadline.weight(.semibold)).foregroundStyle(AssistanceColors.secondaryText)
                Text("Shared sessions").font(.largeTitle.bold()).accessibilityAddTraits(.isHeader)
                Text("Only people who have trusted you appear here.").font(.body)
                if model.setupRequired {
                    VStack(alignment: .leading, spacing: 12) {
                        Label("Shared sessions unavailable", systemImage: "network.slash").font(.headline)
                        Text(model.message).fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(20)
                    .background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 20))
                    .accessibilityIdentifier("helper.setupRequired")
                } else {
                    Text(model.message).fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("helper.message")
                    if model.signedIn {
                        ForEach(model.sessions) { session in
                            VStack(alignment: .leading, spacing: 12) {
                                Text(session.ownerName).font(.title2.bold()).accessibilityAddTraits(.isHeader)
                                Text("Sharing is on · Available to join").font(.body)
                                Button {
                                    Task { await model.join(session) }
                                } label: {
                                    Text("Join \(session.ownerName)").font(.headline)
                                        .frame(maxWidth: .infinity, minHeight: 60)
                                }
                                .buttonStyle(.borderedProminent)
                                .tint(AssistanceColors.primaryActionFill)
                                .disabled(model.busy)
                                .accessibilityHint("Opens your friend’s rear camera. Your camera and microphone stay off.")
                                .accessibilityIdentifier("helper.join.\(session.sessionID)")
                            }
                            .padding(20)
                            .background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 20))
                        }
                        if model.nextCursor != nil {
                            Button("Check more friends") { Task { await model.loadMore() } }
                                .frame(minHeight: 44).disabled(model.busy)
                                .accessibilityHint("Checks the next page, even if this page has no available sessions.")
                        }
                        Button("Refresh sessions", systemImage: "arrow.clockwise") {
                            Task { await model.refresh() }
                        }.frame(minHeight: 44).disabled(model.busy)
                        DisclosureGroup("Accept a friend invitation") {
                            VStack(alignment: .leading, spacing: 12) {
                                Text("Enter the private code your friend shared. They may need to approve your account.")
                                TextField("Invitation code", text: $invitationCode)
                                    .textContentType(.oneTimeCode)
                                    .textInputAutocapitalization(.never)
                                    .autocorrectionDisabled()
                                    .textFieldStyle(.roundedBorder)
                                    .accessibilityIdentifier("helper.inviteCode")
                                Button("Accept invitation") {
                                    Task { await model.redeem(invitationCode) }
                                }.frame(minHeight: 44).disabled(model.busy)
                            }.padding(.top, 12)
                        }
                        Button("Sign out") { Task { await model.signOut() } }
                            .frame(minHeight: 44).disabled(model.busy)
                    } else {
                        Button {
                            Task { await model.signIn() }
                        } label: {
                            Text("Sign in").font(.headline).frame(maxWidth: .infinity, minHeight: 60)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(AssistanceColors.primaryActionFill)
                        .disabled(model.busy || model.accountStatus == .loading)
                    }
                    if model.busy { ProgressView("Please wait") }
                }
            }
            .padding(24)
            .frame(maxWidth: 650, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .background(Color(uiColor: .systemGroupedBackground))
    }

    private func camera(session: NativeHelperSession) -> some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if let track = model.track {
                SwiftUIVideoView(track, layoutMode: .fit, mirrorMode: .off)
                    .opacity(model.videoFresh ? 1 : 0)
                    .accessibilityHidden(true)
            }
            if !model.videoFresh {
                VStack(spacing: 12) {
                    Image(systemName: "video.slash").font(.largeTitle)
                    Text(model.connectionStatus).font(.headline)
                        .multilineTextAlignment(.center)
                }
                .foregroundStyle(.white)
                .padding(24)
                .accessibilityIdentifier("helper.videoUnavailable")
            }
        }
        .overlay(alignment: .top) {
            VStack(alignment: .leading, spacing: 8) {
                Text("\(session.ownerName)’s camera").font(.title2.bold()).accessibilityAddTraits(.isHeader)
                Text(model.connectionStatus).font(.body)
                    .accessibilityIdentifier("helper.connectionStatus")
            }
            .frame(maxWidth: 650, alignment: .leading)
            .padding(16)
            .cameraIsland(cornerRadius: 22)
            .padding(.horizontal, 16).padding(.top, 8)
        }
        .overlay(alignment: .bottom) {
            VStack(spacing: 12) {
                if showingDetails {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 10) {
                            Text("Session details").font(.headline)
                            Text("Watch-only video. Call audio and remote guidance controls are not connected yet.")
                            Text("Hand tracking: Unavailable")
                            Text("Object name: Unknown")
                            Text("Camera → target: Unknown")
                            Text("Fingertip → target: Unknown")
                            Text("The owner has not shared a tracking or task-status feed. No distance is estimated.")
                                .foregroundStyle(.primary)
                        }
                        .font(.body)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .frame(maxHeight: dynamicTypeSize.isAccessibilitySize ? 220 : 200)
                    .accessibilityIdentifier("helper.telemetryUnavailable")
                }
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 12) { detailButton; leaveButton }
                    VStack(spacing: 12) { detailButton; leaveButton }
                }
            }
            .frame(maxWidth: 650)
            .padding(16)
            .cameraIsland(cornerRadius: 22)
            .padding(.horizontal, 16).padding(.bottom, 8)
        }
    }

    private var detailButton: some View {
        Button(showingDetails ? "Hide details" : "Session details", systemImage: "info.circle") {
            showingDetails.toggle()
        }
        .font(.headline)
        .frame(maxWidth: .infinity, minHeight: 52)
        .buttonStyle(.bordered)
        .accessibilityIdentifier("helper.details")
    }

    private var leaveButton: some View {
        Button(role: .destructive) { model.close() } label: {
            Label("Leave call", systemImage: "phone.down.fill")
                .font(.headline).frame(maxWidth: .infinity, minHeight: 52)
        }
        .buttonStyle(.borderedProminent)
        .tint(Color(red: 0.70, green: 0.14, blue: 0.20))
        .accessibilityIdentifier("helper.leave")
    }
}
