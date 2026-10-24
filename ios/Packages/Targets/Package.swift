// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "Targets",
    platforms: [.iOS(.v17), .macOS(.v13)],
    products: [.library(name: "Targets", targets: ["Targets"])],
    targets: [
        .target(name: "Targets"),
        .testTarget(name: "TargetsTests", dependencies: ["Targets"], resources: [.copy("Fixtures")])
    ]
)
