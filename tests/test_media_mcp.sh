#!/usr/bin/env bash
#
# The media MCP server's request handling: a slow call must not stop the server
# answering everything else, a render must come back as a job rather than as a
# request the client will time out on, and a still-running job must say so.
# No backend: a job record is fabricated on disk with a live pid.
#
# Run: bash tests/test_media_mcp.sh
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -euo pipefail

ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MCP="$ROOT/media/media_mcp.py"

fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

STORE="$(mktemp -d)"
trap 'rm -rf "$STORE"; kill "$SLEEPER" 2>/dev/null || true' EXIT

# A job that is genuinely running: `jobs` reaps any record whose pid is gone,
# so the pid has to be alive for the record to stay "running".
sleep 30 & SLEEPER=$!
JOBS="$STORE/media/jobs"
mkdir -p "$JOBS"
JOB_ID="1785500000-abcdef"
cat > "$JOBS/$JOB_ID.json" <<EOF
{"id":"$JOB_ID","kind":"image","status":"running","argv":["image","x"],
 "summary":"x","output":null,"error":null,"pid":$SLEEPER,
 "created":1785500000.0,"started":1785500000.0,"finished":null,
 "log":"$JOBS/$JOB_ID.log"}
EOF

# Ask for a status (blocks for the wait window) and then a listing, in that
# order, and record which answer comes back first.
drive() {
  FXLLA_STORE="$STORE" FXLLA_MCP_WAIT_S=3 python3 "$MCP" <<EOF
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"media_job_status","arguments":{"job_id":"$JOB_ID","wait_s":120}}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_media_jobs","arguments":{}}}
EOF
}

out="$(drive)"

# --- the slow call does not block the fast one -----------------------------
# Inline handling made the server deaf for the whole render: every later call,
# including the status calls asking about that render, timed out.
# Parsed as JSON: a response carries several "id" keys once a job record is
# nested in it, so matching the text would read the wrong one.
ids() { python3 -c 'import json,sys
print(" ".join(str(json.loads(l)["id"]) for l in sys.stdin if l.strip()))'; }
order="$(printf '%s\n' "$out" | ids)"
case "$order" in
  "1 3 2") pass "a listing is answered while a status call is still waiting";;
  *) fail "a listing is answered while a status call is still waiting (order: $order)";;
esac

# --- a running job reports as running, and says not to resubmit ------------
status_line="$(printf '%s\n' "$out" | python3 -c 'import json,sys
for l in sys.stdin:
    if l.strip() and json.loads(l)["id"] == 2:
        print(json.loads(l)["result"]["content"][0]["text"])')"
case "$status_line" in
  *"is running"*) pass "a running job is reported as running";;
  *) fail "a running job is reported as running";;
esac
case "$status_line" in
  *"do NOT submit"*|*"do NOT"*) pass "the running answer says not to resubmit";;
  *) fail "the running answer says not to resubmit";;
esac

# --- wait_s is capped -------------------------------------------------------
# The call above asked to wait 120 s with a 3 s window. Uncapped it would have
# blocked past any client timeout, which is how a status request came back as
# "MCP error -32001" and told the caller nothing.
start=$(date +%s)
FXLLA_STORE="$STORE" FXLLA_MCP_WAIT_S=3 python3 "$MCP" > /dev/null <<EOF
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"media_job_status","arguments":{"job_id":"$JOB_ID","wait_s":120}}}
EOF
elapsed=$(( $(date +%s) - start ))
if [ "$elapsed" -lt 30 ]; then pass "wait_s is capped at the server window ($elapsed s)"
else fail "wait_s is capped at the server window ($elapsed s)"; fi

# --- an unknown job is an error, not a hang --------------------------------
out2="$(FXLLA_STORE="$STORE" FXLLA_MCP_WAIT_S=3 python3 "$MCP" <<EOF
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"media_job_status","arguments":{"job_id":"1785500000-000000"}}}
EOF
)"
case "$out2" in
  *"unknown job"*) pass "an unknown job id is reported";;
  *) fail "an unknown job id is reported";;
esac

# --- a render goes to a job by default, and only a literal false opts out ---
# The whole point of the change: a call held open for a render outlasts the
# client, which reports a timeout the caller cannot tell from a failure.
# Checked against the argument vector, so no backend is needed.
if python3 - "$ROOT" <<'PY'
import sys
sys.path.insert(0, sys.argv[1] + "/media")
import media_mcp as m


class Result:
    returncode = 0
    stdout = "1785500000-abcdef"
    stderr = ""


seen = []
m.subprocess.run = lambda cmd, **kw: (seen.append(cmd), Result())[1]

cases = [({}, True, "absent"), ({"async": None}, True, "null"),
         ({"async": True}, True, "true"), ({"async": False}, False, "false")]
for args, want_job, label in cases:
    seen.clear()
    m._spawn([sys.executable, "x", "image", "p"], args, "failed")
    got = "--async" in seen[0]
    if got != want_job:
        sys.exit("async %s: submitted as job=%s, wanted %s" % (label, got, want_job))
PY
then pass "renders default to a job, and only async:false opts out"
else fail "renders default to a job, and only async:false opts out"; fi

# --- every tool still advertises itself ------------------------------------
tools="$(FXLLA_STORE="$STORE" python3 "$MCP" <<'EOF'
{"jsonrpc":"2.0","id":9,"method":"tools/list","params":{}}
EOF
)"
missing=0
for t in generate_image generate_video generate_speech edit_image upscale_image \
         media_job_status list_media_models list_loras list_media_jobs cancel_media_job; do
  case "$tools" in *"\"$t\""*) ;; *) missing=$((missing + 1));; esac
done
if [ "$missing" -eq 0 ]; then pass "tools/list advertises all ten tools"
else fail "tools/list advertises all ten tools ($missing missing)"; fi

# --- the async default is documented as a default --------------------------
# The contract changed: a render returns a job id when it outlasts the window,
# so a description still promising a path would send the caller looking for a
# file that does not exist yet.
case "$tools" in
  *"Default true"*) pass "the async flag documents its default";;
  *) fail "the async flag documents its default";;
esac

printf '\n'
if [ "$fails" -eq 0 ]; then printf 'all media MCP checks passed\n'
else printf '%d check(s) failed\n' "$fails"; exit 1; fi
