# Firesky Prediction Principles

> How the engine gets from numerical-model fields to a level label. Every number
> and formula below is taken from the source code; the `file:line` anchors refer
> to algorithm version `2026.11.04.87`.
>
> Formulas are written in LaTeX and render directly on GitHub, in VS Code,
> Obsidian and Typora. 中文版：[PRINCIPLES.md](PRINCIPLES.md)

## 0. The idea in one sentence

> A burning sunset = "a cloud that the low sun can directly illuminate around
> dusk/dawn, that the observer can actually see".

The engine uses model cloud columns to answer "where is the cloud, how thick, how
transparent", ray tracing to answer "can it be lit, can it be seen", and an
empirical curve to translate the physics into human-readable levels.

## 1. Overall data flow

```
GFS subset (0.25°, 1h–3h) ─┐
EC open data (2nd model)   ─┼─► cloud-column derivation (3-family geometry/opacity/deep risk)
CAMS aerosol (11 levels)   ─┘         │
                                      ▼
solar geometry (astral + refraction) ──► native ray tracer (Rust): solar-inflow leg + observer return leg
                                      │
                                      ▼
                          empirical scoring (anchor map / window integral / response curve) ──► levels
```

- **Event window**: anchored on the sunrise/sunset instant (computed by astral,
  then corrected for the solar semi-diameter and refraction). Dawn uses offsets
  $-75\ldots+20$ min, dusk uses $-20\ldots+75$ min, one 5-minute step each,
  20 steps total (`national_batch.py:123-124`).
- **Analysis radius**: a unified 1000 km for both point and regional runs
  (`analysis_constants.py:20-21`). It is a data envelope, not a promise that
  every remote cloud is scored — visibility is resolved per cloud by ray tracing.

## 2. Symbols

| Symbol | Meaning | Unit |
|---|---|---|
| $A$ | cloud amount (model horizontal coverage) | 1 |
| $o$ | opacity (relative proxy for in-cloud optical depth) | 1 |
| $z_b,\;z_t$ | cloud base / top altitude | m |
| $\theta_s$ | geometric solar elevation | ° |
| $\theta_{\rm ill}$ | illumination elevation $=\theta_s+\rho(\theta_s)+0.2667°$ | ° |
| $\tau_c$ | cloud optical depth along the path | 1 |
| $\tau_a$ | aerosol optical depth along the path | 1 |
| $T$ | transmission $e^{-\tau}$ | 1 |
| $b$ | burnable fraction (geometric reach × self-shield transmission) | 1 |
| $\iota$ | inflow-leg AOD illumination factor | 1 |
| $c$ | return-leg AOD contrast factor | 1 |
| $m$ | material quality $L_M/(2.5A_{\rm burn})$ | 1 |
| $q$ | final quality index, range $[0,\,2.5]$ | 1 |
| $R$ | Earth radius $6371$ km (pure geometry) | km |
| $R_{\rm eff}$ | effective radius $\tfrac{7}{6}R$ (sight-line elevation only) | km |

## 3. Data layer: from GRIB to "three cloud families"

### 3.1 Model fields → families

Each GFS pressure level (200–925 hPa) carries cloud fraction (`tcc`/layer
fraction), condensate (CLWMR/ICMR), RH, temperature and wind. Derivation rules:

1. **Family assignment**: by pressure, high ($p\le400$ hPa) / middle
   ($400<p\le700$ hPa) / low ($p>700$ hPa) (`national_batch.py:506-507`). A
   400 hPa sample is re-classified as middle when a strong upper deck is present,
   so a deep connected cloud body is not misread as cirrus
   (`national_batch.py:1906-1919`).
2. **Member significance**: the level holding the family peak always joins the
   geometry; every other level must satisfy **both** $A\ge0.10$ and
   $A\ge0.20\times$ the family peak (`national_batch.py:2755-2770`). Thin
   companions stay out of the geometry and act only through joint opacity.
3. **Deck geometry**: a min/max envelope over significant members, then unioned
   with the model's own cloud-layer boundaries
   (`national_batch.py:2886-2907`):
   $$z_b=\min\!\left(\min_{i\in\mathcal M}z^{\rm lower}_{i},\;z_b^{\rm model}\right),
     \qquad
     z_t=\max\!\left(\max_{i\in\mathcal M}z_{i},\;z_t^{\rm model}\right)$$
   where $\mathcal M$ is the significant-member set and $z^{\rm model}$ collapses
   to identity when missing; each bound is already terrain-clipped where it is
   produced. Widening the envelope is the conservative direction: better to
   count a little extra blocking than to miss a block of cloud. This is the
   deliberate contract that **two substantial decks keep their combined bounds**
   as independent blocking evidence
   (`test_contiguous_pressure_levels_count_once_as_one_cloud_deck`).
4. **Joint opacity**: decks inside one family combine as serial transmission,
   each taking its own densest significant member
   (`national_batch.py:2768-2777`):
   $$o_{\rm fam}=1-\prod_{j\in{\rm decks}}\left(1-\max_{i\in j}o_i\right)$$
5. **Deep-cloud risk**: convective anvil columns that span families with rich
   liquid/ice condensate are quantified separately as $r_{\rm deep}$ for the
   "sun cannot get through" penalty (`national_batch.py:1760-1830`).

Single-level opacity is itself a blend of RH and condensate proxies
(`national_batch.py:1673-1705`):

$$o=\mathrm{clamp}_{01}\!\left(0.22\,u_{\rm rh}+0.78\,u_{\rm op}\right),\qquad
u_{\rm rh}=\mathrm{clamp}_{01}\!\left(\frac{{\rm RH}-82}{16}\right)$$

$$u_{\rm op}=\mathrm{clamp}_{01}\!\left(0.35+0.65\,u_{\rm cond}\right),\qquad
u_{\rm cond}=\mathrm{clamp}_{01}\!\left(\frac{\log_{10}q_{c}+8}{3}\right)$$

$q_c$ is liquid plus ice mixing ratio (kg/kg). Relative humidity always keeps a
22 % weight (it separates a hazy background from a cloud that can actually
occupy the sky); the leading $0.35$ applies only when the model provides a cloud
fraction, condensate alone degrades to plain $u_{\rm cond}$, and with neither the
level opacity is 0.

### 3.2 Cloud-base geometry: layer midpoints + model boundaries

The **scored native path** does not use a fixed layer-depth table:

1. Pressure-level centre heights $z_i$ come from model geopotential; a level's
   lower interface is the midpoint to the next level, clamped by model terrain
   (`national_batch.py:2845-2856`):
   $$z^{\rm lower}_{i}=\max\!\left(\frac{z_i+\max\left(z_{i+1},\,z_{\rm surf}\right)}{2},\;
     z_{\rm surf}\right)$$
   The top level has no successor and falls back to a fixed depth
   $z_i-\Delta_i$ (`_LEVEL_DEPTH_M`, `national_batch.py:494-498`).
2. The family envelope prefers the **model's own cloud-layer bottom/top pressure
   surfaces** (`*CloudLayerBottom/Top`), interpolated in log pressure
   (`national_batch.py:1092-1211`):
   $$z(p)=z(p_a)+\left(z(p_b)-z(p_a)\right)\frac{\ln p-\ln p_a}{\ln p_b-\ln p_a}$$
   The step-1 midpoint envelope is only a fallback for missing fields.

> **Difference from earlier docs**: the surface-parcel LCL
> $z_b=z_{\rm surf}+125\,{\rm m/K}\cdot(T-T_d)$ (`scoring.py:1150-1166`) is used
> only by the **legacy assessment path and cross-section diagnostics**
> (`assess_timestep`, `cross_section`, `cloudbase`). The main scoring path takes
> its cloud base from the model boundaries and layer midpoints above. Do not mix
> the two.

### 3.3 CAMS aerosol

- CAMS pressure levels (1000–300 hPa, 11 levels) map to **standard-atmosphere
  heights**
  $$h(p)=44330\left[1-\left(\frac{p}{1013.25}\right)^{0.1903}\right]\ {\rm m}$$
  giving $110.9\le h\le9165.4$ m (`aerosol.py:105-122`), then bilinearly to the
  model grid.
- **Full-column normalisation**: the 11-level raw extinction profile $r(h)$ is
  scaled into an AOD550-constrained extinction coefficient
  (`national.rs:719-740`):
  $$\kappa(h)=\frac{{\rm AOD}_{550}\,r(h)}{\displaystyle\int_{h_0}^{h_{11}}r(h')\,{\rm d}h'}
   \times10^{3}\quad\left[{\rm km^{-1}}\right]$$
  The denominator is always the **complete profile**, never the above-terrain
  part: a high station keeps only the AOD share above its head, and the
  below-terrain share is discarded rather than compressed into a thin band and
  inflated several-fold.
- **Path integral**: trapezoidal along the path with the 3-D node length
  $\Delta\ell=\sqrt{\Delta x^{2}+\Delta z^{2}}$:
  $$\tau_a=\int_\Gamma\kappa\left(h(\ell)\right){\rm d}\ell$$
  A ray that climbs above 20 km stops tracing (`national.rs:8583, 9215`).
- Fail-closed cache: missing files / coverage gaps / missing levels raise a clear
  error — the engine never invents AOD.

## 4. Ray-tracing layer (Rust, `native/src/national.rs`)

For every observer × every five-minute step, two legs are traced. They do
different jobs:

- the **inflow leg** answers "is the cloud base in direct sunlight";
- the **return leg** answers "how much contrast does that lit cloud have for the
  observer" — it is not a second direct-sun extinction test
  (`national.rs:249-252`).

### 4.1 Solar-inflow leg (target → sun)

Marching from the target cell **back along the solar azimuth**, deciding whether
sunlight reaches the cloud base.

**Node spacing** (`national.rs:8438`):

$$\Delta s=\mathrm{clamp}\left(0.25°\times111\ {\rm km},\;8,\;35\right)=27.75\ {\rm km}$$

**Ray height** propagates in the target's local tangent frame
(`national.rs:8434-8437, 8546`):

$$z(\ell)=z_b+\ell\tan\theta_{\rm loc}+\frac{\ell^{2}}{2R},\qquad
\theta_{\rm loc}=\theta_{\rm ill}+\frac{d_t\cos\Delta{\rm az}}{R}$$

$d_t$ is the target-to-observer distance. The curvature term uses **pure
geometry** $R=6371$ km. The $7/6$ effective radius appears only in the sight-line
elevation `apparent_elevation` (`national.rs:3489-3492`), never in the solar path.

**Two different earth-shadow criteria — do not conflate them**:

| Purpose | Criterion | Location |
|---|---|---|
| Fast veto (scoring) | $z\le\min\left(z_{\rm terrain},0\right)+25$ m ⇒ blocked by Earth | `national.rs:8556-8564` |
| Diagnostic illumination floor | $z_{\min}=z_o+R\left(\sec\alpha-1\right),\ \alpha=\min\left(20°,-\theta_{\rm loc}\right)$ | `geometry.py:339-355` |

The fast veto floors at $\min\left(z_{\rm terrain},0\right)$ rather than the
target cell's terrain, so an upslope never kills a ray that is still climbing;
below-sea-level basins keep the target's own floor.

**Per-node cloud sampling** gives the blocking factor and the optical-depth
increment (`national.rs:515, 558-565`):

$$b_{\rm node}=\mathrm{clamp}_{01}\!\left(A\,o\,i\,\sigma\right),\qquad
\Delta\tau_c=b_{\rm node}^{1.35}\,\frac{\Delta s}{140\ {\rm km}}$$

- $i$ is the vertical intersection factor with the deck top/bottom, with soft
  edges of 450/300/180 m for high/middle/low (`national.rs:8006-8010`);
- $\sigma$ is the same-layer self-shield factor (§4.1.1);
- the $1.35$ exponent expresses that a half-transparent deck does not pass half
  the light.

**Peak correction** (`national.rs:528, 547-555`):

$$T_c=e^{-\tau_c}\left(1-0.72\,b_{\max}\right)$$

A single solid node discounts the result even when the integral is small. This is
an empirical sub-grid correction for the unresolved densest segment, not a second
physical optical depth.

#### 4.1.1 Target-deck self-shielding

The deck the observer is looking at must not block itself. Same-layer connected
cells get a factor that decays linearly with distance
(`national.rs:397-410`):

$$\sigma(d)=\begin{cases}
0.20, & d\le60\ {\rm km}\\[2pt]
0.20+0.80\,\dfrac{d-60}{150-60}, & 60<d<150\ {\rm km}\\[8pt]
1.00, & d\ge150\ {\rm km}
\end{cases}$$

Distant same-layer cells are the surrounding field, not the target's own deck, so
they return to full blocking.

The exemption only applies when the **sun is already below the horizon**
($\theta_s\le0$), the deck is judged connected, and the deck is neither thick nor
dense (`national.rs:235-243, 381-389`):

$$z_t-z_b\le6.5\ {\rm km},\qquad o\le0.60,\qquad r_{\rm deep}<0.20$$

The connection latch carries a **3-cell hysteresis**
(`national.rs:417-423, 9306-9320`):

$$\text{connected}\leftarrow
\begin{cases}
\text{false}, & A<0.02\ \text{(hard clear, breaks immediately)}\\[2pt]
\text{false}, & 3\ \text{consecutive disconnected cells}\\[2pt]
\text{true}, & \text{otherwise}
\end{cases}$$

A single marginal cell never flips the state, preventing one bilinear wobble from
jumping the deck transmission by a factor of ~2.6.

#### 4.1.2 Wind advection (semi-Lagrangian + RK2 midpoint wind)

Where did the cloud at the target instant come from? Both bracketing model times
are sampled with a wind displacement (`national.rs:5929-5956`):

$$\mathbf{x}_{1/2}=\mathbf{x}+\tfrac{1}{2}\Delta t\,\mathbf{u}\left(\mathbf{x}\right),
\qquad
\mathbf{x}'=\mathbf{x}+\Delta t\,\mathbf{u}\left(\mathbf{x}_{1/2}\right)$$

Sampling the wind at the half-step position and then taking the full step with it
is exactly RK2; it corrects roughly 70 km of trajectory bending under strong
shear. Out-of-grid displacements clamp to the grid edge
(`national.rs:5847-5854, 5961-5975`), so an advected inflow wall stays visible
instead of silently clearing the sky.

When only one bracket side holds a deck, linear blending would dilute it into a
permanent half-wall ($e^{-\tau/2}\ne\frac{1}{2}e^{-\tau}+\frac{1}{2}$) and erase
the event's duration. A **deterministic dither picks one side** instead
(`national.rs:5863-5892`):

$$T=\begin{cases}T_{\rm right}, & w\ge\xi\\[2pt] T_{\rm left}, & w<\xi\end{cases},
\qquad
\xi=\operatorname{frac}\!\left(\sin\left(127.1s+311.7\right)\times43758.5453\right)$$

$w$ is the temporal bracket weight and $s$ a spatial/temporal seed. $\xi$ is
approximately uniform across cells, so the ensemble mean still equals the linear
blend while each individual cell keeps the full deck properties (duty cycle
included).

### 4.2 Observer return leg (observer → target)

The same optical integration from the observer's height
(`national.rs:7040-7160`):

1. The sight-line height follows **spherical great-circle geometry** $z(\ell)$
   (not a parabolic approximation), with a per-node terrain veto
   (`national.rs:7110-7120`):
   $$z(\ell)\le z_{\rm terrain}+25\ {\rm m}\;\Longrightarrow\;\text{terrain blocked}$$
2. Aerosol optical depth uses the §3.3 path integral. For cloud nodes beyond the
   target body it accumulates $\tau_c$ and $b_{\max}$, giving $T_{\rm obs}$ and
   $\tau_{\rm ret}$.

**Observer visibility gate**: near-to-far sky compositing yields a visibility
field that folds into one surface-weather gate
(`national.rs:10540-10547`):

$$g_{\rm wx}=\min\Big(\underbrace{S\left(V;\,1500,\,8000\right)}_{\text{visibility }V\,[{\rm m}]},
\;\underbrace{1-S\left(P;\,0.05,\,1.0\right)}_{\text{precipitation }P\,[{\rm mm/h}]}\Big)$$

$S$ is smoothstep (`national.rs:3474-3483`); $V$ and $P$ come from the nearest
cell in the forward half-disk. The Python layer then vetoes the whole field below
`MIN_OBSERVER_VISIBILITY_FOR_SCORE = 0.05` (`forward_field_scoring.py:29`): dense
fog yields "no glow" outright.

### 4.3 View projection (solid-angle integral)

The forward sky is discretised into **36 azimuth bins (±90°, 5° each) × 90
elevation bins (1° each)** (`national.rs:173-190`). Elevation weights are exact
spherical-band integrals:

$$w_e=\sin\left(e+1\right)°-\sin\left(e\right)°,\qquad
\sum_{e=0}^{89}w_e=\sin90°=1$$

Any view field $X_{a,e}$ reduces to "azimuth average + elevation weighting"
(`angular_solid_angle_field`, `national.rs:3342-3347`):

$$F=\frac{1}{36}\sum_{a=0}^{35}\sum_{e=0}^{89}w_e\,\mathrm{clamp}_{01}\!\left(X_{a,e}\right)$$

Bin 0 extends down to **−1.5°** (`national.rs:3501`): clouds below the geometric
horizon still occlude the **refracted** sun disk (at sunset the sun appears
~0.83° below the horizon).

**Near-to-far, family-deduplicated visible share** (`national.rs:10113-10160`).
Each angular cell keeps a total occupancy $V$ and a per-family maximum
$C^{\rm fam}$ (multiple projections of one family are not double-counted):

$$C^{\rm fam}\leftarrow\max\left(C^{\rm fam}_{\rm prev},\;
\mathrm{clamp}_{01}\!\left(A_k\omega_k\right)\right)$$

$$\kappa_k=\frac{C^{\rm fam}-C^{\rm fam}_{\rm prev}}{1-C^{\rm fam}_{\rm prev}},\qquad
v_k=\left(1-V_{<k}\right)\kappa_k,\qquad
V_{<k+1}=V_{<k}+v_k$$

$v_k$ is that cloud's visible share in this cell. Burnable area and loads
accumulate as $v_kb_k$, then reduce over all cells:

$$A_{\rm burn}=F\!\left[\sum_k v_kb_k\right],\qquad
L_M=2.5\,F\!\left[\sum_k v_kb_k m_k\right],\qquad
L_I=2.5\,F\!\left[\sum_k v_kb_k m_k\iota_k\right],\qquad
L=2.5\,F\!\left[\sum_k v_kb_k m_k\iota_kc_k\right]$$

$F[\cdot]$ is the solid-angle reduction above, $2.5$ scales the reduced value into
the quality-index range (capped at 2.5), and $m_k$ is the cloud material quality.

### 4.4 Perceptual response of the two legs

The inflow leg's physical quantity is the Beer-Lambert transmission $T=e^{-\tau_a}$,
but **the score does not consume transmission directly**. It consumes a
calibrated human/camera dynamic-range response (`national.rs:1503-1534`):

$$\iota=\mathrm{PWL}_{\ln T}\Big(T;\;
\left(1/5000,\,0\right),\left(1/1000,\,0.15\right),\left(1/500,\,0.35\right),
\left(1/50,\,0.85\right),\left(1/20,\,1\right),\left(1,\,1\right)\Big)$$

The return leg is not another direct-sun extinction factor either, but a gradual
contrast response (`national.rs:252, 1537-1558`):

$$c=\mathrm{clamp}_{01}\!\left(T_{\rm ret}^{\,0.025}\cdot
S\left(T_{\rm ret};\,1/5000,\,1/1000\right)\right)$$

The $0.025$ exponent costs ordinary AOD scenes only 6–29 %, while return light in
the per-mille range is still gated away.

## 5. Empirical scoring layer (Python, `forward_field_scoring.py`)

### 5.1 Per-step instant score

For each step the native scorer emits four scalars: burnable amount
$A_{\rm burn}$, material load $L_M$, incoming-illumination load $L_I$, and total
load $L$. All three factors are recoverable from load ratios, and all are sky
means weighted by $v_kb_k$ (`forward_field_scoring.py:475-510`):

$$m=\mathrm{clamp}_{01}\!\left(\frac{L_M}{2.5\,A_{\rm burn}}\right),\qquad
\iota=\frac{L_I}{L_M},\qquad
c=\frac{L}{L_I}$$

(1 when the denominator is zero.) Then a four-stage ledger:

$$q_{\rm cov}=\mathrm{PWL}\left(A_{\rm burn};\;\text{anchors in §5.3}\right)$$

$$f_m=\mathrm{clamp}_{01}\!\left(\frac{m}{0.72}\right)$$

$$\begin{aligned}
L_1&=\max\left(0,\;q_{\rm cov}+q_{\rm cov}\left(f_m-1\right)\right)=q_{\rm cov}f_m\\
L_2&=\max\left(0,\;L_1+L_1\left(\iota-1\right)\right)=L_1\iota\\
L_3&=\max\left(0,\;L_2+L_2\left(c-1\right)\right)=L_2c
\end{aligned}$$

$$\mathrm{pre}=\mathrm{clamp}_{0}^{2.5}\left(L_3\right)
=\mathrm{clamp}_{0}^{2.5}\left(q_{\rm cov}\,f_m\,\iota\,c\right)$$

`0.72` is `MATERIAL_REFERENCE_QUALITY` (`forward_field_scoring.py:32`): a deck
exactly at reference material is neither rewarded nor penalised, anything below
is scaled down.

> Writing each stage as `base + base×(factor−1)` is algebraically identical to the
> product, but it preserves the audit meaning "each stage adjusts exactly once".
> The diagnostic fields $L_1,L_2,L_3$ therefore let you see which link cost the
> score.

### 5.2 Event aggregation: the unified 20-minute window

Per-step scores must not be averaged naively — what an observer can see is **one
continuous window**, not the sum of every fragment. The 20 five-minute samples
carry trapezoidal widths $\Delta_k$ (`forward_field_scoring.py:313-329`):

$$\mathrm{pre}_{\rm event}=\left(\sum_k w_k\,\mathrm{pre}_k\right)f_D\left(D\right)$$

$$w_k=\frac{\left|\Delta_k\cap W^{\star}\right|}{\min\left(D,\,20\right)},\qquad
W^{\star}=\arg\max_{W}\sum_k\left|\Delta_k\cap W\right|\mathrm{pre}_k,\qquad
D=\sum_k\Delta_k\,\mathbb{1}\left[\text{active}\right]$$

- The denominator $\min\left(D,20\right)$ makes a short single-run event
  degenerate exactly to the active-period mean;
- **One formula, no branch** (`forward_field_scoring.py:1106-1182`): fragmented
  events are continuous across the 20-minute boundary, removing the old
  0.844→0.612 cliff;
- Windows may bridge inactive gaps (gaps contribute no integral); candidate start
  points sit at every axis breakpoint;
- The selected $w_k$ applies to the anchor score and all three factors at once,
  with the response curve applied afterwards;
- The duration factor is a piecewise-linear saturating function
  (`forward_field_scoring.py:1089-1104`):

$$f_D\left(D\right)=\mathrm{PWL}\Big(D;\;
\left(0,0\right),\left(5,0.25\right),\left(10,0.50\right),\left(15,0.75\right),
\left(20,1.00\right),\left(40,1.04\right),\left(70,1.07\right),\left(120,1.10\right)\Big)$$

Saturation means the engine does not reward dragging out a long tail: past 20
minutes each doubling buys only a few percent.

### 5.3 Calibrated response curve and levels

The final step is a single monotonic convex transform
(`forward_field_scoring.py:266-274`):

$$q=\mathrm{clamp}_{0}^{2.5}\left(1.225\,\mathrm{pre}^{\,1.2}\right)$$

The $1.2$ exponent amplifies the middle-upper range. The saturation point
$\mathrm{pre}^{\star}=\left(2.5/1.225\right)^{1/1.2}=1.812$ corresponds to
$A_{\rm burn}\approx0.9124$.

Cloud-amount anchors (`CLOUD_AMOUNT_SCORE_POINTS`,
`forward_field_scoring.py:247-259`):

| $A_{\rm burn}$ | 0 | 0.02 | 0.05 | 0.12 | 0.25 | 0.40 | 0.55 | 0.70 | 0.85 | 0.95 | 1.00 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| $q_{\rm cov}$ | 0 | 0.02 | 0.05 | 0.20 | 0.40 | 0.60 | 0.80 | 1.00 | 1.50 | 2.00 | 2.50 |

Level thresholds (`forecast_characteristics.py:12-27`):

| $q$ | Label |
|---|---|
| $<0.001$ | no glow (无明显火烧云) |
| $<0.05$ | faintest burn (微微烧) |
| $<0.20$ | small burn (小烧) |
| $<0.40$ | small-to-medium (小烧到中等烧) |
| $<0.60$ | medium burn (中等烧) |
| $<0.80$ | medium-to-large (中等烧到大烧) |
| $<1.00$ | imperfect large burn (不完美大烧) |
| $<1.50$ | typical large burn (典型大烧) |
| $<2.00$ | premium large burn (优质大烧) |
| $\ge2.00$ | once-in-a-lifetime burn (世纪大烧) |

Additional diagnostics: **colour diagnosis** (golden / orange_red / deep_red /
muted, from solar elevation + the CAMS AOD 0.32 gate + cloud phase;
`forecast_characteristics.py:225-231`), **timing window** (active start/end
interpolated at coverage 0.003), and **trend** (forming / steady / dissipating).

## 6. Nowcast principles

| Input | Retrieval | How it enters the score |
|---|---|---|
| Himawari-9 HSD/HCAI | B13/B11 multispectral phase + ice probability; GFS vertical prior + satellite top → column base posterior | the local column scores directly; low-cloud detection requires $0.55+0.15\,\mathrm{clamp}_{01}\!\left(\frac{z_{\rm terrain}-1500}{2000}\right)$; aggregation needs $\ge34\%$ of known columns detected (`satellite_cloud_column.py:3571, 3920`) |
| Satellite motion | whole-frame FFT phase correlation → displacement | `strong` gate: $r\ge0.35$ **and** motion age $\le40$ min; full weight $0$–$2$ h, linear decay $s=\frac{4-t}{2}$ for $2$–$4$ h, diagnostic-only beyond (`satellite_cloud_column.py:2811, 2899-2926`) |
| Weather radar (RainViewer) | 65 km neighbourhood + light-path sampling | sample weight $\max\left(0.25,\frac{220-d}{100}\right)$ (1 for $d\le120$ km); convective ceiling below |
| Airport METAR | corridor station choice (neighbourhood/corridor relevance + freshness); ceiling/CAVOK | visibility ceiling below |

**Two ceilings** (`service.py:1249-1266`):

$$q_{\rm conv}=\mathrm{clamp}\Big(1-0.88\,S\left(r_{\rm path};\,0.18,\,0.72\right),\;
{\rm low}=0.12\Big)$$

$$q_{\rm vis}=\mathrm{clamp}\Big(1-0.82\,S\left(r_{\rm mtr};\,0.12,\,0.75\right),\;
{\rm low}=0.18\Big)$$

$$\text{nowcast}=\min\left(q,\;q_{\rm conv},\;q_{\rm vis}\right)$$

**METAR reliability** (`service.py:1175-1210`):

$$\rho=\mathrm{clamp}_{01}\Big(f_{\rm fresh}\;w_{\rm dist}\;
\left(0.65+0.35a\right)\;\mathrm{clamp}_{01}\!\left(1-\frac{\Delta z}{2500},\;
{\rm low}=0.35\right)\Big)$$

$$f_{\rm fresh}=\begin{cases}1, & {\rm age}\le30\ {\rm min}\\[2pt]
\dfrac{120-{\rm age}}{90}, & \text{otherwise}\end{cases},\qquad
w_{\rm dist}=\begin{cases}1, & d\le80\ {\rm km}\\[2pt]
\dfrac{240-d}{160}, & \text{otherwise}\end{cases}$$

- Azimuth alignment $a=1-\frac{\left|\Delta{\rm az}\right|}{180}$: a station
  looking down the light path keeps the most weight, one behind the observer the
  least;
- The elevation gap $\Delta z$ describes a different boundary layer: a valley
  observer cannot borrow a plateau airport.

A missing satellite source fails closed — there is no fallback to model scoring.

## 7. Caching and version contracts

- Algorithm version `2026.11.04.87` (`version.py:9`); point cache v121
  (`web_server.py:3011`), regional cache v125 and regional summary v55
  (`web_server.py:4285-4286`), map frame v25 (`map_frames.py:22`). Any
  scoring-semantics change must bump these so stale caches invalidate naturally.
- Cache keys = SHA256[:24] over every result-affecting parameter plus the
  algorithm/data versions (`web_server.py:3089, 4531`).

## 8. Known limits (honest list)

- 0.25° model resolution (~27 km): sub-grid cloud walls and gaps are only
  approximated;
- CAMS level heights use the standard atmosphere, not the actual temperature
  profile (~100 m per-level offsets);
- Bilinear sampling dilutes duty cycles in space; partially corrected
  (dither / edge clamping) but inherent to the model grid;
- Opacity $o$ is a relative CLWMR/ICMR + RH proxy, not an absolute optical depth
  or effective radius — see the explicit declaration in
  `national_batch.py:128-136`;
- The inflow-leg curvature uses pure geometry $R=6371$ km; refraction shows up
  only in the illumination elevation $\theta_{\rm ill}$ and the sight-line
  elevation ($R_{\rm eff}$). They are not the same radius and must be changed
  separately;
- The load factors are weighted means over angular cells, and the ledger
  reassembles them as a product. That approximates "mean of products" by "product
  of means" — a deliberate smoothing when material/aerosol conditions vary a lot
  across the sky.
