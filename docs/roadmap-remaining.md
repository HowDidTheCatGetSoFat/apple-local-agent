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

### Media and gateway memory coordination - DONE

Delivered: media generation frees the gateway's resident models before a job
(`POST /admin/unload`); the gateway reloads on demand. Opt out with
`--keep-models` or `FXLLA_MEDIA_KEEP_MODELS`. Original note kept below.

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

### Media controls in the menu bar app - DONE

Delivered: a Media section (prompt, image/video/voice picker, Generate) that runs
`fxlla media` and reveals the output in Finder. Original note kept below.

- What: the app predates the media feature and exposes none of it. Add
  image/video/voice entry points (at minimum: trigger a generation, show
  progress, reveal the output file).
- Why: the app is the discoverable surface; media is invisible to anyone who
  does not read the CLI help.
- Effort: M. Depends on the Tier 1 coordination so an app-triggered video job
  does not race the gateway for memory.

### Media weights on a fresh machine - doctor check DONE

Delivered: `fxlla doctor` now has a media section that checks the image/video/
voice backends and the weight cache, so the gap is visible. Full bandwidth-
capped pull integration (the L part below) is still pending. Original note kept.

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

### CI for the Swift app - DONE

Delivered: `.github/workflows/app.yml` builds the app (unsigned) on a macOS
runner, path-filtered to `app/**` so the paid runner stays idle otherwise.
Original note kept below.

- What: CI covers shell, python, and CodeQL; the app is only built locally.
  Add a macOS runner job that runs `app/build.sh` (unsigned) on every PR.
- Why: an app build break is currently invisible until someone builds locally.
  Signing and notarization stay local (credentials live in the keychain), so
  the job only guards compilation.
- Effort: S.

### Reconcile ROADMAP.md checkboxes - DONE

Delivered: the shipped items (menu bar app, `fxlla doctor`, shell completions)
are now marked in ROADMAP.md, and the installer item is marked partial. Original
note kept.

- What: several shipped items are still marked `[ ]` in ROADMAP.md: the
  SwiftUI menu bar app (Phase 1), `fxlla doctor`, and shell completions
  (Cross-cutting). Flip them to `[x]` and re-check the rest of the list while
  at it.
- Why: the roadmap is the public statement of what exists; stale checkboxes
  make delivered work look pending.
- Effort: S. Do this first; it costs minutes and corrects the record.

### Unified MCP install and tool-usage skills - DONE

Delivered: `fxlla wire-opencode --all` registers the provider and all MCP servers
at once, and `fxlla skills install` installs a portable skill pack (`skills/`)
for Claude Code and opencode. Original note kept below.

- What: one command that registers the model provider and all MCP servers
  (rag, graph, media) at once, instead of the current per-tool
  `kb|graph|media wire-opencode`. Plus a shippable skills/prompt pack that
  teaches a model when and how to use them: retrieve from a knowledge base
  before answering, walk the code graph before an edit, offer media generation,
  and run the availability-and-consent download flow (`fxlla avail` -> present
  size and time -> confirm -> pull).
- Why: wiring a tool is not the same as using it. Today the MCP servers get
  registered but there is no guidance layer, so the tools sit idle unless the
  user asks for them by name. This is the orchestration half the main ROADMAP
  delegates to the client, but the pack has to exist and be installable.
- Effort: M. The unified command is S; authoring the skills and validating them
  against opencode and Claude Code is the bulk.
- Open choice: skill format - opencode skills/rules, Claude Code skills, or a
  portable prompt pack that `fxlla` installs into each client's config.

### Model weight sources: Civitai and Hugging Face keys, optional downloaders - DONE

Delivered: `FXLLA_CIVITAI_TOKEN` and `fxlla pull civitai:<id>` fetch LoRAs and
checkpoints from civitai.com (bandwidth-capped, into `<store>/civitai`); HF
downloads gated repos with `HF_TOKEN`; and `fxlla pull --downloader hf` runs the
Hugging Face CLI via `uvx` for xet/LFS repos. Original note kept below.

- What: configurable API keys for Civitai (`FXLLA_CIVITAI_TOKEN`) and Hugging
  Face (`HF_TOKEN`, already present), and an option to fetch weights with the
  providers' own downloaders (`hf download` / `huggingface_hub.snapshot_download`
  for HF; Civitai's download API) instead of the built-in aria2c tree walk.
- Why: the aria2c walk does not cover Civitai at all, and can miss HF edge cases
  (xet-backed repos, some LFS). Keys unlock gated and account-scoped downloads;
  Civitai hosts LoRAs and checkpoints useful for the image models. Keep aria2c
  the default for the bandwidth cap; the native downloaders are opt-in.
- Effort: M. Keys and a Civitai pull path are S-M; wiring HF's own downloader as
  an option is S.
- Note: keys live only in the git-ignored `config.env`, never in the repo, the
  same rule as `HF_TOKEN`.

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
- Persist the GPU wired limit: DONE - `fxlla ram persist` installs a
  LaunchDaemon that reapplies the limit at boot; `fxlla ram unpersist` removes it.

## Suggested order

The Tier 1 gap and most of Tier 2 are done (checkboxes, doctor media checks,
media in the app, Swift CI). What remains, in order:

1. Model weight sources (Civitai and HF keys, optional native downloaders) -
   unblocks image LoRAs/checkpoints and gated repos. (Unified MCP install and
   the tool-usage skills are done.)
3. CLI on PATH from the installer, then full media-weight pull integration.
4. Tier 3 backlog as demand appears; async media jobs first if video usage
   grows, since it builds on the memory coordination already in place.
