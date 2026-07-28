import SwiftUI

// The dropdown panel shown from the menu bar icon.
struct PanelView: View {
    @ObservedObject var model: StatusModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("fxlla").font(.headline)
            Text(model.summary)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(.secondary)
                .lineLimit(5)
                .fixedSize(horizontal: false, vertical: true)

            if !model.samples.isEmpty {
                Divider()
                ChartsView(samples: model.samples)
            }

            Divider()
            HStack {
                if model.running {
                    Button("Stop gateway") { model.stopGateway() }
                } else {
                    Button("Start gateway") { model.startGateway() }
                }
                if model.busy { ProgressView().controlSize(.small) }
                Button("Refresh") { model.refresh() }
                Spacer()
                Button("Quit") { NSApplication.shared.terminate(nil) }
            }
            .disabled(model.busy)
        }
        .padding(12)
        .frame(width: 320)
    }
}
