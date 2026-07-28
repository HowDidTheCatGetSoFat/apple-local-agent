#!/usr/bin/env bash
#
# Shell completion tests: the __complete helper lists, the generated scripts
# parse in their target shell, and bash completion filters candidates.
#
# Run: bash tests/test_completions.sh
set -euo pipefail

ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$ROOT/bin:$PATH"   # make 'fxlla' resolvable for completion callbacks
FXLLA="$ROOT/bin/fxlla"

fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

contains() {  # <desc> <needle> <text...>
  local desc="$1" needle="$2"; shift 2
  if printf '%s\n' "$*" | grep -qw -- "$needle"; then pass "$desc"; else fail "$desc (missing: $needle)"; fi
}

# __complete helper lists.
contains "commands include serve" "serve" "$("$FXLLA" __complete commands)"
contains "commands include completions" "completions" "$("$FXLLA" __complete commands)"
contains "kb subcommands include search" "search" "$("$FXLLA" __complete kb)"
contains "graph subcommands include impact" "impact" "$("$FXLLA" __complete graph)"

# Derive a real alias from the live catalog rather than hardcoding one, so a
# catalog rename does not produce a confusing failure here.
CAT_ALIAS="$("$FXLLA" __complete catalog | head -1)"
if [ -n "$CAT_ALIAS" ]; then pass "catalog is non-empty (sample: $CAT_ALIAS)"; else fail "catalog is non-empty"; fi

# generated scripts parse in their target shell.
"$FXLLA" completions bash > "${TMPDIR:-/tmp}/fxlla_c_bash.sh"
if bash -n "${TMPDIR:-/tmp}/fxlla_c_bash.sh"; then pass "bash script parses"; else fail "bash script parses"; fi
if command -v zsh >/dev/null 2>&1; then
  "$FXLLA" completions zsh > "${TMPDIR:-/tmp}/fxlla_c_zsh.sh"
  if zsh -n "${TMPDIR:-/tmp}/fxlla_c_zsh.sh"; then pass "zsh script parses"; else fail "zsh script parses"; fi
else
  pass "zsh script parses (skipped: no zsh)"
fi

# unknown shell is rejected.
if "$FXLLA" completions fish >/dev/null 2>&1; then fail "reject unknown shell"; else pass "reject unknown shell"; fi

# bash completion completes a catalog alias for 'pull' and filters by prefix
# (run in a real bash; the word is passed via env to avoid nested quoting).
run_comp() {  # $1 = current word -> prints COMPREPLY, space-separated
  WORD="$1" SCRIPT="${TMPDIR:-/tmp}/fxlla_c_bash.sh" bash -c '
    source "$SCRIPT"
    COMP_WORDS=(fxlla pull "$WORD"); COMP_CWORD=2
    _fxlla_complete
    printf "%s " "${COMPREPLY[@]}"'
}
contains "pull completes a catalog alias" "$CAT_ALIAS" "$(run_comp "$CAT_ALIAS")"
empty="$(run_comp "zzzz-no-such-prefix" | tr -d '[:space:]')"
if [ -z "$empty" ]; then pass "unknown prefix yields no completion"; else fail "unknown prefix yields no completion (got: $empty)"; fi

if [ "$fails" -ne 0 ]; then printf '\n%d test(s) failed\n' "$fails"; exit 1; fi
printf '\nall completion tests passed\n'
