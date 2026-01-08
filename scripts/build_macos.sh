#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script is for macOS only."
  exit 1
fi

APP_NAME="DynamicBadge"
ARCH="$(uname -m)"
TARGET_ARCH="${TARGET_ARCH:-$ARCH}"

PYTHON="${PYTHON:-"$ROOT_DIR/.venv/bin/python"}"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="python3"
fi

if ! "$PYTHON" -m PyInstaller --version >/dev/null 2>&1; then
  echo "PyInstaller not found. Install it with: $PYTHON -m pip install -U pyinstaller"
  exit 1
fi

FFMPEG_BIN_DIR="${FFMPEG_BIN_DIR:-"$ROOT_DIR/ffmpeg_bin"}"
if [[ ! -f "$FFMPEG_BIN_DIR/ffmpeg" || ! -f "$FFMPEG_BIN_DIR/ffprobe" ]]; then
  echo "Missing ffmpeg/ffprobe in: $FFMPEG_BIN_DIR"
  echo "Expected files:"
  echo "  - $FFMPEG_BIN_DIR/ffmpeg"
  echo "  - $FFMPEG_BIN_DIR/ffprobe"
  exit 1
fi

if [[ "$TARGET_ARCH" != "x86_64" && "$TARGET_ARCH" != "arm64" && "$TARGET_ARCH" != "universal2" ]]; then
  echo "Invalid TARGET_ARCH: $TARGET_ARCH (expected: x86_64 | arm64 | universal2)"
  exit 1
fi

if command -v file >/dev/null 2>&1; then
  ffmpeg_info="$(file "$FFMPEG_BIN_DIR/ffmpeg" || true)"
  ffprobe_info="$(file "$FFMPEG_BIN_DIR/ffprobe" || true)"
  case "$TARGET_ARCH" in
    universal2)
      if [[ "$ffmpeg_info" != *"universal"* || "$ffmpeg_info" != *"x86_64"* || "$ffmpeg_info" != *"arm64"* ]]; then
        echo "ffmpeg arch mismatch for TARGET_ARCH=universal2: $ffmpeg_info"
        exit 1
      fi
      if [[ "$ffprobe_info" != *"universal"* || "$ffprobe_info" != *"x86_64"* || "$ffprobe_info" != *"arm64"* ]]; then
        echo "ffprobe arch mismatch for TARGET_ARCH=universal2: $ffprobe_info"
        exit 1
      fi
      ;;
    *)
      if [[ "$ffmpeg_info" != *"$TARGET_ARCH"* && "$ffmpeg_info" != *"universal"* ]]; then
        echo "ffmpeg arch mismatch for TARGET_ARCH=$TARGET_ARCH: $ffmpeg_info"
        exit 1
      fi
      if [[ "$ffprobe_info" != *"$TARGET_ARCH"* && "$ffprobe_info" != *"universal"* ]]; then
        echo "ffprobe arch mismatch for TARGET_ARCH=$TARGET_ARCH: $ffprobe_info"
        exit 1
      fi
      ;;
  esac
fi

export PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-"$ROOT_DIR/.pyinstaller"}"
export TMPDIR="${TMPDIR:-"$ROOT_DIR/.tmp"}"
mkdir -p "$PYINSTALLER_CONFIG_DIR" "$TMPDIR"

rm -rf build "dist/$APP_NAME.app"
mkdir -p build

"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --distpath dist \
  --workpath build \
  --specpath build \
  --target-arch "$TARGET_ARCH" \
  --name "$APP_NAME" \
  --icon "$ROOT_DIR/2.icns" \
  main.py

APP_PATH="$ROOT_DIR/dist/$APP_NAME.app"
if [[ ! -d "$APP_PATH" ]]; then
  echo "Build failed: $APP_PATH not found."
  exit 1
fi

BIN_DST="$APP_PATH/Contents/MacOS/ffmpeg_bin"
mkdir -p "$BIN_DST"
cp -f "$FFMPEG_BIN_DIR/ffmpeg" "$BIN_DST/ffmpeg"
cp -f "$FFMPEG_BIN_DIR/ffprobe" "$BIN_DST/ffprobe"
chmod +x "$BIN_DST/ffmpeg" "$BIN_DST/ffprobe"

ZIP_PATH="$ROOT_DIR/dist/$APP_NAME-macos-$TARGET_ARCH.zip"
rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" "$ZIP_PATH"

echo "Created: $ZIP_PATH"
