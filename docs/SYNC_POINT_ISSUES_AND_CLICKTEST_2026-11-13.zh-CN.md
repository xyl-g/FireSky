# 后台同步 × 单点预测：复现问题、根因与全量点测计划

日期：2026-11-13。状态：**问题已复现并定位根因，修复与全量点测待执行**。
承接 [跨平台选型](BACKGROUND_SYNC_PLATFORM_SELECTION.zh-CN.md)（7.3 决策 / 7.4 落地）与
[助手设计](DATA_SYNC_HELPER_DESIGN.zh-CN.md)。本文记录用户在**真实安装并启用 helper 后**仍遇到的
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
| F1 | **覆盖感知的同步触发**（治 R1） | 状态增加「覆盖指纹」（bbox+hours+schema+coverage_version）；due 或预检发现覆盖不足时，对**当前周期**补同步缺失整小时/点域；或 bump coverage_version 使旧 current 状态失效重评 | 当前周期标记 current 但点域缺 54–60 时，tick/立即同步能补齐；补齐后杭州预检 GFS ready |
| F2 | **operation 锁与预检并发**（治 R2） | 取消必释放锁；预检刷新不与下载争锁；忙时禁用「下载/确定/刷新」并发入口；锁冲突改为排队或友好提示 | 点测 S3 触发序不再报英文锁错误；下载可完成 |
| F3 | **错误本地化**（治 R5） | 引擎锁/让路错误映射中文 + 建议；UI 不透传裸英文 | 界面不再出现裸英文错误串 |
| F4 | **helper 可见性 + 工作区权威**（治 R3/R4） | 设置页显示上次 tick 时间/结果/下次间隔；安装助手时展示并校验工作区；说明周期代理「未在后台运行」属正常 | 用户能确认 helper 活着；工作区唯一且与缓存一致 |

> F1 是本次「还是遇到了」的核心；F2 是阻塞用户的直接错误。两者修完再进入全量点测。

---

## 5. 全量点测清单（开发完成后逐项执行）

方法：启动**构建产物** `dist/native-macos/FireSky.app`（真实工作区），结合
macOS 无障碍/AppleScript 点击 + 引擎 RPC（`desktop-call`/JSONL）复核 + `FIRESKY_NATIVE_SNAPSHOT` 截图留证。
每行记录：操作→预期→实际→证据（截图/日志）。** golden path 与边界都要点 **。

### 5.1 单点预测
- [ ] 搜索城市/手动经纬度/定位按钮→地图与元数据刷新
- [ ] 日期（今天/明天/自定义）+ 现象（dusk/dawn/…）切换→预检重算
- [ ] 预检：GFS/EC/CAMS 三源状态正确；**GFS 缺覆盖时「下载缺失数据」可完成且不报锁错误**（F1/F2）
- [ ] 预检刷新按钮在下载进行中不报错（F2）
- [ ] 取消预检/下载→锁释放、可再次发起（F2）
- [ ] 确定预测→结果概览/趋势/台账/时间条联动；模型切换 gfs/ec
- [ ] 结果恢复/任务历史回看

### 5.2 区域预测
- [ ] 区域选择/地图编辑边界/网格开关/采样叠加
- [ ]  inspector 网格/剖面/对比；剖面弹窗与冻结剖面
- [ ] 区域预测执行与结果展示；相机 fit/zoom

### 5.3 实况观测
- [ ] nowcast 准备/卫星图切换/剖面步进
- [ ] 卫星实况与预报叠加一致性

### 5.4 数据与下载
- [ ] 库存清单展开/保留策略提示
- [ ] 网络探测三源行刷新
- [ ] **立即同步**：当前周期覆盖不足时能补齐（F1）；逐源实时进度条可见
- [ ] 任务历史列表/事件/忘记/恢复

### 5.5 设置
- [ ] 引擎/工作区切换→重连；**工作区权威唯一**（F4）
- [ ] CAMS key 保存/探测
- [ ] 数据同步开关/源/间隔/锚/时效保存→生效；重存解锁熔断
- [ ] **后台助手安装/卸载/刷新**；plist 工作区正确；上次 tick 时间/结果可见（F4）
- [ ] 保留策略保存；外观切换

### 5.6 后台 / 跨进程
- [ ] 关闭应用后 helper 到点独立跑一轮（launchctl 观察 + helper-status.json 更新）
- [ ] 应用与 helper 同时触发→跨进程锁互斥、让路提示中文（F3）
- [ ] 有下载/失败时系统通知弹出；skipped/current 不打扰
- [ ] 睡眠唤醒后间隔顺延不崩溃

### 5.7 回归
- [ ] 引擎 lib 全测 + desktop_sync 23 项 + 包络/锁单测
- [ ] 双架构构建 + 签名校验
- [ ] 旧 fixture/缓存路径不回归

---

## 6. 执行顺序

1. F1 覆盖感知触发（含状态覆盖指纹/版本）→ 单测 + 当前周期补齐实测。
2. F2 锁与预检并发 + F3 本地化 → 复现 S3 触发序验证。
3. F4 可见性/工作区权威 → 设置页点测。
4. 重建 app → 按第 5 节**全量点测**逐项留证 → 汇总验收报告回填本文。