#!/usr/bin/env bash
# MM0 P-3: libaec 1.1.8 static cross-build (iOS device/simulator + Android arm64).
# ECMWF GRIB2 uses AEC (CCSDS) compression; ecCodes -DENABLE_AEC=ON needs it
# (without it, decoding EC values fails with ecCodes -67 "Functionality not
# enabled" — MN1 simulator full-chain, 2026-11-19).
#
# Usage: build_libaec.sh <libaec-src-dir> <spike-build-root> [XCODE_DEV_DIR] [NDK]
# Installs CMake package configs into <root>/build-{ios-device,ios-sim,android}/aec
# (ecCodes finds them via CMAKE_PREFIX_PATH) and copies libaec.a next to
# libeccodes.a in <root>/build-*/lib/ for the engine's native link-search.
set -euo pipefail
SRC="${1:?libaec source dir}"
ROOT="${2:?spike build root}"
DEV="${3:-/Applications/Xcode.app/Contents/Developer}"
NDK="${4:-${ANDROID_NDK_HOME:-/opt/homebrew/share/android-ndk}}"
XC="$DEV/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang"
DEV_SDK="$DEV/Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS.sdk"
SIM_SDK="$DEV/Platforms/iPhoneSimulator.platform/Developer/SDKs/iPhoneSimulator.sdk"
J="$(sysctl -n hw.ncpu)"

for N in ios-device ios-sim; do
  if [ "$N" = ios-device ]; then S="$DEV_SDK"; else S="$SIM_SDK"; fi
  cmake -S "$SRC" -B "$ROOT/build-libaec-$N" \
    -DCMAKE_SYSTEM_NAME=iOS -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_OSX_SYSROOT="$S" \
    -DCMAKE_C_COMPILER="$XC" -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF -DBUILD_STATIC_LIBS=ON -DBUILD_TESTING=OFF \
    -DCMAKE_INSTALL_PREFIX="$ROOT/build-$N/aec"
  cmake --build "$ROOT/build-libaec-$N" -j "$J"
  cmake --install "$ROOT/build-libaec-$N"
  cp "$ROOT/build-$N/aec/lib/libaec.a" "$ROOT/build-$N/lib/libaec.a"
done

cmake -S "$SRC" -B "$ROOT/build-libaec-android" \
  -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-26 \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=OFF -DBUILD_STATIC_LIBS=ON -DBUILD_TESTING=OFF \
  -DCMAKE_INSTALL_PREFIX="$ROOT/build-android/aec"
cmake --build "$ROOT/build-libaec-android" -j "$J"
cmake --install "$ROOT/build-libaec-android"
cp "$ROOT/build-android/aec/lib/libaec.a" "$ROOT/build-android/lib/libaec.a"
echo "libaec.a installed next to libeccodes.a in build-{ios-device,ios-sim,android}/lib/"
