# fxlla RAG

Local retrieval-augmented search over your files. Standard library only, plus
`llama-server` for embeddings. Nothing leaves the machine.

## Pieces

- `rag.py` - the store and CLI. SQLite under `<FXLLA_STORE>/kb`, text chunking,
  embeddings via a local `llama-server --embeddings`, and cosine search.
- `rag_mcp.py` - a minimal stdio MCP server exposing one tool, `rag_search`,
  so opencode and Claude Code can query a knowledge base.

Driven through the CLI: `fxlla kb add|search|ls|rm`, `fxlla kb mcp`,
`fxlla kb wire-opencode`.

## Requirements

- The `embed` catalog model: `fxlla pull embed --quant Q5_K_M`.
- `FXLLA_STORE` set (the CLI passes it).

## Notes and limits (v0)

- Search is brute-force cosine over all chunks in a knowledge base. Fine for
  thousands of chunks; a vector index (sqlite-vec / LanceDB) is a later step.
- Each `add` and `search` starts and stops the embedding server. A persistent
  warm server is a later optimization.
- Indexed file types are common text and code extensions; binary files are
  skipped.
