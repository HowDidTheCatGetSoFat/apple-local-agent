---
name: fxlla-media
description: Generate images, short videos, or speech locally with the fxlla media tools when the user asks for a picture, a clip, or spoken audio. Use generate_image, generate_video, and generate_speech instead of describing what you would make.
---

# Generate media locally

fxlla exposes local generation over an MCP server: `generate_image` (mflux-cv),
`generate_video` (ltx-2-mlx), and `generate_speech` (mlx-audio). When the user
asks for a picture, a short clip, or spoken audio, produce it rather than only
describing it.

## When to use

- The user asks to create or edit an image, a short video, or speech from text.
- A visual or audio artifact would answer better than words.

## How

- `generate_image`: pass a clear prompt. Defaults to a fast model; the tool
  returns the path to the written PNG.
- `generate_video`: keep clips short (frames are a small multiple, e.g. 25 or
  49). Returns an MP4.
- `generate_speech`: needs a reference voice wav configured on the machine; it
  returns a WAV. If it fails for a missing reference, relay that.
- Report the output file path so the user can open it.

## Notes

- Generation is synchronous by default and can take from seconds (image) to a
  minute or more (video). Set expectations before a long job.
- For video, or any render you expect to be slow, pass `async: true`: the call
  returns a job id immediately. Poll `media_job_status` with that id (statuses:
  queued, running, done, failed, cancelled) and report the output path once it is
  done. `list_media_jobs` shows all of them and `cancel_media_job` stops one.
  Do not block a long conversation on a synchronous video render.
- Jobs run one at a time, so a submission may sit in `queued` while another
  render finishes. That is expected, not a failure.
- Heavy jobs are memory-hungry; fxlla frees the gateway's resident models first
  unless told to keep them. If a backend is not configured, relay the tool's
  error and point to `fxlla doctor`.
