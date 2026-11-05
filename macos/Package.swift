// swift-tools-version:5.9
import PackageDescription

// Workshop for Mac: an AppKit shell around Workshop's web UI. Built on a Mac by
// scripts/macos-build.sh, which wraps the executable in Workshop.app.
let package = Package(
    name: "Workshop",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "Workshop", path: "Sources/Workshop"),
    ]
)
