import SwiftUI

// Holds the state shown in the menu bar, populated from `fxlla status` and the
// stats time-series. Refreshes on a timer.
@MainActor
final class StatusModel: ObservableObject {
    @Published var running = false
    @Published var summary = "Loading..."
    @Published var samples: [StatsSample] = []
    @Published var resident: [ResidentModel] = []
    @Published var budgetGB: Double = 0

    private var timer: Timer?

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
            await MainActor.run {
                self.running = isRunning
                self.summary = clean.isEmpty ? "fxlla not found or no output" : clean
                self.samples = samples
                self.resident = health?.resident ?? []
                self.budgetGB = (health?.budgetMB ?? 0) / 1024
            }
        }
    }

    func startGateway() { runThenRefresh(["serve"]) }
    func stopGateway() { runThenRefresh(["unserve"]) }

    private func runThenRefresh(_ args: [String]) {
        busy = true
        Task.detached {
            _ = CLI.run(args)
            await MainActor.run { self.busy = false; self.refresh() }
        }
    }
}
