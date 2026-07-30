#!/usr/bin/env bash
#
# Consent for large downloads. The load-bearing case is a transfer nobody asked
# for: a script, an agent, an MCP call, or the weights a render fetches on first
# use. Those must refuse. A human at a terminal gets a prompt instead.
#
# Hermetic on purpose: the previous version of this file inherited the ambient
# environment and the user's own config.env, so with FXLLA_ASSUME_YES already set
# five assertions inverted and the suite really did invoke `hf download` on a 62 GB
# repo. Nothing here may reach the network.
#
# Run: bash tests/test_consent.sh
# Re-exec under bash if started by another shell (BASH_SOURCE below is bash-only).
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -euo pipefail

ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FXLLA="$ROOT/bin/fxlla"

fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

HFH="$(mktemp -d)"
STORE="$(mktemp -d)"
CFG="$(mktemp -d)"
trap 'rm -rf "$HFH" "$STORE" "$CFG"' EXIT

# An empty XDG_CONFIG_HOME keeps the user's config.env out, and these three must
# not leak in from the caller's shell either.
export XDG_CONFIG_HOME="$CFG"
unset FXLLA_ASSUME_YES FXLLA_CONFIRM_ABOVE_GB HF_TOKEN

run() { FXLLA_STORE="$STORE" FXLLA_MEDIA_HF_HOME="$HFH" bash "$FXLLA" "$@" </dev/null; }

# --- the helper ------------------------------------------------------------
gate() {  # gate <gb> [env assignments...]
  local gb="$1"; shift
  # The single-quoted body is expanded by the inner shell; values arrive as env.
  # shellcheck disable=SC2016
  env GATE_GB="$gb" GATE_ROOT="$ROOT" "$@" bash -c '
    REPO_ROOT="$GATE_ROOT"; . "$REPO_ROOT/lib/core.sh"
    require_download_consent "$GATE_GB" "a test" && echo PROCEEDED
  ' </dev/null 2>&1 || true
}

out="$(gate 62)"
if grep -q "nothing here asked a human" <<< "$out"; then pass "no terminal, over threshold: refuses"
else fail "no terminal, over threshold: refuses (got: $out)"; fi
if grep -q -- "--yes" <<< "$out"; then pass "the refusal says how to proceed"
else fail "the refusal says how to proceed"; fi

if grep -q PROCEEDED <<< "$(gate 1)"; then pass "under threshold: proceeds"
else fail "under threshold: proceeds"; fi

# Proves the comparison is numeric rather than a string or a silent zero: 5.5 is
# just over the default 5, where "0.27" would pass for either reason.
if grep -q "asked a human" <<< "$(gate 5.5)"; then pass "5.5 GB is over the 5 GB default"
else fail "5.5 GB is over the 5 GB default"; fi
if grep -q PROCEEDED <<< "$(gate 4.9)"; then pass "4.9 GB is under it"
else fail "4.9 GB is under it"; fi

# A size we cannot read must fail closed, not sail through as zero.
for bad in "" "abc" "-5" "1.2.3" "62GB"; do
  if grep -q "asked a human" <<< "$(gate "$bad")"; then pass "unreadable size '$bad' fails closed"
  else fail "unreadable size '$bad' fails closed"; fi
done

if grep -q PROCEEDED <<< "$(gate 62 FXLLA_ASSUME_YES=1)"; then pass "FXLLA_ASSUME_YES authorizes"
else fail "FXLLA_ASSUME_YES authorizes"; fi
if grep -q "asked a human" <<< "$(gate 3 FXLLA_CONFIRM_ABOVE_GB=1)"; then pass "threshold is configurable"
else fail "threshold is configurable"; fi

# --- catalog models --------------------------------------------------------
# Refusal paths only: an authorized pull would really transfer, so the bypass is
# covered at the helper level above instead.
out="$(run pull qwen3-coder 2>&1 || true)"
if grep -q "asked a human" <<< "$out"; then pass "a catalog pull is gated"
else fail "a catalog pull is gated (got: $(tail -1 <<< "$out"))"; fi
if [ ! -e "$STORE/models/qwen3-coder" ]; then pass "a refused pull leaves no model directory"
else fail "a refused pull leaves no model directory"; fi

# --- media weights ---------------------------------------------------------
out="$(run pull media:krea2 2>&1 || true)"
if grep -q "asked a human" <<< "$out"; then pass "a media pull is gated"
else fail "a media pull is gated (got: $(tail -1 <<< "$out"))"; fi
if [ -z "$(ls -A "$HFH" 2>/dev/null)" ]; then pass "nothing reached the cache"
else fail "nothing reached the cache"; fi

repo="$(grep -E '^chatterbox' "$ROOT/config/media.conf" | cut -d'|' -f2 | xargs)"
dir="$HFH/hub/models--${repo//\//--}"
mkdir -p "$dir/blobs"
dd if=/dev/zero of="$dir/blobs/weights" bs=1024 count=2048 2>/dev/null
out="$(run pull media:chatterbox 2>&1 || true)"
if grep -q "already cached" <<< "$out" && ! grep -q "asked a human" <<< "$out"; then
  pass "a cached alias is never gated"
else fail "a cached alias is never gated (got: $(tail -1 <<< "$out"))"; fi

# --- a render that would download its own weights --------------------------
out="$(FXLLA_STORE="$STORE" FXLLA_MEDIA_HF_HOME="$HFH" \
        python3 "$ROOT/media/generate.py" image "a cat" 2>&1 </dev/null || true)"
if grep -q "would first download" <<< "$out"; then pass "a render is gated on its weights"
else fail "a render is gated on its weights (got: $(tail -1 <<< "$out"))"; fi
# The MCP server and the job worker both re-invoke generate.py, so gating there
# covers all three entry points rather than only the shell wrapper.
if grep -q "fxlla pull media:" <<< "$out"; then pass "it names the pre-fetch command"
else fail "it names the pre-fetch command"; fi

# --- argument handling -----------------------------------------------------
if grep -q "unknown flag" <<< "$(run pull media:krea2 --bogus 2>&1 || true)"; then
  pass "an unknown flag on a media pull errors"
else fail "an unknown flag on a media pull errors"; fi
if grep -q "give one media alias" <<< "$(run pull media:krea2 media:chatterbox 2>&1 || true)"; then
  pass "two media aliases error instead of silently picking one"
else fail "two media aliases error instead of silently picking one"; fi
if grep -q "Usage:" <<< "$(run pull --yes 2>&1 || true)"; then pass "--yes alone shows usage"
else fail "--yes alone shows usage"; fi
if grep -q "unknown flag" <<< "$(run on tiny --bogus 2>&1 || true)"; then
  pass "on rejects an unknown flag"
else fail "on rejects an unknown flag"; fi

if [ "$fails" -ne 0 ]; then printf '\n%d test(s) failed\n' "$fails"; exit 1; fi
printf '\nall consent tests passed\n'
