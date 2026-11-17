# FireSky 移动端双原生蓝图（iOS SwiftUI + Android Kotlin）

> 状态：开工蓝图 v1（2026-11-12）；**MM0 可行性尖峰已通过**。
> 决策记录：2026-11-12 用户裁定——移动端**弃 Tauri 壳改双原生**；UI 基底 = 最初 macOS 的 SwiftUI 方案（主仓库 `apps/macos/Sources/FireSkyNative/`，约 3.6k 行）；**v1 不做区域预测**。**厚客户端（引擎内嵌、离线独立）不变**。桌面 Tauri 线（Codex 负责）不受影响。
> 上位文档：`docs/TAURI_DESKTOP_PLAN.md`（桌面）；MM0 尖峰全记录：`spikes/mm0-eccodes-cross/README.md`。
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
- 交叉编译配方直接引用尖峰脚本（`spikes/mm0-eccodes-cross/scripts/`），engine 全量构建时 14 条坑逐条对照。

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
- **P-3**：移动构建特性对齐审计——桌面 brew 版 ecCodes 启用的 AEC/PNG/JPEG2000 若真实数据用到，补交叉编译 libaec/libpng/openjpeg（尖峰 README 坑 4）。

### MN1 — engine-mobile + iOS 模拟器骨架
engine `desktop` 主循环 Read/Write 参数化；`engine-mobile` crate（UniFFI）+ 内存 duplex；SwiftUI 工程落地（先快照基底或最小骨架）；模拟器内 hello→check→prepare→forecast（`allow_download:false` + 预置缓存样本）全链路。
**验收**：iOS 模拟器跑通全链路且响应与桌面 JSON-lines 帧级一致；杀 App 重启状态恢复；桌面 `desktop` 子命令回归不受影响。

### MN2 — Android 骨架 parity
Compose 工程 + `EngineClient.kt` + UniFFI Kotlin 绑定；definitions assets 解压与缓存根落地；模拟器/真机冒烟。
**验收**：与 MN1 同一预置缓存样本全链路一致；`libengine_mobile.so` 静态嵌入，仅依赖系统库。

### MN3 — 真机真实链路
Wi-Fi/蜂窝下载策略（默认 Wi-Fi，蜂窝需确认）、CAMS Key 设置页、DEM 按需拉取、存储配额 LRU、前台下载 + 返回续传。
**验收**：双平台真机蜂窝/Wi-Fi 完成真实单点预测；下载中杀进程无半截缓存；iOS 锁屏 5 分钟返回可续传或明确失败。

### MN4 — 实况范围裁决
真机实测 HSD 下载量/耗时/耗电/存储，三选一：全链上机（仅 Wi-Fi+前台）/ 已缓存回放 / 缓发登记。数据表回写本文档。

### MN5 — 分发
iOS TestFlight（Apple Developer 账号，D5）/ AdHoc；Android APK 签名侧载；identifier/版本与桌面收敛规则一致。

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

- **D1 地图**：MapKit + 天地图/OSM 自定义 tile overlay（原生集成最好）vs 复用 `map.html`（WKWebView，最大保真）——MN1 需定。
- **D2 绑定**：UniFFI（默认）vs 手工 C ABI；以 MN1 实测回调体验为准。
- **D3 最低版本**：Android minSdk 26 / iOS deployment target 16——按目标设备定。
- **D4 iPad**：是否按通用 App（iPhone+iPad）直接出？
- **D5 Apple Developer 账号**（$99/年，TestFlight 前提）。
- **D6 实况链**：归 MN4。

---

## 附录 A：Tauri 移动壳路线（2026-11-12 搁置备忘）

同日已完成 thick-client 架构分析并验证了引擎可行性（MM0），壳层原本计划 Tauri 2 mobile + 内存 duplex 服务 + 共享 Svelte UI。用户裁定改双原生后，该路线搁置；**全部引擎侧成果（尖峰、坑清单、构建脚本）由本蓝图继承**。重评触发条件：UniFFI/双原生成本显著超预期（R1/R2 恶化）、或未来需要第三套 UI 面（如 Web/PWA）且团队愿意回到共享 UI 模型。
