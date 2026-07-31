#!/usr/bin/env python3
"""Perceptual checks on generated media.

A header check tells you a file is a WAV, not that it contains speech. The
failure modes that actually happen here - a TTS run that emits silence, a clip
distorted by a DC offset - produce a structurally valid file that passes
magic-byte validation and fails an ear.

These checks look at the content, and only flag what is unambiguously broken. A
false positive rejects a render the user asked for, which is worse than a miss, so
anything a caller could plausibly have wanted on purpose is accepted: a very short
clip, a single-frame video, a quiet passage, a tiny image. A file this module
cannot parse gets no verdict at all rather than a rejection.

Standard library only. `audioop` is not used: it was removed in Python 3.13.
"""
import array
import json
import os
import struct
import subprocess
import sys
import wave

# Cap on total samples read, so neither a long file nor a many-channel one can
# blow up memory. Frames are multiplied by channels, hence the division below.
MAX_SAMPLES = 48000 * 60 * 2  # a stereo minute at 48 kHz

# Fractions of full scale, all far below anything audible as content. Real
# generated speech measures a peak near 0.95 and an rms near 0.13.
SILENT_PEAK = 0.005      # a peak under 0.5 percent of full scale is silence
QUIET_RMS = 0.0005       # average level this low carries no signal
DC_OFFSET = 0.10         # a mean this far from zero means the waveform is skewed
MOSTLY_SILENT = 0.98     # share of windows with no energy (speech has pauses,
                         # but not 98 percent of them)
WINDOW_MS = 50


def skip_quality_checks():
    return os.environ.get("FXLLA_MEDIA_SKIP_QUALITY", "") not in ("", "0", "false")


def _samples(path):
    """Return (samples, full_scale, channels, rate), or None for an unsupported
    sample width.

    Samples are centered on zero regardless of width, and always live in an
    `array` rather than a list: a Python int costs ~46 bytes, which turns a
    many-channel file into hundreds of megabytes right after the caller freed the
    gateway's models to make room for the render.
    """
    with wave.open(path, "rb") as w:
        width = w.getsampwidth()
        channels = max(w.getnchannels(), 1)
        rate = w.getframerate()
        raw = w.readframes(min(w.getnframes(), MAX_SAMPLES // channels))
    if width == 1:
        # 8-bit WAV is unsigned, centered on 128.
        return array.array("h", (b - 128 for b in raw)), 128.0, channels, rate
    if width == 2:
        data = array.array("h")  # native endianness; little on every target here
        data.frombytes(raw[: len(raw) - (len(raw) % 2)])
        return data, 32768.0, channels, rate
    if width == 4:
        data = array.array("i")
        data.frombytes(raw[: len(raw) - (len(raw) % 4)])
        return data, 2147483648.0, channels, rate
    if width == 3:
        # 24-bit: sign-extend three little-endian bytes at a time.
        def signed24():
            for i in range(0, len(raw) - 2, 3):
                v = raw[i] | (raw[i + 1] << 8) | (raw[i + 2] << 16)
                yield v - 0x1000000 if v & 0x800000 else v
        return array.array("i", signed24()), 8388608.0, channels, rate
    return None  # unusual width: no opinion rather than a wrong one


def check_wav(path):
    """Problems with a generated WAV, as a list of human-readable strings."""
    try:
        parsed = _samples(path)
    except Exception as exc:
        # Cannot parse means no opinion, never a rejection: Python's `wave` reads
        # PCM only, and a float32 WAV (what many TTS stacks write) is perfectly
        # valid output that this module simply cannot measure.
        print("audio quality check skipped for %s: %s" % (path, exc), file=sys.stderr)
        return []
    if parsed is None:
        return []
    samples, full_scale, channels, rate = parsed
    if not samples:
        return ["contains no audio frames"]

    low, high = min(samples), max(samples)
    if low == high:
        # A constant waveform carries nothing, whatever value it sits at. This
        # also catches an all-zero-bytes 8-bit file, which centers to -128 and
        # would otherwise look like full-scale distortion.
        return ["is silent (a constant sample value)"]

    peak = max(high, -low) / full_scale
    mean = sum(samples) / len(samples) / full_scale
    rms = (sum(s * s for s in samples) / len(samples)) ** 0.5 / full_scale

    problems = []
    if peak < SILENT_PEAK:
        return ["is silent (peak %.4f of full scale)" % peak]
    if rms < QUIET_RMS:
        problems.append("carries almost no signal (rms %.5f of full scale)" % rms)
    if abs(mean) > DC_OFFSET:
        problems.append("has a large DC offset (mean %.3f of full scale), which "
                        "usually means the waveform is distorted" % mean)

    # Windowed energy: a clip that is silent almost everywhere is a failed render
    # even when a click at one end lifts the peak. The trailing partial window is
    # included, because dropping it made the verdict depend on where in the file
    # the content happened to sit.
    per_window = max(int(rate * channels * WINDOW_MS / 1000), 1)
    windows = quiet = 0
    for start in range(0, len(samples), per_window):
        chunk = samples[start:start + per_window]
        windows += 1
        if (sum(s * s for s in chunk) / len(chunk)) ** 0.5 / full_scale < QUIET_RMS:
            quiet += 1
    if windows >= 4 and quiet / windows > MOSTLY_SILENT:
        problems.append("is silent for %.0f percent of its length"
                        % (100.0 * quiet / windows))
    return problems


def check_png(path):
    """Problems with a generated PNG. Reads IHDR only; no pixel decoding, so a
    blank-but-well-formed image is not detected."""
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
           "-show_entries",
           "stream=nb_frames,width,height,r_frame_rate,duration:format=duration",
           "-of", "json", path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                              stdin=subprocess.DEVNULL)
    except Exception:
        return None  # not installed, not permitted, timed out: all mean no data
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


def image_facts(path):
    """{width, height, bytes} from the PNG header, or {} when unreadable.

    Returned alongside the path so a caller does not have to shell out to
    inspect what it just asked for - which a real model did, repeatedly."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(24)
    except OSError:
        return {}
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return {"bytes": size}
    width, height = struct.unpack(">II", head[16:24])
    return {"width": width, "height": height, "bytes": size}


def video_facts(path):
    """What the produced video actually IS: {duration_s, frames, fps, width,
    height}, with whatever ffprobe could not answer left out.

    Measured, not derived from the request: a caller reading back its own
    --frames and --frame-rate can be wrong about the result and say so
    confidently. A real model did exactly that - reported 49 frames at 24 fps
    as "about 10 seconds" (it is 2.04) and called an unmet 4-8 second
    requirement satisfied."""
    info = _ffprobe(path)
    if not info:
        return {}
    streams = info.get("streams") or []
    stream = streams[0] if streams else {}
    facts = {}
    for key, name in (("width", "width"), ("height", "height")):
        if stream.get(key):
            facts[name] = int(stream[key])
    try:
        facts["frames"] = int(stream.get("nb_frames"))
    except (TypeError, ValueError):
        pass
    rate = stream.get("r_frame_rate") or ""
    if "/" in rate:
        num, den = rate.split("/", 1)
        try:
            if float(den):
                facts["fps"] = round(float(num) / float(den), 3)
        except ValueError:
            pass
    # The stream's own duration when present, else the container's.
    for source in (stream.get("duration"), (info.get("format") or {}).get("duration")):
        try:
            facts["duration_s"] = round(float(source), 2)
            break
        except (TypeError, ValueError):
            continue
    return facts


def check_video(path):
    """Problems with a generated video.

    Deliberately narrow. Frame count and duration are caller-controlled
    (`--frames`, `--frame-rate`), so a one-frame or fraction-of-a-second clip is
    something the user asked for, not a defect. Only a container with no usable
    video at all is flagged. Without ffprobe there is nothing to inspect beyond
    what validate_video_output already checked, so this returns no problems rather
    than guessing.
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
    try:
        frames = int(stream.get("nb_frames"))
    except (TypeError, ValueError):
        frames = None  # absent or "N/A": common and not a defect
    if frames == 0:
        problems.append("holds no frames")
    return problems


def report(kind, path, problems):
    """A single message describing what is wrong, or None when nothing is."""
    if not problems:
        return None
    return "%s at %s %s" % (kind, path, "; and ".join(problems))
