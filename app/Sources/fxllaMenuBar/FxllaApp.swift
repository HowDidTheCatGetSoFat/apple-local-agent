import SwiftUI

// fxlla menu bar app. A thin control surface over the fxlla CLI: it shells out
// to `fxlla` and reads its state, and never duplicates the CLI's logic.
@main
struct FxllaMenuBarApp: App {
    @StateObject private var model = StatusModel()

    var body: some Scene {
        MenuBarExtra {
            PanelView(model: model)
        } label: {
            Image(systemName: model.iconName)
        }
        .menuBarExtraStyle(.window)
    }
}
