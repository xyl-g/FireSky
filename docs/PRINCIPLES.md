# 火烧云预测原理（PRINCIPLES）

> 本文档解释 firesky 引擎"从数值模式到档位"的完整物理与算法链路。所有数字与
> 公式均取自源码（文件:行号标注，行号对应算法版本 `2026.11.04.87`）。
>
> 公式以 LaTeX 书写，GitHub、VS Code、Obsidian、Typora 均可直接渲染。
> English version: [PRINCIPLES.en.md](PRINCIPLES.en.md)

## 0. 一句话原理

> 火烧云 = 「有一片朝昏时刻能被低角度太阳直接照到的云，且观测者看得见它」。

引擎用数值模式的云柱回答"云在哪、多厚、透不透"，用光线追踪回答"照不照得到、
看不看得到"，用经验曲线把物理量翻译成人能理解的档位。

## 1. 总体数据流

```
GFS 子集 (0.25°, 1h~3h) ─┐
EC 开放数据 (第二模式)   ─┼─► 云柱派生（三族云几何/透明度/深云风险）
CAMS 气溶胶 (11 层)      ─┘            │
                                       ▼
太阳几何（astral + 折射）──► 原生光线追踪（Rust）：太阳入射腿 + 观测者返程腿
                                       │
                                       ▼
                            经验评分层（锚点映射/窗口积分/响应曲线）──► 档位
```

- **事件窗**：锚定日落/日出时刻（astral 计算，再叠加太阳视半径与折射修正）。
  朝霞取偏移 $-75\ldots+20$ 分钟、晚霞取 $-20\ldots+75$ 分钟，每 5 分钟一步，
  各 20 步（`national_batch.py:123-124`）。
- **分析半径**：单点与区域统一 1000 km（`analysis_constants.py:20-21`）。这是数据
  包络，不是"每个云都参与评分"的承诺——可见性由光线追踪逐云解决。

## 2. 符号表

| 符号 | 含义 | 单位 |
|---|---|---|
| $A$ | 云量（模式给出的水平覆盖比例） | 1 |
| $o$ | 不透明度（云中光学厚度的相对代理） | 1 |
| $z_b,\;z_t$ | 云底 / 云顶海拔 | m |
| $\theta_s$ | 太阳几何仰角 | ° |
| $\theta_{\rm ill}$ | 受光仰角 $=\theta_s+\rho(\theta_s)+0.2667°$ | ° |
| $\tau_c$ | 云光学深度（沿光路积分） | 1 |
| $\tau_a$ | 气溶胶光学深度（沿光路积分） | 1 |
| $T$ | 透过率 $e^{-\tau}$ | 1 |
| $b$ | 可烧比例（几何可烧 × 自遮蔽透过） | 1 |
| $\iota$ | 入射腿 AOD 受光因子 | 1 |
| $c$ | 返程腿 AOD 对比度因子 | 1 |
| $m$ | 材质质量 $L_M/(2.5A_{\rm burn})$ | 1 |
| $q$ | 最终质量指数，值域 $[0,\,2.5]$ | 1 |
| $R$ | 地球半径 $6371$ km（纯几何） | km |
| $R_{\rm eff}$ | 有效地球半径 $\tfrac{7}{6}R$（仅视线仰角用） | km |

## 3. 数据层：从 GRIB 到"三族云"

### 3.1 模式量 → 云族

GFS 每个压层（200–925 hPa）给出云量（`tcc`/层云量）、凝结物（CLWMR/ICMR）、
相对湿度、温度、风。派生规则：

1. **族归属**：按压层压力划入高（$p\le400$ hPa）/ 中（$400<p\le700$ hPa）/
   低（$p>700$ hPa）三族（`national_batch.py:506-507`）。400 hPa 在存在上层强
   支持时改判为中云，避免把连续深厚云体误判成卷云（`national_batch.py:1906-1919`）。
2. **成员显著性**：族内峰值所在层必进几何；其余层需**同时**满足 $A\ge0.10$ 且
   $A\ge0.20\times$ 族峰值（`national_batch.py:2755-2770`）。薄的伴随层不进几何，
   只通过联合透明度起作用。
3. **甲板几何**：族内取包络，再与模式自带的层底/层顶**取并集**
   （`national_batch.py:2886-2907`）：
   $$z_b=\min\!\left(\min_{i\in\mathcal M}z^{\rm lower}_{i},\;z_b^{\rm model}\right),
     \qquad
     z_t=\max\!\left(\max_{i\in\mathcal M}z_{i},\;z_t^{\rm model}\right)$$
   其中 $\mathcal M$ 是显著成员集合，$z^{\rm model}$ 缺字段时该分支退化为恒等；两个
   上界都已在各自的生成处被模式地形夹紧。取并集是保守方向：宁可多算一点阻挡，也
   不放过一层挡光的云。这是有意保留的契约：**两个实质甲板保留合并边界**，各自作为
   独立的阻挡证据
   （`test_contiguous_pressure_levels_count_once_as_one_cloud_deck`）。
4. **联合不透明度**：同一族内的多个甲板按串联透过率叠加，每个甲板取其显著成员的
   最大不透明度（`national_batch.py:2768-2777`）：
   $$o_{\rm fam}=1-\prod_{j\in{\rm decks}}\left(1-\max_{i\in j}o_i\right)$$
5. **深云风险**：跨族连续、且液/冰凝结物丰厚的对流砧状厚云单独量化为
   $r_{\rm deep}$，用于"太阳照不进去"的深云惩罚（`national_batch.py:1760-1830`）。

单层不透明度本身是相对湿度与凝结物的混合代理
（`national_batch.py:1673-1705`）：

$$o=\mathrm{clamp}_{01}\!\left(0.22\,u_{\rm rh}+0.78\,u_{\rm op}\right),\qquad
u_{\rm rh}=\mathrm{clamp}_{01}\!\left(\frac{{\rm RH}-82}{16}\right)$$

$$u_{\rm op}=\mathrm{clamp}_{01}\!\left(0.35+0.65\,u_{\rm cond}\right),\qquad
u_{\rm cond}=\mathrm{clamp}_{01}\!\left(\frac{\log_{10}q_{c}+8}{3}\right)$$

$q_c$ 为液水 + 冰水混合比（kg/kg）。相对湿度始终占 22% 权重（它区分灰霾背景与
真正能占满天空的云）；$u_{\rm op}$ 的前置常数 $0.35$ 只在模式给出云量分数时生效，
仅有凝结物时退化为裸的 $u_{\rm cond}$，两样都没有则整层不透明度记 0。

### 3.2 云底几何：层间中点 + 模式边界高度

**被评分的原生路径**不使用固定层深表：

1. 压层中心高度 $z_i$ 由模式位势高度给出；层下界取与下一层的中点，并被模式地形
   夹紧（`national_batch.py:2845-2856`）：
   $$z^{\rm lower}_{i}=\max\!\left(\frac{z_i+\max\left(z_{i+1},\,z_{\rm surf}\right)}{2},\;
     z_{\rm surf}\right)$$
   顶层没有下一层，回退到固定层深 $z_i-\Delta_i$（`_LEVEL_DEPTH_M`，
   `national_batch.py:494-498`）。
2. 族包络优先采用 **GFS 自带的云层底/顶气压面**（`*CloudLayerBottom/Top`），按
   对数气压插值到几何高度（`national_batch.py:1092-1211`）：
   $$z(p)=z(p_a)+\left(z(p_b)-z(p_a)\right)\frac{\ln p-\ln p_a}{\ln p_b-\ln p_a}$$
   仅在缺字段时才回退到第 1 步的中点包络。

> **与旧版文档的差异**：地面抬升凝结高度
> $z_b=z_{\rm surf}+125\,{\rm m/K}\cdot(T-T_d)$（`scoring.py:1150-1166`）只用于
> **legacy 评估路径与剖面/横截面诊断**（`assess_timestep`、`cross_section`、
> `cloudbase`）。主评分链路的云底来自上面的模式边界与层间中点，两者不可混用。

### 3.3 CAMS 气溶胶

- CAMS 等压面（1000–300 hPa，11 层）映射到**标准大气高度**
  $$h(p)=44330\left[1-\left(\frac{p}{1013.25}\right)^{0.1903}\right]\ {\rm m}$$
  即 $110.9\le h\le9165.4$ m（`aerosol.py:105-122`），再水平双线性到模式格点。
- **全柱归一化**：把 11 层原始消光剖面 $r(h)$ 缩放成受 AOD550 约束的消光系数
  （`national.rs:719-740`）：
  $$\kappa(h)=\frac{{\rm AOD}_{550}\,r(h)}{\displaystyle\int_{h_0}^{h_{11}}r(h')\,{\rm d}h'}
   \times10^{3}\quad\left[{\rm km^{-1}}\right]$$
  归一化分母始终是**完整廓线**，不是地形以上的部分：高原观测者只保留自己头顶那段
  AOD 份额，地形以下的部分被丢弃，而不是把整柱 AOD 压进薄层放大数倍。
- **路径积分**：沿光路做梯形积分，节点长取三维间距
  $\Delta\ell=\sqrt{\Delta x^{2}+\Delta z^{2}}$：
  $$\tau_a=\int_\Gamma\kappa\left(h(\ell)\right){\rm d}\ell$$
  射线升到 20 km 以上即终止追踪（`national.rs:8583, 9215`）。
- 缓存 fail-closed：缺文件 / 不覆盖 / 缺层 → 明确报错，不虚构 AOD。

## 4. 光线追踪层（Rust，`native/src/national.rs`）

对每个观测点 × 每个五分钟步长追踪两条腿。两条腿的物理职责不同：

- **入射腿**回答"云底是否被太阳直接照到"；
- **返程腿**回答"这道已被照亮的云，在观测者眼中有多少对比度"——它不是第二次
  直射消光检验（`national.rs:249-252`）。

### 4.1 太阳入射腿（目标 → 太阳）

从目标云格沿**太阳方位反方向**前进，判断阳光能否到达该云底部。

**节点步长**（`national.rs:8438`）：

$$\Delta s=\mathrm{clamp}\left(0.25°\times111\ {\rm km},\;8,\;35\right)=27.75\ {\rm km}$$

**射线高度**在目标点的局部切线框架中传播（`national.rs:8434-8437, 8546`）：

$$z(\ell)=z_b+\ell\tan\theta_{\rm loc}+\frac{\ell^{2}}{2R},\qquad
\theta_{\rm loc}=\theta_{\rm ill}+\frac{d_t\cos\Delta{\rm az}}{R}$$

$d_t$ 是云目标到观测者的距离；曲率项用**纯几何** $R=6371$ km。$7/6$ 有效半径
只出现在视线仰角 `apparent_elevation`（`national.rs:3489-3492`），不参与太阳光路。

**地影判据分两层，不要混淆**：

| 用途 | 判据 | 位置 |
|---|---|---|
| 快速否决（评分用） | $z\le\min\left(z_{\rm terrain},0\right)+25$ m 即判被地球挡住 | `national.rs:8556-8564` |
| 诊断用受光下界 | $z_{\min}=z_o+R\left(\sec\alpha-1\right),\ \alpha=\min\left(20°,-\theta_{\rm loc}\right)$ | `geometry.py:339-355` |

快速否决的下界取 $\min\left(z_{\rm terrain},0\right)$ 而不是目标格地形，是为了让
上坡地形不误杀一条仍在爬升的光线；海平面以下的盆地保留目标自身的下界。

**逐节点云采样**给出阻挡度与光学深度增量（`national.rs:515, 558-565`）：

$$b_{\rm node}=\mathrm{clamp}_{01}\!\left(A\,o\,i\,\sigma\right),\qquad
\Delta\tau_c=b_{\rm node}^{1.35}\,\frac{\Delta s}{140\ {\rm km}}$$

- $i$ 是射线与云层顶底的垂直相交因子，边界用软边过渡（高/中/低云分别
  450/300/180 m，`national.rs:8006-8010`）；
- $\sigma$ 是同层甲板的自遮蔽系数（§4.1.1）；
- 指数 $1.35$ 表达"半透明云不完全透一半光"的非线性。

**峰值修正**（`national.rs:528, 547-555`）：

$$T_c=e^{-\tau_c}\left(1-0.72\,b_{\max}\right)$$
即使积分 $\tau_c$ 不大，一个实心节点也要显著打折。这是对未解析最密段的经验亚格
修正，不是第二套物理光学厚度。

#### 4.1.1 目标甲板自遮蔽

观测者正盯着的甲板不该把它自己挡死。与目标同层且连通的格元，其阻挡度乘以随距离
线性衰减的系数（`national.rs:397-410`）：

$$\sigma(d)=\begin{cases}
0.20, & d\le60\ {\rm km}\\[2pt]
0.20+0.80\,\dfrac{d-60}{150-60}, & 60<d<150\ {\rm km}\\[8pt]
1.00, & d\ge150\ {\rm km}
\end{cases}$$

远处的同层云只是环境场，不是"目标自己的甲板"，所以回归全阻挡。

豁免只在**太阳已落到地平线以下**（$\theta_s\le0$）、甲板判定为连通、且甲板本身
不厚不密时生效（`national.rs:235-243, 381-389`）：

$$z_t-z_b\le6.5\ {\rm km},\qquad o\le0.60,\qquad r_{\rm deep}<0.20$$

连通判定带 **3 格迟滞**（`national.rs:417-423, 9306-9320`）：硬清空（$A<0.02$）立即
断开，边缘值不连通需连续 3 格才判为断开，否则保持连通。

$$\text{connected}\leftarrow
\begin{cases}
\text{false}, & A<0.02\\[2pt]
\text{false}, & 3\ \text{consecutive misses}\\[2pt]
\text{true}, & \text{otherwise}
\end{cases}$$

单格的边缘值不会翻转状态，避免一次双线性抖动就让整块甲板的透过率跳变约 2.6 倍。

#### 4.1.2 风场平流（半拉格朗日 + RK2 中点风）

目标时刻的云从哪里来？对左右两个模式时刻各做一次风位移采样
（`national.rs:5929-5956`）：

$$\mathbf{x}_{1/2}=\mathbf{x}+\tfrac{1}{2}\Delta t\,\mathbf{u}\left(\mathbf{x}\right),
\qquad
\mathbf{x}'=\mathbf{x}+\Delta t\,\mathbf{u}\left(\mathbf{x}_{1/2}\right)$$

先用半步位置采样中点风，再用中点风做整步位移——这正是 RK2，在强切变下修正约
70 km 的落点偏差。位移出界一律钳制到网格边缘（`national.rs:5847-5854,
5961-5975`），平移后的入流边界保持可见，不再静默变晴空。

两时刻只有一侧有云时，线性混合会把整块甲板稀释成永久半透墙
（$e^{-\tau/2}\ne\frac{1}{2}e^{-\tau}+\frac{1}{2}$），也抹掉了事件的时长。因此改用
**确定性 dither 二值选边**（`national.rs:5863-5892`）：

$$T=\begin{cases}T_{\rm right}, & w\ge\xi\\[2pt] T_{\rm left}, & w<\xi\end{cases},
\qquad
\xi=\operatorname{frac}\!\left(\sin\left(127.1s+311.7\right)\times43758.5453\right)$$

$w$ 是按时间括号算出的权重，$s$ 是空间/时间种子。$\xi$ 在格元间近似均匀分布，所以
集合平均仍等于线性混合，而单个格元保留完整甲板属性（含占空比）。

### 4.2 观测者返程腿（观测者 → 目标）

同一套光学积分，但从观测者高度出发（`national.rs:7040-7160`）：

1. 视线高度按**球面大圆几何** $z(\ell)$ 给出（不是抛物近似），逐节点做地形否决
   （`national.rs:7110-7120`）：
   $$z(\ell)\le z_{\rm terrain}+25\ {\rm m}\;\Longrightarrow\;\text{blocked}$$
2. 气溶胶光学深度沿用 §3.3 的路径积分；对目标体之外的云节点累加 $\tau_c$ 与
   $b_{\max}$，得到 $T_{\rm obs}$ 与 $\tau_{\rm ret}$。

**观测能见度门槛**：近→远的天空合成给出能见度场，最终并成一个"地面天气门"
（`national.rs:10540-10547`）：

$$g_{\rm wx}=\min\Big(S\left(V;\,1500,\,8000\right),\;
1-S\left(P;\,0.05,\,1.0\right)\Big)$$

其中 $V$ 是最近格元能见度（m），$P$ 是降水率（mm/h），$S$ 是 smoothstep
（`national.rs:3474-3483`）。Python 侧再以 `MIN_OBSERVER_VISIBILITY_FOR_SCORE = 0.05`
（`forward_field_scoring.py:29`）整场否决：浓雾天直接输出"无明显火烧云"。

### 4.3 视角投影（立体角积分）

前方天空离散成 **36 方位 bin（±90°，5°/bin）× 90 仰角 bin（1°/bin）**
（`national.rs:173-190`）。仰角权重是精确球带积分：

$$w_e=\sin\left(e+1\right)°-\sin\left(e\right)°,\qquad
\sum_{e=0}^{89}w_e=\sin90°=1$$

任一视角场 $X_{a,e}$ 的归约都是"方位平均 + 仰角加权"
（`angular_solid_angle_field`，`national.rs:3342-3347`）：

$$F=\frac{1}{36}\sum_{a=0}^{35}\sum_{e=0}^{89}w_e\,\mathrm{clamp}_{01}\!\left(X_{a,e}\right)$$

bin 0 下探到 **−1.5°**（`national.rs:3501`）：几何地平线之下的云仍会遮挡**折射后**
的太阳盘（日落时太阳视位置在地平线下约 0.83°）。

**由近及远、按族去重的可见份额**（`national.rs:10113-10160`）。每个角元维护一个
整体占据量 $V$，以及每族各自的最大占据量 $C^{\rm fam}$（同一族的多层投影不重复
计数）：

$$C^{\rm fam}\leftarrow\max\left(C^{\rm fam}_{\rm prev},\;
\mathrm{clamp}_{01}\!\left(A_k\omega_k\right)\right)$$

$$\kappa_k=\frac{C^{\rm fam}-C^{\rm fam}_{\rm prev}}{1-C^{\rm fam}_{\rm prev}},\qquad
v_k=\left(1-V_{<k}\right)\kappa_k,\qquad
V_{<k+1}=V_{<k}+v_k$$

$v_k$ 是该云在这个角元的可见份额。可烧面积与各类负载都以 $v_k b_k$ 累加，再对
所有角元做立体角归约：

$$A_{\rm burn}=F\!\left[\sum_k v_kb_k\right],\qquad
L_M=2.5\,F\!\left[\sum_k v_kb_k m_k\right],\qquad
L_I=2.5\,F\!\left[\sum_k v_kb_k m_k\iota_k\right],\qquad
L=2.5\,F\!\left[\sum_k v_kb_k m_k\iota_kc_k\right]$$

$F[\cdot]$ 即上面的立体角归约，$2.5$ 把归约结果放大到质量指数的量程（截顶 2.5），
$m_k$ 是云材质质量。

### 4.4 两条腿的感知响应

入射腿的物理量是 Beer-Lambert 透过率 $T=e^{-\tau_a}$，但**进分的不是透过率本身**，
而是一条标定过人眼/相机动态范围的分段响应（`national.rs:1503-1534`）：

$$\iota=\mathrm{PWL}_{\ln T}\Big(T;\;
\left(1/5000,\,0\right),\left(1/1000,\,0.15\right),\left(1/500,\,0.35\right),
\left(1/50,\,0.85\right),\left(1/20,\,1\right),\left(1,\,1\right)\Big)$$

返程腿也不再乘一次直射消光，而是一条渐进对比度响应
（`national.rs:252, 1537-1558`）：

$$c=\mathrm{clamp}_{01}\!\left(T_{\rm ret}^{\,0.025}\cdot
S\left(T_{\rm ret};\,1/5000,\,1/1000\right)\right)$$

指数 $0.025$ 让普通 AOD 场景只损失 6–29%，而返程光低到千分之几时仍被门限抹平。

## 5. 经验评分层（Python，`forward_field_scoring.py`）

### 5.1 每步即时分（instant）

native 每步给出四个标量：可烧云量 $A_{\rm burn}$、材质负载 $L_M$、入射受光负载
$L_I$、总负载 $L$。三个因子可由负载比值反解，它们都是按 $v_kb_k$ 加权的天空均值
（`forward_field_scoring.py:475-510`）：

$$m=\mathrm{clamp}_{01}\!\left(\frac{L_M}{2.5\,A_{\rm burn}}\right),\qquad
\iota=\frac{L_I}{L_M},\qquad
c=\frac{L}{L_I}$$

（分母为零时取 1。）随后是四级账本：

$$q_{\rm cov}=\mathrm{PWL}\left(A_{\rm burn};\;\text{anchors}\right)$$

（锚点见 §5.3。）

$$f_m=\mathrm{clamp}_{01}\!\left(\frac{m}{0.72}\right)$$

$$\begin{aligned}
L_1&=\max\left(0,\;q_{\rm cov}+q_{\rm cov}\left(f_m-1\right)\right)=q_{\rm cov}f_m\\
L_2&=\max\left(0,\;L_1+L_1\left(\iota-1\right)\right)=L_1\iota\\
L_3&=\max\left(0,\;L_2+L_2\left(c-1\right)\right)=L_2c
\end{aligned}$$

$$\mathrm{pre}=\mathrm{clamp}_{0}^{2.5}\left(L_3\right)
=\mathrm{clamp}_{0}^{2.5}\left(q_{\rm cov}\,f_m\,\iota\,c\right)$$

`0.72` 是材质参考质量 `MATERIAL_REFERENCE_QUALITY`（`forward_field_scoring.py:32`）：
材质刚好达标时不加不减，低于它按比例扣分。

> 逐项写成的 `base + base×(factor−1)` 与连乘等价，但保留了"每一级只调整一次"的
> 审计语义——诊断字段 $L_1,L_2,L_3$ 因此可以逐级反查是哪一环扣了分。

### 5.2 事件聚合：统一 20 分钟窗口

单步分不能直接平均——观测者能同时看到的是**一段连续窗口**，不是整场事件的碎片
总和。20 个五分钟样本各有梯形宽度 $\Delta_k$（`forward_field_scoring.py:313-329`）：

$$\mathrm{pre}_{\rm event}=\left(\sum_k w_k\,\mathrm{pre}_k\right)f_D\left(D\right)$$

$$w_k=\frac{\left|\Delta_k\cap W^{\star}\right|}{\min\left(D,\,20\right)},\qquad
W^{\star}=\arg\max_{W}\sum_k\left|\Delta_k\cap W\right|\mathrm{pre}_k,\qquad
D=\sum_k\Delta_k\,\mathbb{1}\left[\text{active}\right]$$

- 分母用 $\min\left(D,20\right)$，短单 run 事件因此精确退化为"活动期均值"；
- **单一公式，无分支**（`forward_field_scoring.py:1106-1182`）：碎片化事件跨
  20 分钟边界连续，消除了旧版 0.844→0.612 的跳崖；
- 窗口可以跨 inactive 间隙（间隙贡献 0 积分），候选起点取全轴断点；
- 选中的 $w_k$ 会同时作用到锚点分与三个因子上，最后再统一做响应曲线；
- 时长因子是分段线性的饱和函数（`forward_field_scoring.py:1089-1104`）：

$$f_D\left(D\right)=\mathrm{PWL}\Big(D;\;
\left(0,0\right),\left(5,0.25\right),\left(10,0.50\right),\left(15,0.75\right),
\left(20,1.00\right),\left(40,1.04\right),\left(70,1.07\right),\left(120,1.10\right)\Big)$$

时长饱和意味着引擎不鼓励"拖长尾"：20 分钟之后每翻倍时间只再多给几个百分点。

### 5.3 校准响应曲线与档位

最后一步只做一次单调凸变换（`forward_field_scoring.py:266-274`）：

$$q=\mathrm{clamp}_{0}^{2.5}\left(1.225\,\mathrm{pre}^{\,1.2}\right)$$

指数 $1.2$ 放大中高段。饱和点
$\mathrm{pre}^{\star}=\left(2.5/1.225\right)^{1/1.2}=1.812$ 对应
$A_{\rm burn}\approx0.9124$。

云量锚点（`CLOUD_AMOUNT_SCORE_POINTS`，`forward_field_scoring.py:247-259`）：

| $A_{\rm burn}$ | 0 | 0.02 | 0.05 | 0.12 | 0.25 | 0.40 | 0.55 | 0.70 | 0.85 | 0.95 | 1.00 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| $q_{\rm cov}$ | 0 | 0.02 | 0.05 | 0.20 | 0.40 | 0.60 | 0.80 | 1.00 | 1.50 | 2.00 | 2.50 |

档位阈值（`forecast_characteristics.py:12-27`）：

| $q$ | 档位 |
|---|---|
| $<0.001$ | 无明显火烧云 |
| $<0.05$ | 微微烧 |
| $<0.20$ | 小烧 |
| $<0.40$ | 小烧到中等烧 |
| $<0.60$ | 中等烧 |
| $<0.80$ | 中等烧到大烧 |
| $<1.00$ | 不完美大烧 |
| $<1.50$ | 典型大烧 |
| $<2.00$ | 优质大烧 |
| $\ge2.00$ | 世纪大烧 |

另有**色彩诊断**（golden / orange_red / deep_red / muted，按太阳仰角 + CAMS AOD
0.32 门槛 + 云相；`forecast_characteristics.py:225-231`）、**时间窗诊断**（按
coverage 0.003 插值的活动起止）与**趋势诊断**（forming / steady / dissipating）。

## 6. 实况 nowcast 原理

| 输入 | 反演 | 进分方式 |
|---|---|---|
| 向日葵 9 HSD/HCAI | B13/B11 多光谱云相 + 冰概率；GFS 垂直先验 + 卫星云顶 → 云柱 base 后验 | 观测点本地云柱直接进分；低云检出阈值 $0.55+0.15\,\mathrm{clamp}_{01}\!\left(\frac{z_{\rm terrain}-1500}{2000}\right)$；聚合需 known 列 $\ge34\%$ 检出（`satellite_cloud_column.py:3571, 3920`） |
| 卫星运动 | 全图 FFT 相位相关 → 位移场 | `strong` 门：$r\ge0.35$ **且** 运动年龄 $\le40$ min；$0$–$2$ h 全权外推，$2$–$4$ h 按 $s=\frac{4-t}{2}$ 线性衰减，$>4$ h 仅诊断（`satellite_cloud_column.py:2811, 2899-2926`） |
| 天气雷达（RainViewer） | 65 km 邻域 + 光路采样 | 采样权重 $\max\left(0.25,\frac{220-d}{100}\right)$（$d\le120$ km 时取 1）；对流顶棚见下 |
| 机场 METAR | 光路走廊选站（邻域/走廊相关性 + 时效）；云幕 / CAVOK | 可见性顶棚见下 |

**两类顶棚**（`service.py:1249-1266`）：

$$q_{\rm conv}=\mathrm{clamp}\Big(1-0.88\,S\left(r_{\rm path};\,0.18,\,0.72\right),\;
{\rm low}=0.12\Big)$$

$$q_{\rm vis}=\mathrm{clamp}\Big(1-0.82\,S\left(r_{\rm mtr};\,0.12,\,0.75\right),\;
{\rm low}=0.18\Big)$$

$$\text{nowcast}=\min\left(q,\;q_{\rm conv},\;q_{\rm vis}\right)$$

**METAR 可靠性**（`service.py:1175-1210`）：

$$\rho=\mathrm{clamp}_{01}\Big(f_{\rm fresh}\;w_{\rm dist}\;
\left(0.65+0.35a\right)\;\mathrm{clamp}_{01}\!\left(1-\frac{\Delta z}{2500},\;
{\rm low}=0.35\right)\Big)$$

$$f_{\rm fresh}=\begin{cases}1, & {\rm age}\le30\ {\rm min}\\[2pt]
\dfrac{120-{\rm age}}{90}, & \text{otherwise}\end{cases},\qquad
w_{\rm dist}=\begin{cases}1, & d\le80\ {\rm km}\\[2pt]
\dfrac{240-d}{160}, & \text{otherwise}\end{cases}$$

（$f_{\rm fresh}$ 在 30 分钟以内不衰减，之后线性降到 120 分钟归零。）

- 方位对齐 $a=1-\frac{\left|\Delta{\rm az}\right|}{180}$：正对光路的机场权重最高，
  位于观测者背后的最低；
- 海拔差 $\Delta z$ 描述不同边界层，谷地观测者与高原机场不能互换。

卫星来源缺帧时 fail-closed：不回退到模式评分。

## 7. 缓存与版本契约

- 算法版本 `2026.11.04.87`（`version.py:9`）；点缓存 v121（`web_server.py:3013`）、
  区域缓存 v125 与区域摘要 v55（`web_server.py:4285-4286`）、地图帧 v25
  （`map_frames.py:22`）。任何评分语义变化都必须 bump，旧缓存自然失效。
- 缓存 key = 全部影响结果的参数 + 算法/数据版本的 SHA256 前 24 位
  （`web_server.py:3089, 4531`）。

## 8. 已知边界（诚实清单）

- 模式水平分辨率 0.25°（约 27 km）：亚格云墙、云隙几何只能近似；
- CAMS 层高度用标准大气而非实际温度层（逐层约 100 m 偏差）；
- 双线性采样对"占空比"的时间/空间稀释已部分修正（dither / 边界钳制），但空间
  双线性的面积再平均仍是模式固有限制；
- 不透明度 $o$ 是 CLWMR/ICMR + RH 的相对代理，不是绝对光学厚度或有效半径——
  `national_batch.py:128-136` 对此有显式声明；
- 入射腿曲率用纯几何 $R=6371$ km，折射只体现在受光仰角 $\theta_{\rm ill}$ 与视线
  仰角（$R_{\rm eff}$）两处。二者不是同一个半径，改动时必须分开处理；
- 负载因子是各角元的加权均值，账本按因子连乘重组，相当于把"乘积的均值"近似为
  "均值的乘积"——当天空各处的云材质/气溶胶条件差异很大时，这是一种有意的平滑。
