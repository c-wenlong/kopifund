// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Storage",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "Storage", targets: ["Storage"])],
    dependencies: [
        .package(path: "../Matching"),
    ],
    targets: [
        .target(name: "Storage", dependencies: ["Matching"]),
        .testTarget(name: "StorageTests", dependencies: ["Storage", "Matching"]),
    ]
)
