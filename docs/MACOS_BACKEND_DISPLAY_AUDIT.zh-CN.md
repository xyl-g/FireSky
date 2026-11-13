# macOS 原生界面后端数据展示审计

更新日期：2026-11-13。本文按当前 Rust desktop runtime 返回合同核对实际展示；
完整逐项映射见 [后端数据与界面展示清单](MACOS_BACKEND_DATA_DISPLAY.zh-CN.md)，
本轮（Web↔macOS 展示对齐）的逐项缺口与归类见
[展示字段矩阵](WEB_MACOS_FIELD_MATRIX.zh-CN.md)。
审计对象是点预测、实况分析和区域预测，不代表真实上游全链路已经验收。
用户最新决定：以 macOS 为产品与 UI 基准，先完善其功能、流程和展示，其他 UI 后续
对齐；见[基准规范](MACOS_UI_BASELINE.zh-CN.md)。本文的旧 Web 对照是历史字段证据，
macOS 当前缺陷或未验收状态不自动成为跨端规范。
2026-11-12 交接见 [HANDOFF_2026-11-12](HANDOFF_2026-11-12.zh-CN.md)；
现有 `dist/native-macos` 应用包仍早于 2026-11-13 展示批次（本机验证包见「本轮验证」）。

## 结论

点预测和实况分析的核心结果已经足够支撑附件中的布局：结果摘要、质量指标卡、质量曲线和逐时台账都不需要新增后端字段。2026-11-12 前主要缺口在 SwiftUI 台账只显示少量字段，已补齐可烧状态、可烧云量、云层 AGL、AOD 550、太阳仰角和场景。2026-11-13 展示对齐批次（对照
[WEB_MACOS_DISPLAY_PARITY_PLAN](WEB_MACOS_DISPLAY_PARITY_PLAN.zh-CN.md)，71 项缺口盘点）进一步补齐：

- 点预测：趋势图多序列（可烧云占比/可烧比例/回程透过率，0–100% 对齐小图）、独立 AOD550 趋势小图、
  只读「评分组成」面板（[Workbench.swift](/Users/xiaoyang/晓洋站点/firesky/apps/macos/Sources/FireSkyNative/Workbench.swift:598)）。
- 实况：指标按「卫星云况 / 运动采样 / 光学与雷达」分组；回程透过率、多光谱云类等后端缺数项显式「未计算/未接入」。
- 区域：固定 0–2.5 质量色带与覆盖/缺测/采样标注、缓存快照可辨认标识、城市排行行详情改用行内 point DTO。
- 诊断页同时接入运行元数据、云体几何支持度、场景分数、数据来源和冻结剖面能力说明。

## 点预测

Rust 在 `engine/src/point_core.rs` 的 `materialize` 中返回：

- `peak_quality_index`、`quality_level`
- `best_time_local`
- `timing_window.start_time_local`、`timing_window.end_time_local`
- `duration_minutes`
- `confidence_score`
- `model_disagreement_score`
- `quality_components`
- `aerosol.aod550`、`aerosol.source`、`aerosol.quality`
- `steps`
- `cycle_time_utc`、`model_source`、`timezone_name`
- 主导可烧云层的 ASL、AGL 和相对观测者高度
- `cloud_phase_proxy`、`cloud_genus_proxy`、`dominant_burnable_cloud_genus_diagnosis`

这些字段的来源是 [point_core.rs](/Users/xiaoyang/晓洋站点/firesky/engine/src/point_core.rs:959) 和 [desktop_runtime.rs](/Users/xiaoyang/晓洋站点/firesky/engine/src/desktop_runtime.rs:1015)。

当前 macOS 页面可以展示：

- 顶部摘要：峰值质量、质量档位、最佳时刻、推荐窗口和起报周期说明。
- 指标卡：有效可烧云量、云质厚度、亮度色彩、主要云层 AGL、AOD 550、入射受光、返程对比度、持续时长、输入支持度和对流风险；同一字段的百分比口径与台账/统计一致。
- 「评分组成」分项面板：只读展示基础分、持续时间修正、入射削光、观测对比度等贡献值与方向，
  并标明几何遮挡已含在可烧云量、不重复扣分；不在 SwiftUI 重算。
- 质量曲线：`steps[].instant_quality_index`，固定 0–2.5 轴；比例序列（可烧云占比/可烧比例/回程透过率）
  用 0–100% 对齐小图；AOD550 独立小图（`steps[].quality_aod550`）；缺测断线不补零；
  悬停读数与最佳时刻标记。
- 逐时台账：时间、质量、档位、可烧状态、可烧云量、云层 AGL、亮度颜色、可烧比例、入射阻挡、
  AOD 550、太阳仰角和场景；带最佳时刻与当前选中时刻标记。
- 「诊断与时间窗」：周期、本地锚点、时区、分析半径、场景分数、云体几何支持度、云相/云属代理、
  AOD 来源、色彩/演化/数据角色、缓存状态和冻结剖面有效性。
- 「大气剖面」：显示结果内嵌 `cross_section`（含温度、RH、云量、地形与理论照明图层与图例），
  并可调用 `profile_query` 读取最佳时刻冻结剖面摘要；实况可选择已发布步骤查询，区域可按网格查询。
  点预测尚无任意时刻选择器，查询摘要不替换原有剖面图。2026-11-13 源码核对：
  `profile_query` 已按 `valid_time_utc` 查询冻结模式剖面，forecast 装配也已读取
  `offset_minutes`；后续先复用现有合同，不预设必须新增 RPC 字段。

峰值数值必须配 `peak_quality_level`，不能配事件整体的 `quality_level`。
`model_disagreement_score` 当前为占位零，未计算真实跨模式分歧，本轮不再用它作主卡指标。

云层 AGL 由 Rust 在 `point_core.rs` 的 `add_geometry` 中从 ASL 高度和地形高度计算；这不是 UI 推算，因此可以直接展示。

## 实况分析

Rust 在 `engine/src/nowcast_runtime.rs` 返回：

- `peak_quality_index`、`peak_quality_level`
- `quality_components`
- `duration_status`、`duration_minutes`
- `cloud_evolution`
- `timing_window`
- `aerosol`
- `summary`
- `nowcast`
- `steps`

来源：[nowcast_runtime.rs](/Users/xiaoyang/晓洋站点/firesky/engine/src/nowcast_runtime.rs:931)。

当前 macOS 页面可以展示：

- 实况摘要、最佳时刻、卫星观测时间。
- 「卫星云况 / 运动采样 / 光学与雷达」三组指标卡；低云状态按后端合同
  `detected|clear|unknown`（[desktop_nowcast_adjustment.rs](/Users/xiaoyang/晓洋站点/firesky/engine/src/desktop_nowcast_adjustment.rs:227)）映射中文；回程透过率序列、
  多光谱云类概率等后端缺数项显式标注「后端未计算」，不冒充观测。
- 质量变化曲线（固定 0–2.5 轴、缺测断线、最佳时刻标记与悬停读数）。
- 卫星云层后验冻结剖面，优先按峰值步骤太阳方位选择最近的实际采样方位；不可烧且没有最佳时刻时取首个有效诊断步骤。实际方位会标明，缺少方位依据时显示原因，不混叠方向。
- 云体运动和雷达诊断（雷达未接入时显式标注「后端未接入雷达观测」）。
- 逐时台账：时间、质量、档位、可烧状态、可烧云量、AOD 550、太阳仰角。

实况逐时结果没有与点预测同等的模式云底/云顶字段，因此台账只展示后端实际返回的字段，不填充虚构的云层高度。
实况报告顶层没有 `cross_section`；「尚无逐时查询入口」的旧说法已过时：实况大气剖面页已提供按
steps 选择时次的冻结查询入口（Picker＋「读取」按钮），查询摘要不替换静态概率图。

## 区域预测

Rust 在 `engine/src/desktop_runtime.rs` 的区域结果中返回：

- `coverage`
- `points`
- `point_count`、`success_count`、`error_count`
- `city_ranking`
- `frame_data`
- `profile_source`
- `precision`
- `cycle_time_utc`
- `timing`
- `input_manifest`

来源：[desktop_runtime.rs](/Users/xiaoyang/晓洋站点/firesky/engine/src/desktop_runtime.rs:1330)。

当前页面可以展示：

- GFS、ECMWF 质量网格（固定 0–2.5 色带，不按单次预测拉伸；缺测网格透明、不进有效排行）。
- GFS/EC 独立峰值和有效网格统计；覆盖范围、网格精度、采样格点与缺测统计标注；跨 ±180° 区域保持网格连续。
- 城市排名和最佳时刻；点击城市行时详情使用行内携带的 point DTO（坐标不落 0.25° 格点也不误报「缺测」）。
- 网格详情、质量指数和剖面查询入口（弹窗字段全部来自区域 DTO：峰值/事件质量、档位、置信度、云族与质量组成）。
- 缓存快照可辨认标识：`cache.status == "hit"` 时标注「缓存快照 · 生成于 …UTC」。
- 数据准备秒数、原生阶段耗时毫秒数。

`frame_data` 是地图渲染所需的原始网格数据，适合交给地图图层处理，不适合在普通文本卡片中展开。

## 不直接放入主界面的字段

以下字段后端已经返回，但属于溯源或工程诊断数据：

- `input_manifest`、数据源 SHA、缓存身份和 artifact contract。
- `native_forecast` 合同；`native_timings_ms` 则作为区域次级耗时信息显示。
- FFI 运行状态、内部缓存 key、原始错误堆栈。
- 区域 `frame_data` 的完整网格数组。
- `profile_source` 原始对象。

这些字段会让主结果界面变成工程调试面板。更适合放在“运行日志”“数据来源”和“高级诊断”中。`profile_source` 也不能直接画成剖面图，它只是后端保留的查询凭证，必须调用 `profile_query` 后才有可绘制的剖面样本。

## 当前不能承诺的展示

1. **完整公共报告合同**  
   点、区域和实况返回中均明确带有 `public_forecast_complete: false`。这表示内部原生结果和桌面展示数据可用，但公共 Web 报告合同仍未完成，不能在 UI 中标成“公共报告已发布”。

2. **动态卫星垂直剖面**  
   `hello.profile_capabilities.dynamic_satellite_vertical` 当前为 `false`（[desktop_runtime.rs](/Users/xiaoyang/晓洋站点/firesky/engine/src/desktop_runtime.rs:466)）。现有实况剖面可以展示卫星后验云层概率和有限的云柱信息，不能承诺完整的动态模式垂直剖面。

3. **没有 CAMS 时的真实 AOD**  
   `aerosol.aod550` 依赖有效 CAMS 数据。缺测时 UI 必须显示“缺测”或预检阻断，不能用默认值伪造 AOD。

4. **点预测和实况的地图时间轴**  
   当前点预测步骤不携带 `map_cells`，因此 `map_capability.available` 为 `false`；实况也返回 `native-nowcast-map-publication-not-requested`。地图时间轴应继续使用区域预测或独立地图发布能力，不能从单点台账直接拼成地图动画。

5. **区域跨日界线的连续地图拼接**  
   CAMS 拆分、合并和逐侧预检，以及连续模式经度轴、风平流、光路和冻结剖面采样
   已有源码修复及科学回归；真实双侧下载和连续地图实机显示仍未验收。

6. **长期历史结果恢复**  
   历史入口已使用 `result_restore(task_id)` 与内容寻址不可变快照；Rust 分发和 Swift
   `Store.restore` 均已接入。300 秒 TTL、同源码/输入/周期限制属于计算缓存 `read_cache`，
   不再用于限制历史结果恢复。快照按报告保留策略回收，缺失/篡改时明确失败；新包 GUI、
   真实长期运行仍待验收。冻结剖面查询源有独立有效期，恢复结果不代表可无限期重算剖面。

7. **全球实况范围**  
   未知地点 EAST_ASIA 回落已移除，按实际球面光路/采样范围规划并修正 GFS 跨线裁剪。
   真实上游及完整两帧实况仍未验收；不能据此承诺任意全球地点或卫星视场外观测可用。

## 对应代码

- 点预测摘要、指标和曲线：[Workbench.swift](/Users/xiaoyang/晓洋站点/firesky/apps/macos/Sources/FireSkyNative/Workbench.swift:397)
- 点预测「评分组成」面板：[Workbench.swift](/Users/xiaoyang/晓洋站点/firesky/apps/macos/Sources/FireSkyNative/Workbench.swift:598)
- 点预测对比曲线与 AOD 小图：[Workbench.swift](/Users/xiaoyang/晓洋站点/firesky/apps/macos/Sources/FireSkyNative/Workbench.swift:744)
- 点预测逐时台账：[Workbench.swift](/Users/xiaoyang/晓洋站点/firesky/apps/macos/Sources/FireSkyNative/Workbench.swift:1293)
- 点预测大气剖面：[Workbench.swift](/Users/xiaoyang/晓洋站点/firesky/apps/macos/Sources/FireSkyNative/Workbench.swift:1580)
- 区域页（色带/排行/缓存标识/城市详情）：[Workbench.swift](/Users/xiaoyang/晓洋站点/firesky/apps/macos/Sources/FireSkyNative/Workbench.swift:2056)
- 实况摘要与指标分组：[Workbench.swift](/Users/xiaoyang/晓洋站点/firesky/apps/macos/Sources/FireSkyNative/Workbench.swift:2494)
- 实况卫星剖面与冻结查询：[Workbench.swift](/Users/xiaoyang/晓洋站点/firesky/apps/macos/Sources/FireSkyNative/Workbench.swift:2706)
- AOD 读取（缺测显示「暂无来源」，不再空标 CAMS）：[ForecastComparison.swift](/Users/xiaoyang/晓洋站点/firesky/apps/macos/Sources/FireSkyNative/ForecastComparison.swift:34)
- 低云状态映射（detected/clear/unknown）：[NowcastPresentation.swift](/Users/xiaoyang/晓洋站点/firesky/apps/macos/Sources/FireSkyNative/NowcastPresentation.swift:71)
- 区域结果解析（含 cacheStatus 与城市行 point DTO）：[RegionalForecast.swift](/Users/xiaoyang/晓洋站点/firesky/apps/macos/Sources/FireSkyNative/RegionalForecast.swift:126)

## 本轮验证

### 2026-11-13 展示对齐批次

- 实现由动态工作流完成（71 项缺口：单点 24/实况 26/区域 21；三块串行实现，13 个文件改动，
  Rust DTO 零改动——弹窗字段已在 `points[]`/`quality_components`）。
- 门禁：swift build、cargo test、全量 pytest（2031 项）、前端合同测试（27 项）最终全绿；
  快级门禁 3 轮收敛。
- 独立复核 17 条意见（15 修/2 驳）：AOD 来源空声明、低云状态枚举映射、城市排行详情误报缺测、
  区域缓存状态缺失、`quality ?? 0` 潜伏模式、缺测占位符统一、VoiceOver 选中态等均已修复并复跑门禁。
- 本机验收：`FIRESKY_NATIVE_DIST=/tmp/firesky-parity-macos bash scripts/build_macos_native.sh`
  构建全新 app；`scripts/test_macos_native.sh`（C FFI、Swift FFI、fixtures、bundle smoke）通过；
  另清理 `ForecastComparison.swift` 一处未使用变量警告后重验通过。
- 原生 UI 可访问性树走查单点/区域/实况页与缺测状态（等待态、后端未计算标注如实呈现）。

### 2026-11-12 交接批次（保留）

- macOS ARM64 release 构建与应用打包通过；C FFI、Swift FFI、Swift fixtures 和 bundle smoke 通过。
- 新增 JSON 数值 0/1 与布尔值、null/非有限数、卫星方位选择、不可烧但仍有剖面、
  跨北向角距和原生耗时解析的回归断言。
- 1440×1040 概览/剖面及 1120×720 台账进行截图检查；云层主卡不再截断数值，
  剖面云底、云顶和理论照明基准使用独立曲线。
- 展示批次 Rust：184 单元测试及 4 进程测试通过，2 项忽略。
  后续最终冻结源码为 engine 191 单测及 4 进程测试通过、3 项忽略，native 154 项通过；
  相关科学回归 238 项通过。后端收尾后未重新打包，不与展示批次混称同版本验收。

### 仍未覆盖（2026-11-13 批次）

- 当前跨批次状态与执行顺序见[最新接续计划](NEXT_AGENT_PLAN_2026-11-13.zh-CN.md)。
  退役批次另有 pytest 2032 通过、1 跳过、2 项区域 CLI fixture 超时记录；上方展示
  批次全绿不能替代当前工作区的统一门禁。本次文档核对未重跑这些测试。
- 像素级视觉走查（1440×900、1120×720、窄窗口、浅色/深色、长标签/负高度/全无效态）与
  VoiceOver/键盘真机走查未做；图表 accessibility 已在代码层补齐（选中态标签、逐行可读标签）。
- 2026-11-13 批次未重新触发真实预测/数据下载；以上验证为 fixture/合同级，不代表真实 CAMS 全链路、
  全球实况、跨日界线区域或其他平台已验收。
- 缓办项：点预测剖面任意时次选择器（先复用 `profile_query.valid_time_utc` 并核对 UI 接线）、
  GFS 云相/冰含量与多光谱云类概率（后端缺数，独立立项）、实况地图（单独立项）。
