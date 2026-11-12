#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-${FIRESKY_NATIVE_APP:-$ROOT/dist/native-macos/FireSky.app}}"
FRAMEWORKS="$APP/Contents/Frameworks"
RESOURCES="$APP/Contents/Resources"
STORE="$ROOT/apps/macos/Sources/FireSkyNative/Store.swift"
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/firesky-macos-smoke.XXXXXX")"
trap 'rm -rf "$SMOKE_DIR"' EXIT
SMOKE_WORKSPACE="$ROOT"
if [ "${FIRESKY_CLEAN_MACHINE_SMOKE:-0}" = "1" ]; then
    # Exercise the C ABI with a non-ASCII workspace path. The directory lives
    # below SMOKE_DIR so the trap removes it together with the rest of the run.
    SMOKE_WORKSPACE="$SMOKE_DIR/工作区"
    mkdir -p "$SMOKE_WORKSPACE"
fi

test -x "$APP/Contents/MacOS/FireSky"
test -f "$FRAMEWORKS/libfiresky_engine.dylib"
test -f "$RESOURCES/app-data/RESOURCE-MANIFEST.json"
test -f "$RESOURCES/lib/libeccodes.dylib"
test -f "$STORE"
rg -F -q 'Contents/Frameworks/libfiresky_engine.dylib' \
  "$ROOT/apps/macos/Sources/FireSkyNative/EngineClient.swift"
python3 "$ROOT/scripts/build_app_resources.py" \
  --root "$RESOURCES/app-data" --check
rg -F -q 'if page == .nowcast' "$STORE"
rg -F -q 'value["forecast_mode"] = "nowcast"' "$STORE"
rg -F -q 'value["model_source"] = "satellite"' "$STORE"
rg -F -q 'value["satellite_source"] = "auto"' "$STORE"
rg -F -q 'let backend = environment["FIRESKY_NATIVE_BACKEND"] ?? "ffi"' \
  "$ROOT/apps/macos/Sources/FireSkyNative/EngineClient.swift"
rg -F -q 'if isBundledApp && backend == "jsonl"' \
  "$ROOT/apps/macos/Sources/FireSkyNative/EngineClient.swift"

codesign --verify --deep --strict "$APP"
if [ -n "${FIRESKY_EXPECTED_SIGNING_IDENTITY:-}" ]; then
    SIGNING_IDENTITY="$(
        codesign --display --verbose=4 "$APP" 2>&1 \
            | sed -n 's/^Authority=\(.*\)$/\1/p' | head -n 1
    )"
    if [ "$SIGNING_IDENTITY" != "$FIRESKY_EXPECTED_SIGNING_IDENTITY" ]; then
        echo "unexpected macOS signing identity: ${SIGNING_IDENTITY:-<none>}" >&2
        exit 1
    fi
fi

EXPECTED_ARCH="${FIRESKY_EXPECTED_ARCH:-}"
if [ -n "$EXPECTED_ARCH" ]; then
    case "$EXPECTED_ARCH" in
        arm64|aarch64) EXPECTED_ARCH="arm64" ;;
        x86_64|amd64) EXPECTED_ARCH="x86_64" ;;
        *) echo "unsupported FIRESKY_EXPECTED_ARCH: $EXPECTED_ARCH" >&2; exit 2 ;;
    esac
    for binary in "$APP/Contents/MacOS/FireSky" \
                  "$APP/Contents/Resources/firesky-engine" \
                  "$FRAMEWORKS/libfiresky_engine.dylib"; do
        actual="$(lipo -archs "$binary")"
        case " $actual " in
            *" $EXPECTED_ARCH "*) ;;
            *) echo "unexpected architecture for $binary: $actual" >&2; exit 1 ;;
        esac
    done
fi

for binary in "$APP/Contents/MacOS/FireSky" \
              "$APP/Contents/Resources/firesky-engine" \
              "$FRAMEWORKS/libfiresky_engine.dylib"; do
    if otool -L "$binary" | grep -Eiq 'python|pyside|pyobjc'; then
        echo "production bundle contains a Python runtime dependency: $binary" >&2
        exit 1
    fi
done

"${CC:-clang}" -std=c11 -Wall -Wextra -Werror \
  -I"$ROOT/engine/include" \
  "$ROOT/scripts/smoke_macos_ffi.c" \
  -L"$FRAMEWORKS" -lfiresky_engine \
  -Wl,-rpath,"$FRAMEWORKS" \
  -o "$SMOKE_DIR/firesky-ffi-smoke"

SMOKE_ARCH="${FIRESKY_EXPECTED_ARCH:-$(uname -m)}"
case "$SMOKE_ARCH" in
    arm64|aarch64) SMOKE_ARCH="arm64" ;;
    x86_64|amd64) SMOKE_ARCH="x86_64" ;;
    *) echo "unsupported smoke architecture: $SMOKE_ARCH" >&2; exit 2 ;;
esac
swiftc -parse-as-library -swift-version 5 -target "$SMOKE_ARCH-apple-macos14.0" \
  "$ROOT/scripts/smoke_macos_ffi.swift" \
  -o "$SMOKE_DIR/firesky-swift-ffi-smoke"

FIRESKY_RESOURCE_DIR="$RESOURCES/app-data" \
FIRESKY_REQUIRE_APP_RESOURCES=1 \
  "$SMOKE_DIR/firesky-ffi-smoke" "$SMOKE_WORKSPACE"

# A packaged app must fail closed when its immutable resource bundle is absent;
# it must never silently read source-tree resources from the workspace.
MISSING_RESOURCES="$SMOKE_DIR/missing-app-data"
if FIRESKY_RESOURCE_DIR="$MISSING_RESOURCES" \
   FIRESKY_REQUIRE_APP_RESOURCES=1 \
   "$SMOKE_DIR/firesky-ffi-smoke" "$SMOKE_WORKSPACE" \
   >"$SMOKE_DIR/missing-resources.stdout" 2>"$SMOKE_DIR/missing-resources.stderr"; then
    cat "$SMOKE_DIR/missing-resources.stderr" >&2 || true
    echo "FFI runtime unexpectedly started without packaged application resources" >&2
    exit 1
fi

FIRESKY_RESOURCE_DIR="$RESOURCES/app-data" \
FIRESKY_REQUIRE_APP_RESOURCES=1 \
  "$SMOKE_DIR/firesky-swift-ffi-smoke" \
  "$FRAMEWORKS/libfiresky_engine.dylib" "$SMOKE_WORKSPACE"

FIRESKY_FIXTURE_WORKSPACE="$SMOKE_WORKSPACE" \
  bash "$ROOT/scripts/test_macos_swift_fixtures.sh" "$APP"

if [ "${FIRESKY_APP_SMOKE:-0}" = "1" ]; then
    SNAPSHOT="$SMOKE_DIR/startup.png"
    FIRESKY_RESOURCE_DIR="$RESOURCES/app-data" \
    FIRESKY_REQUIRE_APP_RESOURCES=1 \
    FIRESKY_NATIVE_SNAPSHOT="$SNAPSHOT" \
    FIRESKY_NATIVE_PREFLIGHT=1 \
    FIRESKY_WORKSPACE="$SMOKE_WORKSPACE" \
      "$APP/Contents/MacOS/FireSky" >"$SMOKE_DIR/app.stdout" 2>"$SMOKE_DIR/app.stderr" &
    APP_PID=$!
    for _ in $(seq 1 45); do
        if [ -s "$SNAPSHOT" ]; then break; fi
        if ! kill -0 "$APP_PID" 2>/dev/null; then break; fi
        sleep 1
    done
    if [ ! -s "$SNAPSHOT" ]; then
        kill "$APP_PID" 2>/dev/null || true
        wait "$APP_PID" 2>/dev/null || true
        cat "$SMOKE_DIR/app.stderr" >&2 || true
        echo "macOS application cold-start smoke did not produce a snapshot" >&2
        exit 1
    fi
    if pgrep -P "$APP_PID" -f 'python|pyside|pyobjc' >/dev/null 2>&1; then
        kill "$APP_PID" 2>/dev/null || true
        wait "$APP_PID" 2>/dev/null || true
        echo "macOS application started a Python process" >&2
        exit 1
    fi
    if kill -0 "$APP_PID" 2>/dev/null; then
        kill "$APP_PID" 2>/dev/null || true
        wait "$APP_PID" 2>/dev/null || true
    fi
    test -s "$SNAPSHOT"
    echo "macOS application cold-start smoke: ok"
fi

echo "macOS native bundle smoke: ok"
