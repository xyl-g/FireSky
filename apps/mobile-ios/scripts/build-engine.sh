#!/usr/bin/env bash
# 构建 engine-mobile 的 iOS 静态库两个切片（真机 + 模拟器），供 FireSkyMobile 链接。
# project.yml 的 LIBRARY_SEARCH_PATHS 直接指向 engine-mobile/target 的产物目录；
# xcodebuild 之前必须先跑本脚本（否则链接阶段找不到 -lengine_mobile）。
#
# 坑（2026-11-12 集成核验实测，两个都要防）：
# 1. rustc 1.99.1（llvm-ar，LLVM 23.1.1）写出的归档符号索引（__.SYMDEF）Xcode 27.2
#    的 ld 读不到——静态库「静默零符号」（dylib 默认 -undefined dynamic_lookup 不报
#    错），首次调用即 dyld 崩溃。Apple ranlib 重建索引后符号可正常解析。
# 2. cargo 同目录还产出 libengine_mobile.dylib（crate-type 含 cdylib），ld 的
#    -lengine_mobile 搜索动态库优先 → 实际动态链接（dylib 未打包进 App）。
#    project.yml 因此按 SDK 显式链接 .a 文件路径，不走 -l 搜索。
set -euo pipefail
# MM0 坑 11：xcode-select 可能仍指 CommandLineTools（无 iOS SDK），脚本内的
# xcrun ranlib 会失败。cargo 侧由 ~/.cargo/config.toml 注入 DEVELOPER_DIR，
# 这里为脚本自身的 xcrun 调用做同样兜底。
if ! xcrun -sdk iphoneos --show-sdk-path >/dev/null 2>&1; then
    export DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer"
fi
cd "$(dirname "$0")/../../../engine-mobile"
echo "==> cargo build --release --target aarch64-apple-ios（真机切片）"
cargo build --release --target aarch64-apple-ios
xcrun -sdk iphoneos ranlib target/aarch64-apple-ios/release/libengine_mobile.a
echo "==> cargo build --release --target aarch64-apple-ios-sim（模拟器切片）"
cargo build --release --target aarch64-apple-ios-sim
xcrun -sdk iphonesimulator ranlib target/aarch64-apple-ios-sim/release/libengine_mobile.a
echo "==> 产物："
ls -l target/aarch64-apple-ios/release/libengine_mobile.a \
      target/aarch64-apple-ios-sim/release/libengine_mobile.a
