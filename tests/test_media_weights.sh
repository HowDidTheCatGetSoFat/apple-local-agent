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

if [ "$fails" -ne 0 ]; then printf '\n%d test(s) failed\n' "$fails"; exit 1; fi
printf '\nall media weight tests passed\n'
