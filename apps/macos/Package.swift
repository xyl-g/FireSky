// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "FireSky",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "FireSkyNative", targets: ["FireSkyNative"])],
    targets: [
        .executableTarget(name: "FireSkyNative", swiftSettings: [.swiftLanguageMode(.v5)]),
    ]
)
