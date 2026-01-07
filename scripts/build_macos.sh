#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

APP_NAME="${APP_NAME:-DynamicBadge}"
ENTRY="${ENTRY:-main.py}"
ICON="${ICON:-2.icns}"

ENTRY_PATH="$ENTRY"
if [[ "$ENTRY_PATH" != /* ]]; then
  ENTRY_PATH="$ROOT_DIR/$ENTRY_PATH"
fi

ICON_PATH="$ICON"
if [[ "$ICON_PATH" != /* ]]; then
  ICON_PATH="$ROOT_DIR/$ICON_PATH"
fi

if [[ ! -x "$PYTHON" ]]; then
  echo "Python not found (set PYTHON=...): $PYTHON" >&2
  exit 1
fi

PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-$ROOT_DIR/.pyinstaller}"
export PYINSTALLER_CONFIG_DIR

build_one() {
  local target_arch="$1"
  local dist_dir="$ROOT_DIR/dist/macos-$target_arch"
  local work_dir="$ROOT_DIR/build/pyinstaller/macos-$target_arch"

  rm -rf "$dist_dir" "$work_dir"
  mkdir -p "$dist_dir" "$work_dir"

  local -a args=(
    --noconfirm
    --clean
    --windowed
    --name "$APP_NAME"
    --icon "$ICON_PATH"
    --target-architecture "$target_arch"
    --distpath "$dist_dir"
    --workpath "$work_dir"
    --specpath "$work_dir"
  )

  if [[ "$target_arch" == "x86_64" ]]; then
    local ffmpeg_dir="${FFMPEG_X86_64_DIR:-$ROOT_DIR}"
    if [[ -f "$ffmpeg_dir/ffmpeg" && -f "$ffmpeg_dir/ffprobe" ]]; then
      args+=(--add-binary "$ffmpeg_dir/ffmpeg:.")
      args+=(--add-binary "$ffmpeg_dir/ffprobe:.")
    else
      echo "Warning: ffmpeg/ffprobe not found for x86_64, will rely on PATH." >&2
    fi
  fi

  if [[ "$target_arch" == "arm64" ]]; then
    local ffmpeg_dir="${FFMPEG_ARM64_DIR:-}"
    if [[ -n "$ffmpeg_dir" && -f "$ffmpeg_dir/ffmpeg" && -f "$ffmpeg_dir/ffprobe" ]]; then
      args+=(--add-binary "$ffmpeg_dir/ffmpeg:.")
      args+=(--add-binary "$ffmpeg_dir/ffprobe:.")
    elif [[ -f "$ROOT_DIR/ffmpeg_arm64" && -f "$ROOT_DIR/ffprobe_arm64" ]]; then
      local tmp_bin_dir="$work_dir/localbin"
      mkdir -p "$tmp_bin_dir"
      cp "$ROOT_DIR/ffmpeg_arm64" "$tmp_bin_dir/ffmpeg"
      cp "$ROOT_DIR/ffprobe_arm64" "$tmp_bin_dir/ffprobe"
      chmod +x "$tmp_bin_dir/ffmpeg" "$tmp_bin_dir/ffprobe"
      args+=(--add-binary "$tmp_bin_dir/ffmpeg:.")
      args+=(--add-binary "$tmp_bin_dir/ffprobe:.")
    else
      echo "Note: arm64 build does not bundle ffmpeg/ffprobe (install via Homebrew on target Mac)." >&2
    fi
  fi

  "$PYTHON" -m PyInstaller "${args[@]}" "$ENTRY_PATH"

  local app_path="$dist_dir/$APP_NAME.app"
  local zip_path="$ROOT_DIR/dist/$APP_NAME-macos-$target_arch.zip"
  rm -f "$zip_path"
  ditto -c -k --sequesterRsrc --keepParent "$app_path" "$zip_path"

  echo "Built $zip_path"
}

build_one x86_64
build_one arm64
