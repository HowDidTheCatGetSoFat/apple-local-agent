"""Passive request metrics for the fxlla gateway.

Time to first token and tokens per second are derived from real proxied
traffic, with no synthetic probe, and appended to the same rolling time-series
(`stats.jsonl`) that `fxlla stats` writes and the menu bar app renders.

Standard library only. The functions here are pure and independently testable;
the gateway feeds streamed bytes to StreamMetrics and calls append_sample once
per completed request.
"""
import json
import os
import threading
import time

# Appends come from many gateway handler threads in one process. Serializing
# append-plus-trim here removes the window where a trim's os.replace could drop
# a concurrent append.
_APPEND_LOCK = threading.Lock()


def stats_file():
    """Path of the stats time-series, matching the CLI's STATS_FILE.

    Honours FXLLA_STATS_FILE, then XDG_STATE_HOME, then ~/.local/state.
    """
    env = os.environ.get("FXLLA_STATS_FILE")
    if env:
        return env
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "fxlla", "stats.jsonl")


def is_completion_path(path):
    """True for the generative endpoints worth timing (chat and legacy text
    completions); false for embeddings, models, and health."""
    p = path.split("?", 1)[0].rstrip("/")
    return p.endswith("/chat/completions") or p.endswith("/completions")


def _delta_text(obj):
    """Generated text carried by one streamed chunk, chat or legacy completion.

    Chat streams put it under choices[].delta.content; legacy completions put
    it under choices[].text. Only string pieces count.
    """
    out = []
    for ch in obj.get("choices", []) or []:
        delta = ch.get("delta")
        if isinstance(delta, dict):
            piece = delta.get("content")
            if isinstance(piece, str):
                out.append(piece)
        text = ch.get("text")
        if isinstance(text, str):
            out.append(text)
    return "".join(out)


def _usage_completion_tokens(obj):
    usage = obj.get("usage")
    if isinstance(usage, dict):
        ct = usage.get("completion_tokens")
        if isinstance(ct, int):
            return ct
    return None


# Above this, the number is describing the transport rather than the model:
# nothing decoding locally on Apple Silicon approaches it, so a rate past it
# means the stream arrived in one piece and the timestamps collapsed.
IMPLAUSIBLE_TPS = 2000


class StreamMetrics:
    """Accumulate streamed SSE bytes and track first token and token count.

    The token count approximates one token per streamed delta, matching how the
    active probe counts. When the server emits a trailing usage chunk
    (stream_options.include_usage), its exact completion_tokens takes over.
    """

    def __init__(self, start):
        self.start = start          # time.monotonic() at request dispatch
        self.first = None           # time.monotonic() of the first token
        self.deltas = 0
        self.usage_tokens = None
        self._buf = ""

    def feed(self, chunk_bytes):
        self._buf += chunk_bytes.decode("utf-8", "ignore")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._line(line.strip())

    def _line(self, line):
        if not line.startswith("data:"):
            return
        data = line[5:].strip()
        if not data or data == "[DONE]":
            return
        try:
            obj = json.loads(data)
        except ValueError:
            return
        ct = _usage_completion_tokens(obj)
        if ct is not None:
            self.usage_tokens = ct
        if _delta_text(obj):
            if self.first is None:
                self.first = time.monotonic()
            self.deltas += 1

    def result(self, end):
        """Return (ttft_ms, tps, tokens); ttft/tps are None when unmeasurable.

        A decode rate needs the tokens to have ARRIVED spread over time. When a
        response comes back buffered - one read carrying the whole stream - all
        the deltas are parsed microseconds apart, so the first-token timestamp
        lands next to the last and the division explodes. One real measurement
        here reported 1,865,386 tokens/s that way, and it dragged the median it
        was pooled into with it.

        There is no local decode anywhere near IMPLAUSIBLE_TPS on this
        hardware, so a computed rate above it is a statement about buffering
        rather than about the model. Reported as unmeasurable instead: fewer
        honest samples beat more invented ones, and the caller already knows
        how to skip a None.
        """
        tokens = self.usage_tokens if self.usage_tokens is not None else self.deltas
        ttft_ms = int((self.first - self.start) * 1000) if self.first else None
        gen = (end - self.first) if self.first else 0
        tps = None
        # Two arrivals at minimum: a rate is measured BETWEEN token arrivals,
        # and one of them times nothing.
        if gen > 0 and tokens and self.deltas > 1:
            rate = tokens / gen
            if rate <= IMPLAUSIBLE_TPS:
                tps = round(rate, 1)
        return ttft_ms, tps, tokens


def usage_from_json(body_bytes):
    """completion_tokens from a non-streamed response body, or None."""
    try:
        obj = json.loads(body_bytes)
    except (ValueError, TypeError):
        return None
    return _usage_completion_tokens(obj)


def build_sample(ts, model, engine, ram_mb, ttft_ms, tps):
    """One time-series row, shaped like the CLI probe plus a source marker."""
    return {
        "ts": int(ts),
        "model": model,
        "engine": engine,
        "ram_mb": int(ram_mb),
        "ttft_ms": ttft_ms,
        "tps": tps,
        "source": "gateway",
    }


def _maybe_trim(path, cap):
    """Keep the newest `cap` lines. Guarded by size so most appends do no I/O."""
    try:
        if os.path.getsize(path) < cap * 120:
            return
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= cap:
            return
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines[-cap:])
        os.replace(tmp, path)
    except OSError:
        pass


def append_sample(path, sample, cap=5000):
    """Append one JSONL sample and trim the file to the newest `cap` lines."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    line = json.dumps(sample) + "\n"
    with _APPEND_LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
        _maybe_trim(path, cap)
