import Foundation

// Runs the fxlla CLI. The app is a thin front end: all real work stays in fxlla.
enum CLI {
    // GUI apps get a minimal PATH, so resolve the binary from common locations.
    static let path: String = {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let candidates = [
            "\(home)/.local/bin/fxlla",
            "/usr/local/bin/fxlla",
            "/opt/homebrew/bin/fxlla",
        ]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) } ?? "fxlla"
    }()

    // Run `fxlla <args...>` and return (combined output, exit code).
    @discardableResult
    static func run(_ args: [String]) -> (out: String, code: Int32) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: path)
        p.arguments = args
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do {
            try p.run()
        } catch {
            return ("error running fxlla: \(error.localizedDescription)", -1)
        }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (String(data: data, encoding: .utf8) ?? "", p.terminationStatus)
    }
}

extension String {
    // Strip ANSI color escapes so CLI output renders cleanly in the panel.
    func strippingANSI() -> String {
        replacingOccurrences(of: "\u{1B}\\[[0-9;]*m", with: "", options: .regularExpression)
    }
}
