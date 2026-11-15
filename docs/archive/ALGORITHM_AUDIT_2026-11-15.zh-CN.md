# FireSky 算法审计（2026-11-15，第七轮）

回答用户的问题：「没有可优化的逻辑了吗？在我们算法中，分析算法是否有问题。」

本轮是**算法级审计**，不只工程层。结论分三类：位级安全优化、近似（需科学
容差审批）、真实算法缺陷。口径遵守用户三次纠正：**上线不限制线程、生产
代码一行不动；性能测试统一按 4 核（`RAYON_NUM_THREADS=4`）分析改造差距。**
本轮除新建只读证据工具 `engine/examples/audit_algorithm_counts.rs` 外，
未改任何生产代码。

## 0. 结论（先说答案）

1. **算法本身健全，未发现真实算法缺陷。** 没有错误物理、没有可在位级一致
   门槛下大规模移除的冗余计算。时间键不守恒（左右秒独立量化）是已记录、
   已提供 opt-in `Consistent120` 的近似自洽问题，不是新缺陷。
2. **位级安全（精确）复用已基本耗尽。** 跨样本/跨光线的精确复用潜力≈0：
   每样本的 (时间键, 方位角) 几乎唯一，平行光线虽共享格但双线性权重各不
   相同。已有的精确缓存（共享 motion、精确光路缓存、重复样本缓存）都在
   正确的位置，没有「漏掉的大块精确复用」。
3. **剩余主成本是每节点的固有物理**，不是冗余：球面几何三角（占 libm
   42.5%）、气溶胶柱插值+归一化、云阻挡 `powf`/`expf`。这些是真实计算量。
4. **唯一的大杠杆是方位角量化共享太阳光路缓存，属近似，需科学容差审批。**
   代码里已有 opt-in 原型（`FIRESKY_NATIVE_ENABLE_SOLAR_PATH_TEMPLATE_CACHE`，
   1° 方位 + 8×8 格块 + 60s 时间片 + 0.02 权重桶），源码注释明确标注为近似、
   未默认启用。它与已被拒的「量化时间键」同类：会改变位级结果，必须先有
   科学容差证据才能谈默认化。

## 1. 口径与负载

- 机器 Apple M1 Max（10 核可用），**忙机 load 7–11**；所有时间为忙机观察，
  不是空闲受控验收，不能写成稳定提升。
- 4 核分析透镜：`RAYON_NUM_THREADS=4`。冻结上下文
  `FIRESKY_CONTEXT_NOW_UTC=2026-11-14T03:01:10.203946+00:00`。
- 负载：冻结中国黄昏，**36,685 观察点 / 365,794 有效样本 / 145,209 格 /
  8 时刻**，0.25°、单模型。请求
  `output/clicktest-20261113/profile-fixed-20261114T144728/request.json`。
- 证据汇总 `output/performance-20261114/algorithm-audit-20261115/audit-summary.json`。

## 2. 审计方法（可复现）

新建只读证据工具 `engine/examples/audit_algorithm_counts.rs`：用生产
decode/derive/CAMS/timeline 合同重放冻结输入，再调真实
`assess_batch_with_cancel` 带诊断计数，**不改评分**。它精确复现生产输入：

- **样本逐位校验**：重生成 timeline 与冻结 samples 逐位比对，6,603,300 个
  字段中**仅 field 5 不同**（365,794 处全在 field 5，其余 8 字段 0 差异）。
  field 5 是 `assessment.rs` 在 `timeline::prepare` 之后填入的真实 AOD
  （占位 0.12），属预期差异。证明 harness 与生产输入一致。
- 两次全国实测（`counts-china.json` / `counts-china-v2.json`）：
  `assess_batch` wall≈528–530s，`gfs_decode_derive`≈7.3s，timeline≈0.04s。

复现：

```bash
RAYON_NUM_THREADS=4 FIRESKY_CONTEXT_NOW_UTC=2026-11-14T03:01:10.203946+00:00 \
  ./engine/target/release/examples/audit_algorithm_counts \
  output/clicktest-20261113/profile-fixed-20261114T144728/request.json \
  <BUNDLE_MANIFEST> <OUT.json>
```

## 3. 工作量画像（算法在算什么）

| 量 | 值 | 说明 |
| --- | ---: | --- |
| 太阳光线总数 | 738,214,725 | 每有效样本≈2018 条 |
| 单层光线 | 266,910,184（36.2%） | 走单层快路径 |
| 双层光线 | 339,727,253 | |
| 三层光线 | 131,577,288 | |
| 共享 motion 时间键 | 195 | 2.215 GiB，与前轮一致 |
| 唯一方位角 | 287,885 | 每观察点方位各异 |
| (键,方位) 对 | 365,100 | 均值 1.002 样本/对 |
| 前向格总数 | 981,449,927 | 半径格 192,452,293、边界带 10,906,640 |

每样本≈2018 条光线×38 节点是真实物理追踪量（0.25° 格、step≈27.75km、
max_trace 1000km）。**36.2% 的光线只命中单层**，已走
`solar_cloud_base_light_uncached`（national.rs:9416）单层快路径，避免三层
融合开销；这是已实现的算法优化，不是遗漏。

## 4. 热点归因（成本花在哪）

行级 self 采样（`line-self-attribution.json`）与 libm 归因
（`hotspot-sample-20261115/libm-attribution.json`）：

| 函数 | self 样本 | 主要行 |
| --- | ---: | --- |
| `assess_observer_sky_v2_internal` (9863) | 134,966 | L10422 `evaluate_scalar` 调用 82,057 |
| `solar_cloud_base_lights_uncached` (8754) | 104,084 | L8779 单层分派 33,831、L9012 消光 14,458、L9172 云阻挡 10,519 |
| `cached_aerosol_path_cache` (5530) | 34,735 | L5562 调 `interpolated_aerosol_column` 25,337 |
| `cached_horizontal_path_node` (5122) | 22,838 | L5259 `target_point_with_trig` 12,977 |
| `interpolated_aerosol_column` (5469) | 16,183 | 4 邻居插值 + 剖面归一化 |

libm self 共 17,194 样本，**`target_point_with_trig`（national.rs:1440，
球面大圆几何 asin/atan2/sin）= 7,301 = 42.5% of libm**。按符号：
atan2f 2924、sinf 2649、asinf 2483、hypotf 2404、`__sincosf_stret` 1859、
powf 1757、expf 1269、cosf 763、logf 627、tanf 459。

解读：主成本是**每节点固有物理**——球面几何三角、气溶胶柱插值+归一化、
云阻挡 `powf(1.35)`/`expf`。这些不是冗余，是真实计算量。

## 5. 精确复用潜力≈0（为什么「没有可优化的逻辑」）

`key-bearing-stats.json`：

- 195 量化时间键 × 287,885 唯一方位 = 365,100 个 (键,方位) 对，
  **364,408/365,100 是单例**（多重度直方图：1→364408、2→690、3→2），
  均值 1.002 样本/对，≥2 样本的对仅覆盖 1,386 个样本（0.38%）。
- 36,685 个 observer cell 全唯一（每 cell 最多 1 样本）。
- **精确光路缓存**（`solar_path_exact_key`，national.rs:4619，键含
  target_cell + 方位位 + 权重位 + 左右秒位）在此输入下命中率≈0：
  每样本的 target_cell+方位几乎唯一。源码注释（national.rs:4667 附近）
  已写明「national batches rarely revisit an identical target key」。
- **平行光线探针**（observer 35.75N 82.25E，太阳方位 246.418°）：
  2,497 前向目标、92,389 节点、5,663 不同格（16.31 节点/格），但
  **精确 (corners, weight-bits) 复用 = 0.0%**。平行光线共享格，但每条
  光线的双线性权重各不相同——几何复用必须近似，无法位级精确。

结论：在位级一致门槛下，**跨样本/跨光线的精确复用已被现有缓存吃尽**，
没有「漏掉的大块精确复用」。共享 motion 缓存（195 键、1876× 复用）、
单层快路径、NEON blocking、observer path batch/slab、angular footprint
cache、unified projection sort、前向半球门、重复样本缓存、observer work
canonical 去重——都已在正确位置生效。

## 6. 三类结论

### 6.1 位级安全优化（小 / 已耗尽）

- 精确复用：≈0（见 §5），无大块可移除冗余。
- 已实现的位级安全优化均已生效（§5 末段清单）。
- 剩余位级安全空间是**微优化**（如减少每节点守卫/查表分支），量级小，
  且第五轮 5h 已证明气溶胶列构建段在全国占比 <0.1%，不值得大改。
- 浮点运算顺序 / libm / 时间量化 / 云层抖动 / 经验评分：**不改**（位级门槛）。

### 6.2 近似（需科学容差审批）

**唯一大杠杆：方位角量化共享太阳光路缓存。** 若把方位角分桶，可让大量
样本共享同一条太阳光路（几何+云层），潜在收益大。分桶场景
（`bearing_bin_scenarios`，仅键+方位分组，未含 target 块/时间片/权重桶）：

| 方位桶 | 组数 | 均值样本/组 | ≥2 样本组覆盖 |
| --- | ---: | ---: | ---: |
| 1.0° | 2,547 | 143.6 | 365,771 |
| 0.5° | 4,809 | 76.1 | 365,757 |
| 0.25° | 9,381 | 39.0 | 365,645 |
| 0.1° | 22,360 | 16.4 | 364,710 |

**代码现状（重要，避免误判为「未探索」）**：生产已有 opt-in 近似原型
`solar_path_template_key`（national.rs:4551）：1° 方位桶
（`SOLAR_PATH_TEMPLATE_ANGLE_BIN_DEG=1.0`，L587）+ 8×8 格块
（`SOLAR_PATH_TEMPLATE_BLOCK_SIZE=8`）+ 60s 时间片 + 0.02 权重桶，
缓存上限 8192。由 `FIRESKY_NATIVE_ENABLE_SOLAR_PATH_TEMPLATE_CACHE`
启用（national.rs:2565），源码注释明确：「The block/angle template is an
approximation because it stores absolute path cells. Keep it opt-in until
it is replaced by an exact relative-path cache.」**默认关闭。**

- 它复用整条光路（几何+云层），比单纯方位分组更激进；实际命中率取决于
  块/时间片/权重桶的联合分布，本轮未单独实测其命中率。
- 它**改变位级结果**（绝对格存储 + 方位/时间/权重量化），与已被拒的
  「量化时间键」同类。**默认化前必须：科学容差证据 + 全量 A/B + 位级/
  语义差异评估**，不能仅凭分组数宣称收益。
- 注释提到的「exact relative-path cache」替代方向是位级安全的，但受 §5
  的唯一性限制，精确复用潜力同样≈0。

**第八轮已实测（见路线图第八轮节）**：同一冻结二进制、4 核忙机、
交叉顺序各 2 轮全国 A/B，仅开关此环境变量：**CPU -18.4%、wall -19.8%
（554.8→444.9s）、内存持平**；代价：62.7% 的点分数变化（平均 0.006 分、
最大 0.31 分），10.6% 的点最佳时刻步改变，6.3% 质量等级改变。
默认化仍待科学/产品容差审批。证据
`output/performance-20261114/template-ab-20261115/template-ab-report.json`。

### 6.3 真实算法缺陷

**未发现。** 逐项核对：

- 球面大圆几何（`target_point_with_trig`）：标准公式，距离三角已按 index
  缓存（`cached_path_distance_trig`，L2922），方位三角已缓存
  （`cached_solar_bearing_trig`，L2952）。无重复计算。
- 气溶胶归一化（`normalization_integral`/`raw_integral`，L698）：对完整
  标准大气剖面归一（非地形裁剪支撑），注释说明这是为避免高地形格把地下
  份额摊到薄层而抬高消光——**有意设计，非 bug**。
- 云阻挡（`cloud_optical_depth_increment`，L615：`powf(1.35)`；
  `cloud_transmission_from_path`：`expf` + 峰值修正）：经验合同，
  峰值修正是亚格经验项，注释明确「no-double candidate was rejected by
  cross-day replay」。无科学证据前不改。
- 时间键独立量化（`temporal_cache_key`，L1067）：左右秒不守恒是**已记录**
  的近似自洽问题，已提供 opt-in `Consistent120`；不是本轮新发现，默认切换
  仍需全量 A/B + 科学容差。
- RK2 中点轨迹、单格边缘 clamp、双线性插值、地形离散、经验评分、CAMS
  aerext532→AOD550 归一：均为文档化合同/有意设计（见路线图算法替换矩阵），
  无新缺陷。

## 7. 建议与门槛

1. **维持现状**：位级安全优化已耗尽，算法健全，不为微优化做大重构。
2. **若用户愿意接受近似**：把方位角量化共享光路缓存作为唯一候选大杠杆，
   先做科学容差评估（量化方位对评分/排行的影响分布），再全量 A/B；
   通过后才谈默认化。这与量化时间键的审批门槛一致。
3. **不宣称稳定提升**：本轮全部为忙机 4 核观察；空闲受控窗口、GUI 实际
   点击 + 真实下载仍是外部条件缺口。

## 8. 证据索引

- `output/performance-20261114/algorithm-audit-20261115/audit-summary.json`（汇总）
- 同目录 `counts-china{,-v2}.json/.log`、`key-bearing-stats.json`、
  `line-self-attribution.json`
- `output/performance-20261114/hotspot-sample-20261115/libm-attribution.json`
- 工具 `engine/examples/audit_algorithm_counts.rs`（只读，不改评分）
- 生产源码 `engine/src/native/national.rs`（行号见正文；含用户既存大改动，
  本轮未 revert、未改生产代码）
