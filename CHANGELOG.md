# 更新日志（Changelog）

产品版本遵循语义化版本（SemVer）。v1.0.0 起产品版本与 Rust 引擎
`engine/Cargo.toml` 的 `version` 一致；旧 Python 栈（`src/firesky/version.py`）
已于 2026-11-13 退役删除。

English: product releases follow SemVer. Since v1.0.0 the product version
matches the Rust engine crate version (`engine/Cargo.toml`); the legacy
Python stack (`src/firesky/version.py`) was retired on 2026-11-13.

## Unreleased（Windows 对齐，随下一版本发布）

Windows 成正式发布目标：UI 对齐至 v1.5.0 macOS 基准（v1.2.0 信息架构 +
v1.3.0 手动删除/地形下载 + v1.5.0 应用内更新）+ 发布能力（zip + 安装器 +
CI 直发）。
Windows becomes an official release target: the client aligns with the
v1.5.0 macOS baseline (v1.2.0 information architecture plus the v1.3.0
manual deletion and terrain download and the v1.5.0 in-app update) and
ships release capability (self-contained zip, installer, CI publishing).

**Windows UI 对齐 / Windows UI alignment**

- 信息架构对齐 v1.2.0：任务历史独立页（列表 + 详情双栏、全部/主动任务/
  后台同步筛选、本地时区显示、事件时间线）、设置四分类（通用/数据/
  位置与海拔/高级，切类保留草稿）、数据管理瘦身（探测与同步迁入设置 → 数据）。
  The Windows client matches the v1.2.0 information architecture:
  standalone Task History with list/detail split and filters, four-category
  Settings with per-category drafts, and the slimmed Data Management page.
- v1.3.0 对齐：数据管理页每周期与每数据源删除入口
  （`data_delete_preview`/`data_delete_execute`，确认框展示范围/文件数/预计
  释放，执行后报告已删/跳过/失败并刷新库存）；数据管理页「地形 SRTM」面板
  （`terrain_inventory` 点状态与区域估算确认 → `terrain_download` 区域/全球/
  点级下载，批量进度含总体百分比/已下载/缺失/失败/当前瓦片，可取消续传）。
  v1.3.0 alignment: per-cycle and per-source delete entries on the data page
  with a scoped confirmation dialog and result summary, plus the terrain
  panel with region-preset estimate confirmation, bulk/point downloads and
  aggregate progress.
- v1.5.0 对齐：设置 → 通用「软件更新」（检查更新、发布说明折叠、下载进度
  与取消、启动静默检查默认开可关）；下载完成后执行 SHA256SUMS.txt 校验，
  解压暂存后由批处理自替换应用目录并自动重启；无写权限时给出手动安装
  指引；发现新版本时侧栏设置入口显示绿色徽标。版本比较/资产名匹配/架构
  探测/Release 解析/校验和行解析为纯函数（与 macOS UpdaterChecks 同口径）。
  v1.5.0 alignment: Settings → General "Software update" with check, release
  notes, download progress and an auto-check toggle; after download the
  SHA256SUMS checksum is verified and the zip is staged and swapped in by a
  batch script with automatic relaunch, or an explanatory manual-install
  hint when the install directory is not writable; a green badge appears on
  the sidebar Settings entry. Version comparison, asset matching,
  architecture detection, release parsing and checksum-row parsing are pure
  functions mirroring the macOS UpdaterChecks fixture.
- 点预测深度：GFS/EC 双摘要卡、分组指标卡（双模式并列）、AOD 环境读数
  来源摘要、评分组成面板、对比曲线（缺测断线 + 悬停读数 + 最佳标记）、
  15 列逐时台账、太阳方向剖面图层、点冻结剖面查询、诊断分组。
  Point forecasting gains dual-model hero cards, grouped metric cards,
  AOD provenance, the read-only score-composition panel, comparison curves
  with gaps and hover readouts, the 15-column ledger, solar-path cross
  sections, frozen-profile queries and grouped diagnostics.
- 实况：卫星帧 hero、15 项指标卡、卫星后验概率剖面热图（unknown 琥珀）、
  冻结卫星剖面查询、不可用原因透传。
  Nowcast gains the satellite-frame hero, metric cards, the posterior
  probability profile heatmap with amber unknown cells, frozen satellite
  profile queries and explicit unavailability reasons.
- 区域：hello 预设区域多选 + 自定义范围、地图拖拽绘制（跨 180° 短弧）、
  固定 0–2.5 色带与缺测图例、采样点标注、网格/城市详情与「在单点模式打开」。
  Regional forecasting adds preset regions, drag-drawn bounds with
  antimeridian short-arc semantics, the fixed palette with missing legend,
  sample annotations, cell details and open-in-point.
- 契约与 UX：`metadata`/`payload` 透传 `elevation_source` + `ecmwf_source`、
  周期龄确认（`cycle_age_confirmed`）、任务运行不锁导航、搜索/定位轻量化、
  结果-参数 stale 徽标 + 恢复回填原始参数、错误中文映射与可复制、
  CAMS 凭据 / EC 端点 / 下载连通性探测 / 后台同步（data_sync_* + 60s 心跳）、
  快捷键 Ctrl+1-5 / Ctrl+,、收藏、今天/明天日期、会话持久化、退出确认。
  Contracts and UX: elevation/EC source passthrough, cycle-age confirmation,
  non-blocking navigation during tasks, stale-parameter banner with restore
  backfill, Chinese error mapping, CAMS credentials, EC endpoint and probe
  settings, background sync with a 60-second heartbeat, shortcuts,
  favorites, date tabs, session persistence and exit confirmation.

**Windows 发布能力 / Windows release capability**

- `scripts/build_windows_native.ps1`：单一路径完成引擎/WinUI 构建、载荷校验、
  干净暂存与版本化 zip + SHA256；`FireSkyInstaller.iss` +
  `scripts/build_windows_installer.ps1` 提供 Inno Setup 安装器（卸载保留用户
  数据，WebView2 检测提示）。
  One packaging path builds, verifies, stages and zips the release; an
  Inno Setup installer adds shortcuts and an uninstaller while keeping user
  data.
- CI 恢复 `windows-native`（x64 + ARM64 原生 runner）并在 `v*` tag 时把
  Windows zip / setup 与 macOS DMG 一起直发 GitHub Release（含 SHA256SUMS）。
  CI restores the windows-native matrix and publishes Windows assets on
  version tags alongside the macOS DMGs.
- map.html（Windows 地图文档）：固定色带、缺测灰与图例、区域拖拽绘制、
  采样点标注与网格/采样开关。
  The shared map document gains the fixed palette, missing-data legend,
  drag-drawn bounds and sample annotations.
- 引擎 Windows 修复：首次安装的全新工作区上 inventory/check 报
  「函数不正确。(os error 1)」——`desktop_inventory::resolved()` 逐组件
  canonicalize 会碰到裸 verbatim 盘根（`\\?\C:`）的
  ERROR_INVALID_FUNCTION；修复为仅对真实层级做归一化并容忍 error 1，
  附 Windows 回归测试。应用版本与 `engine/Cargo.toml` 同源注入程序集
  （应用内更新的版本比较依据）。
  Engine fix for Windows: on a fresh workspace the verbatim (`\\?\`) drive
  root made per-component canonicalize fail with ERROR_INVALID_FUNCTION,
  breaking inventory/check before any download; normalized only real path
  levels and tolerated error 1, with a Windows regression test. The app
  assembly now embeds the engine version for in-app update comparison.
> 发布说明：v1.9.5 / v1.9.6 因 mobile-release CI 的 Android 交叉工具链
> 问题未产出发布物（仓库 .cargo/config.toml 携带 darwin NDK 绝对路径，
> ubuntu runner 失败）；修复后三个版本随 **v1.9.7** 一并发布——即本版
> 资产包含 v1.9.5（i18n + 任务详情）与 v1.9.6（数据同步专项）的全部内容。
> Release note: v1.9.5 / v1.9.6 shipped inside **v1.9.7** after a CI
> toolchain fix (portable Android cross paths); this release therefore
> contains everything from both.

## v1.9.8 (2026-11-21)

卡死治理 + 排队透明化 + helper 一致性校验 + Windows 动态文案 i18n。
Stall governance, queue transparency, helper consistency check, and
Windows dynamic-string i18n.

**引擎 / Engine**

- 空闲看门狗：check/prepare/forecast/terrain_download/data_sync 等长任务
  超过 15 分钟没有任何进度事件即自动取消并转明确可重试错误——引擎或
  网络真卡死时 UI 不再永久 busy（退出守卫反复弹窗与界面假死的根因）。
- ADS 队列/读取事件化：排队等待（状态 + 已等待分钟数）与资产分片读取
  （每 32 MiB）持续外发进度，CAMS 排队与慢链路大资产不再是无事件黑箱，
  同时维持看门狗心跳；「慢而活跃」不会被误杀。
  Engine: idle watchdog converts true stalls into retryable failures;
  ADS queue-wait and chunked-read events keep long phases observable.

**桌面 / 客户端**

- macOS 后台助手一致性校验：已安装 plist 烘焙的工作区与当前数据目录
  不一致时在设置页显示警示并引导重装（历史 bug：陈旧路径在别的目录
  全源同步）。
- Windows 动态文案 i18n：553 处界面字符串接入 L10n.T()（状态键与引擎
  参数保持中文原文，仅显示位翻译）；语言切换经视觉树遍历双向幂等。
  macOS helper workspace-mismatch warning; 553 Windows UI strings
  localized while state keys and engine parameters stay untouched.

## v1.9.7 (2026-11-21)

进度与吞吐透明化（prepare 逐文件进度 / ADS 断点续传 / 欠账可视化 / MB/s 吞吐 / EC index 复用 / 手动优先让路）+ 三端 i18n 收尾 + 任务详情打开报告 + web uPlot 试点合并。
Progress & throughput transparency (per-file prepare progress, ADS range
resume, missing-cycle visibility, MB/s throughput, EC index reuse,
manual-priority yield) plus i18n wrap-up on three platforms, report access
from task details, and the web uPlot pilot merge.

**引擎 / Engine**

- prepare 阶段逐文件进度：GFS 8–35% / EC 35–55% / CAMS 55–90% 段内按文件
  细分（替换固定 10% 占位），EC 下载改用与同步轮同款 4 worker 线程池；
  事件带 bytes/elapsed_ms 供吞吐显示。
- ADS 断点续传：资产下载重试不再清空临时文件，从断点 `Range: bytes=N-`
  续传（206 校验 Content-Range 起点；服务端忽略 Range 回 200 时退回全量
  重下），几百 MB 资产失败重试不再从头下载。
- 同步轮/准备阶段进度消息内联实时吞吐（`… · 8.4 MB/s`），事件携带
  bytes/elapsed_ms 机读字段。
- 每源同步状态新增 `missing_cycles` 字段（缺失周期最多 8 个），欠账一眼
  可见。
- 手动优先让路：prepare/forecast 开始时放置跨进程哨兵（1s 重放保持），
  后台同步轮在源边界与文件粒度自动让路（cancelled 不计熔断，崩溃自愈）。
- EC index 跨会话缓存（URL→记录，已发布周期 index 不可变），消除并行
  逐小时下载的 index 重复请求。
  Engine: per-file prepare progress with phase spans, ADS range resume,
  inline MB/s throughput in progress events, missing_cycles field,
  manual-priority yield sentinel for background sync rounds, and a
  cross-session EC index cache.

**桌面 / 移动端**

- macOS 设置/数据页全量 i18n：集中英译表（与 Android values-en 同口径）
  + 94 处显示位 + 22 处格式化模板（t()/tf()），语言来回切换幂等。
- iOS 设置页数据/高级分类接入同源字典（t()/tf()）。
- Windows 界面语言：中心字典 + 视觉树遍历替换（zh↔en 幂等），设置 →
  通用新增语言选择（跟随系统/中文/English，LocalSettings 持久化）。
- macOS 任务详情卡新增「完整报告」入口：直接打开该次预测的已发布
  HTML 报告（html_report_path 契约）。
  macOS settings/data pages fully localized; iOS data/advanced settings
  share the same dictionary; Windows gains interface-language selection
  with a visual-tree applier; macOS task details can open the published
  HTML report.

**macOS 退出守卫修复 / Quit-guard fix**

- 修复退出确认框循环：用户选择「仍要退出」后置位强制标志，后续
  terminate 直接放行；注销/关机等系统反复发起 terminate 时 5s 内不再
  重复弹确认。新增启动兜底：8s 后仍无可见窗口时强制前置主窗口，消除
  「界面没出来」的卡死观感。
  Quit confirmation can no longer loop: explicit exit sets a force flag,
  system-initiated repeated termination is re-confirmed at most once per
  5s, and a launch watchdog brings the main window forward if no window
  is visible 8s after launch.

**Web**

- 合并 feat/web-ui-ux-polish 余下 3 提交：uPlot 试点（WebTrendChart
  两处 SVG 换 canvas 图表）+ Playwright 视觉基线脚手架。视觉基线快照
  为本机测试数据，**不再入库**（用户裁定）：`tests/**/__screenshots__/`
  加入 .gitignore，本地/CI 用 `--update-snapshots=missing` 现场生成。
  Merges the remaining web-ui-ux-polish commits (uPlot canvas pilot,
  visual baseline scaffolding). Snapshot baselines are local test data
  and stay out of the repo (user decision); they are gitignored and
  generated on demand.

## v1.9.6 (2026-11-21)

数据同步专项修复：ADS 大资产自适应超时 + 失败欠账自动补齐 + GFS/EC 并发下载与逐文件进度 + 同步页实时刷新。来源为专项走查（CAM 撞 120s 总超时熔断、失败后 24h+ 不重试、GFS 串行 22 min、杂散 helper 抢带宽）。
Data-sync hardening round: adaptive ADS asset timeouts, automatic retry
after failed rounds, concurrent GFS/EC downloads with per-file progress,
and live sync status refresh.

**引擎 / Engine**

- ADS 资产下载总时限按体积自适应（`asset_transfer_budget`）：旧实现固定
  `min(remaining, 120s)` 覆盖整个响应体读取，慢链路下几百 MB 的整周期
  CAMS 资产必然读不完，3 次重试全部撞同一上限后熔断（`ADS asset response
  read failed`）。现按保守吞吐 256 KiB/s 折算下限（512 MiB ≈ 34 min），
  仍受 remaining 预算封顶。
- 失败欠账自动补齐：上一轮真实下载失败且间隔已过后，即使发布探测暂未
  核验也再次尝试（`retry_after_failure`）；ADS 恢复可达后自动补齐失败
  周期，无需手动干预。探测失败（未消耗间隔）带 `probe_only` 标记不触发
  重试，避免上游不可达时 60s tick 空转放大；熔断语义不变。
- GFS/EC 并发下载（4 worker）+ 源内逐文件进度：每目标文件已有独立
  lock，并发安全；GFS 121 文件串行 ≈ 22 min → 4 并发 ≈ 6 min，EC
  41 文件 ≈ 32 min → ≈ 8 min。轮进度把本源百分比区间细分到文件粒度，
  长下载期间 UI 不再停在源起点不动。
- 修复地形下载历史白名单不同步已在 v1.9.5 交付；本轮新增 download_pool
  与超时预算的回归测试。
  Engine: adaptive ADS asset budgets, retry-after-failure (probe failures
  excluded via a probe_only marker), 4-worker concurrent GFS/EC downloads
  with per-file progress blended into the round percent.

**桌面（macOS）/ Desktop**

- 同步状态每 15s 自动刷新（设置页可见且引擎空闲时）：后台轮次、CAMS
  补齐、熔断状态变化不再依赖手动刷新；每源状态行新增上一轮实际耗时
  （elapsed_ms）。
- 应用启动时清理其它工作区的杂散 helper 轮：陈旧 launchd plist 指向旧
  workspace（如 /tmp 测试目录）时，杂散轮会以全源设置在错误工作区长期
  同步并与真实轮并行抢带宽；正式 helper 被终结后由 launchd 自动拉起。
- 说明：引擎 reqwest 已启用 `macos-system-configuration`，原生跟随系统
  代理（Clash 等），无需额外注入代理环境变量。
  Desktop: 15s live refresh for sync/helper status, per-round elapsed
  display, stale cross-workspace helper cleanup at startup.

## v1.9.5 (2026-11-21)

界面多语言 i18n（三端）+ 任务历史可追溯预测详情 + 引擎修复 + Windows token 补齐。
UI i18n across three platforms, task history tracing to forecast details,
engine fixes, and Windows token completion.

**界面多语言 / UI i18n**

- macOS 新增 L10n 轻量键值查找：语言偏好（跟随系统/中文/English）持久化
  到 UserDefaults，未覆盖的 key 回落中文原文；导航、任务历史整页、
  开始预测等高频字符串已接入（覆盖渐进扩展）。
- Android 全量接入 `stringResource`：`values/strings.xml`（中文默认）+
  `values-en/strings.xml`，覆盖工作台/结果卡片/历史/数据管理/设置全部
  页面（含引擎中文状态映射）；设置 → 通用新增「语言」选择，切换后重建
  立即生效。
- iOS L10n 同构接入：底部导航/工作台/准备面板/历史页/设置分类 +
  「语言」选择（`@AppStorage` 驱动整树刷新）。
- Windows 文案接入在后续版本跟进；新增 key 口径与 DESIGN.md
  「跨端文案逐字一致」一致。
  macOS gains a lightweight L10n lookup with task-history wiring; Android
  adopts string resources across all four pages with an English variant
  and a language picker; iOS mirrors the same L10n with an app-storage
  driven language picker. Windows follows in a later release.

**任务详情 → 预测详情 / Task history traces forecast details**

- 任务历史页选中已完成的预测任务时，经 `result_restore` 读取不可变结果
  快照（引擎侧 SHA256 校验，fail-closed），直接呈现该次预测的关键信息：
  双模型峰值指数/档位/最佳时刻/推荐窗口 + 结果摘要；快照过期或清理时
  给出可读说明。macOS 详情区、Android/iOS 任务详情卡同构实现；快照内容
  不可变，会话内按 task_id 缓存。
  Selecting a completed forecast task now reads its immutable result
  snapshot (SHA256-verified) and shows the key numbers and summary inline;
  expired snapshots render a readable notice.

**引擎修复 / Engine fixes**

- 修复地形区域下载被任务历史白名单拒绝（`method does not create
  workflow history`）：`desktop_runtime::begin_task` 与
  `TaskHistory::begin` 两份白名单不同步——runtime 侧已放行
  `terrain_download`、历史侧仍拒绝，GUI 地形卡报错且任务历史缺失该类
  任务；已同步白名单并加回归测试。
  Terrain region downloads were rejected by the task-history whitelist
  (the runtime side allowed `terrain_download` while the history side did
  not); the whitelists are now in sync with a regression test.

**Windows token 补齐 / Windows token completion**

- App.xaml 补齐 DESIGN.md v2 dark token：夜空蓝底三段（#080D17/#0D1423/
  #0A0F1C）、hairline 卡片边框（dark 白 8% / light 黑 6%）、状态徽标底色
  （success/danger 14%）、accent/渐变按主题拆分 Light/Dark 资源、
  卡片圆角 24（glass 视觉半径折算）与 chip 圆角 6。
  App.xaml completes the DESIGN.md v2 dark tokens: night-blue background
  triple, hairline card borders, 14% chip backgrounds, theme-split
  accent/gradient resources, card radius 24 and chip radius 6.

## v1.9.4 (2026-11-21)

UI 设计 B 批全量落地（B1 天空语义时段条 / B2 Windows token 对齐 / B3 品牌插画空态）+ 暗色夜空蓝修正。
Design batch B (sky-semantics timeline, Windows token alignment, branded
empty states) and dark theme night-blue correction.

**B1 天空语义时段条 / Sky timeline**

- 结果概览 hero 卡新增 SkyTimeline：渐变窗口区间条 + 最佳时刻圆点 +
  观测点时区倒计时（"最佳时刻 HH:mm · X 小时 Y 分后"）；跨午夜窗口
  自动归一化；无窗口数据时回退推荐窗口文字。
  Result hero card gains a SkyTimeline: gradient window bar + best-time
  dot + timezone-aware countdown; midnight-crossing windows normalised.

**B2 Windows token / Windows token alignment**

- Windows App.xaml accent 对齐 DESIGN.md v2：暖橙 #BD523D 替换默认
  #E63D5C，新增 FireSky token 资源（渐变端点/青绿/卡片圆角）。
  Windows App.xaml accent aligned to DESIGN.md v2 tokens.

**B3 品牌插画空态 / Branded empty states**

- 历史页与数据管理页空态统一为 BrandMarkScene：品牌标 + 双弧线轨道 +
  三颗星点（macOS/iOS Canvas 同构，Android CircularProgress+图标等效）。
  Empty states unified with BrandMarkScene across platforms.

**暗色主题修正 / Dark theme correction**

- 暗色底色从灰绿体系改为**夜空蓝黑**（hue 220-224°，暗环境下与暖橙
  accent 形成"夜空+暖光"叙事）；玻璃卡 dark tint 调整为夜蓝面板
  （层间 Δ+19 lum，上轮 Δ<1）；Android/iOS dark scheme 同步修正。
  Dark background switched from grey-green to deep night-blue
  (hue 220-224°); glass card dark tint adjusted to night-blue panel
  (Δ+19 lum, up from <1).

## v1.9.3 (2026-11-21)

UI 设计 B 批（B1 天空语义可视化 / B2 Windows token 对齐 / B3 品牌插画空态）+ 暗色主题夜空蓝修正。
Design batch B (sky-semantics timeline, Windows token alignment, branded
empty states) and dark theme night-blue correction.

**B1 天空语义时段条 / Sky timeline**

- 结果概览 hero 卡新增 SkyTimeline：渐变窗口区间条 + 最佳时刻圆点 +
  观测点时区倒计时（"最佳时刻 HH:mm · X 小时 Y 分后"）；跨午夜窗口自动
  归一化；无窗口数据时回退推荐窗口文字。
  Result hero card gains a SkyTimeline: gradient window bar + best-time
  dot + timezone-aware countdown; midnight-crossing windows are
  normalised; falls back to text when no window data exists.

**B3 品牌插画空态 / Branded empty states**

- 历史页与数据管理页空态统一为 BrandMarkScene：品牌标 + 双弧线轨道 +
  三颗星点（macOS/iOS Canvas 同构，Android CircularProgress 等效）。
  Empty states across history and data pages are unified with a
  BrandMarkScene (brand mark + dual orbit arcs + three star points).

**暗色主题修正 / Dark theme correction**

- 暗色底色从灰绿体系改为**夜空蓝黑**（hue 220-224°，暗环境下与暖橙
  accent 形成"夜空+暖光"叙事）；玻璃卡 dark tint 调整为夜蓝面板
  （层间 Δ+19 lum，上轮 Δ<1）；Android/iOS dark scheme 同步修正。
  Dark background switched from grey-green to deep night-blue
  (hue 220-224°), paired with the warm-orange accent; glass card dark
  tint adjusted to night-blue panel (Δ+19 lum, up from <1).
- Windows App.xaml accent 对齐 DESIGN.md v2（暖橙替换默认 #E63D5C）。
  Windows App.xaml accent aligned to DESIGN.md v2 tokens.

**其他 / Other**

- retention_lifecycle 并发 restore 测试放宽时序断言（慢 runner 上 cleanup
  先完成属合理语义），修复 CI flaky。
  retention_lifecycle concurrent restore test assertion relaxed for slow
  runners, fixing CI flakiness.

## v1.9.2 (2026-11-21)

数据管理类目扩展（A1）+ 毛玻璃卡片视觉（P3，QiuChenly AppStore 风格借鉴）。
Data-management category expansion (A1) and frosted-glass card visuals
(P3, borrowing from the QiuChenly AppStore style).

**数据管理类目 / Data categories**

- `inventory` 契约新增 `categories`：模型源之外的四类数据统计（卫星
  HSD、地形 SRTM、已发布报告、地图帧）+ 单点/区域结果缓存。
  The `inventory` contract gains `categories`: stats for satellite HSD,
  SRTM, published reports, map frames, and point/regional result caches.
- 手动删除 scope v2：`{"category": X}` 整类删除与 `{"category": X,
  "item": id}` 按项删除（卫星按扫描日、地形按瓦片名）；**map-frames
  删除时自动保护仍被存活快照引用的帧**（引用集来自任务历史快照扫描，
  保护项在预览 protected 中单独列出）；模型源周期删除契约保持兼容。
  Delete scope v2 supports whole-category and per-item deletion
  (satellite by scan day, SRTM by tile name); map frames referenced by
  live snapshots are automatically protected and listed separately.

**毛玻璃卡片 / Frosted-glass cards**

- 数据管理页全面卡片化：地形/库存/空态各为独立 `regularMaterial`
  毛玻璃卡（16pt 大圆角 + hairline），移除通栏 Divider；探测与地形卡
  同步升级；dark 主题卡片与背景层次实测 +13 lum（原 <1）。
  The data page is fully cardified: each block is an independent
  frosted-glass card (16pt radius + hairline), Dividers removed; dark
  theme card-vs-background separation measured at +13 lum.

## v1.9.1 (2026-11-21)

> 版本口径（2026-11-21 用户裁定）：**日常迭代一律 patch +1**（1.9.0 →
> 1.9.1 → 1.9.2…），仅重大功能里程碑才升 minor。避免一天内 minor 跳号。

UI 设计系统 P2 落地（docs/UI_DESIGN_REFRESH_2026-11-21 方案 D6/D7/D9）。
Design system P2 rollout (D6/D7/D9 of the refresh proposal).

- **历史行菜单化**（D9）：行尾三个图标按钮（恢复/重试/删除）收进单个
  「…」菜单（macOS Menu / iOS Menu / Android DropdownMenu），命中区 36，
  删除项红字隔离；三端快照验收通过。
  History rows collapse the restore/retry/delete icon buttons into a
  single "…" menu with the destructive item isolated (verified on all
  three platforms).
- **设置锚点导航**（D7）：macOS 设置 → 数据分类顶部新增锚点 chips
  （下载源/CAMS/连通性/同步/保留/目录），点击滚动直达，消除 5+ 屏长
  滚动；快照验收确认顺序为页头 → chips → 内容。
  macOS Settings → Data gains anchor chips for direct navigation.
- **空态品牌化**（D6）：历史页与数据管理页空态统一为品牌小图（24%
  透明度）+ 标题 + 说明；Android 用 WbTwilight 图标等价呈现。
  Empty states are unified with a subtle brand mark across platforms.
- 版本收敛 1.10.0（engine/engine-mobile/macOS/Android versionCode 14/iOS build 14）。

## v1.9.0 (2026-11-21)

UI 设计系统 P1 落地（docs/UI_DESIGN_REFRESH_2026-11-21 方案，纯视觉层）。
Design system P1 rollout (docs/UI_DESIGN_REFRESH_2026-11-21, visual only).

- 新增四端共享 DesignSystem：**主行动强调渐变**（暖橙→琥珀，dark/light
  两套，端点经视觉验收微调）、**状态徽标 StatusChip**（语义色 14% 底 +
  图标 + 文字，五态）、卡片 hairline 边框、深色页面底渐变去紫修正
  （青绿同族）。
  Shared DesignSystem on all four platforms: accent gradient for primary
  actions, StatusChip (semantic 14% background + icon + text, five
  states), card hairline border, and a purple-free dark page gradient.
- 替换点：工作台「开始预测」主按钮渐变；任务历史与预检行状态徽标；
  Android/iOS 主按钮渐变与历史徽标。纯视觉层——功能行为与文案口径
  逐字不变。
  Applied to the workbench CTA, task-history/preflight rows, and mobile
  equivalents; purely visual, copy identical.
- 设计 token 与组件规范沉淀于 DESIGN.md（v2，Google Labs design.md
  格式，lint errors=0），四端 UI 迭代的单一事实来源。
  Tokens and component specs live in DESIGN.md (v2, Google Labs
  design.md format, lint errors=0) as the single source of truth.

## v1.8.1 (2026-11-21)

瓦片下载可用性修复与数据目录口径（走查实测发现）。
Tile download usability fixes and the data-directory ruling (found by
hands-on walkthrough).

**瓦片下载 / Tile download**

- 修复 GUI 启动场景下瓦片下载挂起/失败：engine 的 reqwest 关闭了默认
  features 导致 macOS SystemConfiguration 系统代理不可用——GUI 启动
  （Launchpad/Finder，无环境变量代理）时直连瓦片 CDN 直到超时。开启
  `macos-system-configuration` 特性后，GUI 场景遵循系统代理（Clash 等
  自动设置），实测无环境变量下 N30E120 下载成功。
  Fixed tile downloads hanging when launched from GUI: the engine's
  reqwest disabled default features, losing macOS SystemConfiguration
  proxy support. With `macos-system-configuration` enabled, GUI launches
  follow the system proxy; verified downloading N30E120 without any env
  proxy.
- 海拔 `auto` 本地命中增加坏瓦片容错：瓦片文件存在但采样失败（损坏/
  尺寸异常）时视为未命中继续 open-meteo 兜底，不再让整个 metadata 失败。
  `auto` local-hit now tolerates a corrupt tile (read failure counts as a
  miss and falls through to the provider) instead of failing metadata.

**数据目录口径 / Data directory ruling**

- 数据目录固定在系统默认位置（用户裁定：不再支持设置）：macOS 设置页
  移除路径输入框与「选择目录」，改为只读展示 +「在访达中打开」；旧版本
  遗留在 UserDefaults 的 workspace 覆盖一律忽略（该残留曾让临时目录
  长期覆盖系统默认）；开发/运维仍可用 FIRESKY_WORKSPACE 环境变量覆盖。
  The data directory is fixed at the system default (user ruling): the
  macOS settings page drops the path editor in favor of a read-only
  display plus "Reveal in Finder"; stale UserDefaults workspace overrides
  are ignored; FIRESKY_WORKSPACE remains for developers.

**任务历史分页 / Task history pagination**

- 引擎 `task_history` 支持 `before` 游标（RFC3339 校验），响应新增
  `has_more`/`next_cursor`；三端历史页底部新增「加载更早任务」，超过
  单页上限的记录不再不可见（此前 macOS 实测 117 条中 17 条不可达）。
  `task_history` gains a `before` cursor with `has_more`/`next_cursor`;
  all three platforms offer "load older tasks" so records beyond the
  first page are reachable again (17 of 117 were unreachable).

## v1.8.0 (2026-11-21)

海拔来源简化（本地 SRTM 优先）与预测日期限制（用户裁定）。
Elevation-source simplification (local SRTM first) and forecast date
limits (user ruling).

**海拔来源 / Elevation source**

- `auto` 语义反转为**本地 SRTM 瓦片优先**（离线命中、毫秒级、不联网），
  未下载瓦片时走 open-meteo，HTTP 失败且允许下载时落回 SRTM 下载发布；
  命中即返回 `local-timezone+srtm`，与 `metadata` 响应 source 一致。
  `auto` now prefers the locally cached SRTM tile, falls back to
  open-meteo, and downloads the tile only when the provider fails and
  downloads are allowed.
- 四端设置页移除「海拔来源」三档选择（默认逻辑即所需行为，无需设置）；
  契约 `auto/http/srtm` 保留（payload 仍透传 `elevation_source=auto`，
  高级用户可继续用环境/请求级覆盖）。设置分类从四类简化为三类
  （通用/数据/高级）。
  The three-mode picker is removed from all four platform settings pages
  (three categories now); the contract stays and still forwards
  `elevation_source=auto`.
- 引擎测试重写覆盖新语义（本地命中优先 / provider 兜底 / HTTP 失败下载
  SRTM / 禁止下载明确报错不冒充）。
  Engine tests rewritten for the new semantics.

**预测日期限制 / Forecast date limits**

- 旧日期（早于本地时区"今天"）**仅使用本地缓存**：prepare 链跳过全部
  下载并返回 `cache-only`，预检 datasets 标注 `past-local-cache-only`
  （UI 缺数提示加"旧日期仅本地缓存"前缀）；绝不重新下载老数据。
  Past dates use local cache only: the prepare chain skips every
  download, preflight rows are annotated, and old data is never
  re-downloaded.
- CAMS 时效窗口（**5 天**，本地时区口径）：超出窗口的日期在预检入口
  明确拒绝（"CAMS 全球大气数据仅覆盖未来 5 天内的日期"），不进入任何
  下载或计算路径；GFS/EC 归档不受此限制。
  CAMS coverage window (5 local-timezone days): beyond-window dates are
  rejected explicitly at preflight entry and never enter download or
  compute paths; GFS/EC archives are unaffected.
- 新增 `date_policy` 契约函数（Past/InWindow/BeyondCams，时区感知，
  `FIRESKY_CONTEXT_NOW_UTC` 可注入基准）与单元测试。

## v1.7.0 (2026-11-21)

数据布局可见化与迁移（方案 §8 阶段 B）：工作区直接使用可见分类目录。
Visible data layout with migration (phase B, §8 of the redesign).

**可见布局 / Visible layout**

- 新增统一路径解析层（engine `paths.rs`）：显式 `FIRESKY_*` 环境变量永远
  优先（开发者/运维选择，不参与迁移），其余按工作区代际取默认——
  Legacy（存在 `.cache`/`.firesky`）继续旧路径完全兼容读写，Visible
  （写入 `data-layout-v1.json` 标记或全新工作区）使用可见目录：
  `gfs/`、`ecmwf/`、`cams/`、`srtm/`、`himawari-hsd/`、`locations.json`、
  `profiles/`、`map-frames/`、`point-forecasts/`、`regional-forecasts/`、
  `reports/`（原 `reports/web` 上移一层）、`settings/`、`state/tasks`。
  A single resolver (engine `paths.rs`) now owns every data path: explicit
  `FIRESKY_*` env vars always win (never migrated), otherwise the
  workspace generation decides — Legacy stays fully compatible, Visible
  uses user-facing category directories marked by `data-layout-v1.json`.
  移动端宿主（engine-mobile）通过 `fsm_start` 注入的显式环境变量锁定
  路径，不受布局改动影响。
  Mobile hosts pin paths via explicit env injection, unaffected.
- 全部默认路径消费者接入统一解析：desktop_runtime（库存/位置/SRTM/
  报告/单点/区域/地图帧/任务历史）、native_data、cache_retention、
  desktop_sync（同步设置与状态）、retention_settings、desktop_profiles。
  Every default-path consumer is wired to the resolver.

**迁移 / Migration**

- 新增 `data_layout_status` / `data_layout_migrate` 契约：状态预览
  （代际、14 项映射的存在性/文件数/体积、env 覆盖清单、忙闲）、迁移
  执行（同卷 rename 逐项移动，冲突立即整单逆序回滚；`reports/web →
  reports` 父目录壳三步上移；成功写入标记；`.cache`/`.firesky` 残壳
  保留由用户确认后清理）。迁移独占计算与同步锁；完成后标记
  `requires_restart`，UI 自动重连引擎通道。
  New layout contracts: a status preview and a migrate executor
  (same-volume renames, full rollback on any conflict, a three-step
  lift for the `reports/web` parent shell, marker write, residual shells
  kept for user cleanup). Migration takes the compute+sync locks and
  marks `requires_restart`; the UI reconnects automatically.
- macOS 设置 → 数据 → 数据目录新增迁移入口：Legacy 提示 + 迁移按钮
  （忙时禁用）+ 确认弹窗（列出待迁移条目数/文件数/体积）+ 结果消息。
  macOS Settings → Data → Data Directory gains the migration entry with
  a confirmation summary and result messages.

## v1.6.1 (2026-11-21)

发布前全流程走查（模拟真实用户体验）发现的问题修复。
Fixes surfaced by a full pre-release walkthrough (simulated real-user flows).

**macOS 安装引导 / macOS install flow**

- 修复 DMG 引导「安装到应用程序」后应用不自动重启：`openApplication` 会
  被 LaunchServices 指向同 bundle id 的现有实例（且 async 版本在同步上
  下文中被丢弃）；改为独立接力进程在旧实例退出后再打开新实例。
  Fixed the DMG-guided install not relaunching: LaunchServices pointed
  `openApplication` at the still-running instance and the async variant
  was discarded; a detached relay process now opens the installed copy
  after the old instance quits.
- 走查路径：下载 Release DMG → 校验 SHA256 → Finder 式挂载 → /Volumes
  双击启动 → 确认弹窗 → 安装 → 自动重启，全链通过。
  Verified end-to-end: download, checksum, mount, launch from volume,
  confirm, install, auto-relaunch.

**地形下载反馈 / Terrain download feedback**

- 修复下载完成消息被 `loadTerrain` 刷新吞掉（双端）：状态刷新成功不再
  清空刚设置的完成消息，仅失败时覆盖。
  The completion message is no longer swallowed by the inventory refresh
  on both platforms; refresh now only overwrites on failure.
- Mock 通道补齐 `terrain_inventory` 的 `estimate_global`（按 bounds 计算
  候选数/已有/估算体积，口径同真引擎），区域确认框不再显示「候选 0 块」。
  Mock transports now implement `estimate_global` with bounds-aware
  candidate counts and size estimates, matching the real engine.

## v1.6.0 (2026-11-21)

Android / iOS 与 macOS 功能对齐（2026-11-20 方案阶段 A/C/D 移动端落地）。
Android and iOS parity with macOS (mobile slice of the 2026-11-20 redesign,
phases A/C/D).

**任务历史 / Task history**

- 双端新增独立「任务历史」页：列表 + 详情（事件时间线，点击行展开）、
  行内恢复已发布结果 / 恢复参数并重新检查 / 删除记录三个动作；删除沿用
  macOS 口径——只删状态与事件，不删报告与数据；协议校验（task_id 长度/
  16 进制、状态枚举、事件 seq 单调）与桌面一致。
  Both mobile apps gain a standalone Task History page: list plus detail
  (event timeline via row tap), inline restore-result / restore-params /
  delete-record actions; deletion only removes status and events. Protocol
  validation mirrors the desktop.
- Android 底部导航扩为 4 项（工作台/数据管理/任务历史/设置）；iOS 底部
  标签扩为 4 项并移除数据页红点徽标（与 macOS 中性化口径一致）。
  Android bottom navigation and iOS tab bar both expand to four entries;
  the iOS data-tab badge is removed, matching the macOS neutral probe
  presentation.

**数据管理 / Data management**

- Android 新增数据管理页；iOS 数据页按 macOS 口径改造：库存（周期展开）
  + 按周期/整源手动删除（`data_delete_preview`/`execute`：确认框展示
  候选数/预计释放/占用跳过说明，预览过期不静默扩大）+ 地形区域下载卡
  （区域预设选择 → 估算确认 → 批量进度/取消续传）。
  Android gains a Data Management screen; the iOS data page is reworked
  to macOS parity: inventory with per-cycle/whole-source manual deletion
  (preview/execute contracts with stale-preview rejection) and a terrain
  region-download card (presets, sampled estimate confirmation, aggregate
  progress with cancellation and resume).

**设置分类 / Settings categories**

- 双端设置拆分类（macOS SettingsCategory 同构）：通用（主题）/ 数据
  （下载源 + CAMS + 下载连通性）/ 位置与海拔（海拔来源）/ 高级（引擎
  通道 + 沙箱路径）；切换分类保留表单草稿。
  Settings are split into General / Data (source + CAMS + probe) /
  Location & Elevation / Advanced on both platforms, with drafts
  preserved across category switches.

**夹具 / Fixtures**

- 移动端截图夹具：Android 沿用 `FIRESKY_NATIVE_PAGE`；iOS 新增
  `SIMCTL_CHILD_FIRESKY_PAGE`（初始页）与 `SIMCTL_CHILD_FIRESKY_TRANSPORT`
  （强制 mock 通道），便于无 UI 点按的模拟器验收。
  Mobile screenshot fixtures: iOS gains `SIMCTL_CHILD_FIRESKY_PAGE` and
  `SIMCTL_CHILD_FIRESKY_TRANSPORT` env injection (Android already had
  `FIRESKY_NATIVE_PAGE`).
- iOS `ObservationDate` 新增 ISO8601 时间戳解析（兼容小数秒），修复任务
  时间戳解析。
  iOS `ObservationDate` gains ISO8601 timestamp parsing (fractional-second
  tolerant).
- Mock 通道（双端）补齐 task_history / task_events / task_forget /
  result_restore / data_delete / terrain 契约，无引擎库环境可驱动新页面
  全链路。
  Both mock transports now cover the history/delete/terrain contracts.

## v1.5.0 (2026-11-21)

应用内更新逻辑（GitHub Releases 检查 → 下载 → 校验 → 自动安装）。
In-app software update flow (check GitHub Releases, download, verify,
install automatically).

**软件更新 / Software update**

- 新增设置 → 通用「软件更新」：检查更新、发现新版本时显示版本号与发布
  说明（折叠）、下载进度与取消；启动后自动静默检查（默认开，可关），
  发现新版本时侧栏「设置」入口显示绿色徽标。
  Settings → General gains a "Software update" block: check for updates,
  release notes (collapsed) and download progress with cancellation; a
  silent check runs after launch (on by default, toggleable) and a green
  badge appears on the sidebar Settings entry when a new version exists.
- 下载完成后自动执行 SHA256SUMS.txt 完整性校验，随后挂载 DMG 拷贝安装
  到「应用程序」（旧版本先入废纸篓）并自动重启；校验失败或无写权限时
  给出明确提示与手动安装指引。下载/检查走 URLSession（系统代理；源码
  运行时遵循环境代理变量）。
  After download the SHA256SUMS.txt checksum is verified, then the DMG is
  mounted, copied into /Applications (previous install trashed) and the
  app relaunches automatically; checksum or permission failures explain
  manual installation. Checking and downloading use URLSession (system
  proxy settings; environment proxy variables honored for dev runs).
- 版本比较/资产名匹配/架构探测/release 解析/校验和行解析为纯函数，进
  入 Swift fixture 门禁（UpdaterChecks）。
  Version comparison, asset matching, architecture detection, release
  parsing and checksum-row parsing are pure functions covered by the
  new UpdaterChecks Swift fixture gate.
- JSONObject typealias 抽到共享 JSONTypes.swift（主 target 与 fixture
  共用）。
  The JSONObject typealias moved to shared JSONTypes.swift.

## v1.4.0 (2026-11-21)

DMG 安装引导（拖拽 + 自助移动）+ 地形区域下载预设。
DMG install guidance (drag-to-Applications plus self-install prompt) and
region presets for terrain download.

**macOS 安装体验 / macOS install experience**

- DMG 现在内置 `/Applications` 符号链接：Finder 打开即是经典的拖拽安装
  窗口；打包冒烟新增挂载校验（应用可访问 + 符号链接指向正确）。
  The DMG now ships an `/Applications` symlink next to the app, so Finder
  opens the classic drag-to-install window; bundle smoke verifies the
  layout by mounting the image.
- 从挂载的磁盘映像双击启动时，应用主动提供安装引导：「应用程序」已有
  相同或更新版本时提供打开已装版本；否则拷贝安装（旧版本先移入废纸篓）
  并重新打开；无写权限时提示手动拖拽。快照/夹具与常规路径不受影响。
  Launching from a mounted image now offers self-install: open the
  already-installed copy when it is same-or-newer, otherwise copy-install
  (older install goes to Trash first) and relaunch; a permission failure
  explains manual drag-install instead of failing silently.

**区域地形下载 / Region terrain download**

- 下载范围从"全球"细化为区域预设（用户口径）：中国、东亚、东南亚、亚洲、
  欧洲、北美、南美、非洲、大洋洲、全球，均为覆盖矩形直接映射引擎
  `bounds`；确认框按所选区域展示候选数/已有/抽样估算体积。全球保留为
  预设之一。
  Download scope gains region presets (China, East Asia, Southeast Asia,
  Asia, Europe, North America, South America, Africa, Oceania, Global);
  each maps to an engine `bounds` rectangle and the confirmation dialog
  shows per-region candidate counts, existing tiles and the sampled size
  estimate. Global remains one of the presets.
- `terrain_inventory` 估算接口支持 `bounds`（区域预设复用同一抽样路径），
  焦点瓦片改为可选（估算请求不必带坐标）。
  The `terrain_inventory` estimate honors `bounds`, and the focused tile
  became optional (estimate requests need no coordinates).

## v1.3.0 (2026-11-21)

数据管理手动删除 + 全球 SRTM 地形下载（引擎契约 + macOS 接入）。
Manual data deletion and global SRTM terrain download (engine contracts,
wired into macOS).

**数据管理 / Data management**

- 新增 `data_delete_preview` / `data_delete_execute` 契约：按库存周期键或
  整源删除（UI 不传磁盘路径）；预览冻结候选集（路径+大小+mtime 身份哈希），
  执行时重算校验，过期即拒绝——确认过的范围不会静默扩大。下载锁
  （flock 核验）占用中的文件跳过并单独报告；GRIB 连同索引/元数据 sidecar
  整组删除；报告、快照与任务记录不受影响。
  New `data_delete_preview` / `data_delete_execute` contracts: delete by
  inventory cycle key or whole source (no disk paths from the UI). The
  preview freezes the candidate set behind an identity hash; execute
  re-derives it and rejects stale previews so a confirmed scope can never
  silently grow. Flock-verified download locks defer busy files, GRIBs
  delete together with their index/metadata sidecars; reports, snapshots
  and task records are untouched.
- macOS 数据管理页：每个周期与每个数据源新增删除入口，确认框显示
  具体范围、文件数与预计释放空间；执行后显示已删除/跳过/失败/实际
  释放并刷新库存。
  The macOS data page gains per-cycle and per-source delete actions with
  a confirmation showing the exact scope, file count and estimated
  space; results report deleted/skipped/failed/actual freed bytes.

**全球地形下载 / Global terrain download**

- `terrain_download` 支持全球批量模式（用户口径：下载即全球，不是单点）：
  枚举全部候选瓦片（默认整个 SRTM 覆盖域，支持 bounds 注入），跳过已
  有瓦片（断点续传），4 并发下载，服务端 404 归类为缺失而非失败；
  `terrain_inventory` 提供按 Content-Length 头抽样的体积估算（候选数/
  已有/预计 GB），确认框展示后再开始。Skadi 数据集对海洋也有小空值
  瓦片，因此全球下载通常覆盖全部候选（抽样估算因海陆混合可能偏低，
  以运行时用量为准）。下载不占用计算锁；磁盘空间护栏（启动前估算
  校验 + 运行中低于 2 GB 自动停止）保留已完成瓦片。
  `terrain_download` gains a global bulk mode: it enumerates every
  candidate tile (whole SRTM coverage by default, bounds injectable),
  skips existing tiles (resumable), downloads with bounded concurrency,
  and classifies provider 404s as absent rather than failures;
  `terrain_inventory` adds Content-Length-header-sampled size estimates
  shown in the confirmation dialog before starting. The Skadi dataset
  also ships small void tiles over oceans, so a global run typically
  downloads every candidate; bulk runs hold no compute lock and a
  free-disk guardrail (pre-start estimate check plus a 2 GB runtime
  stop) preserves finished tiles.
- macOS 数据管理页「地形 SRTM」面板：主按钮「下载全球地形数据」（确认
  候选/体积/已有后开始），批量进度显示总体百分比、已下载/缺失/失败/
  已有计数与当前瓦片；「仅当前地点」保留为次级入口。取消可从任务
  历史页查看（地形下载记录为任务）。
  The macOS terrain panel makes "download global terrain" the primary
  action behind the estimate confirmation, shows aggregate progress
  (percent, downloaded/absent/failed/existing, current tile), and keeps
  point download as a secondary action; runs appear in Task History.

**其他 / Other**

- SRTM 瓦片下载改用遵循环境代理的数据面 HTTP client（与 ADS 同一口径），
  修复需要代理的网络下直连 S3 挂起、整请求超时失败的问题；无代理环境
  行为不变。
  SRTM tile downloads now use a data-plane HTTP client that honors the
  environment proxy (the established ADS rule), fixing hangs and
  whole-request timeouts on networks that require the proxy; proxy-less
  environments are unchanged.
- 下载瓦片尺寸以真实 gzip 边界校验（仅接受两种已知 SRTM 维度），取消
  响应以 64 KiB 分块为上界；`tile_count`/`tiles_truncated` 让大规模
  本地清单不再膨胀 JSON。
  Tile acceptance validates the real gzip bounds (only the two known SRTM
  dimensions), cancellation is bounded by one 64 KiB chunk, and terrain
  inventory caps the tiles list (`tile_count`/`tiles_truncated`).

## v1.2.0 (2026-11-20)

macOS 信息架构重构：任务历史独立页面 + 设置分类拆分 + 数据管理瘦身。
macOS information-architecture rework: standalone Task History page,
categorized Settings, and a slimmer Data Management page.

**macOS 信息架构 / macOS information architecture**

- 任务历史成为独立侧栏页面（`Page.history`，⌘5）：左列表 + 右详情区，
  新增「全部 / 主动任务 / 后台同步」筛选；行内直接提供恢复结果、恢复
  参数并重查、删除记录三个动作；详情区含状态、原始 UTC、进度与完整
  事件时间线。不再嵌在数据页底部。
  Task History is now a standalone sidebar page (⌘5) with a list/detail
  split, an all/active/background-sync filter, and inline restore/
  retry/delete actions; details show status, raw UTC, progress, and the
  full event timeline instead of living at the bottom of the data page.
- 设置拆分为四类：通用（主题）、数据（下载源 / CAMS / 下载连通性 /
  后台同步与助手 / 存储与保留 / 数据目录）、位置与海拔（海拔来源，
  移除冒充配置的「当前实际来源」）、高级（自定义引擎路径）。切换分类
  保留表单草稿；CAMS 提醒跳转直接落位到「数据」并展开教程。
  Settings is split into General / Data / Location & Elevation /
  Advanced; drafts survive category switches, and the CAMS onboarding
  alert jumps straight to the Data category with the tutorial expanded.
- 数据管理页瘦身：移除嵌入的下载连通性卡片与「立即同步」（随同步配置
  迁入「设置 → 数据」），顶部显示实际数据目录并提供「同步设置」跳转；
  库存仅统计模式数据时明确标注口径。
  The Data Management page drops the embedded probe card and "sync now"
  (moved to Settings → Data), shows the resolved data directory up
  front, links to sync settings, and states that totals cover the model
  datasets only.
- 顶栏按页面生成：设置 / 数据管理 / 任务历史不再借用预测地点（修复
  设置页误显示「杭州 · 坐标」），删除装饰性日期 chip；数据侧栏红点
  移除（探测状态在设置内中性呈现）。
  The topbar no longer leaks the forecast location onto Settings /
  Data / History (fixes "Hangzhou · coordinates" appearing in
  Settings), drops the decorative date chip, and removes the sidebar
  probe badge (probe state is presented neutrally in Settings).

**发布 / Release**

- 版本收敛 1.2.0（engine / engine-mobile / macOS / Android versionCode 4 /
  iOS build 4 一致；SemVer minor：UI 信息架构变更）。
- 本地验收 macOS arm64：`cargo build` 引擎 1.2.0、Swift 构建 + FFI/
  夹具/打包冒烟全绿；数据管理 / 任务历史 / 设置三页快照视觉验收通过
  （无坐标泄漏、筛选与分类栏齐全、无破版）。

## v1.1.1 (2026-11-20)

海拔来源三档 + macOS 体验修复 + 同步助手卸载修复。
Elevation source modes, macOS UX fixes, and the sync-helper uninstall fix.

**海拔来源 / Elevation source**

- 新增 `elevation_source` 三档契约（engine `location.rs`）：`auto`（默认，
  HTTP 优先、SRTM 兜底）、`http`（仅 open-meteo，失败显式报错不静默落
  SRTM）、`srtm`（跳过 HTTP，本地时区 + SRTM 瓦片，可离线）。
  New `elevation_source` contract: `auto` (default, HTTP first with SRTM
  fallback), `http` (open-meteo only, explicit error on failure), `srtm`
  (fully local timezone + SRTM tiles, works offline).
- macOS / Android / iOS / 共享 Web 四端设置页均可选择并持久化该档位，
  随 metadata 与预测准备请求下发；引擎严格校验非法取值。
  Selectable and persisted in Settings on macOS / Android / iOS / shared
  Web; forwarded with metadata and prepare payloads; invalid values are
  rejected by the engine.

**macOS 体验 / macOS UX**

- 结果标题跟随结果的原始参数而非当前表单；参数变更后旧结果保留，标注
  「参数已变更」并提供一键重算；恢复历史结果先回填原始参数。
- 任务运行中不再锁死切页/表单/搜索：⌘1-4 切页可用，顶栏常驻进度与取消；
  搜索/定位走独立轻量状态，不占用全局 busy。
- 收藏支持右键删除、带坐标命名、空态引导；会话持久化上次地点/日期/模式；
  时间统一本地时区展示（UTC 进 help）；任务历史行标题去重、后台同步轮次
  默认折叠；错误全文可见可复制；保存失败保留草稿；卸载助手与任务运行中
  退出均需显式确认；未预测状态不再误显示预报家族标签。
- Results keep their original parameters (title, restore, stale badge with
  one-click recompute); navigation/forms/search stay usable while tasks
  run; favorites delete/coordinate naming/empty-state guidance; session
  persistence; local-timezone timestamps; task-history dedup with folded
  background sync rounds; full copyable errors; drafts kept on save
  failure; explicit confirmations for helper uninstall and quit-while-
  running; correct "not forecast yet" labeling.

**同步助手 / Sync helper**

- 修复卸载后台助手后在途同步仍存活的问题：卸载（及安装自愈）时按工作区
  精确终止 `data_sync_tick` 引擎子进程（SIGTERM → 宽限 → SIGKILL），
  释放同步锁；应用内同步不受影响。
  Uninstalling (and install self-heal) now terminates in-flight helper
  `data_sync_tick` engine rounds for the workspace (SIGTERM, grace period,
  then SIGKILL), releasing the sync lock.
- 同步锁竞争错误附带持锁进程 PID；应用文案中性化为「另一轮数据同步正在
  进行（应用内同步或后台助手）」，不再把应用内同步误报为后台助手。
  Lock-busy errors now name the holder PID; the app uses neutral wording
  instead of misattributing in-app sync rounds to the helper.

**发布 / Release**

- 版本收敛 1.1.1（engine / engine-mobile / macOS / Android versionCode 3 /
  iOS build 3 一致；SemVer patch：修复与小型体验增强）。
- 多平台构建与回归交由 GitHub Actions（`native-desktop.yml` macOS
  arm64/x86_64 矩阵 + `ci.yml` 回归）；本地验收 macOS arm64：引擎
  `cargo test --lib` 501 passed / 0 failed / 3 ignored，Swift 构建 +
  FFI/夹具/打包冒烟全绿，共享 Web `npm test` 158 pass；海拔三档实测
  （srtm 本地瓦片 / http 在线 / auto）路由正确。
  Multi-platform builds/regression run on GitHub Actions; local macOS
  arm64 acceptance: engine 501 tests passed, Swift build + FFI/fixture/
  bundle smokes green, shared Web 158 tests passed, live three-mode
  elevation routing verified.

## v1.1.0 (2026-11-20)

移动端与 Docker 正式版：Android/iOS 双原生客户端与 Docker 镜像首次发布。
First mobile & Docker release: native Android/iOS clients and the Docker image.

**新模块 / New modules**

- **Android**（Kotlin/Compose + JNI）：`engine-mobile` 真引擎接线（手工 C ABI
  `fsmobile.h` + JNI shim，D2 裁定路线）；ecCodes definitions 完整树随 assets
  打包、首启解包到 filesDir；默认传输 mock → ffi（设置页保留 mock 切换）；
  自签签名 release APK（arm64-v8a，minSdk 26）。本地模拟器 instrumented
  全链 + 与桌面帧级 parity + kill-restart 会话恢复验收通过（测试计数见发布
  门禁日志，证据归档 output/）。
- **iOS**（SwiftUI）：definitions 进 Bundle（`Resources/eccodes/definitions`
  文件夹引用，测试 bundle 优先，消除宿主 env 注入假阳性）；默认传输 ffi；
  模拟器 `xcodebuild test` 全绿（含新增 kill-restart 恢复测试，计数见发布
  门禁日志）；无签名 device IPA 供 AltStore/Sideloadly 免费 Apple ID 自签
  侧载（7 天有效）。地图为 MapKit 原生（D1 裁定）。
- **Docker**：三阶段镜像以 Release 资产 tarball 分发
  （`FireSky-1.1.0-docker-arm64.tar.gz`，linux/arm64 本地构建 + 冒烟通过；
  amd64 可源码构建；本版暂未发布 GHCR）。

**引擎 / Engine**

- 修复全部编译警告：去除多余 `mut`、仅测试引用函数加 `#[cfg(test)]`、serde
  `deny_unknown_fields` 契约字段标注 `#[allow(dead_code)]`；零行为变化。
  `cargo test --release` 491 passed / 0 failed / 3 ignored。

**会话恢复 / Session restore**

- 移动双端对齐 macOS `result_restore` 语义：hello → task_history → 最近可
  恢复 forecast → 恢复 result 与 location/date/phenomenon 参数（gfs 缺失回退
  ec），守 busy/generation，不覆盖进行中会话；杀 App 重启自动恢复上次结果。

**发布 / Release**

- 版本收敛 1.1.0（engine / engine-mobile / macOS / Android / iOS 一致；
  SemVer minor：新增 Android/iOS/Docker 模块）。
- 载荷：`FireSky-1.1.0-macos-arm64.dmg`、`FireSky-1.1.0-macos-x86_64.dmg`
  （沿用 v1.0.0 门禁）、`FireSky-1.1.0-android-arm64.apk`、
  `FireSky-1.1.0-ios-unsigned.ipa`、`SHA256SUMS.txt`、Docker tarball（兜底）。
  安装指引（Gatekeeper xattr / Android 未知来源+同 key 覆盖 / iOS 自签 7 天 /
  docker run）见 README「下载安装 / Releases」。
- 平台策略（2026-11-20 用户裁定）：Windows 正式包后续在 Windows 机器构建
  （源码与 CI 验证保留）；Linux 不再作为发布目标（GTK4/FFI 代码保留）。
- 已知限制：移动端 v1 仅单点预测（无区域/实况）；Android 地图延后（坐标/
  收藏输入）；iOS 真机蜂窝下载验收待硬件；definitions 约 131 MB 待裁剪；
  macOS ad-hoc 签名（无 Developer ID/公证，首次打开需手动放行）。

## v1.0.0 (2026-11-20)

首个原生正式版：macOS SwiftUI/AppKit + Rust FFI 单引擎。
First native major release: macOS SwiftUI/AppKit with a single Rust engine.

2026-11-20 正式发布（GitHub Release `v1.0.0`，双架构 DMG）；2026-11-15
完成发布模拟预演。

**架构 / Architecture**

- 正式产品为 macOS 原生 `.app`（SwiftUI/AppKit + `libfiresky_engine.dylib`
  ABI v1）；Python/PySide/Tauri 旧栈已全部退役删除，正式包不含 Python，
  不因 FFI 缺失静默回退。
- 点、区域、实况统一 `check -> publications -> prepare -> forecast` 工作流；
  区域独立城市排名、冻结结果剖面、任务历史/回放/恢复、后台同步助手。
- 内置资源包（695 城市目录 + 时区数据，sha256 清单审计）；资源缺失硬失败。

**数值与性能 / Numerics & performance**

- 时间缓存单位/键一致性修复；新增 opt-in consistent/exact 时间键模式
  （默认 quantized-120s 不变）。
- 全国 0.25° 批量（36,685 点 / 365,794 样本）：共享 motion 缓存、单层快
  路径、NEON blocking、统一投影排序等已落地；4 核忙机 wall ≈ 551s、峰值
  ≈ 3.25 GiB（< 4 GiB 门槛）。位级一致门槛下精确复用已审计穷尽
  （见 docs/archive/ALGORITHM_AUDIT_2026-11-15.zh-CN.md）。
- 近似光路模板缓存保持 opt-in（实测 4 核 CPU -18.4%，但改变 10.6% 点的
  最佳时刻步，未获容差审批，默认关闭）。

**跨平台与移动 / Cross-platform & mobile**

- Native Desktop CI 首次全绿（run 44571959210 @ f341ef8，10 jobs）：
  macOS arm64/x86_64（DMG + hdiutil 校验 + FFI bundle 冒烟）、Windows
  WinUI x64/ARM64（编译 + 产物验证：exe/引擎 DLL/资源清单/无 Python）、
  Linux GTK4（构建 + FFI smoke）、Linux FFI amd64/arm64、Docker
  amd64/arm64 构建 + 运行时冒烟、原生资源契约。
- 区域单模型契约三端对齐 macOS：Linux GTK/WinUI/Web 区域页改为 GFS/EC
  单选（引擎拒绝区域 `model_source:"both"`），回放恢复同步对齐。
- MN1 移动里程碑：`engine-mobile` 承载桌面同源 `serve_runtime`；iOS
  模拟器全链路 GFS+EC 真实解码、预置缓存零网络、与桌面帧级 parity；
  Android `libengine_mobile.so` 仅依赖系统库。MN2/MN3（Android 全链路、
  真机与 definitions 打包）进行中，见 `docs/MOBILE_NATIVE_PLAN.md`。

**正式发布 / Release**

- 发布流程与证据：`docs/RELEASE_MACOS.zh-CN.md`；模拟预演记录
  `docs/RELEASE_V1_SIMULATION_2026-11-15.zh-CN.md`。
- 载荷：`FireSky-1.0.0-macos-arm64.dmg`（本地构建，全门禁通过）与
  `FireSky-1.0.0-macos-x86_64.dmg`（CI Intel runner 构建 + 冒烟），同源码
  f341ef8；`SHA256SUMS.txt` 随 Release 发布。
- 门禁（2026-11-20）：Rust 491 passed / 0 failed / 3 ignored + 集成
  4+2+4；pytest 契约 38 passed / 1 skipped；clean-machine/冷启动/bundle
  冒烟 4/4；离线 flow ×3 + UI 回放 ×3（deny network*）全过；双 DMG
  hdiutil verify + 挂载校验（版本/架构/codesign/资源清单）。
- 已知限制：ad-hoc 签名（无 Developer ID/公证，首次打开需手动放行，见
  发布文档）；Windows/Linux 真机交互式 UI 验收与正式安装包未完成；
  macOS Intel 真机验收未完成（CI Intel 构建+冒烟已过）；空闲受控性能
  窗口未获得。

**清理 / Cleanup**

- 历史计划/迁移/退役文档移入 `docs/archive/`；删除误生成 `-q/` 目录、
  `desktop/` 空壳与重复文档副本；旧 UI 实验产物移出 `dist/`。
- 发布前精简（2026-11-16）：24 个历史 output 目录（约 10 GB）与
  `engine/target-profile`（1.7 GB）移入废纸篓；15 个开发期 bench/audit
  examples 删除（不参与 release 构建，CI 无引用）；归档文档链接修正。
  测试套件（cargo test / pytest / Swift 夹具 / CI 冒烟脚本）保留为
  质量门禁，不进入发布包。

## v0.1.1 (2026-11-07)

首次发布后的修正与改进。Fixes and improvements since v0.1.0.

**修复**

- EC 单点：`ecmwf-opendata` 依赖未声明（新增 `ec` extra）；下载清单补
  2t/2d 地表温/露点（部分时效公开数据不发布，按可选增强处理，缺失时
  LCL 云底自动回退），EC 剖面输出字段集与 GFS 对齐
- 实况：移除机场 METAR 修正链路与 HCAI 下载路径（下载过慢），校准帧
  改为 HSD 分段独占；satpy/pyresample/aiohttp 等实况依赖此前未声明，
  新增 `nowcast` extra
- 实况发布门：覆盖率按文档契约逐采样点计算；语义改为**有可用采样即
  发布**，覆盖率随结果披露（`calibrated_sample_coverage_ratio`），
  仅零覆盖 fail-closed
- 旧原生 dylib 残留已废弃符号：重建后 EC 路径回归稳定
- 根路径 `/` 自动跳转预测工作台；文档（PRINCIPLES/README 中英）
  GitHub 数学渲染全面修复（转义、列表结构、lt/gt）

**改进**

- CAMS/ADS 批量预取：事件窗口多个时刻合并为一次 ADS 请求，冷启动
  CAMS 阶段时延约减半
- GFS/EC 单点报告字段集对齐（70 vs 70，69 共有；仅剩源特有字段）
- 精简代码库：移除未接入服务路径的模块与前端组件，收敛公开 payload
  字段到实际产出范围
- 新增 GitHub Actions CI（pytest / cargo test / svelte-check / docker
  build / macOS+Windows 桌面打包）；DEPLOY 补实况预热 cron 示例与
  token 步骤
- **PySide6 桌面应用**（`desktop/app.py`）：本地单机形态，与 Web 服务
  共用同一预测核心路径（数值一致）；观测点表单/常用城市/进度日志/
  峰值档位卡片/逐五分钟质量曲线与台账；CI 自动出 macOS dmg 与
  Windows exe 目录

## v0.1.0 (2026-11-07)

首个公开发布版本。First public release.

**引擎（Python + Rust）**

- 完整预测链路：GFS 0.25° 子集下载（fail-closed 位图掩膜 + mmap 解码缓存）、
  三族云柱派生、ECMWF IFS 第二模式源、CAMS 11 层 532nm 气溶胶剖面（含
  lead 回退）
- Rust 光追评分器（CPU）：逐观测点 × 逐五分钟步长的太阳入射 + 观测者返程
  双向追踪，含地球曲率、地形/海面下界、半拉格朗日风场平流（RK2 中点风）、
  36 方位 × 90 仰角视角球带积分、目标甲板自遮蔽距离衰减与连接迟滞、时间
  括号占空比混合
- 全国区域批处理（共享张量批量，90 个 Rust 契约测试）与单点精算
- 云属诊断：压力族兼容判定 + 证据驱动云属代理 v2（相态 + 教程经验厚度
  阈值，显示专用、不参与评分），前端附烧霞适配度排序
- 实况 nowcast：向日葵 9 HSD + HCAI 校准卫星云柱（多观测 + 落地判定）

**服务**

- FastAPI 单进程同时提供 API（`/api/v1/firesky/*`）与前端 SPA 静态托管
- 异步预测 job（SSE 进度 / 取消 / 相同请求去重）、全国摘要 / 截面 / 诊断
  端点、数据资产状态与 prune
- CAMS/ADS 下载有界并发（默认 2，`FIRESKY_CAMS_DOWNLOAD_WORKERS` 可调 1–4），
  缓解冷缓存首跑的 ADS 排队等待

**前端（SvelteKit + Tailwind 4）**

- 预测 / 实况 / 形势图三页 + 管理控制台（总览 / 实时计算 / 运行队列 / 数据资产）
- 全站液态玻璃材质体系（明暗双主题、亮色 Warm Sand 色板统一）、地图图层
  控制、云属卡片

**部署**

- Docker Compose 三阶段构建（Rust → Node → python:3.13-slim，非 root 运行）
- 裸机部署文档覆盖 Linux systemd / macOS launchd / Windows（PowerShell、
  任务计划、NSSM、WSL2）三平台，含反代 / cron 定时 / 安全清单 / 排障
- GPL-3.0-only 许可证；致谢 SunsetBot 火烧云教程
  （<https://www.sunsetbot.top/halo/>）
