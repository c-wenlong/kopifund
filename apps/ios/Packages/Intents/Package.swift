// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Intents",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "Intents", targets: ["Intents"])],
    dependencies: [
        .package(path: "../Matching"),
        .package(path: "../YNABClient"),
        .package(path: "../Storage"),
    ],
    targets: [
        .target(name: "Intents", dependencies: ["Matching", "YNABClient", "Storage"]),
        .testTarget(name: "IntentsTests", dependencies: ["Intents", "Storage"]),
    ]
)
