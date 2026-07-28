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

## Requirements

- Apple Silicon Mac (M-series).
- Homebrew, `uv`, and `aria2` (installed or set up by `fxlla setup`).
- An external disk or a folder with room for the weights.

## Install

```sh
git clone https://github.com/HowDidTheCatGetSoFat/fxlla.git
cd fxlla
ln -sf "$PWD/bin/fxlla" ~/.local/bin/fxlla
mkdir -p ~/.config/fxlla && cp config/config.env.example ~/.config/fxlla/config.env
fxlla setup
```

`fxlla setup` installs `mlx-lm` and `llama.cpp` and verifies the environment.

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

| Command                 | What it does                                    |
|-------------------------|-------------------------------------------------|
| `fxlla setup`           | Install and verify dependencies                 |
| `fxlla models`          | List the catalog                                |
| `fxlla pull <model>`    | Download a model (bandwidth-capped, resumable)  |
| `fxlla ls`              | List downloaded models                          |
| `fxlla on [model]`      | Start the server and register it in opencode    |
| `fxlla off`             | Stop the server                                 |
| `fxlla status`          | Server, model, and idle status                  |
| `fxlla stats [--watch]` | Live tok/s, TTFT, RAM (also --json, --last N)   |
| `fxlla ram [auto|reset]`| Adjust the GPU memory limit                     |
| `fxlla wire-opencode`   | Register the local provider in opencode         |
| `fxlla config`          | Show the effective configuration                |

## Keys and model cache

Both are set in `~/.config/fxlla/config.env`, which is never committed:

- `FXLLA_STORE` is where models are cached. Point it at any mounted disk or a
  local folder.
- `HF_TOKEN` is only needed for gated Hugging Face repositories. Leave it unset
  otherwise.

Never put tokens anywhere else in the tree. `config/config.env` is git-ignored.
An exported environment variable always wins over `config.env`, which in turn
wins over the built-in defaults.

## Shell completions

Completion for commands, catalog aliases (`pull`), downloaded models
(`on`/`off`/`rm`), and `kb`/`graph` subcommands:

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
download first. This is what lets an agent offer a download (with size and time)
and only pull after you agree, instead of blocking on a silent large download.

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

## Using the full 128 GB

macOS reserves about 75 percent of RAM for the GPU. For large models raise it:

```sh
fxlla ram          # show current and recommended limit
fxlla ram auto     # raise it (asks for sudo, keeps a reserve for the OS)
fxlla ram reset    # back to default
```

The change reverts on reboot.

## Keep-warm

`FXLLA_KEEP_WARM` (minutes, default 10) stops the server after that idle time
to free RAM. Set `0` to disable. `fxlla status` shows the idle timer.

## Multi-model gateway

`fxlla on` serves one model. `fxlla serve` starts a gateway: a single
OpenAI-compatible endpoint that fronts every downloaded model.

```sh
fxlla serve                 # start the gateway on 127.0.0.1:8080
fxlla status                # gateway, resident models, RAM budget
fxlla unserve               # stop it (unloads all backends)
```

`GET /v1/models` lists every downloaded model. A request picks one by name:

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
app/build.sh && open app/fxlla.app
```

## Knowledge bases (RAG)

Index your files into a local knowledge base and search them semantically. It
uses a small local embedding model (the `embed` catalog model, via llama.cpp)
and a SQLite store; nothing leaves the machine.

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

## Code graph

Index a Python codebase and navigate it structurally: where a symbol is
defined, where it is referenced, and which functions call it. Uses the standard
library `ast` and a SQLite store; no dependencies.

```sh
fxlla graph index .                 # index Python files or directories
fxlla graph def _db                 # where a symbol is defined
fxlla graph callers _db             # which functions call it
fxlla graph refs chunk_text         # where it is referenced
```

Expose it to your tools as an MCP server (`find_definition`, `find_references`,
`find_callers`):

```sh
fxlla graph wire-opencode
```

## How it fits together

`fxlla` is the control plane. It serves an OpenAI-compatible endpoint that
opencode consumes as a `local` provider, alongside Claude and any other
provider. Claude Code is not modified. The roadmap extends this with local
knowledge bases, a code graph, and image and video generation, all exposed as
MCP tools that any client can call. See `ROADMAP.md`.

## Configuration

All settings live in `~/.config/fxlla/config.env` or the environment:
`FXLLA_STORE`, `FXLLA_RATE_MBIT`, `FXLLA_HOST`, `FXLLA_PORT`,
`FXLLA_DEFAULT_MODEL`, `FXLLA_KEEP_WARM`, `FXLLA_CTX`, `FXLLA_NGL`,
`FXLLA_RAM_RESERVE_MB`, `HF_TOKEN`.

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
