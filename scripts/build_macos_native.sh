#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${FIRESKY_NATIVE_DIST:-$ROOT/dist/native-macos}"
CODESIGN_IDENTITY="${FIRESKY_CODESIGN_IDENTITY:--}"
CODESIGN_ENTITLEMENTS="${FIRESKY_CODESIGN_ENTITLEMENTS:-}"
# Optional cargo target dir override (e.g. keep artifacts outside repo paths
# that third-party cleaners may purge). Defaults to engine/target.
CARGO_TARGET_ROOT="${FIRESKY_CARGO_TARGET_DIR:-$ROOT/engine/target}"
export CARGO_TARGET_DIR="$CARGO_TARGET_ROOT"

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
ENGINE_OUT="$CARGO_TARGET_ROOT/$CARGO_TARGET/release"
if [ ! -x "$ENGINE_OUT/firesky-engine" ] || \
   [ ! -f "$ENGINE_OUT/libfiresky_engine.dylib" ]; then
    echo "engine artifacts missing under $ENGINE_OUT; aborting before replacing $APP" >&2
    exit 1
fi
APP="$OUT/FireSky.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$APP/Contents/Frameworks"
cp "$BIN/FireSkyNative" "$APP/Contents/MacOS/FireSky"
cp "$BIN/FireSkySyncHelper" "$APP/Contents/MacOS/FireSkySyncHelper"
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
sign_nested "$APP/Contents/MacOS/FireSkySyncHelper"
sign_nested "$APP/Contents/Frameworks/libfiresky_engine.dylib"
ICON_TMP="$(mktemp -d)"
trap 'rm -rf "$ICON_TMP"' EXIT
swiftc -swift-version 5 -target "$HOST_SWIFT_TARGET" \
  "$ROOT/apps/macos/Sources/FireSkyNative/BrandMark.swift" \
  "$ROOT/apps/macos/Tools/RenderBrandIcon.swift" -o "$ICON_TMP/render-icon"
"$ICON_TMP/render-icon" "$ICON_TMP/FireSky.iconset"
iconutil -c icns "$ICON_TMP/FireSky.iconset" -o "$APP/Contents/Resources/FireSky.icns"
PLIST="$APP/Contents/Info.plist"
# Single source of truth for the release version: engine/Cargo.toml.
ENGINE_VERSION="$(sed -n 's/^version = "\([^"]*\)"/\1/p' "$ROOT/engine/Cargo.toml" | head -1)"
test -n "$ENGINE_VERSION" || { echo "cannot read version from engine/Cargo.toml" >&2; exit 1; }
/usr/libexec/PlistBuddy -c "Clear dict" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleExecutable string FireSky" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string app.firesky.native" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleName string FireSky" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundlePackageType string APPL" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $ENGINE_VERSION" "$PLIST"
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
# codesign --deep --strict rejects bundles containing symlinks with invalid
# destinations; surface the offending paths instead of a cryptic codesign error.
BUNDLE_LINKS="$(find "$APP" -type l)"
if [ -n "$BUNDLE_LINKS" ]; then
    printf 'bundle contains symbolic links (codesign --strict will reject):\n%s\n' "$BUNDLE_LINKS" >&2
    exit 1
fi
sign_app "$APP"
codesign --verify --deep --strict "$APP"

# Package a distributable DMG around the signed app. The staging layout puts
# the app next to an /Applications symlink so Finder shows the classic
# drag-to-install window; the app itself also offers to self-move when
# launched from a mounted image (see FireSkyApp.move-to-Applications logic).
# Set FIRESKY_BUILD_DMG=0 to skip.
DMG_STAGING=""
trap 'rm -rf "$ICON_TMP" "${DMG_STAGING:-}"' EXIT
if [ "${FIRESKY_BUILD_DMG:-1}" = "1" ]; then
    VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$PLIST")"
    DMG="$OUT/FireSky-$VERSION-macos-$MACOS_ARCH.dmg"
    rm -f "$DMG"
    DMG_STAGING="$(mktemp -d "${TMPDIR:-/tmp}/firesky-dmg.XXXXXX")"
    cp -R "$APP" "$DMG_STAGING/FireSky.app"
    ln -s /Applications "$DMG_STAGING/Applications"
    # hdiutil create 偶发 "Resource busy"（runner 后台服务占用映像服务），
    # 间隔重试三次再放弃——构建本身是确定性的，重试只覆盖瞬时冲突。
    created=0
    for attempt in 1 2 3; do
        if hdiutil create -volname "FireSky $VERSION" -srcfolder "$DMG_STAGING" -ov -format UDZO "$DMG"; then
            created=1
            break
        fi
        echo "hdiutil create attempt $attempt failed; retrying in 15s" >&2
        rm -f "$DMG"
        sleep 15
    done
    [ "$created" = "1" ] || { echo "DMG creation failed after retries" >&2; exit 1; }
    hdiutil verify "$DMG"
    printf '%s\n' "$DMG"
fi

# Repeated builds leave superseded crate artifacts (same crate name, older
# metadata hash) and incremental sessions behind. Move that residue to the
# Trash after a successful bundle so the shared target dir stays bounded.
# Set FIRESKY_BUILD_CLEAN=0 to keep everything for debugging.
if [ "${FIRESKY_BUILD_CLEAN:-1}" = "1" ]; then
    python3 - "$CARGO_TARGET_ROOT" <<'PY'
import os, re, shutil, sys, time
root = sys.argv[1]
trash = os.path.expanduser("~/.Trash")
stamp = time.strftime("%Y%m%d-%H%M%S")
moved = 0
def to_trash(path):
    global moved
    dest = os.path.join(trash, f"firesky-build-residue-{stamp}-{os.path.basename(path)}")
    try:
        shutil.move(path, dest)
        moved += 1
    except OSError:
        pass
for dirpath, dirnames, filenames in os.walk(root):
    if os.path.basename(dirpath) == "incremental":
        for name in list(dirnames):
            to_trash(os.path.join(dirpath, name))
        dirnames[:] = []
        continue
    if os.path.basename(dirpath) != "deps":
        continue
    groups = {}
    for name in filenames:
        match = re.match(r"^(lib)?([A-Za-z0-9_]+)-[0-9a-f]{16}(\..+)?$", name)
        if match:
            # Codegen objects carry a unique CGU id in their extension; group
            # them by crate so superseded generations are actually pruned.
            ext = ".rcgu.o" if name.endswith(".rcgu.o") else (match.group(3) or "")
            groups.setdefault((match.group(2), ext), []).append(name)
    for names in groups.values():
        if len(names) < 3:
            continue
        names.sort(key=lambda n: os.path.getmtime(os.path.join(dirpath, n)), reverse=True)
        # Keep the two newest hashes: the fresh bundle and the previously
        # running app may hardlink different generations.
        for name in names[2:]:
            to_trash(os.path.join(dirpath, name))
    # Codegen intermediates are regenerable; later builds reuse the linked
    # binaries/rlibs, not the per-CGU objects. Drop them after every bundle.
    for name in filenames:
        if name.endswith(".rcgu.o"):
            to_trash(os.path.join(dirpath, name))
print(f"build residue: moved {moved} superseded artifacts to Trash")
PY
fi
printf '%s\n' "$APP"
