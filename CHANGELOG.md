# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `fxlla setup --media` installs the media backends the way everything else
  gets installed: as uv tools at pinned versions (mflux-cv, mlx-audio from
  PyPI; ltx-2-mlx has no PyPI release, so its pin is a commit on the public
  repository). Determinism comes from pins, not from bundling bytes -
  relocatable Python venvs cannot be signed into the app. With the video
  binary on PATH and the voice interpreter resolved automatically (env
  override, then the uv tool venv, then python3 - doctor and setup ask
  generate.py's own resolution instead of re-implementing it), a fresh
  machine goes from clone to `fxlla media image` with two commands. The one
  manual piece is voice's reference wav: cloning a voice needs a voice.

- `FXLLA_MFLUX_BIN_DIR`: point the whole mflux family (image models, edit,
  upscale) at a custom directory. Image was the one media backend without a
  location knob - video and voice had theirs - because it is eight per-model
  CLIs, which a single-binary variable cannot cover; a directory covers them
  all, the specific `FXLLA_EDIT_BIN`/`FXLLA_UPSCALE_BIN` still win, and a set
  directory missing a binary errors naming both instead of silently falling
  back to PATH and running a different install than the one pointed at.
  `fxlla doctor` checks the directory when the knob is set.

### Fixed
- The media MCP registration was written with an EMPTY environment through
  `fxlla wire-opencode --all`, so every media tool call from an editor died
  with "FXLLA_STORE is not set". The wiring copied only exported variables,
  and config.env is read with `: "${VAR:=default}"`, which assigns without
  exporting - only `fxlla media wire-opencode` (which exports on its way
  through cmd_media) ever worked. Found by reading a real session where a
  model hit this four times and abandoned the MCP for about thirty shell
  commands.
- `--output` accepts a directory. Passing one ("put it in ~/Downloads") is
  the obvious thing to try and used to reach the toolchain as a filename,
  failing deep inside a backend; all five generators now name the file
  inside it. A trailing separator creates the directory.
- Image dimensions are no longer dropped in silence. `--aspect` together with
  `--width/--height` made aspect win without a word, so a request for 512x512
  produced a 1024x1024 file; the combination is now refused, naming both. The
  test suite had encoded the old behavior as intent and defended the bug.
- `fxlla media video` reports the MEASURED duration, frames and fps of what it
  produced, and the MCP returns them too. A model computed 49 frames at 24 fps
  as "about 10 seconds" (it is 2.04) and declared an unmet 4-8 second
  requirement satisfied. `--seconds` now asks for duration directly instead of
  making the caller do the multiplication.
- Voice through the MCP used a python without mlx-audio: `lib/core.sh`
  defaulted FXLLA_VOICE_PYTHON to `python3`, and the registration forwarded
  that default as if it were an explicit choice, beating the uv tool venv that
  actually has mlx-audio. The default now lives only in generate.py's
  resolution, where unset means unset.
- opencode's context meter now runs on each model's real window. The
  registration declared no per-model limit, so the meter and the
  auto-compaction trigger used a made-up number. The gateway now reports each
  model's context on /v1/models - the model's own config.json window for mlx,
  the served -c for gguf - and the registration writes it as the opencode
  limit, with output capped between 4k and 16k at a quarter of the window.
- Selecting the local provider in opencode no longer stops at an API key
  prompt. The local servers validate no key, but opencode asks for one when
  the provider omits it; the registration now carries a dummy `apiKey`
  (kept only if the user has not set their own).
- Embedding models no longer appear as chat models. The gateway's model list
  - which also feeds the opencode registration on `fxlla serve` - excludes
  catalog entries with role `embed`, matched by alias and by source repo, so
  `embed` cannot end up selectable in an editor again (it shipped that way
  once) and a raw API caller cannot make the gateway spawn a chat server on
  a BERT. The opencode registration now consumes the gateway's own filtered
  `/v1/models` instead of re-enumerating the store, so there is exactly one
  place that decides what is chat-servable. With a moved or missing catalog
  the filter excludes nothing: hiding a stranger's models would be worse
  than listing an extra one.

## [0.2.0] - 2026-07-31

Everything on the original roadmap is now delivered or explicitly declined
with a measurement. Highlights: the multi-model gateway with passive metrics,
RAG with a selectable embedding model and its own retrieval eval, the KuzuDB
code graph across nine languages, local media generation with async jobs and
content validation, consent gates on every download path, a signed and
notarized menu bar app that bundles the CLI, and `fxlla eval`, which scores
chat models on mechanically checked quality and measured speed.

### Added
- `fxlla eval`: score chat models on quality and speed locally, so choosing a
  model rests on data instead of the catalog's prose. Quality is 30 authored
  tasks across four dimensions - code executed against asserts in a sandbox,
  structured tool calls (with a separate column for calls that only appear in
  the text channel, which an agent stack never sees), instruction following,
  and long-context recall at 8k/16k - every check mechanical, no model judging
  another model. Speed is cold start, first request (the engines split the
  cold cost differently: llama-server loads before /health flips, mlx_lm loads
  on the first request, and the table shows both so neither lies), TTFT,
  decode and prefill tok/s, peak RSS, and tokens spent. Each model runs alone
  on a cold dedicated server; a busy eval port is refused rather than adopted.
  Every run prints a fingerprint of the rendered task set and a harness
  version: two runs are comparable exactly when both match. Results carry
  server build, weights identity, and machine identity, and go only to the
  state dir, which the harness never reads back. Measured on the first sweep
  (M5 Max, fingerprint f6ae21eaee7d, harness v3): redteam-4bit 10/10 code and
  8/8 tools at 93.5 tok/s and 42 GB resident; qwen3-coder 8/10 and 8/8 at
  131.2 tok/s and 16 GB; coder-1.5b 4/10 at 399.9 tok/s with its tool calls
  stranded in the text channel; tiny 1/10 code, which is the discrimination
  the task set was gated on. Dogfooding on those models fixed code extraction
  twice before shipping - real replies end in fenced example output (not
  Python), or in fenced usage examples (which compile), or quote compiling
  fragments mid-explanation, and each style silently lowered a correct score -
  so extraction concatenates the compiling fences that bind names, and those
  semantic changes are why the harness says v3.
- The embedding model behind `fxlla kb` is selectable. The catalog gained four
  more `embed` entries next to nomic-embed-text v1.5: bge-small (384-dim),
  bge-large and qwen3-embedding (1024-dim), and embeddinggemma (768-dim).
  `FXLLA_EMBED_MODEL` picks one by alias; unset keeps the previous model, so
  existing knowledge bases are untouched. Dimensions in the catalog notes are
  read from the GGUF headers, not from documentation.
- `fxlla kb reindex <name>` re-embeds a knowledge base with the currently
  selected model, which is what makes switching models possible: vectors from
  two models are not comparable, and the width guard previously refused the
  search without offering a way forward. It re-embeds the chunk text already in
  the store rather than re-reading sources that may have changed, in a single
  transaction, so an interrupted run leaves the base exactly as it was.
- `fxlla kb eval` scores retrieval instead of guessing at it: a golden set of 19
  questions over this repository's own documentation, reporting recall@1,
  recall@k, MRR, median query latency and a fingerprint of the corpus. On
  fingerprint 0c32ee03a384, nomic-embed-text scores recall@1 68%, recall@5 100%,
  MRR 0.809 at 8 ms per query, against bge-small at 74%, 84%, MRR 0.789 and
  5 ms - a 37 MB model ahead on the first hit and well behind by the fifth. Read
  those gaps with care: 19 queries means one query is worth 5.3 points, so the
  harness separates models that differ clearly and cannot rank two close ones.
  Since the corpus is live documentation, every edit moves every score;
  two runs are comparable only when their fingerprints match, which is why one
  is printed. `CHANGELOG.md` and `docs/JOURNAL.md` are excluded from the corpus
  for the same reason: they are where results get written down, and including
  them made each recorded number stale the moment it was recorded.

### Fixed
- `fxlla kb` no longer embeds against whatever server happens to hold the port.
  Reuse adopted any process answering /health without asking what it had loaded:
  demonstrated with zero embedding models installed, where indexing and search
  both still succeeded against a borrowed server. That was harmless while one
  model was possible and is not now, since nomic and embeddinggemma are both
  768-dim and qwen3-embedding and bge-large both 1024, so the width guard cannot
  see the difference and the store is silently poisoned. It now asks the server
  which model it loaded and refuses on a mismatch, naming both and pointing at
  `fxlla kb stop`. A server that will not identify itself is still allowed,
  since pointing `FXLLA_EMBED_PORT` at your own is legitimate.
- The embedding server is no longer started with `--pooling mean`. Every
  embedding GGUF declares its own pooling type and llama.cpp honours it: nomic
  wants mean, bge wants CLS, qwen3-embedding wants last-token. Forcing mean was
  correct only for the model that happened to be the default and silently
  degraded the rest, since a badly pooled vector is still a vector. Verified
  before removing the flag: omitting it reproduces `--pooling mean` bit for bit
  on nomic, while `--pooling cls` visibly differs. The README instructions for
  running a server by hand carried the same flag and were corrected.
- A gateway model switch no longer rounds up to the next whole second, and a
  backend that dies on start fails immediately instead of holding the 180s
  timeout. The wait loop polled once a second and never looked at the process it
  was waiting for. Measured: a switch to a small model went from 1.28s to about
  1.01s (four runs each), and a backend that had already exited went from burning
  the entire budget to 0.01s.
- `fxlla kb search` was five times slower than it needed to be. The embedding
  server is ready in about 0.17s, but the health check polled once a second, so
  the first probe missed it and every query slept a full second waiting for a
  server that was already up. Polling is now 20 ms and a cold query costs about
  0.2s instead of 1.1s. Reusing an already-running server, and leaving it alone
  afterwards, already worked before this change - it just also spawned a doomed
  process that could not bind the port; that is now skipped, and a warm query
  still costs about 0.09s.
- Concurrent `fxlla kb` commands no longer fail. Starting the server is now
  serialized with a lock file, so ten simultaneous searches start one server
  instead of ten and none of them fail: previously up to four in ten died with a
  raw traceback after losing the race to bind the port. A search whose borrowed
  server is stopped mid-request retries once instead of aborting, which matters
  most to `kb add`, where it used to leave a partial index behind.
- `fxlla kb` refuses to mix embedding widths. A server for a different model
  returns a different number of dimensions, and the cosine comparison silently
  truncated to the shorter vector: searches returned meaningless scores and an add
  wrote unrankable rows into the base for good. Both now stop with a message
  naming the two widths, before anything is written.
- Errors from the embedding port are messages rather than tracebacks. A chat
  server on `FXLLA_EMBED_PORT` produced a two-kilobyte Python traceback, which the
  MCP layer forwarded verbatim as a tool result.
- New `fxlla kb stop` stops the embedding server, since a reused one outlives the
  command that started it and previously had to be found by pid.
- `fxlla on <alias>` registered the wrong model in opencode. It read the id from
  the first entry of `/v1/models`, and `mlx_lm` now enumerates the whole Hugging
  Face cache there, so whichever unrelated model sorted first was registered as
  the local chat model. On a machine with diffusion weights cached that meant
  opencode's `local` provider pointed at an image model. The id is now derived
  from what was actually launched, per engine.
- `fxlla skills install` no longer leaves stale skills behind. It never removed
  anything, so a renamed or deleted skill kept living in `~/.claude/skills` (and
  its path stayed in opencode's `instructions`) telling the model to reach for a
  tool that had changed. Install now prunes skills it previously installed that
  the repo no longer ships, identified by a marker file so directories it did not
  create - other tools' skills, and your own `instructions` entries - are left
  untouched. It also validates each skill's frontmatter first and aborts the whole
  run if one is malformed, rather than half-installing a pack a client will
  silently ignore. New `fxlla skills status` reports each installed copy as
  current, drifted, or missing, and `fxlla doctor` summarises it.
- CodeQL analyzes the Python sources again. It was narrowed to workflow files
  early on, when the repo had no Python and CodeQL failed the run for a language
  with no source; there are now ~6,700 lines across `gateway`, `rag`, `graph`,
  and `media` that were never scanned, and the green check only covered the
  workflows. It now runs a matrix of `actions` plus `python`, on pull requests
  and on pushes to `main` (so the default branch has a baseline).
- CI stopped warning on every run: `actions/checkout` and `astral-sh/setup-uv`
  were on majors that target the deprecated Node 20, and `setup-uv` cached
  against `uv.lock`/`requirements*.txt`, neither of which exists here (this repo
  gets dependencies from `uv run --with`), so the cache never invalidated.
- `ludeeus/action-shellcheck` is pinned to a release SHA instead of tracking
  that repository's `master`, and `github/codeql-action` moved to v4 (v3 warned
  on every run that it is deprecated in December 2026). Every workflow now runs
  warning-free.

### Changed
- Code graph now uses an embedded KuzuDB graph (Cypher) instead of a flat SQLite
  store. `fxlla graph impact` is a Cypher variable-length path over a derived
  CALLS relationship, replacing the Python breadth-first walk. `fxlla graph`
  runs the backend under `uv run --with kuzu` (uv is already required);
  `FXLLA_GRAPH_PYTHON` overrides the interpreter. The `ast` extraction, the CLI,
  and the MCP tools are unchanged. Existing graphs are re-created at the new
  `<store>/graph/graph.kuzu` location on the next `fxlla graph index`.

### Added
- Large downloads need consent. Any transfer over `FXLLA_CONFIRM_ABOVE_GB` (5 GB
  by default) prompts at a terminal and refuses everywhere else, naming the size
  and how to proceed. This covers all four routes that move bytes: catalog models
  (both downloaders), `media:<alias>` weights, Civitai files, and the weights a
  render fetches on first use. That last one was the widest hole: a first
  `fxlla media image` pulled tens of gigabytes mid-render with nothing asked, and
  because a background job and an MCP tool call both re-invoke `media/generate.py`
  the check lives there rather than in the shell wrapper. `--yes` and
  `FXLLA_ASSUME_YES=1` authorize; the menu bar app passes `--yes` because its own
  dialog already showed the size. A size that cannot be read is treated as large,
  a resumed pull asks about the remainder, and a refusal leaves no directories or
  marker files behind.
- Media output is checked for content, not only for a valid container: a header
  check passes for eight seconds of silence, which is a real failure mode of these
  toolchains. `media/quality.py` adds stdlib-only checks (silence, a constant
  waveform, near-zero signal, a large DC offset, a mostly-silent clip; PNG
  dimensions; a usable video stream via `ffprobe` when installed) wired into the
  existing validators. Only unambiguous garbage is flagged - frame count and clip
  length are caller-controlled, so short clips are accepted, and a file the checks
  cannot parse gets no verdict rather than a rejection. `--skip-quality` or
  `FXLLA_MEDIA_SKIP_QUALITY=1` accepts a flagged file, and `fxlla doctor` reports
  whether `ffprobe` is available.
- Media weight catalog and pre-fetch: `config/media.conf` maps each media alias to
  its Hugging Face repositories with sizes, `fxlla media weights` shows what is
  cached, and `fxlla pull media:<alias>` fetches ahead of a render into the media
  HF cache (where the toolchains look them up by repo id, so the bandwidth cap
  does not apply). `fxlla doctor` reports how many entries are ready. Video
  correctly pulls both the LTX model and the Gemma text encoder its pipelines load.
- The app bundles the CLI and can install it on PATH: `app/build.sh` copies the
  CLI tree into `fxlla.app/Contents/Resources/cli` (never `config/config.env`,
  which holds tokens), so a `.dmg` install is self-contained. A new **Install the
  fxlla command** action in the panel symlinks it into `~/.local/bin` - an
  explicit user action, idempotent, and it refuses to replace a file or a link it
  did not create (for example a git checkout you develop against).
- Background media jobs: `--async` on any `fxlla media` generator returns a job
  id immediately instead of blocking, with `fxlla media jobs`, `job <id>`,
  `cancel <id>`, and `jobs --prune` to follow and clean up. Jobs are serialized
  (one render at a time) because they share unified memory with the gateway's
  models. Over MCP, generators take `async: true` and the new `media_job_status`,
  `list_media_jobs`, and `cancel_media_job` tools poll and manage them.
- Multi-language code graph: `fxlla graph` now indexes JavaScript, TypeScript,
  Go, Rust, Java, C/C++, and Ruby via tree-sitter in addition to Python (still
  `ast`). All languages share the KuzuDB graph, so `def`/`callers`/`impact`
  resolve by name across them. Runs under `uv run --with kuzu==0.11.3 --with
  tree-sitter --with tree-sitter-language-pack`.
- Optional sqlite-vec KNN index for `fxlla kb search`: set `FXLLA_KB_INDEX=1` to
  replace the brute-force cosine scan with a vector index rebuilt on demand from
  the chunks table. It runs `rag/core.py` under `uv run --with sqlite-vec`
  (no persistent install) and falls back to the scan when the extension is
  unavailable, so a clean clone still works. The `fxlla kb` CLI is unchanged. The
  RAG MCP server and its `wire-opencode` registration run under the same
  interpreter, so `rag_search` uses the index too.
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
