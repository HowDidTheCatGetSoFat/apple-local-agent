#!/usr/bin/env python3
"""fxlla media: local image and video generation.

Images go through the mflux-cv toolchain (mflux-generate, mflux-generate-z-image-turbo,
...), mapping a friendly model name to the right CLI. Video goes through
ltx-2-mlx (LTX-2.3). Both write under <FXLLA_STORE>/media by default and the
produced file is validated, since a zero exit code is not proof of a real render.

Config via environment:
  FXLLA_MEDIA_HF_HOME   HF cache for the image diffusion weights (exported as HF_HOME)
  FXLLA_MEDIA_MODEL     default image model (default z-image-turbo)
  FXLLA_MEDIA_OUT       output directory (default <FXLLA_STORE>/media)
  FXLLA_VIDEO_BIN       path to the ltx-2-mlx binary (default: ltx-2-mlx on PATH)

Usage (normally driven via `fxlla media`):
  generate.py image "<prompt>" [--model z-image-turbo] [--steps N] [--seed N]
                              [--width N --height N | --aspect 1:1] [-q {3,4,5,6,8}]
                              [--low-ram] [--metadata] [-o path]
  generate.py video "<prompt>" [--stage distilled] [--frames N] [--frame-rate R]
                              [--width N] [--height N] [--seed N] [--low-ram] [-o path]
  generate.py edit "<prompt>" --image path [--seed N] [-q {3,4,5,6,8}] [-o path]
  generate.py upscale --image path [--scale 2x] [-o path]
  generate.py models          list the supported image models
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

import jobs  # background media jobs (submit, poll, cancel)
import quality  # perceptual checks on the produced file
import weights  # consent for the weights a render would download

# Set by --skip-quality. An MCP client cannot set an environment variable on an
# already-running server, so the flag is the only override available there.
SKIP_QUALITY = False

STORE = os.environ.get("FXLLA_STORE", "")
OUT_DIR = os.environ.get("FXLLA_MEDIA_OUT") or os.path.join(STORE, "media")
DEFAULT_MODEL = os.environ.get("FXLLA_MEDIA_MODEL", "z-image-turbo")
VIDEO_BIN = os.environ.get("FXLLA_VIDEO_BIN", "ltx-2-mlx")
# Instruction-based image edit and diffusion upscale are separate mflux-cv CLIs.
# One directory for the whole mflux family (image models, edit, upscale):
# image alone is eight per-model CLIs, so a single-binary knob cannot cover
# it. The specific knobs below still win for edit and upscale.
MFLUX_BIN_DIR = os.environ.get("FXLLA_MFLUX_BIN_DIR", "")
EDIT_BIN = os.environ.get("FXLLA_EDIT_BIN", "")
UPSCALE_BIN = os.environ.get("FXLLA_UPSCALE_BIN", "")


def mflux_cli(name):
    """Resolve one mflux-family CLI: FXLLA_MFLUX_BIN_DIR when set, PATH
    otherwise. A set directory missing the binary is an error worth naming -
    silently falling back to PATH would run a different install than the one
    the user pointed at, which is the quiet failure the knob exists to
    prevent."""
    if not MFLUX_BIN_DIR:
        return name
    path = os.path.join(MFLUX_BIN_DIR, name)
    if not os.path.isfile(path):
        raise ValueError(
            "FXLLA_MFLUX_BIN_DIR is set (%s) but '%s' is not in it"
            % (MFLUX_BIN_DIR, name))
    return path

# Media generation and the gateway's resident LLMs share unified memory. Before
# a job, ask a running gateway to free its models so the render has headroom;
# the gateway reloads on demand afterward. FXLLA_MEDIA_KEEP_MODELS opts out.
GATEWAY_HOST = os.environ.get("FXLLA_HOST", "127.0.0.1")
GATEWAY_PORT = os.environ.get("FXLLA_PORT", "8080")
KEEP_MODELS = os.environ.get("FXLLA_MEDIA_KEEP_MODELS", "") not in ("", "0", "false")

# Friendly name -> the mflux-cv CLI and defaults. `base_model` is only needed
# for the multi-model `mflux-generate` binary (FLUX.1). `steps` is a sane
# default for the fast distilled models; None leaves the CLI's own default.
MODELS = {
    "z-image-turbo": {"cli": "mflux-generate-z-image-turbo", "steps": 8},
    "z-image":       {"cli": "mflux-generate-z-image", "steps": None},
    "boogu":         {"cli": "mflux-generate-boogu", "steps": 8},
    "flux2-klein":   {"cli": "mflux-generate-flux2", "steps": None},
    "qwen":          {"cli": "mflux-generate-qwen", "steps": None},
    "krea2":         {"cli": "mflux-generate-krea2", "steps": None},
    "schnell":       {"cli": "mflux-generate", "base_model": "schnell", "steps": 4},
    "dev":           {"cli": "mflux-generate", "base_model": "dev", "steps": None},
}

# LTX-2.3 `generate` requires exactly one quality stage; distilled is the fast,
# verified default. frame-rate is mandatory (the model was trained at 24).
VIDEO_STAGES = ("distilled", "one-stage", "two-stage", "two-stages-hq")
DEFAULT_STAGE = "distilled"
DEFAULT_FRAME_RATE = 24

# Voice runs through a separate interpreter (FXLLA_VOICE_PYTHON) that has
# mlx-audio installed, so fxlla itself never imports it. Chatterbox ships no
# speaker conditionals, so a reference voice wav is required.
VOICE_PYTHON = os.environ.get("FXLLA_VOICE_PYTHON", "python3")


def resolved_voice_python():
    """The interpreter that actually has mlx-audio.

    FXLLA_VOICE_PYTHON always wins. Otherwise `fxlla setup --media` installs
    mlx-audio as a uv tool, and that venv's python is the one interpreter
    guaranteed to import it - so it is the default when present. The bare
    python3 fallback keeps the old behavior for hand-rolled venvs on PATH.
    Doctor and setup ask THIS function (via the voice-python subcommand)
    instead of re-implementing the resolution in shell."""
    explicit = os.environ.get("FXLLA_VOICE_PYTHON")
    if explicit:
        return explicit
    try:
        root = subprocess.run(["uv", "tool", "dir"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
        candidate = os.path.join(root, "mlx-audio", "bin", "python")
        if root and os.path.isfile(candidate):
            return candidate
    except Exception:
        pass
    return "python3"
VOICE_MODEL = os.environ.get("FXLLA_VOICE_MODEL", "YUGOROU/Chatterbox-Multilingual-MLX-4bit")
VOICE_REF = os.environ.get("FXLLA_VOICE_REF", "")
VOICE_LANG = os.environ.get("FXLLA_VOICE_LANG", "en")
VOICE_BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_backend.py")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _env():
    env = dict(os.environ)
    hf = os.environ.get("FXLLA_MEDIA_HF_HOME")
    if hf:
        env["HF_HOME"] = hf
    return env


def free_gpu(reason, keep=False):
    """Ask a running gateway to unload its resident models before a heavy job.

    Best-effort: if no gateway is up (connection refused) or the request fails,
    there is nothing to free and we proceed. Skipped when the caller opts out
    via keep=True or FXLLA_MEDIA_KEEP_MODELS."""
    if keep or KEEP_MODELS:
        return
    url = "http://%s:%s/admin/unload" % (GATEWAY_HOST, GATEWAY_PORT)
    req = urllib.request.Request(url, data=b"{}",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            info = json.loads(r.read() or b"{}")
    except Exception:
        return
    freed = info.get("unloaded") or []
    if freed:
        sys.stderr.write("[media] freed gateway models before %s: %s\n"
                         % (reason, ", ".join(freed)))


def build_command(spec, prompt, output, steps=None, seed=None, width=None,
                  height=None, aspect=None, quantize=8, low_ram=False, metadata=False):
    """Assemble the mflux-cv argument vector for one image generation."""
    cmd = [mflux_cli(spec["cli"])]
    if spec.get("base_model"):
        cmd += ["--base-model", spec["base_model"]]
    cmd += ["--prompt", prompt, "--output", output, "--quantize", str(quantize)]
    eff_steps = steps if steps is not None else spec.get("steps")
    if eff_steps is not None:
        cmd += ["--steps", str(eff_steps)]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    if aspect:
        cmd += ["--aspect", aspect]
    else:
        if width:
            cmd += ["--width", str(width)]
        if height:
            cmd += ["--height", str(height)]
    if low_ram:
        cmd += ["--low-ram"]
    if metadata:
        cmd += ["--metadata"]
    return cmd


# A structurally valid file can still be garbage: silent speech, a one-frame
# "video", a blank image. Content checks run after the container checks and are
# skippable, because a false positive would reject a render the user wanted.
def _check_quality(kind, path, checker):
    if SKIP_QUALITY or quality.skip_quality_checks():
        return
    try:
        problems = checker(path)
    except Exception as exc:  # a checker must never be the reason a render fails
        print("quality check skipped for %s: %s" % (path, exc), file=sys.stderr)
        return
    message = quality.report(kind, path, problems)
    if message:
        raise RuntimeError(message + " (pass --skip-quality, or set "
                           "FXLLA_MEDIA_SKIP_QUALITY=1, to accept it)")


def validate_output(path):
    """Fail if the output is missing, not a PNG, or implausibly small.

    mflux can exit 0 while writing nothing useful; checking the file is the only
    reliable signal. This catches empty or truncated files, not every bad image.
    """
    if not os.path.exists(path):
        raise RuntimeError("no output produced at %s" % path)
    if os.path.getsize(path) < 1024:
        raise RuntimeError("output at %s is suspiciously small; likely a failed render" % path)
    with open(path, "rb") as f:
        if f.read(8) != PNG_MAGIC:
            raise RuntimeError("output at %s is not a PNG" % path)
    _check_quality("image", path, quality.check_png)


def generate_image(prompt, model=None, steps=None, seed=None, width=None,
                   height=None, aspect=None, quantize=8, low_ram=False,
                   metadata=False, output=None, keep_models=False):
    if not prompt:
        raise ValueError("prompt is required")
    model = model or DEFAULT_MODEL
    spec = MODELS.get(model)
    if spec is None:
        raise ValueError("unknown model '%s'; try one of: %s"
                         % (model, ", ".join(sorted(MODELS))))
    os.makedirs(OUT_DIR, exist_ok=True)
    output = output or os.path.join(OUT_DIR, "fxlla-%s-%d.png" % (model, int(time.time())))
    free_gpu("image", keep_models)
    cmd = build_command(spec, prompt, output, steps=steps, seed=seed, width=width,
                        height=height, aspect=aspect, quantize=quantize,
                        low_ram=low_ram, metadata=metadata)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=_env())
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr.strip() or "%s failed" % spec["cli"])[-800:])
    validate_output(output)
    return output


def build_video_command(prompt, output, stage=DEFAULT_STAGE, frames=None,
                        frame_rate=DEFAULT_FRAME_RATE, width=None, height=None,
                        seed=None, low_ram=False, model=None, bin_path=None):
    """Assemble the ltx-2-mlx argument vector for one video generation.

    generate requires exactly one stage flag (distilled is the fast default) and
    frame-rate is mandatory (the model was trained at 24 fps)."""
    if stage not in VIDEO_STAGES:
        raise ValueError("unknown stage '%s'; try one of: %s"
                         % (stage, ", ".join(VIDEO_STAGES)))
    cmd = [bin_path or VIDEO_BIN, "generate", "--%s" % stage,
           "--prompt", prompt, "--output", output,
           "--frame-rate", str(frame_rate if frame_rate is not None else DEFAULT_FRAME_RATE)]
    if model:
        cmd += ["--model", model]
    if frames:
        cmd += ["--frames", str(frames)]
    if width:
        cmd += ["--width", str(width)]
    if height:
        cmd += ["--height", str(height)]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    if low_ram:
        cmd += ["--low-ram"]
    return cmd


def validate_video_output(path):
    """Fail if the output is missing, not an MP4, or implausibly small."""
    if not os.path.exists(path):
        raise RuntimeError("no output produced at %s" % path)
    if os.path.getsize(path) < 10240:
        raise RuntimeError("output at %s is suspiciously small; likely a failed render" % path)
    with open(path, "rb") as f:
        head = f.read(16)
    if b"ftyp" not in head:
        raise RuntimeError("output at %s is not an MP4" % path)
    _check_quality("video", path, quality.check_video)


def generate_video(prompt, stage=DEFAULT_STAGE, frames=None,
                   frame_rate=DEFAULT_FRAME_RATE, width=None, height=None,
                   seed=None, low_ram=False, model=None, output=None,
                   keep_models=False):
    if not prompt:
        raise ValueError("prompt is required")
    os.makedirs(OUT_DIR, exist_ok=True)
    output = output or os.path.join(OUT_DIR, "fxlla-video-%d.mp4" % int(time.time()))
    free_gpu("video", keep_models)
    cmd = build_video_command(prompt, output, stage=stage, frames=frames,
                              frame_rate=frame_rate, width=width, height=height,
                              seed=seed, low_ram=low_ram, model=model)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=_env())
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr.strip() or "video generation failed")[-800:])
    validate_video_output(output)
    return output


def build_voice_command(text, output, ref, model=None, lang=None, speed=1.0,
                        python=None, backend=None):
    """Assemble the command that runs the voice backend under the mlx-audio
    interpreter. A reference voice wav is required (the model has no built-in
    speaker conditionals)."""
    if not ref:
        raise ValueError("a reference voice wav is required "
                         "(set FXLLA_VOICE_REF or pass --ref)")
    return [python or resolved_voice_python(), backend or VOICE_BACKEND,
            "--text", text, "--output", output, "--ref", ref,
            "--model", model or VOICE_MODEL, "--lang", lang or VOICE_LANG,
            "--speed", str(speed)]


def validate_wav_output(path):
    """Fail if the output is missing, not a WAV, or implausibly small."""
    if not os.path.exists(path):
        raise RuntimeError("no output produced at %s" % path)
    if os.path.getsize(path) < 1024:
        raise RuntimeError("output at %s is suspiciously small; likely a failed render" % path)
    with open(path, "rb") as f:
        head = f.read(12)
    if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise RuntimeError("output at %s is not a WAV" % path)
    _check_quality("audio", path, quality.check_wav)


def generate_speech(text, ref=None, lang=None, model=None, speed=1.0,
                    output=None, keep_models=False):
    if not text:
        raise ValueError("text is required")
    os.makedirs(OUT_DIR, exist_ok=True)
    output = output or os.path.join(OUT_DIR, "fxlla-voice-%d.wav" % int(time.time()))
    free_gpu("voice", keep_models)
    cmd = build_voice_command(text, output, ref or VOICE_REF, model=model,
                              lang=lang, speed=speed)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=_env())
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr.strip() or "voice generation failed")[-800:])
    validate_wav_output(output)
    return output


def build_edit_command(prompt, image, output, seed=None, quantize=8,
                       bin_path=None):
    """Assemble the mflux-cv qwen-edit argument vector for one image edit.

    An input image is required: qwen-edit conditions the edit on it, so an
    empty path cannot produce anything. The image is passed via --image-paths
    (the CLI accepts one or more; a single edit uses one)."""
    if not prompt:
        raise ValueError("prompt is required")
    if not image:
        raise ValueError("an input image is required")
    cmd = [bin_path or EDIT_BIN or mflux_cli("mflux-generate-qwen-edit"),
           "--prompt", prompt, "--image-paths", image,
           "--output", output, "--quantize", str(quantize)]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    return cmd


def generate_edit(prompt, image, seed=None, quantize=8, output=None,
                  keep_models=False):
    if not prompt:
        raise ValueError("prompt is required")
    if not image:
        raise ValueError("an input image is required")
    if not os.path.exists(image):
        raise ValueError("input image not found: %s" % image)
    os.makedirs(OUT_DIR, exist_ok=True)
    output = output or os.path.join(OUT_DIR, "fxlla-edit-%d.png" % int(time.time()))
    free_gpu("edit", keep_models)
    cmd = build_edit_command(prompt, image, output, seed=seed, quantize=quantize)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=_env())
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr.strip() or "image edit failed")[-800:])
    validate_output(output)
    return output


def build_upscale_command(image, output, scale=None, bin_path=None):
    """Assemble the mflux-cv seedvr2 argument vector for one image upscale.

    seedvr2 has no prompt; the input image is the required conditioning. --scale
    maps to the CLI's --resolution, which accepts a target shortest-edge in
    pixels or a scale factor such as 2x."""
    if not image:
        raise ValueError("an input image is required")
    cmd = [bin_path or UPSCALE_BIN or mflux_cli("mflux-upscale-seedvr2"),
           "--image-path", image, "--output", output]
    if scale is not None:
        cmd += ["--resolution", str(scale)]
    return cmd


def generate_upscale(image, scale=None, output=None, keep_models=False):
    if not image:
        raise ValueError("an input image is required")
    if not os.path.exists(image):
        raise ValueError("input image not found: %s" % image)
    os.makedirs(OUT_DIR, exist_ok=True)
    output = output or os.path.join(OUT_DIR, "fxlla-upscale-%d.png" % int(time.time()))
    free_gpu("upscale", keep_models)
    cmd = build_upscale_command(image, output, scale=scale)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=_env())
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr.strip() or "image upscale failed")[-800:])
    validate_output(output)
    return output


def cmd_image(args):
    path = generate_image(
        args.prompt, model=args.model, steps=args.steps, seed=args.seed,
        width=args.width, height=args.height, aspect=args.aspect,
        quantize=args.quantize, low_ram=args.low_ram, metadata=args.metadata,
        output=args.output, keep_models=args.keep_models)
    print(path)


def cmd_video(args):
    path = generate_video(
        args.prompt, stage=args.stage, frames=args.frames, frame_rate=args.frame_rate,
        width=args.width, height=args.height, seed=args.seed, low_ram=args.low_ram,
        model=args.model, output=args.output, keep_models=args.keep_models)
    print(path)


def cmd_voice(args):
    path = generate_speech(
        args.text, ref=args.ref, lang=args.lang, model=args.model,
        speed=args.speed, output=args.output, keep_models=args.keep_models)
    print(path)


def cmd_edit(args):
    path = generate_edit(
        args.prompt, args.image, seed=args.seed, quantize=args.quantize,
        output=args.output, keep_models=args.keep_models)
    print(path)


def cmd_upscale(args):
    path = generate_upscale(
        args.image, scale=args.scale, output=args.output,
        keep_models=args.keep_models)
    print(path)


def cmd_models(_args):
    for name in sorted(MODELS):
        spec = MODELS[name]
        default = " (default)" if name == DEFAULT_MODEL else ""
        print("%-16s %s%s" % (name, spec["cli"], default))


def _summary(args):
    # Short label for `fxlla media jobs`, whatever the generator's input is.
    for field in ("prompt", "text", "image"):
        value = getattr(args, field, None)
        if value:
            return value if len(value) <= 60 else value[:57] + "..."
    return ""


def cmd_jobs(args):
    if getattr(args, "prune", False):
        print("removed %d finished job(s)" % jobs.prune())
        return
    records = jobs.listing()
    if getattr(args, "json", False):
        print(json.dumps(records))
        return
    if not records:
        print("(no media jobs)")
        return
    for rec in records:
        print(jobs.describe(rec))


def cmd_job(args):
    rec = jobs.get(args.id)
    if rec is None:
        sys.exit("unknown job: %s" % args.id)
    if getattr(args, "json", False):
        print(json.dumps(rec))
        return
    print(jobs.describe(rec))
    if rec.get("error"):
        print(rec["error"])


def cmd_cancel(args):
    rec = jobs.cancel(args.id)
    if rec is None:
        sys.exit("unknown job: %s" % args.id)
    print(jobs.describe(rec))


def main():
    p = argparse.ArgumentParser(prog="fxlla-media")
    sub = p.add_subparsers(dest="cmd", required=True)
    im = sub.add_parser("image")
    im.add_argument("prompt")
    im.add_argument("--model", "-m")
    im.add_argument("--steps", type=int)
    im.add_argument("--seed", type=int)
    im.add_argument("--width", type=int)
    im.add_argument("--height", type=int)
    im.add_argument("--aspect")
    im.add_argument("--quantize", "-q", type=int, default=8)
    im.add_argument("--low-ram", action="store_true")
    im.add_argument("--metadata", action="store_true")
    im.add_argument("--output", "-o")

    vi = sub.add_parser("video")
    vi.add_argument("prompt")
    vi.add_argument("--stage", choices=VIDEO_STAGES, default=DEFAULT_STAGE)
    vi.add_argument("--frames", type=int)
    vi.add_argument("--frame-rate", type=int, default=DEFAULT_FRAME_RATE)
    vi.add_argument("--width", type=int)
    vi.add_argument("--height", type=int)
    vi.add_argument("--seed", type=int)
    vi.add_argument("--model", "-m")
    vi.add_argument("--low-ram", action="store_true")
    vi.add_argument("--output", "-o")

    vo = sub.add_parser("voice")
    vo.add_argument("text")
    vo.add_argument("--ref")
    vo.add_argument("--lang")
    vo.add_argument("--model", "-m")
    vo.add_argument("--speed", type=float, default=1.0)
    vo.add_argument("--output", "-o")

    ed = sub.add_parser("edit")
    ed.add_argument("prompt")
    ed.add_argument("--image", required=True)
    ed.add_argument("--seed", type=int)
    ed.add_argument("--quantize", "-q", type=int, default=8)
    ed.add_argument("--output", "-o")

    up = sub.add_parser("upscale")
    up.add_argument("--image", required=True)
    up.add_argument("--scale",
                    help="target shortest edge in pixels or a factor, e.g. 2x")
    up.add_argument("--output", "-o")

    for sp in (im, vi, vo, ed, up):
        sp.add_argument("--keep-models", action="store_true",
                        help="do not free the gateway's resident models first")
        # dest is not 'async': that is a Python keyword.
        sp.add_argument("--async", dest="run_async", action="store_true",
                        help="submit as a background job and print its id")
        sp.add_argument("--skip-quality", action="store_true",
                        help="accept output that fails the content checks")
        sp.add_argument("--yes", "-y", dest="yes", action="store_true",
                        help="authorize downloading any missing weights")

    sub.add_parser("models")
    # Introspection for doctor and setup: the ONE place voice-interpreter
    # resolution lives, so shell never re-implements it.
    sub.add_parser("voice-python")
    jl = sub.add_parser("jobs")
    jl.add_argument("-j", "--json", action="store_true")
    jl.add_argument("--prune", action="store_true",
                    help="delete finished job records and their logs")
    jg = sub.add_parser("job")
    jg.add_argument("id")
    jg.add_argument("-j", "--json", action="store_true")
    jc = sub.add_parser("cancel")
    jc.add_argument("id")
    args = p.parse_args()
    global SKIP_QUALITY
    SKIP_QUALITY = bool(getattr(args, "skip_quality", False))
    # A generator downloads its weights on first use. Ask before that happens,
    # here rather than in the shell wrapper, because a background job and an MCP
    # tool call both re-invoke this module directly.
    if args.cmd in ("image", "video", "voice", "edit", "upscale"):
        if not getattr(args, "yes", False):
            weights.require(args.cmd, getattr(args, "model", None) or DEFAULT_MODEL
                            if args.cmd == "image" else None)
    if getattr(args, "run_async", False):
        # Reuse the invocation verbatim (minus the flag) as the job's argv, so a
        # background job runs exactly the same generator as the direct call.
        argv = [a for a in sys.argv[1:] if a != "--async"]
        print(jobs.submit(args.cmd, argv, _summary(args))["id"])
        return
    {"image": cmd_image, "video": cmd_video, "voice": cmd_voice,
     "edit": cmd_edit, "upscale": cmd_upscale, "models": cmd_models,
     "voice-python": lambda _a: print(resolved_voice_python()),
     "jobs": cmd_jobs, "job": cmd_job, "cancel": cmd_cancel}[args.cmd](args)


if __name__ == "__main__":
    if not STORE or not os.path.isdir(STORE):
        sys.exit("FXLLA_STORE is not set or does not exist")
    main()
