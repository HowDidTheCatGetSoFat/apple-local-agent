#!/usr/bin/env bash
#
# Build the fxlla menu bar app into a runnable .app bundle.
#
# Usage:
#   app/build.sh                 debug build, unsigned
#   app/build.sh --release       release build
#   app/build.sh --release --sign   release build, signed with Developer ID
#
# Re-exec under bash if started by another shell (for example: zsh app/build.sh).
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
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

# Bundle the CLI so an install from the .dmg is self-contained: the app is a
# front end for `fxlla`, and without this it can only drive a CLI the user
# already cloned. bin/fxlla resolves symlinks before computing REPO_ROOT, so
# linking it onto PATH from here works (see "Install the command" in the app).
CLI_DIR="$APP/Contents/Resources/cli"
mkdir -p "$CLI_DIR/config"
for d in bin lib gateway rag graph media skills; do
  cp -R "../$d" "$CLI_DIR/"
done
# config is copied file by file on purpose: config/config.env is git-ignored and
# holds the user's tokens, so it must never end up in a distributable bundle.
cp ../config/models.conf ../config/config.env.example "$CLI_DIR/config/"
find "$CLI_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$CLI_DIR" -name 'test_*.py' -delete
if [ -e "$CLI_DIR/config/config.env" ]; then
  echo "refusing to bundle config/config.env (it holds tokens)" >&2
  exit 1
fi

if [ "$SIGN" = 1 ]; then
  require_identity
  codesign --force --deep --options runtime --sign "$SIGN_ID" "$APP"
  verify_signed "$APP"
  echo "signed with: $SIGN_ID"
fi

echo "built $APP  (run it: open $APP)"
