// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "Matching",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "Matching", targets: ["Matching"])],
    targets: [
        .target(name: "Matching"),
        .testTarget(name: "MatchingTests", dependencies: ["Matching"]),
    ]
)
