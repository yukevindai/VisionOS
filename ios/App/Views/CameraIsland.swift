import SwiftUI

struct CameraIsland: ViewModifier {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.colorSchemeContrast) private var contrast
    var cornerRadius: CGFloat = 20

    func body(content: Content) -> some View {
        content
            .foregroundStyle(Color(uiColor: .label))
            .background {
                let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                if reduceTransparency || uiTestingReduceTransparency {
                    shape.fill(Color(uiColor: .systemBackground))
                } else if contrast == .increased {
                    shape.fill(.regularMaterial)
                } else {
                    shape.fill(.ultraThinMaterial)
                        .overlay {
                            shape.fill(Color(uiColor: .systemBackground)
                                .opacity(colorScheme == .light ? 0.28 : 0.12))
                        }
                }
            }
            .overlay {
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .strokeBorder(.primary.opacity(contrast == .increased ? 0.65 : 0.15), lineWidth: 1)
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
            }
    }

    private var uiTestingReduceTransparency: Bool {
        #if DEBUG
        let arguments = ProcessInfo.processInfo.arguments
        return arguments.contains("--uitesting") && arguments.contains("--ui-reduce-transparency")
        #else
        return false
        #endif
    }
}

extension View {
    func cameraIsland(cornerRadius: CGFloat = 20) -> some View {
        modifier(CameraIsland(cornerRadius: cornerRadius))
    }
}
