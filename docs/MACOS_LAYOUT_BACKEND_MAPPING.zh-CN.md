# macOS 结果布局与后端字段映射

更新日期：2026-11-12

最终源码状态与暂停接续见 [本轮交接](HANDOFF_2026-11-12.zh-CN.md)；
现有 macOS 应用包尚未包含最后的后端修复。

本文按 macOS 单点预测参考布局，说明每个结果区域对应的 Rust 返回字段、缺值表现和
不能直接展示的原因。页面应优先展示面向用户的稳定指标，原始网格和完整性身份保留在
高级诊断或完整报告中。

## 主结果区

| 布局区域 | Rust 字段 | 展示规则 |
| --- | --- | --- |
| 峰值质量 | `peak_quality_index`、`peak_quality_level` | 显示 `0–2.5` 和质量档位；模型失败时显示“未完成”和 `model_errors` 的原因 |
| 最佳观赏时刻 | `best_time_local`、`timing_window` | 显示当地时间和推荐窗口；时区来自经纬度对应的 `timezone_name` |
| 可烧云量 | `quality_components.burnable_cloud_coverage_effective`、`forward_burnable_cloud_ratio_peak` | 转成百分比；没有有效步骤时显示“暂无数据” |
| 有效持续 | `duration_minutes`、`duration_status` | 显示分钟数；`estimated` 语义为模型估计，不是实测持续时间 |
| AOD 550 | `aerosol.aod550`、`aerosol.source`、`aerosol.quality` | 显示数值和来源；CAMS 缺测时显示缺测，不用默认晴空值替代 |
| 主要云层 | `dominant_burnable_cloud_base_agl_mean_m`、`dominant_burnable_cloud_top_agl_mean_m`、`dominant_burnable_pressure_family`、`cloud_genus_proxy` | 显示云底至云顶 AGL；同时可显示压力族/云属代理。字段为空时显示“暂无可用云体目标” |
| 亮度与色彩 | `quality_components.brightness_color`、`color_diagnosis` | 主卡显示稳定分数，详细诊断放入高级区域 |
| 入射受光与返程对比度 | `quality_components.incoming_aod_illumination_factor`、`quality_components.observer_aod_contrast_factor` | 作为解释性指标显示，不解释为独立概率 |
| 输入支持度 | `confidence_score`、`confidence_score_semantics` | 可显示百分比，明确输入覆盖/几何支持，不是事件命中概率；缺失语义字段不伪造原始值 |
| 对流风险 | `convective_risk` | 展示实际模式风险指标，替代当前尚未计算的跨模式分歧占位零 |

点预测 GFS 与 EC 应并列显示。某一模式失败时保留另一模式结果，不能将失败模式填成
0 或“晴空”。

## 质量曲线与逐时台账

质量曲线使用：

- `steps[].valid_time_utc` 或 `steps[].valid_time_local`
- `steps[].instant_quality_index`
- `steps[].valid`

逐时台账可以稳定展示：

- 时间、质量指数、质量档位；
- `physically_burnable` 可烧状态；
- `burnable_cloud_coverage` 可烧云量；
- 云底/云顶 AGL；
- `quality_aod550`；
- `solar_elevation_deg`；
- `selected_regime` 场景。

缺测行必须断开曲线并显示“缺测”，不能把缺测值变成 `0`。

## 诊断与时间窗

适合放在诊断页而不是主卡的字段包括：

- `cycle_time_utc`、`forecast_lead_hours`、`analysis_radius_km`、`compute_backend`；
- `cloud_geometry_proxy_source`、`cloud_geometry_support_count`、
  `cloud_geometry_uncertainty_m`、`cloud_geometry_confidence`；
- `quality_index_semantics`、`confidence_kind`；
- `source_roles`、`data_provenance`、`profile_source`；
- `native_forecast`、`input_manifest` 的合同和校验状态。

点预测也展示 `cloud_evolution`、`color_diagnosis`、数据角色及缓存状态。
区域次级摘要展示 `timing.native_timings_ms`，不把毫秒值当成整次端到端耗时。

其中 `input_manifest.sources` 的绝对路径、SHA-256、cache key 和原始合同版本只适合
工程诊断或报告，不适合主界面。

## 区域预测

区域结果可展示：

- `frame_data`：交给地图图层绘制质量网格；
- `points[]`：网格质量、档位、最佳时刻和缺测状态；
- `success_count`、`error_count`、`point_count`：区域摘要；
- `precision`、`cycle_time_utc`、`timing.prefetch_seconds`：高级摘要；
- `city_ranking.items[]`：城市排名、搜索、定位；
- `profile_source`：点击网格后调用 `profile_query` 读取冻结剖面；
- `coverage`：区域范围和地图适配。

`frame_data` 的压缩网格、tensor 和完整数组不直接展开成文本；地图、网格选点和剖面
查询已经是对应的人类可读入口。逐条 `errors[]` 可在选中缺测网格时显示，不能把它们
混入质量排行。

## Nowcast 实况

实况主卡可以展示：

- `peak_quality_index`、`quality_level`、`best_time_local`、`timing_window`；
- `nowcast.near_cloud_ratio`、`far_cloud_ratio`、`satellite_low_cloud_state`；
- `nowcast.satellite_motion_quality`、`motion_correlation`、
  `satellite_motion_age_minutes`、`satellite_advection_horizon_hours`；
- `nowcast.convective_score_ceiling`、`geometric_path_blocking_risk`、
  `confidence`、`calibrated_sample_coverage_ratio`；
- `aerosol.aod550`；
- 雷达来源、观测时刻、最大回波、降水比例和可靠度；
- `satellite_vertical_profile` 的冻结样本，标注实际采样方位；
- `steps[]` 的质量曲线和逐时台账。

当前 `hello.profile_capabilities.dynamic_satellite_vertical` 为 `false`，所以不能承诺
逐时重建的动态卫星垂直剖面。没有冻结卫星样本时显示“暂无卫星剖面数据”。
实况报告顶层没有 `cross_section`；有多个采样方位时，通过峰值步骤的太阳方位选择
最近的已有方向；不可烧且没有最佳时刻时取首个有效诊断步骤的方位。若没有方位依据，
会显示具体原因，不把多方向样本叠成一个剖面。

## 当前不能直接展示的内容

| 内容 | 原因 |
| --- | --- |
| 点预测的地图时间轴 | 点预测步骤不携带 `map_cells`，`map_capability.available=false`；地图应使用区域结果 |
| 未请求的 nowcast 地图帧 | 只有显式 `include_map_cells=true` 才发布地图帧，当前 macOS 主流程没有请求 |
| 逐帧卫星动画、原始 HSD、雷达栅格 | 当前合同只返回统计、冻结剖面和诊断，不返回可播放帧序列或地图瓦片 |
| EC HCC/MCC/LCC 和模型层云水/云冰 | EC basic 路径没有这些输入，只有 TCC/RH 代理；不能用代理冒充模型层字段 |
| `confidence_score` 作为命中概率 | 后端语义是输入覆盖和几何支持度，不是概率校准结果 |
| 公共 Web 报告已完成 | 当前 `public_forecast_complete=false`；内部 Rust 结果可用于桌面展示，但不能标成公共报告完成 |
| 完整 tensor、绝对路径、SHA-256、cache key | 属于计算、恢复和完整性身份，直接展示会造成误读并泄露本地布局 |
| 跨 ±180° 的完整预测地图 | CAMS 拆分/合并/预检、连续经度轴和相关采样已有修复及科学回归；真实 ADS 双区域下载与连续地图实机显示仍未验收 |
| 真实 GFS/EC 分歧数值 | 当前 `model_disagreement_score` 是占位零，缺少跨模型对齐计算；诊断明确“尚未计算” |
| 点/实况按时刻冻结剖面查询 | 已接入点预测最佳时刻与实况步骤查询；点预测尚无任意时刻选择器，读取摘要不替换内嵌剖面图 |
| 历史结果恢复 | `result_restore` 已实现不可变快照恢复，不受计算缓存 300 秒和原始文件校验限制；仍受报告保留策略与依赖完整性约束 |
| 任意全球地点的实况 | 已移除 EAST_ASIA 回落并修正球面采样范围与 GFS 跨线裁剪；真实两帧全链路仍未验收，卫星视场外不承诺观测可用 |

因此，截图中的“主要云层”可以直接接入后端字段；“地图动画”“完整卫星图层”和
“模型层云水/云冰”等内容必须等相应后端合同完成后再显示，当前应明确显示原因。
