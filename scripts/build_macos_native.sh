#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${FIRESKY_NATIVE_DIST:-$ROOT/dist/native-macos}"
swift build --package-path "$ROOT/apps/macos" -c release
BIN="$(swift build --package-path "$ROOT/apps/macos" -c release --show-bin-path)"
APP="$OUT/FireSky.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN/FireSkyNative" "$APP/Contents/MacOS/FireSky"
ICON_TMP="$(mktemp -d)"
trap 'rm -rf "$ICON_TMP"' EXIT
swiftc -swift-version 5 -target "$(uname -m)-apple-macos14.0" \
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
/usr/libexec/PlistBuddy -c "Add :NSLocationWhenInUseUsageDescription string FireSky 使用当前位置选择预测点。" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSLocationUsageDescription string FireSky 使用当前位置选择预测点。" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :FireSkyWorkspace string $ROOT" "$PLIST"
codesign --force --sign - "$APP"
codesign --verify --deep --strict "$APP"
printf '%s\n' "$APP"
