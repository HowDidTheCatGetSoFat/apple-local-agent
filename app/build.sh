#!/usr/bin/env bash
#
# Build the fxlla menu bar app into a runnable .app bundle.
#
# Usage:
#   app/build.sh                 debug build, unsigned
#   app/build.sh --release       release build
#   app/build.sh --release --sign   release build, signed with Developer ID
#
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=app/sign-lib.sh
. "./sign-lib.sh"

CONFIG=debug
SIGN=0
for a in "$@"; do
  case "$a" in
    --release) CONFIG=release ;;
    --sign)    SIGN=1 ;;
    *) echo "unknown flag: $a" >&2; exit 1 ;;
  esac
done

swift build -c "$CONFIG"
BIN="$(swift build -c "$CONFIG" --show-bin-path)/fxllaMenuBar"

APP="fxlla.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp Resources/Info.plist "$APP/Contents/Info.plist"
cp "$BIN" "$APP/Contents/MacOS/fxllaMenuBar"

if [ "$SIGN" = 1 ]; then
  require_identity
  codesign --force --deep --options runtime --sign "$SIGN_ID" "$APP"
  verify_signed "$APP"
  echo "signed with: $SIGN_ID"
fi

echo "built $APP  (run it: open $APP)"
