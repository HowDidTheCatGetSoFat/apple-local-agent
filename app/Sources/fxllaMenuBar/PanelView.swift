import SwiftUI

// The dropdown panel shown from the menu bar icon.
struct PanelView: View {
    @ObservedObject var model: StatusModel

    var body: some View {
        ScrollView {
        VStack(alignment: .leading, spacing: 10) {
            Text("fxlla").font(.headline)
            Text(model.summary)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(.secondary)
                .lineLimit(5)
                .fixedSize(horizontal: false, vertical: true)

            if !model.models.isEmpty {
                Divider()
                ForEach(model.models) { m in
                    let res = model.residentFor(m.alias)
                    HStack {
                        Circle()
                            .fill(res != nil ? Color.green : Color.secondary.opacity(0.4))
                            .frame(width: 6, height: 6)
                        Text(m.alias).font(.caption)
                        Spacer()
                        if let res {
                            Text(String(format: "%.1f GB", res.sizeMB / 1024))
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(.secondary)
                        } else {
                            Button("Load") { model.load(m.alias) }
                                .font(.caption)
                                .buttonStyle(.borderless)
                        }
                    }
                }
                Text(String(format: "budget %.0f GB", model.budgetGB))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }

            if !model.downloadable.isEmpty {
                Divider()
                Text("Download").font(.caption2).foregroundStyle(.tertiary)
                ForEach(model.downloadable) { c in
                    HStack {
                        Text(c.alias).font(.caption)
                        Text(c.size).font(.caption2).foregroundStyle(.secondary)
                        Spacer()
                        if model.pulling.contains(c.alias) {
                            ProgressView().controlSize(.small)
                        } else {
                            Button("Pull") { model.pull(c.alias) }
                                .font(.caption)
                                .buttonStyle(.borderless)
                        }
                    }
                }
            }

            if !model.samples.isEmpty {
                Divider()
                ChartsView(samples: model.samples)
            }

            Divider()
            HStack {
                Text("GPU RAM").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Max") { model.ramAuto() }
                Button("Default") { model.ramReset() }
            }
            .font(.caption)
            .disabled(model.busy)

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
        }
        .frame(width: 320)
        .frame(maxHeight: 520)
    }
}
