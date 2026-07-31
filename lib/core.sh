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
# True for the usual opt-in values; empty, 0, false, no, off are all false.
is_true() { case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in 1|true|yes|on) return 0;; *) return 1;; esac; }

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
: "${FXLLA_MEDIA_MODEL:=z-image-turbo}"  # default image model (fxlla media models)
: "${FXLLA_MEDIA_HF_HOME:=}"   # HF cache holding diffusion weights (empty = HF default)
: "${FXLLA_MEDIA_OUT:=}"       # media output dir (empty = <FXLLA_STORE>/media)
: "${FXLLA_VIDEO_BIN:=ltx-2-mlx}"  # path to the ltx-2-mlx binary (fxlla media video)
: "${FXLLA_VOICE_PYTHON:=python3}"  # interpreter with mlx-audio (fxlla media voice)
: "${FXLLA_VOICE_MODEL:=YUGOROU/Chatterbox-Multilingual-MLX-4bit}"  # TTS model
: "${FXLLA_VOICE_REF:=}"       # reference voice wav (required for voice; sets timbre)
: "${FXLLA_VOICE_LANG:=en}"    # default speech language code
: "${FXLLA_MEDIA_KEEP_MODELS:=}"  # set to 1 to keep gateway models during media jobs
: "${FXLLA_MEDIA_SKIP_QUALITY:=}"  # set to 1 to accept output that fails the content checks
: "${FXLLA_ASSUME_YES:=}"      # set to 1 to authorize large downloads without asking
: "${FXLLA_CONFIRM_ABOVE_GB:=5}"  # confirm before transferring more than this many GB
: "${FXLLA_CIVITAI_TOKEN:=}"   # Civitai API token for downloading from civitai.com
: "${FXLLA_DOWNLOADER:=aria2}"  # default pull transfer: aria2 (bandwidth-capped) or hf
: "${FXLLA_KB_INDEX:=}"        # set to 1 for the sqlite-vec KNN index (kb search)
: "${FXLLA_KB_PYTHON:=}"       # override interpreter for rag/core.py (see fxlla kb)
: "${FXLLA_EMBED_PORT:=8090}"  # port for the local llama.cpp embedding server
# Eval backends: clear of 8080 (server/gateway), 8090 (embeddings) and the
# gateway's 8100+ backend range.
: "${FXLLA_EVAL_PORT:=8097}"
: "${FXLLA_GRAPH_PYTHON:=}"    # override interpreter for the code graph (needs kuzu)

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
MEDIA_CATALOG="$REPO_ROOT/config/media.conf"
SELF="$REPO_ROOT/bin/fxlla"
BASE_URL="http://$FXLLA_HOST:$FXLLA_PORT/v1"

mkdir -p "$STATE_DIR"

# bytes/s for aria2c from the configured megabits
rate_bytes() { echo $(( FXLLA_RATE_MBIT * 1000000 / 8 )); }

# Consent for a large download.
#
# The load-bearing case is a transfer nobody asked for: an agent, a script, an MCP
# call, or the weights a media render fetches on first use. Those get refused with
# instructions, because there is no one to ask. A human at a terminal is offered
# the size and can decline, which is a courtesy - they already typed the command.
# Callers that obtained consent some other way (the app shows a size dialog before
# it calls pull) pass --yes and skip all of this.
#
# Call this BEFORE creating directories or writing marker files, so a refusal
# really does leave nothing behind.
require_download_consent() {
  local gb="$1" what="$2" reply
  is_true "${FXLLA_ASSUME_YES:-}" && return 0
  # Fail closed on a size we cannot read: better to ask about a transfer that
  # turns out to be small than to skip the question on a 60 GB one.
  case "$gb" in
    ''|*[!0-9.]*|*.*.*) gb="" ;;
  esac
  if [ -n "$gb" ] \
     && awk -v g="$gb" -v t="${FXLLA_CONFIRM_ABOVE_GB:-5}" 'BEGIN{exit !(g+0 <= t+0)}'; then
    return 0
  fi
  local shown="${gb:-an unknown number of}"
  # Two separate questions. Is a human driving? That is stdin being a terminal;
  # a script, an agent, or an MCP call has it redirected and must get the refusal
  # immediately rather than wait on a prompt nobody will answer. And where does
  # the question go? /dev/tty, not stdout, which is often redirected - a prompt
  # written there is invisible while the read blocks. /dev/tty can also exist and
  # still fail to open, so probe it by opening it.
  if [ -t 0 ] && (exec </dev/tty >/dev/tty) 2>/dev/null; then
    # Prompt on the terminal itself, not on stdout: stdout is often redirected,
    # and a prompt written there is invisible while the read blocks forever.
    # Ignoring SIGTTIN makes the read fail instead of stopping a background job.
    printf '%sDownload %s GB for %s? [y/N] %s' \
      "$(_c '0;33')" "$shown" "$what" "$(_c 0)" > /dev/tty 2>/dev/null || true
    trap '' TTIN 2>/dev/null || true
    reply=""
    read -r -t 60 reply < /dev/tty 2>/dev/null || reply=""
    trap - TTIN 2>/dev/null || true
    case "$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')" in
      y|yes) return 0 ;;
      "")    die "no answer, so nothing was downloaded" ;;
      *)     die "cancelled" ;;
    esac
  fi
  die "this would transfer about ${shown} GB for ${what}, and nothing here asked a human first.
  Nothing was downloaded. Present the size to the user, and once they agree re-run
  with --yes. FXLLA_CONFIRM_ABOVE_GB raises the threshold if you want fewer stops."
}

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
# read a field for a given alias from the media weight catalog (config/media.conf)
_media_field() {
  local q="$1" n="$2" alias line
  [ -f "$MEDIA_CATALOG" ] || return 1
  while IFS= read -r line; do
    case "$line" in \#*|'') continue;; esac
    alias="$(echo "$line" | cut -d'|' -f1 | xargs)"
    [ "$alias" = "$q" ] || continue
    echo "$line" | cut -d'|' -f"$n" | xargs
    return 0
  done < "$MEDIA_CATALOG"
  return 1
}

# The Hugging Face cache the media toolchains read. Empty means the HF default
# (~/.cache/huggingface); media/generate.py applies the same rule, so a pull and a
# render always agree on where weights live.
media_hf_home() { printf '%s' "${FXLLA_MEDIA_HF_HOME:-}"; }

# Is a repo present in that cache? A repo directory can exist with only metadata
# (an interrupted fetch, or a listing that never downloaded), so look for actual
# weight-sized content rather than trusting the directory. This also keeps the
# check working across cache layouts: plain downloads fill blobs/, xet-backed ones
# add trees/, and both keep the real bytes under the repo directory.
# -print -quit stops at the first hit without a pipe into head, which under
# `set -o pipefail` would SIGPIPE find and report a false negative.
media_repo_cached() {
  local repo="$1" home dir hit
  home="$(media_hf_home)"
  [ -n "$home" ] || home="$HOME/.cache/huggingface"
  # models--<org>--<name>: slashes become double dashes, existing dashes are kept
  # (verified against a real cache, e.g. models--black-forest-labs--FLUX.1-dev).
  dir="$home/hub/models--$(printf '%s' "$repo" | sed 's|/|--|g')"
  [ -d "$dir" ] || return 1
  hit="$(find "$dir" -type f -size +1M -print -quit 2>/dev/null || true)"
  [ -n "$hit" ]
}

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
