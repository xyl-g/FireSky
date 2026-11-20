# 原生 UI + Rust FFI 跨平台基线

更新日期：2026-11-20

本文记录 macOS 基线完成后，Windows/Linux 仿照 macOS 接入的实际状态。
它区分“源码和合同已完成”和“目标机器已验收”，不把静态检查当作发布验收。

## 1. 固定架构

- 预测、下载、校验、预检、准备、确认令牌、报告发布、任务历史和缓存清理由
  Rust 引擎拥有。
- macOS 使用 SwiftUI/AppKit + `libfiresky_engine.dylib`。
- Windows 使用 WinUI 3/C# + `firesky_engine.dll`，目标为 x64 和 ARM64。
- Linux 使用 GTK4/C++20 + `libfiresky_engine.so`。
- 三端共用 `engine/include/firesky_ffi.h` 的 ABI v1、JSON envelope、错误码、
  任务状态、确认令牌、资源清单和 retention 合同。
- 正式安装包不启动 Python，不依赖 Tauri，也不因 FFI 缺失静默切换 JSONL。
  JSONL 仅允许显式 `FIRESKY_NATIVE_BACKEND=jsonl` 的开发诊断路径；正式包直接失败。

## 2. macOS 基线

已完成并在本机 ARM64 通过：

- FFI 同步/异步调用、进度回调、取消、`join/free`、字符串释放。
- 关闭顺序：取消任务 -> 等待 `join/free` -> 释放 runtime -> 卸载动态库。
- 点、区域、实况统一执行 `check -> publications -> prepare -> forecast(confirmed=true)`。
- 预检弹窗展示缺失、过期和待核验输入；未准备成功不能确认预测。
- 区域 GFS/EC 独立城市排名、网格排名和冻结 `profile_query`。
- GFS/EC/CAMS 周期保留数，以及卫星/报告/地图按天滚动保留。
- macOS 原生数据页已接入持久任务历史、事件回放、结果恢复和手动重试预检。
- `.app` 的 `RESOURCE-MANIFEST.json`、ecCodes、definitions/samples、城市目录和
  时区资源硬校验。

已运行：

```text
Rust: 174 passed, 2 ignored
Runtime process tests: 4 passed
Swift release build: passed
macOS FFI/Swift fixture/smoke: passed
Application cold-start and native bundle smoke: passed
```

仍需发布环境验收：Intel x86_64 真机、真实 CAMS 账号和上游网络、无开发工具
clean-machine、权限/代理/升级/卸载保留数据。（2026-11-20 更新：Developer
ID 签名/公证为用户裁定不做，v1.0.0 以 ad-hoc 签名发布，见
`docs/RELEASE_MACOS.zh-CN.md`；Intel 载荷已由 CI macos-26-intel 构建+冒烟，
真机验收仍开放。）

## 3. Windows 对齐实现

已完成源码和静态合同：

- `EngineClient.cs` 按进程架构选择 x64/ARM64 Rust target，并加载版本化 DLL。
- `Resources/app-data/RESOURCE-MANIFEST.json` 缺失时硬失败；正式包缺 DLL 或
  尝试 JSONL 也硬失败。
- 保留外部显式资源环境变量，不因开发目录没有 bundle 资源而清除它们。
- 所有业务调用走异步 FFI task，取消后等待 join/free，再释放 runtime/DLL。
- 预检和预测流程与 macOS 一致，nowcast 使用 satellite 参数和确认令牌。
- 设置页接入 GFS/EC/CAMS 周期保留、卫星/报告/地图天数保留。
- 区域页接入网格排行、城市排行、地图/列表选点和冻结结果 profile 查询；
  profile 响应校验结果 ID、输入 SHA/字节数、模式、坐标、UTC 时次和周期。
- `hello` 校验 `profile_query`、`task_history`、`task_events`、`result_restore` 能力。

验证：

```text
bash scripts/test_windows_ffi_contract.sh: passed
git diff --check: passed
```

本地主机没有 `dotnet` 和 Windows 运行环境；WinUI x64/ARM64 编译与产物验证
已由 GitHub CI（windows-2022 / windows-11-arm）完成（2026-11-20，见 §7）。
真机启动、资源缺失负例和真实 CAMS 数据验收仍开放。

Windows 数据页已按 macOS 数据页合同接入任务历史 UI（2026-11-14）：
`task_history(limit=100)` 响应与单条记录校验（schema、automatic_restart、
total、64 位十六进制 task_id、状态枚举、ISO8601 时间、event_seq、重复记录）、
`task_events` 事件回放（seq 严格递增、next_seq、truncated）、删除确认
（running 禁止，`task_forget` 需 `forgotten==true`）、`result_restore`
（非 nowcast 必须含 model_results，retry.kind 页切换、gfs/ec 模型回退）与
恢复参数重查（应用 payload 后走统一预检入口）。恢复与删除互斥、随 busy
禁用；文案与 macOS 一致。`scripts/test_windows_ffi_contract.sh` 已加入
`task_forget`/`result_restore` 在窗口层的存在性断言。

## 4. Linux 对齐实现

已完成源码和可用主机上的静态/CMake 合同：

- GTK4/C++20 壳通过 RAII 加载 `libfiresky_engine.so`，任务持有 runtime/library
  状态，避免 in-flight task 下提前 `dlclose`。
- `check -> prepare -> forecast(confirmed=true)` 和 nowcast 参数与 macOS 一致。
- 设置页接入六项 retention；区域页提供独立城市排名视图。
- 冻结 profile 查询绑定结果 ID、输入身份、模式、坐标和 UTC 时次。
- FFI smoke 覆盖 hello capability、同步/异步调用、错误 envelope、任务历史、
  task events、task_forget 软失败、result_restore 缺记录硬失败、retention 保存/
  恢复、profile 缺失响应和三类预检。
- 统一 modal 预检已按 macOS 行为接入：点/区域/实况共用一个「预测」入口，
  打开 modal 自动执行 check → 报告（缺失/可更新/待核验分列）→ 下载缺失数据/
  更新数据（准备进度）→ 确定预测（数据周期未核验或过期需确认；confirmed=true，
  可选 cycle_age_confirmed）；未准备成功不得确认，取消 sheet 即丢弃令牌，
  发布核验异步进行且不阻塞本地可用性。
- 任务历史已接入：task_history(limit=100) 响应与单条记录校验（schema、
  automatic_restart、total、task_id 64 位十六进制、状态枚举、ISO8601 时间、
  event_seq、重复记录）、事件回放（seq 递增、next_seq、truncated）、
  删除确认（running 禁止）、恢复参数重查和已发布结果恢复（model_results 校验、
  retry.kind 页切换、gfs/ec 模型选择回退）。
- `FIRESKY_REQUIRE_APP_RESOURCES=1` 下，缺少 immutable resource bundle 的运行时
  直接失败；不会退回源码资源、Python 或 JSONL。

验证：

```text
bash scripts/test_linux_ffi.sh
  Linux 主机：构建并运行 FFI smoke，同时执行缺资源负例
  非 Linux 主机：明确跳过运行时 smoke
```

本地主机是 macOS；Linux GTK4 构建与 FFI hello smoke 已由 GitHub CI
ubuntu-24.04 完成，`linux-ffi` 在 ubuntu-24.04 / ubuntu-24.04-arm 上运行
`scripts/test_linux_ffi.sh`（含缺资源负例），覆盖 amd64/arm64（2026-11-20，
见 §7）。发行包/安装器、桌面权限/通知/文件选择器和真实 CAMS 数据仍待
Linux 真机验收。

## 5. 不可移植边界

没有已知的自有天气计算算法因语言原因不能移植到 Rust。必须保留的外部边界是：

- 上游 GFS/EC/CAMS/HSD 服务、账号授权、代理和网络失败行为；
- C ecCodes 动态库及其 definitions/samples 资源；
- 各平台窗口、文件选择、定位、通知、钥匙串和动态库加载；
- macOS SwiftUI、Windows WinUI、Linux GTK 的系统生命周期。

这些边界不改变 Rust 业务合同。任何平台缺少资源、ABI 不匹配或目标环境不满足
时都应失败关闭，而不是改走 Python。

## 6. 最终交付门禁

三端都要在同一源码版本分别完成：

1. 目标架构编译和启动；
2. 资源清单篡改/缺失、动态库缺失、非法 JSON、取消和重复 join 负例；
3. 点、区域、实况的真实预检、准备、确认和报告流程；
4. retention 清理不删除正在使用的输入和仍被报告引用的地图；
5. clean-machine 安装、升级、卸载和用户数据保留；
6. 真实 CAMS 账号、代理和失败恢复验收。

在这些门禁完成前，交付状态应写为“macOS 实现完成；Windows/Linux 源码与 FFI
合同已对齐，目标机发布验收待完成”，不能宣称三端发布完成。

## 7. Native Desktop CI 首次全绿（2026-11-20）

run 44571959210 @ f341ef8（主 CI run 44571959208 同 commit 亦绿），10 jobs
全部 success：

| Job | Runner | 验证范围 |
| --- | --- | --- |
| Native resource contracts | ubuntu-24.04 | 资源清单审计、Docker 运行时契约、Windows FFI 适配契约 |
| SwiftUI + Rust FFI arm64 | macos-26 | 构建、DMG hdiutil VALID、FFI bundle 冒烟 |
| SwiftUI + Rust FFI x86_64 | macos-26-intel | 同上（Intel 原生构建） |
| Docker linux/amd64 / arm64 | ubuntu-24.04 | 镜像构建 + 运行时冒烟 |
| Linux GTK4 + Rust FFI | ubuntu-24.04 | GTK4 构建 + FFI hello smoke |
| WinUI + Rust FFI x64 | windows-2022 | vcpkg ecCodes bundle、Rust 引擎、dotnet 编译、产物验证（exe/引擎 DLL/资源清单/无 Python） |
| WinUI + Rust FFI arm64 | windows-11-arm | 同上（ARM64 原生） |
| Linux FFI amd64 / arm64 | ubuntu-24.04 / ubuntu-24.04-arm | `test_linux_ffi.sh` C++ FFI 合同 + 缺资源负例 |

依赖链：contracts → macos-native（双架构，fail-fast:false）→
linux-native + windows-native → linux-ffi；Docker 独立并行。

打通修复链（四提交）：

1. `18259b8`：区域单模型契约三端（Linux GTK/WinUI/Web）对齐 macOS；
   vcpkg bundle 按 port manifest 计算非 host 依赖闭包，排除 python3 等
   host 构建依赖 DLL。
2. `fd414c8`：NuGet NU1605 修复（BuildTools 10.0.26100.4654）；地图资产
   恢复为 `apps/shared/` vendored 副本（leaflet 1.9.4 + firesky.png）。
3. `e95c52d`：WinUI 首次编译 6 处 C# 错误（Utf8 缺 new、IntPtr Task 字段
   遮蔽、Panel.IsEnabled 递归禁用、历史时间戳 required 属性）。
4. `f341ef8`：verify 步骤输出路径修复——`dotnet -p:Platform` 限定产物在
   `bin/<Platform>/Release/...` 而非 `bin/Release`，改为递归搜 bin/ 并按
   引擎 DLL 存在性筛出完整产物。

CI 验证 ≠ 真机发布验收。仍开放：Windows/Linux 真机交互式 UI、发行包/
安装器、桌面权限/通知/文件选择器、真实 CAMS 账号与代理、clean-machine
安装/升级/卸载（§6 门禁）；macOS Intel 真机（CI Intel 构建+冒烟已过）。
Developer ID 签名/公证为用户裁定不做（ad-hoc 发布）。

交付状态（2026-11-20，v1.1.0）：macOS 正式发布 v1.1.0（ad-hoc，双架构 DMG，
沿用 v1.0.0 门禁；v1.0.0 为首个正式版，见 `docs/RELEASE_MACOS.zh-CN.md`）；
Android 自签签名 APK、iOS 无签名 device IPA、Docker 镜像（GHCR
`ghcr.io/xyl-g/firesky:1.1.0`，tarball 兜底）随 v1.1.0 首次发布，移动端验收
（模拟器全链 + 桌面帧级 parity + 会话恢复）见 `docs/MOBILE_NATIVE_PLAN.md`。

平台策略注记（2026-11-20 用户裁定）：

- **Windows**：v1.1.0 不含 Windows 发布资产；源码与 FFI 合同、CI 构建+产物
  验证保持现状，正式安装包后续在 Windows 机器上构建发布。§3 所列真机启动、
  资源缺失负例与真实 CAMS 验收转为该后续工作的前置项。
- **Linux**：自 2026-11-20 起**不再作为发布目标**；GTK4/C++ 客户端与 FFI
  代码、CI 验证 job 全部保留（本文 §4 降级为源码构建参考），发行包/安装器
  与 Linux 真机发布验收不再推进；服务器部署推荐 Docker。
- §6 门禁对 Windows/Linux 不再阻塞发布；macOS 已发布项沿用
  `docs/RELEASE_MACOS.zh-CN.md` 的实际门禁记录，Intel 真机验收仍开放。
