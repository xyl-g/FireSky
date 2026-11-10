# FireSky 桌面端 Tauri 迁移落地蓝图（Tauri 2 + Svelte + Vite）

> 状态：可开工蓝图（v2，2026-11-11；v1 经评审 9 条意见修订——进度契约按链区分、地图基底改 `desktop/assets/map.html`、M3 协议适配层、打包 Rust 评分依赖、Windows 双档验收、桌面定位降级方案、退出语义修正、icon.ico 复制机制、单实例/更新器落点）
> 依据：三份现状勘察（功能面 / IPC 契约 / 打包现状）+ 本会话两轮对仓库的实地复核（见 §1.4，其中 14–20 号为 v2 评审轮复核）。
> 已定决策：**Tauri 2 + Svelte + Vite**；范围先 **macOS / Windows 桌面**（Android/iOS 暂缓）；后端初期 **sidecar 复用现有 Python `firesky.desktop_rpc`**，M3 才切 `engine/` Rust 引擎；**Qt 版（desktop/ PySide6）按兼容版路线退役**。

---

## 0. 问题定义

`docs/RUST_MIGRATION_STATUS.zh-CN.md:43`（P1「桌面完整运行时」）已明确：macOS/Windows 原生客户端目前启动 Python `firesky.desktop_rpc` 子进程，Qt 桌面本身也运行于 Python。仓库现状是**四套桌面形态并存**（PySide6+PyInstaller、SwiftUI、WinUI3、待建的 Tauri），元数据漂移（bundle id `cn.firesky.desktop`+0.1.1 vs `app.firesky.native`+0.2.0，`desktop/FireSky.spec:135,139` vs `scripts/build_macos_native.sh:20,23`）。

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
- **Tauri 需要的现成资产**：`desktop/assets/firesky.icns`（ic04-ic14 全尺寸）可直接复用；`desktop/assets/firesky.png` 实测 1024×1024 RGBA，是 `tauri icon` 合格源图；**地图页基底是 `desktop/assets/map.html`（709 行）而非 `apps/shared/map.html`（56 行）**——后者 grep `set_grid|tianditu|esri` 为 0，只是 OSM 演示页（随 WinUI 分发）；前者已含 11 个 fs* API（`fsInit:572`、`fsSetMini:565`、`fsSetTheme:585`、`fsSetSource:590`、`fsSetView:591`、`fsSetName:598`、`fsSetBase:602`、`fsFitBounds:603`、`fsInvalidate:606`、`fsSetGrid:678`、`fsFocusGridCell:694`）、三供应商切换与境外回退（:333-465）、天地图 Key 缺失引导（:244-254,391-392,461）、瓦片/JS 错误上报（:320-328），仅 console 桥 5 处（`FIRESKY_*` 于 :323,328,479,511,553）需替换；`src/firesky/desktop_rpc.py` 就是现成协议层。

### 1.4 本会话复核记录

| # | 事实 | 验证方式 |
|---|------|----------|
| 1 | frontend/ 是 SvelteKit 5（svelte ^5.28）+ Vite 6 + adapter-static + Leaflet 1.9 + shadcn-svelte + Tailwind 4，路由组 (app)/forecast、(app)/now、(app)/outlook | `ls frontend/src/routes`、读 `frontend/package.json` |
| 2 | 仓库无任何现成 Tauri 脚手架 | `find . -maxdepth 3 -name tauri.conf.json -o -name src-tauri` 为空 |
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
| 16 | `apps/shared/map.html` 56 行、grep `set_grid\|tianditu\|esri` 为 0；`desktop/assets/map.html` 709 行、11 个 fs* API（`fsInit:572`…`fsSetGrid:678`、`fsFocusGridCell:694`）、console 桥 5 处（:323,328,479,511,553） | wc -l / grep 复核（v2 评审轮） |
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
│  lib/ipc/engineClient.ts      │◄──┤   （复用现有协议，0 改动）         │
│  （invoke → listen(progress)）│   │ M3:   firesky-engine serve*      │
│  stores: spot/settings/       │   │   （Rust 生产桌面服务模式）        │
│  dataSession/theme            │   └────────┬─────────────────────────┘
└───────────────┬───────────────┘            │ 按需拉起
                │ 静态资源                    │
│  map.html (Leaflet, CSP 干净) │   ┌────────▼────────┐
└───────────────────────────────┘   │ engine/src/*    │
                                    │ desktop_* Rust  │
                                    └─────────────────┘
```

\* M3 的 `serve` 现状是顺序迁移端点（`engine/src/rpc.rs:857-872`），切引擎前必须补生产桌面服务模式（见 M3 范围）。

### 2.2 技术选型

| 层 | 选型 | 理由 |
|----|------|------|
| 壳 | Tauri 2（stable，macOS/Windows 桌面；mobile API 暂不启用） | 已定决策；系统 WebView 免 QtWebEngine 302MB 包体 |
| 前端 | Svelte 5 + Vite（**非 SvelteKit**，纯 SPA） | 桌面四页与 Web 版页面结构差异大，frontend/ 带鉴权/console/admin 包袱；但口径常量、地图与剖面组件作为移植参照 |
| UI 基座 | Tailwind CSS 4 + 少量 shadcn-svelte 组件 | 与 frontend/ 同栈，降低团队切换成本 |
| 地图 | Leaflet 1.9（同款），`desktop/assets/map.html` 打进 Tauri 静态资源 | 基底用 `desktop/assets/map.html`（709 行能力齐全），仅替换 console 前缀桥为 postMessage；`apps/shared/map.html`（56 行演示页）仅作 CSP 参考 |
| IPC | 复用 desktop_rpc JSON-lines 协议，Rust 侧读写帧 → `tauri::Emitter` 推 progress 事件 → 前端 listen | 协议零改动；apps/ 两端客户端已验证该协议可行 |
| 状态 | Svelte stores 显式替代 Qt 信号扇出（SpotStore→writable store；主题/地图配置→派生 store） | 替代 findChildren 广播（`app.py:526,554`）的隐式耦合 |
| 持久化 | tauri-plugin-store（配置 JSON 0600）+ fs plugin；收藏 locations.json 保持现有路径与损坏留档恢复语义 | 对齐 `widgets.py:158-226` 契约 |

### 2.3 目录规划（推荐 `apps/tauri/`，与 apps/macos、apps/windows 并列；最终命名见 openQuestions Q3）

```
apps/tauri/
├── package.json  vite.config.ts  svelte.config.js  tsconfig.json
├── src/                          # Svelte 前端
│   ├── App.svelte  routes/（point | regional | nowcast | data 四页）
│   ├── lib/ipc/engineClient.ts   # 唯一 IPC 封装（invoke + listen）
│   ├── lib/ipc/types.ts          # 帧类型 / report 类型（对齐 desktop_rpc）
│   ├── lib/stores/{spot,settings,dataSession,theme}.ts
│   ├── lib/components/{map,profile,charts,widgets,data}/
│   └── map.html                  # Leaflet 页（基底 desktop/assets/map.html，换 console 桥为 postMessage）
├── src-tauri/
│   ├── tauri.conf.json           # identifier cn.firesky.desktop、版本单一来源
│   ├── Cargo.toml  build.rs
│   └── src/{main.rs, lib.rs, sidecar.rs, ipc.rs, commands.rs, menu.rs}
└── icons/                        # pnpm tauri icon desktop/assets/firesky.png 生成
```

### 2.4 复用清单（不重写的东西）

| 资产 | 用途 |
|------|------|
| `src/firesky/desktop_rpc.py` | M1–M2 sidecar，协议零改动 |
| `desktop/assets/map.html` | Tauri 地图页基底（709 行：11 个 fs* API / 三供应商切换与境外回退 / Key 缺失引导 / 瓦片与 JS 错误上报，`map.html:320-703`）；仅把 console 前缀桥（`FIRESKY_PICK/SOURCE/BASE/TILEERROR/JSERROR`，`map.html:323,328,479,511,553`）换成 postMessage/Svelte 直调；`apps/shared/map.html`（56 行演示页，grep `set_grid|tianditu|esri` 为 0）仅作 CSP 参考 |
| `desktop/assets/firesky.png/.icns` | tauri icon 源图 / macOS 图标 |
| `frontend/src/lib/utils/{constants,cloud-render,geo,format}.ts` | 档位配色/云属/趋势/场景中文映射（与 `desktop/theme.py:29` 注释同表口径，直接拷贝对齐） |
| `frontend/src/lib/components/map/`、剖面组件（NationalCrossSection / SatelliteVerticalProfile） | M2 平移剖面与地图的参照实现 |
| `desktop/assets/icons/` Lucide SVG | 前端改用 `@lucide/svelte`（frontend 已用），主题色经 CSS 变量 |
| `engine/target/release/firesky-engine` | M3 目标后端（本机已有 10.4MB 构建） |

---

## 3. IPC 设计（M1 的落地细节）

### 3.1 sidecar 生命周期（src-tauri/sidecar.rs，对齐 apps/ 两端客户端模式）

- **开发模式**：解析 workspace（`FIRESKY_WORKSPACE` 环境变量 → 探测 `<repo>/.venv`），spawn `<venv-python> -u -m firesky.desktop_rpc`，cwd=workspace 根，env `PYTHONPATH=<workspace>/src + PYTHONUNBUFFERED=1`（Windows 另加 `PYTHONIOENCODING=utf-8`）。启动前校验 `pyproject.toml` 存在 + python 可执行（同 `EngineClient.swift:24-29`）。
- **打包模式（M2 末引入）**：sidecar 为 PyInstaller onefile 可执行 `firesky-desktop-rpc-<target-triple>`（tauri externalBin 命名约定），内部跑 `python -m firesky.desktop_rpc`；cwd 显式设为用户数据目录（见 §5 R7 cwd 契约）。**打包必配环境变量清单**：缓存根（覆盖 WebServerConfig 相对路径，R7）+ nowcast 评分后端**二选一**——随包分发 `firesky-engine` 并设 `FIRESKY_RUST_ENGINE_BIN`，或强制 `FIRESKY_NOWCAST_SCORING_BACKEND=python`（默认 "rust" 且无 Python 兜底，`national_batch.py:6903-6906,6786-6787`；`rust_grid.py:25-27` 缺二进制直接 raise，决策入 Q4）+ 可选 `FIRESKY_NATIVE_LIBRARY_PATH`（R9-②）+ Windows 另加 `PYTHONIOENCODING=utf-8`。打包形态取舍见 openQuestions Q4。
- **退出顺序**（比 apps 两端现状更强——两端均无优雅等待：Swift `stop()` 关 stdin 后立即 `terminate()` 无等待（`EngineClient.swift:183-192`），C# `Dispose()` 直接 `Kill(entireProcessTree:true)` 不关 stdin（`EngineClient.cs:185-196`），照抄任一端都会丢优雅取消）：前端退出 → 等在途请求完成（可配置超时）→ 关 stdin 等 EOF 清理（`desktop_rpc.py:501-503`）→ 超时 SIGTERM（Windows `Kill(entireProcessTree)`）。进程退出事件 → 广播失败给全部 pending（failAll，"引擎已退出"）。
- **存活判断**：无心跳帧（协议现状），仅靠进程退出事件 + 写入失败；M3 若切引擎可加 ping（协议演进点，见 openQuestions Q10）。

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
- **区域缓存命中识别（M2 落地）**：Qt 判据 `stage=="cache" && percent==99` + message 日志（`ui_regional.py:448-451`）在 IPC 下两处失效——pair 映射后 GFS 命中=49、EC 命中=99，且 message 不在帧内。M2 二选一：**默认走协议最小增强**——desktop_rpc `safe_event` 补 `message` 与显式 `cache_hit: bool` 透传（M1 保持协议零改动，M2 一次改清，同步 `apps/README.md` 协议文档与两端客户端）；备选零协议改动——前端按 `source` 反演（gfs 相 percent≥49 即原始 99 命中），代价是丢 message 日志文本。取舍见 Q10。

---

## 4. 里程碑

### M0 — Tauri 骨架（无引擎、无 IPC）

**目标**：Tauri 2 + Svelte 5 + Vite 工程落地并进主干 CI：四页空壳可导航、亮暗主题 + 十档配色与 Qt/Web 口径对齐、菜单/快捷键骨架、图标与元数据统一，双平台出可安装冒烟产物。全程零 Python 依赖。

**涉及文件**（全部新建，另少量改动现有文件）：
- `apps/tauri/`：`package.json`、`vite.config.ts`、`svelte.config.js`、`tsconfig.json`、`src/App.svelte`、`src/routes/{point,regional,nowcast,data}/+page.svelte`（空壳）、`src/lib/stores/theme.ts`、`src/app.css`
- `apps/tauri/src-tauri/`：`tauri.conf.json`（identifier `cn.firesky.desktop`；版本单一来源，见 Q1；macOS minimumSystemVersion 定值，见 Q6；Windows webviewInstallMode，见 Q7；macOS `NSLocationWhenInUseUsageDescription` 迁移占位，对齐 `FireSky.spec:142`，服务 R14）、`Cargo.toml`（含 `tauri-plugin-single-instance`）、`src/{main.rs,lib.rs,menu.rs}`（应用菜单：设置⌘,/关于/视图/任务，对齐 `app.py:417` 的角色归并；单实例插件初始化）、`icons/`
- `apps/tauri/src/lib/theme/level-colors.ts`：十档配色，与 `desktop/theme.py:13` LEVEL_COLORS、`frontend/src/lib/utils/constants.ts` 三方对齐（本会话核实 frontend 同栈可直接拷贝）
- `.github/workflows/tauri-desktop.yml`：新工作流（tauri-action 双平台，Rust 工具链 + pnpm 缓存；签名 secret 预留不启用）
- 修改：`README.md` 桌面章节加 Tauri 开发说明；`.gitignore`

**验收标准**：
1. `pnpm tauri dev` 打开窗口：侧栏四页导航（Ctrl+1..4）可用，当前页高亮；
2. 亮暗切换即时生效且十档配色与 Qt 版逐色一致（人工对照 + 单测断言与 `theme.py:13` 同值）；
3. macOS 应用菜单出现 设置⌘,/关于；F5/Esc/Ctrl+K 快捷键路由到当前页（M0 阶段打印占位事件）；
4. `svelte-check` 通过、`cargo clippy -- -D warnings` 通过；
5. CI 双平台 build 通过并上传产物；macOS 产物 Info.plist 的 identifier/版本与决策一致（PlistBuddy 断言）；
6. 图标全套由 `pnpm tauri icon desktop/assets/firesky.png` 生成入库（含 .ico）；随后**显式复制 `apps/tauri/icons/icon.ico` → `desktop/icon.ico`**——`FireSky.spec:115` 引用的是后一路径，`tauri icon` 产出不会自动落到那里，不复制则 Qt 兼容版 Windows 打包断链仍在（本会话 ls 实测 `desktop/icon.ico` 不存在）；
7. 仓库内 `rg "app.firesky.native|0\.2\.0"` 不再出现在新生成的 Tauri 元数据中（漂移收敛）；
8. 单实例：应用已运行时二次启动聚焦既有窗口、**不拉起第二个 sidecar 进程**（防双开抢缓存目录与会话令牌——会话是进程内存态，`desktop_rpc.py:53-60,220-221`）。

### M1 — sidecar IPC 接通 + 地图页

**目标**：Rust 壳长命拉起 Python `desktop_rpc`，协议桥全通（握手/进度/取消/三帧错误）；Leaflet 地图页在系统 WebView 下工作（选点落库 + 区域热力 + 底图/Key 切换）；端到端跑通一次真实 GFS 单点预测。此里程碑后，Tauri 版具备「能用的单点预测」。

**涉及文件**：
- `apps/tauri/src-tauri/src/sidecar.rs`：workspace/python 解析、spawn、stderr→`~/Library/Application Support/FireSky/engine.log`（Windows `%LOCALAPPDATA%\FireSky\engine.log`，对齐 `EngineClient.cs:66` LocalApplicationData）、退出 failAll（§3.1）
- `apps/tauri/src-tauri/src/ipc.rs`：帧读写、pending 表、progress 事件转发（§3.2）
- `apps/tauri/src-tauri/src/commands.rs`：`engine_request`/`engine_cancel`/`engine_status` commands
- `apps/tauri/src/lib/ipc/{engineClient.ts,types.ts}`：握手校验 protocol==1、方法封装、progress listen（§3.3）
- `apps/tauri/src/lib/stores/{spot,dataSession}.ts`：SpotStore 等价物（单写口 + elevation=-1 哨兵拦截）+ check→publications→prepare→forecast 状态机
- `apps/tauri/src/map.html` + `src/lib/components/map/MapCanvas.svelte`：**基底 = `desktop/assets/map.html`（709 行，能力齐全，勿从零重造约 600 行已有逻辑）**——移植改动只有两处：① console 前缀桥（`FIRESKY_PICK/SOURCE/BASE/TILEERROR/JSERROR`，`map.html:323,328,479,511,553`）换成 window.postMessage 或 Svelte 直调；② Key/主题/mini 注入走 `fsInit/fsSetTheme/fsSetSource/fsSetMini`（`map.html:565-606`）。`apps/shared/map.html`（56 行演示页）不作基底；
- `apps/tauri/src/routes/point/+page.svelte`（M1 半成品：观测点选择 + 运行 + 进度条 + 结果 JSON 卡片，完整面板留给 M2）
- `apps/tauri/src-tauri/tauri.conf.json`：externalBin（开发模式不打包，仅打包模式注册）
- 脚本 `scripts/smoke_desktop_rpc.sh`（或 .ps1）：封装本会话已验证的最小启动配方（cwd=repo 根、PYTHONPATH=src、`-u -m firesky.desktop_rpc`、printf hello | 断言 protocol==1、EOF 退出码 0），CI 可复用
- **前置任务 T-W1**：`scripts/setup_windows_eccodes.ps1` + 文档——Windows dev 机 GRIB 解码依赖方案（conda-forge eccodes / vcpkg / 手动 DLL 三选一实测；brew 仅覆盖 macOS，打包分发方案是另一层，见 R7/Q4）；落地前 Windows 验收走 M1 验收 7 的必过档

**验收标准**：
1. `scripts/smoke_desktop_rpc.sh` 通过（hello 返回 protocol==1, engine=="firesky"，EOF EXIT=0）——与本次勘察实测配方一致；
2. UI 内完成 hello 握手 → check → prepare → forecast(confirmed) 全链路，产出与 Qt 版同输入同结构的预测 payload（JSON 字段级 diff 快照测试，选固定观测点+固定日期）；
3. 进度语义按链验证（§3.4）：nowcast 进度条呈两段式（前端映射下载 2–60 / 评分 62–100）；单点预测（GFS+EC 双模型）进度条**直读服务端 pair 映射值** 0–100 连续推进（GFS 相 0–50、EC 相 50–100），无二次映射；运行中点取消 → 服务端报 `cancelled`，UI 状态复位；
4. 杀死 sidecar 进程 → UI 全部在途请求立即失败并提示「引擎已退出」，重启按钮可恢复；
5. 地图页（基底对齐 `desktop/assets/map.html` 的 11 个 fs* API 全部可用）：OSM/天地图/Esri 切换与境外回退、天地图 Key 缺失引导、单击落点 → SpotStore 写入（含 DEM 700ms 防抖查询，`ui_map.py:76-79` 契约）、收藏列表读写（损坏 JSON 留档恢复，`widgets.py:165`）；
6. 区域热力冒烟：区域预测 API 结果 `fsSetGrid` 渲染 + 缺测点样式 + `fsFocusGridCell` 点选联动（对齐 `ui_regional.py:539` 行为）；
7. macOS（WKWebView）通过 1–6；Windows（WebView2）**分两档验收**：必过档 = 1、4、5、8（握手/进程退出/地图/帧界）+ network_probe；全链路档（2、3、6，含 GRIB 解码）以前置任务 T-W1（Windows dev 机 GRIB 依赖方案，§5 R8）落地为前提，未落地前 Windows 显式降级为必过档并在 CI 标注；
8. 帧界安全：sidecar stderr 输出乱码/超长行不影响 UI（1 MiB 上限拒绝路径实测）。

### M2 — 逐面板平移（核心工作量）

**目标**：按映射表把 Qt 四页 + 三对话框 + 全部自绘组件平移为 Svelte；`workers.py` 的 QThread 编排全部改为「前端状态机 + engineClient」；**隐性契约逐项建等价测试**；期间 Qt 版不删，作为兼容版并行存在（退役在 M4）。

**逐面板映射表**：

| Qt 现状（源） | Tauri/Svelte 目标 | 平移要点（契约锚点） |
|---|---|---|
| MainWindow/AppShell/NavButton/BrandMark（`desktop/app.py:215`，PAGES `:64`） | `src/App.svelte` + 侧栏组件 + `src-tauri/src/menu.rs` | 菜单角色归并、关闭前下载确认（`app.py:576`）→ Tauri 关闭事件 + dialog 插件；cwd 写探针（`app.py:596`）→ 打包模式改用 app 数据目录，开发模式保留探针 |
| 单点预测页 PointForecastTab（`desktop/ui_point.py:100`） | `src/routes/point/`（左栏观测点/参数/执行区，右栏结果标签组） | DEM 哨兵 -1 提交前拦截（`ui_point.py:1204`）；结果档位/云属/趋势中文表复用 frontend 常量；HTML 报告入口（`ui_point.py:1477`）→ opener 插件 |
| 区域预测页 RegionalForecastTab（`desktop/ui_regional.py:63`，REGION 码 `:44-53`） | `src/routes/regional/` | 7 预置 + 自定义 bbox 校验（`ui_regional.py:385-405`）；区域编排仍在 Python sidecar（M3 前不变）；**缓存命中识别重做**：Qt 判据 `stage==cache && percent==99`（`ui_regional.py:448-451`）在 IPC 下失效——pair 映射后 GFS 命中=49/EC 命中=99 且 message 被剥离（`desktop_rpc.py:444-445`），按 §3.4 默认方案落地（协议最小增强补 `message`/`cache_hit`）；**降级不一致顺手修**：Tauri 版地图常驻，不再需要 MiniMap 降级分支（`ui_regional.py:39` 的问题就此消解） |
| 实况观测页 NowcastTab（`desktop/ui_nowcast.py:51`） | `src/routes/nowcast/` | HSD 卫星帧行进数据清单；参数变更徽章（`ui_nowcast.py:347`）；共享观测点点位条与单点页同 store |
| 数据与下载页 DataSourceTab（`desktop/ui_data.py:22`） | `src/routes/data/` | published_cycles 5 分钟缓存（`ui_data.py:176`）；历史日期拦截（`ui_data.py:286`）；清理 2 天前缓存 job；打开缓存目录 → opener 插件（对齐 `ui_data.py:246`） |
| 设置弹窗 SettingsPanel/Dialog（`desktop/ui_datasource.py:17`） | `src/routes/` 外的 `SettingsDialog.svelte` + menu.rs 触发 | CAMS Key 写 `~/.cdsapirc` 0600（`ui_datasource.py:95-118`）**契约先保持**（钥匙串方案见 Q5）；天地图 Key 校验保存 + settings store 广播替代 mapSettingsChanged 信号 |
| 数据准备会话 ForecastDataPanel（`desktop/data_panel.py:20`，begin `:81`） | `src/lib/stores/dataSession.ts` + `src/lib/services/preparation.ts` | payload 深拷贝 + revision + is_current 守卫（`data_panel.py:94-96,158-168`）；发布核验异步回填（`:187`）；下载后按已下载计划复检 |
| 数据准备弹窗 ForecastDataDialog（`desktop/data_dialog.py:65`） | `src/lib/components/data/PreparationDialog.svelte` | 状态三态（需下载/可更新/已下载，`data_dialog.py:44-62`）；F5 刷新；**仅接受 ready 报告放行确认**（`data_dialog.py:224`） |
| 地图选点浮层 MapPickerDialog（`desktop/ui_map.py:60`） | `src/lib/components/map/MapPickerDialog.svelte` | 收藏点 + 「纬度,经度」搜索；DEM 700ms 防抖（`:76-79`）；未配天地图 Key 引导跳设置 |
| FireSkyMap/_BridgePage（`desktop/web_map.py:217`，console 桥 `:193`） | `src/lib/components/map/MapCanvas.svelte` + `src/map.html` | M1 已落底；本项补齐 mini 模式等 API 面与主题重注入；console 桥整体废弃 |
| MiniMap 示意图（`desktop/widgets.py:630`） | 不平移（地图常驻系统 WebView，无降级需求）；仅保留「WebView 初始化失败」的错误态 UI | 有意裁剪，见风险 R4 复核项 |
| NationalProfileCanvas / SatelliteProfileCanvas（`desktop/profile_canvas.py:164,511`，共 790 行） | `src/lib/components/profile/{NationalProfile,SatelliteProfile}.svelte`（Canvas 2D） | 参照 frontend 同名 Web 组件移植；X/Y 轴语义、压力族云色块、缺测斜纹逐项对照；set_data 只收 dict 快照的契约保持（props 传 JSON） |
| widgets.py 组件库（QualityCurve `:883`/GlassCard/MetricCard/ModelResultsBar/SegmentedControl/RunButton/Toast `:1020`/ElidedLabel） | `src/lib/components/{charts,widgets}/` | Toast → svelte 浮层；QualityCurve set_steps 契约（steps JSON）保持；样式从 QSS 迁 Tailwind（QSS 不平移，见 R5） |
| Workers（`desktop/workers.py:64`，ForecastWorker `:117`，_on_engine_progress `:209`，DataFetchWorker `:282`） | 删除；职责并入 `lib/ipc/engineClient.ts` + `lib/stores/dataSession.ts` + 每页 run 状态机 | 四信号（progress/finished_ok/failed/cancelled）→ IPC 三帧 + cancel 命令；**进度映射按链区分**（nowcast 前端套 `workers.py:209-243` 两段式；point/regional 直读 pair 值，§3.4）；心跳日志（8s 无事件）移入前端；SatelliteWarmupWorker 现状无调用方（`:351`），**不平移**，登记为遗留缺口 |
| CurrentLocationController（`desktop/location_controller.py:12`） | `src-tauri` 定位命令 + `CurrentLocationButton.svelte` | Tauri geolocation 插件平台表虽含桌面，但安装/权限示例全移动端向、无桌面权限配置章节（v2 评审轮核实 v2.tauri.app/plugin/geolocation）→ **M2 先实机验证插件桌面路径**；不可用则降级自研命令（macOS CoreLocation via objc2/Swift、Windows WinRT Geolocation）。前置：NSLocation 声明迁入 tauri.conf.json（对齐 `FireSky.spec:142`，Qt 版 `.app` 拒绝逻辑 `location_controller.py:27-31` 即权限声明缺失的先例）+ 签名（R10/R14）；20s 超时 + 时间戳新鲜度校验保留；明确不做 IP 兜底 |
| theme.py（LEVEL_COLORS `:13`、QSS `:247`、档位/云属/趋势/场景映射） | `src/lib/theme/`（CSS 变量 + level-colors.ts + 中文映射表） | 十档三方对齐（Qt/frontend/Tauri）；QSettings 主题持久化（`:494`）→ tauri-plugin-store |
| icons.py（`desktop/icons.py:11`） | `@lucide/svelte`（frontend 同款） | 主题色经 currentColor/CSS 变量 |
| SpotStore/Location/持久化三件套（`desktop/widgets.py:229,87,158-226`） | `src/lib/stores/spot.ts` + tauri-plugin-store/fs | 唯一写口 set_location；locations.json 路径与损坏留档恢复语义不变；配置 0600 权限校验进单测 |
| data_access shim（`desktop/data_access.py`）→ firesky.desktop_data | 并入 engineClient 方法（inventory/check/prepare 直接走 IPC）；`FIRESKY_DESKTOP_DATA_BACKEND` 环境变量保留（M3 切换用） | shim 消失，路由下沉到 sidecar |
| _prepare_qtwebengine（`desktop/__init__.py:10`） | 删除（系统 WebView 无需打包修复） | 白屏防线改为：Tauri 启动时 WebView 版本探测 + 地图页加载失败错误态（对应 R2） |

**验收标准**：
1. **面板逐个验收**：每面板按映射表「契约锚点」列建立核对清单，Qt 版与 Tauri 版同输入并排对照（截图 + 数据 JSON diff），全部勾选；
2. **契约等价测试**（新增 `apps/tauri/tests/`）：① is_current 守卫（下载完成时参数已变 → 拒绝确认）② published_cycles 5 分钟缓存 ③ DEM 700ms 防抖 ④ elevation=-1 拦截 ⑤ locations.json 损坏留档恢复 ⑥ 配置 0600 ⑦ 进度映射按链验证（nowcast 两段式；point/regional pair 直读无二次映射）⑧ 区域缓存命中识别覆盖双模型两侧（GFS 相 percent=49 / EC 相 percent=99，或按 §3.4 默认方案用 `cache_hit` 字段），且不依赖 IPC 帧中不存在的 `message`（若协议增强已落地则另测 message 日志回填）；
3. `svelte-check` + `vitest` 通过；四页全部脱离占位数据，走真实 sidecar；
4. 键盘路由完整：F5/Ctrl+Return 运行、Esc 取消、Ctrl+K 聚焦搜索，按当前页分发（对齐 `app.py:444-460`）；
5. 打包模式冒烟：PyInstaller onefile sidecar 进 externalBin，双平台安装包离线可跑通单点预测全链路；打包环境变量清单（§3.1）生效——nowcast 评分后端二选一（随包 `firesky-engine` + `FIRESKY_RUST_ENGINE_BIN` 或强制 `FIRESKY_NOWCAST_SCORING_BACKEND=python`，R7）有决策记录；native 评分库路径按 R9-② 处置（sidecar spec dest 修为 `firesky/native` 或注入 `FIRESKY_NATIVE_LIBRARY_PATH`），否则如实标注「原生评分静默回退（`native_core.py:318-319` 返 None → Python 回退）」；GRIB 依赖按 R8/T-W1 结论（未决则 Windows 打包验收降级为「hello 握手 + 网络探测」并显式标注）；
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
- `engine/src/` 补齐：`cams_credentials`/`save_cams_key`（对齐 `desktop_credentials.py` 的 env 锁死语义，`desktop_credentials.py:70-71`）、`network_probe`、`search`/`metadata`、`dismiss`
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
7. Windows 实机通过全链路（R8 依赖问题此时必须已解）。

### M4 — Qt 退役

**目标**：`desktop/`（PySide6）停止功能演进，转入兼容版维护期；打包链、CI、文档全面收敛到 Tauri；apps/ 两套原生客户端同步处置（见 Q2）。

**涉及文件（改动为主，不删代码）**：
- `desktop/`：目录头部加 MAINTENANCE.md（兼容版声明：只收安全/崩溃修复，新功能一律进 Tauri 版）
- `desktop/FireSky.spec`：保留但标注兼容；修复遗留断链（icon.ico 已由 M0 的 Tauri 图标链替代说明）
- `.github/workflows/ci.yml`：desktop job（PyInstaller）按 Q9 决策停用或移入兼容 workflow；`.github/workflows/native-desktop.yml`：SwiftUI/WinUI 冒烟按 Q2 处置；`tauri-desktop.yml` 成为桌面主工作流
- `Makefile`：新增 `tauri-dev` / `tauri-build` / `tauri-bundle` 目标，`desktop-macos` 等旧目标标注兼容
- `README.md` / `README.en.md`：桌面章节以 Tauri 版为主，Qt 版降级为兼容说明；`apps/README.md`：架构图更新
- `docs/`：RUST_MIGRATION 系列 P1 表勾销「桌面完整运行时」

**验收标准**：
1. 发布说明明确：Tauri 版为正式桌面版，Qt 版为兼容版（版本号与 bundle id 已在 M0 统一，不再漂移）；
2. Tauri 版功能对齐清单（M2 映射表全表）逐项勾销；Qt 版与 Tauri 版已知行为差异清单成文（预期差异如 MiniMap 裁剪）；
3. CI 上桌面产物唯一来源是 tauri-desktop.yml（或按 Q9 保留双轨）；
4. `docs/RUST_MIGRATION_STATUS.zh-CN.md:43` 的 P1 项状态改为已收口（桌面链路）；
5. 退役半年后（或按用户决策时点）执行物理删除 `desktop/`、`apps/macos`、`apps/windows` 的收尾 PR（不在本蓝图排期）。

---

## 5. 风险清单

| # | 风险 | 依据 | 缓解 |
|---|------|------|------|
| R1 | **结构性断点：引擎调用从同进程 Python import 变跨进程 IPC**。workers.py 全部 `from firesky.web_server import ...`，Tauri 前端无法 import Python；桌面 Rust 运行时是迁移文档 P1 缺口 | `workers.py:144,155-160,257-262`；`RUST_MIGRATION_STATUS.zh-CN.md:43` | M1–M2 用已验证的 desktop_rpc sidecar 承载全部编排（协议零改动）；M3 才切引擎；编排语义（令牌/单飞/两段进度）在两阶段保持一致，前端只写一次 |
| R2 | **地图栈重验**：QWebEngineView+console 前缀桥 → 系统 WebView；Leaflet 在 WKWebView/WebView2 下的拖拽/瓦片/CSP 行为需重验；QtWebEngine 白屏防线（`__init__.py:10`）失效需等价方案 | `web_map.py:182-217`；基底 `desktop/assets/map.html`（console 桥 5 处待替换） | M1 就把地图页列为验收项（不走 M2 才发现）；错误态 UI + 瓦片失败重试；天地图 Key 注入走 Svelte 响应式而非 console 桥 |
| R3 | **取消/并发模型重设计**：Qt 取消是引擎回调里同步抛异常 + 600s wait；IPC 取消是协作式（回调点生效）、服务端 4 线程/16 活跃/compute_lock 单飞 | `workers.py:83-90`；`ui_regional.py:614`；`desktop_rpc.py:466,493-494` | M1 验收标准 3 显式验证取消；UI 明示「下载/预测串行」约束；M3 时 engine 侧并发模型对齐该语义而非扩容 |
| R4 | **自绘组件体量**：profile_canvas 790 行 + widgets 1067 行 + theme 503 行 QSS，逐像素对齐工作量大 | 本会话 wc -l 复核 | 参照 frontend 已有同名 Web 组件（NationalCrossSection/SatelliteVerticalProfile）移植而非从零画；M2 逐面板验收并排对照；MiniMap 有意裁剪（地图常驻）需用户确认（Q8 附带） |
| R5 | **主题口径漂移**：十档 LEVEL_COLORS 与云属/趋势中文表是 Qt/Web/桌面三方共享口径；QSS 动态刷新（unpolish/polish）在 CSS 无直接对应 | `theme.py:13,29,247` | level-colors.ts 单一来源 + 三方一致性单测；主题用 CSS 变量 + Svelte 响应式，放弃逐组件 repolish 模型 |
| R6 | **会话细节契约易丢**：发布周期 5min 缓存、DEM 700ms 防抖、is_current 守卫、0600 权限、损坏留档恢复、EOF 静默改判 cancelled | 勘察 risks#8 + `data_panel.py:94-96`、`ui_map.py:76-79`、`widgets.py:165-176,223` | M2 验收标准 2 的七项契约测试逐条落地，进 CI |
| R7 | **sidecar 打包形态未决**：Python 引擎=完整运行时（numpy/h5py/eccodes/satpy 链），ecCodes 原生库不随 wheel（实测 wheel 无 dylib）；WebServerConfig 缓存按 cwd 相对解析（`web_server.py:309-317`），打包 cwd 不对则缓存散落；**nowcast 评分默认 rust 且无 Python 兜底**：`FIRESKY_NOWCAST_SCORING_BACKEND` 默认 "rust"（`national_batch.py:6903-6906`），启用即 return 走 Rust 路径（`:6786-6787`），`rust_grid.engine_binary()` 缺二进制直接 raise（`rust_grid.py:25-27`）——打包 sidecar（cwd=数据目录）解析不到 `<repo>/engine/target/release` 时 nowcast 评分必失败；体积大头仍在引擎侧（Qt 版 dmg 已 302MB） | 勘察 blockers#1-3 + v2 评审轮复核 | M1 开发模式绕开（venv 直跑）；打包模式 PyInstaller onefile + 显式 cwd=app 数据目录 + 环境变量清单（§3.1：缓存根 + 评分后端二选一 + 可选 `FIRESKY_NATIVE_LIBRARY_PATH`）；**打包必须二选一**：随包分发 `firesky-engine` 并设 `FIRESKY_RUST_ENGINE_BIN`，或强制 `FIRESKY_NOWCAST_SCORING_BACKEND=python`（决策入 Q4）；eccodes 分发未决前 Windows 打包验收降级（M2 验收 5） |
| R8 | **Windows 契约从未实机验证**：apps/windows 自述未构建/未交互测试（`apps/README.md:122-125`）；GRIB 原生依赖、WebView2、路径分隔符全部待验；且 **Windows dev 机 eccodes 安装无既定路径**（brew 仅覆盖 macOS，R7 的打包分发不覆盖 dev 机） | 勘察 blockers#11 + v2 评审轮 | 前置任务 **T-W1**「Windows dev 机 GRIB 依赖方案」（conda-forge / vcpkg / 手动 DLL，M1 涉及文件）；M1 Windows 验收分必过档/全链路档两档（M1 验收 7）；Tauri 版 Windows 验收每个里程碑都跑真机，不靠纸面契约 |
| R9 | **现存缺陷被迁移放大**：① `FireSky.spec:115` 引用的 icon.ico 不存在；② 打包 dylib 路径与 `native_core.py:273-277` 候选（`parents[2]/native/target/release` 与 `_MEIPASS/firesky/native`）均不符、无人设 FIRESKY_NATIVE_LIBRARY_PATH；③ ui_regional.py:39 无降级 import | 本会话 ls/grep 复核 | ① M0 验收 6 显式复制 `apps/tauri/icons/icon.ico` → `desktop/icon.ico` 修复；② **Tauri 打包版 sidecar 同为 PyInstaller，同样受影响**——`native_library()` 失败返回 None（`native_core.py:318-319`）后上层走 Python 回退：打包版单点预测可跑通，但原生评分是静默回退路径而非可用能力（如实标注，非「不受影响」）；修复选项：sidecar spec dest 改 `firesky/native`（匹配 _MEIPASS 候选，`FireSky.spec:55-68` 现 dest="native"）或启动注入 `FIRESKY_NATIVE_LIBRARY_PATH`（§3.1 清单），M3 后 engine 二进制消解该问题；③ 已在 M2 映射表消解 |
| R10 | **签名/公证缺失**：全线 adhoc（实测 FireSky.app adhoc、README 自述右键打开）；Tauri 正式分发需 Developer ID + 公证 + hardened runtime + NSLocation 声明迁移 | `FireSky.spec:142`；勘察 tauriNeeds | M0 conf 预留 entitlements/NSLocation 配置；签名启用时点取决于证书（Q6），未启用前发布说明延续「右键打开」话术 |
| R11 | **元数据漂移**：bundle id（cn.firesky.desktop vs app.firesky.native）、版本（0.1.1/0.2.0/0.1.0）、最低系统（12.0 vs 14.0）三处不一致 | `FireSky.spec:135-144`、`build_macos_native.sh:20-23`、`pyproject.toml:3` | M0 验收标准 5/7 强制收敛；单一来源定为 tauri.conf.json（Q1） |
| R12 | **ADS Key 明文契约**：`~/.cdsapirc` 是引擎侧直接读的文件契约，改钥匙串会牵动引擎读 Key 路径 | `ui_datasource.py:95-118`、`desktop_credentials.py:70-71` | M2 保持文件契约不动（最小风险）；钥匙串方案独立评估（Q5），若采纳放 M3（engine 侧同步实现 Key 读取） |
| R13 | **stdout 纪律**：sidecar 内任何框架外 print 会破坏帧界；EOF 时在途请求静默改判 cancelled | `desktop_rpc.py:505-510`；勘察 blockers#10 | M1 sidecar.rs 不解析 stderr 做协议；退出顺序「等在途→关 stdin→杀」可配置；M3 engine 侧同纪律 |
| R14 | **桌面定位能力未经验证**：Tauri geolocation 插件平台表含桌面，但安装/权限示例全移动端向（`cargo add --target android/ios`、`#[cfg(mobile)]`、无桌面权限配置章节；v2.tauri.app/plugin/geolocation 本会话核实）；无论插件或自研（CoreLocation/WinRT）都依赖 NSLocation 声明迁移（`FireSky.spec:142`）与签名（R10）；Qt 版 `.app` 拒绝逻辑（`location_controller.py:27-31`）正是权限声明缺失的先例 | v2 评审轮核实 | M2 先插件桌面实机验证（M2 映射表）；不可用降级自研命令（macOS CoreLocation via objc2、Windows WinRT Geolocation）；NSLocation 声明 M0 进 tauri.conf.json；不做 IP 兜底（维持现状） |

---

## 6. openQuestions（待用户决策）

1. **元数据单一来源**：Tauri 版 bundle id 定 `cn.firesky.desktop`（沿用 PyInstaller）还是 `app.firesky.native`？版本号以 tauri.conf.json 为准还是跟随 pyproject（0.1.0）？macOS LSMinimumSystemVersion 取 12.0 还是 14.0（影响 Tauri 2 系统要求下限的表述）？
2. **apps/ 两套原生客户端去留**：SwiftUI（apps/macos）与 WinUI3（apps/windows）在 M2 后是否冻结为参考实现、M4 随 Qt 一起退役？还是继续维护？
3. **Tauri 工程目录**：本蓝图推荐 `apps/tauri/`（与 apps/macos、apps/windows 并列）；是否认可？还是放 `desktop-tauri/` 与 desktop/ 平级？
4. **sidecar 打包形态**：PyInstaller onefile（复用现有 spec 经验、体积大）vs python-build-standalone 受控 venv 目录（可控但装配复杂）？eccodes 原生库随包方案（静态链入 / 随包携带 dylib+dll / 放弃 GRIB 严格校验降级探测）与 **Windows dev 机方案（T-W1：conda-forge / vcpkg / 手动 DLL）**？打包 nowcast 评分后端二选一：随包 `firesky-engine` + `FIRESKY_RUST_ENGINE_BIN`，还是强制 `FIRESKY_NOWCAST_SCORING_BACKEND=python`（R7）？
5. **ADS Key 存储**：保持 `~/.cdsapirc` 文件契约（引擎直接读，M2 零改动）还是迁平台钥匙串（需引擎侧同步改 Key 读取，建议绑 M3）？
6. **签名/公证**：是否具备 Apple Developer ID 证书与公证账号？Windows 侧是否做 Authenticode？未签名发布期多久可接受？（tauri 更新器依赖签名密钥体系，启用时点与本题绑定；桌面定位 R14 也受签名/权限声明影响）
7. **WebView2 分发模式**：downloadBootstrapper（默认，装时需联网）还是 embedBootstrapper/offlineInstaller（离线包变大）？
8. **M2 起始面板与裁剪确认**：建议顺序 数据页（最简）→ 实况 → 区域 → 单点右栏完整面板（剖面最后）；MiniMap 不平移的裁剪是否接受？
9. **CI 双轨时长**：ci.yml 的 PyInstaller desktop job 在 M2 期间与 tauri-desktop.yml 并行出包（成本翻倍），何时停用——M4 前移还是完成后？
10. **协议演进**：M2 默认方案需对 desktop_rpc 做最小增强（`safe_event` 补 `message`/`cache_hit` 透传，§3.4 区域缓存命中识别）+ M3 可加 ping 帧（协议现无心跳，存活靠进程退出）——两者都需同步 apps/ 两端客户端与协议文档，是否接受这批最小演进（否则区域缓存命中走前端反演备选方案）？

---

## 7. 明确不做（本蓝图边界）

- Android/iOS：Tauri 2 具备能力但本期不启用 mobile API、不做触屏适配；
- 引擎接管 FastAPI/CLI/任务队列（`RUST_MIGRATION_STATUS.zh-CN.md:42` 另一条 P1）：M3 只覆盖桌面 IPC 面；
- IP 定位兜底（现状明确不做，维持）；
- frontend/（Web 版）的功能合并或改造：仅作组件与口径的移植参照。
