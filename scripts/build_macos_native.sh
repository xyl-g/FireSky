#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${FIRESKY_NATIVE_DIST:-$ROOT/dist/native-macos}"
CODESIGN_IDENTITY="${FIRESKY_CODESIGN_IDENTITY:--}"
CODESIGN_ENTITLEMENTS="${FIRESKY_CODESIGN_ENTITLEMENTS:-}"

sign_nested() {
    local path="$1"
    if [ "$CODESIGN_IDENTITY" = "-" ]; then
        codesign --force --sign - "$path"
    else
        codesign --force --options runtime --timestamp \
            --sign "$CODESIGN_IDENTITY" "$path"
    fi
}

sign_app() {
    local path="$1"
    local args=(--force)
    if [ "$CODESIGN_IDENTITY" = "-" ]; then
        args+=(--sign -)
    else
        args+=(--options runtime --timestamp --sign "$CODESIGN_IDENTITY")
    fi
    if [ -n "$CODESIGN_ENTITLEMENTS" ]; then
        test -f "$CODESIGN_ENTITLEMENTS"
        args+=(--entitlements "$CODESIGN_ENTITLEMENTS")
    fi
    codesign "${args[@]}" "$path"
}

# Keep the target explicit so an Intel build cannot accidentally bundle an
# Apple Silicon engine (or vice versa). CI supplies FIRESKY_MACOS_ARCH on each
# native runner; local builds default to the current host architecture.
HOST_ARCH="$(uname -m)"
case "$HOST_ARCH" in
    arm64|aarch64) HOST_ARCH="arm64"; HOST_SWIFT_TARGET="arm64-apple-macosx14.0" ;;
    x86_64|amd64) HOST_ARCH="x86_64"; HOST_SWIFT_TARGET="x86_64-apple-macosx14.0" ;;
    *) echo "unsupported host architecture: $HOST_ARCH" >&2; exit 2 ;;
esac
MACOS_ARCH="${FIRESKY_MACOS_ARCH:-$HOST_ARCH}"
case "$MACOS_ARCH" in
    arm64|aarch64)
        MACOS_ARCH="arm64"
        CARGO_TARGET="aarch64-apple-darwin"
        SWIFT_TARGET="arm64-apple-macosx14.0"
        SWIFT_ARCH="arm64"
        ;;
    x86_64|amd64)
        MACOS_ARCH="x86_64"
        CARGO_TARGET="x86_64-apple-darwin"
        SWIFT_TARGET="x86_64-apple-macosx14.0"
        SWIFT_ARCH="x86_64"
        ;;
    *)
        echo "unsupported FIRESKY_MACOS_ARCH: $MACOS_ARCH (expected arm64 or x86_64)" >&2
        exit 2
        ;;
esac

if [ "$HOST_ARCH" != "$MACOS_ARCH" ] && [ -z "${FIRESKY_ECCODES_LIBRARY:-}" ] &&
   [ -z "${FIRESKY_ECCODES_PREFIX:-}" ]; then
    cat >&2 <<EOF
cross-architecture macOS builds require FIRESKY_ECCODES_LIBRARY or
FIRESKY_ECCODES_PREFIX for a decoder built for $MACOS_ARCH
EOF
    exit 2
fi

cargo build --manifest-path "$ROOT/engine/Cargo.toml" --release --locked \
    --target "$CARGO_TARGET" --lib --bin firesky-engine
swift build --package-path "$ROOT/apps/macos" -c release --triple "$SWIFT_TARGET"
BIN="$(swift build --package-path "$ROOT/apps/macos" -c release \
    --triple "$SWIFT_TARGET" --show-bin-path)"
ENGINE_OUT="$ROOT/engine/target/$CARGO_TARGET/release"
APP="$OUT/FireSky.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$APP/Contents/Frameworks"
cp "$BIN/FireSkyNative" "$APP/Contents/MacOS/FireSky"
cp "$ENGINE_OUT/firesky-engine" "$APP/Contents/Resources/firesky-engine"
cp "$ENGINE_OUT/libfiresky_engine.dylib" "$APP/Contents/Frameworks/libfiresky_engine.dylib"
install_name_tool -id "@rpath/libfiresky_engine.dylib" "$APP/Contents/Frameworks/libfiresky_engine.dylib"
install_name_tool -add_rpath "@executable_path/../Frameworks" "$APP/Contents/MacOS/FireSky"
python3 "$ROOT/scripts/build_app_resources.py" \
  --root "$ROOT/resources/app-data" --check
cp -R "$ROOT/resources/app-data" "$APP/Contents/Resources/app-data"
bash "$ROOT/scripts/bundle_macos_eccodes.sh" "$APP/Contents/Resources"
cp "$ROOT/LICENSE" "$APP/Contents/Resources/LICENSE"
sign_nested "$APP/Contents/Resources/firesky-engine"
sign_nested "$APP/Contents/Frameworks/libfiresky_engine.dylib"
ICON_TMP="$(mktemp -d)"
trap 'rm -rf "$ICON_TMP"' EXIT
swiftc -swift-version 5 -target "$HOST_SWIFT_TARGET" \
  "$ROOT/apps/macos/Sources/FireSkyNative/BrandMark.swift" \
  "$ROOT/apps/macos/Tools/RenderBrandIcon.swift" -o "$ICON_TMP/render-icon"
"$ICON_TMP/render-icon" "$ICON_TMP/FireSky.iconset"
iconutil -c icns "$ICON_TMP/FireSky.iconset" -o "$APP/Contents/Resources/FireSky.icns"
PLIST="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Clear dict" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleExecutable string FireSky" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string app.firesky.native" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleName string FireSky" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundlePackageType string APPL" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string 0.2.0" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string 1" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string FireSky.icns" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 14.0" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :LSArchitecturePriority array" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :LSArchitecturePriority:0 string $SWIFT_ARCH" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSLocationWhenInUseUsageDescription string FireSky 使用当前位置选择预测点。" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSLocationUsageDescription string FireSky 使用当前位置选择预测点。" "$PLIST"
if [ -n "${FIRESKY_NATIVE_WORKSPACE:-}" ]; then
    /usr/libexec/PlistBuddy -c "Add :FireSkyWorkspace string $FIRESKY_NATIVE_WORKSPACE" "$PLIST"
fi
sign_app "$APP"
codesign --verify --deep --strict "$APP"
printf '%s\n' "$APP"
