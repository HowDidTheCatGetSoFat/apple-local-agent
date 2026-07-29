#!/usr/bin/env bash
#
# Pull argument handling that does not need the network: downloader validation
# and the usage message.
#
# Run: bash tests/test_pull.sh
set -euo pipefail

ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FXLLA="$ROOT/bin/fxlla"

fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

# an unknown downloader is rejected before any network access
if FXLLA_STORE=/tmp bash "$FXLLA" pull tiny --downloader nope >/dev/null 2>&1; then
  fail "unknown downloader rejected"
else
  pass "unknown downloader rejected"
fi

# a bare pull prints usage and fails
if FXLLA_STORE=/tmp bash "$FXLLA" pull >/dev/null 2>&1; then
  fail "bare pull errors"
else
  pass "bare pull errors"
fi

if [ "$fails" -ne 0 ]; then printf '\n%d test(s) failed\n' "$fails"; exit 1; fi
printf '\nall pull tests passed\n'
