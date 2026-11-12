# FireSky 桌面端 Tauri 迁移落地蓝图（Tauri 2 + Svelte + Vite）

> **【已退役（2026-11-13）】** Tauri 桌面壳按
> `docs/PYTHON_TAURI_PYSIDE_RETIREMENT_PLAN.zh-CN.md` 进入退役流程：
> 正式桌面为原生 UI + Rust FFI（macOS 先行），共享 Web 页面迁出 Tauri package
> 后由 Rust HTTP 托管。本文全部 M1–M4 计划、Tauri 发布目标与打包细节均为
> 历史记录，不再执行；执行记录见 `docs/RETIREMENT_PHASE0_BASELINE.zh-CN.md`。

> **架构覆盖（2026-11-12）**：本文是 Tauri/共享 Web 的历史蓝图和合同参考。
> 正式桌面路线已改为原生 UI + Rust FFI，先完成 macOS，再按同一 ABI 扩展
> Windows/Linux；Docker/Web 继续使用 Rust HTTP。本文中的“正式桌面端”、
> M1–M4 和 Tauri 发布目标不再是当前待办，冲突处以
> `docs/NATIVE_UI_FFI_MIGRATION.zh-CN.md` 为准。
> 状态：决策已确认蓝图（v10，2026-11-11）。当前 M2-E **保留现有 UI，迁入原 Web 的业务展示内容**；地图为 **天地图 + OpenFreeMap，默认天地图**。**完整 M1/M2、生产地图授权、原生 GUI 和安装包验收尚未完成**。下方分轮记录；目标设计不等于已实现功能清单。
> UI 方向（2026-11-11 用户确认）：**保留当前 Tauri / Swift 原稿的布局、导航与视觉，把原 Web 能展示的内容放入现有 UI**，不复制旧 Web 的设计。**地图云层叠加不再考虑**；保留地图选点、区域质量网格、拖绘与设置内切源。
> 依据：三份现状勘察（功能面 / IPC 契约 / 打包现状）+ 本会话两轮对仓库的实地复核（见 §1.4，其中 14–20 号为 v2 评审轮复核）。
> 历史交付决策：**Tauri 2 + Svelte + Vite 曾作为正式桌面端，Web 页面同时复用于 Docker**；
> 该决策已由 2026-11-12 的原生 UI + Rust FFI 决策覆盖。
> 后端直接使用 **Rust**，不再安排 Python sidecar 过渡或 PyInstaller 双交付；
> 旧 SwiftUI/WinUI 源码仅保留参考，旧 Python 产品入口在替代验收后移除。
> 本文下方早期勘察、Python sidecar 和 M1–M4 阶段计划保留为历史记录，
> 与最新范围冲突时以 Q01–Q20 和联合清单为准，不是当前实现状态或待办。

## 当前联合实施入口（2026-11-11）

详见 [Tauri、共享 Web 与 Rust 联合迁移清单](TAURI_RUST_JOINT_MIGRATION.zh-CN.md)。
T01–T08 将业务内容、动态剖面/时次、区域诊断与城市排名、数据准备、
缓存/历史报告、壳能力及六类目标发布与 Rust 剩余工作一起执行。
保留当前 Tauri UI，不另造 Docker 业务前端；桌面 IPC 与浏览器 HTTP 只做传输及能力适配。

---

## 内容迁移与桌面操作（M2-E，2026-11-11）

- 现有概览 / 大气剖面 / 诊断与时间窗 / 逐时台账不改版。概览补时间与周期、云场/颜色/压力族/相态/云属/演变与摘要；诊断补边界敏感度、几何窗、前方/本地云场量、评分与有符号修正、光学量、卫星/雷达/运动约束和来源。
- 原双模型质量趋势保留，附加六条云况/光路趋势与独立 AOD 图、时次读数和步进；逐时表保留原 Web 十项业务列及既有状态列。0、缺测、无效与单模型失败分开处理，不复制旧 Web 的缺测默认零。
- 模式剖面与卫星剖面读取真实内嵌矩阵/采样，显示热图、压力/温度层、地形/理论受光线及 ASL/AGL 目标信息；支持键盘逐格检查。
- 区域矩形拖绘接入成熟绘图库并同步四界，按 0.25° 外扩；选中格点与来源/精度补齐既有报告字段，缺失 CAMS 起报不推造。
- 加入原生菜单、单实例聚焦、关闭确认及受限报告/缓存打开。协作取消后等请求终态再退出；不声称取消确认即停止下载。无主窗口时正常放行退出以回收 sidecar。

仍需后续：动态时次剖面缺安全桌面 RPC 与源张量生命周期，当前只显示报告内嵌剖面；趋势与台账尚未共享选中时次。原生引擎路径编辑、旧配置迁移、原生定位权限、真实 GUI / Windows / 安装包验收未完成。**云图、可烧云/挡光云地图叠加及其逐帧播放已由用户排除，不再作为完成条件**。分项验证见 `apps/desktop-tauri/README.md` 的 M2-E 记录。

本轮验证：Node **80/80**、Chrome fixture UI **40/40**、Svelte **0 errors / 0 warnings**、顺序生产前端构建通过；桌面 Rust **18/18**，check / strict clippy / fmt 通过。390–1440px 明暗截图与零值/缺测、单模型失败、菜单事件、等待取消终态退出、真实鼠标/触屏拖绘均有 fixture 回归。未运行真实下载/预测或原生 GUI；MapLibre 主包/worker 仍有体积提示。

---

## 地图方案定案（M2-D，2026-11-11）

- 用户确认仅保留天地图与 OpenFreeMap，默认天地图；未配天地图 Key 时提示设置，不静默回退。
- 天地图继续使用已有影像 / 街道 / 地形图层；OpenFreeMap 使用官方矢量样式与 MapLibre GL Leaflet 桥，复用既有选点、完整气象网格和视图意图重放，移除 OSM 公共栅格及 Esri 服务入口。
- 来源/底图只在设置切换，非敏感偏好可持久化；天地图 Key 仍仅运行内存，不写入磁盘或日志。
- MapLibre worker 经 Vite 打包；免 Key 不等于免归属标注、无限服务保证或已完成原生安装包验收。

---

## 桌面细化与地图重选（M2-C，2026-11-11）

- 用户没有 Apple Developer 账号，取消 Tauri 的 Apple Maps：移除 SDK 加载器、地图组件、类型依赖和设置 Token/类型入口。既有 Swift 原生客户端不作删除。
- 现有 OSM 暂作开发默认，天地图 / Esri 继续保留在设置；不据此替用户确定最终供应商，不擅自接入高德、MapTiler 或 OpenFreeMap。
- 继续以 Swift 工作台结构为原稿，细化中性窗口/侧栏材质、分隔线、字重与控件层次，保留 FireSky 强调色和数据语义；CSS 材质不冒充原生 AppKit 材质。
- 地图切源仍只在设置；开发合成网格与技术状态改为 `?mapDebug=1` 显式启用，正常地图仍保留可操作的加载失败提示。
- 先向用户报告地图服务候选的账号、底图能力、坐标及服务条款边界，等待选择后再做正式接入、真实网络和授权验收。

验证：Node **50/50**、Chrome fixture UI **27/27**、Svelte **0 errors / 0 warnings**、生产前端构建通过。地图为离线对象/网格 fixture；真实服务网络、授权和原生 GUI 不在通过项中。

---

## 地图接入与真实样式核验（M2-D，2026-11-11）

- 设置页仅保留天地图与 OpenFreeMap，默认天地图；天地图的影像 / 矢量 / 地形选择仅在天地图来源下显示。
- OpenFreeMap 通过官方 `@maplibre/maplibre-gl-leaflet` 桥接到现有 Leaflet 交互，继续复用选点、网格、零值 / 缺测、聚焦、清除和视图意图重放。
- OpenFreeMap 真实 Chrome 网络核验覆盖浅色和深色样式：杭州道路、河流、中文标注、字体、精灵和矢量数据实际返回并渲染；未观察到页面错误。天地图真实 Key、Tauri 原生窗口、安装包和生产预测仍未验收。

本轮验证：Node **51/51**、Chrome fixture UI **29/29**、Svelte **0 errors / 0 warnings**、生产前端构建通过。新增无 WebGL 恢复与主题切换后点位 / 缩放保持回归，修正图层清理误删 Leaflet 自身解绑监听及移除后延迟回调；采用即时缩放避免弹窗隐藏留下半完成动画。构建报告有 MapLibre worker / 主包体积提示，未在本轮拆包。

---

## Swift 设计移植（M2-B，2026-11-11）

- 主壳恢复 Swift 的默认单点、四业务导航与底部设置；地图选点为弹窗，不再独立页面；顶栏恢复当前地点/用途与日期。
- 单点与实况共用 292px 地点/参数栏，含 176px 小地图、搜索、坐标/海拔/时区、收藏、日期/现象。右侧沿用摘要、九指标、概览/剖面/诊断/逐时台账；无数据为空值，不用示例数据填充。
- 区域页沿用顶部区域多选、日期/现象、自定义坐标，双模式摘要、GFS/EC/双图、完整网格、详情/排行。网格 0 与 null 区分，错误模式不标记成功；地图不截断，排行最多 2000 项。
- 设置按 Swift 外观/数据源/CAMS/本地引擎分组，插入用户指定的地图/地图凭据。EC 来源统一用于探测、数据准备和预测；CAMS 复用现有显式保存 RPC。地图 Token / Key 只在内存；主题、EC 来源、收藏为 WebView 本地存储，不自动与旧客户端互迁。
- 三类预测接入真实 check→prepare→forecast(confirmed:true)，单次 Token、请求快照、旧回包隔离和协作取消；自动测试仅 fixture 执行准备/预测，不运行生产下载。
- 数据页按 Swift 顺序重排：本地数据标题、下载探测、数据源库存与折叠周期；原有额外区域准备放末尾。

当轮尚未完整对齐的能力：卫星剖面专用热力渲染、区域地图拖绘范围、外部打开完整报告/缓存、原生引擎路径编辑、旧配置迁移、原生定位权限和完整原生菜单。前三项与原生菜单在后续 M2-E 已推进，当前边界以上方最新记录为准。浏览器 Geolocation 按钮不等于 macOS/Windows 原生定位已验收。

本轮验证：Node **60/60**、Chrome fixture UI **25/25**、Svelte **0 errors / 0 warnings**、生产前端构建通过；真实 Python hello 与隔离空缓存 inventory→check→dismiss 冒烟通过，未运行生产下载或预测。已有 debug 原生进程及 Python 子进程已启动，但 Computer Use 未识别开发窗口，原生交互验收仍未完成。

---

## 继续推进（M2-A，2026-11-11）

本轮在原有未提交骨架上增量实现：

- 数据与下载页：真实 IPC 库存清单（含 UTC 周期、大小、时效、范围、无索引文件），按区域/日期/事件检查双模型与 CAMS，显式核验发布、确认后准备数据、协作取消、网络下载探测。准备不会自动发起预测；取消确认不等于任务已经停止，界面等待终态后解锁。
- `DataSessionController`：请求参数快照、revision、失效令牌 dismiss、发布回包与准备竞争隔离、准备失败撤销 ready、重连失效和页内进度隔离。数据页隐藏后保留任务，不把切页当取消。
- protocol=1 增量透传可选 `message`（1024 字符上限）和严格布尔 `cache_hit`；真实缓存命中分支显式标记，两端旧客户端兼容缺省字段。不得通过 GFS 49% / EC 99% 推断缓存命中。
- 用户补充地图决策：默认 **MapKit JS**，并保留 **天地图 / OpenStreetMap / Esri**。**地图服务、底图类型、Token / Key 只在设置页修改，地图页面不放切换控件**。Swift 原生 MapKit 保留参考，不做原生 NSView 桥接。
- Apple 使用官方 major-6 CDN，脚本加载与授权初始化分离；共享选点和原始网格跨地图源重放，保留 0 与缺测。MapKit JS 标准/卫星/混合；其他源沿用 Leaflet。默认无 Token 不请求 Apple SDK，不把未配置状态当成功。
- Token / Key 暂只保存在 App 运行内存，不写 localStorage、工程、日志或磁盘；设置页不回显已保存值，不接受 .p8 私钥。Apple Token 的获取、域名/来源限制、配额、有效期以及生产 Tauri 自定义 origin 的可用性仍需真实验证。

边界：这不是 Qt 数据页全量对齐。按任意来源批量下载、缓存移出/打开目录、独立库存全局发布核验、CAMS 凭据编辑、配置持久化及完整预测面板仍待后续。真实下载由用户显式确认，本轮自动检查不请求生产 prepare/forecast 或 ADS 任务。

验证结果在本轮结束时同步至 `apps/desktop-tauri/README.md`；此前 M1-A 的 GUI 权限阻断记录保留，不用浏览器 fixture 替代原生验收。

---

## 本轮实现与验证记录（M1-A，2026-11-11）

实现位于 `apps/desktop-tauri/`，不是完整 M1/M2：

- 已落地 identifier、macOS 12.0、Windows WebView2 `offlineInstaller`；以 `tauri.conf.json` 为权威校验 npm/Cargo 及 lockfile 版本。
- IPC 保留兼容命令，新增有序 `rpc_start` / `rpc_wait`、同步 requestId 句柄、generation 隔离、生命周期通知与串行重连。stdout 真正有界至 1 MiB，非法/截断/超长帧失败连接；启动部分失败与阻塞 stdin 写入均不阻止有限宽限的直接子进程回收。取消仍为协作式，未承诺清理整个后代进程树。
- 地图具备对象 READY/错误/重试、配置重放、App 内存共享选点、三源切换、精确保留零值/缺测的网格 canvas 与聚焦/清除接口。DEV-only 合成网格与离线模式不冒充预测结果或瓦片网络成功。
- 主壳参考 `apps/macos/Sources/FireSkyNative/Workbench.swift`：196px 侧栏、72px 顶栏、图标导航、暖色低透明选中态、轻面板与渐变；这是跨平台 CSS 近似，不声称使用原生 Swift glassEffect。内容迁移继续参考旧 Web 的结果卡→指标→趋势→逐时表/剖面、区域地图/图例/缺测语义，本轮未迁移业务结果面板。

| 验证项 | 实际结果与边界 |
|---|---|
| `npm test` | 14/14：3 版本/元数据、5 IPC helper、6 网格；包括首次进度前取消顺序、零值/缺测、10,001 格不截断 |
| `npm run check` / `npm run build` | 最终源码通过，Svelte 0 errors / 0 warnings；生产前端构建成功，不是安装包 |
| 桌面 manifest `cargo test` / `cargo check` | 12/12 Rust 测试及 check 通过；含 EOF、快退、坏/超长帧、取消、旧 generation 隔离、启动中途失败、阻塞 stdin writer |
| 桌面 manifest `cargo clippy --all-targets -- -D warnings` | 通过；未运行并行引擎迁移的整仓测试 |
| `npm run smoke:rpc` | 真实 Python protocol=1 / engine=firesky / 10 regions；关闭 stdin 后退出码 0；无 prepare/forecast 或数据下载 |
| 既有 Python RPC 定向测试 | 2 passed，14 deselected；不是全量 pytest 或预测等价性验收 |
| 离线浏览器地图 | READY、三格 fixture、零值 `0.000`、null 缺测聚焦、OSM 后仍能切源已验证；初始化失败/重试最后通过受控 DOM 按钮点击验证，不算完整纯 GUI 测试。最终清除交互因自动化定位超时未实测，接口实现及类型检查通过 |
| 原生开发启动 | `tauri dev` 编译并启动 `firesky-desktop`，进程检查确认其 Python RPC 直接子进程存在；没有据此声称窗口交互通过 |
| 原生 GUI 验收 | **阻断**：Computer Use 不能读取窗口/辅助功能树，提示给 `ZCode Computer Use.app` 授予辅助功能权限并完整重启 ZCode。已停止 GUI 操作，不绕过权限，不以浏览器代替。原生握手状态、重连、跨页选点、网格交互、正常 UI 退出仍待验证；本轮自有 dev task 已停止 |

尚未验证真实瓦片加载/故障恢复、全部 pre-READY 配置组合、macOS 12 实机、Windows 离线安装、onefile/externalBin/ecCodes clean-machine 打包、真实预测全链路。未签名/发布/提交/推送，未删除旧客户端或清理未知进程/构建目录。

---

## 0. 问题定义

`docs/RUST_MIGRATION_STATUS.zh-CN.md:43`（P1「桌面完整运行时」）已明确：macOS/Windows 原生客户端目前启动 Python `firesky.desktop_rpc` 子进程，Qt 桌面本身也运行于 Python。仓库现状是**四套桌面形态并存**（PySide6+PyInstaller、SwiftUI、WinUI3、新建骨架的 Tauri），元数据漂移（bundle id `cn.firesky.desktop`+0.1.1 vs `app.firesky.native`+0.2.0，`desktop/FireSky.spec:135,139` vs `scripts/build_macos_native.sh:20,23`）。

本蓝图的目标：用**一套 Tauri 壳**统一桌面端，UI 全部收敛到 Svelte，后端分两阶段（Python sidecar → Rust 引擎），Qt 版冻结为兼容版直至退役。

---

## 1. 现状勘察摘要（三份勘察结论）

### 1.1 功能面勘察（Qt 桌面，desktop/）

- **形态**：PySide6 单窗口应用。`desktop/app.py:215` MainWindow 承载 QStackedWidget 四页（PAGES 定义于 `desktop/app.py:64`：单点预测 / 区域预测 / 实况观测 / 数据与下载），外加三个对话框：设置弹窗（`desktop/ui_datasource.py:17`）、数据准备弹窗（`desktop/data_dialog.py:65`）、地图选点浮层（`desktop/ui_map.py:60`）。
- **所有耗时任务经 QThread**：`desktop/workers.py:64` WorkerBase（progress/finished_ok/failed/cancelled 四信号）+ 7 个 worker（ForecastWorker `workers.py:117`、DataFetchWorker `workers.py:282`、SatelliteWarmupWorker `workers.py:351` 已定义但无面板调用）。worker 内直接 `import firesky.web_server` 权威路径——**同进程 Python import，这是 Tauri 化的结构性断点**。
- **引擎调用已有 Rust 分支但默认 Python**：desktop_data 六入口按 `FIRESKY_DESKTOP_DATA_BACKEND` 路由（`src/firesky/desktop_data.py:50,222,319,484,651,775`），nowcast 评分按 `FIRESKY_NOWCAST_SCORING_BACKEND` 路由；区域批处理编排（`run_and_cache_national_forecast_payload`）仍是纯 Python。
- **状态中枢**：SpotStore 全局观测点单写口 + changed 信号扇出（`desktop/widgets.py:229`），DEM 未回填哨兵 `elevation=-1`（`widgets.py:87` Location）。
- **共享会话契约**：ForecastDataPanel.begin() 深拷贝 payload + revision + is_current 守卫（`desktop/data_panel.py:81`），发布周期核验异步回填（`data_panel.py:187`）。
- **自绘体量大**：两张大气剖面画布 790 行（`desktop/profile_canvas.py:164,511`）、widgets.py 组件库 1067 行（QualityCurve/GlassCard/MetricCard/Toast 等）、theme.py 503 行 QSS + 十档配色 LEVEL_COLORS（`desktop/theme.py:13`）。
- **地图栈**：FireSkyMap = QWebEngineView + Leaflet（`desktop/web_map.py:217`），JS↔Python 靠 console 前缀桥（`_BridgePage.javaScriptConsoleMessage`，`web_map.py:193`）。**降级策略现状不一致**：`ui_regional.py:39` 无降级直接 import FireSkyMap，而 `ui_point.py:93`、`ui_map.py:43` 是 try/except 降级 MiniMap。
- **系统级耦合**：macOS 应用菜单 PreferencesRole/AboutRole（`app.py:417`）、关闭确认与线程收尾（`app.py:576`）、.app cwd 写探针（`app.py:596`）、系统定位（`desktop/location_controller.py:12`）、QtWebEngine 打包修复（`desktop/__init__.py:10`）。

### 1.2 IPC 契约勘察（desktop_rpc / apps 原生客户端 / engine）

- **协议**：私有 JSON-lines 子进程 IPC（无端口/HTTP/令牌），stdin/stdout UTF-8 单行帧，上限 1 MiB；请求 `{"id","method","params"}`，响应三型 result/error/progress，错误码三值 `invalid_request`/`operation_failed`/`cancelled`（`src/firesky/desktop_rpc.py:1` 模块 docstring、`:457-461`）。**stdout 只能是协议通道**：main() 把 sys.stdout 重定向到 stderr（`desktop_rpc.py:505-510`，本会话复核）。并发：ThreadPoolExecutor max_workers=4（`desktop_rpc.py:466`），compute_lock 单飞；取消为协作式（progress 回调点生效）。
- **方法集**：hello / search / metadata / inventory / network_probe / check / publications / prepare / forecast / dismiss / cancel / cams_credentials / save_cams_key（`desktop_rpc.py:146-278` dispatch，本会话复核）。hello 返回 `protocol:1, engine:"firesky"`（`:150`）。
- **两端客户端模式同构但退出语义粗糙**：启动→hello 握手校验 protocol==1→请求复用→退出即全失败（`apps/macos/Sources/FireSkyNative/EngineClient.swift:35`、`apps/windows/FireSky/EngineClient.cs:25`）；退出上两端**均无优雅等待**——Swift `stop()` 关 stdin 后立即 `terminate()`（`EngineClient.swift:183-192`），C# `Dispose()` 直接 `Kill(entireProcessTree:true)` 不关 stdin（`EngineClient.cs:185-196`）。**启动配方（本会话已实测）**：`cwd=<repo 根>，PYTHONPATH=src + PYTHONUNBUFFERED=1，<venv python> -u -m firesky.desktop_rpc`，hello 正常、EOF 退出码 0。
- **会话令牌是进程内存态**：1800s 过期、单次消费、≤32 个（`desktop_rpc.py:53-60,220-221`）——sidecar 必须长命，不能按请求拉起。
- **engine（Rust）三个入口**：`serve`（`engine/src/rpc.rs:858`，注释自述 *"Sequential migration/debug endpoint, not the production desktop service"*——单线程、无 progress、无 cancel，本会话复核）、`desktop-location [--config]`（`engine/src/main.rs:12-25`）。rpc.rs 已有 **72 个方法臂**，含 desktop_session/prepare/publications/inventory/location/nowcast 系列（`engine/src/rpc.rs:227-232`），但**没有** cams_credentials/save_cams_key/network_probe/search/metadata（grep 复核为空）。
- **硬约束**：ecCodes 原生库不随 pip wheel 打包（实测 wheel 内无 dylib，运行时靠 brew 的 libeccodes）；WebServerConfig 缓存目录按 cwd 相对解析（`src/firesky/web_server.py:309-317`）；CDSAPI_KEY 环境变量存在时 save_cams_key 拒绝写文件（`desktop_credentials.py:70-71`）；Windows 契约从未在 Windows 实机验证（`apps/README.md:122-125` 自述）。

### 1.3 打包现状勘察

- **Qt 版**：PyInstaller onedir（`desktop/FireSky.spec`），macOS BUNDLE `cn.firesky.desktop` / 0.1.1 / LSMinimumSystemVersion 12.0 / 定位用途声明（`FireSky.spec:135-144`，本会话复核）；实测产物 dist/FireSky.app 为 adhoc 签名。**已发现两个现存缺陷**：① Windows 分支引用的 `desktop/icon.ico` 不存在（`FireSky.spec:115`，ls 复核）；② 包内 dylib 落在 `Contents/Frameworks/native/`，而 `native_core.py:273,277` 候选路径对不上且无任何地方设 `FIRESKY_NATIVE_LIBRARY_PATH`（grep 复核）——推断打包后原生评分器静默回退。
- **CI**：`ci.yml` desktop job（matrix macos/windows，PyInstaller + hdiutil，无签名/公证）；`native-desktop.yml`（未提交）仅 SwiftUI/WinUI 编译冒烟。Makefile 无 PyInstaller/DMG 目标。
- **Tauri 需要的现成资产**：`desktop/assets/firesky.icns`（ic04-ic14 全尺寸）可直接复用；`desktop/assets/firesky.png` 实测 1024×1024 RGBA，是 `tauri icon` 合格源图；**地图页基底是 `desktop/assets/map.html`（709 行）而非 `apps/shared/map.html`（56 行）**——后者 grep `set_grid|tianditu|esri` 为 0，只是 OSM 演示页（随 WinUI 分发）；前者已含 11 个 fs* API（`fsInit:572`、`fsSetMini:565`、`fsSetTheme:585`、`fsSetSource:590`、`fsSetView:591`、`fsSetName:598`、`fsSetBase:602`、`fsFitBounds:603`、`fsInvalidate:606`、`fsSetGrid:678`、`fsFocusGridCell:694`）、三供应商切换与境外回退（:333-465）、天地图 Key 缺失引导（:244-254,391-392,461）、瓦片/JS 错误上报（:320-328），仅 console 桥事件（`FIRESKY_*` 于 :323,328,479,511,553，另含 :706 的 `FIRESKY_READY` 就绪握手）需替换；`src/firesky/desktop_rpc.py` 就是现成协议层。

### 1.4 本会话复核记录

| # | 事实 | 验证方式 |
|---|------|----------|
| 1 | frontend/ 是 SvelteKit 5（svelte ^5.28）+ Vite 6 + adapter-static + Leaflet 1.9 + shadcn-svelte + Tailwind 4，路由组 (app)/forecast、(app)/now、(app)/outlook | `ls frontend/src/routes`、读 `frontend/package.json` |
| 2 | 勘察开始时无 Tauri 脚手架；后续工作流已生成 `apps/desktop-tauri/`，cargo check 与 vite build 通过，尚未 GUI 实启 | 初始 find 记录 + 工作流构建门禁；编译通过不等于 M0/M1 全部验收 |
| 3 | bundle id/版本漂移：cn.firesky.desktop+0.1.1（`FireSky.spec:135,139`）vs app.firesky.native+0.2.0（`scripts/build_macos_native.sh:20,23`）vs pyproject 0.1.0 | grep 复核 |
| 4 | desktop_rpc 方法名与 hello capabilities（point/regional/nowcast/inventory/network_probe） | grep `desktop_rpc.py:146-278` |
| 5 | desktop_rpc main() stdout 重定向 + PYTHONUNBUFFERED 兜底 | 读 `desktop_rpc.py:505-510` |
| 6 | ThreadPoolExecutor max_workers=4、result/error/cancelled 帧语义 | 读 `desktop_rpc.py:444-470` |
| 7 | engine `serve` 为顺序迁移/调试端点（无并发/progress/cancel） | 读 `engine/src/rpc.rs:857-872` |
| 8 | engine hello 72 能力含 desktop_* 系列，但无 cams_credentials/save_cams_key/network_probe | grep `engine/src/rpc.rs` |
| 9 | `engine/target/release/firesky-engine` 本机存在；rust_grid.py 默认按 `<repo>/engine/target/release` 解析、可用 FIRESKY_RUST_ENGINE_BIN 覆盖 | ls + grep `rust_grid.py:21-24` |
| 10 | `desktop/icon.ico` 缺失（FireSky.spec:115 引用）；`desktop/ui_map.py` 存在；`desktop/assets/firesky.png` 1024×1024 | ls / sips |
| 11 | ui_regional.py:39 无降级 import FireSkyMap；ui_point.py:93 / ui_map.py:43 有 try/except | grep 复核 |
| 12 | Qt 各组件行号锚点（app.py:64,215,576,596；workers.py:64,117,209,282,351；theme.py:13；widgets.py:87,229；web_map.py:182,193,217；data_panel.py:20,81,187；data_dialog.py:65；profile_canvas.py:164,511；__init__.py:10） | grep 复核 |
| 13 | RUST_MIGRATION_STATUS P1 三缺口（实况数据生命周期 / HTTP+CLI / 桌面完整运行时） | grep `docs/RUST_MIGRATION_STATUS.zh-CN.md:41-43` |
| 14 | 进度契约三条链：pair 映射 `index*50+percent//2`（`workers.py:34`）且 desktop_rpc 服务端复刻同款（`desktop_rpc.py:367-370,400-403`）；progress 帧剥 message 只留 percent/stage/source（`desktop_rpc.py:444-445`）；nowcast 两段式映射只挂 Qt 单模型/nowcast 路径（`workers.py:198-201,209-243`） | 读代码复核（v2 评审轮） |
| 15 | 区域缓存命中判据 `stage==cache && percent==99` + message 日志（`ui_regional.py:448-451`）——pair 映射下 GFS 命中=49、EC 命中=99，且 message 不在 IPC 帧 | 读代码复核（v2 评审轮） |
| 16 | `apps/shared/map.html` 56 行、grep `set_grid\|tianditu\|esri` 为 0；`desktop/assets/map.html` 709 行、11 个 fs* API（`fsInit:572`…`fsSetGrid:678`、`fsFocusGridCell:694`）、console 桥含 :323,328,479,511,553 及 :706 的 `FIRESKY_READY` | wc -l / grep 复核（v2 评审轮） |
| 17 | nowcast 评分后端默认 "rust"（`national_batch.py:6903-6906`）、启用即 return 无 Python 兜底（`:6786-6787`）；`rust_grid.engine_binary()` 缺二进制直接 raise（`rust_grid.py:25-27`）；native 库加载失败返回 None 走上层回退（`native_core.py:318-319`） | 读代码复核（v2 评审轮） |
| 18 | engine hello `"engine":"firesky-rust"`（`rpc.rs:210`）vs Python `"firesky"`（`desktop_rpc.py:150`）；`desktop_prepare` 入参带请求文件 `path`（`desktop_prepare.rs:20-27`）；引擎 forecast 仅 `*_to_files` 端点（`rpc.rs:385-397`） | 读代码复核（v2 评审轮） |
| 19 | 两端退出均无优雅等待：Swift 关 stdin 即 terminate（`EngineClient.swift:183-192`）、C# 直接 `Kill(entireProcessTree:true)` 不关 stdin（`EngineClient.cs:185-196`）；Windows 日志目录 LocalApplicationData（`EngineClient.cs:66`） | 读代码复核（v2 评审轮） |
| 20 | Tauri geolocation 插件平台表含桌面（windows/linux/macos）但安装/权限示例全移动端向：`cargo add --target 'cfg(any(target_os="android",target_os="ios"))'`、`#[cfg(mobile)]`、无桌面权限配置章节 | WebFetch `v2.tauri.app/plugin/geolocation`（v2 评审轮） |

**未验证项（如实声明）**：未实际运行 desktop_rpc 完整会话（check→prepare→forecast 往返）；未跑 PyInstaller 打包产物；GitHub CI 实际运行状态未查（gh 未登录）；apps/windows 未在 Windows 实机验证。以上引用自勘察材料。

---

## 2. 目标架构

### 2.1 架构图

```
┌────────────────────────────────────────────────────────────┐
│ Tauri 2 壳（Rust, src-tauri/）                              │
│  窗口/菜单/快捷键(F5·Esc·Ctrl+K) / 单实例(M0 验收8) /         │
│  更新器(签名体系就绪后启用，见 Q6)                            │
│  sidecar.rs: 长命子进程管理(spawn/stdin关闭/SIGTERM/failAll) │
│  ipc.rs: JSON-lines 帧读写(1MiB上限) + cancel 帧 + 事件转发   │
│  commands.rs: invoke 命令 → 转发 sidecar / 本地能力          │
└───────────────┬────────────────────────────┬───────────────┘
                │ Tauri events/commands       │ spawn
┌───────────────▼───────────────┐   ┌────────▼─────────────────────────┐
│ Svelte 5 + Vite 前端          │   │ M1–M2: <python> -u -m            │
│  四页 + 三对话框 + 组件库      │   │   firesky.desktop_rpc (Python)   │
│  lib/ipc/engineClient.ts      │◄──┤   （M1 复用，M2 最小增强）         │
│  （invoke → listen(progress)）│   │ M3:   firesky-engine desktop*      │
│  stores: spot/settings/       │   │   （Rust 生产桌面服务模式）        │
│  dataSession/theme            │   └────────┬─────────────────────────┘
└───────────────┬──────────────────┘            │ 按需拉起
                │ 静态资源                       │
┌───────────────▼───────────────┐   ┌────────▼────────┐
│ 天地图 / OpenFreeMap          │   │ engine/src/*    │
│ Leaflet + MapLibre 适配层     │   │ desktop_* Rust  │
└───────────────────────────────┘   └─────────────────┘
```

\* M3 的 `serve` 现状是顺序迁移端点（`engine/src/rpc.rs:857-872`），切引擎前必须补生产桌面服务模式（见 M3 范围）。

### 2.2 技术选型

| 层 | 选型 | 理由 |
|----|------|------|
| 壳 | Tauri 2（stable，macOS/Windows 桌面；mobile API 暂不启用） | 已定决策；系统 WebView 免 QtWebEngine 302MB 包体 |
| 前端 | Svelte 5 + Vite（**非 SvelteKit**，纯 SPA） | 桌面四页与 Web 版页面结构差异大，frontend/ 带鉴权/console/admin 包袱；但口径常量、地图与剖面组件作为移植参照 |
| UI 基座 | Tailwind CSS 4 + 少量 shadcn-svelte 组件 | 与 frontend/ 同栈，降低团队切换成本 |
| 地图 | 默认天地图；Leaflet 1.9 承载天地图栅格与 OpenFreeMap 的 MapLibre 矢量桥 | 地图服务、底图类型和凭据只在设置页修改。复用 `desktop/assets/map.html` 的选点/网格语义，不再将其整体作为页面打包；OpenFreeMap 使用官方样式与归属标注，真实天地图 Key、原生 GUI 与安装包行为待验 |
| IPC | 复用 desktop_rpc JSON-lines 协议，Rust 侧读写帧 → `tauri::Emitter` 推 progress 事件 → 前端 listen | M1 协议不变；M2 已接受 message/cache_hit 最小增强并同步客户端（§3.4） |
| 状态 | Svelte stores 显式替代 Qt 信号扇出（SpotStore→writable store；主题/地图配置→派生 store） | 替代 findChildren 广播（`app.py:526,554`）的隐式耦合 |
| 持久化 | tauri-plugin-store（配置 JSON 0600）+ fs plugin；收藏 locations.json 保持现有路径与损坏留档恢复语义 | 对齐 `widgets.py:158-226` 契约 |

### 2.3 目录规划（已定 `apps/desktop-tauri/`，与现有骨架一致）

以下为目标模块划分，非当前骨架完成清单。工程是 Vite SPA，`routes/` 仅为可选组件分组，不采用 SvelteKit 的 `+page.svelte` 文件路由；当前入口与视图切换以 `src/App.svelte` 为准。

```
apps/desktop-tauri/
├── package.json  vite.config.ts  svelte.config.js  tsconfig.json
├── src/                          # Svelte 前端
│   ├── App.svelte  routes/（point | regional | nowcast | data 四页）
│   ├── lib/ipc/engineClient.ts   # 唯一 IPC 封装（invoke + listen）
│   ├── lib/ipc/types.ts          # 帧类型 / report 类型（对齐 desktop_rpc）
│   ├── lib/stores/{spot,settings,dataSession,theme}.ts
│   ├── lib/components/{map,profile,charts,widgets,data}/
│   └── lib/views/{MapView,LeafletMapView}.svelte
├── src-tauri/
│   ├── tauri.conf.json           # identifier cn.firesky.desktop、版本单一来源
│   ├── Cargo.toml  build.rs
│   └── src/{main.rs, lib.rs, sidecar.rs, ipc.rs, commands.rs, menu.rs}
└── icons/                        # pnpm tauri icon desktop/assets/firesky.png 生成
```

### 2.4 复用清单（不重写的东西）

| 资产 | 用途 |
|------|------|
| `src/firesky/desktop_rpc.py` | M1–M2 过渡 sidecar；M1 协议不变，M2 补 message/cache_hit |
| `desktop/assets/map.html` | Leaflet 适配层的选点、网格、就绪和错误语义参考；Svelte 直接承载实现，不平移 console 桥。地图源/底图/Key 入口迁入设置；不静默替换用户选择的地图服务。`apps/shared/map.html` 仍仅作早期参考 |
| `desktop/assets/firesky.png/.icns` | tauri icon 源图 / macOS 图标 |
| `desktop/theme.py:13` 的 `LEVEL_COLORS` | 十档颜色的迁移基准；生成 Tauri `level-colors.ts`，建立 Qt↔Tauri 逐色一致性断言。`frontend/src/lib/utils/constants.ts` 不含该色表，不可作为颜色来源 |
| `frontend/src/lib/utils/{constants,cloud-render,geo,format}.ts` 与 `frontend/src/lib/components/forecast/MetricsGrid.svelte` | 按实际导出复用地图选项、云渲染、地理与格式化工具；云属中文映射部分内联在 `MetricsGrid.svelte`，需逐项核对，不能假定一个 constants 文件包含全部档位/云属/趋势/场景映射 |
| `frontend/src/lib/components/map/`、剖面组件（NationalCrossSection / SatelliteVerticalProfile） | M2 平移剖面与地图的参照实现 |
| `desktop/assets/icons/` Lucide SVG | 前端改用 `@lucide/svelte`（frontend 已用），主题色经 CSS 变量 |
| `engine/target/release/firesky-engine` | M3 目标后端（本机已有 10.4MB 构建） |

---

## 3. IPC 设计（M1 的落地细节）

### 3.1 sidecar 生命周期（src-tauri/sidecar.rs，对齐 apps/ 两端客户端模式）

- **开发模式**：解析 workspace（`FIRESKY_WORKSPACE` 环境变量 → 探测 `<repo>/.venv`），spawn `<venv-python> -u -m firesky.desktop_rpc`，cwd=workspace 根，env `PYTHONPATH=<workspace>/src + PYTHONUNBUFFERED=1`（Windows 另加 `PYTHONIOENCODING=utf-8`）。启动前校验 `pyproject.toml` 存在 + python 可执行（同 `EngineClient.swift:24-29`）。
- **打包模式（M2 末引入，Q4 已定）**：过渡 sidecar 为 PyInstaller onefile 可执行 `firesky-desktop-rpc-<target-triple>`（tauri externalBin 命名约定），封装 `firesky.desktop_rpc` 入口与 Python 运行时，M3 桌面链路切 Rust 后退出生产路径；cwd 显式设为用户数据目录（见 §5 R7）。**打包必配环境变量清单**：缓存根（覆盖 WebServerConfig 相对路径，R7）+ 随包分发 `firesky-engine` 并将 `FIRESKY_RUST_ENGINE_BIN` 指向安装后的实际二进制路径，保持 nowcast Rust 评分，不强制 Python 评分（默认 rust 且无 Python 兜底，`national_batch.py:6903-6906,6786-6787`；`rust_grid.py:25-27` 缺二进制直接 raise）+ 可选 `FIRESKY_NATIVE_LIBRARY_PATH`（R9-②）+ Windows 另加 `PYTHONIOENCODING=utf-8`。ecCodes 原生库、definitions 与再分发许可仍需装配验证，不能由 onefile 选择推定已解决。
- **退出顺序**（比 apps 两端现状更强——两端均无优雅等待：Swift `stop()` 关 stdin 后立即 `terminate()` 无等待（`EngineClient.swift:183-192`），C# `Dispose()` 直接 `Kill(entireProcessTree:true)` 不关 stdin（`EngineClient.cs:185-196`），照抄任一端都会丢优雅取消）：前端退出 → 等在途请求完成（可配置超时）→ 关 stdin 等 EOF 清理（`desktop_rpc.py:501-503`）→ 超时 SIGTERM（Windows `Kill(entireProcessTree)`）。进程退出事件 → 广播失败给全部 pending（failAll，"引擎已退出"）。
- **存活判断**：无心跳帧（协议现状），仅靠进程退出事件 + 写入失败；M3 可加 ping（Q10 已接受；实现前仍无心跳帧）。

### 3.2 帧桥（src-tauri/ipc.rs）

- 读线程按 `\n` 切帧（1 MiB 上限），解析三种响应：`result`/`error` resolve 对应 pending；`progress` → `app.emit("engine://progress", {requestId, event})`。
- 写侧：`{"id":uuid,"method","params"}` + `\n`；cancel 帧 `{"method":"cancel","params":{"request_id"}}` 由前端取消命令触发（映射到 Tauri command `engine_cancel`）。
- id 用 UUID；重复 id 拒绝；活跃请求计入服务端 ≤16 上限（`desktop_rpc.py:493-494`），前端排队。

### 3.3 前端封装（lib/ipc/engineClient.ts，单例）

- `connect()`：hello → 校验 `protocol===1`；`engine` 期望值**按后端注入而非编译期常量**——Python sidecar 校验 `==="firesky"`（`desktop_rpc.py:150`），M3 Rust 后端校验 `==="firesky-rust"`（`rpc.rs:210`）；期望值来自 engineStatus 配置，保证 M3 切换/回滚（`FIRESKY_TAURI_BACKEND`）不需改编译产物；通过后广播 capabilities/cams_credentials/workspace。
- 方法类型化：hello/search/metadata/inventory/network_probe/check/publications/prepare/forecast/dismiss/camsCredentials/saveCamsKey/cancel。
- **会话状态机照抄**：check → publications（异步核验，不占操作锁）→ prepare → forecast（`confirmed:true` + ready 报告 + 令牌单次消费），对应 `data_panel.py:81` 的 revision/is_current 守卫在前端用 `dataSession` store 重放。

### 3.4 进度与取消语义（前端按链区分；M1 不改协议）

progress 帧经服务端清洗只保留 `{percent, stage, source}`（`safe_event`，`desktop_rpc.py:444-445`，**message 被剥离**；network_probe 另带 `probe`，`desktop_rpc.py:446-451`）。三条链的服务端事件形态不同，前端**不能一刀切套两段式映射**：

| 链 | 服务端事件形态 | 前端处理 |
|----|----------------|----------|
| nowcast | 引擎原始 stage/percent 直传（`desktop_rpc.py:360-362`，无 pair 合成、无两段式映射） | 前端复刻 Qt 版两段式映射：下载 2–60% / 评分 62–100%（挂钩点 `workers.py:198-201`、映射与 download/score 分界 `workers.py:209-243`） |
| point / regional（GFS+EC 双模型） | 服务端已复刻 pair 映射 `percent = index*50 + percent//2`（`desktop_rpc.py:367-370,400-403`；同款在 `workers.py:23-36`）：GFS 相 0–50、EC 相 50–100，**已是 0–100 连续值** | **直读，不得二次映射**（再套 2–60/62–100 会双重映射）；按 `source` 分相显示「GFS/EC」 |
| network_probe | percent 无意义，`probe` 字段透传（`desktop_rpc.py:446-451`） | 渲染探测结果卡片，不走百分比 |

- 心跳：8 秒无事件发日志、不伪造百分比（`workers.py:176-196` 语义移入前端）。
- 取消为协作式：发 cancel 帧后进度回调点才生效；下载轮询超时（`workers.py:321,347`）语义等价实现。
- 错误三码 `invalid_request`/`operation_failed`/`cancelled` 直接透传 UI；EOF 时的静默改判 cancelled（勘察 blockers#10）接受该语义，退出清理先等在途完成再关 stdin（可配置）。
- **区域缓存命中识别（M2 落地，Q10 已接受）**：Qt 判据 `stage=="cache" && percent==99` + message 日志（`ui_regional.py:448-451`）在 IPC 下两处失效——pair 映射后 GFS 命中=49、EC 命中=99，且现状 message 不在帧内。采用协议最小增强：desktop_rpc `safe_event` 补 `message` 与显式 `cache_hit: bool` 透传，生产事件源同步提供准确命中标志，不能仅凭映射后的百分比推断缓存命中。M1 保持协议不变，M2 同步 `apps/README.md` 与两端客户端兼容处理；M3 可增加 ping，但未实现前不得假定存在心跳。

---

## 4. 里程碑

### M0 — Tauri 骨架（无引擎、无 IPC）

**目标**：Tauri 2 + Svelte 5 + Vite 工程落地并进主干 CI：四页空壳可导航、亮暗主题 + 十档配色与 Qt 逐色对齐、菜单/快捷键骨架、图标与元数据统一，双平台出可安装冒烟产物。仅本里程碑的纯 UI 骨架不依赖 Python；M1–M2 的过渡产品仍含 Python sidecar，不能称为已去 Python。

**涉及文件**（全部新建，另少量改动现有文件）：
- `apps/desktop-tauri/`：`package.json`、`vite.config.ts`、`svelte.config.js`、`tsconfig.json`、`src/App.svelte`、`src/views/{Point,Regional,Nowcast,Data}View.svelte`（空壳）、`src/lib/stores/theme.ts`、`src/app.css`
- `apps/desktop-tauri/src-tauri/`：`tauri.conf.json`（identifier `cn.firesky.desktop`；版本以本文件为单一来源；macOS `minimumSystemVersion: "12.0"`；Windows `webviewInstallMode: { "type": "offlineInstaller" }`，不是 embedBootstrapper；初期未签名，不启用公证或正式更新分发）；macOS `NSLocationWhenInUseUsageDescription` 通过 Tauri 的 Info.plist 合并配置迁移（对齐 `FireSky.spec:142`，服务 R14），不假定它是 tauri.conf.json 的任意顶层字段；`Cargo.toml`（含 `tauri-plugin-single-instance`）、`src/{main.rs,lib.rs,menu.rs}`（应用菜单：设置⌘,/关于/视图/任务，对齐 `app.py:417` 的角色归并；单实例插件初始化）、`icons/`
- `apps/desktop-tauri/src/lib/theme/level-colors.ts`：从 `desktop/theme.py:13` 的 LEVEL_COLORS 迁移十档配色，建立 Qt↔Tauri 双方逐色断言；frontend 的 constants 不含该表，中文标签另按实际组件核对
- `.github/workflows/tauri-desktop.yml`：新工作流（tauri-action 双平台，Rust 工具链 + pnpm 缓存；签名 secret 预留不启用）
- 修改：`README.md` 桌面章节加 Tauri 开发说明；`.gitignore`

**验收标准**：
1. `pnpm tauri dev` 打开窗口：侧栏四页导航（Ctrl+1..4）可用，当前页高亮；
2. 亮暗切换即时生效且十档配色与 Qt 版逐色一致（人工对照 + 单测断言与 `theme.py:13` 同值）；
3. macOS 应用菜单出现 设置⌘,/关于；F5/Esc/Ctrl+K 快捷键路由到当前页（M0 阶段打印占位事件）；
4. `svelte-check` 通过、`cargo clippy -- -D warnings` 通过；
5. CI 双平台 build 通过并上传产物；macOS 产物 Info.plist 的 identifier/版本与决策一致（PlistBuddy 断言）；
6. 图标全套由 `pnpm tauri icon desktop/assets/firesky.png` 生成入库（含 .ico）；随后**显式复制 `apps/desktop-tauri/icons/icon.ico` → `desktop/icon.ico`**——`FireSky.spec:115` 引用的是后一路径，`tauri icon` 产出不会自动落到那里，不复制则 Qt 兼容版 Windows 打包断链仍在（本会话 ls 实测 `desktop/icon.ico` 不存在）；
7. 仓库内 `rg "app.firesky.native|0\.2\.0"` 不再出现在新生成的 Tauri 元数据中（漂移收敛）；
8. 单实例：应用已运行时二次启动聚焦既有窗口、**不拉起第二个 sidecar 进程**（防双开抢缓存目录与会话令牌——会话是进程内存态，`desktop_rpc.py:53-60,220-221`）。

### M1 — sidecar IPC 接通 + 地图页

**目标**：Rust 壳长命拉起 Python `desktop_rpc`，协议桥全通（握手/进度/取消/三帧错误）；天地图栅格与 OpenFreeMap 矢量地图适配在系统 WebView 下工作（选点落库 + 区域热力 + 设置页底图/Key 切换）；端到端跑通一次真实 GFS 单点预测。此里程碑后，Tauri 版具备「能用的单点预测」。

**涉及文件**：
- `apps/desktop-tauri/src-tauri/src/sidecar.rs`：workspace/python 解析、spawn、stderr→`~/Library/Application Support/FireSky/engine.log`（Windows `%LOCALAPPDATA%\FireSky\engine.log`，对齐 `EngineClient.cs:66` LocalApplicationData）、退出 failAll（§3.1）
- `apps/desktop-tauri/src-tauri/src/ipc.rs`：帧读写、pending 表、progress 事件转发（§3.2）
- `apps/desktop-tauri/src-tauri/src/commands.rs`：`engine_request`/`engine_cancel`/`engine_status` commands
- `apps/desktop-tauri/src/lib/ipc/{engineClient.ts,types.ts}`：握手校验 protocol==1、方法封装、progress listen（§3.3）
- `apps/desktop-tauri/src/lib/stores/{spot,dataSession}.ts`：SpotStore 等价物（单写口 + elevation=-1 哨兵拦截；locations.json 持久化保留 `elevation_source: manual|dem`，不能只保存海拔数值）+ check→publications→prepare→forecast 状态机
- `apps/desktop-tauri/src/map.html` + `src/lib/components/map/MapCanvas.svelte`：**基底 = `desktop/assets/map.html`（709 行，能力齐全，勿从零重造约 600 行已有逻辑）**——移植改动只有两处：① console 前缀桥（`FIRESKY_PICK/SOURCE/BASE/TILEERROR/JSERROR/READY`，`map.html:323,328,479,511,553,706`；READY 就绪握手必须保留）换成 window.postMessage 或 Svelte 直调；② Key/主题/mini 注入走 `fsInit/fsSetTheme/fsSetSource/fsSetMini`（`map.html:565-606`）。`apps/shared/map.html`（56 行演示页）不作基底；
- `apps/desktop-tauri/src/views/PointView.svelte`（M1 半成品：观测点选择 + 运行 + 进度条 + 结果 JSON 卡片，完整面板留给 M2）
- `apps/desktop-tauri/src-tauri/tauri.conf.json`：externalBin（开发模式不打包，仅打包模式注册）
- 脚本 `scripts/smoke_desktop_rpc.sh`（或 .ps1）：封装本会话已验证的最小启动配方（cwd=repo 根、PYTHONPATH=src、`-u -m firesky.desktop_rpc`、printf hello | 断言 protocol==1、EOF 退出码 0），CI 可复用
- **前置任务 T-W1**：`scripts/setup_windows_eccodes.ps1` + 文档——Windows dev 机采用 **conda-forge ecCodes**，验证 DLL、definitions、Python 绑定与 sidecar 所用解释器环境一致；brew 仅覆盖 macOS。安装包内再分发是另一层工程任务（见 R7），不能让最终用户依赖开发机 conda 环境；落地前 Windows 验收走 M1 验收 7 的必过档

**验收标准**：
1. `scripts/smoke_desktop_rpc.sh` 通过（hello 返回 protocol==1, engine=="firesky"，EOF EXIT=0）——与本次勘察实测配方一致；
2. UI 内完成 hello 握手 → check → prepare → forecast(confirmed) 全链路，产出与 Qt 版同输入同结构的预测 payload（JSON 字段级 diff 快照测试，选固定观测点+固定日期）；
3. 进度语义按链验证（§3.4）：nowcast 进度条呈两段式（前端映射下载 2–60 / 评分 62–100）；单点预测（GFS+EC 双模型）进度条**直读服务端 pair 映射值** 0–100 连续推进（GFS 相 0–50、EC 相 50–100），无二次映射；运行中点取消 → 服务端报 `cancelled`，UI 状态复位；
4. 杀死 sidecar 进程 → UI 全部在途请求立即失败并提示「引擎已退出」，重启按钮可恢复；
5. 地图适配层对齐 `desktop/assets/map.html` 的选点/网格语义：设置页切换天地图/OpenFreeMap 及底图；凭据缺失引导至设置，不静默换源；单击落点 → SpotStore 写入（含 DEM 700ms 防抖查询，`ui_map.py:76-79` 契约）、收藏列表读写（损坏 JSON 留档恢复，`widgets.py:165`）；
6. 区域热力冒烟：区域预测 API 结果 `fsSetGrid` 渲染 + 缺测点样式 + `fsFocusGridCell` 点选联动（对齐 `ui_regional.py:539` 行为）；
7. macOS（WKWebView）通过 1–6；Windows（WebView2）**分两档验收**：必过档 = 1、4、5、8（握手/进程退出/地图/帧界）+ network_probe；全链路档（2、3、6，含 GRIB 解码）以前置任务 T-W1（Windows dev 机 GRIB 依赖方案，§5 R8）落地为前提，未落地前 Windows 显式降级为必过档并在 CI 标注；
8. 帧界安全：sidecar stderr 输出乱码/超长行不影响 UI（1 MiB 上限拒绝路径实测）。

### M2 — 逐面板平移（核心工作量）

**目标**：按映射表把 Qt 四页 + 三对话框 + 所需自绘组件平移为 Svelte；`workers.py` 的 QThread 编排全部改为「前端状态机 + engineClient」；**隐性契约逐项建等价测试**。已定顺序：**数据页 → 实况 → 区域 → 单点右栏完整面板（剖面最后）**；MiniMap 明确不平移，保留 WebView 初始化失败错误态。期间 Qt 版不删并行出包；M2 完成后 SwiftUI/WinUI3 冻结为参考实现，M4 与 Qt 一起退役。

**逐面板映射表**：

| Qt 现状（源） | Tauri/Svelte 目标 | 平移要点（契约锚点） |
|---|---|---|
| MainWindow/AppShell/NavButton/BrandMark（`desktop/app.py:215`，PAGES `:64`） | `src/App.svelte` + 侧栏组件 + `src-tauri/src/menu.rs` | 菜单角色归并、关闭前下载确认（`app.py:576`）→ Tauri 关闭事件 + dialog 插件；cwd 写探针（`app.py:596`）→ 打包模式改用 app 数据目录，开发模式保留探针 |
| 单点预测页 PointForecastTab（`desktop/ui_point.py:100`） | `src/routes/point/`（左栏观测点/参数/执行区，右栏结果标签组） | DEM 哨兵 -1 提交前拦截（`ui_point.py:1204`）；结果档位/云属/趋势中文表按 Qt 与 frontend 实际组件逐项核对，不假定都在 frontend 常量文件；HTML 报告入口（`ui_point.py:1477`）→ opener 插件 |
| 区域预测页 RegionalForecastTab（`desktop/ui_regional.py:63`，REGION 码 `:44-53`） | `src/routes/regional/` | 7 预置 + 自定义 bbox 校验（`ui_regional.py:385-405`）；区域编排仍在 Python sidecar（M3 前不变）；**缓存命中识别重做**：Qt 判据 `stage==cache && percent==99`（`ui_regional.py:448-451`）在 IPC 下失效——pair 映射后 GFS 命中=49/EC 命中=99 且 message 被剥离（`desktop_rpc.py:444-445`），按 §3.4 已确认的最小增强方案落地（协议最小增强补 `message`/`cache_hit`）；**降级不一致顺手修**：Tauri 版地图常驻，不再需要 MiniMap 降级分支（`ui_regional.py:39` 的问题就此消解） |
| 实况观测页 NowcastTab（`desktop/ui_nowcast.py:51`） | `src/routes/nowcast/` | HSD 卫星帧行进数据清单；参数变更徽章（`ui_nowcast.py:347`）；共享观测点点位条与单点页同 store |
| 数据与下载页 DataSourceTab（`desktop/ui_data.py:22`） | `src/routes/data/` | published_cycles 5 分钟缓存（`ui_data.py:176`）；历史日期拦截（`ui_data.py:286`）；清理 2 天前缓存 job；打开缓存目录 → opener 插件（对齐 `ui_data.py:246`） |
| 设置弹窗 SettingsPanel/Dialog（`desktop/ui_datasource.py:17`） | `src/routes/` 外的 `SettingsDialog.svelte` + menu.rs 触发 | CAMS Key 在 M2 暂写 `~/.cdsapirc` 0600（`ui_datasource.py:95-118`）作为过渡契约；Q5 已定 M3 迁平台钥匙串并同步 Rust 读取；天地图 Key 校验保存 + settings store 广播替代 mapSettingsChanged 信号 |
| 数据准备会话 ForecastDataPanel（`desktop/data_panel.py:20`，begin `:81`） | `src/lib/stores/dataSession.ts` + `src/lib/services/preparation.ts` | payload 深拷贝 + revision + is_current 守卫（`data_panel.py:94-96,158-168`）；发布核验异步回填（`:187`）；下载后按已下载计划复检 |
| 数据准备弹窗 ForecastDataDialog（`desktop/data_dialog.py:65`） | `src/lib/components/data/PreparationDialog.svelte` | 状态三态（需下载/可更新/已下载，`data_dialog.py:44-62`）；F5 刷新；**仅接受 ready 报告放行确认**（`data_dialog.py:224`） |
| 地图选点浮层 MapPickerDialog（`desktop/ui_map.py:60`） | `src/lib/components/map/MapPickerDialog.svelte` | 收藏点 + 「纬度,经度」搜索；DEM 700ms 防抖（`:76-79`）；未配天地图 Key 引导跳设置 |
| FireSkyMap/_BridgePage（`desktop/web_map.py:217`，console 桥 `:193`） | `src/lib/components/map/MapCanvas.svelte` + `src/map.html` | M1 已落底；本项补齐 mini 模式等 API 面与主题重注入；console 桥整体废弃 |
| MiniMap 示意图（`desktop/widgets.py:630`） | 不平移（地图常驻系统 WebView，无降级需求）；仅保留「WebView 初始化失败」的错误态 UI | 有意裁剪，见风险 R4 复核项 |
| NationalProfileCanvas / SatelliteProfileCanvas（`desktop/profile_canvas.py:164,511`，共 790 行） | `src/lib/components/profile/{NationalProfile,SatelliteProfile}.svelte`（Canvas 2D） | 参照 frontend 同名 Web 组件移植；X/Y 轴语义、压力族云色块、缺测斜纹逐项对照；set_data 只收 dict 快照的契约保持（props 传 JSON） |
| widgets.py 组件库（QualityCurve `:883`/GlassCard/MetricCard/ModelResultsBar/SegmentedControl/RunButton/Toast `:1020`/ElidedLabel） | `src/lib/components/{charts,widgets}/` | Toast → svelte 浮层；QualityCurve set_steps 契约（steps JSON）保持；样式从 QSS 迁 Tailwind（QSS 不平移，见 R5） |
| Workers（`desktop/workers.py:64`，ForecastWorker `:117`，_on_engine_progress `:209`，DataFetchWorker `:282`） | 删除；职责并入 `lib/ipc/engineClient.ts` + `lib/stores/dataSession.ts` + 每页 run 状态机 | 四信号（progress/finished_ok/failed/cancelled）→ IPC 三帧 + cancel 命令；**进度映射按链区分**（nowcast 前端套 `workers.py:209-243` 两段式；point/regional 直读 pair 值，§3.4）；心跳日志（8s 无事件）移入前端；SatelliteWarmupWorker 现状无调用方（`:351`），**不平移**，登记为遗留缺口 |
| CurrentLocationController（`desktop/location_controller.py:12`） | `src-tauri` 定位命令 + `CurrentLocationButton.svelte` | Tauri geolocation 插件平台表虽含桌面，但安装/权限示例全移动端向、无桌面权限配置章节（v2 评审轮核实 v2.tauri.app/plugin/geolocation）→ **M2 先实机验证插件桌面路径**；不可用则降级自研命令（macOS CoreLocation via objc2/Swift、Windows WinRT Geolocation）。前置：NSLocation 声明迁入 tauri.conf.json（对齐 `FireSky.spec:142`，Qt 版 `.app` 拒绝逻辑 `location_controller.py:27-31` 即权限声明缺失的先例）+ 签名（R10/R14）；20s 超时 + 时间戳新鲜度校验保留；明确不做 IP 兜底 |
| theme.py（LEVEL_COLORS `:13`、QSS `:247`、档位/云属/趋势/场景映射） | `src/lib/theme/`（CSS 变量 + level-colors.ts + 中文映射表） | 十档颜色以 Qt LEVEL_COLORS 为基准迁移到 Tauri，做双方逐色断言；frontend 仅按实际组件复用中文标签与渲染逻辑；QSettings 主题持久化（`:494`）→ tauri-plugin-store |
| icons.py（`desktop/icons.py:11`） | `@lucide/svelte`（frontend 同款） | 主题色经 currentColor/CSS 变量 |
| SpotStore/Location/持久化三件套（`desktop/widgets.py:229,87,158-226`） | `src/lib/stores/spot.ts` + tauri-plugin-store/fs | 唯一写口 set_location；locations.json 路径、`elevation_source`（manual / dem）与损坏留档恢复语义不变；配置 0600（适用平台）及海拔来源读写往返校验进单测 |
| data_access shim（`desktop/data_access.py`）→ firesky.desktop_data | 并入 engineClient 方法（inventory/check/prepare 直接走 IPC） | `FIRESKY_DESKTOP_DATA_BACKEND` 只控制 Python sidecar 内 desktop_data 的 Python/Rust 委派（`rust_desktop_data.py:160-163`），不负责 Tauri 子进程选择；M3 外层后端切换另用拟新增的 `FIRESKY_TAURI_BACKEND`（§3.3），不得混用 |
| _prepare_qtwebengine（`desktop/__init__.py:10`） | 删除（系统 WebView 无需打包修复） | 白屏防线改为：Tauri 启动时 WebView 版本探测 + 地图页加载失败错误态（对应 R2） |

**验收标准**：
1. **面板逐个验收**：每面板按映射表「契约锚点」列建立核对清单，Qt 版与 Tauri 版同输入并排对照（截图 + 数据 JSON diff），全部勾选；
2. **契约等价测试**（新增 `apps/desktop-tauri/tests/`）：① is_current 守卫（下载完成时参数已变 → 拒绝确认）② published_cycles 5 分钟缓存 ③ DEM 700ms 防抖 ④ elevation=-1 拦截与 elevation_source 读写往返 ⑤ locations.json 损坏留档恢复 ⑥ 配置 0600 ⑦ 进度映射按链验证（nowcast 两段式；point/regional pair 直读无二次映射）⑧ 区域缓存命中识别以显式 `cache_hit` 为准，覆盖 GFS 相 percent=49 / EC 相 percent=99 与相同百分比非命中事件，另测 `message` 日志回填；增强前缺字段需安全兼容，不凭百分比猜命中；
3. `svelte-check` + `vitest` 通过；四页全部脱离占位数据，走真实 sidecar；
4. 键盘路由完整：F5/Ctrl+Return 运行、Esc 取消、Ctrl+K 聚焦搜索，按当前页分发（对齐 `app.py:444-460`）；
5. 打包模式冒烟：PyInstaller onefile sidecar 进 externalBin，随包 `firesky-engine` + `FIRESKY_RUST_ENGINE_BIN` 生效，nowcast Rust 评分可用；native 库路径按 R9-② 处置（sidecar spec dest 修为 `firesky/native` 或注入 `FIRESKY_NATIVE_LIBRARY_PATH`），否则如实标注「原生评分静默回退（`native_core.py:318-319` 返 None → Python 回退）」。双平台在干净机验证；Windows 无 WebView2 时由 `offlineInstaller` 离线装好运行时。预测数据预先缓存时验证离线全链路；首次下载预测数据仍需联网，不把 WebView2 离线安装等同于数据离线可用。ecCodes 随包装配未通过前只能标注「hello 握手 + 网络探测」部分通过，不宣称 Windows 全链路验收完成；
6. Qt 版同时段照常出包（兼容版），发布说明注明双版本。

### M3 — 后端切 engine（Python 退出生产路径）

**目标**：sidecar 从 Python `desktop_rpc` 切到 Rust `firesky-engine`，桌面 IPC 全方法覆盖且语义等价；切换后桌面链路无 Python 进程。这是把 `docs/RUST_MIGRATION_STATUS.zh-CN.md:43`「桌面完整运行时」P1 缺口收口的里程碑。**范围只覆盖桌面 IPC 面，不要求引擎接管 FastAPI/CLI（后者是另一条 P1，`:42`）。**

**前置差距（勘察 + v2 评审轮复核确认）**：
1. engine `serve` 是单线程顺序循环、无 progress 帧、无 cancel（`engine/src/rpc.rs:857-872` 注释自述 *"Sequential migration/debug endpoint, not the production desktop service"*）→ **需新增生产桌面服务模式**（reader 线程 + worker 池 + progress 事件 + cancel，参照 `engine/src/desktop_location.rs` 已有的模式）；
2. engine 缺桌面 UI 辅助方法：cams_credentials、save_cams_key、network_probe、search/metadata（grep 复核为空）；
3. **方法名同义不同名**：引擎方法臂为 `desktop_session/desktop_prepare/desktop_publications/desktop_inventory/desktop_location` 等（`rpc.rs:232,243-333`），对应 Python 协议的 `check/prepare/publications/inventory` → 需**方法名适配层**（推荐 engine 侧加别名臂保持前端协议不变；或 Tauri `ipc.rs` 映射表，二选一 M3 设计定）；
4. **参数形态差异**：`desktop_prepare` 入参带请求文件路径 `path`（`desktop_prepare.rs:20-27`），非纯内联 params → 适配层需把内联 params 落请求文件，或引擎侧改收内联；
5. **无 live forecast**：引擎 forecast 均为 `*_to_files` 迁移端点（`rpc.rs:385-397`，返回 manifest_path，无进度/取消）——M3 的 live forecast（含 progress/cancel/双模型 pair 映射，§3.4）是**新增实现**，为 M3 最大工作量项，不得按「已有基础」估算；
6. 编排层（GFS/EC 双模型编排、进度清洗、compute_lock 单飞、会话令牌 1800s/单次/≤32）目前 Python 持有（`desktop_rpc.py:243-354`），engine 侧 `desktop_session/desktop_prepare` 有基础（`rpc.rs:232`）但未对齐全部编排语义。

**涉及文件**：
- `engine/src/rpc.rs` + 新 `engine/src/desktop_service.rs`：生产桌面服务入口（`firesky-engine desktop` 子命令，`engine/src/main.rs:3-28` 加分支）；并发模型、progress、cancel、EOF 清理语义对齐 `desktop_rpc.py:444-503`
- `engine/src/` 补齐：`cams_credentials`/`save_cams_key`，按 Q5 接入 macOS Keychain / Windows Credential Manager 并同步 Rust 读取；保留环境变量锁定优先级（`desktop_credentials.py:70-71`），旧文件迁移经用户确认，不输出密钥或自动删除原文件；补齐 `network_probe`、`search`/`metadata`、`dismiss`
- **方法名/参数适配层**（前置差距 3/4）：engine 侧别名臂（`rpc.rs` dispatch 加 `check/prepare/publications/inventory` 别名，`desktop_prepare` 改收内联 params 或适配层落请求文件）或 Tauri `ipc.rs` 映射表——二选一，M3 设计定
- `src-tauri/src/sidecar.rs`：sidecar 命令切 `[engine-binary] desktop`；打包产物从「PyInstaller+Python 运行时」换成单个 Rust 二进制
- `src/firesky/desktop_rpc.py`：保留但标注「兼容 sidecar」，供 A/B
- `docs/RUST_MIGRATION_STATUS.zh-CN.md`：P1 表更新

**验收标准**：
1. **双后端 A/B 等价**：按前置差距 3 的适配映射逐对执行——同一观测点/日期/现象，分别对 Python sidecar（check/publications/prepare/forecast/inventory/network_probe）与 engine 后端（desktop_session/desktop_publications/desktop_prepare/新增 live forecast/desktop_inventory/…）各跑全链路，结果 JSON 字段级一致（浮点按 docs/RUST_PERFORMANCE.md 既定容差口径），作为常驻回归测试；
2. 取消语义等价：forecast 运行中 cancel → 两后端均报 `cancelled`；下载中 cancel 行为一致（协作式，不中断外部传输）；
3. 进度契约等价：两段式百分比与 stage 序列逐事件对齐；
4. 会话令牌等价：1800s 过期、单次消费、≤32、dismiss/cancel 作废（对齐 `desktop_rpc.py:53-60,220-221`）；
5. 切换后 `ps` 验证桌面链路无 python 进程；体积/启动时间对比记录进 `docs/RUST_PERFORMANCE.md`；
6. 回滚开关：`FIRESKY_TAURI_BACKEND=python|rust`（默认 rust），兼容版 Python sidecar 一个版本内可切回；**握手期望值按后端注入**（python→`engine=="firesky"`、rust→`engine=="firesky-rust"`，§3.3），切换/回滚不需改编译产物；
7. Windows 实机通过全链路（R8 依赖问题此时必须已解）；
8. 凭据迁移验收：平台钥匙串读写、重启后可用、环境变量覆盖/锁定、无凭据与拒绝访问错误路径均有测试，日志与 IPC 状态不得回传密钥明文。

### M4 — Qt / SwiftUI / WinUI3 退役

**目标**：Qt、SwiftUI、WinUI3 退出主桌面交付路线，保留源码作为兼容/参考材料；SwiftUI/WinUI3 须在 M2 完成后冻结。打包链、CI、文档在 M4 完成后收敛到 Tauri；不在本轮删除任何客户端。

**涉及文件（改动为主，不删代码）**：
- `desktop/`：目录头部加 MAINTENANCE.md（兼容版声明：只收安全/崩溃修复，新功能一律进 Tauri 版）
- `desktop/FireSky.spec`：保留但标注兼容；修复遗留断链（icon.ico 已由 M0 的 Tauri 图标链替代说明）
- `.github/workflows/ci.yml`：desktop job（PyInstaller）保持双轨直到 M4 完成后再停用；`.github/workflows/native-desktop.yml`：SwiftUI/WinUI 冒烟在 M4 退役时停用；`tauri-desktop.yml` 成为桌面主工作流
- `Makefile`：新增 `tauri-dev` / `tauri-build` / `tauri-bundle` 目标，`desktop-macos` 等旧目标标注兼容
- `README.md` / `README.en.md`：桌面章节以 Tauri 版为主，Qt 版降级为兼容说明；`apps/README.md`：架构图更新
- `docs/`：RUST_MIGRATION 系列 P1 表勾销「桌面完整运行时」

**验收标准**：
1. （历史目标，已被 2026-11-12 原生 UI + Rust FFI 决策覆盖）发布说明明确：Tauri
   版为正式桌面版，Qt 版为兼容版；
2. Tauri 版功能对齐清单（M2 映射表全表）逐项勾销；Qt 版与 Tauri 版已知行为差异清单成文（预期差异如 MiniMap 裁剪）；
3. M4 验收完成前 PyInstaller 与 Tauri 保持双轨出包；完成后停止旧构建链，桌面产物收敛到 tauri-desktop.yml；
4. `docs/RUST_MIGRATION_STATUS.zh-CN.md:43` 的 P1 项状态改为已收口（桌面链路）；
5. `desktop/`、`apps/macos`、`apps/windows` 的物理删除不在本蓝图排期，未来另行确认；本次退役决策不授权现在删除源码。

---

## 5. 风险清单

| # | 风险 | 依据 | 缓解 |
|---|------|------|------|
| R1 | **结构性断点：引擎调用从同进程 Python import 变跨进程 IPC**。workers.py 全部 `from firesky.web_server import ...`，Tauri 前端无法 import Python；桌面 Rust 运行时是迁移文档 P1 缺口 | `workers.py:144,155-160,257-262`；`RUST_MIGRATION_STATUS.zh-CN.md:43` | M1–M2 用 desktop_rpc sidecar 承载全部编排（M1 协议不变，M2 按 Q10 最小增强）；M3 才切引擎；编排语义（令牌/单飞/两段进度）在两阶段保持一致，前端只写一次 |
| R2 | **地图栈重验**：QWebEngineView+console 前缀桥 → 系统 WebView；Leaflet 在 WKWebView/WebView2 下的拖拽/瓦片/CSP 行为需重验；QtWebEngine 白屏防线（`__init__.py:10`）失效需等价方案 | `web_map.py:182-217`；基底 `desktop/assets/map.html`（console 桥事件及 FIRESKY_READY 就绪握手待替换） | M1 就把地图页列为验收项（不走 M2 才发现）；错误态 UI + 瓦片失败重试；天地图 Key 注入走 Svelte 响应式而非 console 桥 |
| R3 | **取消/并发模型重设计**：Qt 取消是引擎回调里同步抛异常 + 600s wait；IPC 取消是协作式（回调点生效）、服务端 4 线程/16 活跃/compute_lock 单飞 | `workers.py:83-90`；`ui_regional.py:614`；`desktop_rpc.py:466,493-494` | M1 验收标准 3 显式验证取消；UI 明示「下载/预测串行」约束；M3 时 engine 侧并发模型对齐该语义而非扩容 |
| R4 | **自绘组件体量**：profile_canvas 790 行 + widgets 1067 行 + theme 503 行 QSS，逐像素对齐工作量大 | 本会话 wc -l 复核 | 参照 frontend 已有同名 Web 组件（NationalCrossSection/SatelliteVerticalProfile）移植而非从零画；M2 逐面板验收并排对照；MiniMap 不平移已获用户确认（Q8），保留 WebView 初始化失败错误态 |
| R5 | **主题口径漂移**：Qt LEVEL_COLORS 是十档颜色基准，frontend constants 并无同表；中文映射分散在实际组件中，不能假定整表共用；QSS 动态刷新（unpolish/polish）在 CSS 无直接对应 | `theme.py:13,29,247` | 从 Qt LEVEL_COLORS 迁移 level-colors.ts + Qt↔Tauri 逐色断言，中文映射另核对；主题用 CSS 变量 + Svelte 响应式，放弃逐组件 repolish 模型 |
| R6 | **会话细节契约易丢**：发布周期 5min 缓存、DEM 700ms 防抖、is_current 守卫、0600 权限、损坏留档恢复、EOF 静默改判 cancelled | 勘察 risks#8 + `data_panel.py:94-96`、`ui_map.py:76-79`、`widgets.py:165-176,223` | M2 验收标准 2 的契约测试逐条落地（含 elevation_source 往返），进 CI |
| R7 | **过渡 sidecar 装配风险**（onefile 已定）：Python 引擎=完整运行时（numpy/h5py/eccodes/satpy 链），ecCodes 原生库不随 wheel（实测 wheel 无 dylib）；WebServerConfig 缓存按 cwd 相对解析（`web_server.py:309-317`），打包 cwd 不对则缓存散落；**nowcast 评分默认 rust 且无 Python 兜底**：`FIRESKY_NOWCAST_SCORING_BACKEND` 默认 "rust"（`national_batch.py:6903-6906`），启用即 return 走 Rust 路径（`:6786-6787`），`rust_grid.engine_binary()` 缺二进制直接 raise（`rust_grid.py:25-27`）——打包 sidecar（cwd=数据目录）解析不到 `<repo>/engine/target/release` 时 nowcast 评分必失败；体积大头仍在引擎侧（Qt 版 dmg 已 302MB） | 勘察 blockers#1-3 + v2 评审轮复核 | M1 开发模式绕开（venv 直跑）；打包模式 PyInstaller onefile + 显式 cwd=app 数据目录 + 环境变量清单（§3.1：缓存根 + 随包引擎路径 + 可选 `FIRESKY_NATIVE_LIBRARY_PATH`）；**已定随包分发 `firesky-engine` 并设 `FIRESKY_RUST_ENGINE_BIN`**，不强制 Python 评分；ecCodes 随包装配未验证前 Windows 仅能标注部分通过，不视为全链路验收完成（M2 验收 5） |
| R8 | **Windows 契约从未实机验证**：apps/windows 自述未构建/未交互测试（`apps/README.md:122-125`）；GRIB 原生依赖、WebView2、路径分隔符全部待验；Windows dev 已选 conda-forge，但仍需脚本与实测（brew 仅覆盖 macOS，开发环境不等于 R7 的安装包分发） | 勘察 blockers#11 + v2 评审轮 | 前置任务 **T-W1**「Windows dev 机 GRIB 依赖方案」（已选 conda-forge，M1 涉及文件）；M1 Windows 验收分必过档/全链路档两档（M1 验收 7）；Tauri 版 Windows 验收每个里程碑都跑真机，不靠纸面契约 |
| R9 | **现存缺陷被迁移放大**：① `FireSky.spec:115` 引用的 icon.ico 不存在；② 打包 dylib 路径与 `native_core.py:273-277` 候选（`parents[2]/native/target/release` 与 `_MEIPASS/firesky/native`）均不符、无人设 FIRESKY_NATIVE_LIBRARY_PATH；③ ui_regional.py:39 无降级 import | 本会话 ls/grep 复核 | ① M0 验收 6 显式复制 `apps/desktop-tauri/icons/icon.ico` → `desktop/icon.ico` 修复；② **Tauri 打包版 sidecar 同为 PyInstaller，同样受影响**——`native_library()` 失败返回 None（`native_core.py:318-319`）后上层走 Python 回退：打包版单点预测可跑通，但原生评分是静默回退路径而非可用能力（如实标注，非「不受影响」）；修复选项：sidecar spec dest 改 `firesky/native`（匹配 _MEIPASS 候选，`FireSky.spec:55-68` 现 dest="native"）或启动注入 `FIRESKY_NATIVE_LIBRARY_PATH`（§3.1 清单），M3 后 engine 二进制消解该问题；③ 已在 M2 映射表消解 |
| R10 | **签名/公证缺失**：全线 adhoc（实测 FireSky.app adhoc、README 自述右键打开）；Tauri 正式分发需 Developer ID + 公证 + hardened runtime + NSLocation 声明迁移 | `FireSky.spec:142`；勘察 tauriNeeds | M0 conf 预留 entitlements/NSLocation 配置；Q6 已定无证书阶段先未签名；不宣称已完成公证、Authenticode 或定位权限验证；安装限制如实说明，正式发布另行确认 |
| R11 | **元数据漂移**：bundle id（cn.firesky.desktop vs app.firesky.native）、版本（0.1.1/0.2.0/0.1.0）、最低系统（12.0 vs 14.0）三处不一致 | `FireSky.spec:135-144`、`build_macos_native.sh:20-23`、`pyproject.toml:3` | M0 验收标准 5/7 强制收敛；Q1 已定 Tauri identifier=cn.firesky.desktop、版本源=tauri.conf.json、macOS 最低=12.0；不表示所有历史客户端元数据已同步改动 |
| R12 | **ADS Key 明文契约**：`~/.cdsapirc` 是引擎侧直接读的文件契约，改钥匙串会牵动引擎读 Key 路径 | `ui_datasource.py:95-118`、`desktop_credentials.py:70-71` | Q5 已定 M3 迁平台钥匙串并同步 engine 读取；M2 暂保留文件契约，迁移须覆盖环境变量锁定语义及拒绝访问错误路径，不输出凭据 |
| R13 | **stdout 纪律**：sidecar 内任何框架外 print 会破坏帧界；EOF 时在途请求静默改判 cancelled | `desktop_rpc.py:505-510`；勘察 blockers#10 | M1 sidecar.rs 不解析 stderr 做协议；退出顺序「等在途→关 stdin→杀」可配置；M3 engine 侧同纪律 |
| R14 | **桌面定位能力未经验证**：Tauri geolocation 插件平台表含桌面，但安装/权限示例全移动端向（`cargo add --target android/ios`、`#[cfg(mobile)]`、无桌面权限配置章节；v2.tauri.app/plugin/geolocation 本会话核实）；无论插件或自研（CoreLocation/WinRT）都依赖 NSLocation 声明迁移（`FireSky.spec:142`）与签名（R10）；Qt 版 `.app` 拒绝逻辑（`location_controller.py:27-31`）正是权限声明缺失的先例 | v2 评审轮核实 | M2 先插件桌面实机验证（M2 映射表）；不可用降级自研命令（macOS CoreLocation via objc2、Windows WinRT Geolocation）；NSLocation 声明 M0 进 tauri.conf.json；不做 IP 兜底（维持现状） |

---

## 6. 已确认决策（2026-11-11）

原有 14 项、第 15 项地图补充和第 16 项 Swift 设计纠正已由用户确认，后续实施按此执行，不再作为开放问题。Q 编号保留用于关联前文设计依据。

| # | 议题 | 已确认决策 |
|---|------|------------|
| 1（Q1） | Bundle identifier | `cn.firesky.desktop` |
| 2（Q1） | 版本单一来源 | `src-tauri/tauri.conf.json`；其他 Tauri 元数据派生或校验，禁止独立维护版本 |
| 3（Q1） | macOS 最低系统 | 12.0 Monterey；仍需在最低支持系统实机验证 |
| 4（Q3） | 工程目录 | `apps/desktop-tauri/`，与现有骨架一致 |
| 5（Q2） | SwiftUI / WinUI3 去留 | M2 完成后冻结为参考实现，M4 随 Qt 一起退役；不表示现在删除代码 |
| 6（Q4） | 过渡 sidecar | PyInstaller **onefile**；用户明确 Python 正在移植到 Rust，此方案仅为中间形态，M3 桌面生产链路切 Rust |
| 7（Q4） | nowcast 评分 | 随包分发 `firesky-engine`，显式设置 `FIRESKY_RUST_ENGINE_BIN`；不以强制 Python 评分作为交付方案 |
| 8（Q4） | Windows 开发 ecCodes | conda-forge；不等于已解决安装包内 ecCodes 库、definitions、许可证与干净机验证 |
| 9（Q5） | ADS/CDS 凭据 | M3 迁平台钥匙串，并同步 Rust 读取；M2 暂保留 `~/.cdsapirc` 0600 过渡契约与环境变量锁定语义 |
| 10（Q6） | 签名 / 公证 | 当前无证书，初期先未签名；不启用正式签名、公证与自动更新分发，不代表授权对外发布 |
| 11（Q7） | Windows WebView2 | 按用户所选离线分发意图，采用 `offlineInstaller`；`embedBootstrapper` 仅内嵌引导器，不保证离线安装运行时 |
| 12（Q9） | CI 双轨 | PyInstaller 与 Tauri 并行出包，直到 M4 完成后再停旧构建链 |
| 13（Q8） | M2 顺序 / 裁剪 | 数据页 → 实况 → 区域 → 单点右栏完整面板（剖面最后）；不平移 MiniMap，保留 WebView 失败错误态 |
| 14（Q10） | 协议最小增强 | 接受 M2 `message` / `cache_hit` 透传，M3 可加 ping；同步协议文档与两端客户端兼容处理 |
| 15（M2-D 定案） | 地图服务与设置入口 | 天地图 + OpenFreeMap，默认天地图；取消 MapKit JS、OSM 公共栅格及 Esri 入口。地图源与底图类型只在设置页切换，地图页面不放切换菜单 |
| 16（设计纠正） | 直接移植 Swift 设计 | `Workbench.swift` / `DialogViews.swift` 为页面设计原稿，不另起布局；四业务导航、地图弹窗、292px 参数栏、结果与设置分组直接转写为 Svelte |

**仍需工程验证，不是待用户重新拍板**：ecCodes 可再分发装配、native 库加载路径、Windows 全链路、定位权限、离线安装与最低系统支持。旧客户端物理删除、证书采购、对外发布另行处理。本次决策回写仅修改蓝图，不代表上述配置和功能已经在骨架中实现。

---

## 7. 明确不做（本蓝图边界）

- Android/iOS：Tauri 2 具备能力但本期不启用 mobile API、不做触屏适配；
- 引擎接管 FastAPI/CLI/任务队列（`RUST_MIGRATION_STATUS.zh-CN.md:42` 另一条 P1）：M3 只覆盖桌面 IPC 面；
- IP 定位兜底（现状明确不做，维持）；
- frontend/（Web 版）的功能合并或改造：仅作组件与口径的移植参照。
