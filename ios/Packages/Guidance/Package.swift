// swift-tools-version: 5.9
import PackageDescription

let package = Package(name: "Guidance", platforms: [.iOS(.v17), .macOS(.v13)],
    products: [.library(name: "Guidance", targets: ["Guidance"])],
    targets: [.target(name: "Guidance"),
              .testTarget(name: "GuidanceTests", dependencies: ["Guidance"], resources: [.copy("Fixtures")])])
