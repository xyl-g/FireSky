#!/usr/bin/env bash
# Build the firesky native scorer and publish the CPU variant.
# The engine ships CPU-only: the Metal/GPU backend was removed.
#
# macOS note: a plain `cp` of a signed dylib keeps the same code-signing
# identifier, and loading both copies in one process is rejected (SIGKILL).
# We therefore re-sign the variant so it carries a distinct identifier.
set -euo pipefail
cd "$(dirname "$0")/../native"
cargo build --release

publish_variant() {
  local src="$1" dst="$2"
  cp -f "$src" "$dst"
  if command -v codesign >/dev/null 2>&1; then
    codesign --force --sign - --identifier "$(basename "$dst")" "$dst" >/dev/null 2>&1 || true
  fi
}

if [ -f target/release/libfiresky_native.dylib ]; then
  publish_variant target/release/libfiresky_native.dylib target/release/libfiresky_native_cpu.dylib
elif [ -f target/release/libfiresky_native.so ]; then
  publish_variant target/release/libfiresky_native.so target/release/libfiresky_native_cpu.so
elif [ -f target/release/firesky_native.dll ]; then
  cp -f target/release/firesky_native.dll target/release/firesky_native_cpu.dll
fi

echo "CPU: $(ls target/release/libfiresky_native*.dylib target/release/libfiresky_native*.so 2>/dev/null | head -3 | tr '\n' ' ')"
