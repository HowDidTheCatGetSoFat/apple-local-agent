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

// Reads the gateway's /health endpoint. Defaults to the standard local port;
// the gateway binds 127.0.0.1:8080 unless FXLLA_PORT is changed.
enum Gateway {
    static let base = "http://127.0.0.1:8080"

    static func health() -> GatewayHealth? {
        guard let url = URL(string: base + "/health"),
              let data = try? Data(contentsOf: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }

        let budget = number(obj["budget_mb"])
        var resident: [ResidentModel] = []
        if let arr = obj["resident"] as? [[String: Any]] {
            for m in arr {
                resident.append(ResidentModel(
                    alias: m["alias"] as? String ?? "?",
                    sizeMB: number(m["size_mb"]),
                    idleS: m["idle_s"] as? Int ?? 0))
            }
        }
        return GatewayHealth(resident: resident, budgetMB: budget)
    }

    // Every downloaded model the gateway can serve.
    static func models() -> [CatalogModel] {
        guard let url = URL(string: base + "/v1/models"),
              let data = try? Data(contentsOf: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let arr = obj["data"] as? [[String: Any]]
        else { return [] }
        return arr.map { CatalogModel(alias: $0["id"] as? String ?? "?", sizeMB: number($0["size_mb"])) }
    }

    // Load a model by sending a tiny request; blocks until the backend is ready.
    static func warmup(_ alias: String) {
        guard let url = URL(string: base + "/v1/chat/completions") else { return }
        var req = URLRequest(url: url, timeoutInterval: 300)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "model": alias, "max_tokens": 1, "stream": false,
            "messages": [["role": "user", "content": "hi"]],
        ]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        let sem = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: req) { _, _, _ in sem.signal() }.resume()
        sem.wait()
    }

    private static func number(_ v: Any?) -> Double {
        if let d = v as? Double { return d }
        if let i = v as? Int { return Double(i) }
        return 0
    }
}

struct CatalogModel: Identifiable {
    let alias: String
    let sizeMB: Double
    var id: String { alias }
}
