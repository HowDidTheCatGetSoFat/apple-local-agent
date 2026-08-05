#!/usr/bin/env bash
#
# One model, one directory. Weights pulled by org/repo and the same weights
# pulled by catalog alias must land in the same place - otherwise the alias
# reports "not downloaded" while the bytes sit on disk under the repo name,
# and the fix is a second download of something already there.
#
# Run: bash tests/test_identity.sh
set -euo pipefail

ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fails=0
ran=0
pass() { ran=$((ran + 1)); printf 'ok   - %s\n' "$1"; }
fail() { ran=$((ran + 1)); printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

assert_eq() {
  local desc="$1" want="$2" got="$3"
  if [ "$want" = "$got" ]; then pass "$desc"; else fail "$desc (want '$want', got '$got')"; fi
}

# A catalog whose aliases differ from every repo basename, so a passing test
# cannot be an accident of the two spellings happening to agree.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
CATALOG="$TMP/models.conf"
cat > "$CATALOG" <<'EOF'
# alias | repo | size | role | engine | note
small-one | someorg/Some-Model-GGUF   | 1GB | dev | gguf | first
other-one | otherorg/Other-Model-GGUF | 2GB | dev | gguf | second
EOF

MODELS_DIR="$TMP/models"
mkdir -p "$MODELS_DIR"

# core.sh needs REPO_ROOT to exist before it is sourced, and it must be sourced
# WITHOUT `|| true`: under `set -u` an unbound variable kills the shell outright,
# so a tolerated failure here ends the file with zero assertions and exit 0 -
# a green run that tested nothing. Let it fail loudly instead.
# shellcheck disable=SC2034  # read by lib/core.sh when it is sourced below
REPO_ROOT="$ROOT"
# shellcheck disable=SC1091
. "$ROOT/lib/core.sh"
# Sourcing resets these to the real store; point them back at the fixture.
CATALOG="$TMP/models.conf"
MODELS_DIR="$TMP/models"

assert_eq "an alias stays itself" \
  "small-one" "$(local_name small-one)"
assert_eq "a catalog repo resolves to its alias, not the repo basename" \
  "small-one" "$(local_name someorg/Some-Model-GGUF)"
assert_eq "matching is case-folded, the way HF resolves repo names" \
  "other-one" "$(local_name OTHERORG/other-model-gguf)"
assert_eq "a repo the catalog does not know keeps its basename" \
  "Unknown-Model" "$(local_name someorg/Unknown-Model)"

# A repo path pasted out of a URL brings a trailing slash. Without stripping it
# the exact-match falls through to basename, which quietly hands back a
# different directory than the alias - the very split this all exists to close.
assert_eq "a trailing slash still resolves to the alias" \
  "small-one" "$(local_name someorg/Some-Model-GGUF/)"
assert_eq "several trailing slashes still resolve to the alias" \
  "small-one" "$(local_name someorg/Some-Model-GGUF///)"
# resolve_repo feeds both .source and the HF API path, so it must not carry the
# slash either, or stray_model_dirs reads back a repo it can never match.
assert_eq "resolve_repo drops a trailing slash" \
  "someorg/Unknown-Model" "$(resolve_repo someorg/Unknown-Model/)"

# The point of the whole exercise: both spellings name ONE directory.
assert_eq "both spellings of the same model agree on a directory" \
  "$(local_name small-one)" "$(local_name someorg/Some-Model-GGUF)"

# --- stray directories on disk -------------------------------------------
# .source records what was actually fetched; the directory name is derived and
# can disagree with it. Trust .source.
mkdir -p "$MODELS_DIR/Some-Model-GGUF" "$MODELS_DIR/other-one" "$MODELS_DIR/Unrelated"
printf 'someorg/Some-Model-GGUF\n'   > "$MODELS_DIR/Some-Model-GGUF/.source"
printf 'otherorg/Other-Model-GGUF\n' > "$MODELS_DIR/other-one/.source"
printf 'nobody/Unrelated\n'          > "$MODELS_DIR/Unrelated/.source"

strays="$(stray_model_dirs || true)"

if printf '%s\n' "$strays" | grep -q "^Some-Model-GGUF	small-one$"; then
  pass "a repo-named directory holding a catalog model is reported, with its alias"
else
  fail "stray directory not reported (got: $(printf '%s' "$strays" | tr '\n' ';'))"
fi

# Assert the ABSENCE of the wrong answers rather than a count of the right
# ones: a count breaks the day a fourth fixture is added, and passes the day
# two bugs cancel out.
if printf '%s\n' "$strays" | grep -q "^other-one	"; then
  fail "a directory already named after its alias was reported as stray"
else
  pass "a directory already named after its alias is left alone"
fi
if printf '%s\n' "$strays" | grep -q "^Unrelated	"; then
  fail "a directory whose repo is not in the catalog was reported as stray"
else
  pass "a directory outside the catalog is left alone"
fi

# A missing catalog is a real state (a partial install, a CLI shipped without
# config/). It must not make the shell print a raw redirection error to stderr
# on every call - once per model directory, in doctor's case.
saved_catalog="$CATALOG"
CATALOG="$TMP/definitely-not-here.conf"
err="$(local_name someorg/Some-Model-GGUF 2>&1 >/dev/null)"
CATALOG="$saved_catalog"
if [ -z "$err" ]; then
  pass "a missing catalog says nothing on stderr"
else
  fail "a missing catalog leaked to stderr: $err"
fi

# A directory with no .source cannot be judged - the name is all there is, and
# guessing from it is how the two identities appeared in the first place.
mkdir -p "$MODELS_DIR/No-Source"
if printf '%s\n' "$(stray_model_dirs || true)" | grep -q "^No-Source	"; then
  fail "a directory with no .source was judged anyway"
else
  pass "a directory with no .source is left alone"
fi

# --- what doctor tells you to run ---------------------------------------
# `mv src dst` renames only while dst does not exist; once it does, mv moves
# src INSIDE dst and the advice silently makes things worse. The two cases must
# not get the same command.
FXLLA="$ROOT/bin/fxlla"

# doctor reads the shipped catalog, not the fixture above, so take a real
# alias/repo pair out of it. Reading the pair instead of hard-coding one means
# editing a catalog row cannot silently turn this into a test of nothing - the
# pair is whatever the catalog currently says, and only a row whose basename
# already equals its alias would be useless (skipped below).
REAL_ALIAS=""; REAL_REPO=""
while IFS='|' read -r a r _rest; do
  case "$a" in \#*|'') continue;; esac
  a="$(trim "$a")"; r="$(trim "$r")"
  [ -n "$a" ] && [ -n "$r" ] || continue
  case "$r" in */*) ;; *) continue;; esac
  [ "$(basename "$r")" = "$a" ] && continue
  REAL_ALIAS="$a"; REAL_REPO="$r"; break
done < "$ROOT/config/models.conf"
REAL_DIR="$(basename "$REAL_REPO")"

if [ -z "$REAL_ALIAS" ]; then
  fail "no catalog row has a repo basename different from its alias - cannot test doctor"
else
  STORE_ONE="$(mktemp -d)"
  mkdir -p "$STORE_ONE/models/$REAL_DIR"
  printf '%s\n' "$REAL_REPO" > "$STORE_ONE/models/$REAL_DIR/.source"
  out_one="$(FXLLA_STORE="$STORE_ONE" bash "$FXLLA" doctor 2>&1 || true)"
  rm -rf "$STORE_ONE"

  # The paths are shell-quoted, so match through the quotes rather than assuming
  # a bare word ends the line.
  if printf '%s\n' "$out_one" | grep -q "mv .*/$REAL_DIR' .*/$REAL_ALIAS'\$"; then
    pass "doctor offers a rename when only the stray name exists"
  else
    fail "doctor did not offer the rename for $REAL_DIR -> $REAL_ALIAS"
  fi

  STORE_TWO="$(mktemp -d)"
  mkdir -p "$STORE_TWO/models/$REAL_DIR" "$STORE_TWO/models/$REAL_ALIAS"
  printf '%s\n' "$REAL_REPO" > "$STORE_TWO/models/$REAL_DIR/.source"
  printf '%s\n' "$REAL_REPO" > "$STORE_TWO/models/$REAL_ALIAS/.source"
  out_two="$(FXLLA_STORE="$STORE_TWO" bash "$FXLLA" doctor 2>&1 || true)"
  rm -rf "$STORE_TWO"

  if printf '%s\n' "$out_two" | grep -q "mv .*/$REAL_DIR"; then
    fail "doctor offered 'mv' when the destination already exists (it would nest, not rename)"
  else
    pass "doctor does not offer 'mv' when both names already exist"
  fi

  # The printed command has to survive being pasted. A store path with a space
  # in it turns an unquoted `mv A B` into a four-argument move.
  SPACED="$(mktemp -d)/a store"
  mkdir -p "$SPACED/models/$REAL_DIR"
  printf '%s\n' "$REAL_REPO" > "$SPACED/models/$REAL_DIR/.source"
  cmd="$(FXLLA_STORE="$SPACED" bash "$FXLLA" doctor 2>&1 | grep -E '^ +mv ' || true)"
  if [ -n "$cmd" ] && eval "$cmd" 2>/dev/null && [ -d "$SPACED/models/$REAL_ALIAS" ]; then
    pass "the printed rename runs verbatim when the store path has a space"
  else
    fail "the printed rename does not survive a store path with a space: $cmd"
  fi
  rm -rf "$(dirname "$SPACED")"
fi

printf '\n%s\n' "-----"
# A file that asserted nothing must not report success. This one already exited
# 0 in silence once, when sourcing core.sh died on an unbound variable before
# the first assertion - the failure looked exactly like a pass.
EXPECTED=16
if [ "$ran" -ne "$EXPECTED" ]; then
  printf 'FAIL - ran %d assertions, expected %d (the file did not finish)\n' "$ran" "$EXPECTED"
  exit 1
fi
if [ "$fails" -eq 0 ]; then printf 'all %d identity tests passed\n' "$ran"; else printf '%d test(s) failed\n' "$fails"; exit 1; fi
