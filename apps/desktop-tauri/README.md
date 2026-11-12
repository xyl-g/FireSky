# FireSky · 冻结 Tauri/Web 参考端

Tauri 2 + Svelte 5 + Vite 6，当前作为冻结的共享 Web/合同参考端，Docker 继续复用
同一份 Web 业务页面。正式桌面路线已改为原生 UI + Rust FFI，macOS 先行；
Windows/Linux 后续复用同一 ABI。当前不是正式桌面发布目标，也不是完整产品验收。
地图为天地图默认 + OpenFreeMap。
实施清单见 [`Tauri/Rust 联合迁移`](../../docs/TAURI_RUST_JOINT_MIGRATION.zh-CN.md)，
历史蓝图见 [`TAURI_DESKTOP_PLAN`](../../docs/TAURI_DESKTOP_PLAN.md)。

## 设计参考与范围

2026-11-11 用户纠正：**直接以已有 Swift 端为设计原稿，不再另起设计**。`Workbench.swift` / `DialogViews.swift` 的页面层级、布局、尺寸、标签与配色直接转写为 Svelte/CSS：196px 侧栏、72px 顶栏、默认单点、四个业务页与底部设置、292px 地点/参数栏、双模型摘要、九项指标和分析标签。地图选点回到 900×620 约束弹窗，不再是独立导航。材质是 CSS 近似，不是原生 glassEffect。

同日最新范围：保留当前 Tauri 的 UI 设计，把原 Web 能展示的业务内容放入现有页面；不复制旧 Web 的布局与卡片样式。**地图云层叠加不再考虑**，不将云图、可烧云/挡光云叠加及其逐帧播放列为移植验收项。地图仍承担选点、区域质量网格和范围拖绘，来源切换仅在设置。

预测视图已替换原技术说明占位，接入真实 IPC 的 check→prepare→forecast；结果只展示引擎返回数据，无结果显示 `--`，数值 0 不作缺测。浏览器自动化使用独立 fixture，不能据此声称真实下载和预测链路验收通过。

- `src-tauri/`：Rust 窗口壳、invoke 命令、长命 `firesky-engine desktop` sidecar、JSON-lines 帧桥。
- `src/App.svelte` / `src/app.css`：导航、引擎状态/重连、跨页共享选点与壳样式。
- `src/lib/ipc.ts`：唯一 IPC 封装及有序取消句柄。
- `src/lib/views/WorkbenchView.svelte` / `src/lib/forecast-session.ts`：地点搜索、海拔时区、坐标编辑、收藏、日期与事件、区域多选/自定义坐标、准备确认、协作取消与显式预测。
- `src/lib/views/ForecastResults.svelte` / `src/lib/forecast-display.ts`：双模型/实况摘要、九项指标、缺测断线趋势、逐时台账及诊断。
- `src/lib/views/RegionalResults.svelte`：GFS / EC / 双图、完整网格、选中格双模式读数与排行；排行只列前 2000，地图不截断。
- `src/lib/views/MapView.svelte`：两地图源适配入口、网格与视图意图重放，来源由设置页控制。
- `src/lib/views/DataView.svelte` / `src/lib/data-session.ts`：库存、检查、显式发布核验、准备确认、协作取消与下载探测。
- `src/lib/views/SettingsView.svelte`：Swift 分组外观、数据源、CAMS 和本地引擎，补入用户指定的地图来源/底图/运行内存凭据。
- `src/lib/views/LeafletMapView.svelte`：天地图栅格与 OpenFreeMap / MapLibre 矢量桥接，复用 Leaflet 选点、网格、视图和取消/重试语义。
- `src/lib/map-grid*.ts`：网格清洗、选择、canvas 绘制/聚焦。
- `scripts/`：版本门禁、原生资源准备与有超时的真实 Rust 握手冒烟。

## 开发与验证命令

前置：Node ≥ 20、npm、Rust stable；生产构建和运行不需要 Python 或 `.venv`。GRIB 解码另需 ecCodes，hello 冒烟不验证 GRIB。故障注入 Rust 单测仍使用仅测试的 Python fixture，可通过 `FIRESKY_TEST_PYTHON` 指定解释器；这不是生产 sidecar。

```bash
cd apps/desktop-tauri
npm ci                 # 首次准备依赖；已有依赖可跳过
npm run tauri dev      # Vite 1420 + Rust 编译 + 原生窗口
npm run check          # 版本一致性 + svelte-check
npm test               # Node 内置测试：版本/IPC helper/网格
npm run test:ui        # Playwright fixture UI；首次需 npx playwright install chromium
# 已安装 Chrome 时可使用 PLAYWRIGHT_CHANNEL=chrome npm run test:ui
npm run build          # 版本一致性 + 生产前端构建，不生成安装包
npm run native:prepare # 构建/复制 Rust 引擎及打包资源
npm run tauri build    # 准备原生资源 + 前端 + Tauri 安装包
npm run smoke:rpc      # 真实 Rust hello → EOF，15 秒总上限；无预测下载
npm run smoke:data     # 真实 Rust，隔离空缓存 hello/inventory/check/dismiss → EOF
cargo test --locked --manifest-path src-tauri/Cargo.toml
cargo check --locked --manifest-path src-tauri/Cargo.toml
cargo clippy --locked --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
```

Vite 端口固定为 1420。若被占用，先识别来源，不要清理未知进程。`dist/` 是 Vite 生成目录，存在不代表最新构建通过。

### 共享浏览器入口

普通浏览器自动使用同源 HTTP 任务通道，Tauri 仍使用原 IPC；检查、准备、确认和
预测复用相同 Runtime DTO，不另建一套业务页面。浏览器重新连接只重建客户端连接，
不会重启共享服务；取消被接收后仍等待真实任务终态。

仓库根目录运行已构建页面：

```bash
FIRESKY_WEB_ROOT=apps/desktop-tauri/dist \
  engine/target/debug/firesky-engine http --workspace "$PWD" --port 8766
# http://127.0.0.1:8766
```

热更新开发时，在本目录执行
`FIRESKY_DEV_API_ORIGIN=http://127.0.0.1:8766 npm run dev -- --host 127.0.0.1 --port 1421`。
Vite 只代理 `/api/v1/firesky`，不将 `FIRESKY_*` 服务端配置注入客户端。
浏览器 CAMS 配置只读，不提供保存 Key、打开服务器缓存目录、退出应用或重启服务。
报告仅打开引擎返回的受限同源 SHA URL，不将服务器本机路径转换为文件访问。
这不代替远程鉴权；默认仅限本机/可信受保护网络，不应直接暴露公网。

### 原生 Sidecar 解析

| 项 | 契约 |
|---|---|
| workspace | `FIRESKY_WORKSPACE` 有效目录；debug 默认向上寻找 `engine/Cargo.toml`；release 默认应用本地数据目录的 `Data/`，不要求源码或 `pyproject.toml` |
| Rust 引擎 | `FIRESKY_RUST_ENGINE_BIN` → 随包 `native/firesky-engine[.exe]` → 资源/程序相邻引擎 → `<workspace>/engine/target/release/` |
| 固定命令 | `firesky-engine desktop --workspace PATH`，cwd 为 workspace；无 Python fallback |
| 环境 | 继承；随包 ecCodes 注入动态库、definitions/samples 路径，Windows 增加 DLL 搜索目录 |
| stderr | `FIRESKY_NATIVE_LOG_DIR/engine.log`；默认 macOS `~/Library/Application Support/FireSky/engine.log`、Windows `%LOCALAPPDATA%/FireSky/engine.log` |

启动失败不阻止窗口打开。修正引擎/数据目录后可按「重新连接」。显式引擎不可执行时立即报错，不自动换用其他引擎。重连会丢弃原 sidecar 的内存会话令牌，后续数据准备必须重新开始。旧 `FIRESKY_PYTHON` / `FIRESKY_DESKTOP_DATA_BACKEND` 不影响生产启动。

`tauri dev/build` 自动执行 `native:prepare`，默认 Cargo release 构建；`FIRESKY_RUST_ENGINE_BIN` 可选择已有引擎并跳过该构建。macOS 复用仓库 ecCodes 依赖闭包打包脚本，支持 `FIRESKY_ECCODES_LIBRARY` / `FIRESKY_ECCODES_PREFIX`；Windows 可通过 `FIRESKY_ECCODES_ROOT` 带入 DLL 和资源树，未配置时明确提示依赖系统 ecCodes。资源存放在忽略的 `src-tauri/resources/native/` 并由 bundle manifest 映射到 `native/`，不包含 Python。`rpc_status.engineBinary` 返回实际引擎路径，旧 `python` 字段只为兼容保留且恒为 null；握手要求 `engine=firesky-rust`。

## IPC 基础契约

sidecar 协议本身保持 version 1：stdin/stdout UTF-8 JSON-lines，stdout 只承载协议，单帧上限 1 MiB。结果/错误/进度复用连接，错误码 `invalid_request` / `operation_failed` / `cancelled`；壳补充 `engine_unavailable` / `engine_exited`。

保留 `rpc_request` / `rpc_cancel` / `rpc_status` 和 `engine-progress`（`{requestId,event}`），新增 `rpc_start` / `rpc_wait` / `rpc_restart`、`engine-status`（含 generation）。

```ts
const request = rpcStart<Result>("method", params);
// requestId 同步可用，不依赖首次 progress；cancel 会等待注册及写入确认。
const resultPromise = request.result;
await request.cancel();
try { await resultPromise; } catch (error) { /* 处理结果，包括协作式取消 */ }
```

`rpc_start` 确认注册并发送后，`rpc_wait` 按 generation 领取结果。每个句柄必须消费 `result`；未领取票据有容量上限。取消是协作式，不保证中断进行中的外部下载。正常退出先关闭 stdin 触发 EOF 清理，宽限 3 秒，兜底 kill/wait **直接子进程**；不保证清理整个后代进程树。非法、截断或超长 stdout 帧会终止该连接并失败在途请求，而不是静默丢帧导致悬挂。没有协议心跳，也不靠任意短任务超时伪装生命周期正确。

## 地图与开发网格

- 2026-11-11 用户确认 **天地图 + OpenFreeMap，默认天地图**。首次启动默认天地图影像；地图服务和天地图底图偏好保存在 WebView 本地存储。旧 OSM / Esri 不再作为服务选项；Apple Maps 已移除，原 Swift 原生端不受影响。
- **设置 → 地图**中选择天地图或 OpenFreeMap。天地图提供影像 / 街道 / 地形；OpenFreeMap 为免 Key 矢量底图，经官方推荐的 MapLibre GL Leaflet 桥渲染，不冒充 OSM 公共栅格瓦片。地图页面无地图源/类型切换菜单。
- **设置 → 地图凭据**仅输入天地图浏览器 Key；仅本次运行内存有效，不写磁盘，不回显已有凭据。
- 地图选点弹窗打开后保留挂载；切到设置修改地图源后，最新网格/聚焦意图重放，App 观测点不丢失。工作台包含 176px 小地图，区域结果包含单图或双图；Leaflet 使用 canvas 网格。
- 天地图未配置 Key 时明确提示，不静默回退其他服务。公开底图能访问不等于已完成服务授权、配额、来源限制或安装包兼容验收。
- `onReady` / `fsIsReady` 仅表示 Leaflet 对象与配置已就绪，**不表示网络瓦片成功**。
- `onError` 区分初始化、瓦片、网格；初始化失败可重试，配置/视图/网格意图在 READY 后重放。
- `spot` / `onPick` 由 App 持有，切页后保留。搜索和 metadata 走真实 RPC，晚回包不覆盖新地点。收藏、主题、EC 来源、地图来源/底图偏好保存在 WebView 本地存储，不与旧 Swift UserDefaults / Qt locations.json 自动互迁；当前观测点与地图凭据仍为内存态。
- `fsSetGrid` / `fsFocusGridCell` / `fsClearGrid` 复用旧地图语义：零值有效、null 为缺测、剔除非有限经纬度，不插值、不截断原网格。
- 开发 URL `?mapDebug=1` 显式启用**合成**正常值/零值/缺测网格、聚焦、清除、离线对象验证工具；日常界面与生产构建不显示这些工具。
- 开发 URL `?mapOffline=1` 可在 Leaflet 初次初始化前禁用瓦片请求。离线对象/网格成功不算底图加载成功。

## 数据准备

首次进入数据页自动读取本地库存，不自动发起发布核验、下载探测、prepare 或 forecast。按 Swift 顺序展示下载连通性、数据源库存与折叠周期，额外区域准备保留在末尾。EC 来源统一在设置修改，应用于探测、准备与预测；选择区域、日期、事件后先「检查数据」，按需「核验最新发布」，再「准备 / 更新数据」并确认。数据页准备不自动预测；缓存文件数/周期索引不等于内容已校验就绪。

取消为协作式，界面等待原请求终态后才解除忙碌；切页不丢任务，连接变化则作废令牌与旧回包。`message` / `cache_hit` 是 protocol=1 可选进度字段，缺省安全兼容，不按百分比猜测命中。

当前支持打开已存在的受限缓存目录，不包含任意源批量下载、缓存移出、独立库存全局发布核验。设置页 CAMS 编辑复用现有凭据 RPC，仅显式保存至本机 0600 配置，环境变量覆盖时禁用保存；不回显已有 Key。准备大区域可能下载大量数据，自动测试不运行真实 prepare/forecast。

单点、区域与实况工作台另有准备弹窗：点击开始只检查，下载按钮显式准备，数据 ready 后仍需再次点击「确认预测 / 确认分析」。请求参数变化或引擎换代会作废会话；预测 Token 单次消费，失败后必须重检。发布核验不自动运行，避免打开页面就请求上游。

## 配置与仍未完成的边界

- identifier：`cn.firesky.desktop`；版本权威：`src-tauri/tauri.conf.json`。npm、npm lock、Cargo、Cargo lock 由 `check:version` 校验，不自动改写其他文件。
- macOS 最低版本 `12.0`、Windows WebView2 `offlineInstaller` 已写配置。配置通过不等于 macOS 12 实机兼容或 Windows 离线安装通过。
- Rust 引擎和 ecCodes 资源准备、原生 sidecar 与 bundle manifest 已接线；Tauri 安装包、Windows 与 clean-machine 安装验收尚未完成，不等同已发布。
- 尚未正式签名/公证/发布/配置自动更新；图标全套、系统定位用途声明与权限仍需后续处理。
- CSP 目前仍为 `null`，尚未加固；已加入单实例聚焦、原生应用/编辑/视图/任务/窗口菜单及关闭确认，但真实原生 GUI 交互尚未验收。定位按钮使用浏览器 Geolocation，未新增系统定位命令，不做 IP 定位兜底。
- 模式/卫星专用剖面、区域地图拖绘、受限报告/缓存外部打开已经实现；原生引擎路径选择和旧配置迁移仍未实现，本地引擎路径只读。
- 原 Web 动态剖面时次查询仍缺桌面 RPC：当前仅绘制报告内嵌剖面。趋势滑杆和台账行选中尚未共享时次；台账选择仅高亮，不触发重新计算。云层地图叠加已由用户明确排除，不列作缺口。
- Android/iOS 延后，Linux 不在范围；旧 Qt/SwiftUI/WinUI3 源码保留。

## 验证记录

### 共享 Web / Rust HTTP（2026-11-11）

Tauri IPC 与浏览器同源 HTTP 使用同一套业务页面。新增服务实例隔离、真实进度、
协作取消与终态释放；浏览器 CAMS 只读、本机能力隐藏、报告 URL 限同源 SHA 路径。
修复旧请求迟到失败误断开新连接的竞态，并为取消状态显示补充回归。

本轮 Node **102/102**、完整 Chrome fixture UI **43/43**（单轮运行）、
Svelte **0 errors / 0 warnings**，随后生产构建通过；Tauri Rust **18/18**、
Rust HTTP 聚焦 **12/12**。真实隔离 Rust HTTP 的 40 次握手领取/释放、旧实例拒绝、
普通浏览器库存/设置/点页面、24 组尺寸/明暗布局和重连检查通过，无下载或凭据写入。
fixture 不是真实预测；Docker daemon 不可用，未进行镜像、安装包、原生 GUI 或真实
CAMS 验收。完整边界和后续清单见联合迁移文档。

### M2-E（2026-11-11）

- 保留当前四标签、双模型摘要、九指标和工作台尺寸。在现有内容区补入时间/模式周期、云场/颜色/压力族/相态/云属/演变、物理分析摘要、边界敏感度、几何窗口、评分与有符号修正、光路/气溶胶、卫星/雷达/运动约束和可用来源字段。
- 保留双模型质量曲线，新增云况/光路六条物理趋势及独立 AOD 图，支持系列选择、时次读数、滑杆与前后步进；每条序列独立断开缺测，不将缺测补零。
- 逐时台账包含原 Web 十项业务列，并保留档位、太阳高度和有效状态。0 有效，缺测/失败显示 `--`；已知无物理可烧目标时，不发布残留颜色/演变诊断。对流评分上限按后端归一化口径显示百分比。
- 大气剖面读取真实模式矩阵与卫星太阳方向采样，绘制概率热图、压力/温度层线、地形、理论受光阈值及目标云 ASL/AGL 信息，支持键盘检查；不将混合方位的卫星扇形数据冒充单条剖面。
- 区域页增加真实矩形拖绘（Leaflet.draw），取消/Esc 恢复地图平移，边界外扩到 0.25° 网格，并同步数值表单；双图只在第一张图拖绘。选中格点补入窗口质量、压力族、有效可烧量、时长、AOD/修正、置信度；来源/精度区兼容现有 Web 与 Rust 报告，不伪造缺失的 CAMS 起报时间。
- 原生菜单/快捷键与前端事件接线；单实例只聚焦现有窗口；忙碌退出可选择继续、协作取消并等终态后退出或强制退出。报告只允许当前引擎报告目录内的既有生成文件，缓存只接受预定义来源，不接受前端任意路径。

边界：本轮自动化使用隔离 fixture，未运行真实 prepare/forecast、未修改真实凭据。原生 GUI、天地图真实 Key、Windows 和安装包仍未验收；剖面动态查询需保留完整源张量及受限令牌生命周期，不能仅把旧 Web HTTP 调用搬到桌面。地图云层叠加已移出范围。

最终验证：Node **80/80**、Chrome fixture UI **40/40**，Svelte **0 errors / 0 warnings**，随后生产前端构建通过。截图覆盖 390 / 768 / 1024 / 1440px 明暗与短窗口，核验内容区、剖面、趋势、区域详情与拖绘；地图包含离线网格 canvas 像素检查，不代表真实天地图加载。桌面 Rust `cargo check --locked`、`cargo test --locked` **18/18**、`cargo clippy --locked --all-targets -- -D warnings`、`cargo fmt -- --check` 通过。`git diff --check` 通过；未运行整仓引擎/科学计算测试。构建仍提示主包约 1.47MB、MapLibre worker 约 518KB，尚未拆包。

### Rust Sidecar 切换（2026-11-11）

默认 sidecar、真实握手和隔离数据 smoke 已切为 Rust，不再要求 Python 工程目录。测试和平台验证结果在本次任务完成后补充；下面 M1/M2 中的 Python 记录是切换前历史，不代表当前生产启动方式。

### M2-C（2026-11-11）

移除 Tauri Apple Maps 的 SDK 加载器、组件、Token/类型设置、类型依赖及原授权测试；保留 Swift 原生客户端。现有 OSM 暂作开发默认，正式供应商待用户选择。日常地图不显示开发网格/READY 技术层，错误恢复仍可见；`?mapDebug=1` 显式开启开发工具，地图切源只在设置中进行。

中性窗口/侧栏材质、分隔线、输入框/分段控件和结果文字层次已细化，维持 Swift 工作台结构。Node **50/50**、Chrome fixture UI **27/27**、Svelte **0 errors / 0 warnings**、生产前端构建与 `git diff --check` 通过。截图覆盖 390 / 768 / 1024 / 1440px 明暗主题、设置/地图/工作台/数据页；Leaflet 网格 canvas 像素、选点与地图切换重放通过。测试数量变化包含移除 Apple 专属测试，不表示预测覆盖被删除。

上述 UI 地图为离线 fixture，不证明真实瓦片加载、服务条款授权、配额、原生 GUI 或定位权限验收；本轮未运行生产预测/下载或 Rust/Swift 构建。

### M2-D（2026-11-11）

用户确认地图方案为 **天地图 + OpenFreeMap，默认天地图**。设置页只保留这两个来源；天地图可选影像 / 矢量 / 地形，OpenFreeMap 使用官方 MapLibre 样式。来源与底图偏好写入 WebView localStorage，天地图 Key 仅保存在本次运行内存中。切换来源后会重放当前观测点、网格、聚焦和清除意图，地图页不显示来源切换入口。

验证结果：Node **51/51**、Chrome fixture UI **29/29**、Svelte **0 errors / 0 warnings**、生产前端构建通过。覆盖无 WebGL 错误恢复和反复切换主题后的选点 / 缩放保持；移除图层时保留 Leaflet 自身解绑事件，缩放使用即时更新，避免隐藏地图留下未完成动画。另在真实 Chrome 网络环境验证 OpenFreeMap 明暗两套样式：杭州道路、河流、中文标注、字体、精灵和矢量数据均实际加载，未出现页面错误；该检查不等于天地图 Key 授权、Tauri 原生 GUI、安装包或生产预测验收。MapLibre worker 已由 Vite 打包，构建仍有大体积 chunk 提示，未做拆包优化。

### M2-B（2026-11-11）

本轮 Node 单测 **60/60**、Chrome fixture UI **25/25** 通过，Svelte **0 errors / 0 warnings**，生产前端构建通过。截图覆盖 390 / 768 / 1024 / 1440px 明暗；检查可用宽度、横向溢出、非空 fixture 结果、失败/缺测状态、地图 canvas 像素与点选。另覆盖定位后元数据、跨时区日期、后端带偏移量的 `valid_time_local`、低云真实枚举及区域多选/自定义边界。

真实 Python hello 与隔离空缓存 inventory→check→dismiss 冒烟通过，EOF 退出码 0；未请求发布查询、下载探测、prepare、forecast 或凭据修改。复用已有 debug 可执行文件启动原生开发进程及 Python 直接子进程，Vite 预览返回 HTTP 200；Computer Use 未识别开发窗口，原生交互仍未验收。本轮只改前端和文档，未重跑 Rust/Swift 构建，不据此声称真实 Apple 授权、生产下载、真实预测、Windows 或安装包验收通过。

### M2-A 历史记录（2026-11-11）

前端 **38/38 Node 测试**、Svelte **0 errors / 0 warnings**、生产前端构建通过。Chrome fixture UI **9/9 通过**，覆盖数据准备交互、设置页切源、无 Token 不请求 SDK、授权失败重试、共享选点与零值/缺测网格保留。390 / 768 / 1024 / 1440px 明暗主题截图检查未见工作区或文档横向溢出。MapKit fixture 像素不是真实 Apple 瓦片。

桌面 Rust **12/12**、Python RPC **20 passed**、Rust 桌面预测桥 **12 passed**，Swift 构建通过；未运行 Windows 构建或整仓测试。真实 Python hello 冒烟及隔离空缓存的 inventory→check→dismiss 冒烟通过，EOF 退出码 0；没有发起发布查询、下载探测、prepare 或 forecast。生产依赖审计无已知漏洞，开发依赖仍有 3 项低风险审计提示。

`tauri dev` 本轮编译并启动，但 Computer Use 未识别开发窗口，未完成原生交互验收，不能据此判定辅助功能权限是否仍是原因。已停止本轮原生开发进程，未清理未知进程。真实 Apple Token / 授权来源、瓦片、中国区坐标对齐、大网格性能、安装包和 Windows 仍待验收。

### M1-A 历史记录（2026-11-11）

2026-11-11：前端 **14/14 测试**、Svelte 检查 **0 errors / 0 warnings**、生产前端构建通过；桌面 Rust **12/12 测试**、check、全 targets 严格 Clippy 通过。真实 Python smoke 返回 protocol=1 / engine=firesky / 10 regions，并在 stdin EOF 后退出码 0。既有 Python RPC 定向测试 2 passed / 14 deselected；未执行整仓 pytest 或真实预测下载。

离线浏览器已验证对象 READY、三格合成网格、零值/缺测聚焦、OSM 后仍保留源切换。模拟初始化失败/重试最后使用受控 DOM 按钮点击，不能当作完整纯 GUI 验证；最终清除按钮因定位超时未实测。远程瓦片成功/失败恢复与全部配置组合未逐一验收。父壳不保留瓦片错误副本，瓦片状态及恢复由地图组件管理。

**原生 GUI 验收尚未通过。** 本轮 `tauri dev` 已编译并启动原生进程及其 Python 子进程，但 Computer Use 读取窗口被辅助功能权限阻断。工具提示：在「系统设置 → 隐私与安全性 → 辅助功能」允许 `ZCode Computer Use.app`（`~/.zcode/computer-use/ZCode Computer Use.app`），然后完整退出并重开 ZCode。已停止本轮自有 dev task；未绕过权限或使用网页代替 Tauri invoke 验收。

权限恢复后的离线原生入口：

```bash
npm run tauri dev -- --config '{"build":{"devUrl":"http://localhost:1420/?mapOffline=1"}}'
```

待验：原生握手状态与重连、导航与共享选点、网格零值/缺测/聚焦/清除、初始化重试、正常 UI 退出后的子进程回收。详见蓝图「本轮实现与验证记录」。当前不是完整 M1-A/M1 验收，也不是 macOS 12 / Windows 或 clean-machine 安装验收。
