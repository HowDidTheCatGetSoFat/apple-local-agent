#!/usr/bin/env bash
#
# Run every shell suite in tests/.
#
# The list of suites is the glob, not a list kept by hand. CI used to name each
# file twice - once in a syntax-check line, once in its own step - so a new test
# file was only run if someone remembered both places, and a forgotten one
# looked exactly like a suite with nothing to say.
#
# A suite fails here if it exits non-zero OR if it printed no assertions at all.
# The second rule exists because a suite can die before its first check - an
# unbound variable while sourcing lib/core.sh ends the file with exit 0 - and
# silence is otherwise indistinguishable from success.
#
# Run: bash tests/run.sh
set -uo pipefail

ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

suites=0 failed=0 total=0
names=""

# Lint first, the way CI actually lints. Two details that are the whole point:
#
#   * No severity filter. SC2086 - unquoted expansion, in a codebase that is
#     largely path assembly - is only `info`, so `-S warning` would drop exactly
#     the class worth catching. Deliberate findings get an inline disable.
#   * ONE FILE PER INVOCATION. Passing them all at once lets shellcheck follow
#     `source` between them and conclude that a variable set in one and read in
#     another is used; the action lints each file alone and cannot. That gap is
#     not theoretical - it is how two SC2034s reached CI green from here.
lint() {
  local files=() f rc=0
  while IFS= read -r f; do files+=("$f"); done < <(
    find "$ROOT" -path "$ROOT/app/fxlla.app" -prune -o -type f -name '*.sh' -print
  )
  files+=("$ROOT/bin/fxlla")
  if ! command -v shellcheck >/dev/null 2>&1; then
    # Say so out loud. A lint that quietly does nothing is the failure this
    # runner exists to catch.
    printf 'shellcheck SKIPPED - not installed (CI still enforces it)\n\n'
    return 0
  fi
  for f in "${files[@]}"; do
    shellcheck -e SC1090 -e SC1091 "$f" || rc=1
  done
  if [ "$rc" -eq 0 ]; then
    printf '%-28s %3d files       ok\n\n' "shellcheck" "${#files[@]}"
    return 0
  fi
  printf '\n=== shellcheck: FAILED\n\n'
  return 1
}

lint_failed=0
lint || { lint_failed=1; names=" shellcheck"; }

for t in "$ROOT"/tests/test_*.sh; do
  [ -f "$t" ] || continue
  name="$(basename "$t")"
  suites=$((suites + 1))

  if ! bash -n "$t" 2>&1; then
    printf '\n=== %s: SYNTAX ERROR\n' "$name"
    failed=$((failed + 1)); names="$names $name"
    continue
  fi

  out="$(bash "$t" 2>&1)"; rc=$?
  n="$(printf '%s\n' "$out" | grep -cE '^(ok|FAIL)' || true)"
  total=$((total + n))

  if [ "$rc" -ne 0 ]; then
    printf '\n=== %s: FAILED (exit %d, %s assertions)\n%s\n' "$name" "$rc" "$n" "$out"
    failed=$((failed + 1)); names="$names $name"
  elif [ "$n" -eq 0 ]; then
    printf '\n=== %s: FAILED (exit 0 but asserted nothing - it did not reach its checks)\n%s\n' "$name" "$out"
    failed=$((failed + 1)); names="$names $name"
  else
    printf '%-28s %3s assertions  ok\n' "$name" "$n"
  fi
done

printf '\n%s\n' "-----"
if [ "$suites" -eq 0 ]; then
  printf 'FAIL - no suites found under %s/tests\n' "$ROOT"
  exit 1
fi
if [ "$failed" -ne 0 ] || [ "$lint_failed" -ne 0 ]; then
  printf '%d of %d suites failed' "$failed" "$suites"
  [ "$lint_failed" -ne 0 ] && printf ', and shellcheck failed'
  printf ':%s\n' "$names"
  exit 1
fi
printf 'all %d suites passed (%d assertions), shellcheck clean\n' "$suites" "$total"
