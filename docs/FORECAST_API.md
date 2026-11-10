# FireSky 预测接口与后端数据能力说明

核对日期：2026-11-09。适用对象：Web 前端、移动端、第三方调用方及后端集成人员。

本文以当前工作区的 `src/firesky/api.py`、`web_server.py`、`national_point_core.py`、`national_batch.py`、`service.py` 和卫星实现为依据，不将旧 README、前端类型声明或界面显示直接当作服务合同。当前工作区包含未提交修改，本文描述的是本次核对时的代码状态，不代表某台远端服务器已经部署相同版本。

阅读导航：

- 能力总览与公共约定：第 1～3 节。
- 单点请求、返回字段与评分：第 4 节。
- 区域请求、27 列摘要、完整逐时缓存：第 5 节。
- 实况请求、卫星 / 雷达 / 云柱与缺测：第 6 节。
- 剖面和地图：第 7 节；任务和错误：第 8 节。
- 数据准备：第 9 节；curl 示例：第 10 节。
- 桌面 IPC、能力边界与现有差异：第 11～13 节。

## 1. 后端现在能给什么

| 数据能力 | 单点模式预测 | 区域模式预测 | 单点实况 / 临近预测 |
| --- | --- | --- | --- |
| 输入来源 | GFS 或 EC，分别请求 | GFS 或 EC，分别请求 | Himawari 校准卫星云柱为主，GFS 垂直先验、气溶胶与雷达辅助 |
| 日出 / 日落事件 | 支持 | 支持，各格点按当地事件计算 | 支持，围绕日出 / 日落，不是任意时刻天气查询 |
| 综合质量指数、档位 | 支持 | 每个格点支持 | 支持，但可能只能发布瞬时质量 |
| 峰值质量、最佳时刻 | 支持 | 每个格点支持 | 支持，主报告按事件核心窗口选峰 |
| 持续时间 | 支持估计，必须检查状态 | 摘要提供每点可烧时长 | 有足够独立观测或合格运动证据才估算，否则为未知 |
| 逐时评分曲线 | `steps` | 完整缓存中的 `frame_data`；精简摘要不含 | `steps`，含云场来源扫描时间 |
| 评分分解 | `quality_components` | 完整缓存每点提供；摘要只保留精选指标 | 同单点，并有卫星与雷达诊断 |
| 高 / 中 / 低云 | 剖面、逐时指标、主导可烧云层 | 摘要每点三族云量；完整缓存有逐时通道 | 云柱、垂直剖面与局地 / 远处云量等 |
| 云底 / 云顶 / 地形 | 支持，多种高度口径 | 完整缓存与剖面支持，摘要不含云底 / 云顶 | 卫星反演及先验联合估计，可能为 `null` |
| 云相 / 云属 | 云相诊断；云属兼容集与代理判断 | 完整数据按实际字段提供，摘要只有主导压力族 | 卫星云相、云型及相关诊断 |
| 气溶胶 | AOD、路径影响、返程透过率等 | 摘要提供 AOD 和入射 / 观测修正等 | 同时提供气溶胶环境；颗粒物可缺失 |
| 地图时间帧 | 有匹配区域缓存时可附带，不保证 | 完整缓存支持区域逐时云场 | 返回地图 token / 时刻，实际取决于成功生成的有效帧 |
| 太阳方向大气剖面 | 结果可直接带 `cross_section` | 通过压力层缓存读取，EC 路由有额外限制 | 有卫星垂直剖面时可附带 |
| HTML 报告 | EC 路径生成；当前 GFS 统一点核心路径不保证生成 | 公共摘要不返回 HTML 报告 | 当前成功路径生成 |
| 数据来源与周期 | 支持 | 支持 | 有卫星扫描与先验来源字段，但部分通用来源字段尚不准确 |
| 区域实况批量计算 | 不适用 | **不支持** | 当前 HTTP 实况接口是单点 |

核心结论：后端交付的不只是“烧不烧”，还包括“什么时候、持续多久、哪一族云、云在什么高度、哪些环节限制质量、数据来自哪个周期”。但它不是完整的通用天气 API，也不提供经过事件命中率校准的概率。

## 2. 服务入口与公共约定

### 2.1 地址和版本

默认入口：

```text
http://127.0.0.1:8765
```

启动命令：

```bash
uvicorn firesky.api:create_app --factory --host 127.0.0.1 --port 8765
```

接口前缀为 `/api/v1/firesky`，请求体为 JSON。FastAPI 默认还提供 `/docs` 和 `/openapi.json`。预测请求体目前直接由业务函数解析，OpenAPI 并没有完整描述全部预测 JSON 字段，不能仅凭自动生成的页面生成完整客户端。

本次核对的主要版本：

| 项目 | 当前值 |
| --- | --- |
| 预测算法版本 | `2026.11.04.87` |
| GFS 区域摘要版本 | `55` |
| EC 区域摘要版本 | `1` |
| 区域完整缓存版本 | `125` |
| 区域公共点数组格式 | `array-v6` |
| GFS 单点响应缓存版本 | `122` |
| EC 单点响应缓存命名空间 | `ecmwf-ifs-basic-point-response-v2` |
| 区域逐时帧版本 | `102` |
| 卫星云柱 schema | `4` |
| 卫星垂直剖面 schema | `1` |

调用区域计算前，先请求：

```http
GET /api/v1/firesky/forecasts/national/contract
```

返回 `scope`、`algorithm`、`summary_version`、`cache_version`、`point_format`。`algorithm` 是完整算法标识，不只是短版本号；客户端应读取并核对，不应自行拼接字符串。

### 2.2 时间、单位和空值

| 约定 | 说明 |
| --- | --- |
| `target_date` | `YYYY-MM-DD`。单点指观测点当地日期；区域由各点时区解释同一个日期 |
| `phenomenon` | `dawn` 朝霞 / 日出；`dusk` 晚霞 / 日落 |
| `timezone_name` | 有效 IANA 时区，例如 `Asia/Shanghai`，不是 `UTC+8` |
| `*_utc` | 带明确 UTC 时区的 ISO 8601 字符串，例如 `2026-11-09T00:00:00Z` 或 `+00:00` |
| `*_local` | 带时区偏移的 ISO 8601 字符串，不能去掉偏移后当 UTC |
| `anchor_local` | 该地点的日出 / 日落事件锚点，不等同于最佳观赏时间 |
| `offset_minutes` | 相对该地点事件锚点的分钟数，不是相对请求发送时间 |
| 坐标 | `lat` 纬度 `[-90,90]`；`lon` 经度 `[-180,180]` |
| 海拔 | 米；输入 `elevation_m` 为海拔高度，范围 `[-500,9000]` |
| 距离 / 气压 | `*_km` 为千米，`*_hpa` 为 hPa |
| 云量和风险 | 多数字段为 `0..1`；带 `_percent` 的字段通常为 `0..100`，不可混用 |
| AOD / 光学厚度 | 无量纲、非负，不是 PM2.5 浓度 |
| 未知值 | 保留 `null` / `unknown` / 缺失状态，不填成 `0`，尤其不能填成“晴空” |

区域数据管理接口的 `bbox` 顺序与预测区域 `bounds` 不同，详见第 9 节。

### 2.3 支持时效的边界

预测是“目标日期 + 朝霞或晚霞事件”，不是一次返回未来七天全部逐小时天气。模式时效能否计算，取决于所选周期的预报时次、模型和 CAMS 输入是否齐全；当前 HTTP 合同没有统一承诺任意地点未来固定 N 天全部可算。

实况面向临近日出 / 日落。未来事件距离参考时刻超过 4 小时会拒绝，不能用实况接口代替多日模式预报。已经过去的事件走历史回顾逻辑，需要该事件窗口的真实卫星资料，不保证历史资料一直在线或本地缓存始终保留。

## 3. 接口总表

下表路径省略公共前缀 `/api/v1/firesky`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | 存活检查，返回 `{"status":"ok"}`，不代表预测输入就绪 |
| GET | `/locations/search?query=...` | 地名定位，返回一个候选对象，不是分页地点列表 |
| GET | `/points/metadata?lat=...&lon=...` | 坐标对应时区、海拔 |
| POST | `/forecasts` | 启动单点模式任务 |
| POST | `/forecasts/compute` | 同步单点预测，完成后直接返回结果 |
| POST | `/forecasts/nowcast` | 启动单点实况任务 |
| POST | `/forecasts/nowcast/compute` | 同步单点实况 |
| POST | `/forecasts/national` | 启动区域任务；结果为精简摘要 |
| POST | `/forecasts/national/compute` | 同步区域计算；结果仍为精简摘要 |
| GET | `/forecasts/national/contract` | 当前区域合同版本 |
| GET | `/forecasts/national/summary` | 读取区域精简摘要，不启动计算 |
| GET | `/forecasts/national/cache` | 读取区域完整缓存，不启动计算 |
| GET | `/forecasts/national/cross-section` | 读取选点太阳方向压力层剖面 |
| GET | `/forecasts/national/diagnostic?token=...&lat=...&lon=...` | 读取已存在的区域诊断包 |
| GET | `/map-frames?token=...&time=...` | 读取单点地图时间帧 |
| GET | `/forecasts/events?job_id=...` | SSE 任务事件流 |
| GET | `/forecasts/jobs` | 当前进程内任务列表 |
| GET | `/forecasts/jobs/{job_id}` | 任务状态和进度 |
| GET | `/forecasts/results/{job_id}` | 读取已完成任务结果 |
| POST | `/forecasts/{job_id}/cancel` | 请求取消未完成任务 |
| POST | `/forecasts/national/refresh` | 后端批量刷新区域预测日期范围 |

虽然路径叫 `national`，实际支持中国、多个大区和自定义矩形，不限全国。

## 4. 单点模式预测

### 4.1 请求参数

```json
{
  "name": "北京观测点",
  "lat": 39.9042,
  "lon": 116.4074,
  "timezone_name": "Asia/Shanghai",
  "elevation_m": 45,
  "target_date": "2026-11-10",
  "phenomenon": "dusk",
  "model_source": "gfs",
  "forecast_mode": "model",
  "satellite_source": "off",
  "gfs_resolution": "0p25"
}
```

| 参数 | 必填 | 取值 / 默认 | 说明 |
| --- | --- | --- | --- |
| `lat`、`lon` | 是 | 合法经纬度 | 请求的观测坐标 |
| `timezone_name` | 是 | IANA 时区 | 后端不会替调用方静默补齐 |
| `elevation_m` | 是 | `-500..9000` 米 | 即使中国网格对齐路径也必须先提供合法值 |
| `target_date` | 是 | 严格日期 | 不能省略成“今天” |
| `phenomenon` | 是 | `dawn` / `dusk` | 必须显式提供 |
| `name` | 否 | 坐标或默认名称 | 最长按 120 字符截取 |
| `model_source` | 否 | `gfs`；可选 `ec` | 一次只选一个模式，不接受 HTTP `both` |
| `forecast_mode` | 否 | `model` | 普通模式预测 |
| `satellite_source` | 否 | 普通模式强制 `off` | 当前模式路径不会因传 `auto` 就变为卫星融合预报 |
| `gfs_resolution` | 否 | `0p25` / `0p50`，默认 `0p25` | 不是任意高分辨率选项；EC 产品按其自身 0.25° 合同执行 |
| `radius_km` | 否 | 模式默认 `1000`，校验 `100..1000` | GFS 统一单点核心会规范为 1000 km，不能假设传 100 就只评估 100 km |
| `workers` | 否 | `1..8` | 计算并行参数，不是 Web 服务进程数 |
| `compute_backend` | 否 | `auto` / `cpu` / `gpu` | 请求偏好；不保证有 GPU 或实际执行 GPU |
| `force_refresh` | 否 | JSON 布尔值，默认 `false` | 绕过响应缓存；不是“上游一定已经发布新周期” |
| `gfs_cycle_override` | 否 | UTC 时间 | GFS 周期覆盖，兼容 `_gfs_cycle_override` |
| `ec_cycle_override` | 否 | UTC 时间 | EC 周期覆盖，兼容 `_ec_cycle_override` |

不应把内部 `_grid_region`、`_point_grid_mode`、`_include_point_core` 等当公共控制开关。`aod550_override` 虽存在于内部模型类型，但当前公共请求解析器未接入该参数，不应承诺调用方可以覆盖 AOD。

### 4.2 中国 GFS 单点的坐标与海拔语义

同时满足 `forecast_mode=model`、`model_source=gfs`、`gfs_resolution=0p25`，且坐标落在代码定义的中国区域矩形时，当前实现会：

1. 对齐最近的中国生产网格点，间距为 0.25°；恰在中点时选择较小坐标。
2. 使用 `Asia/Shanghai` 和该模式网格的地形海拔。
3. 在 `point_grid_resolution` 返回请求坐标与实际计算坐标。

中国区域这里指 `[73,18,136,54]` 的矩形，不是国界多边形。调用方不要在地图上把网格结果标成“严格原坐标、用户输入海拔的独立预报”。

`point_grid_resolution` 字段：

```text
mode = nearest-regional-grid-v1
region
grid_spacing_deg
requested_lat / requested_lon
resolved_lat / resolved_lon
resolved_timezone_name
resolved_elevation_asl_m（可提供时）
```

其他地区、EC 以及不满足上述条件的路径不应默认套用此规则。

### 4.3 返回数据目录

字段为条件性提供的地方已标明。不同路径有相同名称，并不保证每个值都非空。

| 类别 | 字段 | 能表达什么 |
| --- | --- | --- |
| 身份 | `observer_name`、`target_date`、`phenomenon`、`timezone_name` | 哪个观测点、哪天、朝霞还是晚霞 |
| 执行 | `model_source`、`forecast_mode`、`compute_backend_requested`、`compute_backend` | 模式来源与执行后端标识 |
| 周期 | `cycle_time_utc`、`model_cycles` | 实际模型起报周期；不是卫星扫描时刻 |
| 几何 | `observer_elevation_asl_m`、`analysis_radius_km`、`anchor_local` | 实际采用的观测者海拔、分析半径和事件时间 |
| 主要结论 | `quality_index`、`quality_level` | 整场事件质量指数和档位 |
| 瞬时峰值 | `peak_quality_index`、`peak_quality_level`、`best_time_local` | 事件中的峰值质量与最佳时刻，可与综合指数不同 |
| 分项评分 | `overall_score`、`burn_score`、`intensity_score`、`extent_score`、`vividness_score` | 归一化诊断分值，不是概率 |
| 支持程度 | `confidence_score`、`confidence_score_semantics` | 输入与几何支持或输入覆盖诊断，不是事件命中概率 |
| 模式诊断 | `model_weights`、`model_disagreement_score` | 当前路径返回的模式权重 / 分歧，不能因有此字段就认为自动运行了双模式 |
| 时间 | `duration_minutes`、`potential_duration_minutes`、`duration_status` | 持续时间估计与是否未知；未知时可为 `null` |
| 事件窗口 | `timing_window` | 由评分曲线等推导的起始、峰值、结束与持续时间，可能为空 |
| 几何窗口 | `geometry_window`、`boundary_uncertainty` | 当前 GFS 统一单点和 EC 单点路径设置为 `null`，不可当作稳定已有能力 |
| 评分解释 | `quality_components` | 云场、光路、AOD、时长与限制因子的详细分解 |
| 场景 | `selected_regime`、`scene_scores`、`local_sky_score`、`remote_sky_score`、`convective_risk` | 局地 / 远处云场、场景与对流风险诊断 |
| 色彩与变化 | `color_diagnosis`、`cloud_evolution` | 偏金黄 / 橙红等代理诊断，及增强 / 减弱趋势，不是相机实拍色值 |
| 主导云层 | `dominant_burnable_pressure_family` | `high` / `middle` / `low` 或空 |
| 云相 | `dominant_burnable_cloud_phase`、`cloud_phase_proxy`（按路径） | 冰 / 水 / 混合相及来源、支持程度 |
| 云属 | `dominant_burnable_cloud_genus_diagnosis`、`cloud_genus_proxy` | 相容云属集合或代理结果，不能证明真实 WMO 云属识别 |
| 气溶胶 | `aerosol` | AOD550、来源和质量；PM2.5 / PM10 及来源可为空 |
| 曲线 | `steps` | 每个有效时刻的评分、太阳高度、云场与光路诊断 |
| 剖面 | `cross_section`、`cross_section_request` | 当前剖面与后续换时刻查询所需参数 |
| 地图 | `map_token`、`map_times`、`map_capability`（按路径） | 可读取的地图帧；可能为空 |
| 报告 | `html_report_path`、`html_report_url` | 本机路径与 HTTP 报告地址；GFS 当前路径可能无报告 |
| 说明 | `summary` | 字符串数组，适合展示，不建议从自然语言反向解析数值 |
| 溯源 | `data_provenance`、`ecmwf_provenance`（EC）、`response_cache` | 输入源、文件 / 周期及缓存身份 |
| 对齐 | `point_grid_resolution`（符合条件时） | 中国 GFS 请求位置与实际网格位置 |

主导可烧云层还可以返回这些高度字段：

| 字段 | 高度口径 |
| --- | --- |
| `dominant_burnable_cloud_base_asl_mean_m` / `...top_asl_mean_m` | 云底 / 云顶平均海拔 |
| `dominant_burnable_target_terrain_asl_mean_m` | 目标云下方地形平均海拔 |
| `dominant_burnable_cloud_base_agl_mean_m` / `...top_agl_mean_m` | 相对目标当地地面的高度 |
| `dominant_burnable_cloud_base_above_observer_mean_m` / `...top_above_observer_mean_m` | 相对观测者海拔的高度 |

不能把 ASL、AGL 和相对观测者高度混成一个“云高”。没有主导目标云层时，上述值可为空。

### 4.4 `quality_components` 评分分解

该对象适合“为什么质量高 / 为什么被压低”的解释视图。字段随路径提供，不要求每条路径填满全部诊断。

| 指标组 | 主要字段 |
| --- | --- |
| 云量基础 | `candidate_cloud_coverage_peak`、`view_cloud_coverage_peak`、`burnable_cloud_coverage_peak`、`burnable_cloud_coverage_mean`、`burnable_cloud_coverage_effective` |
| 前方云场 | `forward_view_cloud_field_amount_peak/mean`、`forward_burnable_cloud_field_amount_peak/mean/effective`、`forward_burnable_cloud_ratio_peak/mean` |
| 可烧载荷 | `forward_burnable_cloud_load_peak/effective`，以及实际返回的材质、入射气溶胶载荷分项 |
| 本地云场 | `observer_local_cloud_field_amount_peak/mean`、`observer_local_burnable_cloud_field_amount_peak/mean`、`observer_column_quality_index` |
| 入射阻挡 | `solar_incoming_block_risk_mean/peak/at_best_cloud` |
| 时长 | `duration_minutes`、`duration_factor`、`duration_adjustment`、`duration_score_cap` |
| 材质 / 明亮色彩 | `brightness_color`、`brightness_adjustment`、`cloud_material`、`cloud_material_adjustment` |
| AOD | `local_aod550`、`quality_aod550`、`observer_path_aod550`、`inflow_path_aod550` |
| 双向光路 | `incoming_aod_illumination_factor`、`observer_aod_contrast_factor`、`observer_return_optical_depth_mean`、`observer_return_transmission_mean` |
| 气溶胶修正 | `incoming_aod_adjustment`、`observer_aod_adjustment` |
| 总结解释 | `raw_quality_index`、`quality_score_cap`、`quality_limiting_factor`、`score_formula`、`score_max`、`scoring_scope` |
| 光学来源 | `cloud_optics_source`、`effective_radius_source`、`cloud_optical_depth_source` |

“可烧云绝对量”“可烧云占前方云量的比例”“严格角面积覆盖”是不同量，不应因为都在 `0..1` 就互相替代。AOD 入射影响和观测返程影响也应分开显示。

### 4.5 `steps` 逐时曲线

每步主要包含：

- 时间和有效性：`valid_time_local`、`valid`；实况路径还提供 `cloud_source_times_utc`。
- 太阳与质量：`solar_elevation_deg`、`instant_quality_index`、`instant_quality_level`、`burn_score`。
- 可烧判定：`physically_burnable`；部分路径含 `observation_burnable`。
- 前方云量、可烧云量、载荷与比例：`forward_view_cloud_field_amount`、`forward_burnable_cloud_field_amount`、`forward_burnable_cloud_load`、`forward_burnable_cloud_ratio`。
- 阶段性排除指标：`forward_candidate_cloud_field_amount`、`forward_target_eligible_cloud_field_amount`、`forward_solar_geometry_reachable_cloud_field_amount`、`forward_external_cloud_path_transmitted_field_amount`、`forward_unoccluded_burn_potential_upper_bound`。
- 遮挡与返程：`solar_incoming_block_risk`、`observer_return_optical_depth`、`observer_return_transmission`，以及本地返程分项。
- 高 / 中 / 低云场及可烧分项：`forward_high_cloud_amount`、`forward_mid_cloud_amount`、`forward_low_cloud_amount` 和对应 `*_burnable_amount`。
- 分项评分、场景、颜色、主导云层高度与云相等，按实际字段提供。

当前统一模式时间轴：朝霞相对日出 `-75..+20` 分钟，晚霞相对日落 `-20..+75` 分钟，5 分钟间隔。部分帧可能因太阳几何条件而无效，应读取 `valid`，不要把所有帧当成有效预报。卫星实况的时轴来源不同，不能硬编码成同样 20 帧。

### 4.6 质量指数与档位

质量指数范围 `0..2.5`，不是 `0..100%`。

| 指数区间 | 档位 |
| --- | --- |
| `[0,0.001)` | 无明显火烧云 |
| `[0.001,0.05)` | 微微烧 |
| `[0.05,0.20)` | 小烧 |
| `[0.20,0.40)` | 小烧到中等烧 |
| `[0.40,0.60)` | 中等烧 |
| `[0.60,0.80)` | 中等烧到大烧 |
| `[0.80,1.00)` | 不完美大烧 |
| `[1.00,1.50)` | 典型大烧 |
| `[1.50,2.00)` | 优质大烧 |
| `[2.00,2.50]` | 世纪大烧 |

优先使用后端返回的 `quality_level` 和 `peak_quality_level`。`quality_index` 是事件综合值，`peak_quality_index` 是瞬时峰值，`burn_score` 是归一化诊断，三者不能混用。

## 5. 区域模式预测

### 5.1 请求示例与限制

中国生产网格：

```json
{
  "target_date": "2026-11-10",
  "phenomenon": "dusk",
  "model_source": "gfs",
  "forecast_mode": "model",
  "satellite_source": "off",
  "regions": ["china"],
  "spacing_deg": 0.25,
  "gfs_resolution": "0p25",
  "workers": 4,
  "compute_backend": "auto"
}
```

自定义区域：

```json
{
  "target_date": "2026-11-10",
  "phenomenon": "dawn",
  "model_source": "ec",
  "forecast_mode": "model",
  "regions": ["custom"],
  "custom_label": "京津附近",
  "custom_bounds": {"west": 115, "south": 39, "east": 118, "north": 41},
  "custom_timezone_name": "Asia/Shanghai",
  "spacing_deg": 0.25,
  "gfs_resolution": "0p25"
}
```

| 参数 | 规则 |
| --- | --- |
| `target_date` | 建议显式传；区域代码允许缺省为中国当地今天，不要依赖调用方系统时区 |
| `phenomenon` | `dawn` / `dusk`，区域默认 `dusk` |
| `model_source` | `gfs` / `ec`，默认 `gfs` |
| `forecast_mode` | 只支持 `model`；传 `nowcast` 会拒绝 |
| `regions` | 非空代码数组，默认 `["east-asia"]`；重复代码去重 |
| `spacing_deg` | `0.25..10`，一般默认 `2`；它是输出观测点间距，不是模型精度提升 |
| 中国单一区域 | 必须 `spacing_deg=0.25`、`gfs_resolution=0p25`，完整生成 36,685 点 |
| EC | 固定使用 0.25° 模型网格，不支持 `gfs_resolution=0p50` |
| `workers` | `1..8`，区域计算默认 4 |
| `radius_km` | 默认 1000 km，校验范围 `100..1000` |
| `custom_bounds` | 选择 `custom` 必填；东界大于西界，北界大于南界，不支持一个矩形跨日期变更线 |
| `custom_timezone_name` | 自定义区域必填，整个自定义矩形共用此 IANA 时区 |
| `force_refresh` | 布尔值，控制是否绕过结果缓存 |

中国公共区域接口不接受显式 `points` 或 `china-city-catalog` 替代全量生产网格。非中国路径底层还支持显式点列表，但不宜当成稳定的公共多点摘要合同使用：它涉及额外海拔来源声明，而且当前 GET 缓存查询不接收点列表。普通调用方应使用命名区域或自定义矩形。

`model_cadence_hours` 虽见于部分前端类型，不应理解为可自由改变公共区域结果的 5 分钟事件时间轴。

### 5.2 内置区域

下表 `bounds` 统一为 `[west,south,east,north]`。

| 代码 | 范围 | 时区 |
| --- | --- | --- |
| `china` | `[73,18,136,54]` | `Asia/Shanghai` |
| `east-asia` | `[63,12,155,60]` | 每点查询 |
| `southeast-asia` | `[85,-20,155,35]` | 每点查询 |
| `south-asia` | `[55,-5,110,45]` | 每点查询 |
| `europe` | `[-25,25,50,75]` | 每点查询 |
| `north-america` | `[-155,5,-40,80]` | 每点查询 |
| `south-america` | `[-90,-60,-25,20]` | 每点查询 |
| `oceania` | `[100,-60,180,5]` | 每点查询 |
| `africa-middle-east` | `[-25,-40,65,40]` | 每点查询 |
| `north-asia` | `[20,35,180,82]` | 每点查询 |
| `custom` | 请求指定 | 请求指定 |

命名区域是矩形网格，会包含海域及边界外地点。非中国命名区域没有统一时区；无法解析坐标时区时存在按经度时差回退的实现，因此不能把所有区域结果都按北京时间解读。

### 5.3 精简摘要与完整缓存不是同一响应

**精简摘要**：区域异步任务最终结果、区域 `/compute` 以及 GET `/summary`，都面向轻量展示。

共同顶层字段：

```text
version / algorithm / scope / point_mode
regions / coverage / target_date / phenomenon / model_source / cycle_time_utc
point_count / success_count / error_count
point_format / point_fields / points / errors
precision / timing / cache / data_provenance
```

GFS 摘要另外含 `forecast_mode`、`compute_backend_requested`、`compute_backend`、`cache_recommendation`；EC 摘要另外含 `model_resolution`、`forecast_hours`，不应要求它具有全部 GFS 专用顶层键。

`precision` 描述点间距、模型间距、点数、处理样本数、垂直廓线和全点覆盖等；`timing` 描述准备、张量、计算、帧生成和总耗时。

**完整缓存**：GET `/forecasts/national/cache`。当前原生批量路径额外保留逐点详细对象、`quality_components`、`frame_index`、`frame_data`、输入清单和相关诊断 / 计算元数据。完整缓存体积大，不应当成每次地图刷新都下载的默认响应。内部 `_point_core` 不属于公共可获取数据。

### 5.4 摘要每个点的 27 个字段

HTTP 原始响应中 `points` 是二维数组，**不是对象列表**。按返回的 `point_fields` 解码：

```javascript
const pointObjects = payload.points.map(row =>
  Object.fromEntries(payload.point_fields.map((name, i) => [name, row[i]]))
);
```

当前 `array-v6` 的列顺序如下：

| 列号 | 字段 | 含义 / 单位 |
| --- | --- | --- |
| 0 | `name` | 点名 |
| 1 | `lat` | 纬度 |
| 2 | `lon` | 经度 |
| 3 | `observer_elevation_asl_m` | 实际模式观测点海拔，米 |
| 4 | `quality_index` | 事件质量指数，`0..2.5` |
| 5 | `quality_level` | 事件质量档位 |
| 6 | `peak_quality_index` | 瞬时峰值指数，`0..2.5` |
| 7 | `peak_quality_level` | 峰值档位 |
| 8 | `dominant_burnable_pressure_family` | `high` / `middle` / `low` / `null` |
| 9 | `confidence_score` | 输入支持诊断，`0..1`，不是命中率 |
| 10 | `best_time_local` | 带偏移的当地最佳时间或 `null` |
| 11 | `burnable_cloud_coverage_effective` | 有效可烧云覆盖指标，`0..1` |
| 12 | `forward_view_cloud_field_amount_peak` | 前方可见云场量峰值，`0..1` |
| 13 | `forward_burnable_cloud_field_amount_peak` | 前方可烧云场量峰值，`0..1` |
| 14 | `forward_burnable_cloud_ratio_peak` | 前方可烧云量比例峰值，`0..1` |
| 15 | `burnable_duration_minutes` | 可烧时长，分钟 |
| 16 | `local_aod550` | 当地 550 nm AOD |
| 17 | `quality_aod550` | 质量计算采用的 AOD 指标 |
| 18 | `incoming_aod_illumination_factor` | 入射气溶胶照明因子，`0..1` |
| 19 | `observer_aod_contrast_factor` | 观测气溶胶对比度因子，`0..1` |
| 20 | `observer_return_optical_depth_mean` | 平均返程光学厚度 |
| 21 | `observer_return_transmission_mean` | 平均返程透过率，`0..1` |
| 22 | `incoming_aod_adjustment` | 入射 AOD 质量修正，指数单位 |
| 23 | `observer_aod_adjustment` | 观测 AOD 质量修正，指数单位 |
| 24 | `high_cloud` | 高云指标，`0..1` |
| 25 | `mid_cloud` | 中云指标，`0..1` |
| 26 | `low_cloud` | 低云指标，`0..1` |

摘要坐标保留两位小数，多数数值保留三位小数，时长保留一位；不要据此认为底层只计算了相同精度。三族云指标也不是逐层互斥占比，不要求三者相加为 1。

### 5.5 缓存读取、陈旧结果与来源

```http
GET /api/v1/firesky/forecasts/national/summary?target_date=2026-11-10&phenomenon=dusk&model_source=gfs&regions=china&spacing_deg=0.25&gfs_resolution=0p25&forecast_mode=model&satellite_source=off
```

GET 查询与 POST 参数转换：

| POST | GET |
| --- | --- |
| `regions: ["china","east-asia"]` | `regions=china,east-asia`，不要传 JSON 数组字符串 |
| `custom_bounds.west/south/east/north` | `custom_west/custom_south/custom_east/custom_north` |
| `custom_label`、`custom_timezone_name` | 同名参数 |
| `target_date`、`phenomenon`、`model_source`、`spacing_deg`、`radius_km`、`gfs_resolution` | 同名参数 |
| 剖面身份 | `profile_key`、`elevation_m` 另传 |

读取函数只读缓存，不会因没有结果就自动算一遍。中国请求务必显式传 `spacing_deg=0.25`，GET 的一般缺省值是 `2`，不适用于中国生产合同。

GFS `/summary` 在当前周期结果缺失时，可以回退到同一目标日期、事件与空间身份下的较早完整周期，返回 `cache.status=stale`。应展示：

- `data_provenance.stale`、`inputs_complete`、`generated_at_utc`。
- `requested_gfs_cycle_utc` 与 `served_gfs_cycle_utc`。
- `gfs_forecast_hours`、`gfs_valid_times_utc`。
- `cams_source`、`cams_run_times_utc`、`cams_valid_times_utc`。

EC 摘要使用独立来源合同：`model_source`、`cycle_utc`、`forecast_hours`、`model_files`、`cams_files`。当前没有同样的 GFS 陈旧摘要回退逻辑。

GET `/cache` 要求匹配当前完整缓存，不等同于 `/summary` 的陈旧摘要回退。`cache.status=hit` 只表示命中缓存，不表示输入永远是最新。

当前公共 GET 参数解析器**不转发**调用方传入的 `gfs_cycle_override` / `ec_cycle_override` 或下划线别名，因此不能用这些 query 参数保证读取一个指定旧周期。需要可复现结果时保留任务原始响应和来源信息；剖面优先使用响应给出的 `profile_key`。

### 5.6 完整缓存中的逐时帧

`frame_data` 当前为 `version=102`，`encoding=base64-quantized-row-major-v1`。其 `frames` 每项对应一个事件相对时刻，每个通道是一段 Base64 编码的点序列，不能直接当 JSON 数值数组使用。

读取规则：

- 用完整点对象的 `frame_index` 找到对应通道槽位，不要拿摘要解码后重排的下标代替。
- 先检查 `valid`；`valid` 和 `physically_burnable` 为原始 `0/1` 字节。
- 大多数云量 / 风险 / 透过率通道为 `u8 / 255`。
- `instant_quality_index` 为小端 `u16 × 0.001`。
- 可烧云场载荷相关通道为小端 `u16 × 0.01`。
- `local_aod550`、`cloud_optical_depth`、`observer_return_optical_depth`、`observer_local_return_optical_depth` 为小端 `u16 × 0.001`。
- `clear_gap_count` 为小端 `u16 × 0.01`。
- 三个主导云层 / 地形海拔通道为小端 `i16 × height_scale_m`，当前高度量化步长 10 米。
- 压力族代码和材质分类代码是枚举字节，不能除以 255 当云量。

帧还提供高 / 中 / 低云及可烧量、目标云层连接与衰减、云光学厚度、太阳照明、遮挡与双向返程诊断。具体通道集合应与 `national_frame_contract.py` 一起校验；`selected_column_diagnostic_source` 可区分 GFS / EC 选中列诊断，不能把某个格点的选中列诊断当成全部前方天空。

## 6. 实况与临近预测

### 6.1 请求方式

```http
POST /api/v1/firesky/forecasts/nowcast
Content-Type: application/json
```

```json
{
  "name": "北京实况",
  "lat": 39.9042,
  "lon": 116.4074,
  "timezone_name": "Asia/Shanghai",
  "elevation_m": 45,
  "target_date": "2026-11-09",
  "phenomenon": "dusk",
  "radius_km": 900
}
```

该日期仅为请求格式示例，实际调用要选择适合参考时间的事件。

实况专用端点会强制 `forecast_mode=nowcast`、`model_source=satellite`，并把 `satellite_source=off` 改成 `auto`。单点必填参数与普通单点相同；半径默认 900 km，允许 `100..1000`。

可额外传 `reference_time_utc` 固定本次观测截止参考时间；缺省为服务端当前 UTC。历史回顾会围绕历史事件选择扫描，并不是拿“现在最新一帧”回填过去。

这是“指定事件的卫星实况分析 / 临近外推”，不是摄像头实景、任意地点当前温度查询，也不是全球任何区域都能获得数据的实况服务。实际可用范围受 Himawari 视场、扫描资料、新鲜度与先验覆盖限制。

### 6.2 实况返回哪些额外数据

顶层仍是单点结果结构，额外重点读取 `nowcast`：

| 数据组 | 字段 | 用途 |
| --- | --- | --- |
| 卫星身份 | `source`、`image_valid_time_utc`、`image_channel`、`image_area` | 来源、扫描时间、通道与区域；不是图片下载 URL |
| 算法 | `nowcast_algorithm`、`cloud_column_schema_version` | 核对当前实况合同 |
| 云量位置 | `near_cloud_ratio`、`far_cloud_ratio`、`local_cloud_ratio`、`horizon_cloud_ratio` | 近处、远处、本地和地平线方向云量诊断 |
| 云幕结构 | `thin_cloud_ratio`、`thick_cloud_ratio`、`cloud_texture_score`、`cloud_uniformity_score`、`cloud_blanket_score` | 薄云 / 厚云、纹理、均匀性和铺满程度 |
| 观赏方向 | `horizon_clearance_score`、`composition_score`、`geometric_path_blocking_risk`、`geometric_path_samples` | 地平线通透与光路遮挡诊断 |
| 动态 | `trend_score`、`projection_hours`、`motion_dx_px_per_hour`、`motion_dy_px_per_hour`、`motion_correlation` | 云团趋势、外推时长与像素空间运动；不是米每秒风场 |
| 运动证据 | `satellite_source_times_utc`、`satellite_motion_age_minutes`、`satellite_motion_quality`、`satellite_advection_horizon_hours` | 源扫描列表、运动年龄、质量与外推时距 |
| 光谱诊断 | `multispectral_valid_time_utc`、`spectral_b11_b13_index`、`spectral_b13_b15_index`、红外 / 可见近远云量、`channel_fusion_weight_visible` | 可为空；字段名存在不代表对应波段一定参与当前 HSD 主评分 |
| 云相 / 云型 | `ice_cloud_probability`、`water_cloud_probability`、`thin_cirrus_probability`、`altocumulus_probability`、`altostratus_probability`、`cirrus_probability`、`cloud_type_label`、`cloud_type_confidence` | 卫星云相和云型代理诊断，部分路径为空 |
| 雷达 | `radar_source`、`radar_valid_time_utc`、`radar_age_minutes`、`radar_coverage_ratio`、`radar_max_dbz` | 雷达来源、新鲜度、覆盖与最大反射率 |
| 雷达降水 / 对流 | `radar_local_precip_ratio`、`radar_path_precip_ratio`、`radar_convective_ratio`、`radar_convective_risk`、`radar_reliability_score` | 本地和光路上的降水 / 对流辅助约束，不是完整雷达图 |
| 合并风险 | `effective_convective_risk`、`effective_path_blocking_risk`、`convective_score_ceiling` | 有效风险和评分上限 |
| 反演可用性 | `calibrated_sample_coverage_ratio`、`satellite_retrieval_quality`、`satellite_low_cloud_state`、`confidence` | 覆盖率、质量与低云是否可判定 |
| 云柱数据 | `cloud_column_sets` | 各扫描 / 外推时刻对应的校准云柱集合 |
| 先验 | `vertical_prior_identity`、`vertical_prior_source` | 同周期垂直先验身份 |
| 位置 / 缓存身份 | `nowcast_point_lat`、`nowcast_point_lon`、`nowcast_cache_identity` | 实况实际选点及输入身份 |
| 数据访问量 | `satellite_range_bytes`、`satellite_range_blocks` | HSD 分段读取字节数和块数，不是请求次数 |
| 垂直剖面 | `satellite_vertical_profile` | 按距离 / 方向 / 压力层的反演与模型先验对照 |
| 光路剖面 | `solar_path_profile` | 云底、云顶、云族、地影门槛、阻挡与压力层气象字段 |

### 6.3 云柱集合与云底不确定性

`cloud_column_sets[]` 每项含 `schema_version`、`valid_time_utc`、`source`、`vertical_prior_identity`、`retrieval_quality`、`low_cloud_state`、运动分量及 `columns`。

每根云柱的核心数据：

| 字段 | 说明 |
| --- | --- |
| `lat`、`lon`、`distance_km`、`bearing_deg` | 云柱位置和相对观测点方向 |
| `base_p10_asl_m` / `base_p50_asl_m` / `base_p90_asl_m` | 云底反演分布的海拔分位数 |
| `base_p10_agl_m` / `base_p50_agl_m` / `base_p90_agl_m` | 云底离当地地面的分位数 |
| `top_asl_m`、`top_agl_m`、`terrain_asl_m` | 云顶和地形高度 |
| `phase` | `water` / `ice` / `mixed` / `unknown` |
| `cloud_type`、`optical_depth` | 云型描述、光学厚度，可缺测 |
| `cloud_probability_by_height` | 垂直高度上的云概率结构 |
| `horizontal_cloud_probability` | 水平方向有云概率，须与条件垂直分布区分 |
| `low_cloud_state` | `detected` / `clear` / `unknown`，未知不等于无低云 |
| `retrieval_quality` | `high` / `medium` / `low` / `unknown` |
| `source`、`vertical_prior_identity`、`vertical_prior_source`、`base_source` | 反演、先验和云底来源 |
| `algorithm`、`schema_version`、`valid_time_utc` | 云柱算法和时刻身份 |

分位数是反演结果的不确定性表达，不是当地激光测云仪观测到的精确云底。

`satellite_vertical_profile` 还含 `status`、`bands`、`prior_source`、采样数 / 有效数 / 未知数、`coverage`、`pressure_levels_hpa` 和 `samples`。每个样本按压力层给出高度、湿度、温度、模式云量、HSD 后验云概率及状态。其 `numeric_clear_sky_allowed=false`：缺测不可数值化为晴空。

### 6.4 时序、缺测与持续时间

主报告围绕日出 / 日落核心 `±15` 分钟选峰和统计；外围时间用于趋势。代码另有 `-30..+30` 分钟、5 分钟间隔的卫星时刻机制，实际 `steps` 受扫描可用性约束，应按返回时刻绘图。

`satellite_motion_quality` 可为 `unknown`、`observation`、`strong`、`decayed`、`diagnostic-only`。有单帧实况不代表有可靠云运动外推，有逐时分数也不代表每一步都是独立新观测。

持续时间需要至少两个实际评分源时刻，或满足强 / 衰减运动外推的合格条件。证据不足时：

- `duration_status=unknown`；
- `duration_minutes` / `potential_duration_minutes` 可为 `null`；
- 质量可按瞬时值发布，客户端不能把未知时长显示成“0 分钟”。

没有完整校准扫描、没有可用云柱、垂直先验缺失或反演质量为 `unknown` 时会失败，不会静默返回一个“0 分晴空”。

当前覆盖门槛存在实现与文字差异：卫星 v4 分析分支用 `coverage>0` 判断至少存在可用列；服务层拒绝 `retrieval_quality=unknown`，但错误文字仍提到“≥80%”。因此不能把“HTTP 成功”解释成已经通过统一 80% 覆盖率验收。需要这一产品门槛的调用方应自行检查 `calibrated_sample_coverage_ratio`。

当前实况不保存预测响应持久缓存，也绕过普通响应内存复用；底层 HSD / 模式 / CAMS 文件缓存仍可使用。

## 7. 剖面与地图数据

### 7.1 太阳方向压力层剖面

```http
GET /api/v1/firesky/forecasts/national/cross-section
```

请求包含区域 / 日期 / 事件身份，加上：

| 参数 | 说明 |
| --- | --- |
| `lat`、`lon` | 剖面观测点 |
| `elevation_m` | 必填，观测者海拔 |
| `offset_minutes` | 模式事件时间轴上的精确 5 分钟时刻；默认 0 |
| `profile_key` | 优先使用单点响应 `cross_section_request` 给出的原值 |

这不是任意两点连线剖面，也没有公共参数任意指定剖面方向；剖面按太阳方位生成。模式剖面默认距离 `0..1000 km`，每 25 km 采样。

主要响应：

```text
observer / valid_time_local / offset_minutes
solar_elevation_deg / solar_azimuth_deg / analysis_radius_km
distances_km / pressure_levels_hpa / terrain_elevation_asl_m
required_illumination_base_asl_m / required_illumination_base_agl_m
theoretical_targets / fields
model_source、terrain_source、spatial_sampling（按路径）
```

`fields` 数组按 `[距离采样点][压力层]` 对齐，主要包含：

| 字段 | 含义 |
| --- | --- |
| `geopotential_height_asl_m` | 压力层位势高度，米 |
| `temperature_c`、`rh_percent` | 温度 °C、相对湿度 % |
| `cloud_fraction_percent`、`cloud_presence`、`cloud_amount` | 云量 / 云存在指标，注意各字段口径 |
| `cloud_liquid_kg_kg`、`cloud_ice_kg_kg` | 云液水、云冰混合比 |
| `omega_pa_s` | 气压垂直速度 Pa/s，不是 m/s |
| `u_wind_ms`、`v_wind_ms` | 水平风分量 m/s |
| `cloud_layer_status` | 1 非可烧目标；2 已照明但不可见；3 理论可照明且可见 |
| `pressure_family_code` | 高 / 中 / 低压力族代码 |
| `cloud_layer_base_asl_m`、`cloud_layer_top_asl_m` | 诊断云层底 / 顶海拔 |

`theoretical_targets` 给出目标云层压力族、云相、云底 / 云顶、地形、照明所需高度、几何地平线余量、地形阻挡及状态原因。

**重要**：剖面 `status=burnable` 是理论照明与几何可见候选，不代表已经通过完整云体遮挡和质量评分。它不能替代 `quality_index`。

GFS `profile_key` 为 24 位十六进制键。当前 EC HTTP 剖面分支要求 `ec:` 加 64 位摘要，并依赖已有 **EC 单点响应缓存**。因此，即使 EC 区域完整缓存存在压力层数据，也不能假定把区域键直接传给该 HTTP 路由就能读取；EC 单点结果内嵌的剖面是当前更可靠的入口。

### 7.2 单点地图帧

```http
GET /api/v1/firesky/map-frames?token=返回的map_token&time=返回的map_times项
```

时间参数要 URL 编码，尤其注意 ISO 时间中的 `+`。帧返回太阳高度 / 方位、瞬时质量、前方云场指标、观测点与 `cells`。

`cells` 主要字段：`lat`、`lon`、`distance_km`、`bearing_deg`、`high_cloud`、`mid_cloud`、`low_cloud`、`cloud_evidence`、`deep_cloud_risk`、`burn_score`、`illumination_score`、`path_clearance_score`、`blocking_risk`。

单点质量计算成功不要求附近已经有完整区域二维网格。当前单点地图通常借用同周期区域缓存，缺少缓存时 token / 时刻可以为空，客户端应隐藏地图时间轴而保留评分与剖面。GFS 某些路径的 `map_capability` 文字与后续附带 token 可能未同步，应结合有效 token、时刻列表和实际取帧结果判断，不能只看一个标志。

### 7.3 区域完整诊断包

GET `/forecasts/national/diagnostic` 要求已经存在的诊断包 token，返回选点 `lat`、`lon`、`observer_name`、`diagnostic`。

当前公共精简摘要没有稳定暴露该 token，不能把此接口当成“任意区域摘要点自动都有完整诊断”。前端代码中另一个 `/forecast/jobs/{job_id}/diagnostic` 调用路径没有在本 FastAPI 服务注册，不应照抄为可用 API。

## 8. 异步任务、SSE 和错误处理

### 8.1 推荐流程

1. 准备坐标、时区、海拔与输入数据。
2. POST 对应异步入口，获得 `job_id`。
3. 订阅 SSE 或轮询 `/forecasts/jobs/{job_id}`。
4. 完成后 GET `/forecasts/results/{job_id}`。
5. 保存来源与周期；有 token 再按需取地图，有剖面参数再查询其他时刻。

单点启动返回 `{"job_id":"..."}`；实况还含 `forecast_mode=nowcast`；区域还含 `scope=regional`。

任务状态为 `queued`、`running`、`done`、`failed`、`cancelled`，不是旧前端类型中的 `computing` / `succeeded`。列表支持 `status`、`limit`、`offset`；`limit` 限制为 `1..200`。

任务详情主要提供：`id`、`status`、`progress_percent`、`progress_stage`、`progress_message`、`error_message`、`request_payload`、`started_at`、`created_at`、`completed_at`、`cache_key`、`algorithm_version`。`request_payload` 当前是 JSON 字符串，不是已解析对象。

### 8.2 SSE 事件

```text
event: progress
data: {"event":"progress","stage":"...","percent":20,"message":"...","elapsed_seconds":3.2}

event: done
data: {"event":"done","stage":"done","percent":100,"message":"预测完成","result_ref":"..."}
```

上述只是结构示意，不是一次实际天气计算结果。

- `progress`：进度与阶段，可能含 `details`，阶段字符串不宜硬编码成封闭枚举。
- `done`：普通单点 / 实况通常内嵌 `result`；区域使用 `result_ref=job_id`，再取结果接口。
- `failed`：`message`、`status`、`error_type`；GFS 输入不可用可标 `status=unavailable`。
- `cancelled`：取消终态。
- 空闲时约 15 秒发送注释心跳 `: ping`。

SSE 是进程内队列，不是持久消息总线，不提供历史事件全量回放保证。已完成任务的晚到订阅者可收到合成终态。建议每个任务只维持一个进度流，断线后通过任务详情恢复状态。

### 8.3 错误码与异步校验差异

| 情形 | 当前 HTTP 语义 |
| --- | --- |
| 同步参数 / 业务 `ValueError` | 400，`{"error":"..."}` |
| FastAPI 查询参数类型校验失败 | 通常 422，采用框架 `detail` 结构 |
| 缓存 / token / 任务不存在 | 404 |
| 结果尚未完成 | 结果接口也返回 404；应先查询任务状态区分 |
| 明确的 GFS 输入不可用异常 | 503，`{"status":"unavailable","error":"..."}` |
| 其他计算失败 | 500，`{"error":"..."}` |
| 实况预热 / 评分异常经包装后 | 可能表现为 500，不保证缺数据一律 503 |

异步 POST 收到 `job_id` 只说明创建了任务，不代表参数与数据已全部通过校验。部分非法参数会在后台才失败；读取失败任务结果时，一般异常被包装为 500，即使原始原因是 `ValueError`。应结合任务状态、SSE 的 `error_type` 和错误信息处理。

### 8.4 调度与生命周期

区域异步计算使用单独 FIFO 队列，可能先处于 `queued`。单点 / 实况任务使用独立线程执行，不具备持久化任务平台的保证。

任务表仅保存在当前 Python 进程内，重启后丢失。已完成任务超过约 900 秒可在后续清理时被移除，并不是精确到时的持久 SLA。部署时建议单个 Uvicorn 服务进程；如果多进程而没有同进程路由粘性，提交和轮询可能落到不同任务表，出现“任务不存在”。

取消属于协作式取消：状态可以先变为 `cancelled`，底层下载或计算不保证立即停止。同步 `/compute` 不进入同一异步区域队列，也不具备同样的任务取消流程；大型区域计算优先使用异步接口。

## 9. 数据准备与运行前提

### 9.1 依赖数据

| 预测类型 | 核心依赖 |
| --- | --- |
| GFS 单点 / 区域 | 对应周期 GFS 云量 / 水冰 / 垂直层 / 地形等输入；当前统一路径还依赖匹配的 CAMS 气溶胶输入 |
| EC 单点 / 区域 | EC IFS Open Data 自身模型与地形等资料、CAMS；不会用 GFS 静默替代 EC 评分输入 |
| 实况 | 可用的校准 Himawari HSD 扫描、同周期 GFS 垂直先验、气溶胶；雷达和颗粒物为辅助数据 |

当前 YAML 默认 `aerosol_source=cams`。实际可用性还取决于本机依赖、已下载文件、上游连通性以及有效 ADS 配置和数据集授权。不要把“服务能启动”视作“所有模式都能立即冷启动计算”。

### 9.2 数据管理接口

| 方法 / 路径（公共前缀省略） | 返回能力 |
| --- | --- |
| POST `/data/requirements` | 目标日期 / 区域所需数据集、周期、时次和下载计划 |
| GET `/data/status` | 本地缓存、数据集就绪 / 缺失等状态 |
| POST `/data/latest/refresh` | 刷新上游周期信息，返回周期及来源等；可能触发配置的区域更新，不能当纯只读查询 |
| POST `/data/fetch` | 启动下载任务，返回 `job_id`、`status`、`sources` |
| GET `/data/fetch/status?job_id=...` | 下载任务状态 |
| POST `/data/prune` | 清理数据，属于有副作用的管理接口 |
| POST `/cache/warmup` | 预测缓存预热，不等同于卫星文件预热 |
| POST `/satellite/cache/warmup` | 卫星缓存预热任务 |
| GET `/satellite/cache/warmup/status?job_id=...` | 卫星预热状态 |

下载计划示例：

```json
{
  "region": "china",
  "timezone": "Asia/Shanghai",
  "target_dates": ["2026-11-10"],
  "phenomena": ["dawn", "dusk"],
  "sources": ["gfs", "ecmwf", "cams"]
}
```

预测参数用 `model_source=ec`；数据下载源常用 `ecmwf`，相关数据计划 / 下载实现也兼容 `ec` 别名，客户端宜统一使用 `ecmwf`。

特别注意坐标边界顺序：

```text
预测 coverage.bounds：        [west, south, east, north]
预测 custom_bounds：          {"west":..., "south":..., "east":..., "north":...}
data/* 请求与计划中的 bbox：   [west, east, north, south]
```

卫星预热接口使用服务端配置的 `satellite_warmup_token` 和请求头 `x-firesky-warmup-token` 进行访问检查。当前预测 / 数据管理主路由没有统一用户鉴权中间件；不要把它们直接视作可公开暴露的多租户 API。认证、速率限制和管理接口隔离需要部署层另行处理。

## 10. 最小调用示例

下面只是调用方式，未执行真实远端数据下载或天气预测。

### 10.1 同步 GFS 单点

```bash
curl -sS 'http://127.0.0.1:8765/api/v1/firesky/forecasts/compute' \
  -H 'Content-Type: application/json' \
  -d '{
    "name":"北京",
    "lat":39.9042,
    "lon":116.4074,
    "timezone_name":"Asia/Shanghai",
    "elevation_m":45,
    "target_date":"2026-11-10",
    "phenomenon":"dusk",
    "model_source":"gfs",
    "forecast_mode":"model",
    "gfs_resolution":"0p25"
  }'
```

切换到 EC 只需改 `model_source`，两者应分别提交。HTTP 没有一次 `both` 自动返回双模式结果的公共参数。

### 10.2 中国区域异步计算

```bash
curl -sS 'http://127.0.0.1:8765/api/v1/firesky/forecasts/national' \
  -H 'Content-Type: application/json' \
  -d '{
    "target_date":"2026-11-10",
    "phenomenon":"dusk",
    "model_source":"gfs",
    "forecast_mode":"model",
    "regions":["china"],
    "spacing_deg":0.25,
    "gfs_resolution":"0p25",
    "workers":4
  }'
```

使用响应中的真实 job ID：

```bash
JOB_ID='替换为刚返回的job_id'
curl -N "http://127.0.0.1:8765/api/v1/firesky/forecasts/events?job_id=$JOB_ID"
curl -sS "http://127.0.0.1:8765/api/v1/firesky/forecasts/jobs/$JOB_ID"
curl -sS "http://127.0.0.1:8765/api/v1/firesky/forecasts/results/$JOB_ID"
```

### 10.3 只读取区域摘要

```bash
curl -sS --get \
  'http://127.0.0.1:8765/api/v1/firesky/forecasts/national/summary' \
  --data-urlencode 'target_date=2026-11-10' \
  --data-urlencode 'phenomenon=dusk' \
  --data-urlencode 'model_source=gfs' \
  --data-urlencode 'regions=china' \
  --data-urlencode 'spacing_deg=0.25' \
  --data-urlencode 'gfs_resolution=0p25'
```

如果要完整逐时云场，把路径改为 `/forecasts/national/cache`，并按完整缓存合同处理，不能再假设 `points` 是 `array-v6` 摘要行。

## 11. 桌面 IPC 与 HTTP 的区别

原生桌面客户端还有私有 JSON-lines IPC：

```bash
python -u -m firesky.desktop_rpc
```

该进程通过 stdin / stdout 通信，不监听 HTTP 端口。支持 `hello`、`search`、`metadata`、`inventory`、`check`、`publications`、`prepare`、`forecast`、`dismiss`、`cancel` 等方法。

桌面流程是“检查数据 -> 获得 session -> 准备 -> 预测”，与 HTTP `job_id` 流程不是同一个合同。桌面单点 / 区域会分别尝试 GFS 和 EC，返回 `model_results` 和 `model_errors`；实况仍走单点卫星路径。桌面区域使用完整结果函数，因此能拿到的逐时数据可能比 HTTP 默认区域摘要多。

Rust 引擎的 JSON-lines 能力也不等于新增了 HTTP API。桌面能调用某个诊断方法，不能据此编造同名 HTTP 路由。

## 12. 当前不能承诺的能力

- 没有区域批量实况公共计算接口。
- 没有通用天气站实测温湿风雨查询；剖面中的温湿风主要来自数值模式或先验。
- 没有经历史事件校准的“出现概率 80%”合同，`confidence_score=0.8` 不表示 80% 会烧。
- 云属代理和颜色代理不等同于真实云属 / 实拍颜色识别；云属诊断标注 `scoring_effect=false`。
- 没有保证每个单点都附带地图、HTML 报告和非空持续时间。
- 没有已注册的历史记录 / 用户反馈 / 校准 API；前端遗留类型不代表接口仍存在。
- 没有在本 FastAPI 层注册卫星影像列表、卫星原图下载或完整雷达图接口；实况返回的是预测与诊断数据。
- 没有持久化分布式任务队列，也没有仅凭 `compute_backend=gpu` 保证实际 GPU 执行的合同。

## 13. 接入时必须留意的现有差异

1. **以真实路由为准**：区域 `/summary` 是 GET，不是旧 README 曾列出的 POST。未知 GET 可能落到 SPA 返回 HTML，调用方要检查 HTTP 状态和 `Content-Type`，不能把 200 一律当 API JSON 成功。
2. **以响应来源为准**：HTTP 每次只预测一个模式，桌面自动双模式属于 IPC 编排，不是 HTTP 自动融合。
3. **以运行时版本为准**：前端 `types.ts` 仍有 GFS 摘要 `version:53`、旧任务状态和部分旧字段；后端实际摘要为 55。不要把旧类型声明直接复制成服务端事实。
4. **实况通用溯源字段有待统一**：当前通用结果包装仍可能返回 `data_provenance.model_source=gfs`、`observation_used=false`，虽然主路径实际上是卫星实况。识别实况应结合顶层 `forecast_mode` / `model_source`、`nowcast` 扫描与云柱证据，不要只读这两个通用字段。
5. **`source_roles` 不是完整观测审计**：GFS 模式结果可能仍列 `auxiliary=satellite_observation`，但普通模式入口已关闭卫星。是否真正使用观测必须结合执行路径和实际来源证据。
6. **EC 剖面路由不是通用区域剖面读取器**：当前分支依赖 EC 单点响应键，见第 7.1 节。
7. **实况覆盖率要显式判断**：当前没有一致的 80% 硬门槛保证，见第 6.4 节。
8. **地图可用性独立判断**：token、时刻和实际帧要一起核实；无地图不意味着预测失败。
9. **状态码不是全部业务状态**：异步创建成功可能随后失败，结果 404 也可能只是尚未完成。

## 14. 本文核对范围

已完成：路由和主要业务函数静态核对，运行时常量 / 区域边界 / 模式事件时间轴核对；5 个 JSON 示例、21 条路由、27 个摘要点字段及 10 个区域边界的自动一致性检查；`tests/test_backend_api.py` 与 `tests/test_engine_api.py` 共 7 项离线契约测试通过。

未验证：外部服务部署版本、真实上游数据当前可下载性、一次完整冷启动 GFS / EC / 卫星计算、实况覆盖质量、预报准确率、GPU 实际执行以及生产负载能力。本说明记录代码可提供的接口和数据，不作为实时数据就绪或预测科学准确性的验收证明。

维护入口：

| 内容 | 主要源码 |
| --- | --- |
| HTTP 路由、错误与 SSE | `src/firesky/api.py` |
| 请求解析、单点 / 区域封装、缓存 | `src/firesky/web_server.py` |
| GFS 单点公共结果 | `src/firesky/national_point_core.py` |
| 区域数据、时间帧与模式剖面 | `src/firesky/national_batch.py`、`national_frame_contract.py` |
| 实况编排与缺测约束 | `src/firesky/service.py`、`satellite.py`、`satellite_cloud_column.py` |
| 质量档位与代理诊断 | `src/firesky/forecast_characteristics.py` |
| 桌面 IPC | `src/firesky/desktop_rpc.py` |
