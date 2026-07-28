import SwiftUI
import Charts

// Time-series charts for the metrics fxlla records: tokens/s, TTFT, and RAM.
struct ChartsView: View {
    let samples: [StatsSample]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            metric("tokens/s", color: .blue, format: "%.0f",
                   points: samples.compactMap { s in s.tps.map { (s.ts, $0) } })
            metric("TTFT ms", color: .orange, format: "%.0f",
                   points: samples.compactMap { s in s.ttftMS.map { (s.ts, $0) } })
            metric("RAM GB", color: .green, format: "%.1f",
                   points: samples.map { ($0.ts, $0.ramMB / 1024) })
        }
    }

    @ViewBuilder
    private func metric(_ title: String, color: Color, format: String,
                        points: [(Date, Double)]) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(title).font(.caption).foregroundStyle(.secondary)
                Spacer()
                if let last = points.last {
                    Text(String(format: format, last.1)).font(.caption.monospacedDigit())
                }
            }
            Chart(Array(points.enumerated()), id: \.offset) { _, p in
                LineMark(x: .value("t", p.0), y: .value(title, p.1))
                    .foregroundStyle(color)
                    .interpolationMethod(.monotone)
            }
            .chartXAxis(.hidden)
            .frame(height: 38)
        }
    }
}
