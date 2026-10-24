// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "Targets",
    platforms: [.iOS(.v17), .macOS(.v13)],
    products: [.library(name: "Targets", targets: ["Targets"])],
    dependencies: [.package(path: "../Tasks")],
    targets: [
        .target(name: "Targets", dependencies: ["Tasks"]),
        .testTarget(name: "TargetsTests", dependencies: ["Targets", "Tasks"], resources: [.copy("Fixtures")])
    ]
)
