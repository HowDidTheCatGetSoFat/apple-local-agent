import SwiftUI

// Holds the state shown in the menu bar, populated from `fxlla status` and the
// stats time-series. Refreshes on a timer.
@MainActor
final class StatusModel: ObservableObject {
    @Published var running = false
    @Published var summary = "Loading..."
    @Published var samples: [StatsSample] = []
    @Published var resident: [ResidentModel] = []
    @Published var models: [CatalogModel] = []
    @Published var budgetGB: Double = 0

    private var timer: Timer?

    func residentFor(_ alias: String) -> ResidentModel? { resident.first { $0.alias == alias } }

    var iconName: String { running ? "cpu.fill" : "cpu" }

    init() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    @Published var busy = false

    func refresh() {
        Task.detached {
            let (out, _) = CLI.run(["status"])
            let clean = out.strippingANSI().trimmingCharacters(in: .whitespacesAndNewlines)
            let isRunning = clean.localizedCaseInsensitiveContains("running")
            let samples = Stats.recent()
            let health = Gateway.health()
            let models = Gateway.models()
            await MainActor.run {
                self.running = isRunning
                self.summary = clean.isEmpty ? "fxlla not found or no output" : clean
                self.samples = samples
                self.resident = health?.resident ?? []
                self.models = models
                self.budgetGB = (health?.budgetMB ?? 0) / 1024
            }
        }
    }

    func startGateway() { runThenRefresh(["serve"]) }
    func stopGateway() { runThenRefresh(["unserve"]) }

    func load(_ alias: String) {
        busy = true
        Task.detached {
            Gateway.warmup(alias)
            await MainActor.run { self.busy = false; self.refresh() }
        }
    }

    private func runThenRefresh(_ args: [String]) {
        busy = true
        Task.detached {
            _ = CLI.run(args)
            await MainActor.run { self.busy = false; self.refresh() }
        }
    }

    func ramAuto() { runPrivileged("ram auto") }
    func ramReset() { runPrivileged("ram reset") }

    // Raising the GPU limit needs root. Run `fxlla ram ...` via a native admin
    // prompt (as root, fxlla's internal sudo needs no password).
    private func runPrivileged(_ args: String) {
        busy = true
        let bin = CLI.path
        Task.detached {
            let script = "do shell script \"\(bin) \(args)\" with administrator privileges"
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
            p.arguments = ["-e", script]
            try? p.run()
            p.waitUntilExit()
            await MainActor.run { self.busy = false; self.refresh() }
        }
    }
}
