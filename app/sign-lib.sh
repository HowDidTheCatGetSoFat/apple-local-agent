# Shared signing helpers for build.sh and package-dmg.sh.
# shellcheck shell=bash
# shellcheck disable=SC2034  # SIGN_ID is consumed by the sourcing scripts

# The Developer ID Application identity (see MAINTAINERS). Override with
# FXLLA_SIGN_ID to sign with a different certificate.
SIGN_ID="${FXLLA_SIGN_ID:-Developer ID Application: mariano abad (V7MBYBHJX6)}"

_sign_die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
_sign_have() { command -v "$1" >/dev/null 2>&1; }

# Fail unless the signing certificate is present in the keychain. Notarization
# and Gatekeeper both require signing, so catch a missing identity before any
# slow build work. The identity list is captured before matching: piping into
# `grep -q` under `set -o pipefail` would let grep's early exit SIGPIPE the
# producer and fail the check despite a match.
require_identity() {
  local ids
  ids="$(security find-identity -v -p codesigning 2>/dev/null || true)"
  case "$ids" in
    *"$SIGN_ID"*) return 0 ;;
  esac
  local avail
  avail="$(printf '%s\n' "$ids" | grep -i 'Developer ID Application' || true)"
  [ -n "$avail" ] || avail="       (none)"
  _sign_die "signing identity not found in keychain: $SIGN_ID
       available Developer ID Application identities:
$avail
       set FXLLA_SIGN_ID to one of the above, or import the certificate."
}

# Fail unless the bundle is validly signed with the hardened runtime, which the
# notary service requires. Output is captured before matching for the same
# pipefail/SIGPIPE reason as require_identity.
verify_signed() {
  codesign --verify --strict --verbose=2 "$1" >/dev/null 2>&1 \
    || _sign_die "codesign verification failed for $1"
  local info
  info="$(codesign -d --verbose=2 "$1" 2>&1 || true)"
  case "$info" in
    *"(runtime)"*) : ;;
    *) _sign_die "$1 is not signed with the hardened runtime (--options runtime)" ;;
  esac
}
