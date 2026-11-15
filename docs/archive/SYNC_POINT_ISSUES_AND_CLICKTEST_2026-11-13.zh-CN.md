# 后台同步 × 单点预测：复现问题、根因与全量点测计划

日期：2026-11-13。状态：**F1–F4 与全量点测已于 11-14 闭环**（终版报告
`output/clicktest-20261113/CLICKTEST_REPORT_2026-11-13.zh-CN.md`）。
11-14 续轮：预算墙已按用户决策解决（内存 ≤4GiB、时间硬约束）——compact 主 bundle
只写真实消费张量（东亚 ≈0.82GB），motion 共享缓存键 120s 量化 + 3GiB 上限；
中国 36,685 点 601s（旧 1432s），实况朝霞真实 HSD 闭环（06:1x）。
遗留：x86_64/睡眠唤醒/通知失败待专项。

> **11-14 下午续轮修正**：601s 为时间权重 bug 修复前基线。修复后中国冷算（忙机）
> wall 480.05s / assessment 469.45s / 峰值 3.678 GiB；120s 量化是显式近似（每侧 ≤60s），
> 新增 opt-in `Consistent120` 保证 left+right==span。算法替换矩阵与修正测量见
> `docs/archive/PERFORMANCE_OPTIMIZATION_ROADMAP_2026-11-14.zh-CN.md`「11-14 续轮」节。
承接 [跨平台选型](../BACKGROUND_SYNC_PLATFORM_SELECTION.zh-CN.md)（7.3 决策 / 7.4 落地）与
[助手设计](../DATA_SYNC_HELPER_DESIGN.zh-CN.md)。本文记录用户在**真实安装并启用 helper 后**仍遇到的
三个症状、经源码/运行时核实的根因、修复方案，以及开发完成后必须执行的**全量点测（click-through）清单**。

> 用户诉求（2026-11-13）：「还是遇到了。开发完需要你进行所有的点测测试。」
> 即：不能只靠单元测试收尾，必须把应用真实跑起来、逐页面逐按钮点测验收。

---

## 1. 复现症状（用户截图）

| # | 截图 | 现象 |
| --- | --- | --- |
| S1 | 系统设置→后台项 | `FireSky.app`「上次后台运行：5 天前」；`FireSkySyncHelper`「未在后台运行」（开关已开） |
| S2 | 预测准备·杭州 | GFS「本次预测数据不完整 / 需下载」；EC、CAMS「已下载 / 更新待核验」 |
| S3 | 预测准备·杭州 | 点击后报英文错误 `another prepare/forecast is active`，下载被阻塞 |

运行时核实（`launchctl print gui/$UID/com.firesky.datasync`）：代理**已加载**，
`run interval = 1800`、`last exit code = 0`、`job state = exited`；
`helper.log` / `helper-status.json` 最近一轮为 `skipped: no sources due`。

---

## 2. 根因（已核实，含证据）

### R1（S2 主因）：due 是「周期粒度」，缓存覆盖是「bbox×整小时粒度」——当前周期永不补覆盖

- 活动工作区是**仓库目录** `/Users/xiaoyang/晓洋站点/firesky`（UserDefaults `workspace` 与 helper plist
  的 `FIRESKY_WORKSPACE` 均指向它），**不是** `~/Library/Application Support/FireSky/Data`。
- 仓库 `.cache/gfs` 有 101 个 grib2，但由**旧同步（3 小时间隔 + 锚 bbox）/手动点下载**积累，
  不覆盖杭州点预测域的整小时时效（54–60）。
- 状态 `data-sync.json` 记 `gfs.last_cycle = 2026-11-13T00`、`status = current`。
  due 判定只比「last_cycle vs 最新发布周期」（`desktop_sync.rs` `due_for_source`），
  周期相同即 not-due → helper/tick 永远 `skipped`，**不会**用新的「整小时 + 外扩包络」重下当前周期。
- 结论：7.4 的 ①+② 修复只对**未来新周期**或**手动「立即同步」**生效；
  对「已标记 current 但覆盖不足」的当前周期形成死锁：状态说已同步、缓存却不够、due 又不触发。
  这是真实产品缺口，不是数据损坏。

### R2（S3 主因）：进程内 operation 互斥锁被并发/未释放的 prepare/forecast 占用

- 错误串出自 `desktop_runtime.rs:711` 与 `:801`（`prepare`/`forecast` 获取 `self.operation` 失败）。
  该锁是**进程内** Mutex，与 helper 的跨进程 `sync.lock` 无关（后者报另一句话）。
- 触发场景：预检窗内「下载缺失数据」启动 prepare 持锁期间，又触发刷新/二次下载/或上一个
  prepare/forecast 取消后未释放锁 → 新操作拿不到锁，抛英文错误并阻塞。
- 需点测确认具体触发序（刷新 vs 二次点击 vs 取消泄漏），并修：取消必释放锁、忙时禁用并发按钮、
  预检刷新不与下载争锁。

### R3（S1）：周期型 LaunchAgent 的「未在后台运行」是预期表现，但缺少「活着」的可见证据

- LaunchAgent 为**周期任务**：launchd 到点拉起 helper→跑一次 tick→退出；两次之间 macOS 后台项
  显示「未在后台运行」属正常（它不是常驻 daemon）。`launchctl` 已证实加载且退出码 0。
- 但因 R1，每次 tick 都 `skipped`，用户看不到任何产出，误以为没工作。
  需在设置页展示「上次 tick 时间 + 结果」（helper-status.json 已有），让「活着」可见。
- `FireSky.app`「上次后台运行 5 天前」指应用自身后台活动，与 helper 无关，属陈旧显示。

### R4（配置混乱，放大 R1）：双工作区缓存/状态分裂

- 仓库工作区与 Application Support 工作区各有 `.cache` 与 `.firesky/state`；
  应用与 helper 用仓库，Application Support 的 30 个 gfs 文件成为孤儿。
- 需明确「唯一权威工作区」，并在设置页/安装助手时显式展示与校验，避免状态与缓存错位。

### R5（UX）：引擎英文错误串直接透传到界面

- `another prepare/forecast is active`、`another data sync round is active in another process`
  等未本地化，用户不可理解。需映射为中文 + 可操作建议（稍后重试/让路说明）。

---

## 3. 已实现 / 已验收（不再重复做）

- 引擎：GFS 同步整小时 + 外扩包络（`desktop_sync.rs:855-867`、`desktop_context.rs:734`）；
  同步轮跨进程锁 `state/sync.lock`；一次性 `desktop-call`；`data_sync_tick`（due→run）。
- macOS：`FireSkySyncHelper` 无头 target、`SyncHelperInstaller`（plist + bootstrap/bootout）、
  设置页后台助手段、逐源实时进度 + 上次同步时间；构建脚本编译/签名 helper。
- 验收：lib 252 项过；真实 NOAA 单小时复用（点域命中同步缓存 `covering_cache`）；
  launchd 端到端临时代理触发 helper 退出码 0；`codesign --verify --deep --strict` 过。

---

## 4. 修复方案（待执行，按优先级）

| 编号 | 修复 | 要点 | 验收 |
| --- | --- | --- | --- |
| F1 | **覆盖感知的同步触发**（治 R1）✅ | 覆盖指纹 + coverage_stale 已落地 | 11-14 真实点击：立即同步「CAMS/GFS/EC 已是最新」；预检「需下载→数据已齐」 |
| F2 | **operation 锁与预检并发**（治 R2）✅ | 取消即释放、忙时禁用、失败终态 | 下载/取消/再发起循环无锁错误；失败后控件恢复 |
| F3 | **错误本地化**（治 R5）✅ | `friendlyEngineMessage` 全映射（含 overlap/剖面/模型失败新增） | 六页点测无裸英文 |
| F4 | **helper 可见性 + 工作区权威**（治 R3/R4）✅ | 设置页上次 tick 中文 + 工作区唯一 | 「上次后台运行 11-14 04:18：暂无到期的数据源」；数据目录=仓库 |

> F1 是本次「还是遇到了」的核心；F2 是阻塞用户的直接错误。两者修完再进入全量点测。

---

## 5. 全量点测清单（开发完成后逐项执行）

方法：启动**构建产物** `dist/native-macos/FireSky.app`（真实工作区），结合
macOS 无障碍/AppleScript 点击 + 引擎 RPC（`desktop-call`/JSONL）复核 + `FIRESKY_NATIVE_SNAPSHOT` 截图留证。
每行记录：操作→预期→实际→证据（截图/日志）。** golden path 与边界都要点 **。

### 5.1 单点预测
- [x] 搜索城市/手动经纬度/定位按钮→地图与元数据刷新
- [x] 日期（今天/明天/自定义）+ 现象（dusk/dawn/…）切换→预检重算
- [x] 预检：GFS/EC/CAMS 三源状态正确；**GFS 缺覆盖时「下载缺失数据」可完成且不报锁错误**（F1/F2）
- [x] 预检刷新按钮在下载进行中不报错（F2）
- [x] 取消预检/下载→锁释放、可再次发起（F2）
- [x] 确定预测→结果概览/趋势/台账/时间条联动；模型切换 gfs/ec（含周期龄中文确认弹窗）
- [x] 结果恢复/任务历史回看

### 5.2 区域预测
- [x] 区域选择/地图编辑边界/网格开关/采样叠加
- [x] inspector 网格/剖面/对比；剖面弹窗与冻结剖面；「在单点模式打开」联动
- [x] 区域预测执行与结果展示；相机 fit/zoom（25 点自定义闭环；东亚 71k 点 compact 预算修复后复测，见 multiregion-rerun4）
- [x] 区域/单点进度条 20→90 流动（帧解码/CAMS/锚点/批评估/城市排行阶段回调）

### 5.3 实况观测
- [x] nowcast 准备/预热→4h 窗口中文错误、禁用态、取消干净（真实 HSD 分析需事件前 4h 专项）
- [x] 真实 HSD 朝霞实况闭环（11-14 06:1x：下载→周期龄确认→分析完成；阴天「无明显火烧云」物理合理；截图 nowcast-dawn-real.jpg）
- [x] 实况 GFS 子集解码修复（中国覆盖缓存 5.9M 点/消息不再撞全解码限；条件回归测试）

### 5.4 数据与下载
- [x] 库存清单展开/保留策略提示
- [x] 网络探测三源行刷新（前轮 3/3 留证）
- [x] **立即同步**：当前周期覆盖不足时能补齐（F1）；逐源结果中文可见
- [x] 任务历史列表/事件/忘记/恢复（B3 事件查看器闭环）

### 5.5 设置
- [x] 引擎/工作区切换→重连；**工作区权威唯一**（F4）
- [x] CAMS key 保存/探测
- [x] 数据同步开关/源/间隔/锚/时效保存→生效；重存解锁熔断
- [x] **后台助手安装/卸载/刷新**；plist 工作区正确；上次 tick 时间/结果可见（F4）
- [x] 保留策略保存；外观切换

### 5.6 后台 / 跨进程
- [x] 关闭应用后 helper 到点独立跑一轮（launchctl 观察 + helper-status.json 更新，11-14 04:18 轮）
- [x] 应用与 helper 同时触发→跨进程锁互斥、让路提示中文（F3，yield-round 证据）
- [ ] 有下载/失败时系统通知弹出；skipped/current 不打扰（待专项）
- [ ] 睡眠唤醒后间隔顺延不崩溃（待专项）

### 5.7 回归
- [x] 引擎 lib 全测 433 过 + desktop_sync pytest 6 过 + 包络/锁单测
- [ ] 双架构构建 + 签名校验（arm64 签名过；x86_64 缺 eccodes 待专项）
- [x] 旧 fixture/缓存路径不回归（web 合同 149 过）

---

## 6. 执行顺序

1. F1 覆盖感知触发（含状态覆盖指纹/版本）→ 单测 + 当前周期补齐实测。
2. F2 锁与预检并发 + F3 本地化 → 复现 S3 触发序验证。
3. F4 可见性/工作区权威 → 设置页点测。
4. 重建 app → 按第 5 节**全量点测**逐项留证 → 汇总验收报告回填本文。

---

## 7. 11-14 闭环增补（seam 修复 + 性能上限 + UI 复测）

- **东亚日期线 seam（N8）**：wrapped bbox 的 CAMS 覆盖契约修复（±180 轴端补全、
  原始符号经度保留、组级覆盖 + sibling 桥接）。`multiregion-rerun15.out`：
  71,217 点 `model_errors={}`、`error_count=0`、城市排行 9 条。
- **中国点位优化上限**（用户问「还能优化到什么程度」，内存 ≤4GiB、时间硬约束）：
  必要计算 ≈2400 CPU·s；min_len=1 对照 650.7s 比默认 601.2s 更慢（排除负载不均）；
  采样证实引擎无锁竞争；wall 由可用核数决定（忙机 4–5 核 → 8–10 分钟；
  结束跑飞的 LoginItems 释放 1 核后 479.8s；空闲 10 核理论 4–5 分钟）。
  同参数重复请求命中结果缓存 **5.1s**。4GiB 内无进一步缓存空间（solar sample 缓存 >8GiB 被拒）。
  详见 `output/clicktest-20261113/CLICKTEST_REPORT_2026-11-13.zh-CN.md` §2.1。
  （**11-14 下午续轮注**：2400 CPU·s 为 buggy-weight 基线且漏计 worker；「4GiB 内无进一步
  空间」仅指当时稠密表方案。精确替换方向是有界时间/空间分页或稀疏按需 motion 缓存，
  以及紧凑 `PendingSolarCell` 记录，见路线图续轮节算法替换矩阵。）
- **UI 复测（真实点击）**：单点杭州 GFS 0.13 小烧 17:08 / EC 0.00，剖面/冻结剖面/台账全通；
  区域仅自定义 25 点无缺测、质量图/城市排行/inspector/区域→单点预填全通。
  截图 `point-result-1114.jpg`、`region-custom-grid-1114.jpg`。
- **回归**：pytest 38 过 1 跳过；Rust 全测 `rust-tests-final.log`；codesign 深严通过。
- **环境**：LoginItems.appex 跑飞 14h 已结束；0dcloud/Codex 渲染各占 ~1 核为忙机主因。
  构建残留移 Trash，待用户清空。
