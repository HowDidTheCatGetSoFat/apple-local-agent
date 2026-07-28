import SwiftUI

// Holds the state shown in the menu bar, populated from `fxlla status`.
@MainActor
final class StatusModel: ObservableObject {
    @Published var running = false
    @Published var summary = "Loading..."

    var iconName: String { running ? "cpu.fill" : "cpu" }

    init() {
        refresh()
    }

    func refresh() {
        Task.detached {
            let (out, _) = CLI.run(["status"])
            let clean = out.strippingANSI().trimmingCharacters(in: .whitespacesAndNewlines)
            let isRunning = clean.localizedCaseInsensitiveContains("running")
            await MainActor.run {
                self.running = isRunning
                self.summary = clean.isEmpty ? "fxlla not found or no output" : clean
            }
        }
    }
}
