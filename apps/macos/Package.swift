// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "FireSky",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "FireSkyNative", targets: ["FireSkyNative"]),
        .executable(name: "FireSkySyncHelper", targets: ["FireSkySyncHelper"]),
    ],
    targets: [
        .executableTarget(name: "FireSkyNative", swiftSettings: [.swiftLanguageMode(.v5)]),
        .executableTarget(name: "FireSkySyncHelper", swiftSettings: [.swiftLanguageMode(.v5)]),
    ]
)
