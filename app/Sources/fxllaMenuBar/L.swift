import Foundation

// Lightweight localization for the panel. English is the base; Portuguese and
// Spanish are provided. A future version can move to a String Catalog.
enum L {
    private static let tables: [String: [String: String]] = [
        "es": [
            "Download": "Descargar", "Load": "Cargar", "Pull": "Bajar",
            "Refresh": "Actualizar", "Quit": "Salir",
            "Start gateway": "Iniciar gateway", "Stop gateway": "Detener gateway",
            "GPU RAM": "RAM GPU", "Max": "Máx", "Default": "Predeterminado",
            "budget": "presupuesto",
        ],
        "pt": [
            "Download": "Baixar", "Load": "Carregar", "Pull": "Baixar",
            "Refresh": "Atualizar", "Quit": "Sair",
            "Start gateway": "Iniciar gateway", "Stop gateway": "Parar gateway",
            "GPU RAM": "RAM GPU", "Max": "Máx", "Default": "Padrão",
            "budget": "orçamento",
        ],
    ]

    static func t(_ key: String) -> String {
        let lang = Locale.current.language.languageCode?.identifier ?? "en"
        return tables[lang]?[key] ?? key
    }
}
