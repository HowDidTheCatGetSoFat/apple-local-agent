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
]

_IMAGE_FLAGS = [("model", "--model"), ("steps", "--steps"), ("seed", "--seed"),
                ("width", "--width"), ("height", "--height"), ("aspect", "--aspect"),
                ("quantize", "--quantize")]
_VIDEO_FLAGS = [("stage", "--stage"), ("frames", "--frames"),
                ("frame_rate", "--frame-rate"), ("width", "--width"),
                ("height", "--height"), ("seed", "--seed"), ("model", "--model")]


def _run(subcmd, flags, args):
    prompt = args.get("prompt")
    if not prompt:
        return "error: prompt is required"
    cmd = [sys.executable, MEDIA, subcmd, str(prompt)]
    for key, flag in flags:
        val = args.get(key)
        if val is not None:
            cmd += [flag, str(val)]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=os.environ)
    if proc.returncode != 0:
        return "error: " + (proc.stderr.strip() or "generation failed")
    return proc.stdout.strip() or "error: no output path returned"


def run_generate(args):
    return _run("image", _IMAGE_FLAGS, args)


def run_generate_video(args):
    return _run("video", _VIDEO_FLAGS, args)


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
                  "generate_video": run_generate_video}.get(tool)
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
