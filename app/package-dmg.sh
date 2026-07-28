#!/usr/bin/env bash
#
# Build a signed fxlla.app, package it as a .dmg, and optionally notarize.
#
# Usage:
#   app/package-dmg.sh                          signed .dmg
#   app/package-dmg.sh --notarize <profile>     also notarize and staple
#
# <profile> is a notarytool keychain profile created once with:
#   xcrun notarytool store-credentials <profile> --apple-id ... --team-id ... --password ...
#
set -euo pipefail
cd "$(dirname "$0")"

./build.sh --release --sign

APP="fxlla.app"
DMG="fxlla.dmg"
SIGN_ID="${FXLLA_SIGN_ID:-Developer ID Application: mariano abad (V7MBYBHJX6)}"

rm -f "$DMG"
STAGING="$(mktemp -d)"
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
hdiutil create -volname "fxlla" -srcfolder "$STAGING" -ov -format UDZO "$DMG"
rm -rf "$STAGING"
codesign --force --sign "$SIGN_ID" "$DMG"

if [ "${1:-}" = "--notarize" ]; then
  PROFILE="${2:?usage: --notarize <notarytool-keychain-profile>}"
  xcrun notarytool submit "$DMG" --keychain-profile "$PROFILE" --wait
  xcrun stapler staple "$DMG"
  echo "notarized and stapled"
fi

echo "built $DMG"
