# fxlla

Run the best open-weights models locally on Apple Silicon, and plug them into
the tools you already use.

`fxlla` runs models with MLX or llama.cpp, caches the weights on an external
disk, downloads them with a bandwidth cap, and serves them over an
OpenAI-compatible endpoint that integrates with opencode. Claude Code keeps its
own Claude models untouched.

Built for M-series Macs with large unified memory. Validated on M5 Max
(128 GB).

## Why

- One command to start or stop a local model, on the right engine, with the
  weights on the disk you choose.
- Uses the full unified memory: `fxlla ram` lifts the macOS GPU memory cap so
  large models fit.
- Bandwidth-capped, resumable downloads so pulling a 60 GB model does not
  saturate your connection.
- Frees RAM on its own after an idle timeout.
- Adds local models to opencode without touching Claude Code.
- Ships local tools any client can call over MCP: RAG knowledge bases, a
  multi-language code graph, and image, video, and speech generation.

## Requirements

- Apple Silicon Mac (M-series).
- Homebrew and `uv` (`brew install uv`); `aria2` is installed by `fxlla setup`
  if missing.
- An external disk or a folder with room for the weights.

## Install

```sh
git clone https://github.com/HowDidTheCatGetSoFat/fxlla.git
cd fxlla
mkdir -p ~/.local/bin && ln -sf "$PWD/bin/fxlla" ~/.local/bin/fxlla
mkdir -p ~/.config/fxlla && cp config/config.env.example ~/.config/fxlla/config.env
$EDITOR ~/.config/fxlla/config.env   # optional: FXLLA_STORE, if not ~/.local/share/fxlla/store
fxlla setup
```

`fxlla setup` installs `mlx-lm` and `llama.cpp` and verifies the environment.
`fxlla setup --media` also installs the media backends (image, video, voice)
at pinned versions - determinism comes from pins, not from bundling bytes,
since relocatable Python venvs cannot be signed into the app. Weights stay
separate and consent-gated. The one manual piece is voice's reference wav
(`FXLLA_VOICE_REF`): cloning a voice needs a voice.

## Quickstart

```sh
fxlla models                # browse the catalog
fxlla pull tiny             # small model to validate the pipeline (~0.3 GB)
fxlla on tiny               # start and register it in opencode
fxlla status                # running model and idle time
fxlla off                   # stop

fxlla pull qwen3-coder      # the daily driver (MLX, ~17 GB)
```

Then open opencode and pick the `local` provider.

## Commands

| Command                       | What it does                              |
|-------------------------------|-------------------------------------------|
| `fxlla setup`                 | Install and verify dependencies           |
| `fxlla models`                | List the catalog                          |
| `fxlla pull <model>`          | Download a model (bandwidth-capped, resumable) |
| `fxlla ls [--json]`           | List downloaded models                    |
| `fxlla avail <alias>`         | Availability as JSON (cached, size, engine) |
| `fxlla on [model] [--pull]`   | Start a single model, register in opencode |
| `fxlla off`                   | Stop the single-model server              |
| `fxlla serve` / `unserve`     | Multi-model gateway: one endpoint, load on demand |
| `fxlla status`                | Server, model, and idle status            |
| `fxlla stats [--watch]`       | Live tok/s, TTFT, RAM (also --json, --last N) |
| `fxlla ram [auto\|reset\|persist\|unpersist\|<MB>]` | Adjust the GPU memory limit |
| `fxlla eval [model ...]`      | Score chat models on quality and speed    |
| `fxlla kb ...`                | Local RAG knowledge bases (MCP: rag_search) |
| `fxlla graph ...`             | Multi-language code graph (MCP: find_definition, ...) |
| `fxlla media image\|video\|voice` | Local media generation (MCP: generate_*) |
| `fxlla logs`                  | Follow the server log                     |
| `fxlla skills install`        | Install the tool-usage skill pack         |
| `fxlla doctor`                | Diagnose the environment                  |
| `fxlla completions <bash\|zsh>` | Print a shell completion script         |
| `fxlla wire-opencode`         | Register the local provider in opencode   |
| `fxlla rm <model>`            | Delete a downloaded model                 |
| `fxlla config`                | Show the effective configuration          |

## Keys and model cache

Both are set in `~/.config/fxlla/config.env`, which is never committed:

- `FXLLA_STORE` is where models are cached. Point it at any mounted disk or a
  local folder.
- `HF_TOKEN` is only needed for gated Hugging Face repositories. Leave it unset
  otherwise.
- `FXLLA_CIVITAI_TOKEN` lets `fxlla pull civitai:<id>` fetch LoRAs and
  checkpoints from civitai.com (into `<store>/civitai`):

  ```sh
  fxlla pull civitai:12345                 # a Civitai model-version id
  fxlla pull https://civitai.com/models/999?modelVersionId=12345
  ```

Never put tokens anywhere else in the tree. `config/config.env` is git-ignored.
An exported environment variable always wins over `config.env`, which in turn
wins over the built-in defaults.

## Shell completions

Completion for commands, catalog aliases (`pull`), downloaded models
(`on`/`off`/`rm`/`eval`), and `kb`/`graph`/`media`/`skills` subcommands:

```sh
# bash: add to ~/.bashrc
source <(fxlla completions bash)

# zsh: add to ~/.zshrc (after compinit)
source <(fxlla completions zsh)
```

## Model availability

Check what is cached before starting anything. `fxlla ls --json` lists cached
models and `fxlla avail <alias>` reports availability for any model:

```sh
fxlla avail qwen3-coder
# {"alias": "qwen3-coder", "cached": false, "known": true, "engine": "mlx",
#  "repo": "...", "size_mb": null, "catalog_size": "17GB"}
# size_mb is the real disk size when cached, else null; catalog_size is the
# human-readable download estimate for a model that is not cached yet.
```

`fxlla on <alias>` fails fast when the model is not cached; pass `--pull` to
download first.

Any transfer over `FXLLA_CONFIRM_ABOVE_GB` (5 GB by default) needs consent. At a
terminal you get a prompt with the size. Everywhere else (a script, an agent, an
MCP call, or the weights a render would fetch on first use) it refuses and says to
re-run with `--yes`, so tens of gigabytes never start unattended. `--yes` or
`FXLLA_ASSUME_YES=1` authorizes, and a size the CLI cannot read is treated as
large rather than waved through. Resuming an interrupted pull asks about the
remainder, not the original size.

## MLX vs GGUF

MLX is the default and the fastest path on this hardware; the `mlx-community`
repositories already ship quantized for MLX. GGUF (llama.cpp) is for models
with no MLX build or when you want a specific imatrix quant:

```sh
fxlla pull coder32-gguf --quant Q4_K_M
```

If a GGUF repository has several quants and you omit `--quant`, `fxlla` lists
them. GGUF cannot be converted to MLX; for Hugging Face safetensors to MLX use
`mlx_lm.convert -q`.

Pulls use `aria2c` with a bandwidth cap by default. For a xet-backed or awkward
repo, `fxlla pull <repo> --downloader hf` fetches it with the Hugging Face CLI
(run via `uvx`, no extra install); it is more robust but ignores the cap.

## Choosing between models: fxlla eval

Rather than trusting the catalog's prose, measure. `fxlla eval` runs every
cached dev/agentic/max model - one at a time, each on a cold dedicated
server - through 30 mechanically checked tasks (code executed against asserts
in a sandbox, structured tool calls, instruction following, long-context
recall) and reports them next to cold cost, TTFT, decode and prefill speed,
peak RSS, and tokens spent:

```sh
fxlla eval                  # every cached dev/agentic/max model
fxlla eval qwen3-coder      # just one (named models skip the RAM guard)
fxlla eval --quick          # pipeline check in ~2 minutes
```

No model judges another model's prose: every check is execution, parsing, or
string mechanics, so scores cannot depend on a judge. Each run prints a task
fingerprint and a harness version; two runs are comparable exactly when both
match, and scores are per-machine. The report states its own noise floor. The
details - what is measured, the sandbox, why the text-channel tool-call column
exists - live in `evals/README.md`.

## Using the full 128 GB

macOS reserves about 75 percent of RAM for the GPU. For large models raise it:

```sh
fxlla ram          # show current and recommended limit
fxlla ram auto     # raise it (asks for sudo, keeps a reserve for the OS)
fxlla ram reset    # back to default
```

`fxlla ram auto` reverts on reboot. To make it stick, `fxlla ram persist`
installs a small LaunchDaemon that re-applies the limit at every boot;
`fxlla ram unpersist` removes it.

## Keep-warm

`FXLLA_KEEP_WARM` (minutes, default 10) stops the server after that idle time
to free RAM. Set `0` to disable. `fxlla status` shows the idle timer.

## Multi-model gateway

`fxlla on` serves one model. `fxlla serve` starts a gateway: a single
OpenAI-compatible endpoint that fronts every downloaded chat model.

```sh
fxlla serve                 # start the gateway on 127.0.0.1:8080
fxlla status                # gateway, resident models, RAM budget
fxlla unserve               # stop it (unloads all backends)
```

`GET /v1/models` lists every downloaded chat model (embedding models are
excluded; they cannot chat and belong to `fxlla kb`). A request picks one by
name:

```sh
curl -s localhost:8080/v1/chat/completions -d '{"model":"qwen3-coder", ...}'
```

The gateway loads the requested model on demand, keeps several small models
resident for instant switching, and evicts the least-recently-used one when a
load would exceed the RAM budget (`FXLLA_GATEWAY_BUDGET_MB`, derived from the
GPU limit by default). In opencode you pick any local model from the `local`
provider and the gateway handles loading.

While it serves, the gateway measures real traffic: it derives tokens/s and
time to first token from the streams it proxies and appends them to the stats
time-series, so `fxlla stats` and the menu bar charts reflect actual usage
rather than a synthetic probe. See `gateway/README.md`.

## Menu bar app

A native SwiftUI menu bar app lives in `app/`: live status, time-series charts
(tokens/s, TTFT, RAM in GB), gateway start/stop, model list with on-demand
load, and a GPU RAM toggle. It is a thin front end over the CLI. See
`app/README.md`.

```sh
app/build.sh && open app/fxlla.app          # unsigned local build
app/package-dmg.sh --check                   # verify signing prerequisites
app/package-dmg.sh --notarize <profile>      # signed, notarized, stapled .dmg
```

Distribution builds are signed with a Developer ID and notarized. Create the
`notarytool` keychain profile once (with an app-specific password or an App
Store Connect API key); `app/package-dmg.sh` reads it from the keychain and
never from the repo. See `app/package-dmg.sh` for the exact commands.

The app ships the CLI inside its bundle, so installing the `.dmg` alone gives a
working `fxlla`. To use it from a terminal, click **Install the fxlla command**
in the panel: it symlinks the bundled CLI into `~/.local/bin` (no admin prompt)
and tells you where. That is a deliberate user action, not something the
installer does silently, and it never replaces a file or a link it did not
create - if you already run `fxlla` from a git checkout, it says so and leaves
your setup alone.

## Knowledge bases (RAG)

Index your files into a local knowledge base and search them semantically. It
uses a small local embedding model (via llama.cpp) and a SQLite store; nothing
leaves the machine.

```sh
fxlla pull embed --quant Q5_K_M     # one-time: the embedding model (~100 MB)
fxlla kb add docs ./docs README.md  # index files or directories
fxlla kb search docs "how does eviction work"
fxlla kb ls
```

Expose it to your tools as an MCP server so any model can call `rag_search`:

```sh
fxlla kb wire-opencode              # register the RAG MCP in opencode
```

### Choosing an embedding model

`fxlla models` lists several under the `embed` role, from 384 to 1024
dimensions. `FXLLA_EMBED_MODEL` picks one by alias; unset means `embed`
(nomic-embed-text v1.5), which is what every base built so far was indexed with.

```sh
fxlla pull embed-qwen3 --quant Q8_0        # multilingual, 1024-dim, 32k context
export FXLLA_EMBED_MODEL=embed-qwen3
fxlla kb reindex docs                      # re-embed: widths must match
```

Vectors from two models are not comparable, so a base built with one model has
to be re-embedded before another can search it. `fxlla kb reindex` does that from
the chunk text already in the store, without re-reading the sources, in a single
transaction: an interrupted run changes nothing.

Rather than guess which model is better, measure. `fxlla kb eval` scores
retrieval over a golden set of questions against this repository's own
documentation and reports recall@1, recall@k, MRR and query latency:

```sh
fxlla kb eval                              # the model currently configured
FXLLA_EMBED_MODEL=embed-small fxlla kb eval
```

The corpus is live documentation, so editing it moves every score. Each run
prints a fingerprint of the corpus it used, and two runs are comparable exactly
when those match. `CHANGELOG.md` and `docs/JOURNAL.md` are left out of it on
purpose: they are where results get written down, so including them made every
recorded number stale the moment it was recorded.

### Reusing a running server

A search spawns the embedding server, uses it, and stops it. That costs about
0.2s. Leave a server running on `FXLLA_EMBED_PORT` and `fxlla kb` reuses it
instead, which brings a query to roughly 0.09s and is worth doing if an agent
retrieves often:

```sh
llama-server --embeddings -m <embed model>.gguf --host 127.0.0.1 --port 8090 &
```

Do not pass `--pooling`: every embedding GGUF declares its own pooling type and
llama.cpp honours it. Forcing `mean` is right for nomic and wrong for bge (CLS)
and qwen3-embedding (last token), and a badly pooled vector is still a vector,
so nothing later would report it.

fxlla never stops a server it did not start, so use `fxlla kb stop` when you want
it gone. The server has to hold the model the base was built with. `fxlla kb`
asks it which one it loaded and refuses on a mismatch, because two models can
share a width: nomic and embeddinggemma are both 768-dim, qwen3-embedding and
bge-large both 1024, and at equal width nothing further down can tell them
apart.

Search uses a brute-force cosine scan by default, which is fine up to a few
thousand chunks. For larger knowledge bases, opt into a vector index:

```sh
export FXLLA_KB_INDEX=1             # sqlite-vec KNN index, built on demand
fxlla kb search docs "how does eviction work"
```

With the index on, `fxlla kb` runs under `uv run --with sqlite-vec` (no
persistent install); it falls back to the scan automatically when the extension
is unavailable, and the results are identical either way. The MCP server picks up
the same setting, so `rag_search` uses the index too; re-run
`fxlla kb wire-opencode` after changing `FXLLA_KB_INDEX` to refresh the
registration.

## Code graph

Index a codebase and navigate it structurally: where a symbol is defined, where
it is referenced, which functions call it, and the transitive blast radius of a
change. Python is parsed with the standard library `ast`; JavaScript, TypeScript,
Go, Rust, Java, C/C++, and Ruby with tree-sitter. Symbols are stored in an
embedded KuzuDB graph, so change-impact is a single Cypher variable-length path,
and `def`/`callers`/`impact` resolve by name across languages. `fxlla graph` runs
the backend under `uv run --with kuzu==0.11.3 --with tree-sitter --with
tree-sitter-language-pack` (uv is already required); set `FXLLA_GRAPH_PYTHON` to
use your own interpreter that has those packages.

KuzuDB is pinned to `0.11.3`: its upstream was archived after the October 2025
Apple acquisition, and 0.11.3 is the final release. It installs and runs fine;
the pin keeps it reproducible. Override `FXLLA_GRAPH_PYTHON` if a maintained fork
or successor is adopted later.

```sh
fxlla graph index .                 # index a directory (Python, JS/TS, Go, ...)
fxlla graph def _db                 # where a symbol is defined
fxlla graph callers _db             # which functions call it
fxlla graph refs chunk_text         # where it is referenced
fxlla graph impact chunk_text       # transitive callers (blast radius)
```

Expose it to your tools as an MCP server (`find_definition`, `find_references`,
`find_callers`, `find_impact`, `list_unused`):

```sh
fxlla graph wire-opencode
```

## Media generation

Generate images, short videos, and speech locally. Images use the mflux-cv
toolchain, video uses `ltx-2-mlx` (LTX-2.3), speech uses mlx-audio (Chatterbox):

```sh
fxlla media models                                  # supported image models
fxlla media image "a red sailboat at sunset" --seed 42
fxlla media video "a sailboat at sunset, gentle waves" \
    --frames 49 --width 768 --height 512 --frame-rate 24
fxlla media voice "Hello from a local voice." --ref reference-voice.wav
fxlla media edit "make it snow" --image photo.png     # instruction-based edit
fxlla media upscale --image photo.png --scale 2x      # diffusion super-resolution
```

Generated audio is checked for content, not just for a valid header, because a
TTS run that emits silence writes a perfectly well-formed WAV. The checks flag
silence, a constant waveform, a large DC offset, and a clip that is silent for
almost its whole length; thresholds sit far below real speech (which measures a
peak near 0.95 against a 0.005 limit), so a render you wanted is not rejected.
Images are checked only for sane dimensions - a blank but well-formed image is not
detected. Video always gets container checks (present, plausibly sized, a real
MP4); its stream is inspected only when `ffprobe` is installed. Frame count and
length are yours to choose, so a one-frame
or fraction-of-a-second clip is accepted. If a check ever rejects something you
wanted, pass `--skip-quality` or set `FXLLA_MEDIA_SKIP_QUALITY=1`.

Point `FXLLA_MFLUX_BIN_DIR` at the directory holding the `mflux-*` image CLIs
when they live in a venv off PATH (unset means PATH, where `fxlla setup
--media` puts them). Point `FXLLA_VIDEO_BIN` at your `ltx-2-mlx` binary and
`FXLLA_VOICE_PYTHON` at an
interpreter with `mlx-audio` (both usually live in a project venv), and
`FXLLA_MEDIA_HF_HOME` at the weight cache. `fxlla doctor` reports which of these
are ready. Before a job, media generation asks a running gateway to free its
resident models so a heavy render does not exceed the GPU limit (opt out with
`--keep-models`). Expose them as MCP tools (`generate_image`, `generate_video`,
`generate_speech`, `edit_image`, `upscale_image`):

```sh
fxlla media wire-opencode
```

### Media weights

The toolchains download their weights from Hugging Face on first use, which can
be tens of gigabytes in the middle of a render. `fxlla media weights` shows the
catalog (`config/media.conf`) with sizes and what is already cached, and you can
fetch ahead of time:

```sh
fxlla media weights                 # alias, kind, size, cached or missing
fxlla pull media:z-image-turbo      # pre-fetch one (about 33 GB)
fxlla pull media:ltx                # video: LTX-2.3 plus its Gemma text encoder
```

Media weights go into the Hugging Face cache (`FXLLA_MEDIA_HF_HOME`, or the HF
default when unset) rather than `FXLLA_STORE`, because the toolchains resolve
them by repository id - the cache layout is what they look for. That also means
the Hugging Face CLI does the transfer, so **the bandwidth cap does not apply to
media pulls**. `fxlla doctor` reports how many catalog entries are ready.

Pre-fetching is optional. Skipping it is fine: the toolchain then downloads only
the files it actually needs on first render, which for some repositories is much
less than the whole thing.

### Background jobs

Video can run for minutes. Add `--async` to any generator to get a job id back
immediately and poll it instead of blocking:

```sh
fxlla media video "a sailboat at sunset" --async   # prints a job id
fxlla media jobs                                  # all jobs, newest first
fxlla media job <id>                              # one job (add --json)
fxlla media cancel <id>                           # stop a queued or running job
fxlla media jobs --prune                          # drop finished records
```

Jobs run **one at a time**: renders share unified memory with the gateway's
models, so a second submission waits (`queued`) until the current one finishes.
State lives next to the outputs, in `<media out>/jobs`. Over MCP, pass
`async: true` to any generator and poll with `media_job_status`, plus
`list_media_jobs` and `cancel_media_job`.

## Tools and skills

Wire every local tool into your client at once, and install the skills that tell
a model when to use them:

```sh
fxlla wire-opencode --all     # provider + rag, graph, and media MCP servers + skills
fxlla skills install          # the skill pack for opencode and Claude Code
fxlla skills status           # is each installed copy current, drifted, or missing
```

The skill pack (`skills/`) is guidance, not code: retrieve from a knowledge base
before answering, walk the code graph before editing, offer media generation,
and check availability and consent before a download. Registering a tool makes
it callable; the skills make a model reach for it at the right time. Install is
idempotent; restart the client to load them.

Install validates each skill's frontmatter first and refuses the whole run if one
is malformed, since a client silently ignores a broken skill and the only symptom
is a model that never reaches for the tool. It also removes skills it previously
installed that the repo no longer ships, so a renamed one stops advertising a tool
that changed - and it only ever touches directories it created, leaving skills
from other tools (and your own `instructions` entries) alone. `fxlla doctor`
reports whether the installed copies are current.

## How it fits together

`fxlla` is the control plane. It serves an OpenAI-compatible endpoint that
opencode consumes as a `local` provider, alongside Claude and any other
provider. Claude Code is not modified. On top of that it ships local knowledge
bases, a multi-language code graph, and image, video, and speech generation,
all exposed
as MCP tools any client can call. See `ROADMAP.md` and `docs/roadmap-remaining.md`
for what is next.

## Configuration

All settings live in `~/.config/fxlla/config.env` or the environment. See
`config/config.env.example` for the full list with comments.

- Core: `FXLLA_STORE`, `FXLLA_RATE_MBIT`, `FXLLA_HOST`, `FXLLA_PORT`,
  `FXLLA_DEFAULT_MODEL`, `FXLLA_KEEP_WARM`, `FXLLA_CTX`, `FXLLA_NGL`,
  `FXLLA_RAM_RESERVE_MB`, `HF_TOKEN`.
- Gateway and RAG: `FXLLA_GATEWAY_BUDGET_MB`, `FXLLA_BACKEND_PORT_BASE`,
  `FXLLA_EMBED_PORT`.
- Media: `FXLLA_MEDIA_MODEL`, `FXLLA_MEDIA_HF_HOME`, `FXLLA_MEDIA_OUT`,
  `FXLLA_VIDEO_BIN`, `FXLLA_MFLUX_BIN_DIR`, `FXLLA_VOICE_PYTHON`,
  `FXLLA_VOICE_REF`, `FXLLA_VOICE_LANG`.

## Catalog

Edit `config/models.conf`: `alias | repo | size | role | engine | note`.
`fxlla pull <org/repo>` also works with any Hugging Face repository.

## Governance

See `AGENTS.md`, `MAINTAINERS.md`, `COLLABORATORS.md`, and `SECURITY.md`.

## Community

Part of the [HowDidTheCatGetSoFat](https://github.com/HowDidTheCatGetSoFat)
community efforts.

## License

MIT. See `LICENSE`.
