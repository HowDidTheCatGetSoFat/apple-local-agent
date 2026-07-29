---
name: fxlla-code-graph
description: Navigate a Python codebase structurally with the fxlla code graph before editing or reasoning about impact. Use to find where a symbol is defined, who references or calls it, the blast radius of a change, and dead code.
---

# Use the code graph before editing Python

fxlla exposes a structural view of a Python codebase over an MCP server:
`find_definition`, `find_references`, `find_callers`, `find_impact`, and
`list_unused`. Use it to reason about code with facts instead of guesses.

## When to use

- Before changing a function, class, or method: check who calls it and the
  transitive blast radius (`find_impact`) so you do not miss a caller.
- To locate where a symbol is defined (`find_definition`) or every place it is
  referenced (`find_references`).
- To find dead-code candidates (`list_unused`) before a cleanup.

## How

- Query by symbol name. Results are name-approximate (a scope-level match), so
  confirm the exact site in the file before editing.
- Use `find_impact` to bound a refactor: it walks transitive callers.
- Treat the graph as a map, not a substitute for reading the code at the site
  you change.

## Notes

- Python only: symbols are extracted with the standard library `ast` and stored
  in an embedded KuzuDB graph. `fxlla graph` runs the backend under
  `uv run --with kuzu`, so no manual install is needed.
- The graph must be indexed first: `fxlla graph index <paths...>`. If a query
  returns nothing, suggest indexing.
