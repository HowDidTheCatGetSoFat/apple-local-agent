# fxlla skills

A portable pack of guidance that teaches a model when and how to use the fxlla
tools. Wiring an MCP server exposes a tool; these skills tell the model when to
reach for it.

## The pack

- `fxlla-knowledge` - retrieve from a local RAG knowledge base (`rag_search`)
  before answering questions about the user's own material.
- `fxlla-code-graph` - navigate a Python codebase structurally (definitions,
  references, callers, impact) before editing.
- `fxlla-media` - generate images, video, or speech locally when asked for a
  visual or audio artifact.
- `fxlla-model-access` - check availability (`fxlla avail`) and get consent
  before downloading weights, so a large pull never happens by surprise.

Each is a `SKILL.md` with a short frontmatter (`name`, `description`) and a body,
the format Claude Code loads directly.

## Install

```sh
fxlla skills install                 # both clients
fxlla skills install --client claude # ~/.claude/skills/<name>/SKILL.md
fxlla skills install --client opencode  # opencode.json "instructions" list
fxlla wire-opencode --all            # provider + every MCP server + these skills
```

Install is idempotent. Restart the client to pick up the changes. Both clients
install from a copy (`~/.claude/skills` and `~/.local/share/fxlla/skills`), so
they keep working if this repo is moved or re-cloned elsewhere.
