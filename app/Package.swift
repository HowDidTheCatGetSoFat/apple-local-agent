// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "fxllaMenuBar",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "fxllaMenuBar",
            path: "Sources/fxllaMenuBar"
        )
    ]
)
