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

# --- every aria2 invocation carries the stall guard -------------------------
# aria2 defaults --lowest-speed-limit to 0, which means it never abandons a
# connection that has stopped delivering - and a socket that stays OPEN while
# moving no bytes is not a timeout either, so nothing else rescues it. Counted
# rather than grepped once, because the failure this catches is a THIRD call
# site added later without the guard.
calls="$(grep -c '^\s*aria2c ' "$FXLLA" || true)"
guarded="$(grep -A 8 '^\s*aria2c ' "$FXLLA" | grep -c 'ARIA_STALL_GUARD' || true)"
if [ "$calls" -gt 0 ] && [ "$calls" -eq "$guarded" ]; then
  pass "all $calls aria2c invocations carry the stall guard"
else
  fail "all aria2c invocations carry the stall guard ($guarded of $calls)"
fi

# and the guard is not a no-op: aria2's own default for this is 0
if grep -qE 'ARIA_STALL_GUARD=.*--lowest-speed-limit=[1-9]' "$ROOT/lib/core.sh"; then
  pass "the guard sets a nonzero lowest-speed-limit"
else
  fail "the guard sets a nonzero lowest-speed-limit"
fi

# retries are unlimited: these are model weights, and giving up partway means
# starting a 22 GB transfer again from the beginning.
if grep -qE 'ARIA_STALL_GUARD=.*--max-tries=0' "$ROOT/lib/core.sh"; then
  pass "a dropped connection is retried indefinitely"
else
  fail "a dropped connection is retried indefinitely"
fi

if [ "$fails" -ne 0 ]; then printf '\n%d test(s) failed\n' "$fails"; exit 1; fi
printf '\nall pull tests passed\n'
