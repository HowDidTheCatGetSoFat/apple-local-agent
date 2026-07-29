#!/usr/bin/env bash
#
# Civitai reference parsing: extract the numeric model-version id from the
# accepted forms and reject the rest. Does not download.
#
# Run: bash tests/test_civitai.sh
set -euo pipefail

ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FXLLA="$ROOT/bin/fxlla"

fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

id_of() { FXLLA_STORE=/tmp bash "$FXLLA" __civitai-id "$1" 2>/dev/null; }

eq() {  # <desc> <ref> <expected-id>
  local got; got="$(id_of "$2" || true)"
  if [ "$got" = "$3" ]; then pass "$1"; else fail "$1 (got '$got', want '$3')"; fi
}

reject() {  # <desc> <ref>
  if id_of "$2" >/dev/null 2>&1; then fail "$1 (accepted)"; else pass "$1"; fi
}

eq  "civitai: prefix"          "civitai:12345"                                         "12345"
eq  "api download url"         "https://civitai.com/api/download/models/67890"         "67890"
eq  "api url with query"       "https://civitai.com/api/download/models/67890?type=Model" "67890"
eq  "model page modelVersionId" "https://civitai.com/models/999?modelVersionId=42&x=1"  "42"
eq  "modelVersionId with fragment" "https://civitai.com/models/9?modelVersionId=42#gallery" "42"

reject "non-numeric id"        "civitai:abc"
reject "empty id"              "civitai:"
reject "not a civitai ref"     "org/repo"
reject "bare number"           "12345"

if [ "$fails" -ne 0 ]; then printf '\n%d test(s) failed\n' "$fails"; exit 1; fi
printf '\nall civitai tests passed\n'
