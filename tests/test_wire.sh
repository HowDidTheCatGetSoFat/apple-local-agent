#!/usr/bin/env bash
#
# RAG MCP wiring: `kb wire-opencode` registers a command whose interpreter tracks
# FXLLA_KB_INDEX, so an index-enabled setup runs the MCP server (and its core.py
# subprocess) under a sqlite-vec-capable python.
#
# Run: bash tests/test_wire.sh
# Re-exec under bash if started by another shell (BASH_SOURCE below is bash-only).
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -euo pipefail

ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FXLLA="$ROOT/bin/fxlla"

fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

CFG="$(mktemp -d)"
trap 'rm -rf "$CFG"' EXIT

oc="$CFG/opencode/opencode.json"

# reads mcp.fxlla-rag.<field> as a compact JSON string
field() { python3 -c "import json,sys;print(json.dumps(json.load(open('$oc'))['mcp']['fxlla-rag']['$1']))"; }

# --- index OFF: plain python3, FXLLA_KB_INDEX empty in the environment ---
XDG_CONFIG_HOME="$CFG" FXLLA_STORE=/tmp bash "$FXLLA" kb wire-opencode >/dev/null
cmd_off="$(field command)"
env_off="$(field environment)"
case "$cmd_off" in
  *'"python3"'*) pass "index off: command uses python3" ;;
  *) fail "index off: command uses python3 (got $cmd_off)" ;;
esac
case "$env_off" in
  *'"FXLLA_KB_INDEX": ""'*) pass "index off: FXLLA_KB_INDEX empty in env" ;;
  *) fail "index off: FXLLA_KB_INDEX empty in env (got $env_off)" ;;
esac

# --- index ON: uv run --with sqlite-vec, FXLLA_KB_INDEX forwarded ---
XDG_CONFIG_HOME="$CFG" FXLLA_STORE=/tmp FXLLA_KB_INDEX=1 bash "$FXLLA" kb wire-opencode >/dev/null
cmd_on="$(field command)"
env_on="$(field environment)"
case "$cmd_on" in
  *'"uv"'*'"sqlite-vec"'*) pass "index on: command uses uv run --with sqlite-vec" ;;
  *) fail "index on: command uses uv (got $cmd_on)" ;;
esac
case "$env_on" in
  *'"FXLLA_KB_INDEX": "1"'*) pass "index on: FXLLA_KB_INDEX=1 in env" ;;
  *) fail "index on: FXLLA_KB_INDEX=1 in env (got $env_on)" ;;
esac

# the MCP path always points at rag_mcp.py
case "$cmd_on" in
  *rag_mcp.py*) pass "command targets rag_mcp.py" ;;
  *) fail "command targets rag_mcp.py (got $cmd_on)" ;;
esac

if [ "$fails" -ne 0 ]; then printf '\n%d test(s) failed\n' "$fails"; exit 1; fi
printf '\nall wire tests passed\n'
