# SkyBuffers 角向场块化设计（2026-11-15，设计稿，未实现）

状态：设计完成，等待实现授权。本文不改任何生产代码。
依据：第五轮性能验收后的剩余缺口清单；`engine/src/native/national.rs` 当前源码。

## 0. 第五轮后续量化（2026-11-15，先测后做的结论）

对带符号全国 profile（`hotspot-sample-20261115/sym-china-sample.txt`）
做调用栈归因后：

- `_platform_memset_pattern16` 共 1,945 个 self 采样，**全部**落在
  `solar_cloud_base_lights_uncached` 内；`_platform_memmove` 共 2,982，
  其中 2,904 同样在该函数内。两者合计 4.76% busy，是 reset 写零 +
  云层临时缓冲的**共同上限**，不是 reset 独占。
- `SkyBuffers::reset` 在 LTO 下被完全内联，nm 无独立符号，采样中无
  reset 函数体行号（national.rs:2684–2716）的独立 self 采样——其
  `fill(0.0)` 成本已摊进调用者与 memset/memmove，无法单独剥离出
  显著占比。
- 写入路径 `for_each_angular_cloud_cell_precomputed` 是稀疏的（只碰
  云足迹覆盖的 azimuth/elevation bin），脏块 reset 语义安全；但
  **收益上限被 4.76% 的 memset+memmove 总占比压住**，扣除云层临时
  缓冲后 reset 独占部分更小，且忙机噪声同量级。

**结论：阶段 A（脏块 reset）与阶段 B（块化存储）在当前全国输入下
均无足够收益证据，按路线图「先测占比再重构」门槛暂不实现。** 设计
保留如下，若未来 profile 显示 reset/memset 占比显著上升（如更大
角向分辨率或更密云层）再启动。此结论与 5h 的 Metal 端到端收益收窄
一致：剩余可优化段要么占比小、要么被外部条件（空闲受控窗口）挡住。

## 1. 问题与量化

每个 rayon worker 持有一份 `SkyBuffers`，其中角向天空场按
`VIEW_ANGULAR_CELLS = 36 × 90 = 3240` 格稠密分配。逐字段清点（`SkyBuffers`
L2509–2533、`new()` L2636–2660、`reset()` L2684–2716）：

- 24 个 `Vec<f32>` 场：sky_view/sky_burn/sky_load/sky_material_load/
  sky_incoming_aerosol_load、observer_aerosol_optical_depth/transmission、
  layer_occupied（3×3240 展平）、ledger_view/ledger_target_eligible/
  ledger_geometry_reachable/ledger_external_path_transmission、
  unoccluded_burn_upper、incoming_blocked、target_deck_connected/
  target_deck_attenuated、target_deep_risk、cloud_optical_depth、
  peak_blocking、clear_gap_count；
- `layer_view`/`layer_burn` 各 `[Vec<f32>; 3]`。

合计 **28 个 3240 格 f32 场 ≈ 362,880 B ≈ 354 KiB/worker**，10 worker
约 3.5 MiB。这比路线图早期「~140 MiB/worker」的估计小两个数量级：
那份估计对应的是旧版含全网格 motion/几何缓存的布局，第五轮源码里这些
已改为共享表（`SharedMotionCache` 等）或按需 slab。**块化的内存收益上限
只有几 MiB，不是 4 GiB 峰值的主项**（主项是 2.2 GiB 共享 motion 表 +
解码网格 + 输出）。

真正的潜在收益在 `reset()`：每个有效样本（全国 365,794 次）都对 28 个
场做全量 `fill(0.0)`，约 **354 KiB/样本 × 365,794 ≈ 123 GiB 累计写**。
这些场每 worker 只有 354 KiB，基本驻留 L2/LLC，实际成本是缓存写端口
与指令吞吐而非 DRAM 带宽；粗估每样本 ~90k 次 store，全国 ~3.3e10 次，
10 核分摊后可能是秒级到十几秒级，占 assessment 458 s 的百分之几。
**是否值得做必须先测 reset 占比，不能先验断言收益。**

## 2. 访问模式（决定块化是否保序）

- **写路径**：`for_each_angular_cloud_cell_precomputed`（L1928–1987）按
  `azimuth_bin` 外层、`elevation_bin` 内层递增访问，index =
  `azimuth_bin * 90 + elevation_bin`。每个云投影的写入顺序由几何决定，
  与存储布局无关。
- **读路径**：`reduce_angular_fields`（L3542–3603）按 index 0..3240
  严格递增做 f32 累加；这是唯一的归约入口（L10782 调用）。
- 没有其他模块直接索引这些场（rg 全量核对：声明/new/reset/reduce/
  写入闭包之外零引用）。

**关键不变量**：只要 (a) 每个 index 的写入序列不变、(b) 归约仍按
index 递增累加，块化就不改变任何浮点运算顺序。块大小取 90 的整数倍
（如 360 = 4 个方位 bin）可保持 index→(block, offset) 为纯除法/取模。

## 3. 设计方案（两阶段，先测量后重构）

### 阶段 A：脏块 reset（低风险，先做）

保持稠密布局不变，只把 `reset()` 的全量 fill 改为脏块追踪：

1. `SkyBuffers` 增加 `angular_dirty: [u64; 3240/64]` 位图（或按 360 格
   块的 `[bool; 9]`）与 `angular_reset_generation: u32`。
2. 写入闭包在首次触碰某块时置脏（每块每样本最多一次分支）。
3. `reset()` 只清脏块并清位图；全冷样本（无云）退化为 O(块数)。
4. 归约路径不变。

预期：reset 写带宽按实际天空覆盖率下降（晴天样本可能只碰 1–2 块）。
风险：写入路径新增分支可能抵消收益；必须 A/B 实测。

### 阶段 B：块化存储（仅当阶段 A 证明 reset 是热点）

把 28 个场改为 `AngularField`（`blocks: Vec<[f32; BLOCK]>` + 脏位图），
`index` 访问改为 `field.get(index)/get_mut(index)` 内联函数。触点清单：
声明 28 处、new 28 处、reset 28 处、reduce 内 ~30 处读取、写入闭包
~25 处 `+=`/赋值，共约 140 处，全部在 national.rs 单文件内。
`layer_occupied` 的 `layer * VIEW_ANGULAR_CELLS + index` 展平索引需改为
`(layer, index)` 二维访问，注意保持族间比较顺序。

## 4. 验收门槛（与路线图一致）

1. 缩减 256 点三轮 A/B：raw assessment/samples 逐位一致（compare 工具
   已具备置信度合同分类）；
2. 全国单轮：12 张量 SHA 一致、confidence 合同一致、峰值 <4 GiB；
3. 性能：空闲或同负载受控重复 ≥2 轮，wall/CPU 收益需超出忙机噪声才记录；
4. 完整 lib 测试通过；不整文件 rustfmt。

## 5. 当前结论

- 块化的**内存动机已被源码清点削弱**（3.5 MiB 而非 140 MiB），
  主要动机转为 reset 写带宽；
- 阶段 A 是下一个可执行实验，但需要受控负载窗口（当前机器持续繁忙，
  与空闲 A/B 同一外部约束）；
- 在测得 reset 占比之前，不做阶段 B 的 140 处重构。
- **11-15 源码级 profile 补充**：memset/memmove 合计仅 4.8% busy
  （含 reset 之外的拷贝），脏块 reset 收益上限约 1–3%；气溶胶组占 24%
  是更高优先级目标。块化降级为低优先级，先做气溶胶批处理原型。

复现清点：`rg -n "sky_view|ledger_view|layer_view" engine/src/native/national.rs`、
`VIEW_ANGULAR_CELLS` 定义 L238、reset L2684、reduce L3542。
