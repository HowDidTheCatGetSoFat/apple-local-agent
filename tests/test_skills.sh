#!/usr/bin/env bash
#
# Skills install: the pack lands in Claude Code's skills dir and opencode's
# instructions list, idempotently.
#
# Run: bash tests/test_skills.sh
set -euo pipefail

ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FXLLA="$ROOT/bin/fxlla"

fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

HOMEDIR="$(mktemp -d)"
CFG="$(mktemp -d)"
trap 'rm -rf "$HOMEDIR" "$CFG"' EXIT

run() { HOME="$HOMEDIR" XDG_CONFIG_HOME="$CFG" FXLLA_STORE=/tmp bash "$FXLLA" "$@"; }

# the pack lists the expected skills
listed="$(run skills ls)"
for name in fxlla-knowledge fxlla-code-graph fxlla-media fxlla-model-access; do
  if printf '%s\n' "$listed" | grep -qx "$name"; then pass "skills ls has $name"; else fail "skills ls has $name"; fi
done

run skills install >/dev/null

# Claude Code: one SKILL.md per skill under ~/.claude/skills
count="$(find "$HOMEDIR/.claude/skills" -name SKILL.md 2>/dev/null | grep -c . || true)"
if [ "$count" -eq 4 ]; then pass "4 Claude skills installed"; else fail "4 Claude skills installed (got $count)"; fi

# opencode: instructions array references the 4 skill files
oc="$CFG/opencode/opencode.json"
n="$(python3 -c "import json;print(len([p for p in json.load(open('$oc')).get('instructions',[]) if p.endswith('SKILL.md')]))" 2>/dev/null || echo 0)"
if [ "$n" -eq 4 ]; then pass "opencode has 4 skill instructions"; else fail "opencode has 4 skill instructions (got $n)"; fi

# idempotent: a second install does not duplicate the instructions
run skills install --client opencode >/dev/null
n2="$(python3 -c "import json;print(len(json.load(open('$oc')).get('instructions',[])))" 2>/dev/null || echo 0)"
if [ "$n2" -eq "$n" ]; then pass "install is idempotent"; else fail "install is idempotent (was $n, now $n2)"; fi

if [ "$fails" -ne 0 ]; then printf '\n%d test(s) failed\n' "$fails"; exit 1; fi
printf '\nall skills tests passed\n'
