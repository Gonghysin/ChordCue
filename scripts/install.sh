#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CHORDCUE_INSTALL_DIR="${CHORDCUE_INSTALL_DIR:-/Applications}"
if pgrep -x ChordCue >/dev/null; then
  echo "Please quit ChordCue before installing." >&2
  exit 1
fi
bash "$PROJECT_DIR/scripts/build.sh"
mkdir -p "$CHORDCUE_INSTALL_DIR"
ditto --norsrc "$PROJECT_DIR/build/ChordCue.app" "$CHORDCUE_INSTALL_DIR/ChordCue.app"
codesign --verify --deep --strict --verbose=2 "$CHORDCUE_INSTALL_DIR/ChordCue.app"
echo "Installed: $CHORDCUE_INSTALL_DIR/ChordCue.app"
echo "Open the app and enable Accessibility in System Settings."
