# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Optional sqlite-vec KNN index for `fxlla kb search`: set `FXLLA_KB_INDEX=1` to
  replace the brute-force cosine scan with a vector index rebuilt on demand from
  the chunks table. It runs `rag/core.py` under `uv run --with sqlite-vec`
  (no persistent install) and falls back to the scan when the extension is
  unavailable, so a clean clone still works. The `fxlla kb` CLI is unchanged.
- Image editing and upscaling: `fxlla media edit "<prompt>" --image <path>`
  (mflux-cv qwen-edit) and `fxlla media upscale --image <path>` (mflux-cv
  seedvr2), exposed over MCP as `edit_image` and `upscale_image`.
- `fxlla ram persist` / `unpersist`: install or remove a LaunchDaemon that
  re-applies the GPU wired limit at every boot, so `fxlla ram auto` survives a
  reboot.
- `fxlla pull --downloader hf`: fetch a Hugging Face repo with the official CLI
  (run via `uvx`, no persistent install) instead of the aria2c tree walk. More
  robust for xet-backed and LFS repos, but it ignores the bandwidth cap. Default
  stays aria2 (`FXLLA_DOWNLOADER`).
- Civitai as a weight source: `fxlla pull civitai:<id>` (or a civitai.com URL)
  downloads a LoRA or checkpoint from civitai.com into `<store>/civitai`,
  bandwidth-capped like every pull, authenticated with `FXLLA_CIVITAI_TOKEN`.
- Tool enablement: `fxlla wire-opencode --all` registers the provider and every
  MCP server (rag, graph, media) at once, and `fxlla skills install` installs a
  portable skill pack (`skills/`) that tells a model when to use the tools -
  retrieve from a knowledge base, walk the code graph before editing, offer
  media generation, and check availability and consent before a download.
  Installs for Claude Code (`~/.claude/skills`) and opencode (its `instructions`
  list), idempotently.
- Menu bar app media controls: a prompt, an image/video/voice picker, and a
  Generate button that runs `fxlla media` and reveals the output in Finder. A
  path-filtered macOS CI job builds the app on `app/**` changes.
- Media generation (`fxlla media image|video|voice`): local image generation
  through the mflux-cv toolchain (z-image-turbo and friends), short video through
  ltx-2-mlx (LTX-2.3), and text-to-speech through mlx-audio (Chatterbox), with
  output validation. Exposed to opencode and Claude Code as an MCP server
  (`generate_image`, `generate_video`, `generate_speech`) via
  `fxlla media wire-opencode`. Speech runs under a separate interpreter
  (`FXLLA_VOICE_PYTHON`) so fxlla itself never depends on mlx-audio.
- Machine-readable model availability: `fxlla ls --json` lists cached models
  (alias, size, engine, repo) and `fxlla avail <alias>` reports
  `{cached, known, engine, repo, size}` for any catalog or downloaded model, so
  an agent can check availability before offering a download. `fxlla on` gains
  opt-in `--pull` while staying fail-fast by default when a model is not cached.
- Shell completions: `fxlla completions <bash|zsh>` prints a completion script
  (load with `source <(fxlla completions bash)`). Completes commands, catalog
  aliases for `pull`, downloaded models for `on`/`off`/`rm`, and `kb`/`graph`
  subcommands, driven by a hidden `fxlla __complete` helper.
- Passive gateway metrics: the multi-model gateway now derives time to first
  token and tokens per second from real proxied traffic (streamed SSE or a
  response's `usage`) and appends samples to the same `stats.jsonl` time-series
  the menu bar app renders, so charts reflect actual usage rather than only a
  synthetic probe. `fxlla stats [--watch]` reads these samples when the gateway
  is serving.
- Code graph (`fxlla graph index|def|refs|callers`): index a Python codebase
  with the `ast` module into a SQLite store and navigate it structurally (where
  a symbol is defined, referenced, and which functions call it). Exposed to
  opencode and Claude Code as an MCP server (`find_definition`,
  `find_references`, `find_callers`).
- RAG knowledge bases (`fxlla kb add|search|ls|rm`): index files locally with a
  small embedding model (llama.cpp) into a SQLite store and search by cosine
  similarity. Exposed to opencode and Claude Code as an MCP server (`rag_search`)
  via `fxlla kb wire-opencode`.
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
  memory, media prerequisites, server health). The media section checks the
  image/video/voice backends and weight cache so a fresh machine sees the gap
  instead of a cryptic runtime failure.
- `fxlla pull` now fails loudly if a download leaves pending `.aria2` control
  files, instead of marking an incomplete model as complete.

### Changed
- Media generation now frees the gateway's resident models before a job so a
  heavy render does not compete with a large LLM for unified memory (they share
  it, and the overlap could OOM). The gateway exposes `POST /admin/unload` and
  reloads on demand afterward; opt out with `--keep-models` or
  `FXLLA_MEDIA_KEEP_MODELS`. MCP tool calls get this for free.
- Hardened app signing and packaging: shared `app/sign-lib.sh` checks the
  Developer ID identity before building and verifies the app is signed with the
  hardened runtime; `app/package-dmg.sh --check` validates the signing
  prerequisites, and the notarize path validates the staple and runs a
  Gatekeeper assessment. Both notarytool credential routes are documented.

### Fixed
- Configuration precedence: an exported environment variable now wins over
  `~/.config/fxlla/config.env` as documented. Sourcing `config.env` (plain
  assignments) used to clobber values exported in the shell.
- Signing checks captured command output before matching: piping into
  `grep -q` under `set -o pipefail` could SIGPIPE the producer and report a
  false negative (e.g. "not signed with the hardened runtime" on a signed app).

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
