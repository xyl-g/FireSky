# FireSky 原生 UI + Rust FFI 迁移决策

更新日期：2026-11-12

用户已要求本轮收尾后暂停，Windows/Linux 继续暂停；最新已做、未做及恢复顺序见
[交接记录](HANDOFF_2026-11-12.zh-CN.md)，不得把本设计清单当成整体发布完成证明。

## 1. 决策

FireSky 桌面端正式采用**平台原生 UI + Rust FFI**，不再把 Tauri 作为桌面产品主线。

实施顺序：

1. macOS：SwiftUI/AppKit + Rust FFI，先完成正式桌面端。
2. Windows：WinUI 3/C# + 同一 Rust FFI 合同。
3. Linux：优先 GTK/桌面环境原生适配 + 同一 Rust FFI 合同；具体 toolkit 在 macOS 端合同稳定后再定。
4. Docker/Web：继续使用 Rust HTTP 服务和独立 Web 页面，不把桌面 UI 打包进服务端。

现有 Tauri 工程和页面冻结保留，用于合同对照、浏览器共享页面和历史回归；在本路线下不再新增 Tauri 业务功能，也不以 Tauri 构建结果作为桌面完成证明。

## 2. 目标架构

```text
                    ┌──────────────────────────────┐
                    │ firesky-engine Rust crate     │
                    │ Runtime / providers / reports │
                    └──────────────┬───────────────┘
                                   │
                  versioned C ABI + UTF-8 wire DTO
                                   │
          ┌────────────────────────┼────────────────────────┐
          │                        │                        │
   macOS SwiftUI             Windows WinUI 3             Linux native UI
   libfiresky_engine         firesky_engine.dll           libfiresky_engine.so
          │                        │                        │
          └────────────── platform UI only ────────────────┘

   Docker/Web ───────── Rust HTTP ───────── 同一 Runtime
```

Rust 拥有数据规划、下载、校验、预检、准备、确认令牌、预测、结果发布、任务历史、缓存回收和报告生成。原生 UI 只负责输入、展示、系统能力以及 FFI 生命周期。

## 3. FFI 合同

### 3.1 ABI 与生命周期

- `firesky_ffi_abi_version()` 返回单调递增的 ABI 版本。
- `firesky_engine_open(workspace, &handle, &error)` 创建不透明运行时句柄；句柄内部持有可跨任务共享的 Rust `Runtime`。
- `firesky_engine_call(handle, method, params_json, &response, &error)` 执行一个同步调用。空
  `params_json` 等价于 `{}`，非空参数必须是 JSON 对象；调用结果和错误均为版本化 JSON envelope。
- `firesky_engine_start(handle, method, params_json, callback, user_data, &task, &error)`
  创建异步任务，覆盖 `check`、`publications`、`prepare`、`forecast` 和下载探测等长时操作。
- 桌面方法合同还包括 `task_history`、`task_events`、`task_forget` 和受约束的
  `read_cache`；后者只接受内容寻址缓存身份，重新校验源文件、模式周期和报告，
  仅用于尚未过期的短期结果恢复（300 秒、同源码版本），不绕过预检或缓存校验。
  `hello` 中的 `result_restore` 目前仅声明、未分发实现；长期不可变历史快照仍缺实现，
  不能把现有历史按钮理解为独立于原始输入保留周期的恢复。
- `firesky_task_cancel(task, &error)` 只请求协作取消；任务在 Rust worker 真正退出前仍保持运行状态。
- `firesky_task_join(task, &response, &error)` 等待任务终态并且只能成功消费一次结果。成功或失败后，
  客户端必须调用 `firesky_task_free(task)` 释放任务句柄；`firesky_task_free` 本身也会请求取消并
  等待 worker 退出，因此可用于异常清理，但可能阻塞。
- 所有由 Rust 写入 `char **` 的字符串（响应和错误）都必须由同一个动态库导出的
  `firesky_ffi_string_free` 释放。Rust 指针、`Vec`、`Result`、`serde_json::Value` 和回调参数
  的临时存储不跨 ABI 暴露。
- `firesky_engine_free(handle)` 只释放运行时句柄。客户端必须先取消并 join/free 所有任务，再释放
  runtime；不得在仍有 native 回调或 worker 使用句柄时调用它。

### 3.2 Wire DTO

嵌套业务结果使用带合同字段的 UTF-8、NUL 结尾 JSON 字符串。同步调用和异步 join 的成功结果格式为：

```json
{
  "contract": "firesky-ffi-call-v1",
  "abi_version": 1,
  "ok": true,
  "method": "hello",
  "result": {}
}
```

失败时通过返回码和 `out_error` 返回错误 envelope：

```json
{
  "contract": "firesky-ffi-error-v1",
  "abi_version": 1,
  "ok": false,
  "method": "prepare",
  "error": {
    "code": 4,
    "message": "..."
  }
}
```

进度回调收到的字符串只在回调函数执行期间有效，客户端必须立即复制；事件使用：

```json
{
  "contract": "firesky-ffi-event-v1",
  "abi_version": 1,
  "method": "prepare",
  "event": {
    "stage": "prepare",
    "percent": 42,
    "source": "gfs",
    "message": "正在准备 GFS 数据",
    "cache_hit": false
  }
}
```

JSON 只是 ABI 的线格式，不代表 Swift、C# 或 GTK 代码可以依赖 Rust 内部字段。每个业务结果仍需有独立的字段/版本合同，新增字段必须向后兼容；错误使用稳定的 `code` 和用户可见 `message`。当前返回码定义见 `engine/include/firesky_ffi.h`。

### 3.3 线程规则

- Rust 回调不得直接操作 UI。
- Swift/C#/GTK 回调只复制事件 bytes，然后切回各自 UI 主线程。
- 关闭顺序固定为：阻止新任务 → 取消任务 → 等待 `firesky_task_join` 或
  `firesky_task_free` 返回 → 释放任务句柄 → 释放 runtime → 卸载动态库。
- `firesky_task_free` 可能等待网络或文件操作协作退出；在它返回前不得 `dlclose`/卸载 Rust
  动态库。若上层需要有界等待，应把未完成任务和动态库句柄保留到 worker 终态，禁止释放后继续回调悬空地址。

## 4. macOS 首阶段

### 已有可复用部分

- `apps/macos/Sources/FireSkyNative/` 已有完整 SwiftUI 工作台、地图、预检弹窗、结果/剖面/区域视图、设置和下载探测。
- `engine::desktop_runtime::Runtime` 已拥有统一 `check → prepare → forecast`、持久任务、取消和结果发布逻辑。
- 现有 JSONL Rust desktop IPC 仅保留为显式开发诊断模式（`FIRESKY_NATIVE_BACKEND=jsonl`）。
  正式 `.app` 缺少或无法加载 FFI 动态库时必须直接失败，不得静默切换 JSONL，也不得启动 Python。

### 首阶段代码任务

1. `engine/src/ffi.rs`：导出稳定 C ABI、运行时句柄、异步任务、取消、join、回调和字符串释放。
2. `engine/Cargo.toml`：同时生成 `rlib`、`cdylib` 和 `staticlib`。
3. `scripts/build_macos_native.sh`：构建并复制 `libfiresky_engine.dylib` 到 `.app/Contents/Frameworks`，设置 `@rpath`，对动态库和应用分别签名。
4. `apps/macos/.../EngineClient.swift`：默认加载 FFI 动态库；所有生产调用通过 FFI；保留 Rust JSONL 仅作开发诊断模式。
5. 增加 FFI 生命周期、错误、取消、回调线程和 macOS bundle smoke 测试。

### macOS 完成门槛

- 无 Python 进程、无 Tauri 依赖，冷启动和重连均通过 FFI。
- 点、区域、实况均通过真实 `check → prepare → confirm → forecast`。
- CAMS 点/区域/实况/报告至少各有冷缓存和热缓存验收；上游不可用时明确记为待验收。
- FFI 动态库、ecCodes 资源、城市/时区资源、报告和缓存路径均在 clean-machine bundle 中可定位。
- macOS ARM64 和 x86_64 使用同一源码版本分别构建并运行；未通过的架构不标为完成。

## 5. Windows 与 Linux

macOS FFI 合同稳定后，Windows/Linux 只重写平台适配层，不复制 Rust 业务逻辑：

- Windows：WinUI 3 导航、窗口、系统定位、文件选择和 WebView2 地图控件；加载 `firesky_engine.dll`。
- Linux：平台原生窗口、文件选择、定位和地图控件；加载 `libfiresky_engine.so`。
- 两端必须复用相同的 ABI 版本、wire DTO、任务状态和预检语义。
- 只有平台能力（定位、钥匙串、系统通知、窗口菜单、文件对话框）允许有差异。

Windows 的 FFI 适配层已按 macOS 基线接线：`EngineClient.cs` 直接使用
`NativeLibrary.Load` 解析 ABI v1，`check`、`publications`、`prepare`、`forecast`
`network_probe` 以及 `hello`、位置、清单和凭据设置等短调用均走异步任务
`firesky_engine_start`/`firesky_task_join`，避免 WinUI 调度线程被 Rust 文件或地理查询
阻塞；`firesky_engine_call` 仍保留用于 ABI 兼容和低层 smoke。任务关闭顺序与 macOS 相同，必须先取消并等待
`firesky_task_join`/`firesky_task_free`，再释放 runtime 和 DLL。JSONL 只在显式设置
`FIRESKY_NATIVE_BACKEND=jsonl` 时用于开发诊断，安装包不会因缺少 DLL 静默启动其他后端。

Windows 工程按运行时架构选择 Rust target，并同时复制引擎 EXE、FFI DLL 和只读
`app-data` 资源：

```powershell
dotnet build apps/windows/FireSky/FireSky.csproj -c Release -p:Platform=x64 -r win-x64
dotnet build apps/windows/FireSky/FireSky.csproj -c Release -p:Platform=ARM64 -r win-arm64
```

当前 macOS 主机无法执行上述 WinUI/.NET 构建；可先运行
`scripts/test_windows_ffi_contract.sh` 做静态合同检查。Windows 实机的 x64/ARM64
启动、WebView2、ecCodes DLL 闭包、签名和真实数据链路仍是未通过的发布门禁。

## 6. 不做的事情

- 不继续扩展 Tauri 桌面业务页面。
- 不将现有 Svelte 页面复制成三套“伪原生” WebView 产品。
- 不把 Python sidecar 或 Python 兼容服务作为生产回退。
- 不把 UI 重写误认为 Rust 算法迁移完成；真实数据和目标架构仍须逐项验收。
- 不在 macOS FFI 合同未稳定前同时启动 Windows、Linux、Android、iOS 的完整 UI 重写。

## 7. 当前状态记录

截至 2026-11-12，macOS SwiftUI 客户端已经接入 `libfiresky_engine.dylib`：
默认从 `.app/Contents/Frameworks` 加载，任务取消、join/free、回调线程切换和
动态库卸载顺序均已实现。点/区域/实况统一预检已接线，区域城市排名和冻结网格剖面
查询已接入，任务历史/事件/短期缓存恢复/手动重试以及缓存周期/天数保留设置也已在
原生数据页暴露。`scripts/build_macos_native.sh`
支持 `FIRESKY_MACOS_ARCH=arm64|x86_64`，构建前校验资源 manifest；
`scripts/test_macos_native.sh` 已覆盖 C ABI、Swift ABI、point/regional/nowcast 预检、
资源缺失硬失败、非 ASCII 工作区、签名、架构、无 Python 动态依赖和 `.app` 冷启动；
本机 ARM64 已实际通过，CI 仍负责 Intel runner 的同版本构建运行。

macOS 的 FFI 实现和本机 ARM64 门禁已完成，但仍不能标记为最终发布完成：真实 CAMS
点/区域/实况/报告冷热缓存、干净机器安装、Developer ID 签名/公证/staple 以及目标机
权限和上游网络仍需验收。这些是发布门禁，不代表 FFI 接线缺失。详细基线见
`docs/MACOS_NATIVE_BASELINE.zh-CN.md`。Windows 已有
WinUI/C# FFI 客户端、nowcast 字段和 x64/ARM64 资源打包接线，当前只能通过静态合同；
Linux 已有 C++20 `dlopen`/RAII FFI、point/regional/nowcast GTK4 预检流程和 headless
smoke，但 GTK 目标机编译、地图/报告呈现、安装包和真实数据仍未验收。Tauri/共享 Web
联合迁移文档中的桌面主线描述以本决策为准，历史测试结果不能替代真实数据和多架构验收。
