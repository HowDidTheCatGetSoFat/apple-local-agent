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
  generate.py models          list the supported image models
"""
import argparse
import os
import subprocess
import sys
import time

STORE = os.environ.get("FXLLA_STORE", "")
OUT_DIR = os.environ.get("FXLLA_MEDIA_OUT") or os.path.join(STORE, "media")
DEFAULT_MODEL = os.environ.get("FXLLA_MEDIA_MODEL", "z-image-turbo")
VIDEO_BIN = os.environ.get("FXLLA_VIDEO_BIN", "ltx-2-mlx")

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


def build_command(spec, prompt, output, steps=None, seed=None, width=None,
                  height=None, aspect=None, quantize=8, low_ram=False, metadata=False):
    """Assemble the mflux-cv argument vector for one image generation."""
    cmd = [spec["cli"]]
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


def generate_image(prompt, model=None, steps=None, seed=None, width=None,
                   height=None, aspect=None, quantize=8, low_ram=False,
                   metadata=False, output=None):
    if not prompt:
        raise ValueError("prompt is required")
    model = model or DEFAULT_MODEL
    spec = MODELS.get(model)
    if spec is None:
        raise ValueError("unknown model '%s'; try one of: %s"
                         % (model, ", ".join(sorted(MODELS))))
    os.makedirs(OUT_DIR, exist_ok=True)
    output = output or os.path.join(OUT_DIR, "fxlla-%s-%d.png" % (model, int(time.time())))
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


def generate_video(prompt, stage=DEFAULT_STAGE, frames=None,
                   frame_rate=DEFAULT_FRAME_RATE, width=None, height=None,
                   seed=None, low_ram=False, model=None, output=None):
    if not prompt:
        raise ValueError("prompt is required")
    os.makedirs(OUT_DIR, exist_ok=True)
    output = output or os.path.join(OUT_DIR, "fxlla-video-%d.mp4" % int(time.time()))
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
    return [python or VOICE_PYTHON, backend or VOICE_BACKEND,
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


def generate_speech(text, ref=None, lang=None, model=None, speed=1.0, output=None):
    if not text:
        raise ValueError("text is required")
    os.makedirs(OUT_DIR, exist_ok=True)
    output = output or os.path.join(OUT_DIR, "fxlla-voice-%d.wav" % int(time.time()))
    cmd = build_voice_command(text, output, ref or VOICE_REF, model=model,
                              lang=lang, speed=speed)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=_env())
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr.strip() or "voice generation failed")[-800:])
    validate_wav_output(output)
    return output


def cmd_image(args):
    path = generate_image(
        args.prompt, model=args.model, steps=args.steps, seed=args.seed,
        width=args.width, height=args.height, aspect=args.aspect,
        quantize=args.quantize, low_ram=args.low_ram, metadata=args.metadata,
        output=args.output)
    print(path)


def cmd_video(args):
    path = generate_video(
        args.prompt, stage=args.stage, frames=args.frames, frame_rate=args.frame_rate,
        width=args.width, height=args.height, seed=args.seed, low_ram=args.low_ram,
        model=args.model, output=args.output)
    print(path)


def cmd_voice(args):
    path = generate_speech(
        args.text, ref=args.ref, lang=args.lang, model=args.model,
        speed=args.speed, output=args.output)
    print(path)


def cmd_models(_args):
    for name in sorted(MODELS):
        spec = MODELS[name]
        default = " (default)" if name == DEFAULT_MODEL else ""
        print("%-16s %s%s" % (name, spec["cli"], default))


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

    sub.add_parser("models")
    args = p.parse_args()
    {"image": cmd_image, "video": cmd_video, "voice": cmd_voice,
     "models": cmd_models}[args.cmd](args)


if __name__ == "__main__":
    if not STORE or not os.path.isdir(STORE):
        sys.exit("FXLLA_STORE is not set or does not exist")
    main()
