# macOS 原生界面后端数据展示审计

更新日期：2026-11-12。本文按当前 Rust desktop runtime 返回合同核对实际展示；
完整逐项映射见 [后端数据与界面展示清单](MACOS_BACKEND_DATA_DISPLAY.zh-CN.md)。
审计对象是点预测、实况分析和区域预测，不代表真实上游全链路已经验收。
最终收尾与暂停接续见 [本轮交接](HANDOFF_2026-11-12.zh-CN.md)；
现有应用包早于最后的后端修复。

## 结论

点预测和实况分析的核心结果已经足够支撑附件中的布局：结果摘要、质量指标卡、质量曲线和逐时台账都不需要新增后端字段。此前主要缺口在 SwiftUI 台账只显示了少量字段。本轮已补充逐时台账的可烧状态、可烧云量、云层 AGL、AOD 550、太阳仰角和场景；实况台账也补充了可烧云量和 AOD 550。诊断页同时接入了运行元数据、云体几何支持度、场景分数、数据来源和冻结剖面能力说明；点预测大气剖面页会绘制后端返回的云底、云顶和理论照明基准。

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

- 顶部摘要：峰值质量、质量档位、最佳时刻和推荐窗口。
- 指标卡：有效可烧云量、云质厚度、亮度色彩、主要云层 AGL、AOD 550、入射受光、返程对比度、持续时长、输入支持度和对流风险。
- 质量曲线：从 `steps[].valid_time_utc` 或 `steps[].valid_time_local` 与 `steps[].instant_quality_index` 绘制。
- 逐时台账：时间、质量、档位、可烧状态、可烧云量、云层 AGL、AOD 550、太阳仰角和场景。
- “诊断与时间窗”：周期、本地锚点、时区、分析半径、场景分数、云体几何支持度、云相/云属代理、AOD 来源、色彩/演化/数据角色、缓存状态和冻结剖面有效性。
- “大气剖面”：显示结果内嵌的 `cross_section`，并可调用 `profile_query` 读取最佳时刻冻结剖面摘要；实况可选择已发布步骤查询，区域可按网格查询。点预测尚无任意时刻选择器，查询摘要也不替换原有剖面图。

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
- 卫星云况、运动与约束、采样与光学指标卡。
- 质量变化曲线。
- 卫星云层后验冻结剖面，优先按峰值步骤太阳方位选择最近的实际采样方位；不可烧且没有最佳时刻时取首个有效诊断步骤。实际方位会标明，缺少方位依据时显示原因，不混叠方向。
- 云体运动和雷达诊断。
- 逐时台账：时间、质量、档位、可烧状态、可烧云量、AOD 550、太阳仰角。

实况逐时结果没有与点预测同等的模式云底/云顶字段，因此台账只展示后端实际返回的字段，不填充虚构的云层高度。
实况报告顶层没有 `cross_section`；后端冻结剖面查询能力不代表 macOS 已有逐时查询入口。

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

- GFS、ECMWF 质量网格。
- GFS/EC 独立峰值和有效网格统计。
- 城市排名和最佳时刻。
- 网格详情、质量指数和剖面查询入口。
- 区域覆盖范围、网格精度和模式周期。
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
   当前历史入口使用 `read_cache`，有 300 秒、同源码版本、源文件和周期有效性限制。
   `hello` 虽声明 `result_restore`，但目前没有对应 RPC 实现。需要独立不可变快照才能与原始数据保留周期解耦。

7. **全球实况范围**  
   未知地点 EAST_ASIA 回落已移除，按实际球面光路/采样范围规划并修正 GFS 跨线裁剪。
   真实上游及完整两帧实况仍未验收；不能据此承诺任意全球地点或卫星视场外观测可用。

## 对应代码

- 点预测摘要、指标和曲线：[Workbench.swift](/Users/xiaoyang/晓洋站点/firesky/apps/macos/Sources/FireSkyNative/Workbench.swift:380)
- 点预测逐时台账：[Workbench.swift](/Users/xiaoyang/晓洋站点/firesky/apps/macos/Sources/FireSkyNative/Workbench.swift:656)
- 实况摘要和指标：[Workbench.swift](/Users/xiaoyang/晓洋站点/firesky/apps/macos/Sources/FireSkyNative/Workbench.swift:996)
- 实况逐时台账：[Workbench.swift](/Users/xiaoyang/晓洋站点/firesky/apps/macos/Sources/FireSkyNative/Workbench.swift:1190)
- AOD 读取：[ForecastComparison.swift](/Users/xiaoyang/晓洋站点/firesky/apps/macos/Sources/FireSkyNative/ForecastComparison.swift:21)
- 区域结果解析：[RegionalForecast.swift](/Users/xiaoyang/晓洋站点/firesky/apps/macos/Sources/FireSkyNative/RegionalForecast.swift:44)

## 本轮验证

- macOS ARM64 release 构建与应用打包通过；C FFI、Swift FFI、Swift fixtures 和 bundle smoke 通过。
- 新增 JSON 数值 0/1 与布尔值、null/非有限数、卫星方位选择、不可烧但仍有剖面、
  跨北向角距和原生耗时解析的回归断言。
- 1440×1040 概览/剖面及 1120×720 台账进行截图检查；云层主卡不再截断数值，
  剖面云底、云顶和理论照明基准使用独立曲线。
- 展示批次 Rust：184 单元测试及 4 进程测试通过，2 项忽略。
  后续最终冻结源码为 engine 191 单测及 4 进程测试通过、3 项忽略，native 154 项通过；
  相关科学回归 238 项通过。后端收尾后未重新打包，不与展示批次混称同版本验收。
- 截图使用明确标记的 UI fixture，不是实际预测。以上不代表真实 CAMS 全链路、
  全球实况、跨日界线区域或其他平台已验收。
