#!/usr/bin/env bash
#
# Media weight catalog: parsing, cache detection, and the listing/pull surface.
# No network: cache states are fabricated on disk.
#
# Run: bash tests/test_media_weights.sh
# Re-exec under bash if started by another shell (BASH_SOURCE below is bash-only).
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -euo pipefail

ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FXLLA="$ROOT/bin/fxlla"

fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

HFH="$(mktemp -d)"
trap 'rm -rf "$HFH"' EXIT

run() { FXLLA_STORE=/tmp FXLLA_MEDIA_HF_HOME="$HFH" bash "$FXLLA" "$@"; }

# --- catalog is well formed ------------------------------------------------
# Every non-comment row needs the six documented fields and a known kind.
bad_rows=0
while IFS= read -r line; do
  case "$line" in \#*|'') continue;; esac
  n="$(awk -F'|' '{print NF}' <<< "$line")"
  [ "$n" -eq 6 ] || { bad_rows=$((bad_rows + 1)); continue; }
  kind="$(cut -d'|' -f4 <<< "$line" | xargs)"
  case "$kind" in image|edit|upscale|video|voice) ;; *) bad_rows=$((bad_rows + 1));; esac
  repos="$(cut -d'|' -f2 <<< "$line" | xargs)"
  case "$repos" in */*) ;; *) bad_rows=$((bad_rows + 1));; esac
done < "$ROOT/config/media.conf"
if [ "$bad_rows" -eq 0 ]; then pass "catalog rows are well formed"
else fail "catalog rows are well formed ($bad_rows bad)"; fi

# --- listing reports every alias, all missing on an empty cache -----------
out="$(run media weights)"
aliases="$(grep -vE '^\s*#|^\s*$' "$ROOT/config/media.conf" | cut -d'|' -f1 | xargs -n1)"
missing_rows=0
for a in $aliases; do
  grep -qE "^${a}[[:space:]]+.*missing" <<< "$out" || missing_rows=$((missing_rows + 1))
done
if [ "$missing_rows" -eq 0 ]; then pass "empty cache lists every alias as missing"
else fail "empty cache lists every alias as missing ($missing_rows off)"; fi

# --- cache detection: real content vs metadata only -----------------------
# chatterbox is a single-repo alias, so faking its cache flips one row.
repo="$(grep -E '^chatterbox' "$ROOT/config/media.conf" | cut -d'|' -f2 | xargs)"
dir="$HFH/hub/models--${repo//\//--}"
mkdir -p "$dir/snapshots/abc"
# metadata only: a directory and a tiny file must not count as cached
printf 'x\n' > "$dir/snapshots/abc/config.json"
# Capture first: `run ... | grep -q` would SIGPIPE the producer under pipefail
# and report a false negative (a bug this repo has hit before).
row="$(run media weights)"
if grep -qE '^chatterbox[[:space:]]+.*missing' <<< "$row"; then
  pass "metadata-only repo is not reported cached"
else fail "metadata-only repo is not reported cached"; fi

# weight-sized content counts
mkdir -p "$dir/blobs"
dd if=/dev/zero of="$dir/blobs/weights" bs=1024 count=2048 2>/dev/null
row="$(run media weights)"
if grep -qE '^chatterbox[[:space:]]+.*cached' <<< "$row"; then
  pass "repo with real content is reported cached"
else fail "repo with real content is reported cached"; fi

# --- pull short-circuits on a cached alias (no network) -------------------
out="$(run pull media:chatterbox 2>&1 || true)"
if grep -q "already cached" <<< "$out"; then
  pass "pull skips an already cached repo"
else fail "pull skips an already cached repo"; fi

# --- unknown alias fails clearly ------------------------------------------
out="$(run pull media:definitely-not-a-model 2>&1 || true)"
if grep -q "unknown media weight" <<< "$out"; then
  pass "unknown media alias is rejected"
else fail "unknown media alias is rejected"; fi

# --- completions advertise the new subcommand ----------------------------
out="$(run __complete media)"
if grep -qx weights <<< "$out"; then pass "completions list 'weights'"
else fail "completions list 'weights'"; fi

# --- several cache roots ---------------------------------------------------
# Weights outgrow a disk, so FXLLA_MEDIA_HF_HOME takes a ':' separated list.
# The first root is where new downloads go; every root is searched for what is
# already here. A path with a space in it has to survive that split - the real
# one on the author's machine is "/Volumes/verga - Data/...".
# shellcheck disable=SC2034  # read by lib/core.sh on the same line
REPO_ROOT="$ROOT"
# shellcheck disable=SC1091
. "$ROOT/lib/core.sh"

ROOT_A="$(mktemp -d)/first cache"
ROOT_B="$(mktemp -d)/second cache"
mkdir -p "$ROOT_A" "$ROOT_B"
trap 'rm -rf "$HFH" "$(dirname "$ROOT_A")" "$(dirname "$ROOT_B")"' EXIT

# A repo with real weight-sized bytes, in the SECOND root only.
FAR="$ROOT_B/hub/models--someorg--Far-Model/snapshots/abc"
mkdir -p "$FAR"
dd if=/dev/zero of="$FAR/model.safetensors" bs=1024 count=2048 >/dev/null 2>&1

export FXLLA_MEDIA_HF_HOME="$ROOT_A:$ROOT_B"

got="$(media_hf_roots | wc -l | tr -d ' ')"
if [ "$got" = 2 ]; then pass "a ':' list yields both roots"
else fail "a ':' list yields both roots (got $got)"; fi

if [ "$(media_hf_write_root)" = "$ROOT_A" ]; then
  pass "new downloads go to the first root"
else fail "new downloads go to the first root"; fi

# The point of the whole thing: cached in a root that is NOT the write root.
if media_repo_cached "someorg/Far-Model"; then
  pass "a repo cached in a later root is found"
else fail "a repo cached in a later root is reported missing"; fi

if [ "$(media_repo_root "someorg/Far-Model")" = "$ROOT_B" ]; then
  pass "the root holding a repo is named, not just 'somewhere'"
else fail "media_repo_root named the wrong root"; fi

if media_repo_cached "someorg/Nowhere"; then
  fail "a repo in no root was reported cached"
else pass "a repo in no root is not cached"; fi

# Spaces: both roots have one. If the split used whitespace these would be four
# broken paths and every check above would be meaningless.
case "$(media_repo_root "someorg/Far-Model")" in
  *"second cache") pass "a root path containing a space survives the split" ;;
  *) fail "a root path containing a space was shredded by the split" ;;
esac

# Metadata alone is not a cached model, in any root.
THIN="$ROOT_A/hub/models--someorg--Thin-Model/snapshots/abc"
mkdir -p "$THIN"; printf '{}' > "$THIN/config.json"
if media_repo_cached "someorg/Thin-Model"; then
  fail "a metadata-only directory counted as cached"
else pass "a metadata-only directory is not cached"; fi

unset FXLLA_MEDIA_HF_HOME
if [ "$(media_hf_roots)" = "$HOME/.cache/huggingface" ]; then
  pass "unset falls back to the single HF default"
else fail "unset falls back to the single HF default"; fi

if [ "$fails" -ne 0 ]; then printf '\n%d test(s) failed\n' "$fails"; exit 1; fi
printf '\nall media weight tests passed\n'
