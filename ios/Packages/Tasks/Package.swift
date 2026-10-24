// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "Tasks",
    platforms: [.iOS(.v17), .macOS(.v13)],
    products: [.library(name: "Tasks", targets: ["Tasks"])],
    targets: [.target(name: "Tasks"), .testTarget(name: "TasksTests", dependencies: ["Tasks"])]
)
