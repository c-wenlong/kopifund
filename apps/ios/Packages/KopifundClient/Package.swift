// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "KopifundClient",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "KopifundClient", targets: ["KopifundClient"])],
    dependencies: [.package(path: "../TestSupport")],
    targets: [
        .target(name: "KopifundClient"),
        .testTarget(name: "KopifundClientTests", dependencies: ["KopifundClient", "TestSupport"]),
    ]
)
