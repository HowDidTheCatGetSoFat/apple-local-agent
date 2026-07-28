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
  # Developer ID Application identity (see MAINTAINERS). Override with FXLLA_SIGN_ID.
  ID="${FXLLA_SIGN_ID:-Developer ID Application: mariano abad (V7MBYBHJX6)}"
  codesign --force --deep --options runtime --sign "$ID" "$APP"
  echo "signed with: $ID"
fi

echo "built $APP  (run it: open $APP)"
