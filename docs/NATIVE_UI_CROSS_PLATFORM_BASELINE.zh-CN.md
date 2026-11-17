# 原生 UI + Rust FFI 跨平台基线

更新日期：2026-11-12

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
clean-machine、Developer ID 签名/公证/staple、权限/代理/升级/卸载保留数据。

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

当前主机没有 `dotnet` 和 Windows 运行环境，尚未完成 WinUI 编译、x64/ARM64
真机启动、资源缺失负例和真实 CAMS 数据验收。

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

当前主机是 macOS，因此 Linux GTK4 目标机运行、发行包、x86_64/ARM64 目标构建、
桌面权限/通知/文件选择器和真实 CAMS 数据仍待 Linux 主机验收。

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
