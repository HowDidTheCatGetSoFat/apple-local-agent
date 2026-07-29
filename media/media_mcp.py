#!/usr/bin/env python3
"""Minimal MCP server exposing local image and video generation over stdio.

Newline-delimited JSON-RPC, no dependencies. Two tools, generate_image
(mflux-cv) and generate_video (ltx-2-mlx), so opencode or Claude Code can render
media locally. Generation is synchronous and can take tens of seconds (video
longer); the call blocks until the file is written.
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
                   "description": "Model name (default z-image-turbo). "
                                  "See 'fxlla media models'."},
         "steps": {"type": "integer"},
         "seed": {"type": "integer"},
         "width": {"type": "integer"},
         "height": {"type": "integer"},
         "aspect": {"type": "string", "description": "e.g. 1:1, 16:9"},
         "quantize": {"type": "integer", "enum": [3, 4, 5, 6, 8]},
     }, "required": ["prompt"]}},
    {"name": "generate_video",
     "description": "Generate a short video from a text prompt using the local "
                    "ltx-2-mlx (LTX-2.3) toolchain. Returns the path to the MP4.",
     "inputSchema": {"type": "object", "properties": {
         "prompt": {"type": "string"},
         "stage": {"type": "string",
                   "enum": ["distilled", "one-stage", "two-stage", "two-stages-hq"],
                   "description": "Quality stage (default distilled, the fast path)."},
         "frames": {"type": "integer"},
         "frame_rate": {"type": "integer", "description": "Default 24 (trained fps)."},
         "width": {"type": "integer"},
         "height": {"type": "integer"},
         "seed": {"type": "integer"},
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
]

_IMAGE_FLAGS = [("model", "--model"), ("steps", "--steps"), ("seed", "--seed"),
                ("width", "--width"), ("height", "--height"), ("aspect", "--aspect"),
                ("quantize", "--quantize")]
_VIDEO_FLAGS = [("stage", "--stage"), ("frames", "--frames"),
                ("frame_rate", "--frame-rate"), ("width", "--width"),
                ("height", "--height"), ("seed", "--seed"), ("model", "--model")]
_VOICE_FLAGS = [("ref", "--ref"), ("lang", "--lang"), ("speed", "--speed"),
                ("model", "--model")]
_EDIT_FLAGS = [("image", "--image"), ("seed", "--seed"), ("quantize", "--quantize")]


def _run(subcmd, positional, flags, args):
    value = args.get(positional)
    if not value:
        return "error: %s is required" % positional
    cmd = [sys.executable, MEDIA, subcmd, str(value)]
    for key, flag in flags:
        val = args.get(key)
        if val is not None:
            cmd += [flag, str(val)]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
    if proc.returncode != 0:
        return "error: " + (proc.stderr.strip() or "generation failed")
    return proc.stdout.strip() or "error: no output path returned"


def run_generate(args):
    return _run("image", "prompt", _IMAGE_FLAGS, args)


def run_generate_video(args):
    return _run("video", "prompt", _VIDEO_FLAGS, args)


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
                  "upscale_image": run_upscale}.get(tool)
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
