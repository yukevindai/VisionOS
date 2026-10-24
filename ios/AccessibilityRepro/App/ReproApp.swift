import SwiftUI

@main
struct ReproApp: App {
    var body: some Scene { WindowGroup { Group { if ProcessInfo.processInfo.arguments.contains("--sampling") { SamplingView() } else { ReproHost() } }.preferredColorScheme(.light) } }
}

struct ReproHost: View {
    @State private var started = false
    var body: some View {
        NavigationStack {
            Group {
                if started { ReproView() }
                else { Button("Start") { started = true }.accessibilityIdentifier("repro.start").foregroundStyle(Color(uiColor: .label)).padding() }
            }.navigationTitle("Reflex").navigationBarTitleDisplayMode(.inline)
        }
    }
}

struct ReproView: View {
    @AccessibilityFocusState private var statusFocused: Bool
    @State private var input = ""
    private let message = "SIMULATED assistance. Use the fixture input below; the microphone is not listening."
    private let arguments = ProcessInfo.processInfo.arguments
    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text("SIMULATED").font(.headline).padding(12)
                    Text("Listening").font(.title2.bold()).accessibilityFocused($statusFocused)
                    Text(message).font(.body).fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("repro.message")
                    Color.clear.frame(height: 150).accessibilityHidden(true)
                    Text("SIMULATED session. No real camera or wristbands. Enter a simulated request to try the flow.").font(.title3).textSelection(.enabled)
                    Text("Simulated voice input").font(.headline)
                    Text("Type a request such as “press the blue button”, “pick up the cup”, “read the menu”, or “open the door”. This replaces microphone input in the fixture.").font(.callout)
                    TextField("Simulated request", text: $input, prompt: Text("Simulated request").foregroundColor(Color(uiColor: .label)), axis: .vertical)
                        .textFieldStyle(.roundedBorder).foregroundStyle(Color(uiColor: .label))
                    Button("Send simulated request") {}.padding().foregroundStyle(Color(uiColor: .label)).accessibilityIdentifier("repro.send")
                }.padding(24)
            }.clipped().accessibilityIdentifier("repro.scroll")
            if !arguments.contains("--no-footer") {
                VStack(spacing: 12) {
                    Button {} label: {
                        Label("Pause assistance", systemImage: "pause.fill")
                            .frame(maxWidth: .infinity, minHeight: 52)
                    }.buttonStyle(.borderedProminent).controlSize(.large)
                        .tint(Color(red: 0, green: 90.0 / 255, blue: 190.0 / 255)).foregroundStyle(.white)
                        .accessibilityIdentifier("repro.pause")
                    Button {} label: {
                        Label("End assistance", systemImage: "phone.down.fill")
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.horizontal, 16).padding(.vertical, 12)
                            .frame(maxWidth: .infinity, minHeight: 52)
                            .background(Color(uiColor: .secondarySystemBackground), in: RoundedRectangle(cornerRadius: 16))
                            .overlay(RoundedRectangle(cornerRadius: 16).stroke(Color(uiColor: .label)))
                    }.buttonStyle(.plain).foregroundStyle(Color(uiColor: .label))
                }.padding().background(Color(uiColor: .systemBackground))
            }
        }.onAppear { statusFocused = true }
    }
}

struct SamplingView: View {
    private let arguments = ProcessInfo.processInfo.arguments
    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Color.clear.frame(height: arguments.contains("--aligned") ? 0 : 120).accessibilityHidden(true)
                    Text("SIMULATED assistance. Use the fixture input below; the microphone is not listening.")
                        .font(.body).fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("sample.message")
                    Color.clear.frame(height: 400).accessibilityHidden(true)
                }.padding(24)
            }.clipped().accessibilityIdentifier("sample.scroll")
            if !arguments.contains("--no-footer") {
                Button("Pause assistance") {}
                    .font(.body).foregroundStyle(.white)
                    .padding(.top, 12)
                    .frame(maxWidth: .infinity, minHeight: 350, alignment: .top)
                    .background(Color(red: 0, green: 90.0 / 255, blue: 190.0 / 255))
            }
        }
    }
}
