# Shared helpers and configuration loading. Sourced by bin/fxlla.
# shellcheck shell=bash
# shellcheck disable=SC2034  # several vars are exported for use by bin/fxlla after sourcing

set -euo pipefail

# --- output ---------------------------------------------------------------
_c()   { printf '\033[%sm' "$1"; }
info() { printf '%s%s%s\n' "$(_c '0;36')" "$*" "$(_c 0)"; }
ok()   { printf '%s%s%s\n' "$(_c '0;32')" "$*" "$(_c 0)"; }
warn() { printf '%s%s%s\n' "$(_c '0;33')" "$*" "$(_c 0)" >&2; }
die()  { printf '%s%s%s\n' "$(_c '0;31')" "$*" "$(_c 0)" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "Missing '$1'. Run: fxlla setup"; }

# --- configuration --------------------------------------------------------
# Precedence: already-exported vars > ~/.config/fxlla/config.env > defaults.
# config.env uses plain assignments, which would otherwise clobber a value the
# user exported in the shell. To honour the precedence, snapshot the config
# vars already exported, source the file, then re-apply the snapshot so the
# environment wins. (This runs under bash: bin/fxlla re-execs if not.)
_user_cfg="${XDG_CONFIG_HOME:-$HOME/.config}/fxlla/config.env"
if [ -f "$_user_cfg" ]; then
  _saved_env="$(export -p | grep -E '^declare -x (FXLLA_[A-Za-z0-9_]*|HF_TOKEN)=' || true)"
  # shellcheck source=/dev/null
  . "$_user_cfg"
  [ -n "$_saved_env" ] && eval "$_saved_env"
  unset _saved_env
fi

: "${FXLLA_STORE:=/Volumes/1TB-WD750-1/llm}"
: "${FXLLA_RATE_MBIT:=25}"
: "${FXLLA_HOST:=127.0.0.1}"
: "${FXLLA_PORT:=8080}"
: "${FXLLA_DEFAULT_MODEL:=qwen3-coder}"
: "${FXLLA_SERVER_ARGS:=}"
: "${FXLLA_KEEP_WARM:=10}"     # idle minutes before auto-stop (0 = never)
: "${FXLLA_CTX:=8192}"         # context size for llama-server (gguf)
: "${FXLLA_NGL:=999}"          # layers offloaded to GPU for llama-server (gguf)

MODELS_DIR="$FXLLA_STORE/models"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/fxlla"
PID_FILE="$STATE_DIR/server.pid"
WATCH_PID="$STATE_DIR/watch.pid"
ACT_FILE="$STATE_DIR/activity"
LOG_FILE="$STATE_DIR/server.log"
CURRENT_FILE="$STATE_DIR/current"
STATS_FILE="$STATE_DIR/stats.jsonl"
GATEWAY_PID="$STATE_DIR/gateway.pid"
GATEWAY_LOG="$STATE_DIR/gateway.log"
CATALOG="$REPO_ROOT/config/models.conf"
SELF="$REPO_ROOT/bin/fxlla"
BASE_URL="http://$FXLLA_HOST:$FXLLA_PORT/v1"

mkdir -p "$STATE_DIR"

# bytes/s for aria2c from the configured megabits
rate_bytes() { echo $(( FXLLA_RATE_MBIT * 1000000 / 8 )); }

# is the store mounted / present?
require_store() {
  [ -d "$FXLLA_STORE" ] || die "Store '$FXLLA_STORE' not found. Is the disk mounted? Set FXLLA_STORE in ~/.config/fxlla/config.env"
  mkdir -p "$MODELS_DIR"
}

# read a catalog field for a given alias:  _catalog_field <alias> <n>
_catalog_field() {
  local q="$1" n="$2" alias line
  while IFS= read -r line; do
    case "$line" in \#*|'') continue;; esac
    alias="$(echo "$line" | cut -d'|' -f1 | xargs)"
    [ "$alias" = "$q" ] || continue
    echo "$line" | cut -d'|' -f"$n" | xargs
    return 0
  done < "$CATALOG"
  return 1
}

# alias -> HF repo (or passthrough when it already looks like org/repo)
resolve_repo() {
  local q="$1"; [ -z "$q" ] && return 1
  local repo; repo="$(_catalog_field "$q" 2 || true)"
  if [ -n "$repo" ]; then echo "$repo"; return 0; fi
  case "$q" in */*) echo "$q"; return 0;; esac
  return 1
}

# alias -> engine (mlx|gguf). Defaults to mlx.
resolve_engine() {
  local e; e="$(_catalog_field "$1" 5 2>/dev/null || true)"
  echo "${e:-}"
}

# local folder name for an alias/repo
local_name() { case "$1" in */*) basename "$1";; *) echo "$1";; esac; }

# list files in an HF repo: prints "size<TAB>path" per file
_hf_list() {
  HF_REPO="$1" HF_TOKEN="${HF_TOKEN:-}" python3 - <<'PY'
import os, json, sys, urllib.request
repo = os.environ["HF_REPO"]; tok = os.environ.get("HF_TOKEN", "")
url = f"https://huggingface.co/api/models/{repo}/tree/main?recursive=true"
while url:
    req = urllib.request.Request(url)
    if tok: req.add_header("Authorization", f"Bearer {tok}")
    try:
        r = urllib.request.urlopen(req)
    except Exception as e:
        sys.stderr.write(str(e) + "\n"); sys.exit(2)
    for e in json.load(r):
        if e.get("type") != "file": continue
        p = e["path"]
        if p == ".gitattributes" or p.startswith("."): continue
        print(f'{e.get("size",0)}\t{p}')
    link = r.headers.get("Link", ""); nxt = ""
    for part in link.split(","):
        if 'rel="next"' in part: nxt = part[part.find("<")+1:part.find(">")]
    url = nxt
PY
}

server_pid()   { [ -f "$PID_FILE" ] && cat "$PID_FILE" 2>/dev/null || true; }
server_alive() { local p; p="$(server_pid)"; [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }

gateway_pid()   { [ -f "$GATEWAY_PID" ] && cat "$GATEWAY_PID" 2>/dev/null || true; }
gateway_alive() { local p; p="$(gateway_pid)"; [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }
