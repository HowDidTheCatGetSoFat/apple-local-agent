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
`FXLLA_SIGN_ID`. Notarization and a `.dmg` installer are tracked in the roadmap.

## Requirements

- macOS 14 or later, Swift 6 toolchain (Xcode).
- The `fxlla` CLI installed and on one of the standard paths
  (`~/.local/bin`, `/usr/local/bin`, `/opt/homebrew/bin`).
