import SwiftUI

// The dropdown panel shown from the menu bar icon.
struct PanelView: View {
    @ObservedObject var model: StatusModel
    @State private var pullTarget: CatalogEntry?
    @State private var cliInstallNote: String?

    var body: some View {
        ScrollView {
        VStack(alignment: .leading, spacing: 10) {
            Text("fxlla").font(.headline)
            Text(model.summary)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(.secondary)
                .lineLimit(5)
                .fixedSize(horizontal: false, vertical: true)

            if let err = model.lastError, !err.isEmpty {
                Text(err)
                    .font(.caption2)
                    .foregroundStyle(.red)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
            }

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
                            Button(L.t("Load")) { model.load(m.alias) }
                                .font(.caption)
                                .buttonStyle(.borderless)
                        }
                    }
                }
                Text("\(L.t("budget")) " + String(format: "%.0f GB", model.budgetGB))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }

            if !model.downloadable.isEmpty {
                Divider()
                Text(L.t("Download")).font(.caption2).foregroundStyle(.tertiary)
                ForEach(model.downloadable) { c in
                    HStack {
                        Text(c.alias).font(.caption)
                        Text(c.size).font(.caption2).foregroundStyle(.secondary)
                        Spacer()
                        if model.pulling.contains(c.alias) {
                            ProgressView().controlSize(.small)
                        } else {
                            Button(L.t("Pull")) { pullTarget = c }
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
            Text(L.t("Media")).font(.caption2).foregroundStyle(.tertiary)
            TextField(L.t("Prompt"), text: $model.mediaPrompt)
                .textFieldStyle(.roundedBorder)
                .font(.caption)
                .disabled(model.generating)
            Picker("", selection: $model.mediaKind) {
                ForEach(MediaKind.allCases) { kind in
                    Text(L.t(kind.label)).tag(kind)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .disabled(model.generating)
            HStack {
                Button(L.t("Generate")) { model.generateMedia() }
                    .disabled(model.generating
                        || model.mediaPrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                if model.generating { ProgressView().controlSize(.small) }
            }
            .font(.caption)

            Divider()
            HStack {
                Text(L.t("GPU RAM")).font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button(L.t("Max")) { model.ramAuto() }
                Button(L.t("Default")) { model.ramReset() }
            }
            .font(.caption)
            .disabled(model.busy)

            HStack {
                if model.running {
                    Button(L.t("Stop gateway")) { model.stopGateway() }
                } else {
                    Button(L.t("Start gateway")) { model.startGateway() }
                }
                if model.busy { ProgressView().controlSize(.small) }
                Button(L.t("Refresh")) { model.refresh() }
                Spacer()
                Button(L.t("Quit")) { NSApplication.shared.terminate(nil) }
            }
            .disabled(model.busy)

            // Installing the CLI onto PATH writes outside the app bundle, so it
            // is an explicit user action rather than something the installer did.
            // Hidden in dev builds, where there is no bundled CLI to link.
            if CLI.bundled != nil {
                VStack(alignment: .leading, spacing: 2) {
                    Button(L.t("Install the fxlla command")) { installCLI() }
                        .controlSize(.small)
                    if let note = cliInstallNote {
                        Text(note)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }

            HStack {
                Text("fxlla " + (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""))
                Spacer()
                Text(Gateway.base)
            }
            .font(.caption2)
            .foregroundStyle(.tertiary)
        }
        .padding(12)
        }
        .frame(width: 320)
        .frame(maxHeight: 520)
        .confirmationDialog(
            pullTarget.map { "\(L.t("Download")) \($0.alias) (\($0.size))?" } ?? "",
            isPresented: Binding(get: { pullTarget != nil },
                                 set: { if !$0 { pullTarget = nil } }),
            presenting: pullTarget
        ) { target in
            Button(L.t("Pull")) { model.pull(target.alias); pullTarget = nil }
            Button("Cancel", role: .cancel) { pullTarget = nil }
        }
    }

    // Links the bundled CLI into ~/.local/bin and reports exactly what happened,
    // including the path, so the user can verify it (or add it to PATH).
    private func installCLI() {
        switch CLI.installOnPath() {
        case .installed(let path):
            cliInstallNote = L.t("Linked at") + " \(path)\n" + L.t("Add its folder to PATH if your shell cannot find it.")
        case .alreadyInstalled(let path):
            cliInstallNote = L.t("Already installed at") + " \(path)"
        case .noBundle:
            cliInstallNote = L.t("This build has no bundled CLI.")
        case .transientLocation:
            cliInstallNote = L.t("Move fxlla to Applications and reopen it first, so the command does not break.")
        case .occupied(let path):
            cliInstallNote = L.t("A file is already there, left untouched:") + " \(path)"
        case .linksElsewhere(let path, let destination):
            cliInstallNote = L.t("Left untouched:") + " \(path) "
                + L.t("already points to") + " \(destination)"
        case .error(let message):
            cliInstallNote = message
        }
    }
}
