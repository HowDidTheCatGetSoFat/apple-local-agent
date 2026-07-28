# fxlla code graph

Structural navigation of a Python codebase. Standard library only (`ast` +
SQLite), no dependencies.

## Pieces

- `codegraph.py` - the store and CLI. Extracts definitions (functions, classes,
  methods), references (calls), and the enclosing scope of each call into a
  SQLite store under `<FXLLA_STORE>/graph`.
- `graph_mcp.py` - a minimal stdio MCP server exposing `find_definition`,
  `find_references`, `find_callers`, `find_impact`, and `list_unused`.

Driven through the CLI: `fxlla graph index|def|refs|callers|impact|unused|ls|rm`,
`fxlla graph mcp`, `fxlla graph wire-opencode`.

- `impact <name>` walks the call graph to show transitive callers (the blast
  radius of a change), capped by `--depth`.
- `unused` lists definitions never referenced by name (dead-code candidates).

## Notes and limits (v0)

- Python only (via `ast`). Multi-language via tree-sitter is a later step.
- References are matched by name; this is scope-approximate, not a full resolver
  (a call to `x.run` and a top-level `run` share the name `run`).
- A graph database (KuzuDB) with Cypher queries is the planned upgrade from the
  flat SQLite tables.
