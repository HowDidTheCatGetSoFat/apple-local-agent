#!/usr/bin/env python3
"""Perceptual checks on generated media.

A header check tells you a file is a WAV, not that it contains speech. The
failure modes that actually happen here - a TTS run that emits silence, a render
that writes a fraction of a second, a clip distorted by a DC offset - all produce
a structurally valid file that passes magic-byte validation and fails an ear.

These checks look at the content. They are deliberately conservative: they exist
to catch obvious garbage, not to judge quality, because a false positive would
reject a render the user actually wanted. Set FXLLA_MEDIA_SKIP_QUALITY=1 to turn
them off.

Standard library only. `audioop` is not used: it was removed in Python 3.13.
"""
import array
import json
import os
import struct
import subprocess
import wave

# Read at most this many frames so a long file cannot blow up memory.
MAX_FRAMES = 48000 * 60  # a minute at 48 kHz

# Fractions of full scale. Chosen well below anything audible as content.
SILENT_PEAK = 0.005      # a peak under 0.5 percent of full scale is silence
QUIET_RMS = 0.0005       # average level this low carries no signal
DC_OFFSET = 0.10         # a mean this far from zero means the waveform is skewed
MOSTLY_SILENT = 0.98     # share of windows with no energy (speech has pauses,
                         # but not 98 percent of them)
WINDOW_MS = 50


def skip_quality_checks():
    return os.environ.get("FXLLA_MEDIA_SKIP_QUALITY", "") not in ("", "0", "false")


def _samples(path):
    """Return (samples, full_scale, channels, rate) or None if unreadable.

    Samples are plain ints, centered on zero regardless of sample width.
    """
    with wave.open(path, "rb") as w:
        width = w.getsampwidth()
        channels = w.getnchannels()
        rate = w.getframerate()
        raw = w.readframes(min(w.getnframes(), MAX_FRAMES))
    if not raw:
        return None
    if width == 1:
        # 8-bit WAV is unsigned, centered on 128.
        data = array.array("B", raw)
        return [s - 128 for s in data], 128.0, channels, rate
    if width == 2:
        data = array.array("h")
        data.frombytes(raw[: len(raw) - (len(raw) % 2)])
        return data, 32768.0, channels, rate
    if width == 4:
        data = array.array("i")
        data.frombytes(raw[: len(raw) - (len(raw) % 4)])
        return data, 2147483648.0, channels, rate
    if width == 3:
        # 24-bit: sign-extend three little-endian bytes at a time.
        out = []
        for i in range(0, len(raw) - 2, 3):
            v = raw[i] | (raw[i + 1] << 8) | (raw[i + 2] << 16)
            out.append(v - 0x1000000 if v & 0x800000 else v)
        return out, 8388608.0, channels, rate
    return None  # unusual width: no opinion rather than a wrong one


def check_wav(path):
    """Problems with a generated WAV, as a list of human-readable strings."""
    try:
        parsed = _samples(path)
    except Exception as exc:  # wave raises wave.Error, struct.error, EOFError, ...
        return ["could not read as WAV audio (%s)" % exc]
    if parsed is None:
        return []
    samples, full_scale, channels, rate = parsed
    if not samples:
        return ["contains no audio frames"]

    peak = max(max(samples), -min(samples)) / full_scale
    mean = sum(samples) / len(samples) / full_scale
    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5 / full_scale

    problems = []
    if peak < SILENT_PEAK:
        problems.append("is silent (peak %.4f of full scale)" % peak)
        return problems  # everything else follows from silence
    if rms < QUIET_RMS:
        problems.append("carries almost no signal (rms %.5f of full scale)" % rms)
    if abs(mean) > DC_OFFSET:
        problems.append("has a large DC offset (mean %.3f of full scale), which "
                        "usually means the waveform is distorted" % mean)

    # Windowed energy: a clip that is silent almost everywhere is a failed render
    # even when a click at one end lifts the peak.
    per_window = max(int(rate * channels * WINDOW_MS / 1000), 1)
    windows = quiet = 0
    for start in range(0, len(samples) - per_window + 1, per_window):
        windows += 1
        chunk = samples[start:start + per_window]
        if (sum(s * s for s in chunk) / len(chunk)) ** 0.5 / full_scale < QUIET_RMS:
            quiet += 1
    if windows >= 4 and quiet / windows > MOSTLY_SILENT:
        problems.append("is silent for %.0f percent of its length"
                        % (100.0 * quiet / windows))
    return problems


def check_png(path):
    """Problems with a generated PNG. Reads IHDR only; no pixel decoding."""
    with open(path, "rb") as f:
        head = f.read(33)
    if len(head) < 24 or head[12:16] != b"IHDR":
        return ["has no readable PNG header"]
    width, height = struct.unpack(">II", head[16:24])
    if width == 0 or height == 0:
        return ["reports zero dimensions (%dx%d)" % (width, height)]
    return []


def _ffprobe(path):
    """Video stream facts via ffprobe, or None when it is unavailable."""
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=nb_frames,duration,width,height",
           "-show_entries", "format=duration", "-of", "json", path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


def check_video(path):
    """Problems with a generated video.

    Best effort: without ffprobe there is nothing to inspect beyond the container,
    which validate_video_output already checked, so this returns no problems
    rather than guessing.
    """
    info = _ffprobe(path)
    if not info:
        return []
    streams = info.get("streams") or []
    if not streams:
        return ["has no video stream"]
    stream = streams[0]
    problems = []
    if not stream.get("width") or not stream.get("height"):
        problems.append("has a video stream with no dimensions")

    frames = stream.get("nb_frames")
    try:
        frames = int(frames)
    except (TypeError, ValueError):
        frames = None
    if frames is not None and frames <= 1:
        problems.append("holds %d frame(s), so it is a still image" % frames)

    duration = stream.get("duration") or (info.get("format") or {}).get("duration")
    try:
        duration = float(duration)
    except (TypeError, ValueError):
        duration = None
    if duration is not None and duration < 0.1:
        problems.append("is %.3fs long, which is not a clip" % duration)
    return problems


def report(kind, path, problems):
    """A single message describing what is wrong, or None when nothing is."""
    if not problems:
        return None
    return "%s at %s %s" % (kind, path, "; and ".join(problems))
