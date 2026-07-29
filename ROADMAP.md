# Roadmap

Goal: the best way to run and exploit local models on Apple Silicon. This
project does not compete with opencode or Claude Code at the agent layer. It is
the best local provider of models and tools, and lets those clients handle
orchestration (skills, workflows, tool calling).

## Architecture principle

```
                    +--------------------------------------+
   opencode /       |  fxlla (control plane + engine)        |
   Claude Code ---->|                                      |
   (orchestrate)    |  model server  (MLX / llama.cpp)     |---> OpenAI endpoint
                    |  MCP: rag        (per-project KB)     |---> MCP tools
                    |  MCP: code-graph (KuzuDB)             |
                    |  MCP: media      (mflux img/video)    |
                    |  stats.json (tok/s, TTFT, RAM)        |
                    +------------------+-------------------+
                                       |
                    +------------------v-----------------+
                    |  Menu bar app (SwiftUI)            |  toggles + telemetry
                    +------------------------------------+
```

The CLI is the single source of truth. The app and the MCP servers are thin
facades that call the CLI and read its state. All the heavy logic (download,
serve, limits) lives and is tested in one place.

## Design principle: model availability and consent

A requested model may be cached or may need downloading. Whose job it is to
offer the download is split across layers, and the rule is that the tool never
surprises the user with a large download.

- Tool (`fxlla` and the MCP servers): expose availability in a machine-readable
  form (cached, size, engine) and make downloading an explicit action. `on`
  never silently pulls a large model; it fails fast with a structured
  not-cached signal, or downloads only under an explicit opt-in
  (`on --pull`). Downloads report size and estimated time at the current cap.
  Delivered on the CLI: `fxlla ls --json`, `fxlla avail <alias>`, and the
  opt-in `fxlla on <alias> --pull`.
- Skill or agent: owns the offer and the consent flow. It checks availability,
  presents size and time (and any cheaper cached alternative), asks the user,
  then pulls with progress and serves. Only the agent has the conversation
  context to ask.
- System prompt: sets the policy and thresholds (for example, auto-download
  under a small size, always confirm above a limit, prefer cached models).

## Multi-model gateway (router)

Status: v0 delivered (`fxlla serve` / `fxlla unserve`) with aggregated
`/v1/models`, on-demand load, and LRU eviction under a RAM budget. Passive
real-traffic metrics are now delivered on top of it: the gateway measures tok/s
and TTFT from the streams it proxies and appends them to the stats time-series,
closing the MLX gap that log parsing could not (see Phase 1).

Serving evolves from one model at a time to a single endpoint that fronts
many. This reconciles the two obvious designs into one: a single unified
channel (one endpoint, one aggregated model list) and on-demand loading (the
client picks any model, the gateway loads or reuses a backend). They are the
same thing seen from two sides.

- One stable OpenAI-compatible endpoint (default 127.0.0.1:8080). `/v1/models`
  aggregates the catalog and downloaded models, keyed by alias. Clients select
  a model by name in the request.
- On a request for model X: proxy to X's backend if it is running; otherwise
  load X (spawn mlx_lm.server or llama-server on an internal port), wait for
  ready, then reverse-proxy with streaming passthrough for SSE.
- RAM budget and eviction: keep several small models resident for instant
  switching; evict the least-recently-used backend when a load would exceed
  the budget or the GPU wired limit. A single large model (120B, 235B) evicts
  the rest.
- Per-backend keep-warm (idle unload), reusing the current watchdog.
- fxlla stays the source of truth for load, download, and RAM; the gateway is
  the router that orchestrates it.

Notes:
- Cold start: the first request to an unloaded model pays the load time.
  Resident hot models avoid it.
- Consumers: opencode consumes the endpoint and model list directly. Claude
  Code needs an Anthropic-shaped shim; OpenRouter needs the endpoint reachable
  (tunnel) with auth, so keep the default bind local.
- Prior art: llama-swap does this for llama.cpp only; this needs multi-engine
  with MLX first.

## Phase 0: Foundations (done, v0.1.0)

- [x] `fxlla` CLI, dual MLX + GGUF engine, bandwidth-capped downloads.
- [x] Keep-warm, GPU RAM control, opencode integration.
- [x] Verified catalog. Validated end to end.

## Phase 1: Menu bar app and metrics

- [x] Stats plumbing: rolling `stats.jsonl` with tokens per second, time to
      first token, and RAM (server process RSS). `fxlla stats [--watch]`.
      Active probe for a single-model server; passive measurement of real
      traffic from the multi-model gateway (streamed SSE token counting and
      trailing `usage`), so MLX tok/s is captured without log parsing.
- [ ] SwiftUI `MenuBarExtra` app (macOS 14+):
  - Start and stop models, show the active one.
  - RAM per model, live tokens per second and time to first token.
  - GPU RAM limit toggle (`fxlla ram auto|reset`) with a privileged helper.
  - Download progress.
  - Localization: English, Portuguese, Spanish.
- [x] Signing and notarization: Developer ID Application, `codesign` and
      `notarytool` (`app/build.sh --sign`, `app/package-dmg.sh --check|--notarize`).
      Verified end to end: a notarized, stapled `.dmg` that passes a Gatekeeper
      assessment offline.
- [ ] Installer: `.dmg` that installs the app and links the CLI.

## Phase 2: RAG and knowledge bases

Status: v0 delivered (`fxlla kb`). Remaining items are performance and options.

- [x] Local embeddings (nomic-embed via llama.cpp) and a local store (SQLite).
- [x] Per-project knowledge bases: index folders and docs, attach to any session.
- [x] MCP server `rag_search` for opencode and Claude Code.
- [x] `fxlla kb add|search|ls|rm`.
- [ ] Vector index (`sqlite-vec` or LanceDB) instead of brute-force cosine, and
      a persistent warm embedding server.
- [ ] MLX embeddings as an option.

## Phase 3: Code graph

Status: v0 delivered (`fxlla graph`) for Python via `ast` + SQLite, with an MCP
server. Remaining items are scale and language coverage.

- [x] Symbol extraction (defs, refs, callers) into a local store.
- [x] Queries: definition, references, callers.
- [x] MCP server (`find_definition`, `find_references`, `find_callers`).
- [x] `fxlla graph index <repo>`.
- [ ] Multi-language parsing with tree-sitter or SCIP (Python only today).
- [ ] KuzuDB (embedded graph DB, Cypher) instead of flat SQLite; change-impact
      and call-graph traversal queries.

## Phase 4: Media skills

A text model cannot draw or speak. It can, however, call a tool. So local
image, video, and speech generation is exposed as an MCP server, and any model
in opencode or Claude Code invokes it through tool calling.

- [x] A `media` MCP server wrapping the local toolchains: image via mflux-cv
      (`mflux-generate` and its family: FLUX, Qwen-image, Z-image, boogu, ...),
      video via ltx-2-mlx (LTX-2.3), and speech via mlx-audio (Chatterbox).
      Tools: `generate_image`, `generate_video`, `generate_speech`.
      `fxlla media image|video|voice|models` on the CLI, with output validation
      (a zero exit code is not proof of a real render).
- [ ] More image operations exposed as tools: `edit_image`, `upscale_image`
      (mflux-cv already ships the edit/upscale CLIs).
- [x] GPU and memory coordination through `fxlla`. Media generation frees the
      gateway's resident models before a job (`POST /admin/unload`) so it does
      not compete with a large LLM for unified memory; the gateway reloads on
      demand. Opt out with `--keep-models` / `FXLLA_MEDIA_KEEP_MODELS`.
- [ ] Async job model for heavy work: submit returns a job id; the caller polls
      for status and the output file path. Keeps tool calls fast and lets video
      run in the background.
- [ ] Reuse the bandwidth-capped `fxlla pull` for image and video weights,
      cached under `FXLLA_STORE` like text models.
- [ ] Set expectations: image generation is fast on this hardware; local video
      is heavy and best-effort.

## Cross-cutting

- [ ] Evals: measure quality and speed per model to choose with data.
- [ ] `fxlla doctor`: environment diagnostics.
- [ ] Optional persistence of the wired limit (LaunchDaemon).
- [ ] Shell completions for bash and zsh.

## Open decisions

- Vector store: `sqlite-vec` (simple) vs LanceDB (scale). Phase 2.
- Graph database: KuzuDB (embedded) is the candidate. Confirm in Phase 3.
- MCP lifecycle owned by the CLI, not the app.
