// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "Tasks",
    platforms: [.iOS(.v17), .macOS(.v13)],
    products: [.library(name: "Tasks", targets: ["Tasks"])],
    dependencies: [.package(path: "../Targets")],
    targets: [.target(name: "Tasks", dependencies: ["Targets"]), .testTarget(name: "TasksTests", dependencies: ["Tasks"])]
)
