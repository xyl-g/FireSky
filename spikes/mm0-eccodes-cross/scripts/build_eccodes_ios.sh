#!/usr/bin/env bash
# MM0 spike: build ecCodes 2.50.0 static lib for iOS device / simulator (arm64).
# Usage: build_eccodes_ios.sh <eccodes-src-dir> <XCODE_DEV_DIR>
set -euo pipefail
SRC="${1:?eccodes source dir}"
DEV="${2:-/Applications/Xcode.app/Contents/Developer}"

XC="$DEV/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang"
DEV_SDK="$DEV/Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS.sdk"
SIM_SDK="$DEV/Platforms/iPhoneSimulator.platform/Developer/SDKs/iPhoneSimulator.sdk"
COMMON=(-DDISABLE_OS_CHECK=ON -DIEEE_LE=1 -DIEEE_BE=0
        -DENABLE_BUILD_TOOLS=OFF -DCMAKE_MACOSX_BUNDLE=OFF -DENABLE_AEC=OFF
        -DCMAKE_SYSTEM_NAME=iOS -DCMAKE_OSX_ARCHITECTURES=arm64
        -DCMAKE_C_COMPILER="$XC" -DCMAKE_CXX_COMPILER="$XC++"
        -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF
        -DENABLE_FORTRAN=OFF -DENABLE_PYTHON=OFF -DENABLE_TESTS=OFF -DENABLE_EXAMPLES=OFF
        -DENABLE_TOOLS=OFF -DENABLE_NETCDF=OFF -DENABLE_OPENMP=OFF -DENABLE_PNG=OFF -DENABLE_JPEG=OFF)

for N in device sim; do
  if [ "$N" = device ]; then S="$DEV_SDK"; else S="$SIM_SDK"; fi
  cmake -S "$SRC" -B "build-ios-$N" "${COMMON[@]}" -DCMAKE_OSX_SYSROOT="$S"
  cmake --build "build-ios-$N" -j "$(sysctl -n hw.ncpu)"
  echo "libeccodes.a -> build-ios-$N/lib/"
done
