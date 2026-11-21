#!/usr/bin/env bash
# MM0 spike: build ecCodes 2.50.0 static lib for Android arm64 (NDK r28, API 26).
# Usage: build_eccodes_android.sh <eccodes-src-dir> <out-build-dir>
set -euo pipefail
SRC="${1:?eccodes source dir}"
OUT="${2:?build dir}"
NDK="${ANDROID_NDK_HOME:-/opt/homebrew/share/android-ndk}"

cmake -S "$SRC" -B "$OUT" \
  -DDISABLE_OS_CHECK=ON \
  -DIEEE_LE=1 -DIEEE_BE=0 \
  -DENABLE_AEC=ON \
  -DENABLE_USE_SHARED_LIB_AEC=OFF \
  -DCMAKE_PREFIX_PATH="$OUT/aec" \
  -Dlibaec_DIR="$OUT/aec/lib/cmake/libaec" \
  -DCMAKE_SYSTEM_NAME=Android -DCMAKE_SYSTEM_VERSION=26 \
  -DCMAKE_ANDROID_ARCH_ABI=arm64-v8a \
  -DCMAKE_ANDROID_NDK="$NDK" \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
  -DENABLE_FORTRAN=OFF -DENABLE_PYTHON=OFF -DENABLE_TESTS=OFF -DENABLE_EXAMPLES=OFF \
  -DENABLE_TOOLS=OFF -DENABLE_NETCDF=OFF -DENABLE_OPENMP=OFF -DENABLE_PNG=OFF -DENABLE_JPEG=OFF
cmake --build "$OUT" -j "$(sysctl -n hw.ncpu)"
echo "libeccodes.a -> $OUT/lib/"
