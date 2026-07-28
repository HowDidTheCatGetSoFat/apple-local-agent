import SwiftUI

// The dropdown panel shown from the menu bar icon.
struct PanelView: View {
    @ObservedObject var model: StatusModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("fxlla").font(.headline)
            Text(model.summary)
                .font(.system(.callout, design: .monospaced))
                .foregroundStyle(.secondary)
            Divider()
            HStack {
                Button("Refresh") { model.refresh() }
                Spacer()
                Button("Quit") { NSApplication.shared.terminate(nil) }
            }
        }
        .padding(12)
        .frame(width: 300)
    }
}
