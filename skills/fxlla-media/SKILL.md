---
name: fxlla-media
description: Generate and edit images, short videos, and speech locally with the fxlla media tools when the user asks for a picture, a clip, or spoken audio. Choose the model and its options deliberately - call list_media_models and list_loras first - instead of accepting defaults or describing what you would make.
---

# Generate media locally

fxlla exposes local generation over MCP. Produce the artifact rather than
describing it, and choose HOW to produce it: the models differ in ways that
change the result, and picking well is part of the job.

## Start by looking

Two calls cost nothing and prevent most bad output:

- `list_media_models` - every model, the options each one supports, which is
  the default, and `prompt_formats` for models whose prompt is structured.
  Never guess a model name or an option; a model that lacks an option refuses
  it by name and names the models that have it.
- `list_loras` - LoRAs the user has downloaded, plus the built-in styles. If
  one fits what they asked for, **offer it**. Someone who downloaded a LoRA
  wants it used, and it is invisible unless you look.

## Choosing a model

The default (`z-image-turbo`) is fast and fine for most things. Reach past it
deliberately:

- **Text inside the image, posters, layouts** - `ideogram4`, with its JSON
  caption when placement matters (below). `qwen` is the other strong one for
  rendered text.
- **Photographic realism** - `krea2`.
- **Maximum fidelity, slow** - `dev` or `z-image`.
- **Fastest** - `z-image-turbo`, `schnell`, `boogu`, `ernie-turbo`.
- **Long, highly specified prompts** - `fibo`.
- **Composition taken from a reference image** - `controlnet` or
  `z-controlnet` (see Controls).
- **Geometry taken from a photo** - `depth`.

Say which model you chose and why, in one short clause. If the user names one,
use it.

## Options worth setting

- `seed` - set one whenever the user might want this image again or a variation
  of it, and report it. Without a seed a good result is unrepeatable.
- `steps` - more is slower and usually better. The distilled models have a low
  default for a reason; leave it unless quality is the complaint.
- `guidance` - how literally the prompt is followed. Raise it when the model
  drifts from the request, lower it when the output looks stiff or over-baked.
- `negative` - what must NOT appear. Cheap and effective against the usual
  offenders: text, watermark, extra fingers, blur.
- `aspect` OR `width`/`height`, never both - they conflict and the call is
  refused. Aspect for standard framing, dimensions for exact pixels.
- `init_image` - start from an existing image instead of from noise.
- `loras` - `"path,0.8"` or a HuggingFace repo id; the scale after the comma is
  optional, and the option repeats. `lora_style` picks a built-in style.

## Ideogram 4 and placed text

`ideogram4` accepts a JSON object as its prompt, which is how you say WHERE
things go instead of hoping. Get the schema and a worked example from
`list_media_models` under `prompt_formats`. Two traps: a bbox is
`[y_min, x_min, y_max, x_max]` - **Y first** - as integers in a 0..1000 space,
and hex colors must be UPPERCASE. Each `text` element carries the literal
characters to render.

Plain prose still works. Use JSON when the user cares about layout: a poster, a
label, a sign, text in a particular corner. A malformed caption is rejected
before the render starts with the problem named, so fix that field rather than
falling back to prose.

## Controls, depth, and chaining

`controls` is repeatable, so several controls STACK in one call. The two
families take different forms and `list_media_models` says which: `controlnet`
takes `"image.png"` or `"image.png,checkpoint"`, `z-controlnet` takes
`"type:path[:strength]"` such as `"pose:pose.png:0.8"`.

To copy the geometry of a photo: run `depth` with `init_image` and
`save_depth: true`, which returns the derived depth map's path on a second
line; then pass that map as a control to a following generation. fxlla exposes
each step - sequencing them is yours.

## Long renders

Pass `async: true` for video and any slow render: it returns a job id
immediately. Then call `media_job_status` with `wait_s` to **block until it
finishes**. Do not call it repeatedly in a loop: a render takes minutes, and
polling burns a call per second while looking like a runaway.

`list_media_jobs` shows all of them, `cancel_media_job` stops one. Jobs run one
at a time, so a submission may sit in `queued` while another finishes. That is
expected, not a failure.

## Video

`generate_video` takes `seconds` for duration - prefer it over `frames`, and do
not pass both. It returns the path plus the MEASURED duration, frame count and
fps: report those, never a duration computed from your own request.

For a video built from existing images, pass `images`: one anchors the opening
frame, two anchor both ends, which is what makes a transition between two
stills. Describing the images in the prompt instead produces a different video
that merely resembles them.

## Weights and consent

Weights download on first use and are large - tens of gigabytes per model, and
some need two repositories. A render whose weights are missing is refused with
the size, and that refusal is correct: present the size to the user and let
them decide, then pre-fetch with `fxlla pull media:<alias>` or pass the
authorization. Never authorize a large transfer on the user's behalf.
`fxlla media weights` lists every model with its size and whether it is cached.

## Reporting

Give the output path, the model, and the seed. If a content check rejected the
output (silence in a WAV, a container with no frames), relay what was flagged
rather than retrying blindly. If a backend is missing, relay the error and
point at `fxlla doctor`.
