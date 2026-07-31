#!/usr/bin/env bash
#
# Configuration precedence tests for lib/core.sh, exercised through
# `fxlla config`. Precedence must be: environment > config.env > defaults.
#
# Run: bash tests/test_config.sh
set -euo pipefail

ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FXLLA="$ROOT/bin/fxlla"
# The built-in fallback, used when neither the environment nor config.env sets
# a store. It must stay portable: a hard-coded external volume made the whole
# install path fail on every machine except the author's.
DEFAULT_STORE="${XDG_DATA_HOME:-$HOME/.local/share}/fxlla/store"

fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

# assert that the `fxlla config` output for a scenario contains an expected line.
# usage: assert_contains "<description>" "<needle>" <command...>
assert_contains() {
  local desc="$1" needle="$2"; shift 2
  local out
  out="$("$@" 2>/dev/null || true)"
  if printf '%s\n' "$out" | grep -qF -- "$needle"; then
    pass "$desc"
  else
    fail "$desc (expected to find: $needle)"
  fi
}

assert_absent() {
  local desc="$1" needle="$2"; shift 2
  local out
  out="$("$@" 2>/dev/null || true)"
  if printf '%s\n' "$out" | grep -qF -- "$needle"; then
    fail "$desc (unexpectedly found: $needle)"
  else
    pass "$desc"
  fi
}

CFG="$(mktemp -d)"
mkdir -p "$CFG/fxlla"
cat > "$CFG/fxlla/config.env" <<'EOF'
FXLLA_STORE="/from/config"
FXLLA_PORT=9999
EOF
trap 'rm -rf "$CFG"' EXIT

# config.env wins over the built-in default when the environment is silent.
assert_contains "config.env overrides default" "/from/config" \
  env -u FXLLA_STORE -u FXLLA_PORT XDG_CONFIG_HOME="$CFG" bash "$FXLLA" config

# an exported env var wins over config.env (the bug: it used to be clobbered).
assert_contains "env overrides config.env (store)" "/from/env" \
  env XDG_CONFIG_HOME="$CFG" FXLLA_STORE="/from/env" bash "$FXLLA" config
assert_absent "config value is not used when env is set" "/from/config" \
  env XDG_CONFIG_HOME="$CFG" FXLLA_STORE="/from/env" bash "$FXLLA" config
assert_contains "env overrides config.env (port)" "127.0.0.1:1234" \
  env XDG_CONFIG_HOME="$CFG" FXLLA_PORT=1234 bash "$FXLLA" config

# the built-in default applies when neither env nor config.env sets the value.
assert_contains "default applies with no env and no config" "$DEFAULT_STORE" \
  env -u FXLLA_STORE XDG_CONFIG_HOME="$CFG/none" bash "$FXLLA" config

if [ "$fails" -ne 0 ]; then
  printf '\n%d test(s) failed\n' "$fails"
  exit 1
fi
printf '\nall config precedence tests passed\n'
