// swift-tools-version: 5.9
import PackageDescription
let package = Package(
    name: "NebulaDesktop", platforms: [.macOS(.v14)],
    products: [.executable(name: "NebulaDesktop", targets: ["NebulaDesktop"])],
    targets: [.executableTarget(name: "NebulaDesktop")])
