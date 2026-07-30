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

# --- the installed set must EQUAL the repo set -----------------------------
# Counting is not enough: a renamed skill keeps the count while leaving a stale
# copy that still tells the model to use a tool that changed.
repo_set="$(run skills ls | sort | tr '\n' ' ')"
inst_set="$(cd "$HOMEDIR/.claude/skills" && for d in */; do [ -f "$d/SKILL.md" ] && basename "$d"; done | sort | tr '\n' ' ')"
if [ "$repo_set" = "$inst_set" ]; then pass "installed set equals repo set"
else fail "installed set equals repo set (repo: $repo_set | installed: $inst_set)"; fi

# --- a stale managed skill is pruned on re-install -------------------------
store="$HOMEDIR/.local/share/fxlla/skills"
for base in "$HOMEDIR/.claude/skills" "$store"; do
  mkdir -p "$base/fxlla-gone"
  printf -- '---\nname: fxlla-gone\ndescription: stale\n---\n' > "$base/fxlla-gone/SKILL.md"
  printf 'installed by fxlla\n' > "$base/fxlla-gone/.fxlla-managed"
done
python3 - "$oc" "$store" <<'PY'
import json, os, sys
cfg, store = sys.argv[1], sys.argv[2]
with open(cfg) as f: d = json.load(f)
d.setdefault("instructions", []).insert(0, "/tmp/my-own-instructions.md")
d["instructions"].append(os.path.join(store, "fxlla-gone", "SKILL.md"))
with open(cfg, "w") as f: json.dump(d, f, indent=2)
PY
run skills install >/dev/null
if [ ! -d "$HOMEDIR/.claude/skills/fxlla-gone" ]; then pass "stale Claude skill pruned"
else fail "stale Claude skill pruned"; fi
if [ ! -d "$store/fxlla-gone" ]; then pass "stale opencode skill pruned"
else fail "stale opencode skill pruned"; fi
stale_instr="$(python3 -c "import json;print(sum(1 for p in json.load(open('$oc')).get('instructions',[]) if 'fxlla-gone' in p))")"
if [ "$stale_instr" -eq 0 ]; then pass "stale opencode instruction dropped"
else fail "stale opencode instruction dropped"; fi

# --- never touch what fxlla did not install --------------------------------
mkdir -p "$HOMEDIR/.claude/skills/someone-elses"
printf -- '---\nname: someone-elses\ndescription: another tool\n---\n' > "$HOMEDIR/.claude/skills/someone-elses/SKILL.md"
run skills install --client claude >/dev/null
if [ -f "$HOMEDIR/.claude/skills/someone-elses/SKILL.md" ]; then pass "unmanaged skill left alone"
else fail "unmanaged skill left alone"; fi
own="$(python3 -c "import json;print(sum(1 for p in json.load(open('$oc')).get('instructions',[]) if p=='/tmp/my-own-instructions.md'))")"
if [ "$own" -eq 1 ]; then pass "user's own instruction entry kept"
else fail "user's own instruction entry kept"; fi

# --- status reports drift --------------------------------------------------
printf '\ndrifted\n' >> "$HOMEDIR/.claude/skills/fxlla-media/SKILL.md"
st="$(run skills status)"
if grep -qE '^fxlla-media[[:space:]]+differs' <<< "$st"; then pass "status detects a drifted copy"
else fail "status detects a drifted copy"; fi
run skills install --client claude >/dev/null
st2="$(run skills status)"
if grep -qE '^fxlla-media[[:space:]]+current' <<< "$st2"; then pass "re-install restores a drifted copy"
else fail "re-install restores a drifted copy"; fi

# --- a malformed skill aborts the install before writing anything ----------
BADHOME="$(mktemp -d)"
bad="$ROOT/skills/zz-test-invalid"
mkdir -p "$bad"
printf -- '---\nname: Zz_Test_Invalid\ndescription: bad name\n---\n' > "$bad/SKILL.md"
out="$(HOME="$BADHOME" XDG_CONFIG_HOME="$BADHOME/cfg" FXLLA_STORE=/tmp bash "$FXLLA" skills install 2>&1 || true)"
rm -rf "$bad"
if grep -q "invalid skill" <<< "$out"; then pass "malformed skill is rejected"
else fail "malformed skill is rejected"; fi
if [ ! -d "$BADHOME/.claude/skills" ]; then pass "rejected install wrote nothing"
else fail "rejected install wrote nothing"; fi
rm -rf "$BADHOME"

if [ "$fails" -ne 0 ]; then printf '\n%d test(s) failed\n' "$fails"; exit 1; fi
printf '\nall skills tests passed\n'
