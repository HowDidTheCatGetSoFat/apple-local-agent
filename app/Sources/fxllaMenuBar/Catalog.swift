import Foundation

// A catalog entry parsed from `fxlla models`.
struct CatalogEntry: Identifiable {
    let alias: String
    let size: String
    var id: String { alias }
}

enum Catalog {
    private static func columns(_ line: Substring) -> [String] {
        line.split(whereSeparator: { $0 == " " || $0 == "\t" }).map(String.init).filter { !$0.isEmpty }
    }

    // Every catalog model (alias, size), from `fxlla models`.
    static func all() -> [CatalogEntry] {
        let (out, _) = CLI.run(["models"])
        var entries: [CatalogEntry] = []
        for line in out.strippingANSI().split(separator: "\n") {
            let cols = columns(line)
            guard cols.count >= 2, cols[0] != "ALIAS", !cols[0].hasPrefix("-") else { continue }
            entries.append(CatalogEntry(alias: cols[0], size: cols[1]))
        }
        return entries
    }

    // Aliases already downloaded, from `fxlla ls`.
    static func downloaded() -> Set<String> {
        let (out, _) = CLI.run(["ls"])
        var s = Set<String>()
        for line in out.strippingANSI().split(separator: "\n") {
            if let first = columns(line).first { s.insert(first) }
        }
        return s
    }
}
