import AppKit
import Combine
import SwiftUI

// fxlla menu bar app. Uses AppKit's NSStatusItem + NSPopover to host the
// SwiftUI panel: this is the reliable menu-bar pattern (SwiftUI's MenuBarExtra
// .window style does not open reliably in a SwiftPM-built bundle). The app is a
// thin control surface over the fxlla CLI.
@main
enum FxllaMenuBarApp {
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        app.run()
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private let popover = NSPopover()
    private let model = StatusModel()
    private var cancellables = Set<AnyCancellable>()

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.image = icon(false)
            button.action = #selector(togglePopover)
            button.target = self
        }

        popover.behavior = .transient
        popover.contentViewController = NSHostingController(rootView: PanelView(model: model))

        // Keep the menu bar icon in sync with the running state.
        model.$running
            .receive(on: RunLoop.main)
            .sink { [weak self] running in self?.statusItem.button?.image = self?.icon(running) }
            .store(in: &cancellables)
    }

    private func icon(_ running: Bool) -> NSImage? {
        NSImage(systemSymbolName: running ? "cpu.fill" : "cpu", accessibilityDescription: "fxlla")
    }

    @objc private func togglePopover() {
        guard let button = statusItem.button else { return }
        if popover.isShown {
            popover.performClose(nil)
        } else {
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
            popover.contentViewController?.view.window?.makeKey()
        }
    }
}
