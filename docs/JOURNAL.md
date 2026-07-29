# Journal

Engineering log of decisions and findings. Newest entry on top.

## 2026-07-29: Code graph on KuzuDB (Phase A)

Second step of the RAG/KuzuDB priority. Swapped the code graph's flat SQLite
store for an embedded KuzuDB graph, keeping the `ast` extraction (`_Visitor`),
the CLI, and the MCP tools identical.

- Model: `Def` and `Ref` node tables mirror the old `defs`/`refs` rows, plus a
  derived `CALLS` relationship from the definition that encloses a call to every
  definition sharing the called name (name-approximate, since Python calls are
  not statically resolved). CALLS is rebuilt in full after each `index` with a
  single MERGE join, so the per-file incremental replace of Def/Ref is untouched
  and edges never duplicate.
- `impact` is now one Cypher variable-length path
  (`MATCH p=(a:Def)-[:CALLS*1..N]->(t:Def) ... min(length(p))`), replacing the
  hand-rolled Python BFS. `unused` uses a `NOT EXISTS { }` subquery. `refs` and
  `callers` stay name-based over `Ref` nodes, so they still resolve calls to
  symbols with no project definition (verified against `serialize_float32`).
- KuzuDB is not stdlib, so `import kuzu` is deferred inside `_conn()`: the module
  still imports under system python for the `_Visitor`/MCP unit tests (CI runs
  there), while `fxlla graph` runs the backend under `uv run --with kuzu`
  (`FXLLA_GRAPH_PYTHON` overrides). The MCP server runs under the same
  interpreter so its `sys.executable` re-invocation of codegraph.py inherits
  kuzu. No stdlib fallback: KuzuDB is the sole engine now, matching the plan.
- Variable-length path bounds cannot be parameterized, so the depth (an int from
  argparse, clamped 1..50) is formatted into the query string.
- Verified end to end: indexed the repo (290 defs, 1418 refs), def/callers/
  impact/refs/unused/stats/ls/rm, the JSON-RPC MCP path, and re-index idempotency.

## 2026-07-29: RAG vector index (sqlite-vec), opt-in

First step of the RAG/KuzuDB priority. `fxlla kb search` scored every chunk with
a python cosine loop (O(n)); fine for small bases, wasteful past a few thousand
chunks.

- `FXLLA_KB_INDEX=1` opts into a `sqlite-vec` KNN index. A per-kb `vec0` virtual
  table (`vec_<kb>`, cosine distance) is rebuilt on demand from the `chunks`
  table whenever its row count drifts, so `cmd_add` stays untouched and the
  index never re-embeds anything - it only repacks stored vectors.
- The chunks table remains the single source of truth. The index is derived
  state; deleting or corrupting it only forces a rebuild on the next search.
- sqlite-vec is not in the system python and macOS system python often forbids
  `load_extension`. So with the index on, `bin/fxlla kb` runs `rag/core.py`
  under `uv run --with sqlite-vec --no-project python` (uv is a hard dep, same
  pattern as `pull --downloader hf`). `FXLLA_KB_PYTHON` overrides the interpreter.
- Fully graceful: `_load_vec` returns None when the index is off or the extension
  will not load, and search falls back to the original brute-force scan. Scores
  are identical (cosine distance = 1 - cosine similarity), verified end to end
  against the live nomic embedder.
- Gotcha found in testing: dropping a `vec0` table needs the extension loaded on
  that connection, so `cmd_rm` loads it best-effort and guards the DROP.
- The RAG MCP server still uses the scan (it spawns system python3); routing it
  through the index is left in the roadmap.

## 2026-07-28: Media and gateway memory coordination

Closed the one robustness gap from docs/roadmap-remaining.md (Tier 1). Media
generation and the gateway's resident LLMs share unified memory; a heavy render
next to a large model could exceed the wired limit and OOM the machine.

- Gateway gained `POST /admin/unload` (Manager.unload_all): terminates every
  resident backend and clears the registry, keeps serving, reloads on demand.
- The coordination lives in `media/generate.py` (free_gpu), not the bash CLI, so
  MCP tool calls get it too - they go through generate.py, not `fxlla media`.
  Each generator calls it before the heavy subprocess.
- Best-effort: no gateway (connection refused) means nothing to free, and the
  request failure is swallowed. Opt out with `--keep-models` /
  `FXLLA_MEDIA_KEEP_MODELS` for a small job that fits alongside the model.
- generate.py reaches the gateway via FXLLA_HOST/FXLLA_PORT, added to the media
  env that cmd_media exports. Verified end to end against a live gateway:
  /admin/unload returns the freed aliases and /health then shows none resident.

## 2026-07-28: Local text to speech (voice)

Added `fxlla media voice` and the MCP `generate_speech` tool, completing the
media trio (image, video, speech). Reversed the earlier "not wireable" verdict.

- Earlier investigation concluded VOICE-1 had no callable text->speech path,
  because the best engine (Chatterbox via mlx-audio) was only importable inside
  a gitignored bot env. The unlock: mlx-audio is also installed in the VFX-1
  venv (the same one that hosts ltx-2-mlx). So voice follows the video pattern:
  a configurable interpreter path (`FXLLA_VOICE_PYTHON`) rather than a CLI.
- `media/voice_backend.py` runs under that interpreter (imports mlx_audio,
  loads Chatterbox, writes a 24 kHz mono WAV). fxlla's own python never imports
  mlx_audio; `generate.py` shells out to it. The backend is not exercised in CI
  (needs mlx-audio); the command-building and WAV validation are.
- Chatterbox ships no `conds.safetensors`, so a reference voice wav is
  mandatory; it sets the timbre. Discovered the `generate` signature by
  introspection (`text`, `lang_code`, `ref_audio`, `speed`, `exaggeration`,
  `cfg_weight`).
- Validated end to end through `fxlla media voice`: a real ~3 s 24 kHz WAV,
  peak ~73 percent full scale and ~69 percent voiced windows (a real utterance,
  not silence). Verified the audio signal, not just the exit code.
- `cmd_media` now exports the media env once (a single name list) so the direct
  call, the MCP server, and the opencode registration forward the same set,
  instead of repeating a growing var list three times.

## 2026-07-28: Signing and notarization hardening

Shared `app/sign-lib.sh` (identity default, `require_identity`, `verify_signed`)
used by `build.sh` and `package-dmg.sh`. New `package-dmg.sh --check` validates
the signing environment without building; the notarize path now validates the
staple and runs a Gatekeeper assessment. Both notarytool credential routes are
documented (app-specific password and App Store Connect API key); credentials
live only in the keychain.

Bug caught while testing: `codesign -d ... | grep -q '...runtime'` reported a
false negative on a correctly signed app. Under `set -o pipefail`, `grep -q`
exits on first match and SIGPIPEs `codesign`, so the pipeline returns non-zero
despite the match. Fixed by capturing the output to a variable and matching
with `case`. The same shape was latent in `require_identity`. General rule for
these scripts: do not pipe a long-running producer into `grep -q` under
pipefail; capture first.

Notarization needs the maintainer's Apple credentials (an active Developer
Program membership plus either an app-specific password or an App Store Connect
API key), stored once as a notarytool keychain profile.

Verified end to end: with the profile in place, `app/package-dmg.sh --notarize`
built, signed, submitted, and got `Accepted` from the notary service, then
stapled and validated. The resulting `.dmg` passes `stapler validate` and a
`spctl` Gatekeeper assessment offline (the ticket is embedded). Whole run ~34 s.
Credentials live only in the keychain and in the git-ignored `config/config.env`.

## 2026-07-28: Media generation (image and video)

Phase 4 delivered on the CLI and MCP. `fxlla media image|video` plus an MCP
server (`generate_image`, `generate_video`).

- Images: mflux-cv (community build, `mflux-cv 0.18.29`), one CLI per model
  family. The wrapper maps a friendly name (z-image-turbo default, boogu,
  flux2-klein, qwen, krea2, schnell, dev) to the right binary. The earlier VAE
  error that parked this work was a stale vanilla-mflux build, not a wrapper
  bug; with mflux-cv it resolves cleanly. Validated with a real 512x512 render.
- Video: ltx-2-mlx (LTX-2.3). Contract gotcha: `generate` requires exactly one
  stage flag (`--distilled` is the fast default) and a mandatory `--frame-rate`
  (trained at 24). The binary usually lives in a project venv, so the path is
  configurable via `FXLLA_VIDEO_BIN`. Validated with a real 25-frame 512x320
  clip in 27s; the MP4 even carries an AAC audio track.
- Both validate the produced file (PNG magic / MP4 ftyp box plus a size floor),
  because these tools can exit 0 while writing nothing useful ("verify the
  pixels, not the exit code").
- Module named `generate.py` (not `media.py`): a file sharing its parent
  directory's name shadows it as a namespace package on import, the same
  collision that forced the `<domain>_mcp.py` naming.
- Voice/audio (a separate local toolchain) is out of scope here and tracked as
  a follow-up.

## 2026-07-28: Config precedence, completions, and model availability

Three CLI items batched into one review-sized change (all touch command
dispatch and model enumeration, so they share a review).

Model availability (#11), the tool side of the availability-and-consent design:
`fxlla ls --json` and `fxlla avail <alias>` expose `{cached, known, engine,
repo, size}` (catalog size for models not yet pulled). `fxlla on` keeps its
fail-fast when a model is not cached and gains an opt-in `--pull`. The agent or
skill still owns the offer-and-consent flow; the CLI just gives it a
machine-readable signal. A partial download (a model dir without the `.source`
completion marker) is deliberately reported as not cached.

- Config precedence (#15): `config.env` used plain assignments, so sourcing it
  clobbered any `FXLLA_*` value exported in the shell, reversing the documented
  order (environment > config.env > defaults). Fix in `lib/core.sh`: snapshot
  the exported config vars (`export -p` filtered to `FXLLA_*`/`HF_TOKEN`, which
  also sidesteps re-applying readonly system vars), source the file, then
  re-apply the snapshot so the environment wins. A `tests/test_config.sh`
  harness drives `fxlla config` across the three tiers and is run in CI.
- Shell completions (#10): `fxlla completions <bash|zsh>` prints a script backed
  by a hidden `fxlla __complete <what>` helper, keeping candidate lists in the
  CLI (single source of truth). Gotcha: `mapfile` is bash 4+, and macOS ships
  bash 3.2, so the bash script uses the classic word-splitting `COMPREPLY=(...)`
  form (safe here since candidates never contain spaces). Verified against the
  real `bash 3.2.57`. Tested in CI (`tests/test_completions.sh`).

## 2026-07-28: Passive gateway metrics

Closed the passive side of the stats work flagged in the earlier entry. The
gateway now measures real proxied traffic instead of relying on a synthetic
probe. Design:

- A separate `gateway/metrics.py` holds the pure logic (SSE token counting,
  first-token timing, usage parsing, sample append) so it is unit-tested
  without sockets. The server feeds streamed chunks to a `StreamMetrics` and
  appends one sample per completed completion request.
- Token count approximates one token per streamed delta, matching the existing
  probe. When a server emits a trailing `usage` chunk
  (`stream_options.include_usage`), its exact `completion_tokens` takes over.
  Non-streamed responses read `usage.completion_tokens` directly.
- Recording is best-effort and fully wrapped: a metrics failure logs and is
  swallowed so it can never affect the proxied response.
- Samples reuse the CLI probe's schema (`ts, model, engine, ram_mb, ttft_ms,
  tps`) plus a `source` marker, so the menu bar app renders them unchanged.
  `serve` pins `FXLLA_STATS_FILE` to the CLI's path so both writers agree.
- `fxlla stats` now reads these passive samples when the gateway is up (and no
  single-model server is), rather than probing.

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
