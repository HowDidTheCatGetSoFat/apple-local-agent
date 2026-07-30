import Foundation

// Runs the fxlla CLI. The app is a thin front end: all real work stays in fxlla.
enum CLI {
    // The copy of the CLI shipped inside the app bundle (see app/build.sh), used
    // when nothing is installed on PATH - an install straight from the .dmg.
    static let bundled: String? = {
        guard let res = Bundle.main.resourceURL else { return nil }
        let path = res.appendingPathComponent("cli/bin/fxlla").path
        return FileManager.default.isExecutableFile(atPath: path) ? path : nil
    }()

    // Where "Install the command" links to. User-writable, so no admin prompt.
    static var linkTarget: String {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".local/bin/fxlla").path
    }

    // GUI apps get a minimal PATH, so resolve the binary from common locations.
    // An installed CLI wins over the bundled one, so a checkout stays in charge
    // during development.
    static let path: String = {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let candidates = [
            "\(home)/.local/bin/fxlla",
            "/usr/local/bin/fxlla",
            "/opt/homebrew/bin/fxlla",
        ]
        if let found = candidates.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) {
            return found
        }
        return bundled ?? "fxlla"
    }()

    // A bundle running from a mounted disk image, or from Gatekeeper's App
    // Translocation, lives at a path that disappears when the image is ejected or
    // the app is moved. Linking into it would leave a dangling `fxlla` on PATH,
    // so the install refuses and asks for a durable location first.
    static var isTransientLocation: Bool {
        let path = Bundle.main.bundleURL.resolvingSymlinksInPath().path
        return path.hasPrefix("/Volumes/") || path.contains("/AppTranslocation/")
    }

    // The view turns these into localized text; CLI stays free of the UI layer.
    enum InstallResult {
        case installed(String)                  // linked at this path
        case alreadyInstalled(String)
        case noBundle                           // dev build, nothing to link
        case transientLocation                  // running from a .dmg or translocated
        case occupied(String)                   // a real file is already there
        case linksElsewhere(String, String)     // target, where it already points
        case error(String)
    }

    // Symlink the bundled CLI into ~/.local/bin, the way "Install 'code' command
    // in PATH" does. Idempotent, and it never replaces something it did not
    // create: a real file, or a symlink pointing at another install (typically a
    // git checkout being developed against), is reported and left alone. Silently
    // repointing that link would break the user's setup behind their back.
    static func installOnPath() -> InstallResult {
        let fm = FileManager.default
        guard let source = bundled else { return .noBundle }
        if isTransientLocation { return .transientLocation }
        let target = linkTarget
        if let existing = try? fm.destinationOfSymbolicLink(atPath: target) {
            return existing == source ? .alreadyInstalled(target)
                                      : .linksElsewhere(target, existing)
        }
        if fm.fileExists(atPath: target) { return .occupied(target) }
        do {
            try fm.createDirectory(atPath: (target as NSString).deletingLastPathComponent,
                                   withIntermediateDirectories: true)
            try fm.createSymbolicLink(atPath: target, withDestinationPath: source)
        } catch {
            return .error(error.localizedDescription)
        }
        return .installed(target)
    }

    // Note: no "is it on PATH?" check here on purpose. A GUI app inherits a
    // minimal PATH that does not reflect the user's shell, so any such check
    // would be wrong more often than right. The UI states where it linked and
    // lets the user add the directory to PATH if their shell cannot find it.

    // Run `fxlla <args...>` off the cooperative thread pool and return
    // (combined output, exit code).
    static func run(_ args: [String]) async -> (out: String, code: Int32) {
        await withCheckedContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async {
                cont.resume(returning: runSync(args))
            }
        }
    }

    private static func runSync(_ args: [String]) -> (out: String, code: Int32) {
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

    // Run a shell command as root via a native admin prompt, off the pool.
    // The caller is responsible for quoting arguments in `command`.
    static func osascriptAdmin(_ command: String) async {
        await withCheckedContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async {
                let script = "do shell script \"\(command)\" with administrator privileges"
                let p = Process()
                p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
                p.arguments = ["-e", script]
                try? p.run()
                p.waitUntilExit()
                cont.resume()
            }
        }
    }
}

extension String {
    // Strip ANSI color escapes so CLI output renders cleanly in the panel.
    func strippingANSI() -> String {
        replacingOccurrences(of: "\u{1B}\\[[0-9;]*m", with: "", options: .regularExpression)
    }

    // Single-quote for a POSIX shell, escaping embedded single quotes.
    func shellQuoted() -> String {
        "'" + replacingOccurrences(of: "'", with: "'\\''") + "'"
    }
}
