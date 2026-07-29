import AppKit
import SwiftUI

// The kind of media the CLI can generate: `fxlla media <type> "<prompt>"`.
enum MediaKind: String, CaseIterable, Identifiable {
    case image, video, voice
    var id: String { rawValue }
    var label: String {
        switch self {
        case .image: return "Image"
        case .video: return "Video"
        case .voice: return "Voice"
        }
    }
}

// Holds the state shown in the menu bar, populated from `fxlla status`, the
// gateway, and the stats time-series. All I/O is async so the UI stays live.
@MainActor
final class StatusModel: ObservableObject {
    @Published var running = false
    @Published var summary = "Loading..."
    @Published var samples: [StatsSample] = []
    @Published var resident: [ResidentModel] = []
    @Published var models: [CatalogModel] = []
    @Published var downloadable: [CatalogEntry] = []
    @Published var pulling: Set<String> = []
    @Published var budgetGB: Double = 0
    @Published var busy = false
    @Published var lastError: String?
    @Published var mediaPrompt = ""
    @Published var mediaKind: MediaKind = .image
    @Published var generating = false

    private var timer: Timer?

    var iconName: String { running ? "cpu.fill" : "cpu" }

    func residentFor(_ alias: String) -> ResidentModel? { resident.first { $0.alias == alias } }

    init() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { _ in
            Task { @MainActor [weak self] in self?.refresh() }
        }
    }

    func refresh() {
        Task {
            let (out, _) = await CLI.run(["status"])
            let clean = out.strippingANSI().trimmingCharacters(in: .whitespacesAndNewlines)
            let health = await Gateway.health()
            let models = await Gateway.models()
            let catalog = await Catalog.all()
            let have = await Catalog.downloaded()

            running = clean.localizedCaseInsensitiveContains("running")
            summary = clean.isEmpty ? "fxlla not found or no output" : clean
            samples = Stats.recent()
            resident = health?.resident ?? []
            self.models = models
            downloadable = catalog.filter { !have.contains($0.alias) }
            budgetGB = (health?.budgetMB ?? 0) / 1024
        }
    }

    func startGateway() { runThenRefresh(["serve"]) }
    func stopGateway() { runThenRefresh(["unserve"]) }

    func load(_ alias: String) {
        busy = true
        Task {
            await Gateway.warmup(alias)
            busy = false
            refresh()
        }
    }

    // Download a model in the background; the panel shows a spinner until done.
    func pull(_ alias: String) {
        pulling.insert(alias)
        Task {
            let (out, code) = await CLI.run(["pull", alias])
            pulling.remove(alias)
            if code != 0 { lastError = out.strippingANSI().trimmingCharacters(in: .whitespacesAndNewlines) }
            refresh()
        }
    }

    // Generate media locally. The CLI prints the output file path on stdout as
    // its last line; on success reveal that file in Finder.
    func generateMedia() {
        let prompt = mediaPrompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !prompt.isEmpty, !generating else { return }
        generating = true
        lastError = nil
        let kind = mediaKind.rawValue
        Task {
            // Guard against a hung CLI leaving the UI stuck: recover after a
            // generous ceiling (a real job finishes well within it).
            let result = await Self.withTimeout(seconds: 900) {
                await CLI.run(["media", kind, prompt])
            }
            generating = false
            guard let (out, code) = result else {
                lastError = "media \(kind) is taking too long; it may still be running"
                return
            }
            let clean = out.strippingANSI().trimmingCharacters(in: .whitespacesAndNewlines)
            if code == 0 {
                if let last = clean.split(separator: "\n").last.map(String.init), !last.isEmpty {
                    let url = URL(fileURLWithPath: last)
                    NSWorkspace.shared.activateFileViewerSelecting([url])
                }
            } else {
                lastError = clean
            }
        }
    }

    // Race an async operation against a timeout; returns nil if the timeout wins.
    private static func withTimeout<T: Sendable>(
        seconds: UInt64, _ op: @escaping @Sendable () async -> T) async -> T? {
        await withTaskGroup(of: T?.self) { group in
            group.addTask { await op() }
            group.addTask {
                try? await Task.sleep(nanoseconds: seconds * 1_000_000_000)
                return nil
            }
            let first = await group.next() ?? nil
            group.cancelAll()
            return first
        }
    }

    func ramAuto() { runPrivileged("ram auto") }
    func ramReset() { runPrivileged("ram reset") }

    // Raising the GPU limit needs root: run `fxlla ram ...` via a native admin
    // prompt. The binary path is shell-quoted so paths with spaces work.
    private func runPrivileged(_ args: String) {
        busy = true
        let command = "\(CLI.path.shellQuoted()) \(args)"
        Task {
            await CLI.osascriptAdmin(command)
            busy = false
            refresh()
        }
    }

    private func runThenRefresh(_ args: [String]) {
        busy = true
        Task {
            let (out, code) = await CLI.run(args)
            busy = false
            lastError = code == 0 ? nil
                : out.strippingANSI().trimmingCharacters(in: .whitespacesAndNewlines)
            refresh()
        }
    }
}
