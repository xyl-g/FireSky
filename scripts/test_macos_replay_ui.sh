#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACE="${1:?usage: test_macos_replay_ui.sh WORKSPACE [point|regional]}"
KIND="${2:-point}"
APP="${FIRESKY_NATIVE_APP:-$ROOT/dist/native-macos/FireSky.app}"
OUT="$WORKSPACE/evidence/ui"
mkdir -p "$OUT"
SOURCES=()
while IFS= read -r source; do SOURCES+=("$source"); done < <(
    rg --files "$ROOT/apps/macos/Sources/FireSkyNative" -g '*.swift' -g '!FireSkyApp.swift'
)
swiftc -parse-as-library -swift-version 5 -target "$(uname -m)-apple-macos14.0" \
    "${SOURCES[@]}" "$ROOT/apps/macos/Tests/TaskHistoryChecks.swift" \
    "$ROOT/apps/macos/Tests/WorkbenchSnapshot.swift" -o "$OUT/replay-ui"

for model in gfs ec; do
    for size in wide narrow; do
        width=1440; height=1040
        if [ "$size" = narrow ]; then width=1120; height=720; fi
        regional=0
        if [ "$KIND" = regional ]; then regional=1; fi
        rm -f "$OUT/$model-$size.png"
        FIRESKY_WORKSPACE="$WORKSPACE" \
        FIRESKY_ENGINE_FFI_LIBRARY="$APP/Contents/Frameworks/libfiresky_engine.dylib" \
        FIRESKY_RESOURCE_DIR="$APP/Contents/Resources/app-data" \
        FIRESKY_REQUIRE_APP_RESOURCES=1 \
        FIRESKY_TEST_REPLAY="$WORKSPACE" \
        FIRESKY_TEST_READ_PROFILE=1 \
        FIRESKY_TEST_MODEL="$model" \
        FIRESKY_TEST_REGIONAL="$regional" \
        FIRESKY_TEST_TAB="大气剖面" \
        FIRESKY_NATIVE_APPEARANCE=light \
        FIRESKY_NATIVE_WIDTH="$width" FIRESKY_NATIVE_HEIGHT="$height" \
        FIRESKY_NATIVE_SNAPSHOT="$OUT/$model-$size.png" \
            /usr/bin/sandbox-exec -p '(version 1)(allow default)(deny network*)' \
            "$OUT/replay-ui" >"$OUT/$model-$size.log" 2>&1
        test -s "$OUT/$model-$size.png"
        rg -q 'Historical replay FFI profile: ready' "$OUT/$model-$size.log"
    done
done
echo "macOS historical replay UI: ok"
