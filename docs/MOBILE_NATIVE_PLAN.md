# FireSky 移动端双原生蓝图（iOS SwiftUI + Android Kotlin）

> 状态：开工蓝图 v1（2026-11-12）；**MM0 可行性尖峰已通过**；**v1.1.0（2026-11-20）双端随正式版发布**——MN1/MN2 验收通过，MN3 部分完成（definitions 打包 + 会话恢复已落，真机蜂窝待硬件），MN5 分发方式已定（自签 APK / 无签名 IPA）。
> 决策记录：2026-11-12 用户裁定——移动端**弃 Tauri 壳改双原生**；UI 基底 = 最初 macOS 的 SwiftUI 方案（主仓库 `apps/macos/Sources/FireSkyNative/`，约 3.6k 行）；**v1 不做区域预测**。**厚客户端（引擎内嵌、离线独立）不变**。桌面 Tauri 线（Codex 负责）不受影响。
> 上位文档：MM0 尖峰全记录（原 `spikes/mm0-eccodes-cross/README.md`）：`docs/ECCODES_CROSS_COMPILE_NOTES.md`。
> 旧文档 `TAURI_MOBILE_PLAN.md` 已废止（同日裁定，内容归档见本文附录 A）。

---

## 0. 换道影响面（什么变、什么不变）

| | 状态 |
|---|---|
| MM0 尖峰（ecCodes 交叉编译/静态链接/模拟器运行、14 条坑清单） | **原样继承**——引擎侧验证与 UI 框架无关 |
| 厚客户端决策（引擎内嵌、离线独立、前台下载模型） | 不变 |
| 移动端 v1 范围 | **砍掉区域预测**（用户裁定）；实况链延后裁决 |
| UI 路线 | ~~共享 Svelte~~ → iOS 一等公民（SwiftUI）+ Android 同构移植（Compose） |
| 对 Codex/main 的依赖 | 移动端不再依赖 `apps/desktop-tauri` 时序；但仍需 `apps/macos` 源码落库（见 P-1） |

选型理由备忘：核心资产是 Rust 引擎与既有 SwiftUI 设计原稿；双原生 = UI 零妥协 + 平台能力一等公民，代价是 Android 侧 UI 重写为同构移植（本蓝图的核心工程量）。

## 1. 目标架构

```
iOS：SwiftUI（基底 apps/macos）              Android：Kotlin + Jetpack Compose（同构镜像）
  EngineClient.swift（JSON-lines 协议客户端      EngineClient.kt（同构实现）
    仅换传输层，协议零改动）                        
        │                                            │
        └────────────┬───────────────────────────────┘
                     │ UniFFI 生成的 Swift/Kotlin 绑定
        ┌────────────▼─────────────────────────────┐
        │ engine-mobile crate（新增，Rust）          │
        │  desktop JSON-lines 服务跑在进程内         │
        │  （内存 duplex，与桌面 desktop 子命令同源，   │
        │   progress/cancel/令牌语义共用桌面验收矩阵）  │
        └────────────┬─────────────────────────────┘
                     │ 同进程调用
        ┌────────────▼─────────────┐   ┌──────────────────────────┐
        │ engine/ Rust 引擎（lib）   │   │ C ecCodes（静态链接，MM0 ✅）│
        └──────────────────────────┘   └──────────────────────────┘
```

### 1.1 桥接设计（关键决策：协议保持）

- **不新造类型化 API**。`engine-mobile` crate 暴露约 5 个原语：`start(config)` / `send(frame)` / `on_frame 回调` / `cancel(request_id)` / `stop`；desktop JSON-lines 服务（stdin/stdout 参数化为 `impl Read + impl Write`，内存 duplex 承载）在进程内线程运行。
- 理由：`EngineClient.swift`（210 行）传输层替换后近乎原样复用；Kotlin 写一份同构客户端；progress 三链语义、取消、令牌直接复用桌面验收矩阵，不引入第二套契约。
- 绑定生成用 **UniFFI**（Swift/Kotlin 双端 codegen，回调接口承载 progress 帧）；备选手工 C ABI（桥接面小，完全可行），见 openQuestions D2。
- 引擎打包形态：iOS staticlib 进 App 二进制；Android cdylib（`libengine_mobile.so`）+ JNI（UniFFI 生成）。
- **definitions 注入**（MM0 坑 10/12/13 直接适用）：iOS 从 bundle resource 取路径；Android assets 首启解压到 `filesDir`；统一设 `ECCODES_DEFINITION_PATH`（**单数**）环境变量后再触发引擎初始化。缓存根/数据目录 → App 沙箱（Application Support / filesDir）。
- 交叉编译配方见 `scripts/eccodes-cross/`，engine 全量构建时 14 条坑逐条对照。

## 2. 复用映射表（apps/macos → 移动双端）

| macOS 源（行数） | iOS 去向 | Android 对应 | 备注 |
|---|---|---|---|
| FireSkyApp.swift (83) | App 壳适配（WindowGroup→移动导航） | MainActivity + Compose 导航 | 菜单/快捷键语义降级为移动手势/工具条 |
| Workbench.swift (1222) | 拆分：单点工作台迁移 | 同构重写 | **区域部分排除**（下两行） |
| RegionalForecast.swift (167) / RegionalMapLayers.swift (126) | **排除（v1 无区域预测）** | 同 | 用户裁定 2026-11-12 |
| Store.swift (701) | 原样带（ObservableObject） | ViewModel + StateFlow 同构 | 状态语义以 iOS 版为参照源 |
| EngineClient.swift (210) | 传输层换 FFI 后复用 | EngineClient.kt 新写 | 协议帧/握手/进度三链语义照抄 |
| ForecastMap.swift (339) | 见 openQuestions D1 | 同 D1 | MapKit+天地图 overlay vs WKWebView+map.html |
| DialogViews (253) / PreparationActions (25) / DownloadProbe (50+77) / ObservationDate (50) / ForecastComparison (115) / MapZoom (22) / BrandMark (33) | 对应迁移 | 同构重写 | 常规移植 |
| NowcastPresentation.swift (100) | **延后**（实况链归 MN4 裁决） | 同 | 裁决后决定迁移或排除 |

## 3. v1 范围

- **做**：单点预测（GFS/EC 双模型独立结果 + phenomenon 选择）、数据会话 check→prepare→confirm（含进度/取消）、观测点管理 + 地图选点、设置（CAMS Key 写沙箱文件，`~/.cdsapirc` 契约不变）、上次结果离线查看、iPad 适配（SwiftUI size class 天然覆盖）。
- **不做（v1）**：区域预测（用户裁定）、实况/nowcast（MN4 裁决）、HTML 报告、推送/小组件、Android WorkManager/iOS BGTask 后台预取。

## 4. 里程碑

### 前置任务
- **P-1**：`apps/` 在主仓库仍是未跟踪目录——等 Codex 提交进 main 后 merge，把 `apps/macos` 作为 UI 基底；或经用户授权后按当日快照只读拷入本分支（记录快照 commit 等价点）。
- **P-2**：`engine/` 全量对 `aarch64-apple-ios` / `aarch64-linux-android` 构建通过（MM0 只验了尖峰 crate；对照 14 条坑清单）。
- **P-3**：移动构建特性对齐审计——**AEC 已落地（2026-11-19）**：EC GRIB2 用 CCSDS 压缩，尖峰 ENABLE_AEC=OFF 使 forecast 报 ecCodes -67；现 `scripts/build_libaec.sh` 交叉 libaec 1.1.8（iOS device/sim + Android 静态），ecCodes 脚本改 `ENABLE_AEC=ON` + 静态链接（交叉 find_package 只搜 sysroot，须显式 `libaec_DIR`；`ENABLE_USE_SHARED_LIB_AEC=OFF`）。桌面 brew 版另链 openjpeg/libpng：当前生产数据路径（GFS simple/complex packing、EC/CAMS AEC）经 MN1 模拟器全链路解码验证未用 PNG/JPEG2000，**缓办**；未来数据源若用该两类 packing 再补 libpng/openjpeg 交叉。

### MN1 — engine-mobile + iOS 模拟器骨架
engine `desktop` 主循环 Read/Write 参数化；`engine-mobile` crate（UniFFI）+ 内存 duplex；SwiftUI 工程落地（先快照基底或最小骨架）；模拟器内 hello→check→prepare→forecast（`allow_download:false` + 预置缓存样本）全链路。
**验收**：iOS 模拟器跑通全链路且响应与桌面 JSON-lines 帧级一致；杀 App 重启状态恢复；桌面 `desktop` 子命令回归不受影响。

**状态（2026-11-19：后端接线 + 全链路 + parity 通过）**：
- 架构收敛到 §1.1 同源设计：engine-mobile 直接承载 `desktop_runtime::serve_runtime`（桌面 `desktop` 子命令本身），内存 duplex + 换行→回调帧适配 + 同步取消镜像（fsm_cancel 经 wire cancel 帧复用桌面取消/任务历史路径）；过渡期 `desktop_rpc.py` 编排移植层（service/backend ~2000 行）删除。C ABI 契约（fsmobile.h）不变；`serve_runtime` 公开化供进程内宿主 fail-fast 构造 Runtime。
- 模拟器全链路 hello→check→prepare→forecast 通过（RealEngineChainTests + FSMobileTransportTests 4/4 绿，GFS+EC 双模型真实解码）。预置缓存 = 桌面 live 缓存 `.cache/{gfs,ecmwf,cams}` 日期子树 {D, D-1}（APFS clonefile 零拷贝种子；原生 ready 契约要求 CAMS 同样就绪，D 取 gfs∩ecmwf 最新交集且 CAMS 在 D/D-1 有真实 GRIB）；prepare/forecast 零网络请求。
- 帧级 parity：模拟器 vs 桌面 stdio 同 payload forecast 结果，归一化易变元数据（workspace 路径/时间戳/派生内容寻址 ID/报告哈希）后**深度相等**，输入文件 basename 逐一一致（证据 output/mn1-parity-20261119/）。
- 静态切片：iOS device/sim `libengine_mobile.a`（Apple ranlib，Xcode ld 符号索引坑）+ Android `libengine_mobile.so`（NEEDED 仅 libm/libdl/libc，fsm_* 五符号齐全）。
- 新坑与修复：MM0 #15（ECC-1088 兜底路径追加 vs 严格指纹 → engine 指纹跳过缺失搜索段，桌面哈希不变）；P-3 AEC（见上）。
- 剩余：definitions 尚未随 App 打包（测试经宿主环境注入，真机资源落地归 MN3）；杀 App 重启的**应用层**状态恢复待 result_restore UI 接线（引擎侧任务历史已随桌面同源落地）；D1 地图裁决未动。（2026-11-20 更新：三项均已落地/裁定，见 MN2/MN3 状态与 D1。）

### MN2 — Android 骨架 parity
Compose 工程 + `EngineClient.kt` + UniFFI Kotlin 绑定；definitions assets 解压与缓存根落地；模拟器/真机冒烟。
**验收**：与 MN1 同一预置缓存样本全链路一致；`libengine_mobile.so` 静态嵌入，仅依赖系统库。

**状态（2026-11-20：验收通过，随 v1.1.0 发布）**：
- 绑定按 D2 裁定走**手工 C ABI + JNI**（非 UniFFI）：`engine-mobile/jni/fsm_jni.c` shim 导出 `Java_com_firesky_mobile_engine_JniTransport_*` 五符号；Gradle 接线 `cargoAndroidEngine` → `linkEngineJni`（NDK clang 静态链接 ecCodes/libaec，`-lc++_static -lc++abi`）→ `copyEccodesDefinitions`（brew definitions 完整树进 assets，首启由 `JniTransport.unpackDefinitions()` 解包到 filesDir）。
- 默认传输 mock → **ffi**（设置页保留 mock 切换）；`WorkbenchStore.connect()` 接 hello → task_history → result_restore 会话恢复链（与 macOS §3.4 参照逐帧同语义）。
- 本地模拟器（AVD firesky34，android-34 arm64-v8a）instrumented 全链测试全绿（`RealEngineChainTest`：种子缓存离线 hello→check→prepare→forecast，`allow_download:false, confirmed:true`；测试计数见发布门禁日志）。
- 与桌面**帧级 parity 通过**：复用 MN1 harness（`output/mn1-parity-20261119/compare.mjs` + `drive-desktop.mjs`，归一化 workspace/时间戳/内容寻址 ID 后逐帧比对）；运行证据见 output/ 归档（发布门禁日志）。
- kill-restart restore 通过：同 workspace 新建 transport/client → hello → 自动恢复上次 forecast 结果与参数。
- definitions 断言通过：assets 解包目录存在且 `fsm_start` 注入成功（单数 `ECCODES_DEFINITION_PATH`，先于引擎初始化）；APK 含 `lib/arm64-v8a/libengine_mobile.so` 与 `assets/eccodes/definitions/`。
- mock 通道仍可切换，原单测不回归。真机冒烟归 MN3。

### MN3 — 真机真实链路
Wi-Fi/蜂窝下载策略（默认 Wi-Fi，蜂窝需确认）、CAMS Key 设置页、DEM 按需拉取、存储配额 LRU、前台下载 + 返回续传。
**验收**：双平台真机蜂窝/Wi-Fi 完成真实单点预测；下载中杀进程无半截缓存；iOS 锁屏 5 分钟返回可续传或明确失败。

**状态（2026-11-20：部分完成）**：
- **已落**：definitions 随包打包——Android assets 完整树 + 首启解包（MN2）；iOS `Resources/eccodes/definitions` 文件夹引用进 Bundle 根，`FSMobileTransport.bundledDefinitionsPath()` bundle 优先（测试不再靠宿主 env 注入，消除 MM0 坑 12 假阳性；宿主注入仅保留为显式标注的调试旁路）。
- **已落**：双端会话恢复（result_restore）——iOS `Store.connect()` 与 Android `WorkbenchStore.connect()` 均接 task_history → 最近可恢复 forecast → 恢复 result 与 location/date/phenomenon 参数，守 busy/generation；iOS 模拟器新增 kill-restart 恢复测试，`xcodebuild test` 全绿（计数见发布门禁日志）。
- **待硬件**：真机蜂窝/Wi-Fi 真实下载、下载中杀进程、锁屏续传、存储配额 LRU 等真机验收项待真机硬件到位后执行；v1.1.0 的 iOS 载荷为无签名 device IPA（分发载体），质量门以模拟器全链为准。

### MN4 — 实况范围裁决
真机实测 HSD 下载量/耗时/耗电/存储，三选一：全链上机（仅 Wi-Fi+前台）/ 已缓存回放 / 缓发登记。数据表回写本文档。

### MN5 — 分发
iOS TestFlight（Apple Developer 账号，D5）/ AdHoc；Android APK 签名侧载；identifier/版本与桌面收敛规则一致。

**状态（2026-11-20：v1.1.0 分发方式已定并落地）**：
- **Android**：自签签名 release APK（`FireSky-1.1.0-android-arm64.apk`，arm64-v8a）。keystore 生成于仓库外（`~/.firesky-release/`，永不入库）；**后续更新必须同一 keystore**，妥善备份。用户侧开启「安装未知应用」后直接安装/覆盖更新。
- **iOS**：无签名 device IPA（`FireSky-1.1.0-ios-unsigned.ipa`），用户以免费 Apple ID 经 AltStore/Sideloadly 重签侧载（**7 天有效**，到期重签）。TestFlight/AdHoc 需付费 Developer 账号（D5），暂不采用。
- 版本号与桌面收敛一致（v1.1.0 统一由发布流程收敛）；安装指引见 README「下载安装 / Releases」。

## 5. 风险清单

| # | 风险 | 缓解 |
|---|------|------|
| R1 | UniFFI 双端成熟度（尤其 Kotlin callback） | 桥接面仅 ~5 原语，fallback 手写 C ABI（D2 预留） |
| R2 | Workbench 1222 行 AppKit→UIKit 适配（菜单/快捷键/多窗口） | 逐段映射表验收；快捷键语义降级为移动手势并在 UI 注明 |
| R3 | engine 全量交叉编译新坑（MM0 仅验尖峰 crate） | P-2 前置 + 坑清单逐条对照 + 桌面验收矩阵作回归基线 |
| R4 | Android definitions/assets 落地与首启时序 | MN2 显式验收项（env 注入先于引擎初始化） |
| R5 | `apps/` 未提交依赖 | P-1 双路径（等提交 / 授权快照） |
| R6 | 双 UI 语义漂移（iOS 为参照源，Android 同构） | Store/协议层快照测试进 CI；验收同输入并排对照 |
| R7 | iOS 后台冻结（同旧蓝图，不变） | 前台下载模型 + 返回续传，不做伪后台 |

## 6. openQuestions

- **D1 地图**：**已裁定（2026-11-20）**——iOS 采用 MapKit 原生（`apps/mobile-ios/FireSkyMobile/Views/MapViews.swift` 已实现）；Android 地图延后，v1 以坐标/收藏输入选点，后续再补原生地图。原选项（MapKit + 天地图/OSM tile overlay vs WKWebView `map.html`）不再阻塞。
- **D2 绑定**：**已裁定——手工 C ABI**（include/fsmobile.h 五原语固定契约，2026-11-12 定、2026-11-19 MN1 全链路复验）；UniFFI 不采用，回调体验满足（帧回调 + write_lock 串行）。
- **D3 最低版本**：Android minSdk 26 / iOS deployment target 16——按目标设备定。
- **D4 iPad**：是否按通用 App（iPhone+iPad）直接出？
- **D5 Apple Developer 账号**（$99/年，TestFlight 前提）。
- **D6 实况链**：归 MN4。

---

## 附录 A：Tauri 移动壳路线（2026-11-12 搁置备忘）

同日已完成 thick-client 架构分析并验证了引擎可行性（MM0），壳层原本计划 Tauri 2 mobile + 内存 duplex 服务 + 共享 Svelte UI。用户裁定改双原生后，该路线搁置；**全部引擎侧成果（尖峰、坑清单、构建脚本）由本蓝图继承**。重评触发条件：UniFFI/双原生成本显著超预期（R1/R2 恶化）、或未来需要第三套 UI 面（如 Web/PWA）且团队愿意回到共享 UI 模型。
