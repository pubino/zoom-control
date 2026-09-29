// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "RoomAgent",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "roomagent", targets: ["roomagent"]),
    ],
    dependencies: [
        .package(url: "https://github.com/apple/swift-argument-parser", from: "1.3.0"),
    ],
    targets: [
        // Pure logic; builds and tests on Linux (Docker) and macOS.
        .target(name: "RoomAgentCore"),
        // macOS system adapters (AVFoundation, CoreAudio, AppKit, AX, ScreenCaptureKit).
        .target(name: "RoomAgentMac", dependencies: ["RoomAgentCore"]),
        .executableTarget(
            name: "roomagent",
            dependencies: [
                "RoomAgentCore",
                "RoomAgentMac",
                .product(name: "ArgumentParser", package: "swift-argument-parser"),
            ]
        ),
        .testTarget(name: "RoomAgentCoreTests", dependencies: ["RoomAgentCore"]),
    ]
)
