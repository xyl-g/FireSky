# 更新日志（Changelog）

产品版本遵循语义化版本（SemVer）。v1.0.0 起产品版本与 Rust 引擎
`engine/Cargo.toml` 的 `version` 一致；旧 Python 栈（`src/firesky/version.py`）
已于 2026-11-13 退役删除。

English: product releases follow SemVer. Since v1.0.0 the product version
matches the Rust engine crate version (`engine/Cargo.toml`); the legacy
Python stack (`src/firesky/version.py`) was retired on 2026-11-13.

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
