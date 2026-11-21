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
    # v1.9.8：runner 上的 cold-start 曾现 Trace/BPT（本地不可复现的时序型
    # 崩溃）。2 次尝试 + 崩溃报告收集（DiagnosticReports 最近 1 份），
    # 重试通过即继续；两次全败才退出并附全部诊断。
    app_attempt() {
        : > "$SMOKE_DIR/app.stdout"; : > "$SMOKE_DIR/app.stderr"
        FIRESKY_RESOURCE_DIR="$RESOURCES/app-data" \
        FIRESKY_REQUIRE_APP_RESOURCES=1 \
        FIRESKY_NATIVE_SNAPSHOT="$SNAPSHOT" \
        FIRESKY_NATIVE_PREFLIGHT=1 \
        FIRESKY_WORKSPACE="$SMOKE_WORKSPACE" \
          "$APP/Contents/MacOS/FireSky" >"$SMOKE_DIR/app.stdout" 2>"$SMOKE_DIR/app.stderr" &
        APP_PID=$!
        for _ in $(seq 1 180); do
            if [ -s "$SNAPSHOT" ]; then return 0; fi
            if ! kill -0 "$APP_PID" 2>/dev/null; then return 1; fi
            sleep 1
        done
        return 2
    }
    smoke_diag() {
        echo "--- app.stdout ---" >&2
        cat "$SMOKE_DIR/app.stdout" >&2 || true
        echo "--- app.stderr ---" >&2
        cat "$SMOKE_DIR/app.stderr" >&2 || true
        ps -o pid,stat,%cpu,etime -p "$APP_PID" >&2 || true
        sample "$APP_PID" 1 100 >&2 || true
        # 崩溃报告：BPT trap 时系统会写 DiagnosticReports，取出最新一份。
        local crash
        crash=$(ls -t ~/Library/Logs/DiagnosticReports/FireSky* 2>/dev/null | head -1)
        if [ -n "$crash" ]; then
            echo "--- crash report: $crash ---" >&2
            head -c 8000 "$crash" >&2 || true
        fi
        kill "$APP_PID" 2>/dev/null || true
        wait "$APP_PID" 2>/dev/null || true
    }
    app_attempt
    rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "cold-start attempt 1 failed (rc=$rc); collecting diagnostics and retrying" >&2
        smoke_diag
        rm -f "$SNAPSHOT"
        app_attempt
        rc=$?
    fi
    if [ "$rc" -ne 0 ]; then
        echo "cold-start attempt 2 failed (rc=$rc); collecting diagnostics" >&2
        smoke_diag
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

# DMG 安装布局：应用旁必须有指向 /Applications 的符号链接（拖拽安装），
# 且应用本体与引擎资源可从只读挂载访问。
DMG_PATH="$(compgen -G "$ROOT/dist/native-macos/FireSky-*.dmg" | sort | tail -1 || true)"
if [ -n "$DMG_PATH" ]; then
    DMG_MOUNT="$(mktemp -d "${TMPDIR:-/tmp}/firesky-dmg-mount.XXXXXX")"
    hdiutil attach -nobrowse -readonly -mountpoint "$DMG_MOUNT" "$DMG_PATH" >/dev/null
    test -d "$DMG_MOUNT/FireSky.app"
    test -x "$DMG_MOUNT/FireSky.app/Contents/MacOS/FireSky"
    test -L "$DMG_MOUNT/Applications"
    test "$(readlink "$DMG_MOUNT/Applications")" = "/Applications"
    hdiutil detach "$DMG_MOUNT" >/dev/null
    rm -rf "$DMG_MOUNT"
    echo "macOS DMG install layout: ok"
fi

echo "macOS native bundle smoke: ok"
