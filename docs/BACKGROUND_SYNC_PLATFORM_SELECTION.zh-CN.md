# 应用无关后台同步：跨平台技术选型（macOS / Windows / Android / iOS）

日期：2026-11-13。状态：**已拍板，macOS P0 已落地并验收**（决策记录见 7.3，落地与验收见 7.4）。
本文原为选型提案；用户已就关键决策点拍板，macOS 基准平台已按定案实现。
它承接 [后台数据同步助手设计](DATA_SYNC_HELPER_DESIGN.zh-CN.md)（已定案、已实现于 `75371d1`），
把该设计第 1.2.5 / 2 / 15.6 节**显式推迟**的「守护进程化 / 应用无关同步」展开为四个平台的
详细选型。平台范围与暂停声明以 [RUST_REMAINING_WORK](RUST_REMAINING_WORK.zh-CN.md) 头部为准：
macOS 为产品与 UI 基准；Windows/Linux 原生开发暂停；Docker 属后续 Web 部署；移动端为全新地基。

> 用户诉求（2026-11-13）：
> 1. 「后台同步必须要开着」→ 希望**应用不开也能同步**（helper 这类方式）。
> 2. 「同步进度不知道 / 能否看到同步进展」→ 进度可见。
> 3. 「最起码查一下远端有没有发布，我要最新的数据」→ 只取**最新已发布周期**（引擎已实现，见下）。
> 4. 「需要考虑 macOS Windows 安卓 ios 的方案，给我详细的选型」→ 本文。

---

## 0. 结论速览（先看这里）

| 平台 | 推荐机制 | 应用不开能否同步 | 周期保证 | 长下载（CAMS/ADS）适配 | 落地难度 |
| --- | --- | --- | --- | --- | --- |
| **macOS** | launchd **LaunchAgent** + 一次性引擎 helper（菜单栏常驻为低风险过渡） | ✅ 登录后即可，无需开应用 | 准周期（`StartInterval`/`StartCalendarInterval`，睡眠会顺延） | ✅ 无后台时长上限，ADS 排队数小时可跑完 | 中（需 helper + 一次性 CLI + 跨进程锁 + 安装/签名 UX） |
| **Windows** | **任务计划程序**（登录触发 + 重复间隔）跑同一 helper | ✅ 登录后即可 | 准周期（睡眠/电池策略会顺延） | ✅ 无后台时长上限 | 中（平台暂停，引擎侧先就绪） |
| **Android** | **WorkManager 周期** + 用户发起时 `dataSync` **前台服务** | ⚠️ 部分（Doze/厂商杀后台/6h 上限） | ❌ 不保证（最小 15min，系统择机） | ⚠️ GFS/EC 可行；CAMS 数小时 ADS 排队**超 6h/24h 上限**，须前台或转服务端 | 高（全新地基：引擎 .so/JNI + UI） |
| **iOS** | **BGProcessingTask** 择机 + **background URLSession** 下载；CAMS 走前台或服务端 | ⚠️ 仅择机（系统决定，常夜间/充电） | ❌ **不保证周期，无常驻守护** | ❌ CAMS 数小时 ADS **无法在后台跑完**；GFS/EC 直链可后台续传 | 高（全新地基：引擎 xcframework + UI） |

**贯穿性建议**：移动端（Android/iOS）受系统后台时长与调度限制，**CAMS 整周期/ADS 排队（可达 2 小时，
`engine/src/ads.rs:46` `wait_timeout_seconds=7200`）几乎不可能在后台稳定跑完**。因为本项目是
**轻量自托管（本人 + 约 2–3 人）**且已有 Docker/HTTP Rust 引擎方向，移动端最稳的架构是
**「服务端集中同步 + 移动端薄客户端」**：由常开机器上的 HTTP 引擎做重同步，手机只查询/触发，
从而绕开移动后台限制。详见第 6 节战略选项。

---

## 1. 共享底座：哪些已经跨平台，哪些还没有

选型必须建立在「同一套 Rust 业务核心 + 同一 FFI ABI」之上，避免每平台重写同步逻辑。

### 1.1 已就绪（纯 Rust、跨平台、可直接复用）

| 能力 | 位置（已核对） | 对选型的意义 |
| --- | --- | --- |
| 同步设置/状态/到期/单轮执行 | `engine/src/desktop_sync.rs`：`load:204`、`status:217`、`save:232`、`due:622`、`run:928` | 四平台共用同一判定与执行，调度器只是「何时调用 `due`/`run`」的外壳 |
| 线上方法 | `desktop_runtime.rs:525-552`：`data_sync_settings`/`save_data_sync_settings`/`data_sync_due`/`data_sync_run` | helper 与 UI 走同一组方法，语义一致 |
| **发布探测先于任何状态写**（fail-closed） | `desktop_sync.rs:970-1000`：`probe_publications` 成功后才写状态；失败只记 failed 行、不耗间隔/熔断预算 | **直接满足用户「查远端是否发布、只要最新」**；调度器无需自己判周期 |
| **进度回调** | `desktop_sync.rs:933` `progress: &dyn Fn(Value)->Result<(),String>`；`run` 逐源/逐阶段回调 | 进度可见的引擎侧已具备；各平台只需把回调映射到 UI/通知 |
| C ABI（v1） | `engine/src/ffi.rs:24` `FIRESKY_FFI_ABI_VERSION=1`；`firesky_engine_open:269`/`free:294`/`call:302`/`start:330`、`firesky_task_cancel:412`/`join:431`/`free:461`、事件回调 `:48` | macOS(Swift)/Windows(C#)/Android(JNI)/iOS(Swift) 都能以同一 ABI 接入 |
| **CAMS 组文件锁（跨进程）** | `cams_data.rs:505-516`（按 run+area 取文件锁）、锁内重查缓存 `:523-547` | 这是**唯一已跨进程**的互斥；helper 进程与应用进程靠它去重 CAMS |
| 凭据边界 | `desktop_credentials.rs:1`（token 不进日志）、CAMS key 仅 `~/.cdsapirc`（0600） | 各平台凭据存储须遵守同一边界（见 5.4） |
| 引擎可执行入口 | `engine/src/main.rs`：`http:67`、`serve:86`(JSONL rpc)、`desktop:91`(JSONL desktop)、`call:97`(一次性 rpc)、`forecast:111`(一次性 compute) | helper 可复用引擎二进制；但**缺一次性 desktop 方法入口**（见 1.2） |

### 1.2 尚缺（选型必须补齐的地基）

1. **无「一次性 desktop 方法」CLI**：`main.rs:91` 的 `desktop` 启动的是**常驻 JSONL 服务**
   （`desktop_runtime::serve`），`call:97` 走的是 `rpc::dispatch`（非 desktop 运行时）。
   定时 helper（launchd/任务计划）需要「开进程→跑一次 `data_sync_due`/`data_sync_run`→退出」，
   建议新增 `desktop-call --workspace <ws> <method> --params <json>`（复用 `Runtime::new`+`dispatch`，
   打印结果即退出）。这是小改动，但是 macOS/Windows helper 的前置。
2. **`operation`/`sync` 互斥是进程内**：`desktop_runtime.rs:40` `operation: Mutex<()>`、同步轮 `sync` 锁
   都只在**单进程内**生效。**helper 进程与应用进程并存时，这两把锁不协调**——只有 CAMS 文件锁跨进程。
   任何「应用无关」方案都必须解决跨进程协调（见 5.2），否则应用与 helper 可能同时跑重计算。
3. **无移动端引擎构建**：`apps/` 仅 `linux/macos/shared/shared-web/windows`，**无 android/ios**。
   移动端要先把引擎编成 Android `.so`（cargo-ndk + JNI）/ iOS `xcframework`（C ABI + Swift 桥），
   属全新地基，不在当前 macOS 交付范围内。
4. **macOS 应用生命周期 = 同步生命周期（现状）**：`FireSkyApp.swift:43`
   `applicationShouldTerminateAfterLastWindowClosed=true`，引擎 FFI 运行时由应用进程持有。
   现状「后台」= 应用运行期间；要「应用不开也同步」必须改生命周期或引入独立 helper 进程。

---

## 2. 核心矛盾：为什么移动端不能照搬桌面方案

| 矛盾 | 桌面（macOS/Windows） | 移动（Android/iOS） |
| --- | --- | --- |
| **后台时长** | 用户登录后 helper 可长跑，无系统时长上限 | Android `dataSync` 前台服务 **6h/24h 共享上限**；iOS `BGProcessingTask` 仅**数分钟**且系统择机 |
| **周期保证** | launchd/任务计划准周期（睡眠顺延） | WorkManager 最小 15min 且 Doze 择机；iOS **不保证周期、无常驻守护** |
| **CAMS/ADS 排队** | 数小时可跑完（`ads.rs:46` 7200s） | **几乎必然超移动后台预算** → 须前台用户发起，或转服务端 |
| **GFS/EC 直链下载** | 可行 | 可行（iOS background URLSession 可在挂起后续传网络） |
| **进程模型** | 可独立 helper 进程 | 移动 App 被系统严格管控，不能常驻独立守护 |

**推论**：
- 桌面：独立 helper（launchd / 任务计划）是「应用无关 + 最新周期 + 长下载」的正解。
- 移动：把「重同步（CAMS/ADS）」与「轻同步（GFS/EC 直链）」分开；重同步优先**服务端集中**或**前台用户发起**，
  轻同步可走系统择机后台。**不要承诺移动端能像桌面一样无人值守周期拉齐 CAMS**。

---

## 3. macOS 选型（基准平台，先做）

### 3.1 候选机制对比

| 方案 | 应用不开能否同步 | 周期/触发 | 长下载 | 跨进程协调 | 权限/签名 | 复杂度 | 评价 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **A. 菜单栏常驻应用**（`NSStatusItem`+`LSUIElement`，改 `applicationShouldTerminateAfterLastWindowClosed`） | ⚠️ 无需窗口，但**应用进程仍在** | 复用现有 60s tick（`Store.swift`） | ✅ | **无需**（同进程，复用 `operation`/`sync` 锁） | 无新增 | 低 | 最低风险过渡；不是真正「应用退出仍同步」 |
| **B. launchd LaunchAgent + 一次性 helper**（`~/Library/LaunchAgents`，`StartInterval`/`StartCalendarInterval`） | ✅ **登录后即可，应用可退出** | 准周期；睡眠顺延，唤醒补跑 | ✅ 无上限 | **需补**（helper 与应用并存，见 5.2） | 用户级，无需 root；helper 需签名 | 中 | **推荐目标**：真正应用无关 |
| **C. SMAppService 登录项/Agent**（macOS 13+，`SMAppService.daemon`/`.loginItem`） | ✅ 同 B，但由系统注册管理 | 同 B | ✅ | 需补 | 需正确签名/公证；用户可在系统设置管理 | 中 | B 的「现代注册」变体，安装/卸载 UX 更规范 |
| **D. LaunchDaemon（系统级，root，登录前运行）** | ✅ 甚至登录前 | 准周期 | ✅ | 需补 | **需 root/管理员**，daemon 签名严格 | 高 | 自托管 2–3 人**过度**，不推荐 |

### 3.2 推荐

- **目标方案：B（launchd LaunchAgent + 一次性引擎 helper）**，注册方式优先 **C（SMAppService）** 以获得
  规范的安装/卸载与系统设置可见性。理由：直接满足「应用不开也同步」，无后台时长上限可跑完 CAMS/ADS，
  用户级无需 root，符合轻量自托管。
- **低风险过渡：A（菜单栏常驻）**。若希望尽快见效、先不动跨进程协调，可先让应用关窗后退到菜单栏继续 tick；
  但这只是「无窗口」，应用进程仍常驻，**不等于应用退出仍同步**。可作为 B 落地前的 interim。

### 3.3 helper 工作流（B/C）

```
launchd（登录触发 + StartInterval，如每 30min）
  └─ firesky-helper（签名的小二进制，或直接引擎二进制）
       ├─ firesky-engine desktop-call --workspace <ws> data_sync_due
       │     └─ 引擎：本地状态 + 发布探测 → 每源 到期/未到期/原因（fail-closed）
       ├─ 若到期：desktop-call data_sync_run（逐源；进度回调写状态文件 + 发通知）
       │     └─ CAMS 走洲包络 + all_leads；GFS/EC 拉最新已验证周期固定时效
       └─ 退出（无常驻）；应用下次启动读状态文件展示「上次/下次同步」
```

- **进度可见**：helper 把 `run` 的进度回调写入共享状态文件（`rust-data-sync-state-v1`），
  并用 `UNUserNotificationCenter` 发「开始/完成/失败」通知；应用内数据页读状态文件展示逐源
  percent/文件数/字节数 + 上次/下次同步（引擎进度事件已具备，见 1.1）。
- **凭据**：CAMS key 仍只读 `~/.cdsapirc`（0600），helper 不复制 token、不进日志。

### 3.4 macOS 必须补齐

1. 一次性 `desktop-call` CLI（1.2 第 1 项）。
2. **跨进程同步轮文件锁**（5.2）：helper 与应用不得同时跑重计算。
3. helper 安装/卸载 UX（SMAppService 注册/注销）、签名/公证、登录项可见性。
4. 睡眠/唤醒语义：launchd 睡眠期不跑，唤醒后补跑一次（`StartCalendarInterval` 错过会在唤醒时触发）。

---

## 4. Windows / Android / iOS 选型

### 4.1 Windows（平台暂停，引擎侧先就绪）

| 方案 | 应用不开能否同步 | 周期 | 长下载 | 权限 | 评价 |
| --- | --- | --- | --- | --- | --- |
| **任务计划程序**（登录触发 + 重复间隔）跑同一 helper | ✅ 登录后 | 准周期（电池/睡眠策略顺延） | ✅ | 用户级 | **推荐**：与 macOS launchd 同构，复用同一 helper/CLI |
| Windows 服务（Session 0） | ✅ 甚至登录前 | 准周期 | ✅ | 需服务安装/管理员 | 无 UI、Session 0 隔离，自托管过度，不推荐 |
| 启动项应用 + 定时器 | ⚠️ 应用进程常驻 | 应用内 tick | ✅ | 无 | 菜单栏常驻的 Windows 类比，过渡可选 |

- 推荐**任务计划程序 + 同一一次性 helper**（WinUI/C# 壳通过 C ABI 或调用引擎二进制）。
- 跨进程协调、凭据（Windows 用 DPAPI/凭据管理器存 CAMS key，仍遵守 token 不进日志）、进度→通知
  （Toast/`AppNotification`）与 macOS 同构。**平台开发暂停**：本文只保证引擎侧跨平台就绪，不写 Windows UI。

### 4.2 Android（全新地基）

**系统约束（官方，见第 8 节来源）**：
- Android 14+：前台服务必须声明 `foregroundServiceType`。
- Android 15（API 35）：`dataSync`/`mediaProcessing` 前台服务 **24h 内累计 6h 上限**（共享，应用回前台重置）；
  超时回调 `Service.onTimeout` 必须 `stopSelf()`，否则崩溃。`shortService` 约 3min。
- **WorkManager 周期最小 15min**，Doze/应用待机下择机执行，不保证准点；厂商 ROM 杀后台更激进。

| 方案 | 适用 | 限制 |
| --- | --- | --- |
| **WorkManager 周期任务** | GFS/EC 直链轻同步、发布探测 | 最小 15min、Doze 择机、不保证周期；网络/充电约束可设 |
| **`dataSync` 前台服务**（用户发起「立即同步」） | 较重下载、需要可见进度通知 | **6h/24h 上限**；CAMS 数小时 ADS 排队可能触顶；须 `onTimeout` 收尾 |
| `shortService`（~3min） | 极短探测/收尾 | 不适合下载 |
| 精确闹钟（`SCHEDULE_EXACT_ALARM`） | 不推荐 | 权限受限、Play 政策严，且仍受 Doze |

**推荐**：WorkManager 周期做**轻同步（GFS/EC）+ 发布探测**；CAMS 重同步走**用户发起的前台服务**或**服务端集中**
（第 6 节）。**如实告知**：受 Doze/厂商/6h 上限影响，Android **无法保证**像桌面一样无人值守周期拉齐 CAMS。
**地基**：引擎编 Android `.so`（cargo-ndk）+ JNI 桥 + Kotlin/Compose UI；凭据用 Android Keystore/EncryptedSharedPreferences。

### 4.3 iOS（全新地基）

**系统约束（官方，见第 8 节来源）**：
- **BGTaskScheduler**：`BGAppRefreshTask`（短刷新，约 30s 级）；`BGProcessingTask`（可运行数分钟但系统可中断，
  **仅设备空闲时运行，用户开始使用即被终止**，常夜间/充电时；需 `processing` `UIBackgroundModes`）；
  须注册 + `Info.plist` `BGTaskSchedulerPermittedIdentifiers`。
- **background URLSession**：网络传输可在 App 挂起后继续（仅网络，无任意常驻代码）。
- **无常驻守护、不保证周期**；低电量模式削减后台；iOS 26+ `BGContinuedProcessingTask` 面向**用户发起**的持续工作。

| 方案 | 适用 | 限制 |
| --- | --- | --- |
| **BGProcessingTask** | 择机轻同步/发布探测 | 数分钟、系统决定时机，**不保证周期** |
| **background URLSession** | GFS/EC 直链下载续传 | 仅网络传输；CAMS ADS 排队（非纯传输）不适配 |
| `BGAppRefreshTask` | 极短刷新 | ~30s 级，不够下载 |
| iOS 26 `BGContinuedProcessingTask` | 用户发起的持续同步 | 需用户发起、新系统；仍非无人值守周期 |
| **前台用户发起 / 服务端集中** | CAMS 重同步 | 见第 6 节 |

**推荐**：iOS = **BGProcessingTask 择机 + background URLSession 跑 GFS/EC 直链**；CAMS 重同步走**前台用户发起**
或**服务端集中**。**明确不承诺** iOS 能后台周期拉齐 CAMS（数小时 ADS 远超后台预算）。
**地基**：引擎编 iOS `xcframework`（C ABI + Swift 桥）+ SwiftUI UI；凭据用 Keychain。

---

## 5. 共享引擎集成方案（四平台同构）

### 5.1 分层：引擎做确定性判定，平台调度器做「何时调用 + 展示」

```
┌─ 平台调度适配器（每平台一个薄壳）──────────────────────────┐
│ macOS: launchd LaunchAgent / SMAppService（或菜单栏 tick）  │
│ Windows: 任务计划程序（或启动项 tick）                      │
│ Android: WorkManager 周期 + dataSync 前台服务               │
│ iOS: BGProcessingTask + background URLSession              │
└───────────────┬───────────────────────────────────────────┘
                │ 调用同一组方法（C ABI / 一次性 CLI / JSONL）
┌───────────────▼─ 共享 Rust 引擎（已跨平台）─────────────────┐
│ data_sync_due（发布探测+到期判定，fail-closed）             │
│ data_sync_run（逐源执行，进度回调，CAMS 跨进程组锁）        │
│ 状态文件 rust-data-sync-state-v1 / 设置 -settings-v1        │
└───────────────┬───────────────────────────────────────────┘
                │ 进度回调 / 状态文件
┌───────────────▼─ 展示与通知（每平台适配）───────────────────┐
│ 应用内数据页：逐源 percent/文件/字节 + 上次/下次同步        │
│ 系统通知：macOS UNUserNotificationCenter / Win Toast /      │
│           Android 前台服务通知 / iOS 本地通知               │
└────────────────────────────────────────────────────────────┘
```

- **判定与执行只有一份**（`desktop_sync.rs`），平台壳只负责触发与展示，杜绝逻辑分叉。
- 周期/发布判定**全部交给引擎**（`due` 已含发布探测），调度器不自己算周期，满足「只要最新已发布」。

### 5.2 跨进程协调（应用无关方案的关键缺口）

- 现状：`operation`/`sync` 是**进程内** Mutex（`desktop_runtime.rs:40`）；只有 **CAMS 组文件锁跨进程**
  （`cams_data.rs:505-516`）。helper 进程与应用进程并存时，二者可能同时跑重计算。
- **方案（推荐）**：新增**同步轮跨进程文件锁**（workspace 下 `sync.lock`，flock/LockFileEx），
  `data_sync_run` 入口取锁；取不到即「另一进程正在同步」显式跳过/返回 busy。这样：
  - helper 与应用互斥，不双跑；
  - 与既有 CAMS 组锁叠加，CAMS 去重仍由组锁保证；
  - 手动 prepare/forecast 的 `operation` 锁仍在各自进程内生效，跨进程重计算冲突由同步轮锁 + 组锁兜底。
- **备选**：保证 helper 与应用**不同时运行**（应用启动时注销/暂停 helper，退出时恢复）——UX 复杂、易竞态，不如文件锁干净。
- 既有边界继承：CAMS 组锁在 ADS 排队期间长持（可达 2h），同区域同 run 手动 prepare 会在 300s 锁超时后显式失败
  （`DATA_SYNC_HELPER_DESIGN` 15.8）；UI 须如实提示「同步排队期间同区域手动准备可能需重试」。

### 5.3 一次性 desktop CLI（helper 前置）

- 新增 `firesky-engine desktop-call --workspace <ws> <method> [--params <json>]`：
  `Runtime::new(ws)` → `dispatch(method, params)` → 打印 JSON → 退出。复用现有运行时，零新逻辑。
- macOS/Windows helper 直接调它；Android/iOS 走 C ABI（`firesky_engine_open`/`call`/`start`）进程内调用。

### 5.4 凭据存储（每平台适配，同一边界）

| 平台 | 存储 | 边界 |
| --- | --- | --- |
| macOS | `~/.cdsapirc`（0600，现状） | token 不进日志/错误（`desktop_credentials.rs:1`） |
| Windows | DPAPI / 凭据管理器 | 同上 |
| Android | Keystore / EncryptedSharedPreferences | 同上 |
| iOS | Keychain | 同上 |

- helper 与 UI 读同一凭据源；CAMS key 不复制进状态文件/通知/日志。

### 5.5 进度 → 展示/通知映射

- 引擎 `run` 的进度回调（`desktop_sync.rs:933`）已逐源/逐阶段发事件。
- **应用内**：数据页展示逐源 percent/文件数/字节数 + 上次/下次同步摘要（读状态文件，helper 写、应用读）。
- **系统通知**：开始/完成/失败各一条；移动端前台服务用持续通知展示进度。
- 这部分直接回应用户「同步进度不知道 / 能否看到进展」，**与是否应用无关解耦**——即使先不做 helper，
  也应先把应用内逐源进度 + 上次/下次摘要补上（引擎事件已具备）。

---

## 6. 战略选项：服务端集中同步 + 移动端薄客户端（强烈建议评估）

**动机**：移动端后台时长/调度限制（第 2、4 节）使「每台手机无人值守周期拉齐 CAMS」基本不可行；
而本项目**轻量自托管（2–3 人）**、已有 **Docker amd64/arm64 + Rust HTTP 引擎**方向
（`RUST_REMAINING_WORK` 头部、`main.rs:67` http 模式）。

**架构**：
```
常开机器（NAS/小主机/Docker）：Rust HTTP 引擎 + launchd/任务计划/cron 定时同步
  └─ 无人值守拉齐 CAMS/GFS/EC 最新已发布周期到服务端缓存（无移动后台限制）
移动端（Android/iOS）+ 桌面：薄客户端
  └─ 查询服务端缓存/预测；必要时用户发起触发服务端同步；本地只做轻缓存
```

**优点**：
- 绕开移动后台 6h/数分钟上限与 Doze/择机；CAMS 数小时 ADS 排队在常开机器上跑完。
- 多设备共享一份最新数据，省流量/省电；符合自托管 2–3 人规模。
- 复用同一 Rust 引擎与 HTTP 模式，移动端无需扛重同步。

**代价/边界**：
- 需要一台常开机器（用户自托管场景通常具备）；移动端离线时依赖本地轻缓存。
- 不引入多租户/集群（遵守既有边界）；HTTP/管理接口按消费者需要安排。
- 与「每设备本地后台同步」**不互斥**：桌面可本地 helper，移动端走服务端，或两者混合。

**建议**：把本选项作为**移动端首选架构**提交用户决策；若用户坚持「每台设备本地后台同步」，
则按第 4 节如实告知 CAMS 限制，移动端重同步退化为「前台用户发起 / 择机 best-effort」。

---

## 7. 分期与决策点

### 7.1 分期（遵守平台暂停声明）

| 阶段 | 范围 | 前置 |
| --- | --- | --- |
| **P0（现在，macOS）** | ① 应用内逐源进度 + 上次/下次摘要（引擎事件已具备）；② 一次性 `desktop-call` CLI；③ 跨进程同步轮文件锁；④ launchd LaunchAgent/SMAppService helper + 安装/卸载/签名 UX | 用户拍板 macOS 方案（A 过渡 / B 目标） |
| **P1（Windows 恢复后）** | 任务计划程序 + 同一 helper；DPAPI 凭据；Toast 通知 | 平台开发恢复；引擎侧 P0 已就绪 |
| **P2（移动端，全新地基）** | 引擎 Android `.so`/iOS `xcframework` 构建 + JNI/Swift 桥 + UI；**优先评估第 6 节服务端集中**；本地后台仅 GFS/EC 择机 | 移动端立项；用户决定「服务端集中 vs 每设备本地」 |
| **P3（Docker/Web）** | 常开 HTTP 引擎 + 定时同步，作为服务端集中底座 | Docker 部署待办 |

### 7.2 待用户拍板的决策点

1. **macOS 目标**：直接做 B（launchd LaunchAgent helper，真正应用无关）？还是先 A（菜单栏常驻，低风险过渡）再 B？
2. **跨进程协调**：采纳「同步轮跨进程文件锁」（5.2 推荐）？还是「helper 与应用不同时运行」备选？
3. **移动端架构**：**服务端集中 + 薄客户端（第 6 节，推荐）**？还是每设备本地后台（接受 CAMS 限制）？
4. **移动端重同步**：若坚持本地，CAMS 是否仅「前台用户发起」，后台只做 GFS/EC 择机？
5. **进度可见优先级**：是否先把应用内逐源进度 + 上次/下次摘要（P0①）独立先做（不依赖 helper）？
6. **helper 注册方式**：macOS 用 SMAppService（现代、系统设置可见）还是传统 LaunchAgent plist？

> 以上均不构成不可逆承诺；任一可经设置或修订本文调整。本文不修改代码，待拍板后再实施。
> 系统级改动（安装 LaunchAgent/登录项、签名公证）属需用户确认的操作，实施前逐项核对。

### 7.3 决策记录（2026-11-13 用户拍板）

| 决策点 | 定案 | 说明 |
| --- | --- | --- |
| **GFS 同步覆盖范围** | **外扩覆盖点预测域**（接受大体积） | 同步 bbox 由锚定区域外扩 `source_radius`（半径 1000km → 外扩 2000km）包络，覆盖锚内任意点预测域；时效改为**整小时** `0..=horizon`。修复「GFS 已同步但单点准备仍缺数据」：旧实现 3 小时间隔 + 锚 bbox 不覆盖点域，导致 54–60 等整小时与点域无法复用缓存。 |
| **macOS 目标（决策 1）** | **B：launchd LaunchAgent helper**（真正应用无关） | 不做菜单栏过渡；直接落地独立无头 helper + LaunchAgent。 |
| **helper 注册方式（决策 6）** | **传统 LaunchAgent plist** + `launchctl bootstrap/bootout gui/$UID` | 较 SMAppService 更可预测（无签名/公证歧义）；plist 烘焙绝对 helper 路径与用户工作区。 |
| **跨进程协调（决策 2）** | **同步轮跨进程文件锁**（5.2 推荐） | `state/sync.lock`（fs2 flock，try-lock 立即让路），应用与 helper 互斥。 |
| **移动端架构（决策 3）** | **每设备本地后台**（非服务端集中） | 接受 iOS/Android 后台限制；第 6 节服务端集中作为后续可选演进，不作首选。 |
| **移动端重同步（决策 4）** | **CAMS 仅前台用户发起**，后台只做 GFS/EC 择机 | 与每设备本地后台一致；CAMS/ADS 长排队不进后台。 |
| **进度可见（决策 5）** | **随 helper 一起**（不独立先做） | 应用内逐源实时进度 + 上次同步时间 + 后台助手上次运行结果一并落地。 |

### 7.4 macOS P0 落地与验收（2026-11-13）

**已实现**
- 引擎：`desktop_sync::sync_gfs` 整小时 + 外扩包络（`desktop_context::gfs_point_domain_envelope`）；
  同步轮跨进程文件锁；一次性 `desktop-call` CLI；`data_sync_tick`（due→run，供各平台 helper 单调用）。
- macOS：独立无头 target `FireSkySyncHelper`（解析内置引擎/资源 → `desktop-call data_sync_tick` →
  写 `state/helper-status.json` + `helper.log` → 有下载/失败时 `osascript` 系统通知）；
  `SyncHelperInstaller`（写 `~/Library/LaunchAgents/com.firesky.datasync.plist` + bootstrap/bootout）；
  设置页「后台助手」安装/卸载/刷新 + 上次后台运行摘要；同步状态行逐源实时进度条 + 上次同步时间。
- 构建：`scripts/build_macos_native.sh` 编译/拷贝/签名 helper，`codesign --verify --deep --strict` 通过。

**验收证据**
- 单元：`gfs_sync_envelope_covers_every_point_domain_inside_anchor`（包络覆盖锚内任意点域）；
  `run_fails_visibly_when_another_process_holds_the_round_lock`（跨进程锁互斥）；`desktop_sync` 23 项全过。
- 真实数据（官方 nomads.ncep.noaa.gov，单小时bounded）：同步外扩包络下载 GFS f055 后，
  杭州点域同小时请求命中**同一缓存文件**（`covering_cache`，schema 相等 + bbox 覆盖），证明 ①+② 复用机制成立。
- launchd 端到端：临时代理（独立 label、禁用同步工作区、`StartInterval=5`、`RunAtLoad`）触发内置 helper，
  `last exit code = 0`、写出 `helper-status.json`/`helper.log`，验证应用无关后台同步；验收后已 bootout 并删除 plist。
- helper 冒烟：禁用同步工作区 → tick `skipped`、状态/日志正确、退出 0。

**已知取舍**：外扩包络为 continental 尺度（china 锚 ≈ [42.25,166.75,72,0]），整小时全时效同步体积大（用户已接受）；
生产由 helper 按间隔在后台跑完。因 due 以周期判定，**已同步的当前周期不会自动补整小时**——需「立即同步」或待下一周期。

---

## 8. 来源（官方文档；URL 与当前选型目标一致，未使用任何代理替换）

**Android**
- Android 15 行为变更（`dataSync`/`mediaProcessing` 前台服务 6h/24h 上限、`onTimeout`）：
  https://developer.android.google.cn/about/versions/15/behavior-changes-15
- 前台服务超时（`Service.onTimeout`、`stopSelf`、各类型时长）：
  https://developer.android.google.cn/develop/background-work/services/fgs/timeout
- WorkManager 周期任务（`PeriodicWorkRequest` 最小 15min、flexInterval、约束）：
  https://developer.android.com/develop/background-work/background-tasks/persistent/getting-started/define-work

**iOS**
- Background Tasks（刷新与维护 App、BGTaskScheduler 注册）：
  https://developer.apple.com/cn/documentation/backgroundtasks/refreshing-and-maintaining-your-app-using-background-tasks
- `BGAppRefreshTask`（短刷新）：
  https://developer.apple.com/cn/documentation/backgroundtasks/bgapprefreshtask
- `BGProcessingTask`（数分钟、系统择机）：
  https://developer.apple.com/cn/documentation/backgroundtasks/bgprocessingtask

**macOS / Windows**
- launchd / LaunchAgent（`StartInterval`/`StartCalendarInterval`）：
  https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html
- `SMAppService`（macOS 13+ 登录项/Agent/daemon 注册）：
  https://developer.apple.com/documentation/servicemanagement/smappservice
- Windows 任务计划程序：
  https://learn.microsoft.com/en-us/windows/win32/taskschd/task-scheduler-start-page

> 引擎侧事实（`desktop_sync.rs`/`ffi.rs`/`main.rs`/`desktop_runtime.rs`/`cams_data.rs`/`ads.rs`/
> `FireSkyApp.swift`）均基于当前工作区实际代码核对（`文件:行号` 见正文），非记忆推断。