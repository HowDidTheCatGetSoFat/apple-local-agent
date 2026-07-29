#!/usr/bin/env bash
#
# The LaunchDaemon plist for persisting the GPU wired limit is well-formed and
# carries the requested value. Does not touch the system (no sudo, no launchctl).
#
# Run: bash tests/test_ram.sh
set -euo pipefail

ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FXLLA="$ROOT/bin/fxlla"

fails=0
pass() { printf 'ok   - %s\n' "$1"; }
fail() { printf 'FAIL - %s\n' "$1"; fails=$((fails + 1)); }

plist="$(FXLLA_STORE=/tmp bash "$FXLLA" __ram-plist 122880)"

has() {  # <desc> <needle>
  if printf '%s\n' "$plist" | grep -qF -- "$2"; then pass "$1"; else fail "$1"; fi
}

has "has the label"            "<string>com.fxlla.wiredlimit</string>"
has "runs sysctl"              "/usr/sbin/sysctl"
has "carries the requested mb" "iogpu.wired_limit_mb=122880"
has "runs at load"             "<key>RunAtLoad</key>"
has "is a plist document"      "<plist version=\"1.0\">"

if [ "$fails" -ne 0 ]; then printf '\n%d test(s) failed\n' "$fails"; exit 1; fi
printf '\nall ram tests passed\n'
