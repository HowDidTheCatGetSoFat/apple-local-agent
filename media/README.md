# fxlla media

Local image and video generation, driven through `fxlla media`. The CLI is a
thin wrapper over two Apple Silicon toolchains; fxlla keeps the friendly names,
sane defaults, and output validation in one place.

## Pieces

- `generate.py` - the wrapper and CLI. Images go through mflux-cv (one CLI per
  model family: `mflux-generate`, `mflux-generate-z-image-turbo`, ...); video
  goes through `ltx-2-mlx` (LTX-2.3). Output lands under `<FXLLA_STORE>/media`
  and every render is validated (a zero exit code is not proof of a real file).
- `media_mcp.py` - a minimal stdio MCP server exposing `generate_image` and
  `generate_video` so opencode and Claude Code can render media as a tool call.

Driven through the CLI:
`fxlla media image|video|models`, `fxlla media mcp`, `fxlla media wire-opencode`.

## Images (mflux-cv)

```sh
fxlla media models                       # list the supported image models
fxlla media image "a red sailboat at sunset" --model z-image-turbo \
    --width 1024 --height 1024 --seed 42 -q 8
```

`z-image-turbo` is the fast default. Common flags: `-q {3,4,5,6,8}` (quantize),
`--steps`, `--seed`, `--width`/`--height` or `--aspect`, `--low-ram`,
`--metadata` (writes a sidecar JSON), `-o <path>`.

## Video (ltx-2-mlx)

```sh
fxlla media video "a red sailboat sailing at sunset, gentle waves" \
    --frames 49 --width 768 --height 512 --frame-rate 24 --seed 42
```

`ltx-2-mlx generate` requires exactly one quality stage; the wrapper defaults to
`--distilled` (the fast, verified path) and passes the mandatory frame rate
(the model was trained at 24 fps). Override the stage with
`--stage {distilled,one-stage,two-stage,two-stages-hq}`.

## Configuration

- `FXLLA_VIDEO_BIN` - path to the `ltx-2-mlx` binary. It commonly lives in a
  project virtualenv rather than on PATH; set it in `~/.config/fxlla/config.env`.
- `FXLLA_MEDIA_HF_HOME` - Hugging Face cache holding the image diffusion weights
  (exported as `HF_HOME` for the child process).
- `FXLLA_MEDIA_MODEL` - default image model (default `z-image-turbo`).
- `FXLLA_MEDIA_OUT` - output directory (default `<FXLLA_STORE>/media`).

## Notes and limits

- Generation is synchronous and can take tens of seconds (video longer). The MCP
  call blocks until the file is written.
- `mflux-generate-qwen-layered` (image to RGBA layers) is not a text-to-image
  sampler and is intentionally not in the model list.
- The external disks that hold the weight caches must be mounted; a "model not
  found" error is usually an unmounted volume.
