import SwiftUI

// Holds the state shown in the menu bar. Populated from the fxlla CLI; this is
// a scaffold and gets wired to the CLI and stats.jsonl in later commits.
@MainActor
final class StatusModel: ObservableObject {
    @Published var running = false
    @Published var summary = "Loading..."

    var iconName: String { running ? "cpu.fill" : "cpu" }

    init() {
        refresh()
    }

    func refresh() {
        // TODO: shell out to `fxlla status` and parse. Placeholder for now.
        summary = "fxlla menu bar (scaffold)"
    }
}
