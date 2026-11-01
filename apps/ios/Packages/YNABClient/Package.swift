// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "YNABClient",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "YNABClient", targets: ["YNABClient"])],
    targets: [
        .target(name: "YNABClient"),
        .testTarget(name: "YNABClientTests", dependencies: ["YNABClient"]),
    ]
)
