# FireSky 性能优化路线图

日期：2026-11-14  
范围：macOS 原生 Rust 引擎，中国区域 0.25° GFS 冷计算；评估 Metal GPU 是否值得实现。

> **11-14 续轮修正（2026-11-14 下午，覆盖下文历史数字）**：本轮修复了时间权重单位 bug、
> 原始/量化缓存混用与填充顺序依赖、full/compact 置信度回退不一致，并做了精确 CPU 优化与
> Instruments 源码级 profile。下文「已有基线」中的 543.4s / 2400 CPU·s / 601s 等是
> **修复前的 buggy-weight 基线**，仅作历史对照，不再代表当前数值行为或「必要物理计算量」。
> 当前权威测量、热点归因与算法替换矩阵见本节末尾「## 11-14 续轮：修正基线与算法评估」。

## 结论

还有可测的优化空间，但眼下缺少能支撑继续改热路径的函数级 profile。中国 36,685 点实测中，`native_assessment` 用时 543.4 秒，其中 `observer_work` 阶段 531.1 秒，约占 97.7%。数据解码约 4.3 秒、模型打包约 2.8 秒、CAMS 组装约 0.4 秒；进一步缩小 JSON、云图或地图输出不会明显缩短这次冷算。

GPU 可以进入原型验证。当前 Mac 是 Apple M1 Max，32 核 GPU、Metal 可用；但评分路径包含不规则逐格光路、云层分支、稀疏候选与逐观察点状态，未必能直接转成高效 GPU kernel。建议先把固定形状的太阳光路/气溶胶积分作为独立 kernel 试验，只有同时满足数值一致、4 GiB 峰值内存和端到端速度门槛，再考虑产品接线。

## 已有基线

| 指标 | 实测 | 证据与解释 |
| --- | ---: | --- |
| 中国网格 | 36,685 点 | 点间距和模型间距均为 0.25°，不能降为 2° |
| 处理样本 | 365,794 | GFS 单模型、全覆盖 |
| v10 默认参数 | 601.2 秒 wall / 2,432.9 秒 CPU | `bench-china-v10-20261114T065910.json` |
| 后续默认重跑 | 595.2 秒、595.0 秒 | 背景负载仍高，结果接近 v10 |
| 结束一个跑飞进程后 | 479.8 秒 / 2,393.8 秒 CPU | 有效 CPU 核约 5 个；不是空闲 10 核验收 |
| `min_len=1` | 650.7 秒 | 比默认慢约 9%，更细任务切分增加开销 |
| 同参数结果缓存命中 | 5.1 秒 | 重复请求直接复用结果；不代表冷算性能 |
| 峰值物理内存 | 约 3.93 GiB | 用户接受上限约 4 GiB，余量很小 |

阶段日志中 `observer_work` 约 531 秒；共享 motion 缓存构建约 12 秒。两次 macOS `sample` 看到 worker 在执行计算，未发现引擎内锁竞争；采样出现 `sinf`、`hypotf`、`atan2f`、`powf` 等数学函数，但它不是带源码符号的精确热点归因，不能据此直接替换数学实现。

数据来自 `output/clicktest-20261113/bench-china-v10-20261114T065910.*`、`bench-china-minlen1-20261114T101729.*`、`bench-china-idle2..4-*` 和 `bench-china-idle1-*`。历史进程负载使这些数据不等同于受控的空闲机器基准。

## 优化顺序

### P0：先获得可复现的源码级 profile

下一次中国冷算只跑一个基准，不并发启动其它重任务。固定引擎二进制、模型周期、缓存状态、区域、温度/电源状态和内存上限；记录进程 RSS/physical footprint、CPU 秒、wall 秒、系统负载和各阶段耗时。

用 Instruments Time Profiler 对已打包引擎做一次完整中国 `force_refresh`，检查符号化调用树，明确 `observer_work` 中时间是在几何筛选、太阳入射光路、观察者回程光路、云场投影/合成或动态分配。保留 profile、benchmark JSON 和 stderr 日志。当前 `sample` 仅用于判断线程在算还是等待，不足以决定具体代码改法。

验收：同一默认配置重复两次，冷算 wall 相差不超过 10%；记录完整 0.25° 覆盖、峰值内存、CPU 秒及 profile。若机器持续繁忙，应报告“忙机值”，不要称为空闲上限。

### P1：CPU 热路径优化

1. 在受控负载下对比 Rayon 线程数 8 与 10，并保留当前 `min_len=16` 做基准；可以补测 `min_len=4/8`，不再把 `min_len=1` 当默认候选。每次只改一个变量。若 8 线程能避免效率核/系统争用并提高整体 wall，才考虑 macOS 默认线程策略；其它平台保留自动值。
2. 根据 Time Profiler 结果再选择向量化或数据布局调整。优先处理重复几何、路径积分和不必要的内存读写；不要仅因采样里出现某个 `libm` 函数，就用近似三角函数替换。任何近似必须单独验证评分、排名、窗口和跨日期结果。
3. 现有 NEON blocking、observer path batch、angular footprint cache、exact solar path cache、projection sort 等路径需要用基准矩阵验证启用条件及命中率。几何模板因内存/缓存压力当前为 opt-in；太阳路径近似模板也是 opt-in。逐项测试，不能叠加开关后再猜收益。
4. 调整任务粒度时同时记录 CPU 秒和 wall 秒。当前 `min_len=1` 墙钟更慢，而 CPU 秒近似不变，说明细分本身不是负载不均的解法。

进入下一阶段的门槛：在相同数据、相同结果约束下，至少两次冷算均稳定改善；建议以端到端 wall 至少降低 10% 作为保留门槛，低于此值继续保持实现简单。

### P1：4 GiB 内的分块太阳路径缓存

目前跨观察点共享的 solar path sample cache 按“全部运动键 × 全部网格节点”估算内存，超过默认 8 GiB 上限时直接返回 `None`。把 8 GiB 上限抬高与用户的 4 GiB 进程预算冲突，因此不能作为直接优化。

可验证的后续方向是有界的空间/时间分块缓存：只保留当前一批运动键或网格块需要的云层路径样本，处理完后复用缓冲区并淘汰旧块。优先做内存估算和缓存命中率模拟，再实现小区域原型；必须确保不会反复重建缓存、拖慢整体计算。缓存若需要近似量化路径值，先作为独立实验，不进入生产精度合同。

门槛：峰值 physical footprint 不超过 4 GiB；缓存计算本身不超过节省的 observer work；中国全量冷算至少改善 10%；评分误差、缺测、城市排行和结果覆盖通过既有验收。

### P2：Metal GPU 原型

本机 `system_profiler SPDisplaysDataType` 识别为 Apple M1 Max、32 核 GPU、Metal 可用；Rust 引擎目前没有 Metal/wgpu 依赖或 GPU 评分后端。GPU 适合大量独立、规则的数值工作；FireSky 的逐格光路有不同长度和分支，且最终云层可见度合成带有顺序依赖，完整照搬 CPU observer 函数可能出现线程发散、临时数据膨胀和调度开销。

建议原型边界：先让 CPU 完成输入解码、候选几何与紧凑路径打包，再把同形状的太阳光路/气溶胶分段积分送入 Metal compute kernel，GPU 返回每条路径的光学深度、透过率等中间值，现有 CPU 继续做最终云层合成和评分。这样可以先判断重型数值核的吞吐，不必一次把整个 scorer 移植到 GPU。

原型要求：

- M1 Max 上用 Metal compute pipeline；pipeline 在预测开始前建立，避免把 shader 首次编译混入每次评分。
- 输入按批次驻留在共享缓冲区，先测批大小、同步和数据整理成本；统一内存不等于没有同步或没有内存成本。
- 分别量 CPU kernel、GPU kernel、输入准备/同步和完整 `native_assessment`；报告冷启动与预热数据。
- 做 CPU/GPU 对照：每条路径的光学深度/透过率误差、最终各格分值、缺测覆盖、峰值时刻、城市排序；测试晴空、厚云、地形遮挡、低云门限与边界候选。
- GPU 失败或不支持时自动回退 CPU；Metal 代码限定 macOS，核心 Rust CPU 路径继续作为其它平台后端。

建议进入产品化的门槛：在真实中国 0.25° 输入上，`native_assessment` 至少快 1.5 倍、完整 forecast wall 至少快 1.3 倍；peak physical footprint 不超过 4 GiB；每个输出点覆盖完整，分数误差不超过经科学验收确定的容差，不能改变模型失败状态。若只在小合成输入上快，或加速被输入转换/同步抵消，就停止 GPU 方案。

Apple Metal 官方文档说明 compute pipeline 的执行方式，也说明 Apple GPU 的 CPU/GPU 共享存储模式及资源同步要求；因此原型仍需测端到端成本，不能把统一内存当作零成本传输。查阅依据：Apple Developer Documentation《Performing Calculations on a GPU》《Choosing a resource storage mode for Apple GPUs》《Setting resource storage modes》。

### 暂不优先：缩减结果序列化

已经采用 compact 输出，`profile_write_and_verify` 为 0；当前模型解码与打包合计约 7 秒，而主要评估约 543 秒。若用户关闭云图或导出，仍可继续省输出空间和界面加载时间，但这不是中国区域冷算从 8–10 分钟降到分钟级的主路径。

## 固定约束与通过条件

- 中国区域网格保持 0.25°；不通过扩大点距换速度。
- 区域一次只跑一个模型，GFS/EC 不并行重复整套计算。
- 进程峰值内存上限 4 GiB；测 physical footprint，不能只看 RSS 或理论张量大小。
- 每项性能改动使用同一周期、相同缓存状态和同一结果合同做冷算 A/B；再做一次热缓存检查。
- 保留 CPU 输出作为对照。验证完整点覆盖、model_errors、缺测、分值、最佳时刻和排名；GPU 结果需先确定数值容差再接受。
- 建议顺序：`Instruments profile → CPU线程/热点 A/B → 有界缓存原型 → Metal单kernel原型 → 全量A/B`。

## 11-14 续轮：修正基线与算法评估

本节是修复数值 bug 后的权威记录。证据目录 `output/clicktest-20261113/`。

### 已修复的数值/缓存缺陷

1. **时间权重单位 bug**：量化键的 `weight_bits` 现在存无量纲 0..1 权重
   （`bucket(weight*span)/span`），不再把秒当权重。
2. **原始/量化缓存混用 + 填充顺序依赖**：observer/aerosol/terrain/cloud/return
   各缓存统一使用同一 `LayerCacheKey` 的时间输入；新增「同桶填充顺序无关」回归测试。
3. **full/compact 置信度回退不一致**：抽出共享 `geometry_confidence`/`confidence_score`；
   full DTO 的 `cloud_geometry_confidence` 现在与评分实际所用值一致（无最佳步时不再
   错误回退 0.55）。新增两条 compact↔full 一致性测试。
4. **精确 CPU 优化（位级保持）**：水平权重在已归一化经度上免重复 `longitude_on_grid`；
   气溶胶垂直光学厚度归一化复用。新二进制默认 quantized 的缩减 assessment SHA256
   `76d72c2f…` 与旧基线**逐位相同**，证明优化不改数值。

### 修正后的中国冷算基线（忙机，非空闲验收）

输入：GFS 周期 `2026-11-13T18:00Z`、目标 2026-11-14、dusk、中国 0.25°、卫星关；
36,685 点 / 365,794 有效样本 / 模型网格 291×499=145,209 格。
证据 `profile-china-corrected-20261114T152300/`。

| 指标 | 实测 |
| --- | ---: |
| wall | 480.05 s |
| native_assessment | 469.45 s |
| observer_work | 463.26 s |
| 共享 motion 建表 | 6.07 s |
| 解码 + 源哈希 | 5.36 s |
| 打包 + 派生 | 2.21 s |
| profiles + solar | 7.70 s |
| CAMS 组装 | 0.27 s |
| JSON 组装/输出 | 1.36 s |
| 峰值 physical footprint | 3,949,301,624 B ≈ 3.678 GiB |
| 结束 footprint | 1,213,171,008 B |
| 负载（前/后） | [7.26,8.52,9.59] / [25.29,24.83,18.26] |

共享 motion：195 键 × 12,197,556 B/键 = 2,378,523,420 B ≈ 2.215 GiB（84 B/键·格）。
assessment SHA256 `759743880e…`、results `b98f0d6c…`、semantic `348cf7d3…`、forecast `728a1f7b…`。
**与保留的旧报告/结果不匹配是预期**（数值修复后必然变化）。旧 `cpu_s=56.14` 因漏计
Rayon worker 而无效；本轮 CPU 用 POSIX `RUSAGE_SELF` user+system（含 worker）。

### Instruments 源码级热点（缩减 256 点，trace 12.08 s / 31.5 CPU·s）

证据 `profile-reduced-xctrace-direct-20261114T1525.trace` 与 `…-aggregate.json`。
阶段权重：observer work 11.52 s、model pack 8.46 s、decode 4.84 s、shared motion 4.84 s。
observer 内 inclusive：`SolarRayWork::evaluate_scalar` 8.40 s、合并太阳 trace ~8.16 s、
单层回退 ~4.01 s（嵌套重叠，不可相加）、气溶胶路径 ~1.84 s、运动云采样 ~1.47 s、
水平权重 1.38 s、经度归一化 0.61 s。self 前列：malloc tiny 1.42 s、free 1.29 s、
水平权重 0.95 s、插值气溶胶 0.66 s、共享 motion 层 0.60 s、经度归一化 0.56 s。
`atan2/asinf/sinf/hypotf/powf` 合计 ~1.4 s。**结论：热点是重复几何/归一化与动态分配，
不是某个 libm 函数；不要整体替换数学库，精确复用/布局收益更有证据。**

### 时间量化自洽问题与 consistent 模式（本轮新增）

旧 `Quantized120` 对 weight、left_seconds、right_seconds **各自独立**取 120 s 桶，
重建后 `left+right` 不一定等于括号 span（例：60/180 s → 0/240 s，而 weight=120/3600）。
±60 s 可让 40 m/s 轨迹移动 ~2.4 km，并翻转单层云 deck 的抖动判定。

新增 opt-in `Consistent120`（`FIRESKY_NATIVE_CONSISTENT_TEMPORAL_KEYS=1`）：
只对「自括号起点的 elapsed」取一次桶，weight/left/right 全部由该桶导出，
**保证 `left+right==span` 与端点精确**，仍是显式近似（每侧 ≤60 s）。
生产默认不变（仍 `Quantized120`）；`Exact` 由 `FIRESKY_NATIVE_EXACT_TEMPORAL_KEYS=1` 选择。

缩减 A/B（256 点 / 2322 样本，新二进制 `783ccd87…`）：

| 模式 | 共享键 | assessment | wall | 峰值 | assessment SHA |
| --- | ---: | ---: | ---: | ---: | --- |
| quantized（旧二进制） | 32 | 3348 ms | 11.47 s | 1.501 GB | `76d72c2f…` |
| quantized（新二进制） | 32 | 3089 ms | 11.08 s | 1.486 GB | `76d72c2f…` |
| consistent（新二进制） | 32 | 2987 ms | 10.90 s | 1.499 GB | `76d72c2f…` |
| exact（旧二进制） | 2322（超预算关闭共享） | 4007 ms | 12.04 s | 1.110 GB | `91991f80…` |

**consistent vs quantized：419,840 个原始评估值零位差，评分/排名/置信度全字段一致。**
原因：真实 GFS 输入括号内秒数本就自洽（left+right≈span），两模式在该输入重合。
consistent 的价值是**对不一致输入的自洽保证**（严格超集），不是此输入上的提速。
quantized vs exact：105/256 点 event/peak/overall 分值不同，event max 0.0201、
peak max 0.0364、overall max 0.00344，19 个最佳步、95 个排名位变化；
**不得宣称 quantized/exact  parity**。详见 `profile-quantized-vs-exact-20261114.json`、
`profile-consistent-vs-quantized-20261114.json`。

### 算法替换矩阵（回答「现在算法是否有问题、有没有替换方案」）

| 算法/现状 | 是否有问题 | 替换方案 | 优先级/门槛 |
| --- | --- | --- | --- |
| 时间键独立量化（Quantized120） | 是：左右秒不守恒 | Consistent120（已实现，opt-in）；或精确稀疏/分页缓存 | 默认切换需全量 A/B + 科学容差 |
| 精确稠密 motion 表 | 内存不可行（缩减 2322 键 ~26.4 GiB） | **有界时间/空间分页或稀疏按需缓存**（最佳精确替换） | 先做命中率模拟+小区域原型；≤4 GiB |
| `SkyBuffers` 稠密角向/全网格缓存 ~140 MiB/worker | 内存压力 | 分页/块化（触及 ~142 处索引） | 大重构，先设计/测试 |
| `PendingSolarCell` 原实测 288 B，云/几何字段与 `SolarRayWork` 重复 | 冗余内存 | **已实现**：只存几何索引、观察点标记、目标方位和 `SolarRayWork`；两遍间读取不可变几何表，`active_mask == 0` 替代 `None`，pending 缓冲放入 `SkyBuffers` 复用 | 152 B（-136 B / -47.2%）；缩减三轮位级一致，未证明稳定 CPU 收益 |
| RK2 中点轨迹 | 合理（误差尺度 \|a\|t²/6，a=1e-5、3h≈0.65 km） | 更细步长需真实剪切收敛证据 | 暂不替换 |
| 单格边缘 clamp | 有意边界外推，非 bug | 区分上游缺测；不改 | 暂不替换 |
| 单层 deck 抖动 | 宣称无偏集成，但固定空间哈希/时间相关未证 | 先测分布/收敛/重放再替换 | 需证据 |
| 双线性插值 / 地形离散 / 经验评分 | 经验合同 | 无科学证据前不改 | 暂不替换 |
| libm（atan2/sinf/…） | 非主热点 | 不整体替换；精确复用优先 | 暂不替换 |
| CAMS aerext532 归一到 AOD550、固定标准大气压层 | 文档化混合方案 | 需局部测高/真实剖面才改 | 暂不替换 |
| 置信度 | 是输入/几何支持度，非命中概率；compact 省略 profiles 致几何置信近常数 | 不得宣传为校准概率 | 文档/产品口径 |

### 剩余验收缺口

- 全量产品验收仍需桌面 `forecast --regional … force_refresh=true`；本驱动不含
  下载/IPC/结果缓存/城市排行。
- 中国全量 consistent/精确分页的冷算 A/B 尚未跑（缩减已证位级关系）。
- 空闲 10 核受控基准未做；当前全部为忙机值。
- 旧 UI 点击验收只对旧二进制有效，数值修复后需重测。

## 11-14 续轮：PendingSolarCell 紧凑化与验证

证据目录：`output/performance-20261114/`。本节记录的是固定输入冷评估、热文件系统；
不含桌面 IPC、下载和结果缓存。所有计时均为忙机观察，不是空闲 10 核上限。

### 实现与身份

`engine/src/native/national.rs` 的 `PendingSolarCell` 不再重复保存几何、云样本、风险、
活动层和目标坐标。第二遍通过 `geometry_index` 读取 `ensure_observer_geometry` 生成且在
两遍间不可变的几何表；`target_bearing` 仍单独保存，因为无云时为 0，不能改用几何方位。
`SolarRayWork::clouds()` 复用工作记录，`active_mask == 0` 表示原 `None` 分支；pending
`Vec` 放入 `SkyBuffers` 复用。没有新增公开开关，没有改浮点表达式、libm、时间量化、
云层抖动或经验评分。

布局测试直接测得旧结构 **288 B**、新结构 **152 B**，减少 **136 B / 47.2%**。上文算法
矩阵中的约 1369 B 是错误估计，已由本实测覆盖。

| 身份 | 值 |
| --- | --- |
| 缩减基线二进制 SHA256 | `189c8a0722b2ed727cadda1bf206561e2df231d0768aaa3e48ac841c7d97e882` |
| 缩减基线 engine source ID | `0c4ab3d1bf0e466c6a5b109c733adc2c88b122b55c22e8c07116fc1e95e4b516` |
| 修改后二进制 SHA256 | `1924187408dbab93178ea5f7e0405cbbde7ef4ddd4d8f314d495326b30681083` |
| 修改后 engine source ID | `b04493c3e605c46bc88e347901efc2da740dba2d53b1b6083d1c60beb0ba24cd` |
| 缩减请求 SHA256 | `43ab5fe9763b9cafb3fe41e21aa129a925841211cfef4be84bcf1c417d953ca2` |
| 全国请求 SHA256 | `cdd08dc1848ddc8908f34b134bcd70bb792d3a2012c0816c72bdbbc97b7a999b` |

基线二进制来自编辑前的当前脏源码，不是 HEAD。缩减输入为 256 点（16×16）、2322 个
有效样本；全国输入为 36,685 点、365,794 个有效样本、145,209 个模型格。两者均为
GFS `2026-11-13T18Z`、目标 `2026-11-14` dusk、0.25° 点距和模型间距、单模型、
默认 quantized 时间键。

### 缩减三轮 A/B

每轮单独进程、串行执行；`observer_work` 与 `shared_motion` 来自阶段日志。

| 轮次 | wall s | CPU s | assessment ms | observer ms | motion ms | 峰值 footprint B |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline r0 | 11.293551042 | 30.774698 | 3178.470791 | 2331.2 | 808.5 | 1,504,740,984 |
| baseline r1 | 11.539205250 | 30.270700 | 3418.001750 | 2429.2 | 950.9 | 1,504,593,504 |
| baseline r2 | 11.236213500 | 30.437104 | 3220.518791 | 2330.7 | 853.1 | 1,493,829,216 |
| after r0 | 11.006602375 | 30.764467 | 3054.773958 | 2283.1 | 735.7 | 1,498,318,432 |
| after r1 | 11.437431250 | 30.820725 | 3193.077041 | 2237.9 | 917.7 | 1,489,258,056 |
| after r2 | 11.138288709 | 30.327207 | 3224.655625 | 2297.2 | 889.8 | 1,486,145,144 |

均值：wall `11.3563 → 11.1941 s`（约 -1.43%）；CPU `30.4942 → 30.6375 s`
（约 +0.47%，无 CPU 收益）；assessment `3272.33 → 3157.50 ms`（约 -3.51%）；
observer `2363.70 → 2272.73 ms`（约 -3.85%）；峰值 `1,501,054,568 →
1,491,240,544 B`（约 -9.81 MB / -0.654%）。轮间范围重叠，且基线/修改后负载不同，
不能把 assessment/observer 均值写成稳定加速。

三轮 `compare` 均通过：每轮 raw assessment 419,840 个值、raw samples 46,080 个值
零位差；epoch、anchor、评分、置信度、云量、最佳步、排名和 compact 字段零变化。
assessment SHA256 为 `76d72c2f41ca31478442c32c5035a08bb1854b218b6f1b3336c0214b9d622b49`，
samples SHA256 为 `11f838b3f6e2818bbf01ff8836051f2c8d6bff383c7c1db28498c6411ed521b6`，
semantic SHA256 为 `88515dce0b85ef354bdfe4bde3bbe1dd07ff3ca45c6da844c1a260c3cd72c30f`。
对应证据为 `baseline-reduced-r0..r2/`、`after-reduced-r0..r2/` 和
`reduced-r0..r2-compare.json`。

### 线程 8 vs 10 单变量观察

使用同一修改后二进制和缩减请求，固定 `FIRESKY_NATIVE_PARALLEL_MIN_LEN=16`，分别
显式设置 `RAYON_NUM_THREADS=8/10`，各两轮、每轮独立进程、串行执行。两组均与
`after-reduced-r0` compare 通过：raw assessment/samples、评分、置信度、最佳步、
排名和 semantic identity 零差异。

| 配置 | wall 均值 s | CPU 均值 s | assessment 均值 ms | observer 均值 ms | 峰值均值 B |
| --- | ---: | ---: | ---: | ---: | ---: |
| 8 线程 | 11.297153416 | 31.381568 | 3205.753438 | 2352.75 | 1,382,393,332 |
| 10 线程 | 11.041959500 | 30.874446 | 3180.600979 | 2312.60 | 1,493,321,312 |

8 线程相对显式 10 线程：wall 约 +2.31%、CPU 约 +1.64%、assessment 约 +0.79%、
observer 约 +1.74%，峰值约少 110,927,980 B（约 105.8 MiB / -7.43%）。轮间范围
重叠，且仍是忙机，不能据此改默认线程策略；当前证据只说明 8 线程可能以速度换
内存，需空闲受控复验。证据为 `threads8-min16-r0..r1/`、
`threads10-min16-r0..r1/` 及对应 compare JSON。

### min_len 4 / 8 / 16 单变量观察

同一修改后二进制、缩减请求、显式 10 线程，分别设置
`FIRESKY_NATIVE_PARALLEL_MIN_LEN=4/8/16`，各两轮独立进程、串行执行。所有轮次与
同轮 `min_len=16` compare 通过，raw assessment/samples、评分、置信度、最佳步、
排名和 semantic identity 零差异。

| min_len | wall 均值 s | CPU 均值 s | assessment 均值 ms | observer 均值 ms | 峰值均值 B |
| --- | ---: | ---: | ---: | ---: | ---: |
| 4 | 11.359722480 | 31.153395 | 3243.160917 | 2340.95 | 1,547,437,712 |
| 8 | 10.966162563 | 30.472145 | 3135.121250 | 2241.10 | 1,519,683,204 |
| 16 | 11.041959500 | 30.874446 | 3180.600979 | 2312.60 | 1,493,321,312 |

相对 16：4 的 wall 约 +2.88%、峰值约 +54.1 MB；8 的 wall 约 -0.69%、
CPU 约 -1.30%、assessment 约 -1.43%，但峰值约 +26.4 MB。8 的两轮 wall 均低于
对应 16 轮次，但差值小、负载不同且峰值更高，不足以达到稳定 10% 保留门槛；
默认 `min_len=16` 保持不变。证据为 `threads10-min4-r0..r1/`、
`threads10-min8-r0..r1/`、`threads10-min16-r0..r1/` 及对应 compare JSON。

### 全国全量观察与独立证据核对

修改后全国单轮：`after-china-r0/summary.json`。

- wall **432.782681167 s**；worker 计入的 `RUSAGE_SELF` CPU **2207.80074 s**。
- native assessment **421976.989084 ms**；observer work **416317.6 ms**；
  shared motion **5545.3 ms**。
- 峰值 physical footprint **3,920,613,240 B ≈ 3.65136 GiB**，低于 4 GiB；
  结束 footprint 1,183,122,728 B。
- 负载前 `[10.81, 11.30, 10.16]`，后 `[34.00, 36.84, 24.63]`，极忙。
- 36,685 点 / 365,794 有效样本完整覆盖，点距和模型间距均 0.25°。

相对旧权威全国基线 `profile-china-corrected-20261114T152300/`，wall 约 -9.85%、
assessment 约 -10.11%、峰值少 28,688,384 B。但旧基线源码身份不同、负载不同，且旧
`cpu_s=56.14` 漏计 worker，不能据此宣称稳定 10% 提升或比较 CPU 秒。

独立只读脚本 `audit_china_evidence.py` 生成 `china-independent-audit.json`：12 个
manifest 张量全部校验和一致、逐位一致；assessment/samples SHA 与旧权威相同；去除
`frames.performance` 后 frames 语义零差异；最佳时刻和观察者网格排名零变化。
结果字段只有 `confidence_score` 不同：**14,072 / 36,685 点**，最大绝对差
`2.9325484263154067e-9`，其他字段零差异。

这项差异来自当前脏源码已有的 full/compact 置信度回退修复：旧值使用 f64 字面量
`0.55` 得到 `0.7092999999999999`；当前 compact 合同使用 f32 常量
`DEFAULT_GEOMETRY_CONFIDENCE=0.55`，得到 `0.7093000029325484`。14,072 个变化点均
无 burnable best、但有 first valid step；未变化点均符合当前 f32 常量。它不是
Pending 紧凑化造成的 raw assessment 变化。

全国 `bench_regional_profile compare` 初次失败（日志 `china-r0-compare.log`）：比较器
固定用 `COMPACT_GEOMETRY_CONFIDENCE=0.55` 的 f64 值重算，未识别旧 f64 回退合同。
该缺口已在「11-14 第三轮」以仅改验证工具的方式闭环，见下节；生产评分未动。

## 11-14 第三轮：全国比较器置信度合同闭环

只修改验证工具 `engine/examples/bench_regional_profile.rs`，不改生产代码：

1. 新增 `ConfidenceContract` 分类：stored `confidence_score` 必须逐位等于当前
   compact 合同（f32 常量拓宽为 f64），或恰好等于旧 f64 字面量 `0.55` 回退值
   **且该观察点无 burnable best step**（对应修复前 `best.map_or(0.55, ..)` 行为，
   与是否存在 first valid step 无关）。其他任何值仍 fail-closed。
2. `confidence_score_delta` 改为记录 stored bits，跨合同差异如实呈现，不再被
   重算值掩盖；报告新增 `confidence_contract` 块统计两侧 legacy 点数。
3. 新增 4 条单元测试：两合同值确实不同、当前合同任意步选择均接受、legacy 仅在
   无 best 时接受（有 best 时拒绝）、扰动位/跨 aerosol base 均拒绝。

身份分离：验证器独立构建于 `output/performance-20261114/verify/bench_regional_profile`
（SHA256 `185c40c230a64cd7e103815a768aaaba7028e2d326cb212c628c0cec4695b090`）；
原 A/B 二进制 `baseline/`、`after/` 未重建未改动。注意 `cargo test --example`
只构建测试 harness，不刷新普通 example 产物；须显式 `cargo build --example`。

最终验证（`china-r0-compare-final.json`，exit 0）：

- raw assessment 60,163,400 值、raw samples 6,603,300 值零位差；
- event/peak/overall/云量/duration/window/epoch/anchor 全零位差；
- `confidence_score` 14,072 点差异（max 2.93e-9），左侧全部识别为 legacy
  回退合同、右侧 0 个；best step、family、质量等级、排名零变化；
- `semantic_identity_equal=false` 如实保留（confidence 字段确实不同）。

缩减三轮与线程实验用同一验证器复跑 compare（`reduced-r0..r2-compare-final.json`、
`threads8-min16-r0..r1-compare-v2.json`）：全部通过，legacy 点数 0，语义一致。
验证器单元测试 4/4 通过；完整 lib 复跑 **455 passed / 3 ignored / 0 failed**。

全国 compare 至此闭环。剩余产品验收（桌面 IPC/下载/结果缓存/城市排行、空闲
受控基准）不受此影响，仍按下节缺口执行。

### 测试、复现与剩余缺口

已通过：`native::national::tests` **102 passed**；完整
`cargo test --manifest-path engine/Cargo.toml --release --locked --lib`
**455 passed / 3 ignored / 0 failed**；release example 构建通过；`git diff --check`
通过。新增布局测试和 8 种 active mask × 0/500 m 观察点海拔的复用/审计位级测试通过。
`rustfmt --check` 仍有 national.rs 中既存格式差异；本轮新增测试格式已对齐，未整文件
重排用户改动。

复现入口（输出目录必须新建；性能运行串行）：

```bash
output/performance-20261114/baseline/bench_regional_profile run-reduced \
  output/clicktest-20261113/profile-reduced-20261114T144728/request.json \
  output/performance-20261114/<fresh-baseline-dir> 1 2322
output/performance-20261114/after/bench_regional_profile run-reduced \
  output/clicktest-20261113/profile-reduced-20261114T144728/request.json \
  output/performance-20261114/<fresh-after-dir> 1 2322
output/performance-20261114/after/bench_regional_profile compare \
  <baseline-manifest> <after-manifest> \
  output/clicktest-20261113/profile-reduced-20261114T144728/request.json \
  <fresh-compare.json>
python3 output/performance-20261114/audit_china_evidence.py
```

线程/粒度实验只需在上述 `run-reduced` 前分别设置
`RAYON_NUM_THREADS=8` 或 `10`，以及 `FIRESKY_NATIVE_PARALLEL_MIN_LEN=4/8/16`；
每次只改一个变量，输出到新目录，再与同配置基准 compare。

剩余缺口：空闲 10 核受控重复 A/B；桌面 `forecast --regional … force_refresh=true`
及下载/IPC/结果缓存/城市排行；分页/稀疏精确缓存、
`SkyBuffers` 块化和 Metal 原型。线程 8 vs 10、`min_len=4/8/16` 已有上述忙机
单变量观察，但均未达到改默认策略的受控验收门槛。
全国置信度比较器已于第三轮闭环（见上）。
下一步先完成当前验证闭环，不同时叠加多个性能变量。

## 11-14 第四轮：桌面产品链路验收（全国 force_refresh + 结果缓存）

用当前源码构建主二进制 `engine/target/release/firesky-engine`
（SHA256 `e5c2aac66a7e864663f51ef96cfe6e2d11ef38b20ad9ba2539f98e702aae1b76`），
走真实 CLI 产品入口 `forecast --regional --input …`（`Runtime::compute` →
`forecast_models` → preflight/pin → prepare → regional → 城市排行 →
publish_cache），不改任何生产代码。

隔离与固定输入：工作区 `output/performance-20261114/desktop-acceptance-20261114/workspace/`
只符号链接只读原始缓存（`.cache/gfs|cams|ecmwf|srtm`）与 `resources/app-data`；
结果缓存、报告、任务历史全部落在隔离目录，用户真实产品状态未触碰。
payload 取自冻结 capture 的 `context-input.json`，另设
`FIRESKY_CONTEXT_NOW_UTC=2026-11-14T03:01:10.203946+00:00` 固定上下文时刻，
与 bench 同一冻结世界（GFS 2026-11-13T18Z、目标 11-14 dusk、CAMS 11-12T12Z）。
运行器 `run_desktop_acceptance.py`（stdlib，`proc_pid_rusage` footprint +
`getrusage RUSAGE_CHILDREN` 差值计 CPU，含 worker 线程）。

刷新轮（`force_refresh=true`，忙机，负载前 `[12.11, 8.87, 7.94]`）：

- wall **504.672 s**，CPU（children 差值）**2438.654 s**；
  其中 native assessment **458.367 s**、prefetch 1.225 s、解码+CAMS+装配约 16.7 s；
- 峰值 physical footprint **3,990,654,984 B ≈ 3.7166 GiB < 4 GiB**，
  峰值出现在 470 s 附近（城市排行/发布阶段叠加），结束前样本已回落；
- 36,685 / 36,685 点成功、`model_errors={}`、`runtime_python_used=false`、
  cycle 与冻结请求一致；输出 40.3 MB。

与冻结 bench（`after-china-r0/run-000`）语义对比（`audit_desktop_acceptance.py`
→ `desktop-acceptance-audit.json`，verdict_pass=true）：

- 36,685 点结果字段**逐位一致**，唯一差异是产品层为每点补注
  `timezone_name=Asia/Shanghai`（bench 无此字段，属合同内行为）；
- `frame_data` 除 `frames.performance` 四个墙钟计时字段外逐位一致
  （与既有审计的 null-performance 口径相同）；
- 城市排行 `status=ready`，694 城（目录 695，1 城被 bbox 过滤），
  rank 连续、peak_quality 降序合同成立；top1 龙井 1.6213；
- `profile_source.available=false`：compact 区域产品按设计不产剖面源
  （`assessment.rs` 中 `with_profiles && !compact_results`），如实记录，非缺陷。

缓存命中轮（同 payload 去掉 `force_refresh`，9 s 后）：wall **0.763 s**、
CPU 0.502 s、峰值 217.9 MB；`cache.status=hit`、key 相同
（`982f9e59…`）、TTL 300 s 内；返回与刷新轮除 cache 状态块外**逐位一致**。
缓存登记 `request` 已剥除 `force_refresh`；report 身份与登记一致；
12 个冻结输入源 SHA256 逐一复核通过；map-frames 工件 11.7 MB 落盘。

边界如实记录：本轮为 CLI 直连 `Runtime::compute`（无 GUI/IPC 会话层）、
全部输入热缓存（无真实下载）、忙机单次观察；load_after 高达 121 说明
期间机器极忙，504.7 s 不能与 432.8 s 的 bench 直接归因比较（产品链路多了
preflight/prepare/城市排行 694 点第二次评估/发布序列化）。GUI 内点击验收、
真实下载路径、空闲受控 A/B 仍是缺口。

复现：

```bash
cargo build --manifest-path engine/Cargo.toml --release --locked --bin firesky-engine
python3 output/performance-20261114/run_desktop_acceptance.py all
python3 output/performance-20261114/audit_desktop_acceptance.py
```

剩余缺口（更新）：空闲 10 核受控重复 A/B；GUI/IPC 会话层与真实下载路径验收；
分页/稀疏精确缓存、`SkyBuffers` 块化和 Metal 原型。桌面 CLI 全链路
（force_refresh、结果缓存命中、城市排行、位级一致性）本轮已闭环。

## 11-14/15 第五轮：IPC 会话层验收 + 精确缓存命中率模拟 + Metal 单 kernel 原型

### 5a. 桌面 IPC 会话层（真实 GUI 协议）验收 — 通过

`run_desktop_ipc_acceptance.py` 驱动 `firesky-engine desktop` 的 stdin/stdout
JSON-lines 协议，走 GUI 实际使用的完整会话链：
`hello → check(regional) → prepare → forecast(confirmed+cycle_age_confirmed) →
progress 流 → task_history → task_events → result_restore`。
同一隔离工作区、同一冻结上下文时刻与 payload（force_refresh=true）。

- check 0.264 s（preflight ready=true，会话 token 建立）；prepare 1.873 s；
- forecast **418.789 s**（忙机，含 694 城排行），37 条 progress 事件，
  stage 覆盖 prepare/forecast，percent 10→93 单调不减；
- 任务历史 `status=done`，不可变结果快照落盘；`result_restore` 22.1 s
  返回 **40,347,912 B 与 forecast 结果逐位一致**；
- IPC 结果与第四轮 CLI 结果除 cache/timing/frames.performance 外**逐位一致**
  （`audit_desktop_ipc.py` → `ipc-audit.json`，verdict_pass=true）。

边界：仍是热缓存（无真实下载）、忙机单次；GUI 图形界面内的实际点击
（Swift 层）未自动化，属产品 UI 验收而非引擎协议缺口。

### 5b. 精确时间键共享 motion 缓存命中率模拟 — 结论：稀疏/分页不可行

`simulate_motion_cache.py`（stdlib，只读）把冻结全国 samples 张量
（365,794 有效样本）按生产 `temporal_cache_key` 合同重放：

| 模式 | 唯一键 | 稠密表 | 平均复用 | 单次使用键 |
| --- | ---: | ---: | ---: | ---: |
| Quantized120（生产默认） | 195 | 2.215 GiB | 1875.9× | 1 |
| Consistent120 | 195 | 2.215 GiB | 1875.9× | 1 |
| Exact | **365,660** | **4153.8 GiB** | **1.0004×** | **365,526（99.96%）** |

quantized/consistent 键数与生产诊断日志（195 键、bytes_per_key=12,197,556）
完全一致，验证了模拟器合同正确。Exact 模式下 3 GiB 预算只能装 264 键：
LRU 命中率 **0%**（64/128/195/264 键容量全部为 0），Belady 理论上界也只有
**0.037%**（134 次命中）。原因：精确键含每观察点唯一的 f32 秒/权重位，
时间局部性几乎不存在。

**结论：精确稀疏/分页 motion 缓存路线在此输入上被证伪，不建议实现。**
quantized 195 键稠密表（2.2 GiB、1876× 复用）就是该输入的正确设计；
要改时间近似只能走 Consistent120 全量 A/B + 科学容差路线（见算法矩阵）。
模拟按观察者主序访问，生产 worker 交错会略有不同，但 99.96% 单次使用
决定了结论对访问序不敏感。证据 `motion-cache-simulation.json`。

### 5c. Metal 单 kernel 原型 — 位级一致达成，吞吐 12×，但产品接线仍 gated

独立原型 `output/performance-20261114/metal-prototype/`（metal 0.33 crate，
不触碰引擎 Cargo.toml/生产代码）。选固定形状的气溶胶垂直梯形积分
（`AerosolColumn::raw_integral` 全剖面热路径，11 层、同表达式同顺序）：

- 运行时 `new_library_with_source` 即使 `fast_math=false` 仍默认 FMA 收缩
  （LLVM IR 可见 `llvm.fmuladd.f32`），8.4M 列中 26% 位差（max 6e-8）；
- 用 Metal CLI `-fno-fast-math -ffp-contract=off` 预编译 metallib 后，
  **1024 / 65,536 / 1,048,576 / 8,388,608 列全部 0 位差**；
- M1 Max 上严格 kernel 吞吐：8.4M 列 1.60 ms vs 单核 CPU 19.2 ms，
  **12.0×**；1M 列 6.5×；65k 列 0.58×（dispatch 开销主导）；
- 证据 `metal-prototype-result.json`、`kernel-strict.metallib` 与 IR 对照。

**产品接线判断（如实）**：该 kernel 只覆盖气溶胶路径积分，xctrace 中
气溶胶相关 self 时间约 0.66–1.84 s / 全国 ~458 s assessment（<0.5%）。
即使 12× 加速该段，端到端收益上限 <0.5%，不满足路线图「端到端速度门槛」。
真正热点（observer_work 97.7%）是不规则逐格光路 + 顺序依赖云层合成，
不是固定形状积分。**结论：Metal 数值可行性与位级一致性已证明，
但当前不值得产品接线；若未来把太阳光路打包成固定形状批处理再评估。**
（注：本段「<0.5%、不值得接线」已被 5e 源码级 profile 与 5f/5g 真实数据
批处理原型推翻，保留原文仅作决策轨迹；以 5e/5f/5g 结论为准。）

### 第五轮复现

```bash
python3 output/performance-20261114/run_desktop_ipc_acceptance.py
python3 output/performance-20261114/audit_desktop_ipc.py
python3 output/performance-20261114/simulate_motion_cache.py
cd output/performance-20261114/metal-prototype && cargo build --release --offline \
  && ./target/release/firesky-metal-prototype
```

剩余缺口（第五轮后）：空闲 10 核受控重复 A/B（机器持续繁忙，外部条件）；
GUI Swift 层实际点击与真实下载路径验收（需 UI 自动化/网络条件）；
`SkyBuffers` 块化（大重构，需先设计）。精确分页缓存与 Metal 单 kernel
原型两项已按门槛完成评估并给出「不接线/不实现」的证据化结论。

### 5d. SkyBuffers 块化设计稿（第五轮补充，未实现）

源码清点推翻了旧「~140 MiB/worker」估计：当前角向场只有 28 × 3240 f32
≈ 354 KiB/worker（10 worker 共 ~3.5 MiB），块化的内存动机不成立；
潜在动机转为每样本 28 场全量 fill 的 reset 写开销（全国累计 ~123 GiB
缓存写）。设计稿 `docs/archive/SKYBUFFERS_BLOCKING_DESIGN_2026-11-15.zh-CN.md`
给出两阶段方案（先脏块 reset 实验、测得占比后再决定是否 140 处块化重构）、
保序不变量与位级验收门槛。实现需要受控负载窗口，与空闲 A/B 同一外部约束。

### 5e. 源码级 profile 闭环（P0 缺口）+ 全国第三/四次独立复现

release profile 是 `strip=true`+LTO，旧二进制采样全为 `???`。用
`CARGO_PROFILE_RELEASE_STRIP=false CARGO_PROFILE_RELEASE_DEBUG=1` +
独立 `CARGO_TARGET_DIR`（`profile-build/`，不改 engine/Cargo.toml）构建
带符号同优化级别二进制，对全国 run 做 `/usr/bin/sample 30s @1ms`：

self 时间占 busy 采样（113,119 / 135,744，忙机 load~30）：

| 组 | 占比 |
| --- | ---: |
| 气溶胶（interpolated_aerosol_column / path_cache / raw_integral / raw_value_at_height / extinction） | **24.0%** |
| solar_cloud_base_lights_uncached（含嵌套闭包） | 17.7% |
| libm（atan2f/sinf/asinf/hypotf/powf/expf…） | 16.2% |
| assess_observer_sky_v2_internal 本体 | 9.9% |
| SharedMotionCache / motion_cloud 查表 | 9.7% |
| cached_horizontal_path_node / begin_horizontal_path_cache | 7.5% |
| memset/memmove（含 reset 写零） | 4.8% |
| for_each_angular_cloud_cell 写入 | 4.2% |
| CloudProjection quicksort | 2.7% |

证据 `hotspot-sample-20261115/source-profile.json`、`sym-china-sample.txt`。
注意：self 归因、嵌套不可相加、忙机 30s 窗口，不是空闲精确计时。

**修正 5c 的 Metal 收益上限**：早先「气溶胶 <0.5%」引用的是 xctrace 缩减
run 的 self 前列（0.66–1.84s），漏掉了 interpolated_aerosol_column 与
path_cache 大头。全国源码级 profile 显示气溶胶组合计 **24% busy**，
其中固定形状垂直积分（raw_integral+raw_value_at_height）约 4.7%，
水平插值/路径缓存约 19%。Metal 严格 kernel 已证明位级一致 + 12× 单核，
若把气溶胶垂直积分段批处理送 GPU，端到端收益上限约 4–5%（不是 <0.5%）；
水平插值段形状也规则，合计上限可到 ~20% 量级，但需要打包/回传开销实测。
**结论更新：Metal 接线值得进入下一阶段原型（气溶胶批处理），不再是
「不值得」；仍受 4 GiB 与位级一致门槛约束。**

**11-15 补充原型（水平插值 kernel）**：把气溶胶组最大 self 热点
`interpolated_aerosol_column`（4 邻居 × 11 层加权）也做成严格 Metal kernel
（`aerosol_column_interpolate`，同守卫顺序/累加顺序）。65,536 / 1,048,576 /
8,388,608 查询全部 **0 位差**（NaN 位也一致），8.4M 查询 GPU 5.67ms vs
单核 CPU 228ms，**40.3×**。垂直积分 kernel 同批复测仍 0 位差、~9–12×。
两个 kernel 合计覆盖气溶胶组 ~24% busy 中的规则数值段；下一步是把
CPU 侧候选几何/路径打包成批、GPU 返回中间列、CPU 继续云层合成的
端到端原型，实测打包/回传与统一内存开销后才能谈产品接线。
证据 `metal-prototype-result.json`（interpolation_sizes）。

memset/memmove 合计 4.8% 与 5d 的 reset 写零假设方向一致，但占比不高，
脏块 reset 的预期收益上限约 1–3%，优先级低于气溶胶批处理。

同窗口两次全国独立复现（均 12 张量逐位一致、confidence 合同一致）：
-  stripped 二进制：wall 420.3s / assessment 410.4s / 峰值 3.665 GiB；
-  带符号二进制：wall 381.4s / assessment 371.2s / 峰值 3.678 GiB。
两者与权威 after-china-r0（432.8s）差异在忙机负载波动范围内，
不构成稳定加速证据；带符号版略快可能是负载窗口差异，未受控。

### 5f. Metal 真实数据验证 — 冻结全国 CAMS + 真实查询几何，全部 0 位差

合成数据通过后，用冻结全国请求（36,685 点 / 365,794 有效样本）的 4 个
CAMS GRIB 源做真实数据验证。新增独立 dumper
`engine/examples/dump_metal_realdata.rs`（只读复用生产合同，不改生产代码）：
`cams::decode_many` → `native::cams::map`（145,209 格点全覆盖）→
按 assessment.rs 的帧内插公式合成 8 个时刻的 [cell][12] 列 →
按 national.rs 的 `horizontal_weights` 公式生成真实查询几何。

查询集合 656,212 条 = 365,794 条真实有效样本（观察者位置、样本筛选
`sample[6]>=0.5` 与冻结 samples.npy 完全一致）+ 145,209 格点中心 +
145,209 确定性抖动点。导出物在 `metal-realdata/`（mapped 张量、时间列、
查询、生产镜像 CPU 归一化积分，共 ~104 MB）。

严格 metallib（`-fno-fast-math -ffp-contract=off`）重编后三层验证：

| kernel | 规模 | 位差 | 单核加速（忙机） |
| --- | --- | ---: | ---: |
| aerosol_column_integral | 8 时刻 × 145,209 列 | **0**（且与 dumper 生产镜像积分逐位一致） | 1.1–2.1× |
| aerosol_column_interpolate | 8 时刻 × 656,212 查询 × 12 值 | **0** | 2.8–7.6× |

合成基准同批复测仍全部 0 位差。证据 `metal-realdata-result.json`、
`metal-realdata.log`、`metal-realdata/meta.json`。

### 5g. 端到端气溶胶批处理原型 — 融合 kernel，1.94 亿值 0 位差，~35–47×

按 5e 结论进入端到端原型：新增融合严格 kernel `aerosol_path_batch`，
单线程内完成「4 邻居水平插值 → 全剖面归一化积分 → K 个路径节点消光」，
镜像 `interpolated_aerosol_column` + `raw_integral` 热路径 +
`raw_value_at_height` 层梯 + `extinction_km_inv_from_path_cache` 的
完整表达式顺序与守卫（fast-path valid 门、support 裁剪、normalization
有限性检查）。CPU 打包真实查询几何 + 每查询 37 个节点高度
（地形 0–4500 m、含 8% 地下 / 4% 超顶 / 2% 无 support 的守卫路径），
GPU 返回逐节点消光，CPU 继续持有云层合成（本原型不接产品）。

实测（Apple M1 Max，忙机，8 时刻 × 656,212 查询 × 37 节点）：

- **194,238,752 个输出值全部 0 位差**（8 个时刻各自全量比对）；
- GPU 每时刻 ~6.4 ms vs 单核 CPU ~247 ms，**35–47×**；
- 打包 56 ms（一次性，全部时刻共用）、共享存储上传 13 ms、
  每时刻回读 ~2–3 ms（97 MB 输出拷贝）——统一内存开销已实测计入；
- 证据 `metal-pathbatch-result.json`、`metal-pathbatch.log`。

**结论更新**：气溶胶规则数值段（profile 中 ~24% busy）的 GPU 批处理
在真实数据上同时满足位级一致与数量级吞吐收益，打包/回传开销远小于
收益。下一步是产品接线设计：候选几何/路径打包进现有 observer 工作流、
4 GiB 峰值预算内的批大小、GPU 失败回退 CPU、以及全国端到端 A/B
（12 张量逐位一致 + 空闲受控计时）。接线仍 gated：本原型证明的是
kernel 级可行性与开销结构，不是端到端产品加速。

复现：

```bash
cd engine && cargo build --release --example dump_metal_realdata
./target/release/examples/dump_metal_realdata \
  ../output/clicktest-20261113/profile-fixed-20261114T144728/request.json \
  ../output/performance-20261114/after-china-r0/run-000/manifest.json \
  ../output/performance-20261114/metal-realdata
cd ../output/performance-20261114/metal-prototype
# metallib 重编（如需）：Metal toolchain cryptex 下 metal -c -fno-fast-math
#   -ffp-contract=off，再 metallib 链接（见 5c）
cargo build --release --offline
./target/release/realdata ../metal-realdata ../metal-realdata-result.json
./target/release/pathbatch ../metal-realdata ../metal-pathbatch-result.json
```

剩余缺口（5g 后）：产品接线设计与全国端到端 A/B（需空闲受控窗口）；
空闲 10 核受控重复 A/B（机器持续繁忙，外部条件）；GUI Swift 层实际
点击与真实下载路径验收（需 UI 自动化/网络条件）；SkyBuffers 块化
（低优先级，设计已完成）。

### 5h. 全国真实结构逐样本验证 — 2.22 亿值 0 位差，收益上限如实收窄

5g 的批处理原型用的是「每查询 37 节点」的假设几何。进一步核对冻结
run 的真实结构后发现两点关键事实：

1. **共享太阳光路缓存在全国 run 中实际被跳过**：冻结 samples 含
   287,885 个不同方位角（每观察者方位各异），超过 `MAX_BEARINGS=16`，
   `shared_solar_path_nodes/node_sets/samples` 全部 0.0ms（见
   after-china-r0.log）。气溶胶热路径实际走逐样本
   `cached_aerosol_path_cache`，不是共享缓存构建。
2. 真实节点数为 **38**（step=27.75km，max_trace=1000km，
   ceil(36.04)+1），不是 37。

据此新增 `nationbuild` 原型：从冻结 samples.npy 用生产
`temporal_cache_key` Quantized120 公式重新导出 **195 个时间键**
（与生产诊断 unique_keys=195 完全一致），365,794 个有效样本按键分组，
每样本沿**自己的真实方位角**做球面大圆偏移生成 38 个节点
（`DestinationPathGeometry::target_point_with_trig` 同公式），
域外节点按生产 `cell_for_point=None` 保持默认零值。融合严格 kernel
`aerosol_shared_build_fused` 镜像逐节点合同：时间混合（含 AOD 的
NaN 分支）→ 4 邻居插值 → **完整 is_valid**（非 fast-path）→
全剖面归一化 → 地形 support。

实测（Apple M1 Max，忙机，195 键全量）：

- **222,402,752 个输出值全部 0 位差**（195 键逐键全量 CPU 比对，
  NaN 按语义一致判定；另有 GPU/CPU 双校验和交叉核对通过）；
- GPU 派发合计 0.32s vs 单核 CPU 参考 1.06s（**~3.3×**），
  几何打包 0.59s（CPU，逐样本真实方位角，无法跨键复用）；
- 证据 `metal-nationbuild-result.json`、`metal-nationbuild.log`。

**收益上限如实收窄**：该段（逐样本气溶胶列+归一化）单核参考仅
~1.06s，10 核并行下 ~0.1–0.3s，对照全国 assessment ~420s 占比
<0.1%。profile 中 24% busy 的气溶胶组大部分是**逐节点消光取值与
路径缓存查表**（raw_value_at_height / extinction_km_inv_from_path_cache
/ cached_aerosol_path_cache 的查表与守卫），不是本 kernel 覆盖的
列构建段；且逐样本方位角各异使几何打包成本（0.59s）与 GPU 收益
（~0.7s）同量级。**结论：全国规模下该批处理接线的端到端收益上限
很小，不构成产品接线的充分理由；5g 的 35–47× 是规则几何大批处理
的 kernel 级吞吐，不能外推到全国逐样本真实结构。** Metal 路线的
数值可行性与位级一致性已完整证明（合成/真实/全国三层全绿），
但「值得接线」的证据门槛（端到端速度）在当前输入结构下未满足。
若未来输入变为少方位角/可共享几何（如区域批量同方位扫描），
5g 的批处理收益才可能兑现。

复现：

```bash
cd output/performance-20261114/metal-prototype
cargo build --release --offline --bin nationbuild
./target/release/nationbuild ../metal-realdata \
  ../after-china-r0/run-000/samples.npy \
  /Users/xiaoyang/晓洋站点/firesky/output/clicktest-20261113/profile-fixed-20261114T144728/request.json \
  ../metal-nationbuild-result.json 195
```

剩余缺口（5h 后，均为外部条件或已证据化关闭）：空闲 10 核受控 A/B
（机器持续繁忙）；GUI Swift 层实际点击与真实下载路径（需 UI 自动化/
网络）；SkyBuffers 块化（低优先级，设计完成）；Metal 产品接线
（数值门槛全过，端到端收益门槛未过，按证据暂不接线）。

## 11-15 第六轮：4 核性能口径受控 A/B（上线不限制线程）

**口径修正（用户明确）**：产品上线**不限制线程**，生产代码一行不动
（rayon 全局池仍按机器可用核数）；**性能测试统一按 4 核口径分析改造
差距**（`RAYON_NUM_THREADS=4`）。此前几轮误把「4 核」理解为上线封顶，
已纠正：4 核只是评估透镜，不是产品默认。

机器 Apple M1 Max（10 核可用），忙机 load 4–11。冻结上下文
`FIRESKY_CONTEXT_NOW_UTC=2026-11-14T03:01:10.203946+00:00`。
before=baseline（189c8a07），after=PendingSolarCell 紧凑化（19241874），
comparator=verify（185c40c2）。每侧交叉顺序各 2 轮全国 + 2 轮缩减。

### 全国 4 核 A/B（36,685 点 / 365,794 样本）

| run | wall s | CPU s | 峰值 GiB | cpu/wall | load |
| --- | ---: | ---: | ---: | ---: | --- |
| before-r0 | 555.33 | 2061.73 | 3.2457 | 3.71 | 10.0→9.0 |
| before-r1 | 546.90 | 2039.83 | 3.2430 | 3.73 | 7.5→8.3 |
| after-r0 | 532.87 | 2042.75 | 3.2444 | 3.83 | 8.3→9.5 |
| after-r1 | 531.64 | 2036.67 | 3.2538 | 3.83 | 9.4→8.3 |
| **均值 before** | **551.11** | **2050.78** | **3.2444** | | |
| **均值 after** | **532.26** | **2039.71** | **3.2491** | | |

wall **-3.42%**，CPU **-0.54%**，峰值 **+4.8 MiB**（基本持平）。

**位级一致性（全绿）**：全国 r0/r1、before-r1 vs after-r1 三组比较
raw assessment（60,163,400 值）+ raw samples + semantic 全等，
confidence 0 位差，排行/最佳步 0 变化；缩减 r1/r2 同样全等。

**结论（如实）**：wall -3.42% 但 CPU 仅 -0.54%、峰值内存持平，且
after 的 cpu/wall 更高（3.83 vs 3.71–3.73）——说明 after 在略低竞争
窗口里把核用得更满，wall 差是**负载窗口噪声，不是稳定加速**。
CPU 时间差 -0.54% 在忙机噪声内。**PendingSolarCell 紧凑化在 4 核
全国口径下位级精确、内存中性，无稳定 CPU/wall 收益**，与 10 核结论
一致。4 核相对 10 核权威 wall 432.78s 慢约 27%（551 vs 433），符合
核数减少的预期；4 核 cpu/wall≈3.7–3.8（10 核忙机仅 5.1）。

证据 `output/performance-20261114/cores4/cores4-ab-report.json`、
`cores4/china-*-r*/summary.json`、`cores4/*-compare.json`。

复现：

```bash
RAYON_NUM_THREADS=4 FIRESKY_CONTEXT_NOW_UTC=2026-11-14T03:01:10.203946+00:00 \
  FIRESKY_NATIVE_DIAG_SHARED_MOTION_KEYS=1 \
  output/performance-20261114/{baseline,after}/bench_regional_profile \
  run output/clicktest-20261113/profile-fixed-20261114T144728/request.json \
  output/performance-20261114/cores4/china-{before,after}-rN 1
# 缩减集用 run-reduced ... 1 2322；比较用 verify/bench_regional_profile compare
```

剩余缺口（第六轮后）：空闲受控窗口（机器持续繁忙，4 核 A/B 已在
忙机交叉顺序下完成，位级全绿、收益结论如实）；GUI Swift 实际点击 +
真实下载（需 UI 自动化/网络）；SkyBuffers 块化与 Metal 产品接线
（均已证据化评估，收益门槛未过，暂不实现）。

## 11-15 第七轮：算法级审计（回答「算法是否有问题、还有没有可优化逻辑」）

完整报告见 `docs/archive/ALGORITHM_AUDIT_2026-11-15.zh-CN.md`。口径不变：上线不
限制线程、生产代码一行不动；4 核（`RAYON_NUM_THREADS=4`）忙机分析透镜。
本轮除新建只读证据工具 `engine/examples/audit_algorithm_counts.rs` 外未改
生产代码。

### 结论（三类）

1. **算法健全，未发现真实算法缺陷。** 无错误物理、无位级门槛下可大规模
   移除的冗余。时间键不守恒是已记录、已提供 opt-in Consistent120 的近似
   自洽问题，非新缺陷。
2. **位级安全（精确）复用已基本耗尽。** 365,100 个 (时间键,方位) 对中
   364,408 是单例（均值 1.002 样本/对）；平行光线共享格但双线性权重各异，
   精确 (corners,weight-bits) 复用 0.0%。精确光路缓存
   （`solar_path_exact_key`，national.rs:4619）在此输入命中率≈0。
3. **剩余主成本是每节点固有物理**：球面几何三角占 libm 42.5%
   （`target_point_with_trig`，national.rs:1440，7301/17194 样本）、气溶胶
   柱插值+归一化、云阻挡 powf/expf。是真实计算量，非冗余。

### 工作量与热点（4 核忙机，365,794 样本）

- 太阳光线 738,214,725 条（每样本≈2018）；单层 36.2% 已走快路径
  （`solar_cloud_base_light_uncached`，national.rs:9416）。
- 行级 self：assess_observer_sky_v2_internal 134,966（L10422 evaluate_scalar
  82,057）、solar_cloud_base_lights_uncached 104,084、cached_aerosol_path_cache
  34,735、cached_horizontal_path_node 22,838（L5259 几何 12,977）。
- 样本逐位校验：重生成 timeline 与冻结 samples 仅 field 5 不同（真实 AOD
  占位差异），其余 8 字段 0 差异，证明审计 harness 精确复现生产输入。

### 唯一大杠杆（近似，需审批）

方位角量化共享太阳光路缓存。1° 桶→2547 组（143.6 样本/组）、0.5°→4809、
0.25°→9381、0.1°→22360。**生产已有 opt-in 近似原型**
`solar_path_template_key`（national.rs:4551，1° 方位 + 8×8 格块 + 60s 时间片
+ 0.02 权重桶，上限 8192），由 `FIRESKY_NATIVE_ENABLE_SOLAR_PATH_TEMPLATE_CACHE`
启用（national.rs:2565），源码注释明确为近似、默认关闭。它改变位级结果，
与已被拒的量化时间键同类：**默认化前必须科学容差证据 + 全量 A/B**，不能
仅凭分组数宣称收益。注释提到的 exact relative-path 替代方向位级安全，但受
唯一性限制复用潜力同样≈0。

### 证据与复现

`output/performance-20261114/algorithm-audit-20261115/audit-summary.json`
（含 counts-china{,-v2}、key-bearing-stats、line-self-attribution）、
`hotspot-sample-20261115/libm-attribution.json`。复现：

```bash
RAYON_NUM_THREADS=4 FIRESKY_CONTEXT_NOW_UTC=2026-11-14T03:01:10.203946+00:00 \
  ./engine/target/release/examples/audit_algorithm_counts \
  output/clicktest-20261113/profile-fixed-20261114T144728/request.json \
  <BUNDLE_MANIFEST> <OUT.json>
```

剩余缺口（第七轮后）：空闲受控窗口（机器持续繁忙）；GUI Swift 实际点击 +
真实下载（需 UI 自动化/网络）；方位角量化共享光路缓存的科学容差评估与
全量 A/B（需用户接受近似后才启动）。位级安全优化已耗尽，不为微优化做大重构。

## 11-15 第八轮：近似模板缓存 4 核 A/B 实测（回答「近似能加速多少」）

用户要求量化近似路线的加速。用第七轮确认的 opt-in 近似原型
`FIRESKY_NATIVE_ENABLE_SOLAR_PATH_TEMPLATE_CACHE`（1° 方位桶 + 8×8 格块 +
60s 时间片 + 0.02 权重桶，national.rs:4551/2565）做 A/B：**同一冻结二进制
（19241874）两侧只差这一个环境变量**，生产代码零改动、默认行为不变。
0.25° 生产网格、单模型、冻结中国黄昏 36,685 点 / 365,794 样本；
`RAYON_NUM_THREADS=4` 忙机（load 4–10），交叉顺序各 2 轮全国 + 2 轮缩减。

### 全国 4 核 A/B

| run | wall s | CPU s | assess s | 峰值 GiB | load |
| --- | ---: | ---: | ---: | ---: | --- |
| exact-r0 | 558.8 | 2052.8 | 548.8 | 3.250 | 5.4→9.0 |
| exact-r1 | 550.8 | 2052.9 | 540.0 | 3.248 | 10.0→8.1 |
| template-r0 | 443.4 | 1675.4 | 433.2 | 3.238 | 9.2→9.7 |
| template-r1 | 446.3 | 1676.9 | 436.1 | 3.230 | 9.7→10.0 |

**CPU -18.4%（2052.9→1676.2s），assessment -20.2%，wall -19.8%
（554.8→444.9s），峰值 -15MiB（持平）**。可信度：exact 两轮 CPU 差
0.005%、template 两轮差 0.09%，且 exact 与第六轮基线（2050.8s）一致；
-18% 远超忙机噪声（第六轮同法噪声 ±0.5%）。wall 受负载窗口影响，
以 CPU 口径为准。缩减集（256 点）CPU -7.9%、assess -13.0%：小区域
共享机会少，收益随规模增大。

### 近似代价（同输入逐位比较，r0/r1 结果完全一致=确定性）

| 指标 | 全国 36,685 点 |
| --- | --- |
| 分数有变化的点 | 22,986（62.7%） |
| 事件分偏差 | 平均 0.0061 分，最大 0.313 分（0–100 量纲） |
| 峰值分偏差 | 平均 0.0119 分，最大 0.700 分 |
| 最佳时刻步改变 | 3,901 点（10.6%） |
| 质量等级改变 | 2,303 点（6.3%）；峰值等级 3,028（8.3%） |
| 主导云族改变 | 1,403 点（3.8%） |
| 最佳火烧窗口时长 | 6,371 点变化，平均 1.0 分钟，最大 25 分钟 |
| 排行位置变化 | 36,662 点（99.9%，多为微小分差引起的名次互换） |
| confidence / anchor | 0 变化 |

raw assessment 位差 6,195,674/60,163,400（10.3%）。**这不是位级一致，
是真实语义偏移**：约每 10 个点有 1 个最佳时刻步改变、每 16 个点有 1 个
质量等级改变。是否可接受属科学/产品容差决策，不是工程决策。

### 结论与门槛

- **近似模板缓存实测：4 核全国 CPU -18.4% / wall -19.8%，内存持平，
  代价如上表。** 默认化仍需用户/科学容差审批；未审批前保持 opt-in，
  生产默认一行未动。
- 若接受该量级偏移，后续可做：容差扫描（0.5°/0.25° 桶更细、块更小
  会减小偏移但降低命中）、跨日重放验证偏移分布稳定性、GUI 验收。
- 本轮全部为忙机观察；空闲受控窗口与 GUI 实际点击仍是外部条件缺口。

证据 `output/performance-20261114/template-ab-20261115/`：
`template-ab-report.json`（汇总）、`china-*-r*/summary.json`、
`{china,reduced}-r{0,1}-compare.json`、`driver.log`、`run_ab.sh`、
`aggregate.py`。复现：

```bash
zsh output/performance-20261114/template-ab-20261115/run_ab.sh
python3 output/performance-20261114/template-ab-20261115/aggregate.py
```
