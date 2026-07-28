import Foundation

// A model currently resident in the gateway.
struct ResidentModel: Identifiable {
    let alias: String
    let sizeMB: Double
    let idleS: Int
    var id: String { alias }
}

struct GatewayHealth {
    let resident: [ResidentModel]
    let budgetMB: Double
}

struct CatalogModel: Identifiable {
    let alias: String
    let sizeMB: Double
    var id: String { alias }
}

// Reads the gateway's HTTP endpoints. Async so the cooperative thread pool is
// never blocked on network I/O. Defaults to the standard local port; the
// gateway binds 127.0.0.1:8080 unless FXLLA_PORT is changed.
enum Gateway {
    static let base = "http://127.0.0.1:8080"

    private static func json(_ pathSuffix: String) async -> Any? {
        guard let url = URL(string: base + pathSuffix) else { return nil }
        let req = URLRequest(url: url, timeoutInterval: 5)
        guard let (data, _) = try? await URLSession.shared.data(for: req) else { return nil }
        return try? JSONSerialization.jsonObject(with: data)
    }

    static func health() async -> GatewayHealth? {
        guard let obj = await json("/health") as? [String: Any] else { return nil }
        var resident: [ResidentModel] = []
        if let arr = obj["resident"] as? [[String: Any]] {
            for m in arr {
                resident.append(ResidentModel(
                    alias: m["alias"] as? String ?? "?",
                    sizeMB: number(m["size_mb"]),
                    idleS: m["idle_s"] as? Int ?? 0))
            }
        }
        return GatewayHealth(resident: resident, budgetMB: number(obj["budget_mb"]))
    }

    static func models() async -> [CatalogModel] {
        guard let obj = await json("/v1/models") as? [String: Any],
              let arr = obj["data"] as? [[String: Any]] else { return [] }
        return arr.map { CatalogModel(alias: $0["id"] as? String ?? "?", sizeMB: number($0["size_mb"])) }
    }

    // Load a model by sending a tiny request; the backend loads on the gateway.
    static func warmup(_ alias: String) async {
        guard let url = URL(string: base + "/v1/chat/completions") else { return }
        var req = URLRequest(url: url, timeoutInterval: 300)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "model": alias, "max_tokens": 1, "stream": false,
            "messages": [["role": "user", "content": "hi"]],
        ]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        _ = try? await URLSession.shared.data(for: req)
    }

    private static func number(_ v: Any?) -> Double {
        if let d = v as? Double { return d }
        if let i = v as? Int { return Double(i) }
        return 0
    }
}
