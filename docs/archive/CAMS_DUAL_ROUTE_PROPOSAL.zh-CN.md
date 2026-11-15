# CAMS 双路线合同与架构提案

日期：2026-11-12。状态：**路线 B 部分经用户决定暂缓**（见下），本文保留为恢复
路线 B 时的合同/架构基线。依据：
[下一阶段 Agent 执行计划](NEXT_AGENT_PLAN_2026-11-12.zh-CN.md)「建议执行顺序」第 2 步。
提案基于当前工作区的实际代码审计（含 文件:行号 证据），不照抄旧文档结论。

## 0. 用户决定记录（2026-11-12，已确认）

1. **路线 B 整体暂缓**：Earth Engine 数据边界、评分接线、缓存与 UI 全部推迟，
   待路线 A（ADS 完整廓线）真实验收完成后再单独提案。恢复时**必须一并覆盖
   实况（nowcast）**（用户明确要求，即第 2.3 节的备选方案成为恢复时的最低范围）。
2. **当前焦点 = 路线 A 真实验收**：用户确认 ADS 与 Earth Engine 账号均可用，
   但要求先验证 ADS。
3. **ADS 代理合同变更（用户指令，已实现）**：ADS 生产客户端与网络探测不再
   强制无代理——移除 `engine/src/ads.rs` 与 `engine/src/desktop_network.rs` 的
   `.no_proxy()`，ADS 与 GFS/EC 一致遵循环境代理（`HTTP(S)_PROXY`/`ALL_PROXY`/
   `NO_PROXY`）。测试合同同步反转
   （`tests/test_rust_cams_data.py::test_proxy_environment_is_used_for_api_and_asset`）。
4. **ADS 排队预期**：用户确认 ADS 下载时间可能很久，验收按长等待安排，
   不因慢速队列放宽预检或超时合同。
5. **CAMS 整周期下载（用户指令，2026-11-12/13 已实现）**：prepare 默认不再按
   请求子集下载 CAMS——单区域（点/普通区域）prepare 用**一次** ADS 任务下载
   请求所在洲的完整包络（双变量 + 11 层 + 该 run 全部时效），后续同洲预测
   零排队直接命中缓存。范围按「请求所在洲」动态决定（用户示例：北京→整个
   亚洲）。实现：`engine/src/continents.rs`（洲包络：最小包含洲，否则相交
   洲并集，海洋点回退请求区域）、`cams_data` 新增 `all_leads` 参数（下载组
   扩展到该 run 全部 lead，仍走既有单任务提交/拆分/发布机制）、
   `desktop_prepare` 默认 `continent_cycle`。逃生口：payload
   `cams_download_scope:"request"` 恢复旧按请求子集合同（桥接层整包透传，
   已测试）。边界：跨界双矩形请求保持精确 per-part 合同；实况走廊与
   `/data/fetch` 数据页路径保持旧合同，待实况真实验收时再接入。预检无需
   放宽——单区域预检本就接受覆盖型文件（`desktop_preflight.rs:206-218`）。

   **真实数据验收（2026-11-13，release `source_id=1a05cca4…`）**：北京点
   prepare 一次 ADS 任务下载亚洲包络全部 40 个 lead（003–120，122 MiB，
   区域键 `1c0e9c4c8ed5` 与 [81,26,-11,180] 哈希一致），126 秒完成，
   `group_errors`/`missing_valid_times` 皆空；广州点 prepare **0 次 ADS
   任务**、2 次缓存命中、22.8 秒完成；两点真实点预测（gfs、目标
   2026-11-14 dusk）分别以 5.0s/3.7s 完成，评分实际消费
   `cams-ads 2026-11-14 06:00Z..09:00Z` 与 `09:00Z..12:00Z`（AOD550≈
   0.905/0.234），证明覆盖选择与跨点复用在生产链路成立。证据：
   `output/fullcycle-acceptance-20261113/`。逐观测物理评分、区域/实况/
   报告发布与 HTML 全链路仍属 V01 待验收，不能由本批证据替代。

本文其余章节（两路线语义、审计、接线、缓存、测试、验收计划）作为路线 B 恢复时
的既定基线保留，暂不执行。

## 1. 合同审计：数据字段 -> 使用模块 -> 评分路径

当前生产链路只有一条（路线 A 基线）。审计结果：

| 数据字段 | 生产/解码 | 缓存身份 | 评分消费 |
| --- | --- | --- | --- |
| ADS `aod550`（surface 柱 AOD） | `engine/src/cams_data.rs:201-205` ADS 查询变量；`engine/src/cams.rs:149-152` 解码校验（surface、唯一） | `cams-optics-v2-{day}-{HHMM}-{lead}-{area_key}.grib`（`cams_data.rs:446-456`）；area_key=JSON `[N,W,S,E]` SHA-256 前 12 hex（`cams_data.rs:100-132`） | 解码为 `Optics.aod`（`cams.rs:11-17`）；3h bracket 混合（`cams.rs:255-283,364`）→ grid 字段 25（`native/src/national.rs:216` `AEROSOL_AOD550=25`，装配 `engine/src/assessment.rs:1043-1059`）；观测锚点双线性回填 `samples[*][5]`（`assessment.rs:1101`） |
| ADS `aerext532`（11 层 isobaricInhPa 消光，m^-1） | 同上查询；`engine/src/cams.rs:154-166` 解码（层位必须 ∈ `LEVELS`，缺层 fail-closed `:211-216`） | 同一 GRIB 文件 | `Optics.extinction` → `[t, cells, 11]` 张量；11 层 = 1000/950/925/900/850/800/700/600/500/400/300 hPa（`native/src/cams.rs:5`）；评分侧 `AEROSOL_PROFILE_LEVELS=11`（`national.rs:65`）、压力→高度 `:256-262` |
| 上述两字段的评分用途 | — | — | **太阳入射**：`cached_aerosol_path_cache` solar 分支（`national.rs:5328-5392`）→ `solar_cloud_base_lights_uncached`（`:8416`，AOD 累计 `:8724-8726`）/ 单层版（`:9078`，`:9319-9334`）；τ 经 `fire_cloud_illumination_from_aerosol_tau`（`:1502-1534`）施加到可燃负载（`:10219,:10358`）。**观测回程**：`observer_cloud_base_aerosol_optical_depth`（`:7031-7243`）及多层版（`:7246`）；τ 经 `observer_cloud_contrast_from_aerosol_tau`（`:1536-1554`）。**归一化定标**：`aod550 × (段积分/全柱积分)`（`national.rs:737,753,788`；全柱积分 `:694-707`） |
| 算法标签 | — | — | `cams-aerext532-11level-aod550-normalized-segment-v1`（`engine/src/frames.rs:20-21`、`engine/src/diagnostics.rs:52`） |
| 实况链 `AerosolInput`（仅 aod550 标量 + path_samples，**不走 11 层张量**） | `engine/src/nowcast_runtime.rs:197-290`（走廊采样，手动覆盖 `:203-205`） | 沿用 CAMS 缓存 | `engine/src/desktop_nowcast_assessment.rs:151-161`（`source` 字段从未被读，即既有 dead_code warning）→ 旧指数标高模型 `aerosol_transmission_samples_impl`（`native/src/lib.rs:2243-2299`；入射 `solar_inflow_aerosol_tau :559`、回程 `observer_aerosol_tau :520`；调用 `desktop_nowcast_assessment.rs:1787,:3518`） |
| GEE 模块现状（未接线） | Python `scripts/download_gee_cams_aod.py` 产出 `gee-cams-aod-v1` 快照（精确 valid time `:45-51`、最短 forecast_hour `:51`、32 MiB/100k 格上限 `:77-83`）；Rust `engine/src/earth_engine_aod.rs` 校验+光谱估算（`rust-gee-aod-calculated-v1`，`usable_as_cams_profile:false`） | 仅临时输出目录，无正式缓存 | 仅注册于 CLI RPC 层（`engine/src/rpc.rs:353-355`）；桌面 FFI 分发（`engine/src/desktop_runtime.rs:449-676`）与 HTTP allowlist（`engine/src/http.rs:463-466`）均**不可达**；未接入 Settings/`desktop_prepare`/评分器 |

## 2. 两路线语义

### 2.1 路线 A：ADS 完整廓线（默认，现状）

- 行为与今日完全一致：ADS AOD550 + 同时刻 11 层 `aerext532`，严格输入合同、
  3h bracket、fail-closed。不要求 Google Cloud 项目。
- 验收基线：真实 ADS 账号的点/区域/实况全链路仍未验收（V01），与本提案无关地
  独立推进。

### 2.2 路线 B：GEE 光谱柱约束 + ADS 垂直廓线

审计确认当前评分器没有独立的「入射削光层输入」：太阳路径 τ 等于
`柱AOD × (11 层廓线段积分 ÷ 全柱积分)`。因此路线 B 的精确语义是：

- **替换太阳入射路径的柱归一化标量**：AOD550（ADS）→ GEE 光谱 AOD532 估算
  （469/550/670 nm 按 Ångström 幂律，`earth_engine_aod.rs:94-96`）。垂直分布
  形状仍来自 ADS 11 层廓线；观测回程路径、云底几何、太阳几何、非气溶胶字段
  一律不变。
- 实现位置 = 第 7 节最小侵入边界；ADS 路线（默认）下逐位等同现状，零回归。
- 波长语义变化必须显式：现状太阳路径有效波长是 550 nm（532 nm 廓线被 550 nm
  柱重定标）；路线 B 太阳路径变为 532 nm 柱（GEE）× 532 nm 廓线形状，回程仍
  550 nm 柱 × 532 nm 廓线。诊断必须分开标注两种柱来源，不能把混合输入写成
  单一 ADS 或单一 GEE。
- GEE 532 nm 是光谱估算，不是观测波段：结果与 provenance 一律写
  `aod532_estimate`，保留 469/550 与 550/670 斜率及差值诊断（模块已实现
  `earth_engine_aod.rs:105-108`），不冒充实测或 AOD550。

### 2.3 实况范围（待确认：推荐 v1 拒绝并明示）

实况链不消费 11 层张量，走 `AerosolInput` + 旧指数标高模型，是独立回归面。
推荐 v1：路线 B 只覆盖点+区域；实况请求携带路线 B 时 fail-closed 拒绝，错误
明确「CAMS AOD route gee_spectral is not supported for nowcast yet」，UI 对
实况场景禁用/标注该选项。不静默回落路线 A。

备选（工作量明显更大）：一并实现实况入射 τ 的 GEE 通道（`AerosolInput` 新增
solar 专用列并维护双路径位级回归）。若选此项需追加验收批次。

## 3. EE 数据生产边界（待确认：推荐分期）

生产包/镜像不依赖 Python、不 spawn `python3` 是硬合同。两个可选边界：

- **推荐：v1 采用「离线导入」边界**。`scripts/download_gee_cams_aod.py` 限定为
  用户显式运行的导入/诊断工具（与现状定位一致）；正式 App 只消费其
  `gee-cams-aod-v1` 快照文件（NPY+JSON 三件套，含源身份哈希），绝不经
  Python 子进程获取。快照合同固定后，后续把生产者换成原生客户端不影响
  缓存/评分/验收结构。
- **备选：v1 直接实现 Rust EE REST/OAuth 客户端**（refresh token、token 安全、
  取消/超时/代理、EECU 错误处理）。工作量与验收面显著更大，不在本提案默认范围。
- 两方案共同要求：project ID 是非秘密配置，可入设置与 provenance；token/凭据
  永不写入日志、任务历史、结果或缓存文件。

## 4. 设置、请求与来源合同

- 新增版本化选择字段 `cams_aod_route: "ads_profile" | "gee_spectral"`，默认
  `ads_profile`；`deny_unknown_fields`、非法值显式报错。
- 设置面（macOS）：`SettingsView`（`apps/macos/Sources/FireSkyNative/DialogViews.swift:151`）
  新增「CAMS AOD 来源」Picker，默认路线 A；路线 B 需同时填写 Earth Engine
  project ID（非秘密）。实况场景按第 2.3 节处理。持久化走引擎 workspace 配置
  （同 retention/CAMS key 模式，`desktop_runtime.rs:475-511`），不存 token。
- 请求冻结：`cams_aod_route` 与 `ecmwf_source` 同路径进入冻结 common 上下文
  （对照 `desktop_context.rs:843-845,1169-1175`），随 check/prepare/forecast
  会话冻结；预检确认后按同一路线准备与评分。设置变更使既有结果缓存自然失效
  （请求身份变化），不另设失效开关。
- provenance：结果与缓存身份记录实际路线、project、影像 ID、起报/有效时刻、
  波段、导出网格与源文件哈希（快照已含，`earth_engine_aod.rs:241-249`）。

## 5. 数据计划、缓存与保留

- 新增 GEE 缓存命名空间：`FIRESKY_GEE_CACHE_DIR`，默认 `.cache/gee-cams-aod`；
  文件布局仿 CAMS：`gee-cams-aod-v1-{day}-{HHMM}-{area_key}/`（内含
  `aod.npy`、`aod-input.json`、`aod-calculated.json`），目录身份=内容 SHA。
- 逐目标 valid time 导出（精确时刻，禁邻近时刻填充）；区域按冻结 area 拆分
  （复用 `cams_data::split_areas` 语义支持跨日界线双矩形）；大区域分块导出 +
  接边去重校验，缺块 fail-closed，部分覆盖不得报告整区 ready。
- 网格映射：GEE 原生 0.4° 网格不得伪装成 ADS/GFS 网格；评分前把 AOD532 双线性
  重映射到模型目标格，每个模型格必须有明确有效值；无效/缺失 → 该目标 fail-closed。
- readiness：路线 B 预检对每个 (area, valid time) 要求快照存在、影像时刻精确
  匹配、源身份校验、重映射覆盖完整；缺项计入 `missing_gee_aod`，不回落路线 A。
  回退只能由用户显式切回路线 A。
- 保留：GEE 快照按新增独立保留设置（周期数口径，空值=停用，沿用
  「不擅设默认」约束）；清理复用 `cache_retention` 模式。

## 6. 评分接线（最小侵入边界，来自审计）

- 点/区域（11 层张量链）：
  1. `assess_batch_with_status` 入口新增可选「太阳柱」输入（每格 AOD532）；
     缺省时太阳柱=grid[25]，行为与现状逐位一致。
  2. native 侧仅替换太阳路径柱定标：`cached_aerosol_path_cache` solar 分支
     （`national.rs:5328-5392`）及太阳路径预计算 `SharedSolarPathSamples`
     aerosol_paths（`:3989-4070`，命中点 `:5339-5351`，门控 `:8437,:9103`）。
  3. **不得改动**：观测回程族（`:7031,:7246` 及其 `solar=false` 调用 `:7115,:7179`）、
     共享消光原语（`raw_value_at_height :601`、`raw_integral :641`、
     `normalization_integral :694`、`vertical_optical_depth :757`）、τ 响应映射
     （`:1502,:1536,:1556`）、engine 侧 ADS 张量装配（`assessment.rs:1038-1103`）。
- 实况（若批准覆盖）：`AerosolInput` 新增 solar 专用列，仅替换
  `solar_inflow_aerosol_tau` 输入；`observer_aerosol_tau` 与 ledger 不动。
- 失败语义：GEE 估算非法（缺带、非幂律差异、估计值越界）→ 该目标 fail-closed，
  保留旧结果并明确报错；不夹逼、不补值。

## 7. 诊断、UI 与 provenance 输出

- 算法标签新增区分版本：路线 A 保持
  `cams-aerext532-11level-aod550-normalized-segment-v1` 不变；路线 B 输出
  独立标签（含 `gee-aod532-solar-column` 语义），不改动 A 标签。
- 结果诊断新增：路线、project、影像 ID、起报/有效/forecast hour、AOD532 estimate
  （观测点与区域摘要）、两段 Ångström 斜率与差值、实际消费的入射柱来源、
  回程柱来源、重映射版本。混合来源必须分别可读。
- UI：数据页/预检弹窗按路线显示缺项（`missing_gee_aod`）；「已配置」不当作
  「已下载」，需要实际下载证明时用显式测试操作。

## 8. 测试计划

1. 光谱数学边界（扩展 `earth_engine_aod.rs` 既有 4 测试）：零值、缺带、非法值、
   非幂律差异、估计值越界。
2. 源选择持久化与请求冻结：非法值拒绝、默认 A、设置变更后缓存身份变化。
3. 缓存身份与 readiness：过期时刻拒绝、影像不匹配拒绝、缺块/部分覆盖
   fail-closed、跨日界线拆分、分块接缝去重。
4. 评分接线：路线 A 无回归（逐位差分）；路线 B 固定输入 A/B 分数差分（仅目标
   气溶胶项变化，云场/几何/非气溶胶字段不变）；无回退；取消与失败保留旧结果；
   混合来源 provenance 字段完整。
5. 实况（若覆盖）：入射 τ 通道新旧差分；否则拒绝路径测试。

## 9. 验收计划与真实资源

- 顺序：fixture 合同/数值测试 → 用户已配置账号的真实 point/regional 下载与
  点预测 → 证明实际消费时次一致后才扩到实况。记录网络、排队、传输、解码、
  评分耗时与源码身份。
- 资源需求（待确认）：ADS 账号（CAMS key 已有设置入口）；路线 B 另需
  Earth Engine project（`ee.Initialize` 可用）。上游不可用或账号未就绪时
  如实标「待验收」，不把 fixture 通过写成生产通过。
- 真实验收不得替代或推迟路线 A 的 V01；路线 B 验收不能抵扣路线 A 缺口。

## 10. 工作包与执行顺序（批准后）

1. **WP1 合同落地**：`cams_aod_route` 字段、设置持久化、请求冻结、桌面/HTTP
   分发路径打通（现 GEE 方法仅 CLI RPC 可达）。
2. **WP2 数据与缓存**：GEE 缓存命名空间、快照导入校验、分块/拆分、readiness
   与 `missing_gee_aod`、保留设置。
3. **WP3 评分接线**：太阳柱可选输入 + native 最小侵入替换 + provenance/标签。
4. **WP4 UI 与诊断**：Settings Picker、预检缺项展示、结果诊断字段。
5. **WP5 测试与验收**：第 8 节全部用例 → 真实验收（第 9 节）。
6. 实况范围按第 2.3 节决定执行。

## 11. 风险与边界

- GEE `ECMWF/CAMS/NRT` 影像覆盖度与 ADS 发布节奏不同：可能出现 ADS 有时刻而
  GEE 无对应影像（或反之）；路线 B 按自身 readiness fail-closed，不做时刻回填。
- 0.4° 源网格重映射会平滑小尺度 AOD 梯度；诊断保留原始格点与插值版本号以便追溯。
- 分块导出受 EE 导出限额约束（32 MiB/100k 格/次），大区域耗时与 EECU 配额
  属外部边界，超限明确报错不重试风暴。
- 本提案不改变 R12 预检合同、CAMS 手动 run 排除（Q09）、无 Python 生产合同；
  Python 清退节奏照旧（O02，替代验收后）。
