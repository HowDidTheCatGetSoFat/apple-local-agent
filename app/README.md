# fxlla menu bar app

A native macOS menu bar app that drives the `fxlla` CLI. It is a thin control
surface: it shells out to `fxlla` and reads its state (`fxlla status`, the
gateway `/health`, and the `stats.jsonl` time-series). All real work stays in
the CLI.

## Features

- Menu bar icon that reflects state (idle vs running).
- Live `fxlla status`, refreshed on a timer.
- Time-series charts (Swift Charts): tokens/s, TTFT, and RAM in GB.
- Gateway start/stop.
- Model list with resident markers and on-demand load.
- Media generation: a prompt, an image/video/voice picker, and a Generate
  button that runs `fxlla media` and reveals the output in Finder.
- GPU RAM limit toggle (native admin prompt).

## Build and run

```sh
app/build.sh              # debug build -> app/fxlla.app
open app/fxlla.app        # launches into the menu bar
```

## Sign and package (Developer ID)

```sh
app/build.sh --release --sign
```

Signing uses the `Developer ID Application` identity; override with
`FXLLA_SIGN_ID`. A signed `.dmg` is built by `app/package-dmg.sh`, which can also
notarize and staple it (`app/package-dmg.sh --notarize <profile>`; `--check`
verifies the signing prerequisites).

## Requirements

- macOS 14 or later, Swift 6 toolchain (Xcode).
- No separate CLI install is required: `app/build.sh` bundles the CLI into the
  app (`Contents/Resources/cli`), and the panel's "Install the fxlla command"
  button symlinks it into `~/.local/bin`. A CLI already installed at
  `~/.local/bin`, `/usr/local/bin`, or `/opt/homebrew/bin` wins over the
  bundled copy (so a development checkout stays in charge).
