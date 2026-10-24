// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "HapticLink",
    platforms: [.iOS(.v17), .macOS(.v13)],
    products: [.library(name: "HapticLink", targets: ["HapticLink"])],
    targets: [
        .target(name: "HapticLink"),
        .testTarget(name: "HapticLinkTests", dependencies: ["HapticLink"])
    ]
)
