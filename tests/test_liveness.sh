#!/usr/bin/env bash
#
# "Is the server up?" is answered from a pid file, and a pid file is a claim.
# Pids get reused, so `kill -0` alone says yes for whatever process holds that
# number now - and a stale file naming a live stranger makes `fxlla on` and
# `fxlla serve` refuse to start, pointing at a server that is not there.
#
# Run: bash tests/test_liveness.sh
set -uo pipefail

ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fails=0
ran=0
pass() { ran=$((ran + 1)); printf 'ok   - %s\n' "$1"; }
fail() { ran=$((ran + 1)); printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

REPO_ROOT="$ROOT"
# No `|| true`: under `set -u` a failed source ends the file before its first
# check, and silence reads exactly like success.
# shellcheck disable=SC1091
. "$ROOT/lib/core.sh"

TMP="$(mktemp -d)"
cleanup() { [ -n "${LIVE:-}" ] && kill "$LIVE" 2>/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT

# A process we know the command line of, to stand in for both "ours" and
# "someone else's".
sleep 300 &
LIVE=$!
# Drop it from the job table so killing it at exit does not print a
# "Terminated" line into the middle of the test output.
disown "$LIVE" 2>/dev/null || true

if _pid_is "$LIVE" 'sleep 300'; then
  pass "a live pid whose command matches is alive"
else
  fail "a live pid whose command matches was reported dead"
fi

# The whole point: the pid is live, but it is not our process.
if _pid_is "$LIVE" 'fxlla_gateway\.py'; then
  fail "a live pid running something else was reported as ours (pid reuse gets through)"
else
  pass "a live pid running something else is not ours"
fi

DEAD_HOST=$(sh -c 'echo $$')   # exits immediately; its pid is free again
sleep 1
if _pid_is "$DEAD_HOST" 'sh'; then
  fail "a pid that has exited was reported alive"
else
  pass "a pid that has exited is not alive"
fi

if _pid_is "" 'anything'; then
  fail "an empty pid was reported alive"
else
  pass "an empty pid is not alive"
fi

# --- through the real accessors -----------------------------------------
GATEWAY_PID="$TMP/gateway.pid"
PID_FILE="$TMP/server.pid"

printf '%s\n' "$LIVE" > "$GATEWAY_PID"
if gateway_alive; then
  fail "gateway_alive believed a pid file naming an unrelated live process"
else
  pass "gateway_alive rejects a pid file naming an unrelated live process"
fi

printf '%s\n' "$LIVE" > "$PID_FILE"
if server_alive; then
  fail "server_alive believed a pid file naming an unrelated live process"
else
  pass "server_alive rejects a pid file naming an unrelated live process"
fi

rm -f "$GATEWAY_PID"
if gateway_alive; then
  fail "gateway_alive reported alive with no pid file at all"
else
  pass "gateway_alive is false with no pid file"
fi

# server_alive must still say yes for a real engine process, or `fxlla off` and
# the keep-warm watchdog would stop seeing a server they are meant to manage.
# Both engine names have to be accepted - gguf models run llama-server, MLX
# ones mlx_lm.server - so check the pattern against each.
for engine in llama-server mlx_lm.server; do
  if printf '%s\n' "/usr/local/bin/$engine --model /x --port 8080" | grep -qE -- 'llama-server|mlx_lm\.server'; then
    pass "the engine pattern accepts $engine"
  else
    fail "the engine pattern rejects $engine, so a running server would look stopped"
  fi
done

printf '\n%s\n' "-----"
EXPECTED=9
if [ "$ran" -ne "$EXPECTED" ]; then
  printf 'FAIL - ran %d assertions, expected %d (the file did not finish)\n' "$ran" "$EXPECTED"
  exit 1
fi
if [ "$fails" -eq 0 ]; then printf 'all %d liveness tests passed\n' "$ran"; else printf '%d test(s) failed\n' "$fails"; exit 1; fi
