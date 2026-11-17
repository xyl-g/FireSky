# 开源引入评估与接入计划

更新日期：2026-11-14。分支 `feat/web-ui-ux-polish`（未合并 main）。
本文回答三个问题：**社区里有什么是本项目需要的**、**怎么判断要不要**、**具体怎么引入**。

选型口径：痛点驱动，不做通用清单。先从仓库实际记录的问题出发
（ecCodes 原生库打包、缓存保留、图表手写 SVG、排行大列表、移动 FFI 蓝图、
依赖供应链无审计），再逐项到 GitHub / crates.io / npm 检索候选项的
维护状态、许可证、与现有栈（Rust 2021 + Svelte 5 + Vite 6 + Playwright）的兼容性。

## 1. 现状基线

| 层 | 现状 |
| --- | --- |
| `engine/` | Rust 2021，axum + reqwest(blocking, rustls) + tokio + geo + rustfft；GRIB 解码经 `libloading` 动态加载原生 ecCodes（`engine/src/grib.rs`），打包脚本 `scripts/bundle-{vcpkg,macos}-eccodes.mjs` |
| `native/` | Rust 评分器（rayon/chrono），被 engine 以路径依赖引用 |
| `apps/shared-web/` | Svelte 5 runes + Vite 6 + Leaflet/MapLibre；图表全部手写 SVG（质量曲线、WebTrendChart、ProfileChart）；区域排行列表 `slice(0, 2000)` 硬上限 |
| CI | GitHub Actions `ci.yml`（ubuntu）：pytest 契约 / cargo test / shared-web check+build |

## 2. 结论总表

| 级别 | 候选 | 用途 | 状态 |
| --- | --- | --- | --- |
| P0-1 | [cargo-deny 0.20](https://github.com/EmbarkStudios/cargo-deny) | Rust 依赖审计（RustSec/许可/来源） | **已落地**（deny.toml + CI job） |
| P0-2 | [rollup-plugin-visualizer](https://github.com/btd/rollup-plugin-visualizer) | shared-web 构建体积分析 | **已落地**（devDep + `build:analyze`） |
| P1-1 | [uPlot](https://github.com/leeoniya/uplot)（MIT） | 时间序列图表（canvas，~50 KB min） | 建议试点，见 §4.1 |
| P1-2 | [TanStack Virtual](https://tanstack.com/virtual)（MIT，`@tanstack/svelte-virtual` 已支持 Svelte 5 runes） | 区域排行列表虚拟化 | 建议试点，见 §4.2 |
| P1-3 | Playwright `toHaveScreenshot`（内置，零新依赖） | 关键布局视觉回归基线 | 建议引入，见 §4.3 |
| P2-1 | [UniFFI](https://github.com/mozilla/uniffi-rs) | 移动双原生 Rust→Swift/Kotlin 绑定 | 移动分支落地，见 §5.1 |
| P2-2 | [grib / grib-core](https://crates.io/crates/grib)（纯 Rust GRIB2） | 替换 ecCodes 原生依赖 | 仅 spike 评估，见 §5.2 |
| P3 | vitest + @testing-library/svelte、tracing | 测试 DX / 结构化日志 | 观察，见 §6 |
| 不引入 | zstd 整文件缓存压缩、ECharts/Chart.js | — | 见 §7 |

## 3. P0 已落地项（本分支）

### P0-1 cargo-deny —— Rust 依赖供应链审计

引入当天即在 engine 依赖图里发现真实问题，价值已自证：

- **`zip 0.6.6` 命中 5 条 RUSTSEC 公告**（RUSTSEC-2026-0367/0368/0369/0403/0429，
  均为 `extract()` 的 SUID/软链提权类）。上游 `npyz 0.9.1` 锁定 zip ^0.6，
  修复需 zip ≥ 9.0.0，暂无升级路径；引擎仅经 `npyz::npz::NpzArchive` 内存读取
  HSD 卫星数据，不走 extract 落盘路径，已在 `deny.toml` 记录豁免与复核条件
  （**npyz 出 zip2 兼容版本后必须移除豁免并升级**）。
- 许可缺口两个（`CC0-1.0`、`Apache-2.0 WITH LLVM-exception`）已加入 allow；
  `native/` 此前无 license 字段，已补 `GPL-3.0-only`（v1.0.0 起 native
  并入 `engine/`，license 字段随 crate 保留）。

**使用方式**（本地与 CI 同一条命令）：

```sh
# 本地安装：brew install cargo-deny（或 cargo install cargo-deny --locked）
cargo deny --manifest-path engine/Cargo.toml --config deny.toml check advisories bans licenses sources
```

CI：`ci.yml` 新增 `cargo-deny` job（`taiki-e/install-action@v2` 安装，engine manifest 跑一次）。

**维护协议**：任何新增豁免必须写 `reason` 与复核条件；每次 Rust 依赖升级后重跑
`cargo deny check`；豁免条目超过 90 天未复核视为过期。`multiple-versions` 目前为
`warn`，依赖图稳定后可收紧为 `deny`。

### P0-2 rollup-plugin-visualizer —— 前端产物体积分析

```sh
cd apps/shared-web
FIRESKY_ANALYZE=1 npm run build   # 产出 bundle-stats.html（sunburst/treemap，含 gzip+brotli 体积）
```

仅 `FIRESKY_ANALYZE=1` 时注入插件（经 `loadEnv` 读取，与 `FIRESKY_DEV_API_ORIGIN` 同一机制），
常规 `npm run build` 与产物完全不变；`bundle-stats.html` 已加入 .gitignore。
用途：引入新依赖前先看它进哪个 chunk、占多少；uPlot/虚拟化试点的前置度量工具。

## 4. P1 建议引入（未实施，等排期）

### 4.1 uPlot：时间序列图表

**问题**：质量指数曲线、WebTrendChart、ProfileChart 均为手写 SVG；时点最多的
趋势图在长周期数据下交互（缩放/游标/图例）都要自己维护。

**选型**：uPlot（canvas 渲染、性能基准显著优于 ECharts/Chart.js、v1.6.x 维护至 2025+
、MIT）。备选 ECharts 因体积与 API 风格与「贴 Swift 基准的极简 UI」冲突被否。

**引入步骤**：

```sh
cd apps/shared-web && npm i uplot
```

1. 新建 `src/lib/views/UPlotChart.svelte` 薄封装（onMount 建 chart、`$effect` 推送数据、
   onDestroy 销毁）；主题色读 `getComputedStyle(document.documentElement)`
   的 `--fs-accent`/`--fs-teal`，深浅主题切换时重建实例。
2. **只先换「云况与光学趋势」（WebTrendChart）一处**，与旧 SVG 并存对比；
   视觉对齐验收走既有 Playwright 用例 + 截图对比。
3. 验收标准：图例/缺测断线表现与现 SVG 一致；`bundle-stats.html` 确认增量
   ≤ 20 KB gzip；existing 51 条 UI 用例全绿。
4. 回滚：试点组件独立，删除即回滚；不并行改旧组件。

### 4.2 TanStack Virtual：区域排行列表虚拟化

**问题**：`RegionalResults.svelte` 排行列表 `slice(0, 2000)` 硬上限 + 全量渲染 DOM。

**选型**：`@tanstack/svelte-virtual`（官方 Svelte 适配器 2025 已支持 Svelte 5 runes；
issue #866 已解决）。备选 svelte-virtual-list（virtuallist.svelte.page）为组件式封装。

**引入步骤**：`npm i @tanstack/svelte-virtual` → 排行 `<div class="ranking-list">` 换虚拟容器 →
**去掉 `slice(0, 2000)` 前先验证**：选中/聚焦联动（`focus(cell)` 同步地图）不受 DOM
虚拟化影响（联动基于 cells 数据而非 DOM 存在性，理论上安全，需用例覆盖 2000+ 行场景）。
验收：2000 行滚动 60fps（现有交互用例全绿 + 手动大区域用例）；地图网格渲染不在此列。

### 4.3 Playwright 视觉快照（零新依赖）

**问题**：现有用例已有截图输出（`testInfo.outputPath`）但只人工查看，无基线断言；
布局回归（如本次历史区迁移）靠既有 DOM 断言兜底，视觉走查仍是人工。

**引入步骤**：挑 3–4 个稳定页面状态（workbench 1440/390 × 双主题、数据页、历史页），
把现有截图调用改为 `await expect(page).toHaveScreenshot(...)`；
基线在 CI（ubuntu）生成并提交，本地跑用 `--update-snapshots` 注意平台差异，
或对字体敏感区域设 `maxDiffPixelRatio: 0.02`。验收：一次真实布局回归演练中能抓住差异。

## 5. P2 架构级评估项（先评估后决定）

### 5.1 UniFFI —— 移动双原生的绑定生成

`feat/android-ios` 蓝图（docs/MOBILE_NATIVE_PLAN.md，厚引擎 + 双原生 UI）需要
Rust→Kotlin/Swift 绑定。[UniFFI](https://crates.io/crates/uniffi) 2025 活跃维护，
Swift 一等支持，并新增实验性 `uniffi-bindgen-kotlin-jni`（官方 JNI 后端，性能明显优于
手工 JNI 反射路径）。KMP 场景另有 [Ubique uniffi-kotlin-multiplatform-bindings](https://github.com/UbiqueInnovation/uniffi-kotlin-multiplatform-bindings)。

**引入方式**：不在本分支动。移动分支落地时新建独立 `apps/ffi`（`firesky-ffi`）crate
包一层稳定 API（desktop_sync、forecast、history），避免 uniffi 宏侵入 engine 主 crate；
engine 已是 `cdylib + staticlib`，接入成本主要在类型映射（区域/报告结构体）。
与蓝图里「手写 JNI」的既有计划二选一，落地前做一次绑定体积/调用开销对比。

### 5.2 grib crate —— 替换 ecCodes 原生依赖（spike）

**动机**：ecCodes 需要按平台打包（vcpkg/macOS bundle 脚本 + `libloading` dlopen），
是移动端与 Windows/Linux 对齐的主要摩擦点。[grib](https://crates.io/crates/grib)
（纯 Rust GRIB2 解码，已重组为 grib-core workspace，MSRV 1.87）是唯一活跃候选。

**风险**：ECMWF 正在推进 GRIB1→GRIB2 迁移，其本地模板（local use）对非 ecCodes
工具历来是坑（2025 年仍有第三方工具解析 ECMWF GRIB2 失败的公开案例）；GFS 的
复杂打包模板覆盖也需逐项验证。

**评估步骤（spike，不进主链）**：离线取缓存内 GFS/EC/CAMS 各 1 份 fixture →
写 `engine/examples/grib_spike.rs` 用 grib crate 解码 → 与 ecCodes 解码结果逐字段比对
（容差 1e-6）→ 结论写入本文档。**只有三类数据全部数值一致才讨论替换**；
否则维持 ecCodes。ecCodes 本身是 Apache-2.0，无许可风险。

## 6. P3 观察项

- **vitest + @testing-library/svelte**：现有 150 条单测跑在自研 `typescript + node:vm`
  runner 上，零运行时依赖且稳定；等组件级测试需求真正出现（Svelte 5 组件单测）
  再整体切换，避免双 runner 并存。
- **tracing**：engine 现以 `eprintln!` 输出诊断；desktop_sync 后台链路变复杂后
  可引入 `tracing` + `tracing-subscriber`（env-filter）。属锦上添花，暂缓。

## 7. 明确不引入

- **zstd 整文件缓存压缩**：GRIB2 数据段自带压缩，整文件再压缩收益通常仅 10–20%，
  且会破坏缓存文件与其他工具（wgrib2/eccodes CLI）直接可用的性质。缓存磁盘压力
  走既有保留策略（RUST_CACHE_RETENTION）解决。
- **ECharts / Chart.js**：体积与 API 复杂度不成比例，与极简 UI 冲突；时序场景 uPlot 更优。

## 8. 节奏建议

1. 本分支合入 P0（已就绪，随 `feat/web-ui-ux-polish` 一起评审）。
2. 下一个迭代窗口做 P1-1 uPlot 试点（一处图表）与 P1-3 视觉快照；P1-2 视大区域实测卡顿决定。
3. spike 类（P2-2）安排在无发布压力的窗口；P2-1 严格留给移动分支。
4. 本文作为唯一登记处：任何新候选先补进 §2 总表再动手。
