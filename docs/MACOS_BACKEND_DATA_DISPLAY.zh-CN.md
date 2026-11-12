# macOS 后端数据与界面展示清单

更新日期：2026-11-12

最终源码验证、未完成项和暂停接续见 [本轮交接](HANDOFF_2026-11-12.zh-CN.md)。
现有 macOS 应用包早于最后的后端修复，不能视为同源码验收产物。

本文把 Rust desktop runtime 的 JSON 返回和 macOS 原生端的展示位置对齐。
判断依据是当前 `engine/src/desktop_runtime.rs`、`engine/src/frames.rs`、
`engine/src/ecmwf_point.rs`、`engine/src/nowcast_runtime.rs` 以及
`apps/macos/Sources/FireSkyNative/` 的实际代码。

## 统一响应外壳

点预测和区域预测返回：

```json
{
  "model_results": {
    "gfs": {},
    "ec": {}
  },
  "model_errors": {}
}
```

某个模式失败时仍保留另一模式的结果；macOS 在模型摘要中显示“未完成”和后端错误，
不会把缺失模式填成 0。两种模式都失败时，Rust 直接返回任务错误。

实况 nowcast 返回一个报告对象，不使用 `model_results`。预检、清单、任务历史、
剖面查询和缓存恢复是独立 RPC，不能和预测报告字段混用。

## 点预测

| 后端字段 | 当前展示位置 | 展示方式 |
| --- | --- | --- |
| `peak_quality_index`、`peak_quality_level`、`best_time_local`、`timing_window` | 顶部预测摘要 | GFS/EC 并列峰值、档位、最佳时刻和推荐窗口 |
| `quality_components.*` | 概览指标卡 | 可烧云量、云质厚度、亮度色彩、入射受光、返程对比度 |
| `aerosol.aod550`、`aerosol.source`、`aerosol.quality` | AOD 卡片和详情提示 | 数值、来源和事件时刻取值范围 |
| `duration_minutes`、`confidence_score`、`convective_risk` | 时效与可靠性卡片 | 持续分钟数、输入支持度、对流风险指标；都不是事件命中概率 |
| `steps[].valid_time_local`、`instant_quality_index`、`solar_elevation_deg` | 逐时台账 | 逐时质量和太阳高度 |
| `steps[].physically_burnable`、`selected_regime`、`scene_scores` | 逐时台账和诊断 | 可烧状态、场景档位和场景分数 |
| `summary`、`cycle_time_utc`、`html_report_path` | 诊断与时间窗 | 摘要、周期和完整报告入口 |
| `forecast_mode`、`compute_backend`、`analysis_radius_km`、`timezone_name` | 诊断元数据 | 运行方式、CPU 后端、分析半径和时区 |
| `confidence_kind` / `confidence_score_semantics`、`quality_index_semantics` | 诊断元数据 | 有值时显示；部分报告未透传全部语义字段，主卡仍明确支持度不是命中概率 |
| `cloud_geometry_proxy_source`、`cloud_geometry_support_count`、`cloud_geometry_uncertainty_m`、`cloud_geometry_confidence` | 大气剖面/诊断 | 几何代理来源、支持样本、误差和支持度 |
| `selected_regime`、`scene_scores`、`color_diagnosis` | 诊断与时间窗 | 主要场景、场景分数和色彩诊断 |
| `dominant_burnable_pressure_family`、云底/云顶高度字段、`cloud_phase_proxy`、`cloud_genus_proxy` | 大气剖面/诊断 | 有值时显示；缺值显示“暂无可用云体目标” |
| `cloud_evolution`、`source_roles`、`data_provenance`、`cache` | 诊断详情 | 云体趋势、数据角色、实际观测使用情况、输入来源和缓存状态；声明辅助角色不等于本次使用过观测 |

逐时台账只放适合扫描的字段。其余云场、光学和几何原始字段保留在 Rust JSON
结果中；HTML 报告是独立的阅读投影，不保证逐个展开所有原始字段。
`cross_section` 已直接绘制云底、云顶和理论照明基准。点预测已接入 `profile_query`
读取最佳时刻的冻结剖面；请求使用已发布剖面的观测坐标（GFS 可对齐到模型网格），
保留时戳微秒精度。实况可选择已发布步骤读取太阳光路诊断；查询摘要不替换静态概率图。

## 区域预测

| 后端字段 | 当前展示位置 | 说明 |
| --- | --- | --- |
| `points[]` | 区域地图、网格详情、网格排行 | 每个网格显示质量、档位、最佳时刻和缺测状态 |
| `errors[]`、`error_count`、`success_count`、`point_count` | 区域摘要和地图图例 | 缺测单独标记，不与质量 0 混淆 |
| `frame_data` | 地图绘制 | 作为地图帧数据使用，不直接在文本面板展开 |
| `city_ranking.status`、`items[]`、`reason` | 城市排名 | 城市可搜索、排序和定位；没有目录或区域不适用时显示原因 |
| `profile_source` | 网格详情中的“读取剖面” | 点击网格后按冻结结果执行 `profile_query` |
| `precision`、`cycle_time_utc`、`regions`、`timing.prefetch_seconds` | 区域摘要和地图 | 显示实际网格精度、周期、覆盖区域数和数据准备耗时 |
| `timing.native_timings_ms` | 高级诊断 | 原生计算耗时保留在结果详情，避免干扰地图阅读 |

区域剖面查询只读取该次结果冻结的源文件。它不会重新下载、不会把 GFS 和 EC
混用，也不会把另一个周期的剖面覆盖到当前结果。

## Nowcast 实况

| 后端字段 | 当前展示位置 | 说明 |
| --- | --- | --- |
| `peak_quality_index`、`quality_level`、`best_time_local`、`timing_window` | 实况摘要 | 实况质量和推荐窗口 |
| `nowcast.near_cloud_ratio`、`far_cloud_ratio`、`satellite_low_cloud_state` | 卫星云况卡片 | 近场、远场、低云状态 |
| `nowcast.satellite_motion_quality`、`motion_correlation`、`satellite_motion_age_minutes`、`satellite_advection_horizon_hours` | 运动与约束 | 运动质量、相关性、帧龄和外推时长 |
| `nowcast.convective_score_ceiling`、`geometric_path_blocking_risk`、`confidence` | 运动与约束/采样与光学 | 对流顶棚、光路阻塞风险和实况置信度 |
| `nowcast.calibrated_sample_coverage_ratio`、`aerosol.aod550` | 采样与光学 | 样本覆盖和 AOD |
| `nowcast.radar_source`、`radar_valid_time_utc`、`radar_max_dbz`、降水比例、`radar_reliability_score` | 云体运动与雷达 | 雷达来源、时间、回波、降水和可靠度 |
| `cloud_evolution`、`timing_window` | 实况诊断 | 云体趋势、质量变化、云隙闭合风险和推荐窗口 |
| `color_diagnosis`、`source_roles` | 实况诊断 | 色彩类型/诊断置信度，以及卫星、气溶胶和雷达的数据角色 |
| `nowcast.satellite_vertical_profile` | 大气剖面 | 绘制冻结高度/距离概率图；优先取峰值步骤太阳方位附近的已有采样方位，无最佳时刻时取首个有效诊断步骤方位，并标出实际采样方位，不混叠多个方向 |
| `steps[]` | 逐时台账和质量曲线 | 质量、档位、可烧状态、太阳高度 |
| `profile_source` | 剖面能力诊断 | 后端支持冻结源查询，但 macOS 实况页尚无逐时查询入口；报告顶层也没有直接返回 `cross_section` |

## 预检、运行和恢复

| RPC | 可展示数据 | macOS 位置 |
| --- | --- | --- |
| `hello` | 引擎版本、协议、能力、资源根、CAMS 配置状态 | 引擎状态和设置 |
| `check` / `prepare` | `datasets[]`、`ready`、`missing_count`、`verified`、`is_latest`、周期和卫星帧信息 | 预测前完整性预检弹窗 |
| `publications` | GFS/EC/CAMS 周期及 `verified` 状态 | 核验失败时可能返回未核验推算值，不能一概标成“最新周期已确认” |
| 任务历史/事件 | 任务状态、进度、错误、取消和终态 | 数据页任务历史 |
| `read_cache` | 缓存身份、报告和源文件校验结果 | 当前历史恢复入口仅复用短期计算缓存：生成时间不超过 300 秒、同源码版本、原始文件身份及周期仍有效；不是独立的长期历史快照 |
| `result_restore` | 目前无可调用实现 | 仅在 `hello` 能力列表声明，未分发实现；不能宣称长期历史恢复已完成 |
| `retention_settings` | GFS/EC/CAMS 周期数、卫星/报告/地图天数 | 设置页 |

## 不直接展示的内容及原因

| 内容 | 原因和处理 |
| --- | --- |
| `frame_data` 内的完整压缩网格、tensor、profile bundle | 体积大、面向绘图/计算，不适合文本卡片。区域地图和 `profile_query` 已提供人可读入口。 |
| `input_manifest.sources` 的绝对路径、SHA-256 和缓存 key | 它们是完整性和恢复身份，不是预测指标。放在诊断详情或报告中，主界面只显示来源状态。 |
| `native_forecast`、`contract`、`source_id` 等协议字段 | 用于版本门禁和兼容性检查，直接展示会让用户误以为是天气指标；发生不匹配时转成错误信息。 |
| 点/实况重复评分字段、模型权重和预报时效语义 | 它们用于解释或复核评分，主界面已有稳定的质量、场景和置信度字段；完整值保留在报告和高级诊断中。 |
| 区域完整 `coverage` 边界和逐条 `errors` | 地图已经表达覆盖范围，摘要显示缺测数量；逐条错误属于异常详情，避免在主地图旁展开大量文本。 |
| 全部 `quality_components` 和逐时所有云场字段 | 字段数量大且单位混合；主界面展示经过命名和单位换算的稳定指标，原始完整值留在报告。 |
| `model_level_cloud_fields`、EC 的 HCC/MCC/LCC 和模型层云水/云冰 | 当前 EC basic 路径明确没有这些输入，不能用 TCC 或代理值冒充。UI 只显示 EC-basic proxy 的说明。 |
| `satellite_vertical_profile` 的动态重建 | `hello.profile_capabilities.dynamic_satellite_vertical=false`。当前展示冻结的卫星结果和太阳方向剖面；没有逐时后验时显示“暂无卫星剖面数据”。 |
| 点预测和普通 nowcast 的云场地图 | 当前点预测步骤不携带 `map_cells`，因此 `map_capability.available=false`；nowcast 只有显式请求 `include_map_cells=true` 才发布地图帧，macOS 主流程没有请求它们。区域预测使用独立 `frame_data` 地图。 |
| 逐帧卫星动画、原始 HSD、雷达栅格叠加 | 当前桌面合同只返回诊断摘要、冻结剖面和雷达统计，不返回可播放帧序列或地图瓦片。不能从统计字段反推图像。 |
| `confidence_score` 作为概率 | 点预测的 `confidence_score_semantics` 表达输入/几何支持，实况尚未透传同等语义字段；二者均不能当成经过校准的事件命中概率。 |
| 真实跨模式分歧 | 当前 GFS、EC 和实况结果的 `model_disagreement_score` 为占位零，尚无跨模型对齐计算。主卡改为真实的 `convective_risk`；诊断页说明分歧未计算，不用零冒充“一致”。 |
| 多方位样本缺少方位依据的卫星剖面 | 若没有太阳方位且采样存在多个方向，UI 不任意拼接，显示“已有多方位卫星样本，但缺少太阳方位”。这属于适配信息不足，不代表卫星样本不存在。 |
| 独立于原始缓存寿命的历史恢复 | 需要不可变结果快照、身份校验及独立恢复 RPC；不能通过放宽计算缓存校验替代。当前保留设置不意味着任意历史结果都能恢复。 |
| 跨日界线区域的完整预测地图 | CAMS 拆分/合并/逐侧预检、连续经度轴、风平流、光路及冻结剖面已有源码修复和科学回归；真实 ADS 双侧下载与连续地图实机显示仍未验收，不能标成生产全链路已支持。 |
| 任意全球地点的实况覆盖 | 已移除未知地点 EAST_ASIA 回落，按实际球面光路/采样范围规划，并修正 GFS 跨线裁剪；真实上游和完整两帧实况仍待验收，卫星视场外不能承诺观测可用。 |

因此，当前 macOS 展示的是“可解释的产品字段”，而不是把后端 JSON 原样倾倒到
界面。凡是数据没有生成、能力明确关闭或需要保留完整性身份的字段，都会显示
“暂无数据/不可用原因”，不会静默填充或伪造结果。
