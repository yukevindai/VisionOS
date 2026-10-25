import SwiftUI

struct StartupSignInView<Content: View>: View {
    @StateObject private var model: StartupSignInModel
    private let content: Content

    init(simulated: Bool, @ViewBuilder content: () -> Content) {
        _model = StateObject(wrappedValue: StartupSignInModel(simulated: simulated))
        self.content = content()
    }

    var body: some View {
        Group {
            if model.hasContinued {
                content
            } else {
                GeometryReader { geometry in
                    ScrollView {
                        VStack(alignment: .leading, spacing: 24) {
                            Text("Welcome to Reflex")
                                .font(.largeTitle.bold())
                                .accessibilityAddTraits(.isHeader)
                            Text("Sign in to identify objects with your camera and connect with trusted friends.")
                                .font(.title3)
                                .fixedSize(horizontal: false, vertical: true)
                            Text("Your sign-in is saved securely on this iPhone.")
                                .fixedSize(horizontal: false, vertical: true)
                            if let message = model.message {
                                Text(message)
                                    .fixedSize(horizontal: false, vertical: true)
                                    .accessibilityIdentifier("startup.message")
                            }
                            if model.busy {
                                ProgressView("Connecting your account…")
                                    .accessibilityIdentifier("startup.progress")
                            }
                            Button {
                                Task { await model.signIn() }
                            } label: {
                                Text("Sign in")
                                    .font(.title2.bold())
                                    .frame(maxWidth: .infinity, minHeight: 72)
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(AssistanceColors.primaryActionFill)
                            .disabled(model.busy || !model.isConfigured)
                            .accessibilityIdentifier("startup.signIn")
                            Button("Continue without signing in") { model.continueWithoutSignIn() }
                                .font(.headline)
                                .frame(maxWidth: .infinity, minHeight: 52)
                                .accessibilityIdentifier("startup.skip")
                            Text("Without signing in, you can use the camera and local Stop controls. Online object recognition requires an account.")
                                .font(.callout)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .padding(24)
                        .frame(maxWidth: 650, minHeight: geometry.size.height, alignment: .center)
                        .frame(maxWidth: .infinity)
                    }
                    .background(AssistanceColors.canvas.ignoresSafeArea())
                    .accessibilityIdentifier("startup.signInScreen")
                }
            }
        }
        .task { await model.load() }
    }
}
