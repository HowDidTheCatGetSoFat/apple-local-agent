import Foundation

// One sample of the rolling time-series that `fxlla stats` writes.
struct StatsSample: Identifiable {
    let id = UUID()
    let ts: Date
    let model: String
    let ramMB: Double
    let ttftMS: Double?
    let tps: Double?
}

enum Stats {
    // ~/.local/state/fxlla/stats.jsonl (respecting XDG_STATE_HOME).
    static var path: String {
        let env = ProcessInfo.processInfo.environment
        let base = env["XDG_STATE_HOME"]
            ?? FileManager.default.homeDirectoryForCurrentUser.path + "/.local/state"
        return base + "/fxlla/stats.jsonl"
    }

    // The last `limit` samples from the time-series.
    static func recent(limit: Int = 300) -> [StatsSample] {
        guard let text = try? String(contentsOfFile: path, encoding: .utf8) else { return [] }
        var out: [StatsSample] = []
        for line in text.split(separator: "\n").suffix(limit) {
            guard let data = line.data(using: .utf8),
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let ts = num(obj["ts"]) else { continue }
            out.append(StatsSample(
                ts: Date(timeIntervalSince1970: ts),
                model: obj["model"] as? String ?? "?",
                ramMB: num(obj["ram_mb"]) ?? 0,
                ttftMS: num(obj["ttft_ms"]),
                tps: num(obj["tps"])))
        }
        return out
    }

    // JSON numbers arrive as Int or Double; null (no probe) becomes nil.
    private static func num(_ v: Any?) -> Double? {
        if let d = v as? Double { return d }
        if let i = v as? Int { return Double(i) }
        return nil
    }
}
