#!/usr/bin/env python3
"""Minimal MCP server exposing local media generation over stdio.

Newline-delimited JSON-RPC, no dependencies. Image (mflux-cv), video (ltx-2-mlx),
speech (mlx-audio), edit, and upscale tools, so opencode or Claude Code can
render media locally.

Generation is synchronous by default and can take tens of seconds (video much
longer); the call blocks until the file is written. Pass `async: true` to submit a
background job and get a job id back immediately, then poll `media_job_status`
(or `list_media_jobs`, `cancel_media_job`). Background jobs run one at a time.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MEDIA = os.path.join(HERE, "generate.py")

TOOLS = [
    {"name": "generate_image",
     "description": "Generate an image from a text prompt using the local "
                    "mflux-cv toolchain. Returns the path to the written PNG.",
     "inputSchema": {"type": "object", "properties": {
         "prompt": {"type": "string"},
         "model": {"type": "string",
                   "description": "Model name (default z-image-turbo). Call "
                                  "list_media_models for the list, what each "
                                  "supports, and the JSON caption schema that "
                                  "ideogram4 accepts as its prompt."},
         "steps": {"type": "integer"},
         "seed": {"type": "integer"},
         "width": {"type": "integer"},
         "height": {"type": "integer"},
         "aspect": {"type": "string", "description": "e.g. 1:1, 16:9"},
         "quantize": {"type": "integer", "enum": [3, 4, 5, 6, 8]},
         "negative": {"type": "string",
                      "description": "What the image must NOT contain."},
         "prompt_file": {"type": "string",
                         "description": "Read the prompt from this file "
                                        "instead of the prompt argument."},
         "init_image": {"type": "string",
                        "description": "Start from this image (img2img)."},
         "loras": {"type": "array", "items": {"type": "string"},
                   "description": "LoRAs to apply. Each entry is a path or a "
                                  "HuggingFace repo id, optionally with a "
                                  "scale after a comma: \"style.safetensors,0.8\". "
                                  "See list_media_models for which models "
                                  "support them."},
         "lora_style": {"type": "string",
                        "description": "A built-in LoRA style (storyboard, "
                                       "portrait, identity, ...); see "
                                       "list_loras."},
         "guidance": {"type": "number",
                      "description": "Guidance scale. Higher follows the prompt "
                                     "more literally. Model default is usually "
                                     "3.5, 10 for depth."},
         "controls": {"type": "array", "items": {"type": "string"},
                      "description": "Control inputs, repeatable to STACK "
                                     "several (e.g. depth + canny). Form "
                                     "depends on the model: \"image.png\" or "
                                     "\"image.png,checkpoint\" for the "
                                     "controlnet model, \"type:path[:strength]\" "
                                     "(e.g. pose:pose.png:0.8) for "
                                     "z-controlnet. Use list_media_models to "
                                     "see which a model takes."},
         "controlnet_strength": {"type": "number",
                                 "description": "Global multiplier over all controls."},
         "depth_image": {"type": "string",
                         "description": "Use this depth map instead of deriving one."},
         "save_depth": {"type": "boolean",
                        "description": "Also write the derived depth map. Its "
                                       "path is returned on a second line, so "
                                       "a later control step can use it."},
     }, "required": ["prompt"]}},
    {"name": "generate_video",
     "description": "Generate a short video from a text prompt using the local "
                    "ltx-2-mlx (LTX-2.3) toolchain. Returns the path to the MP4 "
                    "and the MEASURED duration, frame count and fps of the "
                    "result - report those, do not compute duration from the "
                    "request.",
     "inputSchema": {"type": "object", "properties": {
         "prompt": {"type": "string"},
         "stage": {"type": "string",
                   "enum": ["distilled", "one-stage", "two-stage", "two-stages-hq"],
                   "description": "Quality stage (default distilled, the fast path)."},
         "seconds": {"type": "number",
                     "description": "Target duration in seconds. Prefer this over "
                                    "frames; do not pass both."},
         "frames": {"type": "integer"},
         "frame_rate": {"type": "integer", "description": "Default 24 (trained fps)."},
         "width": {"type": "integer"},
         "height": {"type": "integer"},
         "seed": {"type": "integer"},
         "images": {"type": "array", "items": {"type": "string"},
                    "description": "Reference images for image-to-video, each "
                                   "\"path\" or \"path,frame,strength\". "
                                   "One anchors the opening frame; two anchor "
                                   "both ends, which is how a transition "
                                   "between two stills is produced. Describing "
                                   "the images in the prompt instead makes a "
                                   "different video that merely resembles them."},
     }, "required": ["prompt"]}},
    {"name": "generate_speech",
     "description": "Synthesize speech from text using the local mlx-audio "
                    "(Chatterbox) toolchain. Returns the path to the WAV.",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string"},
         "ref": {"type": "string", "description": "Reference voice wav (timbre)."},
         "lang": {"type": "string", "description": "Language code, e.g. en, es, pt."},
         "speed": {"type": "number"},
     }, "required": ["text"]}},
    {"name": "edit_image",
     "description": "Edit an existing image from a text instruction using the "
                    "local mflux-cv qwen-edit toolchain. Returns the path to the "
                    "written PNG.",
     "inputSchema": {"type": "object", "properties": {
         "prompt": {"type": "string", "description": "The edit instruction."},
         "image": {"type": "string", "description": "Path to the input image."},
         "seed": {"type": "integer"},
         "quantize": {"type": "integer", "enum": [3, 4, 5, 6, 8]},
     }, "required": ["prompt", "image"]}},
    {"name": "upscale_image",
     "description": "Upscale an image using the local mflux-cv seedvr2 "
                    "diffusion super-resolution. Returns the path to the PNG.",
     "inputSchema": {"type": "object", "properties": {
         "image": {"type": "string", "description": "Path to the input image."},
         "scale": {"type": "string",
                   "description": "Target shortest edge in pixels or a factor, e.g. 2x."},
     }, "required": ["image"]}},
    {"name": "media_job_status",
     "description": "Status of a background media job: queued, running, done, "
                    "failed, or cancelled, plus the output path once done. "
                    "Pass wait_s to BLOCK until it finishes instead of polling "
                    "in a loop.",
     "inputSchema": {"type": "object", "properties": {
         "job_id": {"type": "string"},
         "wait_s": {"type": "number",
                    "description": "Block up to this many seconds for the job "
                                   "to finish. Prefer this over repeated "
                                   "calls; a render takes minutes and polling "
                                   "it wastes a call per second."},
     }, "required": ["job_id"]}},
    {"name": "list_loras",
     "description": "LoRAs found on this machine and the built-in styles. Each entry carries base_model: the architecture it was trained for, prefixed with ~ when inferred from the weights rather than declared. Apply one only to its own base - a krea2 adapter does nothing useful on z-image. Check here before "
                    "generating: if a LoRA fits what the user asked for, offer "
                    "it rather than ignoring what they already downloaded.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_media_models",
     "description": "Image models available locally, each with the options it "
                    "supports (negative prompt, LoRA, dimensions, ...), the "
                    "built-in LoRA styles, which model is the default, and "
                    "prompt_formats: the JSON caption schema for models that "
                    "take one (ideogram4), with an example. Call this instead "
                    "of guessing model names, flags, or prompt structure.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_media_jobs",
     "description": "List background media jobs, newest first.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "cancel_media_job",
     "description": "Cancel a queued or running background media job.",
     "inputSchema": {"type": "object", "properties": {
         "job_id": {"type": "string"},
     }, "required": ["job_id"]}},
]

# Every generator tool takes an optional async flag: submitting returns a job id
# immediately instead of blocking, which matters for video (minutes long). Poll
# with media_job_status.
_ASYNC_DOC = ("Submit as a background job and return a job id immediately "
              "instead of waiting. Poll it with media_job_status.")
_SKIP_QUALITY_DOC = ("Accept output that fails the content checks (silence, a "
                     "container with no frames). Use only after a check rejected "
                     "something you actually wanted.")
for _tool in TOOLS:
    if _tool["name"].startswith(("generate_", "edit_", "upscale_")):
        _tool["inputSchema"]["properties"]["async"] = {
            "type": "boolean", "description": _ASYNC_DOC}
        _tool["inputSchema"]["properties"]["skip_quality"] = {
            "type": "boolean", "description": _SKIP_QUALITY_DOC}

_IMAGE_FLAGS = [("model", "--model"), ("steps", "--steps"), ("seed", "--seed"),
                ("width", "--width"), ("height", "--height"), ("aspect", "--aspect"),
                ("quantize", "--quantize"), ("negative", "--negative"),
                ("prompt_file", "--prompt-file"), ("guidance", "--guidance"),
                ("controls", "--control"),
                ("controlnet_strength", "--controlnet-strength"),
                ("depth_image", "--depth-image"),
                ("init_image", "--init-image"), ("loras", "--lora"),
                ("lora_style", "--lora-style")]
_VIDEO_FLAGS = [("stage", "--stage"), ("seconds", "--seconds"),
                ("frames", "--frames"),
                ("frame_rate", "--frame-rate"), ("width", "--width"),
                ("height", "--height"), ("seed", "--seed"), ("model", "--model"),
                # generate.py takes --image (singular, repeatable), mirroring
                # ltx's own flag. --images does not exist in either.
                ("images", "--image")]
_VOICE_FLAGS = [("ref", "--ref"), ("lang", "--lang"), ("speed", "--speed"),
                ("model", "--model")]
_EDIT_FLAGS = [("image", "--image"), ("seed", "--seed"), ("quantize", "--quantize")]


def _exec(cmd, failure):
    proc = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
    if proc.returncode != 0:
        return "error: " + (proc.stderr.strip() or failure)
    return proc.stdout.strip() or "error: no output"


def _run(subcmd, positional, flags, args):
    value = args.get(positional)
    if not value:
        return "error: %s is required" % positional
    cmd = [sys.executable, MEDIA, subcmd, str(value)]
    for key, flag in flags:
        val = args.get(key)
        if val is None:
            continue
        # A list argument repeats its flag rather than being stringified: one
        # --image per reference, which is what anchoring several frames needs.
        if isinstance(val, list):
            for item in val:
                cmd += [flag, str(item)]
        else:
            cmd += [flag, str(val)]
    if args.get("save_depth"):
        cmd.append("--save-depth-map")
    if args.get("async"):
        cmd.append("--async")
    if args.get("skip_quality"):
        cmd.append("--skip-quality")
    proc = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
    if proc.returncode != 0:
        return "error: " + (proc.stderr.strip() or "generation failed")
    return proc.stdout.strip() or "error: no output path returned"


def run_job_status(args):
    job_id = args.get("job_id")
    if not job_id:
        return "error: job_id is required"
    cmd = [sys.executable, MEDIA, "job", str(job_id), "--json"]
    wait_s = args.get("wait_s")
    if wait_s:
        cmd += ["--wait", str(wait_s)]
    return _exec(cmd, "unknown job")


def run_list_loras(_args):
    return _exec([sys.executable, MEDIA, "loras", "--json"],
                 "could not list loras")


def run_list_models(_args):
    """Capabilities, from the catalog. Without this a model reads the source to
    find out what it can do: one opened generate.py and media_mcp.py ten times
    in a single session doing exactly that."""
    return _exec([sys.executable, MEDIA, "models", "--json"],
                 "could not list models")


def run_list_jobs(_args):
    return _exec([sys.executable, MEDIA, "jobs", "--json"], "could not list jobs")


def run_cancel_job(args):
    job_id = args.get("job_id")
    if not job_id:
        return "error: job_id is required"
    return _exec([sys.executable, MEDIA, "cancel", str(job_id)], "unknown job")


def run_generate(args):
    return _run("image", "prompt", _IMAGE_FLAGS, args)


def run_generate_video(args):
    result = _run("video", "prompt", _VIDEO_FLAGS, args)
    # Hand back what the file IS, measured. A caller that computes duration
    # from its own request can be wrong and sound certain: one reported 49
    # frames at 24 fps as "about 10 seconds" (2.04) and declared an unmet
    # 4-8 second requirement satisfied. Async returns a job id, not a path,
    # so there is nothing to measure yet.
    if result.startswith("error:") or not os.path.isfile(result):
        return result
    sys.path.insert(0, HERE)
    import quality  # noqa: E402  (local module, beside this file)
    facts = quality.video_facts(result)
    if not facts.get("duration_s"):
        return result
    return ("%s\nmeasured: %.2fs, %s frames, %s fps, %sx%s"
            % (result, facts["duration_s"], facts.get("frames", "?"),
               facts.get("fps", "?"), facts.get("width", "?"),
               facts.get("height", "?")))


def run_generate_speech(args):
    return _run("voice", "text", _VOICE_FLAGS, args)


def run_edit(args):
    # qwen-edit needs both the instruction (positional) and the input image.
    if not args.get("image"):
        return "error: image is required"
    return _run("edit", "prompt", _EDIT_FLAGS, args)


def run_upscale(args):
    # seedvr2 has no prompt; the image is its required input, passed as --image.
    image = args.get("image")
    if not image:
        return "error: image is required"
    cmd = [sys.executable, MEDIA, "upscale", "--image", str(image)]
    scale = args.get("scale")
    if scale is not None:
        cmd += ["--scale", str(scale)]
    if args.get("async"):
        cmd.append("--async")
    if args.get("skip_quality"):
        cmd.append("--skip-quality")
    proc = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
    if proc.returncode != 0:
        return "error: " + (proc.stderr.strip() or "upscale failed")
    return proc.stdout.strip() or "error: no output path returned"


def handle(msg):
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        return _ok(mid, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fxlla-media", "version": "0.1.0"},
        })
    if method == "tools/list":
        return _ok(mid, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params", {})
        tool = params.get("name")
        runner = {"generate_image": run_generate,
                  "generate_video": run_generate_video,
                  "generate_speech": run_generate_speech,
                  "edit_image": run_edit,
                  "upscale_image": run_upscale,
                  "media_job_status": run_job_status,
                  "list_media_models": run_list_models,
                  "list_loras": run_list_loras,
                  "list_media_jobs": run_list_jobs,
                  "cancel_media_job": run_cancel_job}.get(tool)
        if runner:
            text = runner(params.get("arguments", {}))
            return _ok(mid, {"content": [{"type": "text", "text": text}]})
        return _err(mid, -32601, "unknown tool")
    if method and method.startswith("notifications/"):
        return None
    if mid is not None:
        return _err(mid, -32601, "method not found")
    return None


def _ok(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
