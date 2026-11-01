// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Storage",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "Storage", targets: ["Storage"])],
    dependencies: [
        .package(path: "../Matching"),
        .package(path: "../YNABClient"),
    ],
    targets: [
        .target(name: "Storage", dependencies: ["Matching", "YNABClient"]),
        .testTarget(name: "StorageTests", dependencies: ["Storage", "Matching"]),
    ]
)
