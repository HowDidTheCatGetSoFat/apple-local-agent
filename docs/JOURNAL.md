# Journal

Engineering log of decisions and findings. Newest entry on top.

## 2026-07-28: Metrics sourcing and fxlla stats

Shipped `fxlla stats` (RAM from server RSS, TTFT and tok/s from a small probe,
appended to a rolling stats.jsonl time-series). Validated on M5 Max:
qwen3-coder (30B-A3B 4bit) about 115 tok/s and 90 ms warm TTFT, 16.7 GB RAM.

Finding on passive metrics: mlx_lm.server does not log per-request tok/s (only
prompt-processing progress and the HTTP line), so passive decode-rate metrics
for MLX cannot come from log parsing. They will come from the multi-model
gateway, which sees the real token stream when it proxies. llama-server exposes
/metrics (Prometheus) and can be read passively. So the passive side of the
stats work is partly a dependency of the gateway.

Also added `fxlla doctor` (environment diagnostics: deps, PATH, store, GPU
memory, server health).

## 2026-07-28: Model availability and consent

Decided the responsibility split for downloading a model that is not cached.
The tool exposes availability in machine-readable form and makes downloads
explicit (no silent large pulls; `on` fails fast or opts in with `--pull`).
The skill or agent owns the offer and consent flow, since only it has the
conversation context to ask. The system prompt sets the thresholds. See
ROADMAP "Design principle: model availability and consent". Tracked as an
issue for the machine-readable status and the offer flow.

## 2026-07-28: Project start

Goal: run the best open-weights models locally on a MacBook Pro M5 Max
(128 GB, 40 GPU cores) for development and red team work, usable from opencode
without losing the Claude models in Claude Code.

### Decisions

- MLX as the primary engine (more efficient than llama.cpp on Metal), and
  GGUF via llama.cpp as the second engine only when a model has no MLX build
  or a specific imatrix quant is wanted. GGUF is not loadable by MLX except in
  limited cases; they are separate ecosystems.
- opencode as the client for local models; Claude Code left untouched (pointing
  it at a local base URL would replace Claude, not add to it).
- Models cached on an external disk (`/Volumes/1TB-WD750-1`, APFS, 931 GB
  free), not the internal SSD.
- Bandwidth-capped downloads via `aria2c --max-overall-download-limit`
  (LM Studio, ollama, and `hf` do not do this natively). 25 Mbps is about
  3.1 MB/s.

### Technical findings

- The file list for a repository comes from the HF tree API
  (`/api/models/<repo>/tree/main?recursive=true`), paginated through the `Link`
  header. An `aria2c` input file is built with `dir=` and `out=` per file.
- macOS ships bash 3.2: an empty array under `set -u` fails
  (`${arr[@]+"${arr[@]}"}`), and awk with direct interpolation is fragile.
  Scripts re-exec under bash when started by another shell such as zsh.
- macOS caps GPU RAM at about 75 percent (98 GB of 128). It is raised with
  `sysctl iogpu.wired_limit_mb`. Recommended 122880 (leaves 8 GB for the OS).
  Reverts on reboot.
- `mlx_lm.server` reports the model id as `default_model` (not the path); the
  opencode `local` provider uses that stable id.
- Signing available: `Developer ID Application` present in the keychain, plus
  Xcode 16 and Swift 6.3, so notarization is viable.

### End-to-end validation (model `tiny`, Qwen2.5-Coder-0.5B-4bit)

- `pull`: 265 MB in 1:44, about 20 Mbit effective (cap working).
- `on`: serves on `:8080`, chat/completions responds, `usage` returns tokens.
- `status` shows the idle timer; `off` cleans the process and state.

### Vision captured (see ROADMAP)

Signed menu bar app with live metrics; per-project RAG and a code graph
(KuzuDB) exposed as MCP servers; image and video skills (mflux) through tool
calling. Position: be the best local provider of models and tools, and leave
orchestration (skills, workflows) to opencode and Claude Code.

### Open

Phase order to prioritize; vector store (sqlite-vec vs LanceDB); confirm
KuzuDB for the graph.
