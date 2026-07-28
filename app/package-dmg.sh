#!/usr/bin/env bash
#
# Build a signed fxlla.app, package it as a .dmg, and optionally notarize.
#
# Usage:
#   app/package-dmg.sh                        build a signed .dmg
#   app/package-dmg.sh --notarize <profile>   also notarize and staple
#   app/package-dmg.sh --check                verify signing prerequisites only
#
# <profile> is a notarytool keychain profile created once, with EITHER an
# app-specific password:
#   xcrun notarytool store-credentials <profile> \
#       --apple-id <email> --team-id V7MBYBHJX6 --password <app-specific-password>
# or an App Store Connect API key (better for automation):
#   xcrun notarytool store-credentials <profile> \
#       --key AuthKey_<KeyID>.p8 --key-id <KeyID> --issuer <IssuerID>
# Credentials live only in your keychain and are never read from the repo.
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=app/sign-lib.sh
. "./sign-lib.sh"

APP="fxlla.app"
DMG="fxlla.dmg"

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
  PROFILE="${2:?usage: --notarize <notarytool-keychain-profile>}"
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
