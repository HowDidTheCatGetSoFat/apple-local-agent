#!/usr/bin/env python3
"""fxlla voice backend: text to speech via mlx-audio (Chatterbox).

Runs under the interpreter named by FXLLA_VOICE_PYTHON, which must have
`mlx_audio` installed. It is invoked as a subprocess by media/generate.py and is
deliberately NOT imported by the rest of fxlla, so fxlla itself never depends on
mlx_audio. Writes a 24 kHz mono PCM WAV and prints its path.

The Chatterbox multilingual model ships no speaker conditionals, so a reference
voice wav (--ref) is required; it sets the timbre and accent.
"""
import argparse
import sys
import wave

import numpy as np
from mlx_audio.tts.utils import load_model

SAMPLE_RATE = 24000


def main():
    p = argparse.ArgumentParser(prog="fxlla-voice-backend")
    p.add_argument("--text", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--ref", required=True)
    p.add_argument("--lang", default="en")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--exaggeration", type=float, default=0.1)
    p.add_argument("--cfg-weight", type=float, default=0.5)
    a = p.parse_args()

    model = load_model(a.model)
    segments = list(model.generate(
        text=a.text, lang_code=a.lang, ref_audio=a.ref, speed=a.speed,
        exaggeration=a.exaggeration, cfg_weight=a.cfg_weight, verbose=False))
    if not segments:
        sys.exit("no audio was generated")

    audio = np.concatenate([np.asarray(s.audio).reshape(-1) for s in segments])
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    # Use the rate the model reports so the WAV header matches the samples even
    # if a future build generates at a different native rate.
    sample_rate = int(getattr(segments[0], "sample_rate", None) or SAMPLE_RATE)
    with wave.open(a.output, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    print(a.output)


if __name__ == "__main__":
    main()
