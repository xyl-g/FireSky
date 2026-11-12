# 后台数据同步助手设计（CAMS / GFS / EC 最新周期）

日期：2026-11-13。状态：**设计提案，待用户拍板**；本文只新增设计，不修改任何
现有代码。所有引用基于当前工作区实际代码审计（`文件:行号`），下载能力的前提
沿用 [CAMS 双路线提案](CAMS_DUAL_ROUTE_PROPOSAL.zh-CN.md) 第 0 节决定 5
（整周期/洲包络下载已实现并经 2026-11-13 真实验收）。

## 0. 需求、决定记录与历史决定边界

### 0.1 新需求（用户 2026-11-13）

1. macOS 端在后台自动同步 CAMS/GFS/EC **最新周期**数据：用户显式启用后，
   引擎在后台把最新已发布周期一次性拉齐到本地缓存，使后续手动预测几乎零排队。
2. 引擎侧 Rust 实现（跨平台）；macOS 设置页提供开关与调度配置；**默认关闭，
   不擅设默认值**，用户显式启用。
3. Windows「如果有就更好」：平台开发整体处于暂停（见
   [RUST_REMAINING_WORK.zh-CN.md](RUST_REMAINING_WORK.zh-CN.md) 头部暂停声明），
   本设计只保证**引擎侧跨平台**（同一套 IPC 方法、同一 FFI ABI），不写 Windows UI。
4. 卫星（HSD）**不在本设计范围内**：沿用 Q07（2026-11-11，已确认）——
   「不常驻后台定时下载/处理卫星数据；发起实况预测时进入统一预检弹窗」
   （`docs/RUST_REMAINING_WORK.zh-CN.md` R04 节）。用户 2026-11-13 的新要求
   仅覆盖 CAMS/GFS/EC 三个模式数据源，**不推翻 Q07**：卫星默认仍不自动定时，
   若将来用户明确要卫星后台同步，属于对 Q07 的新决定，须单独提案并记录。

### 0.2 与既有决定的边界（逐条）

| 既有决定 | 本设计的关系 |
| --- | --- |
| Q01/R12 预测前统一预检弹窗 | 不变。同步只是**预取**，不替代预测前的 check/prepare/confirm；同步从不写会话（`desktop_session`）也不写周期 pin（`desktop_runtime.rs:254-262` 的 `pin` 仅属于手动链路）。 |
| Q05/R03 城市排名随区域计算、不另设预热 | 不变。本设计不新增 `/cache/warmup`，不为排名扩大下载；R03 的旧 national refresh 只读巡检语义不动。 |
| Q07/R04 卫星不后台定时 | **边界声明**：见 0.1 第 4 条。同步助手的调度机制为模式数据专用；卫星源不在 `sources` 枚举内。 |
| Q08/R05 最新周期选择 | 预测时仍以预检报告为准（`desktop_runtime.rs:526-568` check + pin）。同步使用独立的发布探测判定「上游已有新周期」，预检合同不受影响。 |
| Q09/R06 CAMS 手动 run 固定排除 | 同步按自动选择取**最新可用 run** 并记录实际 run 时间到状态与结果，不新增任何手动周期入口。 |
| Q11/R08 任务持久化、重启不自动续跑 | 同步任务写入同一 `TaskHistory`；应用退出后未完成任务标 `interrupted`，**不自动重新下载**，手动「立即同步」才重新执行（与既有合同一致，`engine/src/task_history.rs:296-299,519-556`）。 |
| Q12/R09 保留策略 | 同步成功（校验通过）后才触发保留清理，与手动 prepare 同一入口（`engine/src/desktop_runtime.rs:274-278`）；下载中或失败的周期不淘汰已有可用周期。 |
| 决定 5（双路线提案第 0 节） | 直接复用：洲包络（`engine/src/continents.rs:65-91`）+ `all_leads`（`engine/src/cams_data.rs:764-778`）+ 默认 `continent_cycle`（`engine/src/desktop_prepare.rs:227-244`）。 |

## 1. 现状审计：可复用面与约束面

### 1.1 可复用的既有能力

| 能力 | 位置 | 说明 |
| --- | --- | --- |
| 上游最新周期探测 | `engine/src/desktop_publications.rs:174-187`（GFS HEAD f006）、`:189-205`（EC index HEAD）、`:207-221`（CAMS 经 `latest_publication_cycle`，`engine/src/cams_data.rs:852-877`） | 返回 `verified` 标志；失败回落启发式并标 `verified=false`（`:111-132`）；结果带 300 秒缓存（`:21`、`:237-255`） |
| CAMS 洲包络 | `engine/src/continents.rs:65-91` `envelope_for`：最小包含洲，否则相交洲并集，海洋点回落请求区域 | 预检覆盖判定已接受覆盖型文件（`engine/src/desktop_preflight.rs:206-218`） |
| CAMS 全时效下载 | `engine/src/cams_data.rs:80` `all_leads` 参数；`:764-778` 把下载组扩展到该 run 全部 lead（≤999）；`:745-754` 按 `(run.day, run.clock)` 分组 | 手动 prepare 已默认 `continent_cycle`（`engine/src/desktop_prepare.rs:233-244`），一次 ADS 任务下载整洲整 run（验收：亚洲包络 40 lead、122 MiB、126 秒） |
| CAMS 缓存锁内去重 | `engine/src/cams_data.rs:505-516`（按 run+area 取文件锁）、`:523-547`（锁内重查缓存）、`:548-578`（提交 ADS 并原子发布） | 第二个调用者等锁后直接命中缓存，不重复排队 ADS |
| ADS 并发上限 | `engine/src/cams_data.rs:24-48` `Slot`：默认 2，`FIRESKY_CAMS_DOWNLOAD_WORKERS` 1..4 | 全局并发闸门，手动与后台共用 |
| GFS 缓存复用 | `engine/src/gfs_data.rs:675-694` `resolve`；`:611-662` `find` 全索引 + `:155-170` `bbox_covers` 覆盖判定；`:695-711` region 子目录仅为存储布局 | 大 bbox 缓存可覆盖小 bbox 请求；region 子目录不影响命中 |
| EC 下载 | `engine/src/ecmwf_data.rs:122-139` Params（`operation="download"` + `forecast_hours`）；`:94-117` 三镜像 URL | `desktop_prepare.rs:144-164` 已按小时列表下载 |
| 运行时单重计算锁 | `engine/src/desktop_runtime.rs:40` `operation: Mutex<()>`；`:600-604`（prepare/forecast）、`:691-694`（compute）均为 `try_lock`，冲突即报「another prepare/forecast is active」 | 一条重计算链；`publications` 特意不长时间持锁（`:586-597`） |
| 任务/取消模型 | `engine/src/desktop_runtime.rs:399-427` `begin_task`（方法白名单 `:405-410`）；serve 四 worker + 16 深度队列（`:1699-1706`）、按 `request_id` 取消（`:1819-1834`）；FFI 任务句柄 `firesky_engine_start/cancel/join`（`engine/src/ffi.rs:322-367,371-383,386-412`） | TaskHistory 状态机 running/done/failed/cancelled/interrupted（`engine/src/task_history.rs:222-223`） |
| 设置持久化管道 | `engine/src/retention_settings.rs:20-35`（版本化 contract + `deny_unknown_fields`）、`:37-46`（workspace 相对路径 + 环境变量覆盖）、`:133-161`（临时文件原子 persist） | macOS 侧 `retention_settings`/`save_retention_settings` 分发（`desktop_runtime.rs:475-476`）与 Swift 管道（`apps/macos/Sources/FireSkyNative/Store.swift:1035-1088`） |
| 保留执行 | `engine/src/cache_retention.rs:636-698` `apply`；CAMS 周期解析 `:324-337`；按周期从新到旧保留 `:366-400`；锁占用时延后 `:239-247` | 「新数据校验成功后才清理」已实现 |
| 凭据边界 | `engine/src/desktop_credentials.rs:1`（「Never return or include tokens in error messages」）、`:97-105` `key()`、`:107-150` `save()` | CAMS key 只存 `~/.cdsapirc`（0600 语义，见 DialogViews 提示 `apps/macos/Sources/FireSkyNative/DialogViews.swift:192`） |

### 1.2 约束面（设计必须服从）

1. **单重计算锁不可长时间占用**：ADS 排队可达小时级（用户确认，双路线提案
   第 0 节决定 4；`engine/src/ads.rs:46` `wait_timeout_seconds: 7200.0`）。
   后台同步若持有 `operation` 锁，会把手动的 prepare/forecast 阻塞数小时——不可接受。
2. **取消是协作式、阶段边界生效**：`cams_data::execute` 本身不接收 cancel 标志；
   `desktop_prepare` 只在阶段入口检查（`engine/src/desktop_prepare.rs:214,335-341,343-344`）。
   已提交的 ADS 组会运行到自身超时，这是现状边界，同步继承。
3. **无 Python 生产合同**：所有新路径 `runtime_python_used:false`（对照
   `desktop_runtime.rs:388-389`、`cams_data.rs:837-848`）。
4. **fail-closed**：缺数据、组错误一律显式失败（`desktop_prepare.rs:259-264`
   缺项即整单失败；`cams_data.rs:796` 组错误收集后仍是显式 missing）。
5. **macOS 应用生命周期 = 同步生命周期**：应用随最后窗口关闭退出
   （`apps/macos/Sources/FireSkyNative/FireSkyApp.swift:43`），引擎 FFI 运行时
   由应用进程持有（`engine/src/ffi.rs:261-282`）。「后台」指**应用运行期间**的
   后台数据同步，不是系统级守护进程。

## 2. 目标与非目标

**目标**

- T1 引擎新增 `desktop_sync` 模块：设置/状态/到期判定/单轮同步执行，纯 Rust、
  跨平台、无 Python、可取消、fail-closed。
- T2 CAMS 同步复用洲包络 + `all_leads`，一个 run 一次 ADS 任务拉齐全部时效。
- T3 GFS/EC 同步拉齐最新已验证周期的固定时效集（范围见第 6.2/6.3 节）。
- T4 与手动 prepare 双向去重，不新增重复排队；尊重单重计算锁与 ADS Slot 上限。
- T5 遵守保留策略：成功后清理，CAMS 保留 3 周期等用户设置继续生效。
- T6 结构化 provenance 与任务历史：每轮同步是可查、可取消、可重试的历史任务。
- T7 macOS 设置页：主开关（默认关）、逐源开关、间隔、同步锚定区域、EC 下载源
  引用、立即同步按钮；凭据不进任何日志。

**非目标**

- 不写 Windows/Linux UI（引擎方法按同一 ABI 暴露即可）。
- 不做卫星后台定时（Q07 不变）。
- 不做系统守护进程/登录前同步/菜单栏常驻。
- 不改变预测主链的预检、pin、确认合同；不新增手动 CAMS run 固定。
- 不引入 Redis/多实例调度（R05 同款边界）。

## 3. 总体架构

### 3.1 分层：引擎做确定性判定，UI 做节拍与展示

- **引擎（跨平台核心）**新增 `engine/src/desktop_sync.rs`（注册于
  `engine/src/lib.rs`，紧随 `desktop_publications` 之后；对照 `engine/src/lib.rs:34`）：
  1. 版本化设置读写（合同 `rust-data-sync-settings-v1`）；
  2. 同步状态文件（合同 `rust-data-sync-state-v1`，记录每源 last_cycle、
     last_result、consecutive_failures、next_due 语义）；
  3. `due` 纯判定函数：本地状态 + 发布探测 → 每源「到期/未到期/原因」；
  4. 单轮同步执行：逐源调用既有 provider，输出 `rust-data-sync-v1` 结构化结果。
- **macOS UI** 只做三件事：设置编辑、60 秒级心跳 tick（到点即调 `data_sync_run`）、
  状态与历史展示。UI 不复制任何调度决策逻辑——`data_sync_due` 返回什么就执行什么。

**为什么不用引擎内部调度线程**（对照后选定 UI tick）：

| 方案 | 结论 |
| --- | --- |
| 引擎内常驻调度线程 | 需要新造一套与 serve 队列并行的任务注册表和取消通道（现有 `cancel` 只认 serve 的 `active` 表，`desktop_runtime.rs:1819-1834`）；FFI 句柄模型（`ffi.rs:322-367`）无法被 UI 直接取消引擎自启线程；且收益为零——引擎进程本就随应用退出（`FireSkyApp.swift:43`）。 |
| **UI tick + 引擎判定（选定）** | 完整复用现有 FFI 异步任务（`FFIEngineClient.swift:135-189` 每个调用都是可取消任务）、serve 取消、`begin_task` 历史与进度回调；引擎保持纯请求/响应，与 V03「原生 UI 负责系统能力，Rust Runtime 统一业务」的分工一致（`docs/RUST_REMAINING_WORK.zh-CN.md` V03 节）。 |

### 3.2 新增 IPC 面（全部进入 `dispatch_cancellable` 的 match，`engine/src/desktop_runtime.rs:460-677`）

| 方法 | 语义 | 锁/阻塞 |
| --- | --- | --- |
| `data_sync_settings` | 返回设置状态（合同、configured、path），镜像 `retention_settings::status`（`retention_settings.rs:121-131`） | 只读 |
| `save_data_sync_settings` | 校验并原子保存；`enabled=true` 时要求全部显式字段合法 | 短临界区 |
| `data_sync_due` | 无副作用判定：每源 `due/not_due` + 原因（`interval_not_elapsed` / `publication_unverified` / `cycle_current` / `suspended_failures` / `busy`）+ 当前发布探测标签 | 只读 + 网络 HEAD/CAMS constraints（有 300 s 缓存，`desktop_publications.rs:21,237-255`） |
| `data_sync_run` | 执行一轮同步；params 必须显式给 `sources`（`["cams","gfs","ec"]` 子集，非空，无默认全集）；成功后应用保留清理 | 见 7.1 锁矩阵 |
| （取消） | 不新增方法：FFI 走任务句柄 `firesky_task_cancel`（`ffi.rs:371-383`）；JSONL 走既有 `cancel` + `request_id`（`desktop_runtime.rs:1819-1834`） | — |

同时：`begin_task` 白名单加入 `"data_sync"`（`desktop_runtime.rs:405-410`），
`hello.capabilities` 加入 `"data_sync"`（`desktop_runtime.rs:465`）。新增
`Runtime.sync: Mutex<()>` 单轮互斥（仿 `network_probe` 的独立锁
`desktop_runtime.rs:41,512-518`），保证同一时刻至多一轮同步。

## 4. 设置与状态合同

### 4.1 `rust-data-sync-settings-v1`

文件 `.firesky/settings/data_sync.json`（16 KiB 上限、临时文件原子 persist、
符号链接拒绝、`deny_unknown_fields`——全部照抄 `retention_settings.rs:15-18,89-114,133-161`
的既有实现；环境变量 `FIRESKY_DATA_SYNC_SETTINGS_PATH` 覆盖路径，
对齐 `retention_settings.rs:37-46`）。

| 字段 | 类型 | 校验 | 默认 |
| --- | --- | --- | --- |
| `enabled` | bool | — | `false`（**默认关闭**） |
| `sources` | object | `cams/gfs/ec` 三个 bool | 全 `false`（启用 `enabled` 时至少一个为 true，否则保存报错） |
| `interval_minutes` | u32 | 启用时必填，30..=1440 | 无（启用时缺值报错，不替用户选） |
| `anchor_region` | string | 必须命中 `desktop_context::REGIONS` 代码（`engine/src/desktop_context.rs:20-31`，查找 `:144`） | 无（启用时必填） |
| `lead_horizon_hours` | u32 | 启用 gfs/ec 时必填，3..=240 且 3 的倍数；CAMS 不使用此字段（全时效来自 ADS 约束） | 无 |
| `ecmwf_source` | string | `ecmwf/aws/google`（同 `ecmwf_data.rs:76`），UI 保存时预填当前 `store.ecmwfSource`（`Store.swift:130-138`） | 启用 ec 时必填 |

决策语义：**所有「启用时必填」字段缺一即保存失败**，符合「默认关闭、不擅设
默认值」硬约束；保存成功即重启调度语义（下次 tick 生效，无需重连引擎）。

### 4.2 `rust-data-sync-state-v1`

文件 `.firesky/state/data-sync.json`（与任务历史同目录父级，
`desktop_runtime.rs:108-112` 默认 `.firesky/state/tasks` 的父目录；同样 16 KiB
上限、原子写）。引擎独占写，`data_sync_due`/`data_sync_status` 读。

| 字段 | 说明 |
| --- | --- |
| `sources.{name}.last_cycle` | 最近一次成功同步的模式周期（ISO UTC；CAMS 为 run time） |
| `sources.{name}.last_result` | 最近一轮结果摘要（status、files、errors、elapsed_ms、task_id） |
| `sources.{name}.consecutive_failures` | 连续失败轮数 |
| `sources.{name}.suspended` | 连续失败达阈值后置 true，等待人工解锁（第 8.3 节） |

状态文件**不含凭据、不含绝对用户数据路径之外的内容**，可随时删除重建
（删除后等效「从未同步」，下轮 due 会重新比较发布周期，不会误下载旧周期之外的东西——
下载本身由缓存命中去重兜底）。

## 5. 调度触发方式

### 5.1 macOS 心跳 tick（Swift 侧，唯一节拍源）

`Store` 增加常驻 `Task`（生命周期同 `publicationTask` 等既有任务字段，
`Store.swift:179-186`；`shutdown()` 统一取消，`Store.swift:1204-1214`）：

```
循环 while store.online:
    睡眠 min(60s, interval/2)          // tick 粒度 60 秒
    guard 设置存在且 enabled           // data_sync_settings 缓存于连接时（对齐 loadRetentionSettings 的 connect 时加载，Store.swift:275）
    due = engine.call("data_sync_due")
    if due.due_for_any && !store.busy:   // busy = 手动会话进行中（Store.swift:46,509-529）
        engine.call("data_sync_run", sources: due.due_sources, progress: …)
```

要点：

1. tick 期间若用户正处手动 prepare/forecast（`store.busy`），**跳过本轮**，
   不发起 `data_sync_run`——这是 UI 侧的第一道让路。
2. `data_sync_due` 的网络探测依赖 `desktop_publications` 的 300 秒小时级缓存
   （`desktop_publications.rs:21,237-255`），60 秒 tick 的实际探测频率 ≤ 每
   5 分钟一次每源；GFS/EC 为 HEAD 请求，CAMS 为一次 ADS constraints 查询
   （`cams_data.rs:852-877`，走既有排队合同）。
3. 应用启动后立即做一次 tick（不等首个睡眠）；从睡眠唤醒由系统恢复
   `Task.sleep` 计时自然触发，不做专门唤醒监听（保持 UI 极薄）。
4. 手动触发：数据页「立即同步」按钮直接 `data_sync_run`，绕过 due 判定
   （同时重置该源 `suspended` 与 `consecutive_failures`，作为人工解锁通道）。

### 5.2 引擎侧 due 判定（`data_sync_due`，确定性、可单测）

对每个 `enabled` 且 `sources.{name}==true` 的源，顺序判定：

1. `suspended == true` → `not_due(suspended_failures)`；
2. 距 `last_attempt` 不足 `interval_minutes` → `not_due(interval_not_elapsed)`；
3. 发布探测（`desktop_publications::execute`，超时沿用其上限 60 s 合同
   `desktop_publications.rs:22-23`）：`verified != true` →
   `not_due(publication_unverified)`——**启发式周期不触发下载**（fail-closed，
   对照 `desktop_publications.rs:111-132` 回落语义）；
4. verified 周期 ≤ `last_cycle` → `not_due(cycle_current)`；
5. 否则 `due`，原因记录 `new_cycle:<label>`。

判定函数设计为纯函数（注入发布探测结果），ADS/GFS/EC 网络路径不做任何
网络 mock 假验收（第 13 节测试计划同理）。

## 6. 单轮同步执行（`data_sync_run`）

进度事件复用 prepare 的 `{"stage":"sync","percent":…,"source":…,"message":…}`
形状（对照 `desktop_runtime.rs:623-630`）；每源独立成败（对照
`forecast_models` 的逐模型 error map 模式，`desktop_runtime.rs:721-748`）；
全程 `check(cancel)` 协作取消。

### 6.1 CAMS（复用洲包络 + all_leads，一个 run 一次任务）

1. 锚定区域 `anchor_region` → `REGIONS` bounds。注意坐标序差异：
   `REGIONS` 是 `[west, east, north, south]`（`desktop_context.rs:20-31`），
   `continents::envelope_for` 收 `[north, west, south, east]`
   （`continents.rs:11-19,65-91`），转换必须显式并加单测。
2. `envelope_for(anchor)` 得下载包络（中国锚点 → 亚洲
   `[81,26,-11,180]`，海洋锚点 → 锚定区域本身，`continents.rs:86-89`）。
3. 目标 run：`desktop_publications` 探测的 verified CAMS 周期
   （内部即 `latest_publication_cycle`，`cams_data.rs:852-877`）。为把组
   钉到该 run，取目标时刻 = `run + 12h/24h/36h` 三个 3 小时对齐时刻
   （1..256 目标上限内，`cams_data.rs:687-697`；`Availability.choose`
   对晚于目标时刻的 run 直接跳过 `cams_data.rs:316-318`，delta 相同时
   rank 偏好更新的 run `:329-347`，三目标必然收敛到同一 run、一个组）。
4. 调 `cams_data::execute { operation:"batch", all_leads:true,
   target_utcs:[上述三时刻], area:envelope, allow_download:true,
   require_exact_valid_time:true }`：`"batch"` 不做 single 回退
   （`:703-707,799-818`），组扩展到该 run 全部 lead（`:764-778`）；
   组内先锁后查缓存再提交（`:505-578`）。
5. 判定：`group_errors` 与 `missing_valid_times` 任一非空 → 该源失败
   （fail-closed，对齐 `desktop_prepare.rs:259-264`）；成功则
   `last_cycle = run`，结果附 `cams_data` 原生计时字段
   （`cams_data.rs:840-847`）与 `all_leads:true` 标记（`:839`）。

### 6.2 GFS

1. 周期 = verified GFS 发布周期（`desktop_publications.rs:174-187`）。
2. bbox = `anchor_region` bounds 经 `gfs_data::canonical_bbox` 网格对齐
   （`gfs_data.rs:127-146`）；region 标签 = `anchor_region`（仅存储布局，
   `gfs_data.rs:695-711`；命中靠全索引 + `bbox_covers`，`:611-662`）。
3. 时效集 = `0..=lead_horizon_hours` 步长 3（用户显式配置，见 4.1），
   逐小时 `GfsClient::resolve(cycle, hour, Some(bbox), false, true)`
   （`desktop_prepare.rs:116-142` 同款调用；客户端参数同
   `desktop_prepare.rs:93-102`：90 s 超时、300 s 锁、4 次尝试）。
4. 任一小时 resolve 失败 → 该源失败；成功的文件天然进入与手动 prepare
   相同的索引与覆盖体系。

### 6.3 EC

1. 周期 = verified ECMWF 发布周期（`desktop_publications.rs:189-205`）。
2. `ecmwf_data::execute { operation:"download", forecast_hours:[0..=horizon 步长 3],
   config.source = 设置的 `ecmwf_source` }`（`desktop_prepare.rs:144-164` 同款；
   Config 默认 90 s 超时/300 s 锁/3 次尝试，`ecmwf_data.rs:37-50`）。
3. 判定与状态更新同 6.1。**体积风险**：EC basic bundle 为逐时效全局文件，
   单周期总量未测量，验收时如实记录（第 14 节 A4）。

### 6.4 收尾：保留清理与状态提交

- 全部请求源完成后（无论成败），仅对**成功且校验通过**的源所在缓存执行
  `retention_settings::apply_configured(&workspace)`（`retention_settings.rs:163-177`；
  手动链路同款，`desktop_runtime.rs:274-278`）。任何失败源不触发清理。
- 原子更新状态文件；`task.finish` 写 TaskHistory（成功存受限结果引用，
  失败/取消不发布成功结果，`task_history.rs:519-556`）。

## 7. 后台任务与手动 prepare 的冲突处理

### 7.1 锁矩阵

| 锁 | 手动 prepare/forecast | 后台同步 | 说明 |
| --- | --- | --- | --- |
| `operation`（`desktop_runtime.rs:40,600-604,691-694`） | 持有（全程） | **不持有**；入口 `try_lock` 探测，被占即报 busy 让路（消息含 "active"，FFI 侧自动映射 `FIRESKY_FFI_BUSY`，`ffi.rs:81-83`） | ADS 排队小时级，同步绝不能占死计算锁 |
| `sync`（新增，仿 `network_probe` 锁 `desktop_runtime.rs:41,512-518`） | 不持有 | 持有（单轮全程） | 同一时刻至多一轮同步；手动「立即同步」与 tick 竞争时后来者 busy |
| CAMS 组文件锁（`cams_data.rs:505-516`） | 使用 | 使用（同一把） | 跨进程互斥，去重的核心 |
| ADS `Slot`（`cams_data.rs:24-48`，默认 2） | 使用 | 使用 | 同步最多占 1 槽（单组串行），手动路径至少还有 1 槽 |
| GFS 每目标锁/索引锁（`gfs_data.rs:731,790-793`） | 使用 | 使用 | 按文件粒度互斥 |

### 7.2 双向去重语义

- **同步先行，手动后至**：手动 prepare 的 CAMS resolve 命中缓存
  （`cams_data.rs:353-385` `current_cache` 精确选择），0 次 ADS 任务；
  与 2026-11-13 广州点验收行为一致（双路线提案第 0 节决定 5）。
- **手动先行，同步后至**：同步入口 `try_lock(operation)` 失败 → 本轮
  `busy` 让路，下个 tick 再判。若手动 prepare 在探测之后才启动：
  两者对同一 (run, area) 的 ADS 提交被组文件锁串行化——后到者在锁内
  重查缓存直接命中（`cams_data.rs:523-547`），不会出现重复排队。
- **绝不触碰会话**：同步不创建/更新/消费 `desktop_session`，不写
  `_{model}_cycle_override`（pin 仅由手动 check 报告驱动，
  `desktop_runtime.rs:254-262,559,637`），因此手动预测的请求身份与
  缓存 key（`cache_key`，`desktop_runtime.rs:1501-1516`）完全不受影响。
- **结果可见性**：同步轮次出现在同一 `TaskHistory` 列表（方法
  `data_sync`），用户能区分「后台同步」与「手动准备」两类任务。

## 8. 失败与重试边界

### 8.1 客户端级（既有，不新增重试循环）

GFS 4 次（`desktop_prepare.rs:100`）、EC 3 次（`ecmwf_data.rs:46`）、
ADS 3 次提交 + 最长 2 小时排队等待（`ads.rs:45-50`）。同步不再叠加重试。

### 8.2 轮次级

- 每源独立成败，失败不阻断其他源（对照 `desktop_runtime.rs:721-748`）。
- 失败源：状态文件记 `last_result.status="failed"` 与结构化错误字符串
  （不含凭据），`consecutive_failures += 1`；**不删除任何既有文件**，
  **不改 `last_cycle`**，**不触发保留清理**。
- 部分成功（如 GFS 40/41 文件）按失败处理（fail-closed）；已下载的文件
  保留在缓存，下一轮经覆盖判定复用（`gfs_data.rs:645-661`）。

### 8.3 跨轮次

- 失败源等到下一个 due 时刻才重试（不做立即指数退避）；同轮内不重试。
- **连续失败熔断**：某源 `consecutive_failures >= 3` 后置 `suspended=true`，
  自动调度停发该源，避免 ADS key 过期/条款失效时的 nightly 无效锤击；
  人工「立即同步」或重新保存设置即解锁并清零计数。
  （阈值 3 为提案值，列入第 16 节决策点。）
- 取消：用户取消同步任务（FFI 句柄 / JSONL cancel）→ `cancelled` 终态、
  不发布成功结果（`task_history.rs:519-556` 既有语义）；取消延迟以阶段边界
  为准（1.2 第 2 条的既有边界，如实写进 UI 提示文案）。
- 应用退出：运行中的同步轮按 Q11/R08 标 `interrupted`，**不自动重启**
  （`task_history.rs:296-299`）；下次启动由 due 判定自然重新规划，
  已完成文件经缓存命中复用。

## 9. 保留策略与磁盘

- 用户已定 CAMS 保留 3 周期（本设计输入）：同步每拉齐一个新 run，
  成功后 `apply_configured` 触发 `cache_retention::model_cycles` 按文件名
  解析 CAMS run（`cache_retention.rs:324-337`）保留最新 3 个周期
  （`:366-400`）。以验收实测 122 MiB/周期计，CAMS 稳态 ≈ 366 MiB。
- GFS/EC 保留周期数沿用用户各自设置；**未配置保留时同步仍可运行**，但
  `data_sync_status` 返回 `retention_configured:false`，macOS 设置页在该
  开关旁显示「未配置保留，缓存将随同步增长」的软警告（是否改为硬性
  要求列入决策点）。
- 清理遵循全部既有保护：活动锁延后删除（`cache_retention.rs:239-247`）、
  在用旧周期等任务结束后再删（Q12 补充确认，R09 节）、只清缓存根内
  文件、报告真实释放量（`:67-83`）。

## 10. 结构化 provenance 与凭据

### 10.1 `rust-data-sync-v1` 每轮输出

```json
{
  "contract": "rust-data-sync-v1",
  "runtime_python_used": false,
  "started_at_utc": "…", "elapsed_ms": …,
  "sources": {
    "cams": {
      "status": "synced|current|failed|cancelled",
      "cycle": "…T00:00:00+00:00", "cycle_label": "2026-11-13 12Z",
      "publication_verified": true,
      "envelope": [81.0, 26.0, -11.0, 180.0], "area_key": "…",
      "all_leads": true, "groups": 1, "single_fallbacks": 0,
      "group_errors": [], "missing_valid_times": [],
      "files": …, "cache_hits": …,
      "asset_downloads": 1, "constraints_requests": …,
      "timings_ms": {"constraints": …, "job_poll_transfer": …, "…": …},
      "retention": {…} | null,
      "task_id": "…"
    }
  }
}
```

CAMS 子对象直接内嵌 `cams_data::execute` 的对应字段（`cams_data.rs:836-849`），
保证来源身份（`source_identity`，`cams_data.rs:828`）与手动链路同源可追溯；
GFS/EC 子对象同样内嵌各自 provider 的状态/路径/计时输出。

### 10.2 凭据边界

- CAMS key 只经 `desktop_credentials::key()` 读取（`desktop_credentials.rs:97-105`），
  该模块合同为「token 永不返回、不出现在错误消息」（`:1,107-150`）；同步
  结果、状态文件、TaskHistory 事件、进度事件一律不含 key。
- 同步不需要新凭据；GFS/EC 为公开端点（`gfs_data.rs:773-779`、
  `ecmwf_data.rs:51-71` 已拒绝带凭据 URL）。

## 11. macOS 接线（原生 UI）

### 11.1 设置页（`DialogViews.swift` SettingsView，`:151-273`）

在「缓存与报告保留」Section（`:221-251`）之后新增「后台数据同步」Section：

| 控件 | 绑定 | 行为 |
| --- | --- | --- |
| Toggle「启用后台同步」 | `$store.dataSyncEnabled` | 默认关（`Store` 初值 `false`，不经 UserDefaults 持久化——持久化事实来源在引擎设置文件，UI 只镜像） |
| 源开关 ×3（CAMS/GFS/EC） | `$store.dataSyncSources…` | 启用主开关后可编辑 |
| 间隔 TextField + 「分钟」 | `$store.dataSyncInterval` | 空即未设置；非法值由引擎保存时报错回显（复用 retentionMessage 模式 `:237-244`） |
| 锚定区域 Picker | `$store.dataSyncAnchor` | 选项来自 `hello.regions`（`Store.swift:269` 已有 `regionDefinitions`） |
| EC 下载源 | 复用现有 `ecmwfSource`（`:163-167`） | 保存时写入同步设置的 `ecmwf_source` |
| 保存按钮 | `store.saveDataSyncSettings()` | 管道照抄 `saveRetentionSettings`（`Store.swift:1055-1088`）：调 `save_data_sync_settings`、用响应回填 UI、错误红字提示 |
| 状态行 | `store.dataSyncStatus` | 显示每源 last_cycle/last_result（`data_sync_settings`/状态合并响应），retention 未配置时显示软警告 |

连接时加载：`connect()` 在 `loadRetentionSettings()`（`Store.swift:275,1035-1053`）
旁新增 `loadDataSyncSettings()`。

### 11.2 数据页（`InventoryView`，`DialogViews.swift:90-149`）

- 顶部工具行（`:97-105`）旁新增「立即同步」按钮：直接 `data_sync_run`
  （显式传当前设置中启用的源），带进度（复用 `updateProgress`，
  `Store.swift:560-567`），busy 时禁用（对齐「按本次位置下载」的
  `store.busy` 守卫模式 `:98-101`）。
- 同步轮次自动出现在页面下方的 `NativeTaskHistoryView`（`:145`），
  无需额外接线。

### 11.3 心跳与生命周期

- `Store` 新增 `syncTickTask`，`connect()` 成功后启动、`shutdown()`
  取消（`Store.swift:1204-1214`）；tick 内容见 5.1。
- 引擎未连接/离线时 tick 静默跳过；`status` 不被同步消息覆盖正在进行的
  手动操作文案（`store.busy` 期间 tick 直接跳过）。

## 12. Windows 与跨平台边界

- 本设计交付的引擎方法（`data_sync_settings/save_data_sync_settings/
  data_sync_due/data_sync_run`）全部走 `dispatch_cancellable` 同一 match
  （`desktop_runtime.rs:460-677`），JSONL 与 FFI 两个后端天然一致；
  FFI ABI 无变化（仍 `firesky_engine_call/start/cancel/join`，
  `ffi.rs:254-427`，`FIRESKY_FFI_ABI_VERSION` 不递增）。
- Windows（WinUI/C#，当前暂停）恢复后只需按 macOS 第 11 节同构实现
  设置 Section + tick + 立即同步，即「如果有就更好」的最小增量；
  本设计不写其 UI。
- 路径/环境变量（`FIRESKY_DATA_SYNC_SETTINGS_PATH`、workspace 相对布局）
  已是跨平台写法（对照 `retention_settings.rs:37-46`、
  `desktop_runtime.rs:66-75` 的 env_path）。

## 13. 测试计划

### 13.1 Rust 单测（新增于 `engine/src/desktop_sync.rs`，风格对照 `retention_settings.rs:179-229` 与 `desktop_runtime.rs:1893-2173`）

1. 设置合同：默认禁用、缺必填字段保存报错、`deny_unknown_fields`、
   非法 interval/region/lead_horizon/ecmwf_source 拒绝、symlink 拒绝、
   原子替换后可回读。
2. due 纯函数：未启用→not_due；间隔未到→not_due(interval)；verified=false
   →not_due（启发式不触发下载）；verified 新周期→due；≤last_cycle→current；
   suspended→not_due 且人工解锁恢复。
3. CAMS 计划器纯函数：REGIONS bounds 序转换（west/east/north/south →
   north/west/south/east）；中国锚点→亚洲包络（对齐 `continents.rs:98-107`
   既有断言）；海洋锚点→锚定区本身；三目标收敛单 run 单组。
4. 锁与并发：`operation` 被持有时 `data_sync_run` busy；`sync` 互斥第二请求
   busy；错误消息含 "active" → FFI BUSY 码（对照 `ffi.rs:81-83`）。
5. 任务与取消：`data_sync` 进 `begin_task` 白名单并落历史；取消后无成功
   结果发布（对照 `cancelled_result_is_not_published`，`desktop_runtime.rs:2157-2172`）。
6. 保留联动：成功后 `apply_configured` 生效、失败轮不清理（对照
   `retention_settings.rs:220-228`）。
7. hello 能力与方法白名单：`data_sync` 出现在 capabilities
   （对照 `hello_is_python_free_and_advertises_native_nowcast`，
   `desktop_runtime.rs:1897-1915`）。
8. 状态文件：损坏/超限/缺合同 fail-closed，等效从未同步。

### 13.2 回归门禁（实现后必须实跑，本文档阶段未运行）

- `cargo test`（engine 全量，含既有 `engine/tests/retention_lifecycle.rs`、
  `retention_multiprocess.rs`、`runtime_process.rs`）；
- pytest 桌面/桥接既有套件（`tests/test_desktop_rpc.py` 等）确认 IPC 合同
  无回归；
- macOS `swift build` + 既有 UI 门禁（对齐 RUST_REMAINING_WORK 记录口径）。

## 14. 验收标准（真实数据；fixture 通过不当作生产通过）

| # | 场景 | 通过标准 |
| --- | --- | --- |
| A1 | CAMS 冷同步：启用 Asia 锚点后首个 due 轮 | 恰好 1 个 ADS 任务、1 个组、全部 lead 到位；`asset_downloads=1`、`group_errors/missing_valid_times` 空；记录耗时与体积（对照决定 5 基线 122 MiB/126 s 量级） |
| A2 | 去重·同步先行 | A1 之后立即手动北京点 prepare：0 次 ADS 任务、缓存命中、预测正常（复现广州点 0 任务验收） |
| A3 | 去重·手动先行 | 手动 prepare 进行中触发同步：同步 busy 让路；prepare 完成后下一 tick 同步 `cycle_current` |
| A4 | GFS/EC 同步 | 最新 verified 周期全时效到位；随后手动点预测预检全部 `covering_cache`/命中，且 GFS/EC 单周期体积首次实测记录在案 |
| A5 | 失败与熔断 | 断网/错误 key 3 轮后该源 `suspended`；恢复 key +「立即同步」解锁；期间既有缓存数据与历史报告完好 |
| A6 | 取消 | ADS 等待中取消：任务终态 cancelled、无成功结果发布；已完成文件保留并被下轮复用 |
| A7 | 保留 | CAMS 保留 3 周期下连续同步 ≥4 个 run：磁盘稳定在 ~3 周期量级，清理报告 removed/freed 与实际一致 |
| A8 | 重启与凭据 | 同步进行中杀进程→重开：任务 interrupted、无自动续跑；全量 grep 日志/任务历史/状态文件无 ADS token |

每项记录 `source_id`、引擎版本、真实耗时；上游不可用时如实标「待验收」
（对齐双路线提案第 9 节口径）。

## 15. 风险与边界

1. **ADS 约束波动**：lead 集、run 时钟以探测时刻 ADS 约束为准（`cams_data.rs:288-350`）；
   上游限流/排队期间同步轮可能长达小时级——UI 以任务历史与进度事件呈现，
   不设同步专属超时（避免放宽既有合同，对齐决定 4）。
2. **发布探测 unverified**：探测失败（网络/ADS 异常）时本轮不下载
   （fail-closed），手动 prepare 不受影响。
3. **GFS schema 覆盖匹配**：`find` 对 `request_schema` 一致性有校验
   （`gfs_data.rs:645-649`）；同步 bbox 与后续手动请求 bbox 不同时依赖
   `bbox_covers` 与 schema 形态一致，A4 验收显式覆盖该路径。
4. **EC 体积**：basic bundle 逐时效全局文件，120 h 时效 ≈ 41 文件/周期；
   必须实测（A4），必要时用户自调 `lead_horizon_hours` 或只启 CAMS。
5. **双 run 组边界**：目标钉三时刻收敛单 run 的推导依赖 `choose` 排序
   （`cams_data.rs:316-347`）；若上游同日出现异常 run 集导致两分组，
   `groups=2` 会在结果中显式可见并按组计费，验收 A1 断言 `groups==1`。
6. **应用生命周期**：应用未运行即无同步（1.2 第 5 条）；守护进程化属
   未来新决定。
7. **跨日 lead**：CAMS run+12/24/36h 可能跨 UTC 日，`Availability` 按
   `day+clock` 分组天然支持（`cams_data.rs:745-754`）；验收覆盖
   12Z run 跨日场景。

## 16. 决策点汇总（需用户拍板）

1. **跨轮失败熔断阈值与解锁方式**：提案「连续 3 轮失败挂起该源，立即同步
   或重存设置解锁」；也可改为纯间隔重试（无熔断）或别的阈值。
2. **retention 未配置时的强度**：提案软警告（同步照跑）；备选硬性要求
   （未配置任一保留则拒绝启用同步）。
3. **GFS/EC 时效范围交互**：提案启用 gfs/ec 时必填 `lead_horizon_hours`
   （3..240，步长 3）；备选固定 120 h 不暴露配置。
4. **锚定区域语义**：提案单区域（`REGIONS` 之一）驱动 CAMS 包络与
   GFS/EC bbox；备选允许多区域叠加（并集包络，下载体积显著增大）。
5. **tick 粒度**：提案 60 秒 UI tick + 30..1440 分钟用户间隔；备选引擎内
   调度线程（见 3.1 取舍表，不推荐）。
6. **手动「立即同步」是否绕过熔断**：提案绕过（作为人工解锁通道）；
   备选仅查看不允许手动触发。
