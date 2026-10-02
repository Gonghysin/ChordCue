#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="$PROJECT_DIR/build"
APP_PATH="$BUILD_DIR/ChordCue.app"
CHORDCUE_ARCH="${CHORDCUE_ARCH:-$(uname -m)}"
CHORDCUE_SIGNING_IDENTITY="${CHORDCUE_SIGNING_IDENTITY:--}"
case "$CHORDCUE_ARCH" in arm64|x86_64) ;; *) echo "Unsupported architecture: $CHORDCUE_ARCH" >&2; exit 1;; esac
mkdir -p "$APP_PATH/Contents/MacOS" "$APP_PATH/Contents/Resources"
echo "Building ChordCue for ${CHORDCUE_ARCH}…"
xcrun swiftc -O -target "$CHORDCUE_ARCH-apple-macos13.0" -parse-as-library \
  "$PROJECT_DIR/Sources/ChordCue.swift" "$PROJECT_DIR/Sources/LogicReader.swift" \
  "$PROJECT_DIR/Sources/ChordTheory.swift" "$PROJECT_DIR/Sources/ChartPDF.swift" \
  "$PROJECT_DIR/Sources/LANBroadcast.swift" "$PROJECT_DIR/Sources/NativeMetronome.swift" \
  -o "$APP_PATH/Contents/MacOS/ChordCue"
cp "$PROJECT_DIR/Resources/Info.plist" "$APP_PATH/Contents/Info.plist"
cp "$PROJECT_DIR/Resources/Broadcast.html" "$PROJECT_DIR/Resources/Metronome.js" \
  "$PROJECT_DIR/Resources/DemoChords.txt" "$PROJECT_DIR/Resources/ChordCue.svg" "$APP_PATH/Contents/Resources/"
if command -v rsvg-convert >/dev/null 2>&1; then
  mkdir -p "$BUILD_DIR/ChordCue.iconset"
  for size in 16 32 128 256 512; do
    rsvg-convert -w "$size" -h "$size" "$PROJECT_DIR/Resources/ChordCue.svg" -o "$BUILD_DIR/ChordCue.iconset/icon_${size}x${size}.png"
    double_size=$((size * 2))
    rsvg-convert -w "$double_size" -h "$double_size" "$PROJECT_DIR/Resources/ChordCue.svg" -o "$BUILD_DIR/ChordCue.iconset/icon_${size}x${size}@2x.png"
  done
  iconutil -c icns "$BUILD_DIR/ChordCue.iconset" -o "$APP_PATH/Contents/Resources/ChordCue.icns"
  /usr/libexec/PlistBuddy -c 'Add :CFBundleIconFile string ChordCue.icns' "$APP_PATH/Contents/Info.plist"
else
  echo "rsvg-convert not found; building without the optional Dock icon."
fi
cp "$PROJECT_DIR/LICENSE" "$PROJECT_DIR/THIRD_PARTY_NOTICES.md" "$APP_PATH/Contents/Resources/"
mkdir -p "$APP_PATH/Contents/Resources/licenses"
cp "$PROJECT_DIR"/licenses/*.txt "$APP_PATH/Contents/Resources/licenses/"
codesign --force --sign "$CHORDCUE_SIGNING_IDENTITY" --identifier local.codex.chordcue --timestamp=none "$APP_PATH"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"
echo "Built: $APP_PATH"
if [[ "$CHORDCUE_SIGNING_IDENTITY" == "-" ]]; then
  echo "Ad-hoc signing: subsequent builds may require Accessibility authorization again."
fi
