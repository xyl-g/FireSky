# Web 与 macOS 历史展示字段矩阵（单点 / 实况 / 区域）

更新日期：2026-11-13。

> 本表是实施前缺口盘点快照，不代表 71 项仍待开发；本批实施状态和 GUI 待验项见
> [展示对齐方案](WEB_MACOS_DISPLAY_PARITY_PLAN.zh-CN.md)，当前接续顺序见
> [最新计划](NEXT_AGENT_PLAN_2026-11-13.zh-CN.md)。旧行号按盘点保留；point-12
> 后续应先复用现有 `profile_query.valid_time_utc`，不预设必须扩展 forecast RPC。
> **用户最新决定：macOS 是产品与 UI 基准，其他 UI 向 macOS 对齐。** 本表保留
> 原 Web -> 桌面的盘点证据，不能直接当作当前开发清单；当前方向与跨端矩阵格式见
> [macOS 基准](MACOS_UI_BASELINE.zh-CN.md)。后续以验收后的 macOS 页面/流程为行登记其他端差异。

本文是《Web 与 macOS 展示对齐方案》实施顺序 1 要求建立的「Web -> desktop 字段矩阵」
（方案 :121「建立 Web -> desktop 字段矩阵和截图基线；标记已有字段、原生已绘、原生缺绘、
后端缺数、明确不做五类」）：把单点预测、实况分析、区域地图三个板块的 Web→macOS 展示缺口
逐项登记，标注五类归类，并给出实现批次划分。矩阵内容与归类完全来自三块缺口盘点结果；
「方案」指 [WEB_MACOS_DISPLAY_PARITY_PLAN.zh-CN.md](WEB_MACOS_DISPLAY_PARITY_PLAN.zh-CN.md)，
「审计」指 [MACOS_BACKEND_DISPLAY_AUDIT.zh-CN.md](MACOS_BACKEND_DISPLAY_AUDIT.zh-CN.md)，
「映射清单」指 [MACOS_BACKEND_DATA_DISPLAY.zh-CN.md](MACOS_BACKEND_DATA_DISPLAY.zh-CN.md)。
行号引用格式 `文件:行` 或「方案 :N」。

整理口径说明：方案与两份背景文档的被引行号已在整理时读取核对一致；`Web 证据` /
`macOS 证据` 中指向前端（`frontend/`）、Rust（`engine/`、`native/`）与 Swift
（`apps/macos/`）源码的行号沿用盘点结果，本文档未逐条重跑源码验证。

## 1. 概述

### 1.1 依据文档

| 文档 | 在本矩阵中的用途 |
| --- | --- |
| `docs/WEB_MACOS_DISPLAY_PARITY_PLAN.zh-CN.md` | 对齐范围决定（:8-18）、目标界面（:43-84）、原生图形规范（:86-95）、后端数据可用性（:97-117）、实施顺序（:119-127）、验收清单（:129-139）、非目标（:141-147）；五类归类出处 :121 |
| `docs/MACOS_BACKEND_DISPLAY_AUDIT.zh-CN.md` | 审计背景：点/实况/区域当前展示结论（:9-98）、「当前不能承诺的展示」（:114-138，含跨日界线实机未验收 :128-131） |
| `docs/MACOS_BACKEND_DATA_DISPLAY.zh-CN.md` | 字段映射背景：点预测映射表（:35-55）、不直接展示的内容及原因（:100-119） |

### 1.2 五类归类定义

| 归类 | 含义 | 处理口径 |
| --- | --- | --- |
| 已有字段 | 后端已返回且原生已有展示面，或仅欠基线/合同保障（如 point-1 覆盖表、regional-8 合同测试断言） | 有动作的只做保障性工作；其余保持现状 |
| 原生已绘 | Web 与 macOS 原生均已呈现且语义符合方案，保持现状 | 无对齐动作 |
| 原生缺绘 | Web 已有该展示面，且所需数据已由桌面后端返回（或属纯 UI 补齐范畴），macOS 原生端缺绘 | 扩展原生 UI，禁止在 SwiftUI 重算评分或猜值 |
| 后端缺数 | 桌面 Rust 合同未计算或显式置 Null/占位，UI 无真实数据可绘 | 先在后端立项计算并声明语义，UI 不占位、不猜值；占位值显式显示「未计算」（方案 :116-117） |
| 明确不做 | 方案「范围决定」「非目标」明确排除的展示面 | 保持现状，不作为对齐缺口 |

### 1.3 板块与分布

| 五类归类 | 单点预测 | 实况分析 | 区域地图 | 合计 |
| --- | ---: | ---: | ---: | ---: |
| 已有字段 | 1 | 6 | 2 | 9 |
| 原生缺绘 | 11 | 13 | 8 | 32 |
| 原生已绘 | 7 | 2 | 7 | 16 |
| 后端缺数 | 4 | 3 | 0 | 7 |
| 明确不做 | 1 | 2 | 4 | 7 |
| **合计** | **24** | **26** | **21** | **71** |

需要动手实现的核心是 32 项「原生缺绘」；「已有字段」中 point-1 与 regional-8 带保障性
动作，其余 7 项为保持现状；「后端缺数」7 项与「明确不做」7 项均不进入 UI 实现批次
（point-17 为映射文档口径澄清，见 3.5）。

## 2. 板块矩阵

### 2.1 单点预测（24 项）

| 编号 | 字段 / 展示项 | 归类 | Web 证据 | macOS 证据 | 对齐动作 |
| --- | --- | --- | --- | --- | --- |
| point-1 | 单点 Web→桌面字段矩阵基线 | 已有字段 | 方案 :101-102 要求先建逐字段覆盖表再扩展台账/Charts | `engine/src/point_core.rs:25-77`（steps[] 与 quality_components 全字段清单）；映射清单 :35-55 点预测映射表 | 按方案实施顺序 1 建立单点逐字段覆盖表，确认趋势/台账/拆解所需字段全部由 steps[]/quality_components 返回，UI 扩展不复制评分逻辑 |
| point-2 | 单点大气剖面图层（温度/RH/云量/云层状态/地形/照明） | 原生缺绘 | `frontend/src/lib/components/forecast/NationalCrossSection.svelte:36-56`（cloudFill 用 cloud_amount/presence/rh/fraction）、111-118（温度层线）、92-109（地形）、85-90（照明基准）、213-219（图例） | `apps/macos/Sources/FireSkyNative/Workbench.swift:864-882,902-925`（WorkCrossSectionView 只画 theoretical_targets 云底/云顶与 required_illumination_base_agl_m 三条线）；后端 fields 全量在 `engine/src/cross_section.rs:552-565` | 用已返回的 cross_section.fields 扩展 WorkCrossSectionView：温度层线、RH/云量色块、地形填充、云层状态描边，配分层图例与单位；缺测不解释成晴空 |
| point-3 | 单点评分拆解面板（分项调整+测量值+方向+不重复扣分语义） | 原生缺绘 | `frontend/src/lib/components/forecast/QualityBreakdown.svelte:47-55`（adjustments 五行）、108-129（分项行与上下文 cap）、132-137（几何遮挡已计入可烧量、AOD 修正一次等解释） | `apps/macos/Sources/FireSkyNative/Workbench.swift:434-508`（概览仅 10 张指标卡，无拆解面板）；后端 coverage_base_score/raw_quality_index/duration_factor/duration_adjustment/incoming_aod_adjustment/observer_aod_adjustment/solar_incoming_block_risk_at_best_cloud 等全在 `engine/src/point_core.rs:47-77` | 新增只读「评分组成」分项表/图（Swift Charts 或分项表），渲染后端贡献值/测量值/方向并标明不重复扣分；禁止在 SwiftUI 重算（方案 :135） |
| point-4 | 趋势图多序列（可烧云占比/可烧比例/回程透过率 + 0–100% 轴语义） | 原生缺绘 | `frontend/src/lib/components/forecast/TrendChart.svelte:45-50`（四序列+虚线图例）、94-99（0–1 右轴映射）；字段为 steps[].burnable_cloud_coverage/forward_burnable_cloud_ratio/observer_return_transmission | `apps/macos/Sources/FireSkyNative/Workbench.swift:583-589` 与 ForecastComparison.swift:129-137（对比曲线仅取 instant_quality_index）；steps 字段已返回 `engine/src/point_core.rs:27-38` | WorkComparisonCurveView 补三条比例序列（右轴或对齐小图），沿用质量色/线型语义并补图例；缺测断线保持现实现 |
| point-5 | 独立 AOD550 趋势小图 | 原生缺绘 | `frontend/src/lib/components/forecast/TrendChart.svelte:274-305`（独立小图含范围轴与时次）、163-168（自适应域）、74-77（steps[].quality_aod550 缺测断点） | `apps/macos/Sources/FireSkyNative/Workbench.swift:555-565` + ForecastComparison.swift:38-45（仅 aerosol.aod550 数值卡与 GFS/EC 取值范围文本，无逐时曲线）；steps[].quality_aod550 已返回 `engine/src/point_core.rs:39` | 新增 AOD550 迷你图（Swift Charts，用 steps[].quality_aod550），标注取值范围、时次与来源；保留现有 aerosol 来源卡 |
| point-6 | 逐时台账缺列（压力族/云底顶/亮度颜色/可烧比例/回程透过率/入射阻挡） | 原生缺绘 | `frontend/src/lib/components/forecast/HourlyTable.svelte:37-49`（10 列）、19-25（云底顶用 ASL） | `apps/macos/Sources/FireSkyNative/Workbench.swift:687-737`（WorkLedgerView 仅 9 列，云层用 AGL 口径）；缺的列字段均在 `engine/src/point_core.rs:25-42`（brightness_color/forward_burnable_cloud_ratio/observer_return_transmission/solar_incoming_block_risk/dominant_burnable_pressure_family/base_top_asl） | WorkLedgerView 表头/行补列，横滚扩展 minWidth；补列时标明 ASL/AGL 口径差异，缺测显示 — |
| point-7 | 台账最佳时刻标记与当前行视觉标记 | 原生缺绘 | `frontend/src/lib/components/forecast/HourlyTable.svelte:54,59`（选中行高亮 + 「最佳」标记） | `apps/macos/Sources/FireSkyNative/Workbench.swift:705-737`（row 不接收 selectedTime/best_time_local，无任何行标记） | 给 WorkLedgerView 传 best_time_local 与共享选中时刻，加最佳星标与当前行高亮；与趋势/剖面共享本地选择时刻但不驱动地图（方案 :54-55） |
| point-8 | 趋势图选中时刻标记线/峰值最佳标记/事件时间轴条 | 原生缺绘 | `frontend/src/lib/components/forecast/TrendChart.svelte:242-244`（选中竖线）、245-259（可点圆点+aria）、315-331（时间轴条含 peak/active 态）、193-214（峰值/有效时次摘要条） | `apps/macos/Sources/FireSkyNative/Workbench.swift:583-616`（WorkComparisonCurveView 无选中标记、无峰值标记、无时间轴条） | 曲线图加选中时刻标记线与最佳时刻标记，可选补事件时间轴条；键盘/VoiceOver 可读（方案原生图形规范 :90-91） |
| point-9 | 诊断组结构化（云属代理 top3/相态/色彩诊断/演变趋势） | 原生缺绘 | `frontend/src/lib/components/forecast/MetricsGrid.svelte:152-208`（regime 信号、霞光颜色+可信度、云属 top3 候选+烧霞适配度、演变趋势中文标签） | `apps/macos/Sources/FireSkyNative/Workbench.swift:787-806,958-984`（diagnosticRow 输出原始值，cloud_genus_proxy 整对象经 ReportDiagnosticValue.text 拍平成 key=value，ForecastComparison.swift:22-24）；candidate_scores 后端已返回 `engine/src/characteristics.rs:310` | 把云属代理/色彩诊断/场景信号/演变趋势改为结构化行或小卡（中文标签+top3 百分比+不参与评分说明），沿用现有 sceneLabel 映射 |
| point-10 | 摘要补起报周期/模式来源说明/主导压力族标签 | 原生缺绘 | `frontend/src/lib/components/forecast/MetricsGrid.svelte:93-110`（时间上下文含「模型起报」）、`frontend/src/routes/(app)/forecast/+page.svelte:175-179` 与 QualityBreakdown.svelte:32-45（EC TCC+PL-RH 说明）、ScoreCard.svelte:74-83（主导压力族标签） | `apps/macos/Sources/FireSkyNative/Workbench.swift:380-432`（hero 无起报周期与来源说明，cycle 仅在诊断页 771 行）；model_cycles 已返回 `engine/src/point_core.rs:963` | hero 摘要补起报周期 chip、模式来源一行说明与主导压力族标签；字段全部已返回，仅 UI 补齐 |
| point-11 | 剖面元信息补全（剖面范围/相对时刻/有效时次/目标云诊断） | 原生缺绘 | `frontend/src/lib/components/forecast/NationalCrossSection.svelte:146-157`（方位/太阳高度/剖面范围/相对时刻/valid_time）、221-231（theoretical_targets status 诊断列表） | `apps/macos/Sources/FireSkyNative/Workbench.swift:893-897`（仅太阳高度/方位/采样三 chip）；status/status_reason 后端已返回 `engine/src/cross_section.rs:509-523` | 剖面标题区补 analysis_radius_km、offset_minutes、valid_time_local，并按 status 汇总目标云候选诊断；仅 UI 补齐 |
| point-12 | 剖面随选中时次刷新（任意时刻选择器） | 原生缺绘 | `frontend/src/routes/(app)/forecast/+page.svelte:392-414`（loadCrossSection 按 step 用 cross_section_request 逐时刷新） | 原盘点显示 forecast RPC 未透传 offset_minutes、cross_section_request 为 Null；2026-11-13 复核 `engine/src/desktop_profiles.rs::Query/query` 已支持 valid_time_utc，forecast 装配也已读取 offset_minutes | 低优先级：先复用现有 profile_query 的精确时刻合同并核对 UI 与冻结源有效期，确认实际缺口后再考虑扩展 RPC；不阻塞其他对齐 |
| point-13 | 单点预测地图（云场/可烧场预测地图+时间条） | 明确不做 | 方案 :11（范围决定：单点预测不渲染预测地图）、:143（非目标：单点预测地图渲染） | 后端口径：`engine/src/point_core.rs:1069-1070`（map_token=""/map_times=[]）、1078-1079（map_capability.available=false，reason「单点结果不携带地图时间轴」）；macOS 已显示该原因 Workbench.swift:839-845，位置选点地图保留 Workbench.swift:212,45-53 | 不做；保留现有 map_capability 原因提示与位置选点地图，不新增云场/可烧场预测地图 |
| point-14 | 目标云边界位置敏感度面板（boundary_uncertainty） | 后端缺数 | `frontend/src/lib/components/forecast/MetricsGrid.svelte:213-258`（边界范围/跨度/判断影响/开始时刻影响） | 后端口径：GFS 桌面合同显式置 Null `engine/src/point_core.rs:1065`，EC 同样为 Null（`engine/src/ecmwf_point.rs:238,297`）；nowcast 路径才有实现（`engine/src/desktop_nowcast_assessment.rs:5681-5683`） | 桌面点预测未计算该字段，UI 不展示；如产品需要先在 Rust 点路径立项计算再补面板，不得占位/猜值 |
| point-15 | GFS 云相标签与冰含量（dominant_burnable_cloud_phase.ice_fraction） | 后端缺数 | `frontend/src/lib/components/forecast/MetricsGrid.svelte:167-176`（相态中文标签+冰含量占比） | 后端口径：GFS 桌面点预测 add_geometry 固定写 {label:unknown, ice_fraction:null, source:not-resolved-for-native-burnable-target}（`engine/src/point_core.rs:903-909,934-935`）；EC 路径有 phase_proxy（`engine/src/desktop_runtime.rs:1078`） | GFS 点路径后端不解析相态，UI 显示「未解析」即可；EC 可用已有 phase_proxy 分模式展示；如需补 GFS 相态需后端独立定义口径 |
| point-16 | 跨模式分歧 model_disagreement_score | 后端缺数 | 审计 :42（当前为占位零，不用作主卡指标）；映射清单 :115 | `apps/macos/Sources/FireSkyNative/Workbench.swift:779`（诊断页已标注「当前后端未计算，GFS/EC 请并列比较」）；字段为占位透传 `engine/src/point_core.rs:968` | 维持现状：不展示分歧数值，只用 GFS/EC 并列比较；后端未计算前不改动 |
| point-17 | 完整报告入口（html_report_path/html_report_url） | 后端缺数 | `frontend/src/routes/(app)/forecast/+page.svelte:784-788`（Web 有「打开完整诊断报告」按钮）；映射清单 :43 称诊断页有完整报告入口 | 后端口径：桌面点报告显式置 Null/空串（`engine/src/point_core.rs:1063,1071`；`engine/src/ecmwf_point.rs:237,308`），Workbench.swift:833-838 的「完整报告」按钮因此永不出现——映射文档与代码口径不一致 | 提请澄清：桌面合同若继续不生成 HTML 报告，应从映射清单移除/标注「桌面不适用」；若要补齐需后端先产出报告文件 |
| point-18 | GFS/EC 峰值摘要（质量/档位/最佳时刻/推荐窗口/状态） | 原生已绘 | `frontend/src/lib/components/forecast/ScoreCard.svelte:87-102` + MetricsGrid.svelte:93-110（质量分/档位/最佳时刻/窗口） | `apps/macos/Sources/FireSkyNative/Workbench.swift:380-432`（WorkHeroView 双模式峰值+peak_quality_level 414 行+最佳时刻+推荐窗口 windowLabel 1753-1756+未完成错误态 425-428） | 保留；峰值已配 peak_quality_level，符合审计 :41 要求，无动作 |
| point-19 | 评分要素指标卡（3 组 10 卡含 AOD 来源/范围卡） | 原生已绘 | `frontend/src/lib/components/forecast/MetricsGrid.svelte:260-275`（指标卡）+ QualityBreakdown.svelte:89-106（quality_components 网格） | `apps/macos/Sources/FireSkyNative/Workbench.swift:434-445`（metricSpec 三组）、447-508（双模式指标卡）、555-565 与 ForecastComparison.swift:29-66（AOD 卡含来源与取值范围 caption） | 保留现状；方案目标界面 :50 的评分要素分组已由「云况与色彩/大气光学/时效与可靠性」分组覆盖 |
| point-20 | 质量趋势曲线 0–2.5 固定轴/GFS-EC 线型区分/缺测断线 | 原生已绘 | 方案 :57-60（固定质量轴、模型线型、缺测断线）+ TrendChart.svelte:51,102-115 | `apps/macos/Sources/FireSkyNative/Workbench.swift:591`（chartYScale 0...2.5）、587（EC 虚线 [4,3]）、576-581（图例）+ ForecastComparison.swift:132-134（无效时次分段断线） | 保留；线色/线型与缺测语义已符合原生图形规范，无动作 |
| point-21 | 台账横向滚动与缺测态 | 原生已绘 | `frontend/src/lib/components/forecast/HourlyTable.svelte:35-36`（overflow-x-auto）、15-17,66-75（缺测显示 —） | `apps/macos/Sources/FireSkyNative/Workbench.swift:672,682`（横向 ScrollView，minWidth 760）、713-731（无效行显示 —/缺测）与 721（可读性 accessibilityLabel） | 保留；方案台账目标中的横滚与缺测态已满足，仅缺列与行标记（见上） |
| point-22 | 剖面理论目标云底/云顶/照明基准曲线 | 原生已绘 | `frontend/src/lib/components/forecast/NationalCrossSection.svelte:85-90`（照明基准）+ 审计 :39 | `apps/macos/Sources/FireSkyNative/Workbench.swift:864-882,902-926`（WorkCrossSectionView 画 theoretical_targets 云底/云顶与 required_illumination_base_agl_m，带线型区分图例与空态文案） | 保留；作为剖面图层扩展（point-2）的既有基准线继续使用 |
| point-23 | map_capability 不可用原因提示与位置选点地图保留 | 原生已绘 | `frontend/src/routes/(app)/forecast/+page.svelte:751-755`（Web 亦显示 map_capability.reason） | `apps/macos/Sources/FireSkyNative/Workbench.swift:839-845`（诊断页 Label 显示 reason）+ 212,45-53（mini/大地图仅用于选点） | 保留；符合「点结果没有地图覆盖层、位置选点地图仍可用」的验收项（方案 :132） |
| point-24 | 位置/参数栏、GFS/EC 比较与结果分页保留 | 原生已绘 | 方案 :47（保留现有原生位置/参数栏、结果比较和结果分页） | `apps/macos/Sources/FireSkyNative/Workbench.swift:126-178`（workbench 布局）、181-302（位置/参数栏）、528-553（概览/大气剖面/诊断与时间窗/逐时台账四标签）、455-461（GFS/EC 切换） | 保留；无需改动 |

### 2.2 实况分析（26 项）

| 编号 | 字段 / 展示项 | 归类 | Web 证据 | macOS 证据 | 对齐动作 |
| --- | --- | --- | --- | --- | --- |
| nowcast-1 | 卫星剖面·压力层标注 | 原生缺绘 | `frontend/src/lib/components/forecast/SatelliteVerticalProfile.svelte:114-124` —— Web 在高度网格外按 pressure_levels_hpa 逐层画行线并标 hPa 标签 | `apps/macos/Sources/FireSkyNative/Workbench.swift:1498-1505` —— Swift Charts 热图只有 x=距离km、y=高度km 轴；后端已返回 pressure_levels_hpa（`engine/src/desktop_nowcast_adjustment.rs:542`）但未画压力层 | 在剖面图增加压力层行线/hPa 标注并与高度轴对齐，数据用已返回的 pressure_levels_hpa+height_m |
| nowcast-2 | 卫星剖面·覆盖状态与采样统计头 | 原生缺绘 | SatelliteVerticalProfile.svelte:99-109 —— 头部显示时次、状态（完整覆盖/部分未知/不可用）、采样点 usable/sample、覆盖率、压力层数 | `Workbench.swift:1493` —— 剖面页只有方位 caption；status/sample_count/usable_sample_count/coverage/unknown_sample_count 后端均已返回（`engine/src/desktop_nowcast_adjustment.rs:532-540`） | 剖面页头部补状态、采样点与覆盖率统计，数据全部来自已有 profile 字段 |
| nowcast-3 | 卫星剖面·来源图例与色标/缺测语义 | 原生缺绘 | SatelliteVerticalProfile.svelte:38-44,136,142,149-159 —— retrieved 用青色透明度映射、unknown 琥珀虚线框+样本 X 标、页脚标明 HSD B07/B13/B15 posterior 代理/不发布 COT/缺测不解释为晴空/GFS vertical prior 说明 | `Workbench.swift:1502,1507-1515` —— native 用 warm 橙色透明度、unknown 画灰色块，图例只有「缺测+0-100%渐变」；bands/source 字段后端已有（`engine/src/desktop_nowcast_adjustment.rs:533,535`） | 按方案 :30「核对颜色、source caption」：补 retrieved/unknown 来源图例与不解释为晴空说明，unknown 改虚线/警示样式，核对色标语义与 Web 一致 |
| nowcast-4 | 实况摘要·光学观测组（云幕/纹理/地平线通透） | 原生缺绘 | `frontend/src/routes/(app)/now/+page.svelte:604-606` —— Web 显示 cloud_blanket_score/cloud_texture_score/horizon_clearance_score | `apps/macos/Sources/FireSkyNative/NowcastPresentation.swift:17-27` —— 指标卡清单无此三项，全 apps/macos grep 无引用；后端已返回（`engine/src/desktop_nowcast_adjustment.rs:1026-1029`） | 概览「卫星云况」组补三张光学观测卡，后端字段已有，仅 UI 补齐 |
| nowcast-5 | 实况趋势·比例序列（可烧云占比/可烧比例） | 原生缺绘 | `frontend/src/lib/components/forecast/TrendChart.svelte:60-64,93-97` —— Web 趋势图画 quality/burnable_cloud_coverage/forward_burnable_cloud_ratio/observer_return_transmission 四序列 | `Workbench.swift:632-638` + ForecastComparison.swift:112-137 —— WorkCurveView 只画单一质量序列；steps 已含 burnable_cloud_coverage/forward_burnable_cloud_ratio（`engine/src/nowcast_runtime.rs:773,778`） | 实况趋势图补可烧云占比与可烧比例序列（0-100% 右轴或独立小图），线型/缺测断线对齐方案图形规范 |
| nowcast-6 | 实况 AOD550 独立小图 | 原生缺绘 | TrendChart.svelte:117-135,163-168 —— Web 独立 AOD 子图，缺测断线、自适应量程 | `Workbench.swift:621-667` —— 实况无 AOD 图；steps[].quality_aod550 已返回（`engine/src/nowcast_runtime.rs:781`） | 实况概览趋势下新增独立 AOD550 小图（取值范围+时次+来源），不与质量轴混用 |
| nowcast-7 | 雷达有效风险组（区域对流风险/光路阻塞风险） | 原生缺绘 | `now/+page.svelte:626-627` —— Web 显示 effective_convective_risk 与 effective_path_blocking_risk | `NowcastPresentation.swift:17-27` 只有 convective_score_ceiling；effective_* 全 apps/macos 无引用；后端已返回（`engine/src/nowcast_runtime.rs:832-833`） | 雷达/约束分组补两个有效风险值，区分「雷达有效值」与「评分顶棚」语义 |
| nowcast-8 | 来源时效标注（实况源 source + 通道 image_channel） | 原生缺绘 | `now/+page.svelte:601` —— Web 显示「实况源：{source} · 通道 {image_channel}」 | `Workbench.swift:1354,1375` —— Hero 硬编码「卫星实况」「GFS 先验 · CAMS 光学」，不读后端 source/image_channel（`engine/src/desktop_nowcast_adjustment.rs:991,994`） | Hero/摘要改读 nowcast.source 与 image_channel 展示真实来源与通道，替换硬编码文案 |
| nowcast-9 | 几何挡光采样点数 | 原生缺绘 | `now/+page.svelte:607-608` —— Web 在几何挡光值后标注「(N点)」geometric_path_samples | `NowcastPresentation.swift:23` 只显示 risk 值；geometric_path_samples 后端已返回（`engine/src/nowcast_runtime.rs:795`） | 光路阻塞风险卡补采样点数角标，说明该值基于 N 个光路样本 |
| nowcast-10 | 实况台账补列（亮度颜色/可烧比例/太阳入射阻挡） | 原生缺绘 | `frontend/src/lib/components/forecast/HourlyTable.svelte:39-50,70-75` —— Web 台账含亮度颜色/可烧比例/太阳入射阻挡列 | `Workbench.swift:1565-1572` —— 实况台账仅 7 列（时间/质量/档位/可烧/可烧云量/AOD550/太阳仰角）；三字段 steps 均已返回（`engine/src/nowcast_runtime.rs:778,779,785`） | 实况台账横向加三列，沿用缺测「—」与单位口径，不需要新后端数据 |
| nowcast-11 | 雷达组并入概览分组 | 原生缺绘 | `now/+page.svelte:620-632` —— Web 将雷达行直接放在实况摘要条内 | `Workbench.swift:1392,1456-1471` —— 方案 :29 要求按「卫星云况/运动采样/光学与雷达」分组，native 概览三组无雷达，雷达指标只在「诊断」标签页 | 概览增加/并入「光学与雷达」分组并带雷达观测时效，避免用户须切到诊断页才能看到雷达状态 |
| nowcast-12 | 实况太阳光路模式剖面（steps[].cross_section） | 原生缺绘 | `now/+page.svelte:313-329,656-660` + NationalCrossSection.svelte:39-159 —— Web 实况页按步绘制温湿/云/地形/照明垂直剖面 | `engine/src/desktop_nowcast_assessment.rs:1965,2014,4173-4215` —— 桌面实况每个 step 实际携带 cross_section（distance_km/cloud_base_m/cloud_top_m/required_illumination_height_m + pressure/RH/温度/云分数 levels）；`Workbench.swift:1482-1557` 实况剖面页只画卫星后验热图。盘点时已运行 `cargo test --manifest-path engine/Cargo.toml --lib model_detail_drops_heavy_lists` 通过（1 passed），证实 model detail 线格式保留 cross_section | 方案未单列此面（:30 只锁卫星剖面）：低优先，用已有 steps[].cross_section.profile 在实况剖面页增补温湿/云/照明图层或先标注「实况含冻结光路剖面」；注意剖面样本无地形字段，地形层不可绘需标注 |
| nowcast-13 | 实况评分组成（quality_components 分项） | 原生缺绘 | `now/+page.svelte:648-654` + QualityBreakdown.svelte:6 —— Web 实况页「气象参数与评分明细」渲染 quality_components | `Workbench.swift:1398-1409` 概览只有指标卡，1758-1774 的 metricValue 分项读取仅用于点预测卡片；report.quality_components 已返回（`engine/src/nowcast_runtime.rs:956`） | 方案未单列实况评分拆解行：低优先，在实况诊断或概览补只读分项表（不重算），或维持现状并在方案里确认排除 |
| nowcast-14 | 多光谱云型概率组（云类/置信度/高积云/高层云/卷云/冰云） | 后端缺数 | `now/+page.svelte:610-619` —— Web 显示 cloud_type_label、cloud_type_confidence 及四种云概率 | engine/src 全量 grep 无 cloud_type_label/altocumulus/altostratus/cirrus/ice_cloud（`engine/src/desktop_nowcast_adjustment.rs:986-1046` 的 AdjustmentOutput 不含）；`NowcastPresentation.swift:40-43` 有读取分支但 metrics 从未引用。Web 值由 Python `src/firesky/satellite.py` 计算，桌面 Rust 实况不产生 | 后端在 Rust 桌面实况合同中计算并声明云类语义后才可展示（方案 :116-117）；未计算前 UI 显式「未计算」，且桌面 bands 恒为 HSD B07/B13/B15，不得把 HSD 代理标成 HCAI 云类真值（方案 :66） |
| nowcast-15 | 实况台账·主导压力族/云底–云顶列 | 后端缺数 | HourlyTable.svelte:41-42,69 —— Web 台账有「主导可烧压力族」「加权云底–云顶」列 | engine 三个 nowcast 文件 grep 无 dominant_burnable_pressure_family/云底云顶字段（AssessmentModelStepOutput 字段清单 `engine/src/desktop_nowcast_assessment.rs:1944-1987` 不含）；审计 :71 明确「实况逐时结果没有与点预测同等的模式云底/云顶字段」 | 维持不虚构（方案 :18：无真实数据显示缺测原因）；若要对齐需先在实况评估 DTO 声明逐时主导云柱字段，否则台账不画这两列 |
| nowcast-16 | 实况回程透过率序列 | 后端缺数 | TrendChart.svelte:64,97 —— Web 趋势画 observer_return_transmission 序列 | `engine/src/nowcast_runtime.rs:786` —— 桌面实况 steps 显式置 observer_return_transmission = Null（不伪造） | 后端声明实况口径的回程透射来源（HSD/CAMS 代理）后才补序列；UI 侧按缺测断线处理，绝不连 0（方案 :59） |
| nowcast-17 | 实况云场地图 | 明确不做 | `now/+page.svelte:287-295,530-581,583-590` —— Web 有底图/云场字段/平滑格点/可烧挡光图层/时间条完整地图 | `engine/src/nowcast_runtime.rs:967-968` —— 后端口径 map_token:null、map_times:[]、map_capability={available:false, reason:"native-nowcast-map-publication-not-requested"}；steps[].map_cells 仅 include_map_cells=true 才填充（`engine/src/desktop_nowcast_assessment.rs:5697-5701`），native/apps/desktop_rpc.py 无任何 include_map_cells 调用方（grep 为空）；macOS 已显示不可用原因（`Workbench.swift:1472-1477`） | 方案 :112-113：本方案不做，需单独提案数据范围/快照容量/地图令牌/渲染合同（合同雏形 MapCloudCellOutput 已存在于 `desktop_nowcast_assessment.rs:5365-5384`），不阻塞图表对齐 |
| nowcast-18 | 地图与图表/剖面联动 | 明确不做 | `now/+page.svelte:337-342,590` —— Web 时间条换帧会联动刷新剖面（handleFrameLoaded→loadCrossSection） | `Workbench.swift:1398-1417` —— native 概览/台账/剖面各标签状态独立，无联动实现 | 方案非目标 :144 与范围决定 :12-13、:17 明确不做互相驱动；保持现状 |
| nowcast-19 | 卫星观测时间 | 已有字段 | `frontend/src/lib/components/forecast/MetricsGrid.svelte:33,109` + api/forecast.ts:313 —— Web 在指标网格显示「卫星观测」时次并强校验 image_valid_time_utc | `Workbench.swift:1374,1380-1384` —— Hero 显示「卫星观测 HH:mm」；后端 image_valid_time_utc（`engine/src/desktop_nowcast_adjustment.rs:992`）与 cycle_time_utc 复用同一时次（`engine/src/nowcast_runtime.rs:947`） | 无动作，保持现状 |
| nowcast-20 | 近/远场云比 | 已有字段 | `now/+page.svelte:602-603` —— Web 摘要条显示近场/远场云比 | `NowcastPresentation.swift:18-19` + `Workbench.swift:1392,1403` —— 「卫星云况」组前两张卡；后端 near/far_cloud_ratio（`engine/src/desktop_nowcast_adjustment.rs:996-997`） | 无动作，保持现状 |
| nowcast-21 | 运动相关性/帧龄/外推时长 | 已有字段 | 方案 :66 —— 方案目标字段清单列出；Web 页面未渲染（仅合同校验 `frontend/src/lib/api/forecast.ts:234,330`） | `Workbench.swift:1458-1460` —— 诊断页「云体运动与雷达」逐行显示三字段；后端已返回（`engine/src/desktop_nowcast_adjustment.rs:1005,1011,1013-1016`） | 无动作；桌面已超出 Web 的显示面，注意保留「仅供诊断」语义 |
| nowcast-22 | 雷达回波/降水/可靠度 | 已有字段 | `now/+page.svelte:620-628` —— Web 雷达行显示最大回波/本地与光路降水/可靠度 | `Workbench.swift:1461-1464` —— 诊断页同组字段；后端 radar_*（`engine/src/nowcast_runtime.rs:821-837`） | 无动作，保持现状 |
| nowcast-23 | 几何阻挡/采样覆盖率/实况支持度 | 已有字段 | `now/+page.svelte:607,629-630` —— Web 显示几何挡光与采样覆盖率 | `NowcastPresentation.swift:23-25` —— 概览「运动与约束/采样与光学」组卡片；后端 geometric_path_blocking_risk（`engine/src/nowcast_runtime.rs:796`）、calibrated_sample_coverage_ratio（`engine/src/desktop_nowcast_adjustment.rs:1045`）、confidence | 无动作，保持现状 |
| nowcast-24 | 峰值质量/档位/窗口/最佳时刻 | 已有字段 | `now/+page.svelte:647`（ScoreCard）—— Web 摘要卡展示质量/档位/最佳时刻 | `Workbench.swift:1360-1373` —— Hero 显示峰值质量/2.5、档位、推荐窗口（windowLabel 1753-1756）、最佳时刻；后端 peak_quality_index/level/timing_window（`engine/src/nowcast_runtime.rs:948,954-955`） | 无动作，保持现状 |
| nowcast-25 | 卫星后验概率热图+冻结剖面查询 | 原生已绘 | SatelliteVerticalProfile.svelte:112-147 —— Web 距离×高度/压力层概率热图 | `Workbench.swift:1498-1515`（Swift Charts RectangleMark 热图+缺测图例）与 1521-1555（按已发布步骤的冻结剖面查询，Web 无此能力）；后端 profile JSON（`engine/src/desktop_nowcast_adjustment.rs:530-544`） | 保留现状；视觉核对项由 nowcast-1～3 承接 |
| nowcast-26 | HSD 代理口径（不冒充 HCAI） | 原生已绘 | SatelliteVerticalProfile.svelte:91-92,150-155 —— Web 以 bands 是否含 CLOUD_MASK 区分 HCAI/HSD 来源图例 | `engine/src/desktop_nowcast_adjustment.rs:535` —— 桌面 bands 恒为 ["B07","B13","B15"]，caption「卫星证据 / GFS 高度先验」（`NowcastPresentation.swift:74-77`），未出现 HCAI 标注 | 保持现状——方案 :66 要求不得将 HSD 代理标为 HCAI 观测真值，桌面天然满足；补 UI 图例时沿用该口径（见 nowcast-3） |

### 2.3 区域地图（21 项）

| 编号 | 字段 / 展示项 | 归类 | Web 证据 | macOS 证据 | 对齐动作 |
| --- | --- | --- | --- | --- | --- |
| regional-1 | 网格弹窗·峰值/事件质量与档位 | 原生缺绘 | `frontend/src/lib/components/map/BurnOutlookMap.svelte:95-110` —— 弹窗头部档位徽标+双分数卡（峰值质量/2.5 与 事件窗口质量/2.5） | `apps/macos/Sources/FireSkyNative/Workbench.swift:1222-1235` —— 网格详情每模式只显示一个分数，档位仅作为 .help 提示（Workbench.swift:1235）；RegionalForecast.swift:83-87 把 quality_index 仅作 peak 缺失时的回退，事件质量与档位未独立解析展示 | 扩展 RegionalCell 独立解析 peak/事件两档分数与 peak_quality_level（points[] 顶层已有 `engine/src/frames.rs:997-1000`），在网格详情面板并列显示，缺测显式标注不重算 |
| regional-2 | 网格弹窗·置信度 | 原生缺绘 | BurnOutlookMap.svelte:127 —— 弹窗显示「置信度 Math.round(confidence_score*100)%」 | `apps/macos/Sources/FireSkyNative/RegionalForecast.swift:16-25` —— RegionalCell 无 confidence 字段，全文件 grep 无 confidence 解析；桌面 points[] 顶层已返回 confidence_score/confidence_kind（`engine/src/frames.rs:1010`） | RegionalCell 增加 confidence_score 并在网格详情面板按百分比展示，缺值时显示未提供而非 0% |
| regional-3 | 网格弹窗·可烧覆盖/持续时长/AOD/入射观测修正 | 原生缺绘 | BurnOutlookMap.svelte:121-125 —— 弹窗明细行：有效可烧云%、可烧时长分钟、质量 AOD、入射修正±.3f、观测修正±.3f | `apps/macos/Sources/FireSkyNative/RegionalForecast.swift:81-89` —— 只读 peak/quality、level、best_time；grep 证实无 burnable/duration/aod 解析；数据已在桌面 points[].quality_components（`engine/src/frames.rs:1063` 组装，`native/src/quality.rs:62,72,99,106,120` 定义键名） | 在 Swift 解析 quality_components 的 burnable_cloud_coverage_effective/duration_minutes/quality_aod550/incoming_aod_adjustment/observer_aod_adjustment 并入弹窗明细，缺键显示「未计算」（方案 :33 不重算/猜值） |
| regional-4 | 网格弹窗·主导可烧云族 | 原生缺绘 | BurnOutlookMap.svelte:114-120 —— 弹窗「主导可烧压力族」高/中/低标签，null 时显示「无可烧目标云」；采样点 tooltip 亦含（BurnOutlookMap.svelte:393） | `apps/macos/Sources/FireSkyNative/RegionalForecast.swift:16-25` —— RegionalCell 无云族字段；桌面 points[] 顶层已返回 dominant_burnable_pressure_family（`engine/src/frames.rs:1001`，无效网格为 null） | RegionalCell 解析 dominant_burnable_pressure_family，在网格详情面板显示高/中/低云族标签，null 显示「无可烧目标云」 |
| regional-5 | 点诊断入口（区域→单点预测） | 原生缺绘 | `frontend/src/routes/(app)/outlook/+page.svelte:319-332` 与 BurnOutlookMap.svelte:130-136 —— 弹窗「打开单点诊断」链接，携带 lat/lon/name/date/phenomenon/model/elevation/cycle 跳转 /forecast | `apps/macos/Sources/FireSkyNative/ForecastMap.swift:110-121` —— 区域页点击只打开网格详情面板；Store.swift:302-310 selectPoint 只改坐标不切换页面，区域侧无跳转单点入口（grep 无单点导航） | 在网格详情面板加「在单点模式打开」按钮，用所选格坐标/模式/日期/现象预填单点预测（属方案 :32-33 明确保留的入口；不违反方案 :80 的图-表联动禁令，因这是用户发起的模式切换） |
| regional-6 | 采样格点开关与逐点标注 | 原生缺绘 | `outlook/+page.svelte:503-507` 采样开关 + BurnOutlookMap.svelte:357-400 —— 上限 2000 的采样圆点及其 tooltip（档位·质量·云族·高/中/低云%） | `apps/macos/Sources/FireSkyNative/Workbench.swift:1035-1037` —— 只有「显示网格」线框开关；ForecastMap.swift grep 证实区域地图无逐点 annotation/marker | 为区域地图增加可选采样格点叠加层（复用 points[]，上限截断），tooltip 显示档位/质量/云族，与网格线开关并存 |
| regional-7 | 区域网格剖面图（温度/RH/云层/地形/照明） | 原生缺绘 | 方案 :123-124（实施顺序 3）要求用 cross_section.fields 补齐单点/区域温度、RH、云层、地形和日照剖面；Web 区域剖面经由 national cross-section 合同提供字段 | `apps/macos/Sources/FireSkyNative/RegionalProfileView.swift:59-78` —— ready 态只显示时间/太阳高度/方位/样本计数等摘要指标，不绘制任何剖面图层 | 用 profile_query 返回的 fields（temperature_c、cloud_fraction_percent 等已在 RegionalProfileView.swift:71-72 读取）绘制距离×高度剖面图层与图例，先区域后单点共用图层组件 |
| regional-8 | 区域 points[] 弹窗字段合同断言 | 已有字段 | `frontend/src/lib/api/types.ts:692-720` —— Web RegionalForecastPoint 顶层扁平字段即弹窗数据面；桌面 points[] 同数据嵌在 quality_components（`engine/src/frames.rs:1063`） | `tests/test_rust_runtime_forecast.py:156` —— 桌面级只断言 points 非空与 frame_data.frames 非空；无弹窗字段断言。工件级深度比对在 `tests/test_rust_frames.py:78-97`（check_payload 逐键比较含 results） | 在桌面级合同测试补断言 points[] 顶层（peak/事件质量、档位、置信度、云族）与 quality_components（可烧覆盖、持续、AOD、修正）键存在，防 Swift 解析依赖字段被静默移除 |
| regional-9 | 图例中间刻度 | 原生缺绘 | `outlook/+page.svelte:516-523` —— 图例标 0/0.5/1.0/1.5/2.0/2.5 六档刻度 | `apps/macos/Sources/FireSkyNative/Workbench.swift:1191-1206` —— 图例只有「0」与「2.5」两端点加缺测虚线块 | 在区域图例补 0.5/1.0/1.5/2.0 中间刻度（色带本身已按 RegionalPalette 固定映射，无渲染改动） |
| regional-10 | 覆盖边界常显 | 原生已绘 | Web 无覆盖边界线，仅 fitBounds（BurnOutlookMap.svelte:422-434）；方案 :78 要求展示覆盖边界/间距/统计/图例 | `apps/macos/Sources/FireSkyNative/RegionalMapLayers.swift:63-90` 画覆盖外框+网格线（外框仅在 cells 为空或 showGrid 时，:90）；fitRegion 支持跨线弧段（:97-150）；网格间距标签 `Workbench.swift:1163-1167` | 小改：网格关闭且有数据时也保留覆盖外框描边（现条件 RegionalMapLayers.swift:90），其余覆盖/间距/统计展示已达标不动 |
| regional-11 | 质量色带 0–2.5 固定映射 | 原生已绘 | `frontend/src/lib/map/cloud-render.ts:37-54` —— burnColor 五段 stops，Web 场渲染按 /2.5 归一 | `apps/macos/Sources/FireSkyNative/RegionalMapLayers.swift:11-25` —— RegionalPalette.fill 同五段 stops、同一 alpha 公式（注释自证对齐），固定 /2.5 不随结果拉伸，符合方案 :92-93 | 保持现状；注意 Web 图例 CSS 渐变（outlook/+page.svelte:607）与 Web 场渲染色不一致属 Web 侧自身问题，非桌面缺口 |
| regional-12 | 缺测网格样式与统计隔离 | 原生已绘 | BurnOutlookMap.svelte:32-36 —— 近零质量 alpha 12 近似透明；Web 无独立缺测图形 | `apps/macos/Sources/FireSkyNative/ForecastMap.swift:232-236` —— 缺测格灰虚线空缺态；RegionalForecast.swift:145 缺测不进 ranked 排行；`Workbench.swift:1133-1135` 有效/高值/缺测统计 | 保持现状，符合方案 :77「缺测保持透明/斜线或空缺态、不进入有效排行」 |
| regional-13 | GFS/EC 并排对照 | 原生已绘 | `outlook/+page.svelte:128-131,172-183` —— Web 为单选模式（gfs/ec 二选一），无双图对照 | `apps/macos/Sources/FireSkyNative/Workbench.swift:1030-1032,1045-1049` —— GFS/EC/双图对照三段切换，compare 时双地图并排+双摘要行（:1019-1029）；方案 :75 要求「保持现有双模式对照」 | 保持现状，Web 未有的双图对照是桌面增强，不回退 |
| regional-14 | 网格/城市排行与搜索定位 | 原生已绘 | Web outlook 页未渲染 city_ranking（outlook/+page.svelte 全文无 city_ranking 引用） | `apps/macos/Sources/FireSkyNative/Workbench.swift:1253-1318` —— 网格/城市双排行+搜索框；RegionalForecast.swift:145-150 排序；focusRegionalCell 定位选中格（Workbench.swift:1261,1295） | 保持现状，符合方案 :79「支持网格/城市排名筛选，不能以列表代替地图渲染」（地图已真实渲染） |
| regional-15 | 点击网格详情面板+剖面查询 | 原生已绘 | BurnOutlookMap.svelte:402-420 —— 点击取最近格弹浮动 popup；Web 区域侧无剖面查询 | `apps/macos/Sources/FireSkyNative/ForecastMap.swift:110-121,177-185` —— 点击命中格（0.25° 桶+跨线别名，RegionalForecast.swift:159-180）加选中描边并开详情面板；`Workbench.swift:1226-1234` 读取 GFS/EC 冻结剖面（Store.swift:850 queryRegionalProfile） | 保持现状；方案 :79「点击网格打开原生详情面板」已满足，弹窗字段缺口另列（见上） |
| regional-16 | 跨 ±180° 网格连续无错缝 | 原生已绘 | 方案 :78 要求跨 ±180° 连续无错缝；非目标 :147 声明跨日界线地图未验收 | `apps/macos/Sources/FireSkyNative/RegionalMapLayers.swift:70-88` 双经度段绘制、:179-181 关抗锯齿消接缝、RegionalForecast.swift:124-139 边界别名命中；审计 :128-131 明确实机显示仍未验收 | 保持实现，列入实机验收项（真实双侧下载+连续地图显示），不阻塞本板块展示对齐 |
| regional-17 | frame_data 地图帧数据 | 已有字段 | `frontend/src/lib/api/types.ts:919` —— Web 原生区域载荷声明 frame_data；但 Web 渲染同样用 points[]（BurnOutlookMap.svelte:9,266-271） | `engine/src/desktop_runtime.rs:1372` 返回 frame_data=document["frames"]（`engine/src/frames.rs:759` 版本 103，含逐时量化帧）；grep 证实 apps/macos 无 frame_data 引用——macOS 直接用 points[]（RegionalForecast.swift:81-89）构建 0.25° 多边形（RegionalMapLayers.swift:45-92） | 保持现状：静态质量场用 points[] 渲染与 Web 等价且更轻；frame_data 留作未来时序帧/动画能力，不作为本板块展示缺口 |
| regional-18 | 数据来源/溯源卡（CAMS 起报、时次、stale） | 明确不做 | `outlook/+page.svelte:455-478` —— Web 区域页有数据来源卡（起报/时次/CAMS 时次/上一份快照 stale） | `engine/src/desktop_runtime.rs:1362-1381` —— 桌面区域结果无 data_provenance 键（data_provenance 仅单点结果有，desktop_runtime.rs:1082-1086）；方案 :32-33 的 Web 可见内容列未含该卡，且映射清单 :100-108 规定溯源字段放诊断不放主界面 | 不做：方案区域地图/区域详情行未列入该展示面；若日后需要，须先在桌面区域结果扩展版本化 provenance 块（现仅 cycle_time_utc 可显示，Workbench.swift:1175-1178 已显示） |
| regional-19 | 自定义区域时区/名称输入 | 明确不做 | `outlook/+page.svelte:437-441,238-242` —— Web 自定义区域含名称与 custom_timezone_name 输入 | `apps/macos/Sources/FireSkyNative/Workbench.swift:1092-1105` —— 桌面自定义范围只有西/东/南/北界，:1100 注明「各网格时区按经纬度自动计算」 | 不做：方案 :32 区域地图行未要求对齐时区/名称输入，且桌面逐格自动时区语义比 Web 单一时区更细；如产品要求再单独立项 |
| regional-20 | 地图↔图表/台账/剖面联动 | 明确不做 | Web 区域页也无联动（弹窗与地图即全部交互）；方案 :12-13 与 :80 明确不要求联动 | `apps/macos/Sources/FireSkyNative/ForecastMap.swift:113-118` —— 地图选点仅打开详情面板，不驱动其他视图 | 不做：方案非目标 :144「地图、趋势图、逐时台账、剖面之间互相驱动/联动」，维持各视图独立 |
| regional-21 | 云场字段图层选择（总云/高/中/低混色） | 明确不做 | BurnOutlookMap.svelte:13,144-162 支持 CloudField 混色渲染，但区域页固定 field="score"（outlook/+page.svelte:490），Web 区域页实际未暴露该选择 | `apps/macos/Sources/FireSkyNative/RegionalMapLayers.swift:41,58` —— 桌面仅按质量填充，无云场字段样式 | 不做：Web 区域页用户可见内容只有质量色带（方案 :32），云场字段选择属实况云场地图行（方案 :31）的范畴，区域侧无对齐缺口 |

## 3. 实现批次划分（单点 → 实况 → 区域）

批次按板块划分，与方案实施顺序（:119-127）的对应关系：批次一对应实施顺序 1-3
（覆盖表 → 纯展示补齐 → cross_section.fields 剖面图层），批次二对应实施顺序 4
（实况指标组与卫星剖面状态一致性；nowcast 地图独立立项，:125），批次三对应实施顺序
2 与 5（区域图例/详情与 points[] 弹窗字段）。nowcast-12/13、point-12 为低优先项，
不阻塞各自批次主线。

### 3.1 批次一：单点预测（point-1 ～ point-12、point-17）

1. **基线（point-1）**：建立单点逐字段覆盖表，确认趋势/台账/拆解所需字段全部由
   steps[]/quality_components 返回，UI 扩展不复制评分逻辑（方案 :101-102）。
   本文档即实施顺序 1 的三板块字段矩阵产物。
2. **纯 UI 补齐（point-2 ～ point-11，10 项，后端字段已全部返回）**：
   - point-2 大气剖面图层（温度层线/RH 与云量色块/地形填充/云层状态描边+分层图例）；
   - point-3 评分拆解面板（只读分项表/图，禁止 SwiftUI 重算，方案 :135）；
   - point-4 趋势图三条比例序列（0–100% 右轴或对齐小图，方案 :57-58）；
   - point-5 AOD550 独立小图（取值范围+时次+来源）；
   - point-6 逐时台账补六列（标明 ASL/AGL 口径差异，缺测显示 —）；
   - point-7 台账最佳星标与当前行高亮（与趋势/剖面共享本地选择时刻但不驱动地图）；
   - point-8 趋势图选中标记线/峰值标记/可选事件时间轴条（键盘/VoiceOver 可读）；
   - point-9 诊断组结构化（云属 top3/相态/色彩诊断/演变趋势）；
   - point-10 hero 摘要补起报周期 chip/模式来源说明/主导压力族标签；
   - point-11 剖面元信息（analysis_radius_km/offset_minutes/valid_time_local/status 诊断）。
3. **低优先/后置（point-12）**：剖面随选中时次刷新——先复用现有
   `profile_query.valid_time_utc` 并核对冻结来源/有效期与 UI 接线；forecast 装配本身
   也已读取 offset_minutes，不将新增 RPC 字段作为预设前置条件。当前保持最佳时刻
   剖面+冻结 profile_query，不阻塞其他对齐。
4. **文档澄清（point-17，非 UI）**：完整报告入口在映射清单 :43 与桌面合同代码口径
   不一致，提请澄清：移除/标注「桌面不适用」，或后端先产出报告文件。

不纳入批次：point-13（明确不做）、point-14 ～ point-16（后端缺数，见 3.5）。

涉及文件：`apps/macos/Sources/FireSkyNative/Workbench.swift`、
`ForecastComparison.swift`、`Store.swift`；`native/src/lib.rs`（point-12）；
`docs/MACOS_BACKEND_DATA_DISPLAY.zh-CN.md`、`engine/src/point_core.rs`
（point-1/point-17 口径核对）。

### 3.2 批次二：实况分析（nowcast-1 ～ nowcast-13）

1. **纯 UI 补齐（nowcast-1 ～ nowcast-11，11 项，后端字段均已返回）**：
   - nowcast-1 卫星剖面压力层行线/hPa 标注（pressure_levels_hpa+height_m）；
   - nowcast-2 剖面页头部覆盖状态与采样统计（status/采样点/覆盖率）；
   - nowcast-3 来源图例与色标/缺测语义（retrieved/unknown 样式对齐 Web，方案 :30）；
   - nowcast-4 概览「卫星云况」组补云幕/纹理/地平线三张光学观测卡；
   - nowcast-5 实况趋势补可烧云占比与可烧比例序列（0-100% 右轴或独立小图）；
   - nowcast-6 实况 AOD550 独立小图（不与质量轴混用）；
   - nowcast-7 雷达有效风险组（区分「雷达有效值」与「评分顶棚」语义）；
   - nowcast-8 Hero/摘要改读 nowcast.source 与 image_channel，替换硬编码文案；
   - nowcast-9 光路阻塞风险卡补采样点数角标（geometric_path_samples）；
   - nowcast-10 实况台账横向补三列（亮度颜色/可烧比例/太阳入射阻挡）；
   - nowcast-11 概览并入「光学与雷达」分组并带雷达观测时效。
2. **低优先（nowcast-12、nowcast-13，方案未单列）**：实况太阳光路模式剖面
   （steps[].cross_section 已随 step 携带，注意剖面样本无地形字段需标注）；实况
   quality_components 只读分项表（不重算），或维持现状并在方案里确认排除。

不纳入批次：nowcast-14 ～ nowcast-16（后端缺数，见 3.5）；nowcast-17 ～ nowcast-18
（明确不做；实况地图是否接入独立立项，方案 :125 不阻塞图表对齐）。

涉及文件：`apps/macos/Sources/FireSkyNative/Workbench.swift`、
`NowcastPresentation.swift`、`ForecastComparison.swift`。

### 3.3 批次三：区域地图（regional-1 ～ regional-10、regional-16 实机验收）

1. **前置合同保障（regional-8）**：在桌面级合同测试（tests/test_rust_runtime_forecast.py）
   补 points[] 顶层（peak/事件质量、档位、置信度、云族）与 quality_components
   （可烧覆盖、持续、AOD、修正）键存在断言，防 Swift 解析依赖字段被静默移除——
   regional-1 ～ regional-4 的解析动作都依赖这批字段。
2. **解析与 UI 补齐（regional-1 ～ regional-7、regional-9，8 项）**：
   - regional-1 网格弹窗并列显示峰值/事件两档分数与 peak_quality_level；
   - regional-2 RegionalCell 增加 confidence_score（缺值显示未提供而非 0%）；
   - regional-3 弹窗明细解析 quality_components 五键（可烧覆盖/持续/AOD/入射与观测修正）；
   - regional-4 主导可烧云族标签（null 显示「无可烧目标云」）；
   - regional-5 网格详情面板加「在单点模式打开」按钮（用户发起的模式切换，非图-表联动）；
   - regional-6 可选采样格点叠加层（复用 points[]，上限截断，与网格线开关并存）；
   - regional-7 区域距离×高度剖面图层与图例（profile_query fields，先区域后单点共用
     图层组件，方案 :123-124）；
   - regional-9 区域图例补 0.5/1.0/1.5/2.0 中间刻度。
3. **小改（regional-10）**：网格关闭且有数据时保留覆盖外框描边（RegionalMapLayers.swift:90
   条件放宽），其余覆盖/间距/统计展示已达标不动。
4. **实机验收项（regional-16，不阻塞展示对齐）**：跨 ±180° 真实双侧下载与连续地图
   显示（审计 :128-131「实机显示仍未验收」）。

保持现状：regional-11 ～ regional-15、regional-17（原生已绘/已有字段，无动作）。
不纳入批次：regional-18 ～ regional-21（明确不做，见 3.4）。区域板块无后端缺数项——
regional-1 ～ regional-4 所需字段已在桌面 points[] 顶层/quality_components 返回；如遇
新字段缺失，按方案 :83-84 先扩展版本化区域点 DTO，禁止前端拼接填充。

涉及文件：`apps/macos/Sources/FireSkyNative/RegionalForecast.swift`、
`Workbench.swift`、`Store.swift`、`RegionalMapLayers.swift`、`ForecastMap.swift`、
`RegionalProfileView.swift`；`tests/test_rust_runtime_forecast.py`、
`tests/test_rust_frames.py`。

### 3.4 「明确不做」项汇总（7 项）

| 编号 | 展示项 | 板块 | 口径依据与处理 |
| --- | --- | --- | --- |
| point-13 | 单点预测地图（云场/可烧场预测地图+时间条） | 单点 | 方案 :11、:143；保留 map_capability 原因提示与位置选点地图，不新增预测地图 |
| nowcast-17 | 实况云场地图 | 实况 | 方案 :112-113 本方案不做，需单独提案数据范围/快照容量/地图令牌/渲染合同（合同雏形 MapCloudCellOutput 已存在）；不阻塞图表对齐 |
| nowcast-18 | 地图与图表/剖面联动 | 实况 | 方案非目标 :144、范围决定 :12-13、:17；保持各视图独立 |
| regional-18 | 数据来源/溯源卡（CAMS 起报、时次、stale） | 区域 | 方案 :32-33 区域行未列入该展示面；溯源字段放诊断不放主界面（映射清单 :100-108）；日后需要须先扩展版本化 provenance 块 |
| regional-19 | 自定义区域时区/名称输入 | 区域 | 方案 :32 未要求；桌面逐格自动时区语义比 Web 单一时区更细；如产品要求再单独立项 |
| regional-20 | 地图↔图表/台账/剖面联动 | 区域 | Web 区域页也无联动；方案 :12-13、:80、:144；维持各视图独立 |
| regional-21 | 云场字段图层选择（总云/高/中/低混色） | 区域 | Web 区域页固定 field="score"，用户可见内容只有质量色带（方案 :32）；云场字段选择属实况云场地图行（方案 :31）范畴 |

### 3.5 「后端缺数」项汇总（7 项）

| 编号 | 展示项 | 板块 | 后端口径 | 处理 |
| --- | --- | --- | --- | --- |
| point-14 | 目标云边界位置敏感度面板（boundary_uncertainty） | 单点 | GFS/EC 桌面点预测显式置 Null（point_core.rs:1065；ecmwf_point.rs:238,297）；nowcast 路径才有实现 | UI 不展示；如产品需要先在 Rust 点路径立项计算再补面板，不得占位/猜值 |
| point-15 | GFS 云相标签与冰含量 | 单点 | GFS add_geometry 固定 {label:unknown, ice_fraction:null}（point_core.rs:903-909,934-935）；EC 有 phase_proxy | GFS 显示「未解析」；EC 用已有 phase_proxy 分模式展示；补 GFS 相态需后端独立定义口径 |
| point-16 | 跨模式分歧 model_disagreement_score | 单点 | 占位透传（point_core.rs:968）；审计 :42、映射清单 :115 | 维持现状：不展示分歧数值，只用 GFS/EC 并列比较；后端未计算前不改动 |
| point-17 | 完整报告入口（html_report_path/html_report_url） | 单点 | 桌面点报告显式置 Null/空串（point_core.rs:1063,1071；ecmwf_point.rs:237,308）；映射清单 :43 与代码口径不一致 | 提请澄清：映射清单移除/标注「桌面不适用」，或后端先产出报告文件；在此之前 UI 按钮保持不出现 |
| nowcast-14 | 多光谱云型概率组（云类/置信度/四种云概率） | 实况 | Rust 桌面实况合同无 cloud_type 字段；Web 值由 Python src/firesky/satellite.py 计算；桌面 bands 恒为 HSD B07/B13/B15 | 后端在 Rust 桌面实况合同计算并声明云类语义后才可展示（方案 :116-117）；未计算前 UI 显式「未计算」，不得把 HSD 代理标成 HCAI 云类真值（方案 :66） |
| nowcast-15 | 实况台账·主导压力族/云底–云顶列 | 实况 | 实况评估 DTO 无逐时云柱字段（desktop_nowcast_assessment.rs:1944-1987 不含）；审计 :71 | 维持不虚构（方案 :18）；对齐需先在实况评估 DTO 声明逐时主导云柱字段，否则台账不画这两列 |
| nowcast-16 | 实况回程透过率序列 | 实况 | 桌面实况 steps 显式置 observer_return_transmission = Null（nowcast_runtime.rs:786，不伪造） | 后端声明实况口径的回程透射来源（HSD/CAMS 代理）后才补序列；UI 缺测断线，绝不连 0（方案 :59） |

区域板块无「后端缺数」项。
