#!/bin/zsh
set -euo pipefail
DESKTOP_TEST_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$DESKTOP_TEST_ROOT/qa"
if [[ "${NEBULA_TEST_PROVIDER:-0}" == "1" ]]; then
  swift "$DESKTOP_TEST_ROOT/Tests/make_video.swift" "$DESKTOP_TEST_ROOT/qa/fixture.mp4"
fi
swiftc -parse-as-library -o "$DESKTOP_TEST_ROOT/qa/native-checks" \
  "$DESKTOP_TEST_ROOT/Sources/NebulaDesktop/Models.swift" \
  "$DESKTOP_TEST_ROOT/Sources/NebulaDesktop/EngineService.swift" \
  "$DESKTOP_TEST_ROOT/Sources/NebulaDesktop/WebWorkspace.swift" \
  "$DESKTOP_TEST_ROOT/Tests/NativeChecks.swift"
NEBULA_TEST_RESOURCES="$DESKTOP_TEST_ROOT/dist/小小宇宙无敌.app/Contents/Resources" \
  "$DESKTOP_TEST_ROOT/qa/native-checks"
