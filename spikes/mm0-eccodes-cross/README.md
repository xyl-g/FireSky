# MM0 尖峰结论：ecCodes + Rust 引擎移动端交叉编译

日期：2026-11-12 ｜ 分支：feat/android-ios ｜ scratch：~/.zcode/tmp/mm0-spike

## 裁决：**通过**。厚客户端路线的最大假设成立——ecCodes 可交叉编译为 iOS/Android 静态库，静态链接进 Rust 二进制后在 iOS 模拟器内运行时解码与桌面逐键一致。MM1 可开工。

## 结论矩阵

| 目标 | ecCodes 2.50.0 静态库 | Rust crate（FFE 绑定） | 运行时解码 |
|---|---|---|---|
| host aarch64-apple-darwin | brew dylib 2.50.0 | ✅ 链接 | ✅ 与 grib_get 逐键一致（t/regular_ll/496点/lat1=60/lon1=0/jScansPositively=0） |
| android aarch64 (API 26) | ✅ 6.1MB（-DENABLE_AEC=OFF） | ✅ 静态链接，ELF PIE 仅依赖 libm/libdl/libc | ⏳ 真机验证归 MM1（无设备/模拟器） |
| iOS device arm64 | ✅ 3.9MB（SDK iPhoneOS27.2） | ✅ 链接（补链 libclang_rt.ios.a） | ⏳ 真机验证归 MM1 |
| iOS simulator arm64 | ✅ 3.9MB（SDK iPhoneSimulator27.2） | ✅ 链接 | ✅ **simctl spawn 运行时解码，与 host 基线逐键一致**（iPhone 17 Pro / iOS 27.2） |

## 已踩过的坑（真实 engine 构建必复现，按序对照）

1. **`check_c_source_runs` 端序探测**：ecbuild 编译后在本机运行探测程序，交叉编译时必失败 → `IEEE_LE=0` 写废，`#ifdef IEEE` 全段编译剔除（`grib_long_to_ieee64` 等符号未定义）。
   **解法**：`-DIEEE_LE=1 -DIEEE_BE=0`（aarch64 双平台均小端）。
2. **ecbuild OS 白名单**：Android 报 "untested for this operating system" → `-DDISABLE_OS_CHECK=ON`。
3. **tools 子目录**：`ENABLE_TOOLS=OFF` 无效，守卫变量是 ecbuild 特性的 `HAVE_BUILD_TOOLS`/`ENABLE_BUILD_TOOLS`；且 iOS 下 tools 变 MACOSX_BUNDLE 导致 install() 报错 → `-DENABLE_BUILD_TOOLS=OFF -DCMAKE_MACOSX_BUNDLE=OFF`。
4. **AEC 依赖**：无 libaec 时配置直接拒绝 → `-DENABLE_AEC=OFF`。⚠️ 真实数据若用 AEC/PNG/JPEG2000 压缩的 GRIB，需额外交叉编译 libaec/libpng/openjpeg——**移动端构建的特性开关必须与桌面 brew 版做一次对齐审计**。
5. **Android：C++ 段链接**：libeccodes.a 含 C++（ExceptionHandler/std::exception）→ NDK clang 驱动默认不带 libc++，需 `-lc++_static -lc++abi`。
6. **iOS device：`___chkstk_darwin` 未定义**：新 SDK clang 默认栈探测插桩，rustc `-nodefaultlibs` 关闭了 compiler-rt 自动链接 → 显式补链 `libclang_rt.ios.a`（sim 侧无此问题）。
7. **`c_char` 差异**：`c_char` 在 aarch64-apple-darwin/ios = `i8`，aarch64-linux-android = `u8`——extern 声明一律用 `core::ffi::c_char`，禁止裸 `i8`。
8. **Homebrew rust 与 rustup 混装**：cargo（真身）从 PATH 找 `rustc`，`/opt/homebrew/bin` 优先时命中无 cross std 的 Homebrew rustc → E0463。解法：`PATH="$HOME/.cargo/bin:$PATH"` 或设置 `RUSTC`。装机规范：移动端构建机统一 rustup。
9. **rustup 首装 `--target` 清单不可信**：本次 aarch64-linux-android 目录存在但清单缺失，`rustup target add` 显式补装才生效。
10. **definitions 不进二进制**：默认路径编译期烘焙（brew 版指向 Cellar，本尖峰 iOS 构建默认 `/usr/local/...`）；移动端必须用环境变量指向随包资源。变量名是**单数 `ECCODES_DEFINITION_PATH`**（2.50.0 只读它，`grib_context.cc:447`；复数 `ECCODES_DEFINITIONS_PATH` 静默无效）。definitions 全量 131MB：BUFR 74MB / GRIB2 40MB（几乎全为 WMO 标准表，local 仅 52K）/ GRIB1 5MB / 其他 ~11MB——**GRIB-only 裁剪现实下限约 46MB**，压缩后更小。
11. **装机规范**：`xcode-select -s /Applications/Xcode.app/Contents/Developer`（本机现仍指 CommandLineTools，Tauri iOS 构建必需）；模拟器运行时 `xcodebuild -downloadPlatform iOS`（8.32GB）；rustup 需在 PATH 优先于 Homebrew rust。
12. **host 注入测试假阳性**：烘焙路径有效时，环境变量对不对都能通过——definitions 注入必须在烘焙路径失效的环境（模拟器/真机）里验证才算数。
13. **`simctl spawn` 不透传宿主环境**：需 `SIMCTL_CHILD_<VAR>` 前缀注入。
14. **部署目标错位（仅警告）**：rust aarch64-apple-ios 默认 min iOS 10.0 vs SDK 27.2——MM1 由 Tauri 项目统一 deployment target 后消除。

## 复现命令

见 scripts/build_eccodes_android.sh、build_eccodes_ios.sh、probe.c、crate/（build.rs 按目标分流链接策略）。

## 对蓝图的修订建议

- MM1 的 engine 构建直接套用本清单的 cmake 标志与链接策略（build.rs 分流：host=libloading/移动=静态）。
- 特性对齐审计（坑 4）列 MM1 前置任务。
- definitions 裁剪（坑 10）列为包体优化任务，目标 <30MB。
