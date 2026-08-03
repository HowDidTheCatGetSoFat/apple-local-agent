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

# a dead connection is caught by the timeout, which IS retryable
if grep -qE 'ARIA_STALL_GUARD=.*--timeout=[0-9]' "$ROOT/lib/core.sh"; then
  pass "a silent connection times out"
else
  fail "a silent connection times out"
fi

# and NOT by a speed floor. aria2 treats --lowest-speed-limit as a terminal
# abort that --max-tries does not cover, so a 22 GB transfer averaging 14 MiB/s
# was killed outright by one dip to 96 KB/s. Pinned so it does not come back.
# Matched on the assignment, not the file: the comment above it names the flag
# in order to explain why it is gone.
if grep -E '^ARIA_STALL_GUARD=' "$ROOT/lib/core.sh" | grep -q 'lowest-speed-limit'; then
  fail "no speed floor: it aborts a healthy download and does not retry"
else
  pass "no speed floor: it aborts a healthy download and does not retry"
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
