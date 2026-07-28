# Remaining work

The core goal is delivered: fxlla runs the best open-weights models locally on
Apple Silicon and plugs them into opencode and Claude Code, end to end. This
document tracks what is left: one real robustness gap, a handful of
completeness items, and a backlog of later-phase upgrades.

## Current state

- CLI with dual engine (MLX + llama.cpp/GGUF), bandwidth-capped resumable
  downloads, keep-warm, GPU RAM unlock, opencode integration.
- Multi-model gateway (`fxlla serve`): one OpenAI-compatible endpoint,
  on-demand load, LRU eviction under a RAM budget, streaming passthrough,
  passive per-request metrics (tok/s, TTFT) appended to `stats.jsonl`.
- Menu bar app (SwiftUI): live status, time-series charts, gateway start/stop,
  model list, GPU RAM toggle. Signed with a Developer ID and notarization
  verified end to end (stapled `.dmg` passes Gatekeeper offline).
- RAG (`fxlla kb`), code graph (`fxlla graph`), and media generation
  (`fxlla media image|video|voice`), each exposed as MCP tools with output
  validation.
- `fxlla stats`, `fxlla doctor`, `fxlla avail`, shell completions, config
  precedence fixed. All GitHub issues are closed.

Effort scale: S is under a day, M is a few days, L is a week or more.

## Tier 1: robustness

### Media and gateway memory coordination

- What: image, video, and speech generation share unified memory with the
  gateway's resident LLMs, and today nothing coordinates them. Running
  `fxlla media video` or `voice` while the gateway holds a large model
  (a 30B is ~17 GB) plus the media model can exceed the wired limit and
  OOM or crash the machine. The media path (CLI and MCP tools) should ask
  fxlla to evict resident models before a heavy job and let the gateway
  reload them on demand afterward.
- Why: this is the one path where normal use of two shipped features together
  can take the machine down. fxlla already owns the model lifecycle
  (load/evict), so the coordination belongs there and nowhere else.
- Effort: M.
- Acceptance: with the gateway holding a 30B resident, `fxlla media video`
  completes without exceeding the wired limit; the evicted model reloads on
  the next chat request; the MCP tools get the same behavior for free because
  they call the CLI. An escape hatch (`--no-evict` or an env var) for small
  image jobs that fit alongside.

## Tier 2: completeness and UX

### Media controls in the menu bar app

- What: the app predates the media feature and exposes none of it. Add
  image/video/voice entry points (at minimum: trigger a generation, show
  progress, reveal the output file).
- Why: the app is the discoverable surface; media is invisible to anyone who
  does not read the CLI help.
- Effort: M. Depends on the Tier 1 coordination so an app-triggered video job
  does not race the gateway for memory.

### Media weights on a fresh machine

- What: `fxlla pull` only manages LLM catalog models (`config/models.conf`).
  The media backends rely on external Hugging Face caches and venv binaries
  already being in place, so a fresh machine cannot run `fxlla media image`
  without manual setup. Either extend the bandwidth-capped pull to media
  weights (cached under `FXLLA_STORE` like text models), or at minimum teach
  `fxlla doctor` to check that the needed caches and binaries
  (`FXLLA_MEDIA_HF_HOME`, `FXLLA_VIDEO_BIN`, `FXLLA_VOICE_PYTHON`) exist.
- Why: the LLM path is reproducible from a clean clone; the media path is not.
- Effort: S for the doctor check, L for full pull integration. Ship the doctor
  check first; it makes the gap visible instead of a cryptic runtime failure.

### CLI on PATH from the installer

- What: the signed `.dmg` installs the app (drag to /Applications) but does
  not put the `fxlla` CLI on PATH. The app should offer to symlink it (or a
  first-run prompt should), the way Docker Desktop and VS Code do.
- Why: today the .dmg gives a working app whose underlying CLI is not
  reachable from a terminal, which undercuts "the CLI is the source of truth".
- Effort: S.
- Acceptance: after installing from the .dmg, a new terminal runs `fxlla`.

### CI for the Swift app

- What: CI covers shell, python, and CodeQL; the app is only built locally.
  Add a macOS runner job that runs `app/build.sh` (unsigned) on every PR.
- Why: an app build break is currently invisible until someone builds locally.
  Signing and notarization stay local (credentials live in the keychain), so
  the job only guards compilation.
- Effort: S.

### Reconcile ROADMAP.md checkboxes

- What: several shipped items are still marked `[ ]` in ROADMAP.md: the
  SwiftUI menu bar app (Phase 1), `fxlla doctor`, and shell completions
  (Cross-cutting). Flip them to `[x]` and re-check the rest of the list while
  at it.
- Why: the roadmap is the public statement of what exists; stale checkboxes
  make delivered work look pending.
- Effort: S. Do this first; it costs minutes and corrects the record.

## Tier 3: backlog

Already noted as later-phase in ROADMAP.md. Kept here for completeness.

- RAG vector index (M): replace brute-force cosine with `sqlite-vec` or
  LanceDB; optional MLX embeddings and a persistent warm embedding server.
  Only matters once knowledge bases grow past a few thousand chunks.
- Code graph upgrades (L): KuzuDB (embedded, Cypher) instead of flat SQLite;
  multi-language parsing via tree-sitter or SCIP (Python-only today);
  transitive change-impact queries.
- More media tools (M): `edit_image` and `upscale_image` (mflux-cv already
  ships the CLIs, so this is wrapper plus validation work); wire Wan 2.2
  (mlx-video) as a second video backend alongside LTX.
- Async media jobs (M): submit returns a job id, the caller polls for status
  and the output path. Keeps MCP tool calls fast and lets heavy video runs
  happen in the background. Pairs naturally with the Tier 1 coordination.
- Evals (L): measure quality and speed per model to choose with data instead
  of catalog notes.
- Persist the GPU wired limit (S): `fxlla ram auto` reverts on reboot; a
  LaunchDaemon can reapply it. Opt-in, since it changes a system-wide sysctl.

## Suggested order

1. Reconcile the ROADMAP.md checkboxes (minutes, corrects the public record).
2. Media/gateway memory coordination (the only crash-grade gap).
3. Media in the menu bar app, then the rest of Tier 2 in any order (the doctor
   check for media prerequisites is the cheapest and most useful next).
4. Tier 3 backlog as demand appears; async media jobs first if video usage
   grows, since it builds on the coordination work.
