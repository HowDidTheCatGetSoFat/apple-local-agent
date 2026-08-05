#!/usr/bin/env bash
#
# Build a signed fxlla.app, package it as a .dmg, and optionally notarize.
#
# Usage:
#   app/package-dmg.sh                        build a signed .dmg
#   app/package-dmg.sh --notarize [profile]   also notarize and staple
#   app/package-dmg.sh --check                verify signing prerequisites only
#
# The profile name is OPTIONAL: without one this reads FXLLA_NOTARY_PROFILE,
# from the environment or from a config.env (see _load_notary_config below).
# That default exists because the name was the thing nobody remembered. Asked
# whether this project had ever notarized, the honest-looking answer came from
# searching the keychain with the wrong command, finding nothing, and reporting
# that there were no credentials - while three submissions sat Accepted in
# Apple's history and the profile name sat in a config file. A name you must
# remember to pass is a name that gets forgotten; `--check` now prints it.
#
# <profile> is a notarytool keychain profile created once, with EITHER an
# app-specific password:
#   xcrun notarytool store-credentials <profile> \
#       --apple-id <email> --team-id V7MBYBHJX6 --password <app-specific-password>
# or an App Store Connect API key (better for automation):
#   xcrun notarytool store-credentials <profile> \
#       --key AuthKey_<KeyID>.p8 --key-id <KeyID> --issuer <IssuerID>
# The SECRET always lives in the keychain and is never read from the repo. Only
# the profile NAME is read from config, and a name is not a credential.
# Re-exec under bash if started by another shell (for example: zsh app/build.sh).
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=app/sign-lib.sh
. "./sign-lib.sh"

APP="fxlla.app"
DMG="fxlla.dmg"

# Read FXLLA_NOTARY_PROFILE without letting a config file clobber the shell.
#
# Two locations, because there are two files and they are not the same one.
# `lib/core.sh` loads ~/.config/fxlla/config.env - that is the CLI's config.
# Release settings ended up in the repo's own git-ignored config/config.env,
# which nothing loads automatically, so the one file a reader would think to
# open was the one that did not have them. Both are read here, repo first,
# and an exported value still wins.
#
# Only FXLLA_NOTARY_PROFILE is taken. The Apple ID and the password in those
# files are not needed - notarytool reads the secret from the keychain - and a
# script that sources a whole config to reach one name is a script that can
# export a password by accident.
_load_notary_config() {
  [ -n "${FXLLA_NOTARY_PROFILE:-}" ] && return 0
  local f
  for f in "../config/config.env" "${XDG_CONFIG_HOME:-$HOME/.config}/fxlla/config.env"; do
    [ -f "$f" ] || continue
    local line
    line="$(grep -E '^[[:space:]]*FXLLA_NOTARY_PROFILE=' "$f" | tail -1 || true)"
    [ -n "$line" ] || continue
    FXLLA_NOTARY_PROFILE="$(printf '%s' "${line#*=}" | tr -d "\"'")"
    [ -n "$FXLLA_NOTARY_PROFILE" ] && return 0
  done
  return 0
}

require_tools() {
  _sign_have codesign || _sign_die "codesign not found (install the Xcode command line tools)"
  _sign_have hdiutil  || _sign_die "hdiutil not found"
  _sign_have xcrun    || _sign_die "xcrun not found"
}

# Validate the signing environment without building anything.
run_check() {
  require_tools
  require_identity
  local nt; nt="$(xcrun --find notarytool 2>/dev/null || true)"
  echo "ok: signing prerequisites satisfied"
  echo "  identity:   $SIGN_ID"
  echo "  notarytool: ${nt:-missing (needed only for --notarize)}"
  # The profile, and whether it actually works, because "do we have
  # credentials?" was answered wrong once by inspecting the keychain instead of
  # asking the service. `notarytool history` is the cheap authoritative test:
  # it either lists past submissions or it fails to authenticate.
  _load_notary_config
  if [ -z "${FXLLA_NOTARY_PROFILE:-}" ]; then
    echo "  notary profile: none set (export FXLLA_NOTARY_PROFILE or pass it to --notarize)"
  elif [ -z "$nt" ]; then
    echo "  notary profile: $FXLLA_NOTARY_PROFILE (unverified, notarytool missing)"
  elif xcrun notarytool history --keychain-profile "$FXLLA_NOTARY_PROFILE" >/dev/null 2>&1; then
    echo "  notary profile: $FXLLA_NOTARY_PROFILE (authenticates)"
  else
    echo "  notary profile: $FXLLA_NOTARY_PROFILE (set, but does NOT authenticate)"
  fi
}

if [ "${1:-}" = "--check" ]; then
  run_check
  exit 0
fi

require_tools
require_identity

./build.sh --release --sign   # build.sh --sign verifies the signature itself

rm -f "$DMG"
STAGING="$(mktemp -d)"
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
hdiutil create -volname "fxlla" -srcfolder "$STAGING" -ov -format UDZO "$DMG"
rm -rf "$STAGING"
codesign --force --sign "$SIGN_ID" "$DMG"

if [ "${1:-}" = "--notarize" ]; then
  _load_notary_config
  PROFILE="${2:-${FXLLA_NOTARY_PROFILE:-}}"
  [ -n "$PROFILE" ] || _sign_die "no notarytool profile.
       Pass one: app/package-dmg.sh --notarize <profile>
       or set FXLLA_NOTARY_PROFILE in config/config.env (git-ignored).
       'app/package-dmg.sh --check' reports which profile is found and whether
       it authenticates; 'xcrun notarytool history --keychain-profile <name>'
       lists past submissions, which is how to tell whether this project has
       ever notarized rather than guessing from the keychain."
  echo "submitting $DMG to the notary service (profile: $PROFILE)..."
  xcrun notarytool submit "$DMG" --keychain-profile "$PROFILE" --wait \
    || _sign_die "notarization failed; check the profile '$PROFILE' and your credentials"
  xcrun stapler staple "$DMG"    || _sign_die "stapling failed"
  xcrun stapler validate "$DMG"  || _sign_die "staple validation failed"
  # Gatekeeper assessment is best-effort: spctl on a disk image can be strict.
  spctl --assess -vv --type open --context context:primary-signature "$DMG" \
    || echo "warning: Gatekeeper assessment (spctl) did not pass"
  echo "notarized and stapled"
fi

echo "built $DMG"
