// swift-tools-version: 6.0
import PackageDescription

// Test-only helpers the other packages' tests share; no app target links it.
let package = Package(
    name: "TestSupport",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "TestSupport", targets: ["TestSupport"])],
    targets: [
        .target(name: "TestSupport"),
    ]
)
