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

# --- the notary profile is not something to remember ------------------------
# app/package-dmg.sh used to REQUIRE the profile name as an argument, and the
# name lived in a config file nothing loads. Asked whether this project had
# ever notarized, the keychain was searched with the wrong command, nothing was
# found, and the answer given was "there are no credentials" - while three
# submissions sat Accepted in Apple's history.
#
# SOURCE-level, and weaker than running it: the real path needs an Apple
# Developer identity and a keychain profile, neither of which exists on a CI
# runner, so `--notarize` cannot be exercised here at all. What this pins is
# the shape - the argument is optional and the config name is consulted - which
# is what a careless edit would undo.
PKG="$ROOT/app/package-dmg.sh"
if grep -q 'PROFILE="${2:-${FXLLA_NOTARY_PROFILE:-}}"' "$PKG"; then
  pass "--notarize falls back to FXLLA_NOTARY_PROFILE"
else
  fail "--notarize falls back to FXLLA_NOTARY_PROFILE"
fi
if grep -q '\${2:?usage' "$PKG"; then
  fail "the profile argument is no longer mandatory"
else
  pass "the profile argument is no longer mandatory"
fi
# Both config locations, because they are two different files: lib/core.sh
# loads ~/.config/fxlla/config.env, while the release settings live in the
# repo's own git-ignored config/config.env. Reading only one is how the name
# went missing.
if grep -q '\.\./config/config\.env' "$PKG"; then
  pass "package-dmg reads the repo's config/config.env"
else
  fail "package-dmg reads the repo's config/config.env"
fi
if grep -q 'XDG_CONFIG_HOME' "$PKG"; then
  pass "package-dmg reads the user's config.env too"
else
  fail "package-dmg reads the user's config.env too"
fi

if [ "$fails" -ne 0 ]; then
  printf '\n%d test(s) failed\n' "$fails"
  exit 1
fi
printf '\nall config precedence tests passed\n'
