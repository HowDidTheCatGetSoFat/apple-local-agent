#!/usr/bin/env python3
"""Minimal MCP server exposing local media generation over stdio.

Newline-delimited JSON-RPC, no dependencies. Image (mflux-cv), video (ltx-2-mlx),
speech (mlx-audio), edit, and upscale tools, so opencode or Claude Code can
render media locally.

A render takes tens of seconds and video takes minutes, which is longer than an
MCP client will wait for a response. So a render is submitted as a background job
and awaited for FXLLA_MCP_WAIT_S seconds: it returns the path if it finished in
that window and the job id if it did not, to be followed with `media_job_status`
(or `list_media_jobs`, `cancel_media_job`). Background jobs run one at a time.

Each tool call runs on its own thread. The read loop used to run them inline, so
one render made the server deaf: every later call - including the status calls
asking about that very render - timed out, and the caller, seeing timeouts,
resubmitted the same render three more times.
"""
import json
import os
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
MEDIA = os.path.join(HERE, "generate.py")

TOOLS = [
    {"name": "generate_image",
     "description": "Generate an image from a text prompt using the local "
                    "mflux-cv toolchain. Returns the PNG path, or a job id and "
                    "a status line when the render outlasts the wait window - "
                    "in which case follow it with media_job_status rather than "
                    "generating again.",
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
                   "description": "LoRAs to apply. Each entry is a path, a "
                                  "bare filename as list_loras reports it, or "
                                  "a HuggingFace repo id, optionally with a "
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
     "description": "Status of a background media job. Blocks while the job "
                    "runs and returns the finished record - output path, or "
                    "the error - as soon as it lands. If it comes back saying "
                    "the job is still running, call this again: that is the "
                    "render still going, not a failure, and resubmitting it "
                    "starts a second one.",
     "inputSchema": {"type": "object", "properties": {
         "job_id": {"type": "string"},
         "wait_s": {"type": "number",
                    "description": "Seconds to block, capped at the server's "
                                   "wait window (FXLLA_MCP_WAIT_S). Asking for "
                                   "more does not wait longer, it only risks "
                                   "your own client timing out."},
     }, "required": ["job_id"]}},
    {"name": "list_loras",
     "description": "LoRAs found on this machine and the built-in styles. Each entry carries base_model: the architecture it was trained for, prefixed with ~ when inferred from the weights rather than declared. Apply one only to its own base - a krea2 adapter does nothing useful on z-image. Check here before "
                    "generating: if a LoRA fits what the user asked for, offer "
                    "it rather than ignoring what they already downloaded.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_media_models",
     "description": "Image models available locally, each with the options it "
                    "supports (negative prompt, LoRA, dimensions, ...), "
                    "dim_step where the model only accepts sizes on a grid, "
                    "the built-in LoRA styles, which model is the default, and "
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

# Every generator tool takes an async flag, and it defaults to on: holding a
# call open for a render that takes minutes only produces a client timeout, and
# a timeout reads as a failure, so the caller renders it again.
_ASYNC_DOC = ("Default true: the render runs as a background job and this "
              "returns the output path if it finishes quickly, or the job id "
              "to follow with media_job_status if it does not. Set false only "
              "to hold the call open until the file is written, which a long "
              "render will outlast.")
_SKIP_QUALITY_DOC = ("Accept output that fails the content checks (silence, a "
                     "container with no frames). Use only after a check rejected "
                     "something you actually wanted.")
# The CLI has taken --output on every generator from the start; none of the MCP
# tools exposed it, so "save it in ~/Downloads" was accepted and then quietly
# ignored - the file landed in the media directory and the answer said nothing.
_OUTPUT_DOC = ("Where to write it: a file path, or a directory to have the "
               "file named inside it. Defaults to the media output directory. "
               "Pass this whenever the user says where they want it.")
for _tool in TOOLS:
    if _tool["name"].startswith(("generate_", "edit_", "upscale_")):
        _tool["inputSchema"]["properties"]["output"] = {
            "type": "string", "description": _OUTPUT_DOC}
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
                ("lora_style", "--lora-style"), ("output", "--output")]
_VIDEO_FLAGS = [("stage", "--stage"), ("seconds", "--seconds"),
                ("frames", "--frames"),
                ("frame_rate", "--frame-rate"), ("width", "--width"),
                ("height", "--height"), ("seed", "--seed"), ("model", "--model"),
                # generate.py takes --image (singular, repeatable), mirroring
                # ltx's own flag. --images does not exist in either.
                ("images", "--image"), ("output", "--output")]
_VOICE_FLAGS = [("ref", "--ref"), ("lang", "--lang"), ("speed", "--speed"),
                ("model", "--model"), ("output", "--output")]
_EDIT_FLAGS = [("image", "--image"), ("seed", "--seed"),
               ("quantize", "--quantize"), ("output", "--output")]


# How long a tool call may block before the client gives up on it. Measured
# against opencode in one session: a 45 s call returned its result, calls that
# ran past ~69 s came back to the model as "MCP error -32001: Request timed
# out" while the render kept going invisibly. Staying under that is what makes
# a slow render report progress instead of vanishing.
WAIT_S = float(os.environ.get("FXLLA_MCP_WAIT_S", "45"))


def _exec(cmd, failure):
    proc = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
    if proc.returncode != 0:
        return "error: " + (proc.stderr.strip() or failure)
    return proc.stdout.strip() or "error: no output"


def _job_record(job_id, wait_s):
    cmd = [sys.executable, MEDIA, "job", str(job_id), "--json"]
    if wait_s:
        cmd += ["--wait", str(wait_s)]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout.strip())
    except ValueError:
        return None


def _still_running(job_id, status):
    return ("job %s is %s and has not finished. Call media_job_status with "
            "job_id \"%s\" to keep waiting - do NOT submit the render again, "
            "it is already going." % (job_id, status, job_id))


def _spawn(cmd, args, failure):
    """Run one generation and return a path, an error, or a job id.

    Submitted as a background job unless the caller says otherwise, then
    awaited for WAIT_S. A render that outlives the window is reported as a
    running job rather than held open until the client times out - which is
    what used to happen, and the caller could not tell a timeout from a
    failure, so it resubmitted."""
    if args.get("skip_quality"):
        cmd = cmd + ["--skip-quality"]
    # Absent and explicit null both mean "use the default", which is the job
    # path; only a literal false opts out.
    background = args.get("async")
    if background is not None and not background:
        # Explicitly opted out: the caller wants the path in this response and
        # accepts that a long render may outlast its own timeout.
        proc = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
        if proc.returncode != 0:
            return "error: " + (proc.stderr.strip() or failure)
        return proc.stdout.strip() or "error: no output path returned"
    proc = subprocess.run(cmd + ["--async"], capture_output=True, text=True,
                          env=os.environ)
    if proc.returncode != 0:
        return "error: " + (proc.stderr.strip() or failure)
    job_id = proc.stdout.strip()
    if not job_id:
        return "error: no job id returned"
    rec = _job_record(job_id, WAIT_S)
    if rec is None:
        return job_id
    status = rec.get("status")
    if status == "done":
        return rec.get("output") or job_id
    if status in ("failed", "cancelled"):
        return "error: job %s %s: %s" % (job_id, status,
                                         (rec.get("error") or "").strip()[-800:])
    return _still_running(job_id, status)


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
    return _spawn(cmd, args, "generation failed")


def run_job_status(args):
    job_id = args.get("job_id")
    if not job_id:
        return "error: job_id is required"
    # Capped, for the same reason the render is: a request to wait 120 s came
    # back to the model as a timeout, not as a status, and told it nothing.
    # An explicit 0 still means "answer now" rather than the default wait.
    raw = args.get("wait_s")
    wait_s = WAIT_S if raw is None else min(float(raw), WAIT_S)
    rec = _job_record(job_id, wait_s)
    if rec is None:
        return "error: unknown job: %s" % job_id
    if rec.get("status") in ("queued", "running"):
        return _still_running(job_id, rec["status"])
    return json.dumps(rec)


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
    if args.get("output") is not None:
        cmd += ["--output", str(args["output"])]
    return _spawn(cmd, args, "upscale failed")


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
            # A runner that raises on its own thread would drop the response
            # and leave the caller waiting on a request that can never arrive -
            # indistinguishable from the hang this server was fixed to avoid.
            try:
                text = runner(params.get("arguments", {}))
            except Exception as exc:  # noqa: BLE001 - report, never drop
                text = "error: %s: %s" % (type(exc).__name__, exc)
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


_WRITE = threading.Lock()


def _emit(resp):
    if resp is None:
        return
    with _WRITE:
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


def main():
    running = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        # Tool calls go to their own thread so a render never stops the server
        # from reading. Responses carry the request id, so the client matches
        # them whatever order they arrive in. initialize and tools/list stay
        # inline: they are instant, and a client waits for initialize before
        # sending anything else.
        if msg.get("method") == "tools/call":
            worker = threading.Thread(target=lambda m=msg: _emit(handle(m)),
                                      daemon=True)
            running.append(worker)
            worker.start()
            running = [t for t in running if t.is_alive()]
        else:
            _emit(handle(msg))
    # Stdin ended. Finish what is in flight first: returning here would let the
    # interpreter exit and take the daemon threads with it, so calls already
    # accepted would never be answered.
    for worker in running:
        worker.join()


if __name__ == "__main__":
    main()
