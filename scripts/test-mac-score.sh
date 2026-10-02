#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEST_DIR="$PROJECT_DIR/.test-tmp/mac-score"
mkdir -p "$TEST_DIR"
xcrun swiftc -parse-as-library "$PROJECT_DIR/Sources/ScoreModels.swift" "$PROJECT_DIR/Sources/LANDevices.swift" \
  "$PROJECT_DIR/Sources/MacTiming.swift" "$PROJECT_DIR/Sources/StandaloneTransport.swift" \
  "$PROJECT_DIR/Sources/MacScoreProject.swift" "$PROJECT_DIR/tests/mac/Issue1Oracle.swift" -o "$TEST_DIR/issue1-oracle"
"$TEST_DIR/issue1-oracle" "$PROJECT_DIR/windows/tests/fixtures/issue1-original.score.json"
"$TEST_DIR/issue1-oracle" "$PROJECT_DIR/windows/tests/fixtures/issue1-original-gp.score.json"
xcrun swiftc -parse-as-library "$PROJECT_DIR/Sources/ScoreModels.swift" "$PROJECT_DIR/Sources/StandaloneTransport.swift" \
  "$PROJECT_DIR/Sources/MacTiming.swift" "$PROJECT_DIR/Sources/MacScoreProject.swift" "$PROJECT_DIR/Sources/ChordTheory.swift" \
  "$PROJECT_DIR/tests/mac/TimingOracle.swift" -o "$TEST_DIR/timing-oracle"
"$TEST_DIR/timing-oracle"
