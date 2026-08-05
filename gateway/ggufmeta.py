#!/usr/bin/env python3
"""What a GGUF says about itself, read from its own header.

One fact, one reader. `bin/fxlla _backend` needs the context window to start
llama-server with, and the gateway needs the same number to report in
/v1/models - if those two disagree, opencode's meter and its auto-compaction
run against a window the backend is not actually serving. So neither computes
it: both call here.

Standard library only, and it reads a few megabytes rather than the file: the
metadata block sits at the front, and these weigh tens of gigabytes.
"""

import os
import struct
import sys

# GGUF value types that are a fixed-width scalar, and their struct codes.
_SCALARS = {0: ("B", 1), 1: ("b", 1), 2: ("H", 2), 3: ("h", 2), 4: ("I", 4),
            5: ("i", 4), 6: ("f", 4), 7: ("?", 1), 10: ("Q", 8), 11: ("q", 8),
            12: ("d", 8)}
_STRING, _ARRAY = 8, 9

# How much to pull per refill. Not a limit: the reader asks for more whenever
# it runs out. A fixed prefix was the first version and it was wrong within a
# day - one publisher's build of the same architecture carries a metadata block
# past 8 MB, and the model simply fell back to the global default with nothing
# said. There is no size that is safely large here, so there is no size.
_CHUNK = 4 << 20

# A model whose header has not ended by here is not one of ours; the cap exists
# so a corrupt length field cannot read a 20 GB file into memory. 32 MB is well
# past the largest real metadata block seen here (a 12 MB vocabulary) and small
# enough that hitting the cap is quick rather than a dozen seconds of CPU.
_MAX_HEAD = 32 << 20

# Metadata pairs a real GGUF declares. The count is read from the file, so a
# corrupt one can claim billions and send the loop nowhere useful.
_MAX_PAIRS = 4096


class _Reader:
    """Walks a GGUF header, pulling more of the file only when it runs out."""

    def __init__(self, fh):
        self.fh, self.buf, self.off = fh, b"", 0

    def take(self, n):
        while self.off + n > len(self.buf):
            if len(self.buf) >= _MAX_HEAD:
                raise EOFError("GGUF header exceeds %d bytes" % _MAX_HEAD)
            more = self.fh.read(_CHUNK)
            if not more:
                raise EOFError("GGUF header runs past the end of the file")
            self.buf += more
        chunk = self.buf[self.off:self.off + n]
        self.off += n
        return chunk

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def u64(self):
        return struct.unpack("<Q", self.take(8))[0]

    def string(self):
        return self.take(self.u64()).decode("utf-8", "replace")

    def skip(self, kind):
        """Step over a value without building it. Vocabularies are huge."""
        if kind == _STRING:
            self.take(self.u64())
        elif kind == _ARRAY:
            inner, count = self.u32(), self.u64()
            if inner == _ARRAY:
                raise ValueError("nested arrays are not read here")
            # Refuse an array that cannot fit before stepping through it. A
            # declared count is a number in a file, not a fact: a crafted
            # header claiming tens of millions of zero-length strings made the
            # per-element loop run for a dozen seconds of blocking CPU, and
            # this parser is on the path of every /v1/models call. The
            # smallest possible element is the 8-byte length prefix of an
            # empty string, so the cheapest honest bound is that.
            least = 8 if inner == _STRING else _SCALARS[inner][1]
            if count > _MAX_HEAD // max(1, least):
                raise ValueError("array of %d elements cannot fit a header" % count)
            if inner == _STRING:
                for _ in range(count):
                    self.take(self.u64())
            else:
                self.take(_SCALARS[inner][1] * count)
        else:
            self.take(_SCALARS[kind][1])

    def scalar(self, kind):
        code, size = _SCALARS[kind]
        return struct.unpack("<" + code, self.take(size))[0]


def metadata(path, keys):
    """The named metadata keys, as a dict of those that were present.

    Keys are matched by SUFFIX, because every one of them is namespaced by the
    architecture ("qwen35.context_length", "qwen2vl.context_length") and the
    architecture is exactly what a caller should not have to know."""
    with open(path, "rb") as fh:
        reader = _Reader(fh)
        if reader.take(4) != b"GGUF":
            raise ValueError("not a GGUF file: %s" % path)
        reader.u32()          # version
        reader.u64()          # tensor count
        pairs = reader.u64()
        if pairs > _MAX_PAIRS:
            raise ValueError("header declares %d metadata pairs" % pairs)
        wanted, found = tuple(keys), {}
        for _ in range(pairs):
            key = reader.string()
            kind = reader.u32()
            if key.endswith(wanted) and kind in _SCALARS:
                found[key] = reader.scalar(kind)
            else:
                reader.skip(kind)
            if len(found) == len(wanted):
                break
        return found


# Everything serve_plan needs to know, in one tuple, so the file is walked once.
#
# It used to call four separate readers - one per fact - and each reopened the
# file and scanned until it found ITS key. Most models declare no MTP head and
# no rope factor, and a key that is absent is only known to be absent at the END
# of the header, so those scans ran to completion every time. Three full walks
# of a 12 MB metadata block per model, 0.28 s each on this machine, 2.1 s across
# a store of fifteen - paid on every /v1/models call, which is what an editor
# asks for at startup.
_PLAN_KEYS = (".context_length", ".rope.scaling.original_context_length",
              ".rope.scaling.factor", ".nextn_predict_layers")

# Keyed by identity AND version: a path whose mtime or size changed is a
# different file. Bounded because a long-lived gateway sees every model in the
# store and nothing evicts on its own; at the cap it is cleared rather than
# aged, since the working set is a handful of models and a partial cache is
# still correct - every miss just re-reads the file it is about.
_FACTS_CACHE = {}
_FACTS_CACHE_MAX = 256


def _derive(meta):
    """(trained, declared, stretched, mtp) from one metadata dict.

    The precedence lives here rather than in each reader, so "which number is
    the trained window" has one answer that the plan and the individual
    accessors cannot drift apart on.
    """
    declared = trained = None
    stretched = False
    mtp = False
    for key, value in meta.items():
        if key.endswith(".rope.scaling.original_context_length") and value:
            trained = int(value)
        elif key.endswith(".context_length") and value:
            declared = int(value)
        elif key.endswith(".rope.scaling.factor"):
            stretched = float(value) > 1.0
        elif key.endswith(".nextn_predict_layers"):
            mtp = int(value) > 0
    return (trained or declared), declared, stretched, mtp


def _facts(path):
    """_derive() for a file, remembered until the file changes."""
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    hit = _FACTS_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        facts = _derive(metadata(path, _PLAN_KEYS))
    except (OSError, ValueError, EOFError, KeyError, struct.error):
        return None
    if len(_FACTS_CACHE) >= _FACTS_CACHE_MAX:
        _FACTS_CACHE.clear()
    _FACTS_CACHE[key] = facts
    return facts


def trained_context(path):
    """The window this model was actually trained for, or None.

    Not the same as the largest number it declares. A model whose GGUF bakes in
    rope scaling advertises the STRETCHED window as its context_length - a
    Qwen3.5 derivative here says 1048576, which is 262144 multiplied by a YaRN
    factor of 4 - and serving at the stretched size means accepting the quality
    loss the stretch costs, for a window nobody fills. The original length is
    the honest answer, and it is recorded right beside the factor.
    """
    try:
        meta = metadata(path, (".context_length",
                               ".rope.scaling.original_context_length"))
    except (OSError, ValueError, EOFError, KeyError, struct.error):
        return None
    for key, value in meta.items():
        if key.endswith(".rope.scaling.original_context_length") and value:
            return int(value)
    for key, value in meta.items():
        if key.endswith(".context_length") and value:
            return int(value)
    return None


def declared_context(path):
    """The largest window the file advertises, stretch included.

    This is what `-c 0` would give you and what the model card headlines. It is
    reachable only because rope scaling is baked in, so asking for it means
    accepting the attention quality that scaling costs - across a window almost
    nobody fills, at a KV cache that grows with every token of it.
    """
    try:
        meta = metadata(path, (".context_length",))
    except (OSError, ValueError, EOFError, KeyError, struct.error):
        return None
    for key, value in meta.items():
        if key.endswith(".context_length") and value:
            return int(value)
    return None


def rope_is_stretched(path):
    """Whether this GGUF bakes in a rope scaling factor above 1.

    llama.cpp honours what the file declares, so a model shipped with YaRN
    applies it at every context size - including the short ones, where it is
    pure loss. Serving at or below the trained window means the stretch buys
    nothing and should be turned off explicitly.
    """
    try:
        meta = metadata(path, (".rope.scaling.factor",))
    except (OSError, ValueError, EOFError, KeyError, struct.error):
        return False
    return any(float(v) > 1.0 for v in meta.values())


def has_mtp_head(path):
    """Whether this file carries a multi-token-prediction head.

    Some publishers ship two builds of the same weights at the same quant, one
    with the head and one without, and the only difference in the header is
    this key - the file names are a convention, not a guarantee. Measured on
    the pair here: 40.9 against 21.7 tokens/s on predictable text, 31.9 against
    20.6 on code. The plain build is flat across prompts because it is memory
    bound; the MTP one varies with how guessable the next tokens are, which is
    what speculation is.
    """
    try:
        meta = metadata(path, (".nextn_predict_layers",))
    except (OSError, ValueError, EOFError, KeyError, struct.error):
        return False
    return any(int(v) > 0 for v in meta.values())


def _entry(directory):
    """The weights file in a model directory, never the projector."""
    marker = os.path.join(directory, ".entry")
    try:
        with open(marker, encoding="utf-8") as fh:
            name = fh.read().strip()
        if name:
            return os.path.join(directory, name)
    except OSError:
        pass
    for name in sorted(os.listdir(directory)):
        # "mmproj" anywhere and in any case. One publisher writes
        # `mmproj-Model-F16.gguf`, another `Model.mmproj-Q8_0.gguf`; anchored,
        # the second reads as an ordinary weights file. Case-folded because the
        # pull side matches with `grep -i` and will happily fetch a `MMProj`
        # spelling that a case-sensitive test here would then hand to
        # llama-server as the model - a projector is not one and cannot load.
        #
        # sorted() is codepoint order, which is why bin/fxlla sorts its own
        # fallback under LC_ALL=C. Under a UTF-8 collation the two disagree
        # about which file comes first, and this function exists so that
        # "which file is the model" has exactly one answer.
        low = name.lower()
        if low.endswith(".gguf") and "mmproj" not in low:
            return os.path.join(directory, name)
    return None


def serve_plan(directory, cap, stretch=False):
    """(context, turn_rope_scaling_off, use_mtp) for the model in `directory`.

    `cap` bounds the context because the window is paid for in RAM whether or
    not it is used: llama-server allocates the KV cache up front, and a quarter
    of a million tokens costs gigabytes. Returns (None, False, False) when the
    file cannot be read, which leaves the caller on its own default rather than
    guessing.

    `stretch` opts into the window the file advertises rather than the one it
    was trained for - for a model shipped with YaRN that is the difference
    between 262144 and 1048576. It only RAISES the ceiling; whether the scaling
    stays on is decided by what is actually served, because below the trained
    length the stretch is a cost with nothing bought. So asking for stretch and
    then capping under the trained window quietly gets you neither, which is
    the correct answer rather than a special case.
    """
    path = _entry(directory) if os.path.isdir(directory) else directory
    if not path or not os.path.isfile(path):
        return None, False, False
    facts = _facts(path)
    if facts is None:
        return None, False, False
    trained, declared, stretched, mtp = facts
    if not trained:
        return None, False, mtp
    ceiling = (declared or trained) if stretch else trained
    served = min(ceiling, cap) if cap else ceiling
    # Only disabled where the stretch is not being used. Above the trained
    # window the scaling is the whole reason the context is reachable at all.
    return served, stretched and served <= trained, mtp


if __name__ == "__main__":
    # Called from bin/fxlla, which is shell and cannot read a binary header.
    # Prints "<context> <rope-off:0|1> <mtp:0|1>", or nothing at all when
    # unreadable so the caller keeps its own default.
    directory = sys.argv[1]
    cap = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 0
    stretch = len(sys.argv) > 3 and sys.argv[3] not in ("", "0", "false")
    context, rope_off, mtp = serve_plan(directory, cap, stretch)
    if context:
        print("%d %d %d" % (context, 1 if rope_off else 0, 1 if mtp else 0))
