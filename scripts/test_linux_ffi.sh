#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUILD=${FIRESKY_LINUX_BUILD_DIR:-"$ROOT/build/linux-ffi"}
LIBRARY=${FIRESKY_ENGINE_FFI_LIBRARY:-"$ROOT/engine/target/release/libfiresky_engine.so"}
RESOURCE_DIR=${FIRESKY_RESOURCE_DIR:-"$ROOT/resources/app-data"}
SMOKE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/firesky-linux-ffi.XXXXXX")
trap 'rm -rf "$SMOKE_DIR"' EXIT

case "$(uname -s)" in
    Linux) ;;
    *)
        echo "Linux FFI smoke: skipped (host is $(uname -s))"
        exit 0
        ;;
esac

if [ ! -f "$LIBRARY" ]; then
    cargo build --manifest-path "$ROOT/engine/Cargo.toml" --release --locked --lib
fi

cmake -S "$ROOT/apps/linux" -B "$BUILD" -DCMAKE_BUILD_TYPE=Release
cmake --build "$BUILD" --parallel

FIRESKY_RESOURCE_DIR="$RESOURCE_DIR" \
FIRESKY_REQUIRE_APP_RESOURCES=1 \
FIRESKY_RETENTION_SETTINGS_PATH="$SMOKE_DIR/retention.json" \
    "$BUILD/firesky_linux_ffi_smoke" "$LIBRARY" "$ROOT"

# A packaged native client must fail closed when its immutable resource bundle
# is absent; it must never silently fall back to source-tree or Python data.
if FIRESKY_RESOURCE_DIR="$SMOKE_DIR/missing-app-data" \
   FIRESKY_REQUIRE_APP_RESOURCES=1 \
   FIRESKY_RETENTION_SETTINGS_PATH="$SMOKE_DIR/missing-retention.json" \
   "$BUILD/firesky_linux_ffi_smoke" "$LIBRARY" "$ROOT" \
   >"$SMOKE_DIR/missing-resources.stdout" \
   2>"$SMOKE_DIR/missing-resources.stderr"; then
    cat "$SMOKE_DIR/missing-resources.stderr" >&2 || true
    echo "FFI runtime unexpectedly started without packaged application resources" >&2
    exit 1
fi

echo "Linux FFI resource hard-failure smoke: ok"

