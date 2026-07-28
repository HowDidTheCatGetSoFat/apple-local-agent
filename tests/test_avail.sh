#!/usr/bin/env bash
#
# Model availability tests: 'fxlla ls --json' and 'fxlla avail <alias>' emit
# valid, correct JSON, and 'on' stays fail-fast without --pull.
#
# Run: bash tests/test_avail.sh
set -euo pipefail

ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FXLLA="$ROOT/bin/fxlla"

fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

# assert a JSON field equals an expected value (via python).
json_eq() {  # <desc> <json> <python-expr on obj 'o'> <expected>
  local desc="$1" js="$2" expr="$3" want="$4" got
  got="$(printf '%s' "$js" | python3 -c "import sys,json;o=json.load(sys.stdin);print($expr)" 2>/dev/null || echo "__ERR__")"
  if [ "$got" = "$want" ]; then pass "$desc"; else fail "$desc (got: $got, want: $want)"; fi
}

STORE="$(mktemp -d)"
trap 'rm -rf "$STORE"' EXIT
mkdir -p "$STORE/models/coder-3b"
printf 'mlx-community/Qwen2.5-Coder-3B-Instruct-4bit\n' > "$STORE/models/coder-3b/.source"
printf 'mlx\n' > "$STORE/models/coder-3b/.engine"
# an incomplete download (no .source) must not count as cached
mkdir -p "$STORE/models/partial-xyz"

run() { FXLLA_STORE="$STORE" bash "$FXLLA" "$@"; }

json_eq "ls --json lists one cached model" "$(run ls --json)" "len(o)" "1"
json_eq "ls --json alias is coder-3b"      "$(run ls --json)" "o[0]['alias']" "coder-3b"
json_eq "ls --json marks cached"           "$(run ls --json)" "o[0]['cached']" "True"

json_eq "avail cached -> cached true"  "$(run avail coder-3b)"    "o['cached']" "True"
json_eq "avail cached -> engine mlx"   "$(run avail coder-3b)"    "o['engine']" "mlx"
json_eq "avail cached -> size_mb is int" "$(run avail coder-3b)"  "type(o['size_mb']).__name__" "int"
json_eq "avail cached -> no catalog_size" "$(run avail coder-3b)" "o['catalog_size']" "None"

# A catalog model that is not the cached fixture, derived from the live catalog.
CAT="$(run __complete catalog | grep -vx coder-3b | head -1)"
json_eq "avail catalog -> cached false" "$(run avail "$CAT")" "o['cached']" "False"
json_eq "avail catalog -> known true"   "$(run avail "$CAT")" "o['known']" "True"
json_eq "avail catalog -> size_mb null" "$(run avail "$CAT")" "o['size_mb']" "None"
json_eq "avail catalog -> has catalog_size" "$(run avail "$CAT")" "bool(o['catalog_size'])" "True"
json_eq "avail unknown -> known false"  "$(run avail totally-unknown)" "o['known']" "False"

# on without --pull must fail fast on an uncached model.
if run on totally-unknown >/dev/null 2>&1; then
  fail "on is fail-fast without --pull"
else
  pass "on is fail-fast without --pull"
fi

if [ "$fails" -ne 0 ]; then printf '\n%d test(s) failed\n' "$fails"; exit 1; fi
printf '\nall availability tests passed\n'
