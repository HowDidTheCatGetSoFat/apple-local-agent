#!/usr/bin/env bash
#
# A vision model is two files: the weights and a multimodal projector. Both have
# to survive the pull and reach llama-server, and neither is checked by anything
# that only looks at the weights.
#
# Run: bash tests/test_vision.sh
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -euo pipefail

ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FXLLA="$ROOT/bin/fxlla"

fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

STORE="$(mktemp -d)"
BIN="$(mktemp -d)"
trap 'rm -rf "$STORE" "$BIN"' EXIT

# A stand-in for llama-server that reports its argv instead of loading weights,
# so the launch can be checked without a model or a GPU.
cat > "$BIN/llama-server" <<'STUB'
#!/bin/sh
echo "$@"
STUB
chmod +x "$BIN/llama-server"

# A model directory shaped the way `fxlla pull` leaves one for a vision repo.
MODEL="$STORE/models/seer"
mkdir -p "$MODEL"
: > "$MODEL/weights-Q4_K_M.gguf"
: > "$MODEL/mmproj-Q8_0.gguf"
: > "$MODEL/mmproj-f16.gguf"
echo gguf > "$MODEL/.engine"
echo "weights-Q4_K_M.gguf" > "$MODEL/.entry"

launch() { PATH="$BIN:$PATH" FXLLA_STORE="$STORE" bash "$FXLLA" _backend seer 9999; }

# --- the projector reaches the server ---------------------------------------
# Without --mmproj the same weights serve as text-only: every image in a request
# is dropped and nothing says so, which is the failure this catches.
out="$(launch)"
case "$out" in
  *--mmproj*) pass "a projector on disk is passed to llama-server";;
  *) fail "a projector on disk is passed to llama-server (got: $out)";;
esac

# --- f16 wins where a repo ships both, in every locale ----------------------
# Pinned to LC_ALL=C on purpose. Glob expansion is collated, and under the C
# locale uppercase sorts first, so a bare mmproj*.gguf picks Q8_0 while a
# UTF-8 locale picks f16. Which projector a model runs with must not depend on
# the environment's language.
out_c="$(LC_ALL=C launch)"
case "$out_c" in
  *mmproj-f16.gguf*) pass "the f16 projector is preferred, locale-independently";;
  *) fail "the f16 projector is preferred, locale-independently (got: $out_c)";;
esac

# --- the weights are the model, never the projector -------------------------
# With --quant Q8_0 both the weights and a projector match the quant, so an
# entry picked by position could launch the server on the projector.
model_arg="$(sed -n 's/.*--model \([^ ]*\).*/\1/p' <<< "$out")"
case "$model_arg" in
  *mmproj*) fail "--model is the weights, not the projector (got: $model_arg)";;
  *weights-Q4_K_M.gguf) pass "--model is the weights, not the projector";;
  *) fail "--model is the weights, not the projector (got: $model_arg)";;
esac

# --- a text-only model is unchanged -----------------------------------------
# The flag must not appear where there is no projector: llama-server errors on
# a --mmproj it cannot read, so an empty value would break every gguf model.
TEXT="$STORE/models/plain"
mkdir -p "$TEXT"
: > "$TEXT/weights-Q4_K_M.gguf"
echo gguf > "$TEXT/.engine"
echo "weights-Q4_K_M.gguf" > "$TEXT/.entry"
out2="$(PATH="$BIN:$PATH" FXLLA_STORE="$STORE" bash "$FXLLA" _backend plain 9999)"
case "$out2" in
  *--mmproj*) fail "no --mmproj without a projector (got: $out2)";;
  *) pass "no --mmproj without a projector";;
esac

# --- the OTHER publisher's naming ------------------------------------------
# Two conventions in the wild, and only one was ever handled. empero-ai writes
# `mmproj-Qwythos-27B-F16.gguf`; mradermacher writes `Model.mmproj-Q8_0.gguf`,
# the model name first. Every glob and grep here was anchored at the front, so
# the second kind was invisible: the pull left the projector in the repo, and
# had it been on disk the launcher would not have found it. Three vision models
# on this machine were fetched blind that way.
SEER2="$STORE/models/seer2"
mkdir -p "$SEER2"
: > "$SEER2/Qwen3.5-9B.Q6_K.gguf"
: > "$SEER2/Qwen3.5-9B.mmproj-Q8_0.gguf"
: > "$SEER2/Qwen3.5-9B.mmproj-f16.gguf"
echo gguf > "$SEER2/.engine"
out3="$(PATH="$BIN:$PATH" FXLLA_STORE="$STORE" bash "$FXLLA" _backend seer2 9999)"
case "$out3" in
  *--mmproj*) pass "a projector named <model>.mmproj-* is found too";;
  *) fail "a projector named <model>.mmproj-* is found too (got: $out3)";;
esac
# No .entry on purpose: this exercises the fallback that scans the directory,
# where sorted() puts `Qwen3.5-9B.mmproj-Q8_0.gguf` BEFORE `Qwen3.5-9B.Q6_K.gguf`
# - so an anchored test there serves the projector as the model.
model_arg3="$(sed -n 's/.*--model \([^ ]*\).*/\1/p' <<< "$out3")"
case "$model_arg3" in
  *mmproj*) fail "the weights win over an alphabetically earlier projector (got: $model_arg3)";;
  *Qwen3.5-9B.Q6_K.gguf) pass "the weights win over an alphabetically earlier projector";;
  *) fail "the weights win over an alphabetically earlier projector (got: $model_arg3)";;
esac

# --- and the same word in another case --------------------------------------
# The pull side matches with `grep -i`, so it will happily download a projector
# spelled `MMProj`. Everything downstream matched case-SENSITIVELY: a glob, a
# `case` pattern and a Python `in`. So that file arrived, sat next to the
# weights, and was never passed - the identical text-only failure as never
# downloading it, reached from the other side. Worse, the entry fallback did
# not recognise it either, so it could be served AS the model.
SEER3="$STORE/models/seer3"
mkdir -p "$SEER3"
: > "$SEER3/Qwen3.5-9B.Q6_K.gguf"
: > "$SEER3/Qwen3.5-9B.MMProj-F16.gguf"
echo gguf > "$SEER3/.engine"
out4="$(PATH="$BIN:$PATH" FXLLA_STORE="$STORE" bash "$FXLLA" _backend seer3 9999)"
case "$out4" in
  *--mmproj*MMProj-F16.gguf*) pass "a projector spelled MMProj is found too";;
  *) fail "a projector spelled MMProj is found too (got: $out4)";;
esac
model_arg4="$(sed -n 's/.*--model \([^ ]*\).*/\1/p' <<< "$out4")"
case "$model_arg4" in
  *MMProj*) fail "an uppercase projector is not served as the model (got: $model_arg4)";;
  *Qwen3.5-9B.Q6_K.gguf) pass "an uppercase projector is not served as the model";;
  *) fail "an uppercase projector is not served as the model (got: $model_arg4)";;
esac

# --- the catalog declares one ------------------------------------------------
# Nothing else in fxlla can see, so losing this row is losing the capability.
if grep -qE '^\s*vision\s*\|' "$ROOT/config/models.conf"; then
  pass "the catalog has a vision model"
else fail "the catalog has a vision model"; fi
if grep -E '^\s*vision\s*\|' "$ROOT/config/models.conf" | grep -q 'gguf'; then
  pass "it is served by the engine that accepts images"
else fail "it is served by the engine that accepts images"; fi

printf '\n'
if [ "$fails" -eq 0 ]; then printf 'all vision checks passed\n'
else printf '%d check(s) failed\n' "$fails"; exit 1; fi
