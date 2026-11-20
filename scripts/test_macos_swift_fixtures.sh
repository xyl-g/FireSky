#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-${FIRESKY_NATIVE_APP:-$ROOT/dist/native-macos/FireSky.app}}"
FRAMEWORKS="$APP/Contents/Frameworks"
RESOURCES="$APP/Contents/Resources"
TARGET="${FIRESKY_SWIFT_TARGET:-}"
if [ -z "$TARGET" ]; then
    case "$(uname -m)" in
        arm64|aarch64) TARGET="arm64-apple-macos14.0" ;;
        x86_64|amd64) TARGET="x86_64-apple-macos14.0" ;;
        *) echo "unsupported host architecture: $(uname -m)" >&2; exit 2 ;;
    esac
fi

test -f "$FRAMEWORKS/libfiresky_engine.dylib"
test -f "$RESOURCES/app-data/RESOURCE-MANIFEST.json"

FIXTURE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/firesky-swift-fixtures.XXXXXX")"
trap 'rm -rf "$FIXTURE_DIR"' EXIT

swiftc -parse-as-library -swift-version 5 -target "$TARGET" \
    "$ROOT/apps/macos/Sources/FireSkyNative/PreparationActions.swift" \
    "$ROOT/apps/macos/Sources/FireSkyNative/DownloadProbe.swift" \
    "$ROOT/apps/macos/Sources/FireSkyNative/ObservationDate.swift" \
    "$ROOT/apps/macos/Sources/FireSkyNative/ForecastComparison.swift" \
    "$ROOT/apps/macos/Sources/FireSkyNative/MapZoom.swift" \
    "$ROOT/apps/macos/Sources/FireSkyNative/NowcastPresentation.swift" \
    "$ROOT/apps/macos/Sources/FireSkyNative/RegionalForecast.swift" \
    "$ROOT/apps/macos/Sources/FireSkyNative/ProfileQuery.swift" \
    "$ROOT/apps/macos/Sources/FireSkyNative/RegionalMapLayers.swift" \
    "$ROOT/apps/macos/Tests/PreparationActionsChecks.swift" \
    -o "$FIXTURE_DIR/preparation-checks"

for check in \
    missing ready blocked copy date calendar diagnostic-values history-time \
    comparison-empty comparison-pair comparison-partial comparison-curve comparison-invalid \
    aod-shared aod-range aod-partial aod-source \
    nowcast-curve nowcast-metrics nowcast-profile \
    profile-query \
    regional-empty regional-models regional-missing regional-grid regional-camera \
    regional-layers regional-web-palette; do
    "$FIXTURE_DIR/preparation-checks" "$check" >/dev/null
done

swiftc -parse-as-library -swift-version 5 -target "$TARGET" \
    "$ROOT/apps/macos/Sources/FireSkyNative/JSONTypes.swift" \
    "$ROOT/apps/macos/Sources/FireSkyNative/EngineClient.swift" \
    "$ROOT/apps/macos/Sources/FireSkyNative/FFIEngineClient.swift" \
    "$ROOT/apps/macos/Sources/FireSkyNative/SyncHelperInstaller.swift" \
    "$ROOT/apps/macos/Tests/SyncHelperChecks.swift" \
    -o "$FIXTURE_DIR/sync-helper-checks"
"$FIXTURE_DIR/sync-helper-checks" >/dev/null

swiftc -parse-as-library -swift-version 5 -target "$TARGET" \
    "$ROOT/apps/macos/Sources/FireSkyNative/JSONTypes.swift" \
    "$ROOT/apps/macos/Sources/FireSkyNative/Updater.swift" \
    "$ROOT/apps/macos/Tests/UpdaterChecks.swift" \
    -o "$FIXTURE_DIR/updater-checks"
"$FIXTURE_DIR/updater-checks" >/dev/null

swiftc -parse-as-library -swift-version 5 -target "$TARGET" \
    "$ROOT/apps/macos/Sources/FireSkyNative/JSONTypes.swift" \
    "$ROOT/apps/macos/Sources/FireSkyNative/EngineClient.swift" \
    "$ROOT/apps/macos/Sources/FireSkyNative/FFIEngineClient.swift" \
    "$ROOT/tests/fixtures/rust_location_client.swift" \
    -o "$FIXTURE_DIR/rust-location-client"

WORKSPACE="${FIRESKY_FIXTURE_WORKSPACE:-$ROOT}"
ENGINE_BINARY="$RESOURCES/firesky-engine"
FIRESKY_ENGINE_FFI_LIBRARY="$FRAMEWORKS/libfiresky_engine.dylib" \
FIRESKY_RESOURCE_DIR="$RESOURCES/app-data" \
FIRESKY_REQUIRE_APP_RESOURCES=1 \
    "$FIXTURE_DIR/rust-location-client" "$WORKSPACE" "$ENGINE_BINARY" native-default \
    >/dev/null

FIRESKY_ENGINE_FFI_LIBRARY="$FRAMEWORKS/libfiresky_engine.dylib" \
FIRESKY_RESOURCE_DIR="$RESOURCES/app-data" \
FIRESKY_REQUIRE_APP_RESOURCES=1 \
    "$FIXTURE_DIR/rust-location-client" "$WORKSPACE" "$ENGINE_BINARY" restart \
    >/dev/null

echo "macOS Swift fixture checks: ok"
