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

## Phase 0: Foundations (done, v0.1.0)

- [x] `fxlla` CLI, dual MLX + GGUF engine, bandwidth-capped downloads.
- [x] Keep-warm, GPU RAM control, opencode integration.
- [x] Verified catalog. Validated end to end.

## Phase 1: Menu bar app and metrics

- [ ] Stats plumbing: `stats.json` with tokens per second, time to first
      token, and RAM (server process RSS). Sources: `llama-server` timings
      (the `timings` object plus `/metrics`) and parsing of the
      `mlx_lm.server` log. New `fxlla stats` command.
- [ ] SwiftUI `MenuBarExtra` app (macOS 14+):
  - Start and stop models, show the active one.
  - RAM per model, live tokens per second and time to first token.
  - GPU RAM limit toggle (`fxlla ram auto|reset`) with a privileged helper.
  - Download progress.
  - Localization: English, Portuguese, Spanish.
- [ ] Signing and notarization: Developer ID Application, `codesign` and
      `notarytool`. Distribute as a signed `.dmg`.
- [ ] Installer: `.dmg` that installs the app and links the CLI.

## Phase 2: RAG and knowledge bases

- [ ] MLX embeddings (for example `bge-m3` or `nomic-embed`) with a local
      vector store (`sqlite-vec` or LanceDB).
- [ ] Per-project knowledge bases (not per model): index folders and docs,
      attach to any session.
- [ ] Expose as an MCP server `rag-search` for opencode and Claude Code.
- [ ] `fxlla kb add|index|ls|rm`.

## Phase 3: Code graph

- [ ] Parsing with tree-sitter or SCIP; symbol graph in KuzuDB (embedded graph
      database, Cypher, well suited to local use).
- [ ] Queries: definition, references, call graph, change impact.
- [ ] MCP server `code-graph`. Combined with RAG this gives structural plus
      semantic navigation of large repositories. This is the main
      differentiator.
- [ ] `fxlla graph index <repo>`.

## Phase 4: Media skills (mflux)

A text model cannot draw. It can, however, call a tool. So local image and
video generation is exposed as an MCP server, and any model in opencode or
Claude Code invokes it through tool calling.

- [ ] A `media` MCP server wrapping the local `mflux` toolchain
      (`mflux-generate` and its variants: FLUX, Qwen-image, Z-image, controlnet,
      depth, fill, kontext, redux, upscale). Tools: `generate_image`,
      `edit_image`, `upscale_image`, and `generate_video` when a local video
      backend is available.
- [ ] GPU and memory coordination through `fxlla`. Image and video generation
      compete with the LLM server for unified memory, so the MCP asks `fxlla`
      to free the model before a heavy job and reload it after. This is why
      `fxlla` owns the lifecycle.
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
