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

# --- the default is not to wait at all --------------------------------------
# The quickest render measured on this hardware is 55 s, so a wait window can
# only ever spend the caller's turn to arrive at the same job id it could have
# had immediately - and while it waits, whoever is driving is stuck.
default_wait="$(FXLLA_STORE="$STORE" python3 -c '
import sys; sys.path.insert(0, sys.argv[1] + "/media")
import media_mcp; print(media_mcp.WAIT_S)' "$ROOT")"
case "$default_wait" in
  0|0.0) pass "a render does not hold the call open by default";;
  *) fail "a render does not hold the call open by default (WAIT_S=$default_wait)";;
esac

# A status call with no wait_s must answer without blocking, even though the
# job is genuinely running.
start=$(date +%s)
FXLLA_STORE="$STORE" python3 "$MCP" > /dev/null <<EOF
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"media_job_status","arguments":{"job_id":"$JOB_ID"}}}
EOF
elapsed=$(( $(date +%s) - start ))
if [ "$elapsed" -le 5 ]; then pass "a status call answers immediately ($elapsed s)"
else fail "a status call answers immediately ($elapsed s)"; fi

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

# --- a finished job reports how long it took --------------------------------
# A caller that wanted to report the wait shelled out to python to subtract two
# timestamps, and it is the one place a real measurement reaches someone who
# read the catalog once at the start of the session and never again.
DONE_ID="1785500001-abcdef"
cat > "$JOBS/$DONE_ID.json" <<EOF
{"id":"$DONE_ID","kind":"image","status":"done","argv":["image","x"],
 "summary":"x","output":"/tmp/x.png","error":null,"pid":$SLEEPER,
 "created":1785500000.0,"started":1785500000.0,"finished":1785500123.0,
 "log":"$JOBS/$DONE_ID.log"}
EOF
done_out="$(FXLLA_STORE="$STORE" python3 "$MCP" <<EOF
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"media_job_status","arguments":{"job_id":"$DONE_ID"}}}
EOF
)"
case "$done_out" in
  *'elapsed_s'*123*) pass "a finished job reports its measured duration";;
  *) fail "a finished job reports its measured duration";;
esac

# --- a finished job says that done is not the same as correct ---------------
# Nothing below this layer can see. The checks confirm a PNG is a well-formed
# PNG and stop there: a real chain removed an object from a photograph, left a
# visible ghost of it, and the job reported done with no warnings. Whoever
# called this is the only part of the pipeline with eyes, so it gets told.
case "$done_out" in
  *verify*'did what was asked'*) pass "a finished job asks the caller to look";;
  *) fail "a finished job asks the caller to look";;
esac

# --- a running job says how long it has been going --------------------------
case "$status_line" in
  *"elapsed"*) pass "a running job reports how long it has been going";;
  *) fail "a running job reports how long it has been going";;
esac

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

# --- every generator can be told where to write -----------------------------
# The CLI took --output from the start and no MCP tool exposed it, so "save it
# in ~/Downloads" was accepted and silently dropped: the render landed in the
# media directory and the answer did not mention it.
if python3 - "$ROOT" <<'PY'
import sys
sys.path.insert(0, sys.argv[1] + "/media")
import media_mcp as m


class Result:
    returncode = 0
    stdout = "/tmp/out.png"
    stderr = ""


seen = []
m.subprocess.run = lambda cmd, **kw: (seen.append(cmd), Result())[1]

runners = [(m.run_generate, {"prompt": "p"}),
           (m.run_generate_video, {"prompt": "p"}),
           (m.run_generate_speech, {"text": "t"}),
           (m.run_edit, {"prompt": "p", "image": "/tmp/in.png"}),
           (m.run_upscale, {"image": "/tmp/in.png"})]
for runner, args in runners:
    seen.clear()
    runner(dict(args, output="~/Downloads", **{"async": False}))
    cmd = seen[0]
    if "--output" not in cmd or cmd[cmd.index("--output") + 1] != "~/Downloads":
        sys.exit("%s dropped the output path: %s" % (runner.__name__, cmd))

for tool in m.TOOLS:
    if tool["name"].startswith(("generate_", "edit_", "upscale_")):
        if "output" not in tool["inputSchema"]["properties"]:
            sys.exit("%s does not advertise output" % tool["name"])
PY
then pass "every generator takes and forwards an output path"
else fail "every generator takes and forwards an output path"; fi

# --- the expected duration rides along with the job id ----------------------
# A catalog is read once and remembered: one caller read it at 11:33, quoted
# those numbers for an hour, and never saw the measurements added at 12:05. A
# figure attached to the submission itself cannot go stale that way.
if python3 - "$ROOT" <<'PY'
import sys
sys.path.insert(0, sys.argv[1] + "/media")
import media_mcp as m


class Submit:
    returncode = 0
    stdout = "1785500000-abcdef\ntypically 2 min here"
    stderr = ""


class Missing:
    returncode = 1
    stdout = ""
    stderr = "unknown job"


calls = []


def fake_run(cmd, **kw):
    calls.append(cmd)
    return Submit() if "--async" in cmd else Missing()


m.subprocess.run = fake_run
text = m.run_generate({"prompt": "p"})
if "typically 2 min here" not in text:
    sys.exit("the submission dropped its estimate: %s" % text)
if "1785500000-abcdef" not in text:
    sys.exit("the submission dropped its job id: %s" % text)
PY
then pass "the expected duration comes back with the job id"
else fail "the expected duration comes back with the job id"; fi

# --- every tool still advertises itself ------------------------------------
tools="$(FXLLA_STORE="$STORE" python3 "$MCP" <<'EOF'
{"jsonrpc":"2.0","id":9,"method":"tools/list","params":{}}
EOF
)"
missing=0
for t in generate_image generate_video generate_speech edit_image upscale_image \
         media_job_status list_media_models list_loras list_media_jobs cancel_media_job \
         describe_image; do
  case "$tools" in *"\"$t\""*) ;; *) missing=$((missing + 1));; esac
done
if [ "$missing" -eq 0 ]; then pass "tools/list advertises every tool"
else fail "tools/list advertises every tool ($missing missing)"; fi

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
