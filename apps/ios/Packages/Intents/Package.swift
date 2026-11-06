// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Intents",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "Intents", targets: ["Intents"])],
    dependencies: [
        .package(path: "../Matching"),
        // Sync (the YNAB run) posts through it
        .package(path: "../YNABClient"),
        .package(path: "../Storage"),
        // the tests' URLSession stand-in
        .package(path: "../TestSupport"),
    ],
    targets: [
        .target(name: "Intents", dependencies: ["Matching", "YNABClient", "Storage"]),
        .testTarget(name: "IntentsTests", dependencies: ["Intents", "Storage", "YNABClient", "TestSupport"]),
    ]
)
