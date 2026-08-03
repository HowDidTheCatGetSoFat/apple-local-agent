#!/usr/bin/env bash
# What context window a gguf model is actually served with.
#
# One number for every gguf was wrong in both directions: it served a 27B
# trained to 262k at 8k, and asked a 7B for more than it had ever seen. The
# window is read from each model's own header now, capped by FXLLA_CTX, and
# these check that what bin/fxlla hands llama-server is that number - the
# module can be right and the wiring still wrong.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FXLLA="$ROOT/bin/fxlla"
fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

STORE="$(mktemp -d)"
BIN="$(mktemp -d)"
trap 'rm -rf "$STORE" "$BIN"' EXIT

cat > "$BIN/llama-server" <<'STUB'
#!/bin/sh
echo "$@"
STUB
chmod +x "$BIN/llama-server"

# Real GGUF headers, written byte by byte: the point is that the header is
# parsed, and an empty file would only ever exercise the fallback.
write_gguf() { # <path> <context> [original_context] [rope_factor] [mtp_layers]
  python3 - "$@" <<'PY'
import struct, sys
path, ctx = sys.argv[1], int(sys.argv[2])
original = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else None
factor = float(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] else None
mtp = int(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] else None

def s(text):
    raw = text.encode()
    return struct.pack("<Q", len(raw)) + raw

pairs = [(s("arch.context_length") + struct.pack("<I", 4) + struct.pack("<I", ctx))]
if original:
    pairs.append(s("arch.rope.scaling.original_context_length")
                 + struct.pack("<I", 4) + struct.pack("<I", original))
if factor:
    pairs.append(s("arch.rope.scaling.factor") + struct.pack("<I", 6)
                 + struct.pack("<f", factor))
if mtp:
    pairs.append(s("arch.nextn_predict_layers") + struct.pack("<I", 4)
                 + struct.pack("<I", mtp))
head = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", len(pairs))
open(path, "wb").write(head + b"".join(pairs))
PY
}

model() { # <alias> ; makes $STORE/models/<alias> and echoes the dir
  local d="$STORE/models/$1"
  mkdir -p "$d"
  echo gguf > "$d/.engine"
  echo "weights.gguf" > "$d/.entry"
  echo "$d"
}

launch() { PATH="$BIN:$PATH" FXLLA_STORE="$STORE" bash "$FXLLA" _backend "$1" 9999; }
ctx_of() { printf '%s\n' "$1" | tr ' ' '\n' | grep -A 1 -x -- '-c' | tail -1; }

# --- a small model is not inflated to the ceiling ----------------------------
# The old behaviour asked every model for the same window, which for anything
# trained below it is a request the model never saw in training.
d="$(model small)"; write_gguf "$d/weights.gguf" 4096
out="$(FXLLA_CTX=32768 launch small)"
if [ "$(ctx_of "$out")" = 4096 ]; then pass "a 4k model is served 4k, not the ceiling"
else fail "a 4k model is served 4k, not the ceiling (got: $(ctx_of "$out"))"; fi

# --- a large model gets its own window, up to the ceiling --------------------
d="$(model big)"; write_gguf "$d/weights.gguf" 262144
out="$(FXLLA_CTX=262144 launch big)"
if [ "$(ctx_of "$out")" = 262144 ]; then pass "a 262k model is served 262k when allowed"
else fail "a 262k model is served 262k when allowed (got: $(ctx_of "$out"))"; fi

out="$(FXLLA_CTX=32768 launch big)"
if [ "$(ctx_of "$out")" = 32768 ]; then pass "the ceiling still bounds it"
else fail "the ceiling still bounds it (got: $(ctx_of "$out"))"; fi

# --- a stretched model is served its TRAINED window, with the stretch off ----
# The file advertises the YaRN-multiplied window. Serving there buys degraded
# attention across a context nobody fills, and llama.cpp applies the scaling at
# every size unless told not to - so below the original length it is pure loss.
d="$(model yarn)"; write_gguf "$d/weights.gguf" 1048576 262144 4.0
out="$(FXLLA_CTX=1048576 launch yarn)"
if [ "$(ctx_of "$out")" = 262144 ]; then pass "the trained window wins over the stretched one"
else fail "the trained window wins over the stretched one (got: $(ctx_of "$out"))"; fi
case "$out" in
  *"--rope-scaling none"*) pass "the baked-in stretch is turned off";;
  *) fail "the baked-in stretch is turned off (got: $out)";;
esac

# --- a model that does not stretch is left alone -----------------------------
out="$(FXLLA_CTX=262144 launch small)"
case "$out" in
  *--rope-scaling*) fail "no --rope-scaling for a model without one";;
  *) pass "no --rope-scaling for a model without one";;
esac

# --- a build with an MTP head drafts against itself --------------------------
# Two builds of the same weights at the same quant ship side by side, and the
# file name is a convention while the header key is the fact. Measured on the
# real pair: 40.9 against 21.7 tokens/s on predictable text, 31.9 against 20.6
# on code. Without the head the flag promises llama.cpp something it cannot do.
d="$(model speculating)"; write_gguf "$d/weights.gguf" 32768 "" "" 1
out="$(FXLLA_CTX=32768 launch speculating)"
case "$out" in
  *"--spec-type draft-mtp"*) pass "an MTP build is served with self-speculation";;
  *) fail "an MTP build is served with self-speculation (got: $out)";;
esac

out="$(FXLLA_CTX=32768 launch small)"
case "$out" in
  *--spec-type*) fail "no speculation flag for a build without the head";;
  *) pass "no speculation flag for a build without the head";;
esac

# --- an unreadable header falls back to the ceiling --------------------------
# A truncated download or a format this cannot parse must not stop the model
# from starting; it drops back to the single number that used to be used.
d="$(model broken)"; : > "$d/weights.gguf"
out="$(FXLLA_CTX=12288 launch broken)"
if [ "$(ctx_of "$out")" = 12288 ]; then pass "an unreadable header falls back to FXLLA_CTX"
else fail "an unreadable header falls back to FXLLA_CTX (got: $(ctx_of "$out"))"; fi

# --- the gateway reports what the backend serves ----------------------------
# If these disagree, opencode's context meter and its auto-compaction run
# against a window the backend is not actually serving.
reported="$(FXLLA_STORE="$STORE" FXLLA_CTX=262144 python3 -c "
import sys; sys.path.insert(0, '$ROOT/gateway')
import fxlla_gateway as gw
print(gw.model_context('yarn'))
" 2>/dev/null)"
if [ "$reported" = 262144 ]; then pass "the gateway reports the served window"
else fail "the gateway reports the served window (got: $reported)"; fi

printf '\n'
if [ "$fails" -eq 0 ]; then printf 'all context checks passed\n'
else printf '%d check(s) failed\n' "$fails"; exit 1; fi
