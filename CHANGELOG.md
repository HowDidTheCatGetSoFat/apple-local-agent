# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Menu bar app (`app/`, SwiftUI `MenuBarExtra`): live `fxlla status`,
  time-series charts for tokens/s, TTFT, and RAM (GB), gateway start/stop,
  model list with on-demand load, and a GPU RAM limit toggle. Builds to a
  `.app` bundle with an optional signed `.dmg`.
- Multi-model gateway (`fxlla serve` / `fxlla unserve`): one OpenAI-compatible
  endpoint fronting every downloaded model, with on-demand loading and
  least-recently-used eviction under a RAM budget. Aggregated `/v1/models`,
  streaming passthrough, and opencode registration of all local models.
- `fxlla stats [--watch] [--json]`: live RAM (server RSS), time to first token,
  and tokens per second via a small probe, appended to a rolling time-series
  (`stats.jsonl`) for the menu bar app.
- `fxlla doctor`: environment diagnostics (dependencies, PATH, store, GPU
  memory, server health).
- `fxlla pull` now fails loudly if a download leaves pending `.aria2` control
  files, instead of marking an incomplete model as complete.

### Planned
- Menu bar app (SwiftUI `MenuBarExtra`), signed and notarized.
- Live metrics: tokens per second, time to first token, RAM per model.
- Per-project RAG and knowledge bases exposed as an MCP server.
- Code graph (KuzuDB) exposed as an MCP server.
- Image and video generation skills (mflux) exposed as MCP tools.
- Signed `.dmg` installer.

## [0.1.0] - 2026-07-28

First working release of the `fxlla` CLI. Validated end to end on M5 Max
(128 GB).

### Added
- `fxlla` CLI: `setup`, `models`, `pull`, `ls`, `on`, `off`, `status`, `logs`,
  `ram`, `wire-opencode`, `rm`, `config`.
- MLX engine (`mlx_lm.server`) as the default backend.
- GGUF backend via llama.cpp (`llama-server`); the engine is selected per
  model through the `.engine` marker. Quant selection with `--quant`.
- Bandwidth-capped downloads (`aria2c`, `FXLLA_RATE_MBIT`, default 25),
  resumable, cached on an external disk (`FXLLA_STORE`).
- Keep-warm watchdog that stops the server after `FXLLA_KEEP_WARM` idle minutes
  (default 10, `0` to disable).
- `fxlla ram` to raise or reset `iogpu.wired_limit_mb` for full 128 GB use.
- opencode integration through a `local` OpenAI-compatible provider. Claude
  Code is left untouched.
- Verified catalog of models across development, agentic, red team, and GGUF
  roles.

### Notes
- Compatible with the macOS system bash (3.2).
