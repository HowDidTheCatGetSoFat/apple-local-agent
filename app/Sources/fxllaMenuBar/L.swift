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
            "Metrics": "Métricas", "live traffic": "tráfico real", "probe": "sonda",
            "Media": "Medios", "Prompt": "Indicación", "Generate": "Generar",
            "Image": "Imagen", "Video": "Video", "Voice": "Voz",
            "Install the fxlla command": "Instalar el comando fxlla",
            "Linked at": "Enlazado en",
            "Already installed at": "Ya instalado en",
            "This build has no bundled CLI.": "Esta versión no incluye el CLI.",
            "A file is already there, left untouched:": "Ya hay un archivo ahí, no se modificó:",
            "Add its folder to PATH if your shell cannot find it.":
                "Agregá su carpeta al PATH si tu shell no lo encuentra.",
            "Left untouched:": "Sin modificar:",
        ],
        "pt": [
            "Download": "Baixar", "Load": "Carregar", "Pull": "Baixar",
            "Refresh": "Atualizar", "Quit": "Sair",
            "Start gateway": "Iniciar gateway", "Stop gateway": "Parar gateway",
            "GPU RAM": "RAM GPU", "Max": "Máx", "Default": "Padrão",
            "budget": "orçamento",
            "Metrics": "Métricas", "live traffic": "tráfego real", "probe": "sonda",
            "Media": "Mídia", "Prompt": "Instrução", "Generate": "Gerar",
            "Image": "Imagem", "Video": "Video", "Voice": "Voz",
            "Install the fxlla command": "Instalar o comando fxlla",
            "Linked at": "Vinculado em",
            "Already installed at": "Já instalado em",
            "This build has no bundled CLI.": "Esta versão não inclui o CLI.",
            "A file is already there, left untouched:": "Já existe um arquivo lá, não foi alterado:",
            "Add its folder to PATH if your shell cannot find it.":
                "Adicione a pasta ao PATH se o shell não encontrar.",
            "Left untouched:": "Sem alteração:",
        ],
    ]

    static func t(_ key: String) -> String {
        let lang = Locale.current.language.languageCode?.identifier ?? "en"
        return tables[lang]?[key] ?? key
    }
}
