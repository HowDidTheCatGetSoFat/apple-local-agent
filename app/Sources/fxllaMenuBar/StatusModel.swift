import SwiftUI

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

    private var timer: Timer?

    var iconName: String { running ? "cpu.fill" : "cpu" }

    func residentFor(_ alias: String) -> ResidentModel? { resident.first { $0.alias == alias } }

    init() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
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
