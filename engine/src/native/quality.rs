//! Complete observer-field empirical composition. The ray-traced ledger is
//! authoritative; local-column quality is diagnostic, never a replacement.
use crate::native::empirical;
use rayon::prelude::*;

pub const INPUT_FIELDS: usize = 18;
pub type Row = [f64; INPUT_FIELDS];
pub const INPUT_NAMES: [&str; INPUT_FIELDS] = [
    "view",
    "burn",
    "load",
    "material_load",
    "incoming_load",
    "incoming_block",
    "visibility",
    "vividness",
    "offset_minutes",
    "local_aod550",
    "local_view",
    "local_burn",
    "local_load",
    "local_material_load",
    "local_incoming_load",
    "observation_gate",
    "score_ceiling",
    "valid",
];
pub const INSTANT_NAMES: [&str; 19] = [
    "index",
    "pre_response_index",
    "quality_response_scale",
    "quality_response_exponent",
    "quality_response_adjustment",
    "physically_burnable",
    "burnable_cloud_coverage",
    "brightness_color",
    "cloud_material",
    "material_factor",
    "material_adjustment",
    "incoming_aod_illumination_factor",
    "incoming_aod_adjustment",
    "observer_aod_contrast_factor",
    "observer_aod_adjustment",
    "coverage_base_score",
    "raw_quality_index",
    "quality_aod550",
    "local_aod550",
];
pub type Instant = [f64; INSTANT_NAMES.len()];

macro_rules! metrics {
    ($($variant:ident => $name:literal),+ $(,)?) => {
        #[derive(Clone, Copy)]
        enum Metric { $($variant),+ }
        pub const FIELDS: &[&str] = &[$($name),+];
    };
}
metrics! {
    Index => "index",
    Base => "base_quality_index",
    Brightness => "brightness_color",
    CoverageEffective => "burnable_cloud_coverage_effective",
    CoverageMean => "burnable_cloud_coverage_mean",
    CoveragePeak => "burnable_cloud_coverage_peak",
    BurnRatioFactor => "burnable_ratio_factor",
    BurnToView => "burnable_to_view_ratio",
    Material => "cloud_material",
    CoverageBase => "coverage_base_score",
    CoverageScore => "coverage_score",
    DurationDelta => "duration_adjustment",
    DurationFactor => "duration_factor",
    Duration => "duration_minutes",
    Window => "duration_window_minutes",
    WindowPreResponse => "duration_window_pre_response_index",
    ActiveThreshold => "event_active_cloud_threshold",
    BurnEffective => "forward_burnable_cloud_field_amount_effective",
    BurnFull => "forward_burnable_cloud_field_amount_full_window_mean",
    BurnMean => "forward_burnable_cloud_field_amount_mean",
    BurnPeak => "forward_burnable_cloud_field_amount_peak",
    IncomingEffective => "forward_burnable_cloud_incoming_aerosol_load_effective",
    IncomingFull => "forward_burnable_cloud_incoming_aerosol_load_full_window_mean",
    IncomingMean => "forward_burnable_cloud_incoming_aerosol_load_mean",
    LoadEffective => "forward_burnable_cloud_load_effective",
    LoadFull => "forward_burnable_cloud_load_full_window_mean",
    LoadMean => "forward_burnable_cloud_load_mean",
    LoadPeak => "forward_burnable_cloud_load_peak",
    MaterialLoadEffective => "forward_burnable_cloud_material_load_effective",
    MaterialLoadFull => "forward_burnable_cloud_material_load_full_window_mean",
    MaterialLoadMean => "forward_burnable_cloud_material_load_mean",
    MaterialLoadPeak => "forward_burnable_cloud_material_load_peak",
    RatioEffective => "forward_burnable_cloud_ratio_effective",
    RatioPeak => "forward_burnable_cloud_ratio_peak",
    Radius => "forward_field_radius_km",
    Sector => "forward_sector_half_angle_deg",
    ViewEffective => "forward_view_cloud_field_amount_effective",
    ViewFull => "forward_view_cloud_field_amount_full_window_mean",
    ViewMean => "forward_view_cloud_field_amount_mean",
    ViewPeak => "forward_view_cloud_field_amount_peak",
    IncomingDelta => "incoming_aod_adjustment",
    IncomingFactor => "incoming_aod_illumination_factor",
    LocalAod => "local_aod550",
    MaterialDelta => "material_adjustment",
    MaterialFactor => "material_factor",
    MaterialReference => "material_reference_quality",
    VetoSteps => "observation_burnability_veto_steps",
    ObserverDelta => "observer_aod_adjustment",
    ObserverFactor => "observer_aod_contrast_factor",
    LocalQuality => "observer_column_quality_index",
    LocalPeak => "observer_column_quality_peak",
    LocalBurnMean => "observer_local_burnable_cloud_field_amount_mean",
    LocalBurnPeak => "observer_local_burnable_cloud_field_amount_peak",
    LocalLoadMean => "observer_local_burnable_cloud_load_mean",
    LocalLoadPeak => "observer_local_burnable_cloud_load_peak",
    LocalMaterialMean => "observer_local_burnable_cloud_material_load_mean",
    LocalMaterialPeak => "observer_local_burnable_cloud_material_load_peak",
    LocalViewMean => "observer_local_cloud_field_amount_mean",
    LocalViewPeak => "observer_local_cloud_field_amount_peak",
    ActiveSteps => "physically_burnable_steps",
    PreResponse => "pre_response_quality_index",
    QualityAod => "quality_aod550",
    ResponseDelta => "quality_response_adjustment",
    ResponseExponent => "quality_response_exponent",
    ResponseScale => "quality_response_scale",
    Raw => "raw_quality_index",
    Max => "score_max",
    BlockEffective => "solar_incoming_block_risk_effective",
    BlockMean => "solar_incoming_block_risk_mean",
    BlockPeak => "solar_incoming_block_risk_peak",
    Visibility => "surface_visibility_gate_peak",
    Ceiling => "observation_score_ceiling",
    CeilingDelta => "observation_ceiling_adjustment",
}

pub const SCORING_SCOPE: &str = "observer_raytraced_sky_v18";
pub const FORMULA: &str = concat!(
    "physical_ledger -> empirical_response: ",
    "perceptual_response(clip(active-period raytraced burnable area * ",
    "cloud-material factor * incoming-sunlight AOD illumination * ",
    "observer-return AOD contrast, then for active duration >20 min use ",
    "the maximum continuous 20-minute pre-response integral within each ",
    "active run, then apply the duration factor; native ray-traced burn area ",
    "is authoritative and receives no post-raytrace high-cloud area damping, ",
    "non-burnable blocker ceiling using only any excess over the native ",
    "burnable/view complement, then 0, 2.5))"
);
pub const LIMITING_FACTORS: [&str; 7] = [
    "不可烧云挡光",
    "无可烧云",
    "太阳入射阻挡已进入可烧云面积",
    "云质与垂直厚度",
    "太阳入射 AOD 削光",
    "观测光路 AOD 降低对比度",
    "可烧云绝对量",
];
pub const LOCAL_SOURCE: &str = concat!(
    "同一评分公式应用于观测点本地 GFS 云柱，仅作单点/范围诊断；",
    "它不替换完整前方天空的区域质量指数"
);
pub const NO_LOCAL_SOURCE: &str = "未提供观测点本地云柱诊断";
pub fn coverage_source(radius: f64) -> String {
    format!(
        concat!(
            "观测点朝太阳方向 ±90°、{radius:.0} km 半圆内",
            "全部 GFS 云格投影到观测者前方天空，按方位与仰角的立体角计算可见及可烧云占比；",
            "观测点所在水平云格覆盖到天顶，远处云格按近边/远边实际仰角贡献；",
            "每个云底分别追踪太阳入射与云底到观测者的回程光路，逐格积分 CAMS AOD 与",
            "垂直廓线得到的消光系数；入射段决定云底受光，回程段独立修正对比度与饱和度；",
            "再按观测视线由近到远遮挡合成；",
            "云量质量仅统计物理可烧时段，全窗口零值只进入持续时间；",
            "云量只由可见且受光的云底面积决定；云质、入射 AOD、观测 AOD 与持续时间各修正一次；",
            "入射阻挡仅在超过原生可烧/可见互补关系时作为不可烧云上限，不重复扣分；",
            "不选 Top-K、不选代表云、不使用镜头焦段"
        ),
        radius = radius
    )
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Scalar,
    Regional,
}
#[derive(Clone, Copy)]
pub struct Options {
    pub mode: Mode,
    pub apply_duration: bool,
    pub local_columns: bool,
    pub score_ceiling: bool,
    pub radius_km: f64,
}
#[derive(Clone)]
pub struct Quality {
    pub values: [f64; FIELDS.len()],
    pub has_samples: bool,
    pub local_columns: bool,
    pub score_ceiling: bool,
    pub limiting: usize,
}
impl Quality {
    pub fn flags(&self) -> [f32; 4] {
        [
            f32::from(self.has_samples),
            f32::from(self.local_columns),
            f32::from(self.score_ceiling),
            self.limiting as f32,
        ]
    }
    pub fn index(&self) -> f64 {
        self.values[Metric::Index as usize]
    }
}

fn clip(x: f64, high: f64) -> f64 {
    x.clamp(0.0, high)
}
fn normalized(row: &Row) -> Row {
    let mut out = *row;
    for (i, value) in out.iter_mut().enumerate().take(15) {
        if i == 8 || i == 9 {
            continue;
        }
        *value = clip(
            *value,
            if [2, 3, 4, 12, 13, 14].contains(&i) {
                2.5
            } else {
                1.0
            },
        );
    }
    out[16] = clip(out[16], 2.5);
    out
}
fn cap(row: &mut Row) -> f64 {
    let ceiling = if row[5] > 1e-9 {
        clip(row[0] * (1.0 - row[5] + 0.015), 1.0)
    } else {
        1.0
    };
    let burn = row[1].min(ceiling);
    let scale = clip(if row[1] > 1e-9 { burn / row[1] } else { 1.0 }, 1.0);
    row[1] = burn;
    for value in &mut row[2..5] {
        *value *= scale;
    }
    scale
}
fn gate(row: &mut Row, allowed: bool) {
    if !allowed {
        row[1..5].fill(0.0);
        row[11..15].fill(0.0);
    }
}

/// The caller supplies its own observation/visibility gate. Instant scoring
/// does not infer the event's visibility or validity masks.
pub fn score_instant(row: &Row, allowed: bool, blocker_cap: bool) -> Instant {
    let mut row = normalized(row);
    if blocker_cap {
        cap(&mut row);
    }
    gate(&mut row, allowed);
    let active = row[1] >= 0.005;
    let coverage = empirical::cloud_amount_score(row[1]);
    let material = if active {
        clip(row[3] / (2.5 * row[1]), 1.0)
    } else {
        0.0
    };
    let material_factor = clip(material / 0.72, 1.0);
    let material_delta = coverage * (material_factor - 1.0);
    let after_material = (coverage + material_delta).max(0.0);
    let incoming = if active && row[3] > 1e-9 {
        clip(row[4] / row[3], 1.0)
    } else {
        1.0
    };
    let incoming_delta = after_material * (incoming - 1.0);
    let after_incoming = (after_material + incoming_delta).max(0.0);
    let observer = if active && row[4] > 1e-9 {
        clip(row[2] / row[4], 1.0)
    } else {
        1.0
    };
    let observer_delta = after_incoming * (observer - 1.0);
    let raw = (after_incoming + observer_delta).max(0.0);
    let pre = if active { clip(raw, 2.5) } else { 0.0 };
    let index = empirical::calibrated_quality_response(pre);
    let aod = if row[9].is_finite() {
        row[9].max(0.0)
    } else {
        0.0
    };
    [
        index,
        pre,
        1.225,
        1.2,
        index - pre,
        f64::from(active),
        row[1],
        if active { row[7] } else { 0.0 },
        material,
        material_factor,
        material_delta * f64::from(active),
        incoming,
        incoming_delta * f64::from(active),
        observer,
        observer_delta * f64::from(active),
        coverage,
        raw,
        aod,
        aod,
    ]
}

pub fn sample_widths(offsets: &[f64]) -> Vec<f64> {
    if offsets.len() <= 1 {
        return vec![5.0; offsets.len()];
    }
    let mut order: Vec<_> = (0..offsets.len()).collect();
    order.sort_by(|&a, &b| offsets[a].total_cmp(&offsets[b]));
    let mut widths = vec![0.0; offsets.len()];
    for (j, &i) in order.iter().enumerate() {
        widths[i] = if j == 0 {
            (offsets[order[1]] - offsets[i]) * 0.5
        } else if j + 1 == order.len() {
            (offsets[i] - offsets[order[j - 1]]) * 0.5
        } else {
            ((offsets[i] - offsets[order[j - 1]]) + (offsets[order[j + 1]] - offsets[i])) * 0.5
        };
    }
    widths
}
fn mean(values: impl Iterator<Item = f64>) -> f64 {
    let (sum, count) = values.fold((0.0, 0_usize), |(s, n), x| (s + x, n + 1));
    if count > 0 {
        sum / count as f64
    } else {
        0.0
    }
}
fn field_statistics(rows: &[Row], widths: &[f64], active: &[u8]) -> [[f64; 15]; 3] {
    let total_active: f64 = widths
        .iter()
        .zip(active)
        .filter(|(_, a)| **a != 0)
        .map(|(w, _)| w)
        .sum();
    let total: f64 = widths.iter().sum();
    let mut result = [[0.0; 15]; 3];
    // Cache each published reduction once without constructing column Vecs.
    // Keep the same per-field summation order as the original reductions.
    for field in [0, 1, 2, 3, 4, 10, 11, 12, 13] {
        result[0][field] = if total_active > 0.0 {
            rows.iter()
                .zip(widths)
                .zip(active)
                .filter(|(_, a)| **a != 0)
                .map(|((r, w), _)| r[field] * w)
                .sum::<f64>()
                / total_active
        } else {
            mean(
                rows.iter()
                    .zip(active)
                    .filter(|(_, a)| **a != 0)
                    .map(|(r, _)| r[field]),
            )
        };
        result[1][field] = if total > 0.0 {
            rows.iter()
                .zip(widths)
                .map(|(r, w)| r[field] * w)
                .sum::<f64>()
                / total
        } else {
            mean(rows.iter().map(|r| r[field]))
        };
        result[2][field] = rows.iter().map(|r| r[field]).fold(0.0, f64::max);
    }
    result
}

/// `scalar_aod` preserves scalar API finite-mean semantics when the caller
/// selected/reordered/duplicated rows from a larger original event.
pub fn score(
    rows: &[Row],
    options: Options,
    scalar_aod: Option<&[f64]>,
) -> Result<Quality, String> {
    if rows.len() > empirical::MAX_SAMPLES
        || !options.radius_km.is_finite()
        || options.radius_km <= 0.0
        || options.radius_km > 20000.0
        || (options.mode == Mode::Regional && (options.score_ceiling || scalar_aod.is_some()))
        || rows.iter().any(|row| {
            row.iter().enumerate().any(|(i, v)| {
                (i != 9 && !v.is_finite()) || ([15, 17].contains(&i) && *v != 0.0 && *v != 1.0)
            })
        })
    {
        return Err("invalid observer-field rows, radius or scoring options".into());
    }
    let aod = scalar_aod.map_or_else(
        || {
            let selected = rows
                .iter()
                .filter(|r| options.mode == Mode::Scalar || r[17] >= 0.5);
            mean(selected.map(|r| r[9]).filter(|v| v.is_finite()))
        },
        |values| mean(values.iter().copied().filter(|v| v.is_finite())),
    );
    let mut rows: Vec<_> = rows
        .iter()
        .filter(|r| r[17] >= 0.5)
        .map(|row| {
            let mut row = normalized(row);
            if !options.local_columns {
                row[10..15].fill(0.0);
            }
            row
        })
        .collect();
    let mut result = Quality {
        values: [0.0; FIELDS.len()],
        has_samples: !rows.is_empty(),
        local_columns: options.local_columns,
        score_ceiling: options.score_ceiling,
        limiting: 1,
    };
    if rows.is_empty() {
        return Ok(result);
    }
    let mut capped = false;
    for row in &mut rows {
        let allowed = row[6] >= 0.05 && (options.mode == Mode::Regional || row[15] >= 0.5);
        gate(row, allowed);
        capped |= cap(row) < 1.0 - 1e-12;
    }
    let n = rows.len();
    let offsets: Vec<_> = rows.iter().map(|r| r[8]).collect();
    let widths = sample_widths(&offsets);
    if widths.iter().any(|v| !v.is_finite()) {
        return Err("event widths overflow".into());
    }
    let active: Vec<_> = rows.iter().map(|r| u8::from(r[1] >= 0.005)).collect();
    let any_active = active.contains(&1);
    let duration: f64 = widths
        .iter()
        .zip(&active)
        .map(|(w, a)| w * f64::from(*a))
        .sum();
    let instant: Vec<_> = rows.iter().map(|r| score_instant(r, true, false)).collect();
    let (weights, window) = if options.apply_duration && any_active {
        let pre: Vec<_> = instant.iter().map(|v| v[1]).collect();
        let window = empirical::event_window_weights(&pre, &active, &widths, Some(&offsets))
            .map_err(str::to_string)?;
        (window.weights, window.window_minutes)
    } else {
        let total: f64 = widths
            .iter()
            .zip(&active)
            .map(|(w, a)| w.max(0.0) * f64::from(*a))
            .sum();
        let fallback = options.mode == Mode::Scalar && total <= 0.0;
        let count = active.iter().filter(|a| **a != 0).count() as f64;
        let weights = widths
            .iter()
            .zip(&active)
            .map(|(w, a)| {
                if fallback && count > 0.0 {
                    f64::from(*a) / count
                } else if total > 0.0 {
                    w.max(0.0) * f64::from(*a) / total
                } else {
                    0.0
                }
            })
            .collect();
        (weights, duration)
    };
    let weight_sum: f64 = weights.iter().sum();
    let weighted = |field: usize| {
        instant
            .iter()
            .zip(&weights)
            .map(|(v, w)| v[field] * w)
            .sum::<f64>()
    };
    let weighted_mean = |field: usize| {
        if weight_sum > 0.0 {
            weighted(field) / weight_sum
        } else {
            0.0
        }
    };
    let coverage = clip(weighted(15), 2.5);
    let material = clip(weighted_mean(8), 1.0);
    let material_factor = clip(weighted_mean(9), 1.0);
    let material_delta = weighted(10);
    let after_material = (coverage + material_delta).max(0.0);
    let incoming_factor = if any_active && weight_sum > 0.0 {
        clip(weighted_mean(11), 1.0)
    } else {
        1.0
    };
    let incoming_delta = weighted(12);
    let after_incoming = (after_material + incoming_delta).max(0.0);
    let observer_factor = if any_active && weight_sum > 0.0 {
        clip(weighted_mean(13), 1.0)
    } else {
        1.0
    };
    let observer_delta = weighted(14);
    let after_aerosol = (after_incoming + observer_delta).max(0.0);
    let ceiling = if options.score_ceiling && any_active && weight_sum > 0.0 {
        clip(
            rows.iter()
                .zip(&weights)
                .map(|(r, w)| r[16] * w)
                .sum::<f64>()
                / weight_sum,
            2.5,
        )
    } else {
        2.5
    };
    let after_ceiling = if options.score_ceiling && any_active {
        instant
            .iter()
            .zip(&rows)
            .zip(&weights)
            .map(|((v, r), w)| v[1].min(empirical::inverse_calibrated_quality_response(r[16])) * w)
            .sum()
    } else {
        after_aerosol
    };
    let multiplier = if options.apply_duration && (any_active || options.mode == Mode::Regional) {
        empirical::duration_factor(duration)
    } else {
        1.0
    };
    let duration_delta = after_ceiling * (multiplier - 1.0);
    let after_duration = (after_ceiling + duration_delta).max(0.0);
    let pre_response = if any_active {
        clip(
            if options.mode == Mode::Scalar {
                after_duration.min(empirical::inverse_calibrated_quality_response(ceiling))
            } else {
                after_duration
            },
            2.5,
        )
    } else {
        0.0
    };
    let index = empirical::calibrated_quality_response(pre_response);
    let mut peak = 0;
    for i in 1..n {
        if rows[i][1] > rows[peak][1] {
            peak = i;
        }
    }
    let statistics = field_statistics(&rows, &widths, &active);
    let active_field = |field: usize, high| clip(statistics[0][field], high);
    let full_field = |field: usize, high| clip(statistics[1][field], high);
    let mean_view = active_field(0, 1.0);
    let mean_burn = active_field(1, 1.0);
    let ratio = if mean_view > 0.0 {
        clip(mean_burn / mean_view.max(1e-6), 1.0)
    } else {
        0.0
    };
    let block_mean = mean(rows.iter().filter(|r| r[0] >= 0.005).map(|r| r[5]));
    let block_peak = rows
        .iter()
        .filter(|r| r[0] >= 0.005)
        .map(|r| r[5])
        .fold(0.0, f64::max);
    let local_max = |field: usize| statistics[2][field];
    let (local_quality, local_peak) = if options.local_columns {
        if rows.iter().any(|r| r[11] >= 0.005) {
            let local: Vec<_> = rows
                .iter()
                .map(|r| {
                    let mut local = *r;
                    local[..5].copy_from_slice(&r[10..15]);
                    local[5] = 0.0;
                    local
                })
                .collect();
            let mut local_options = options;
            local_options.local_columns = false;
            local_options.score_ceiling = false;
            let local_aod = [aod];
            let local_result = score(
                &local,
                local_options,
                if options.mode == Mode::Scalar {
                    Some(&local_aod)
                } else {
                    None
                },
            )?;
            (
                local_result.index(),
                local
                    .iter()
                    .map(|r| score_instant(r, true, false)[0])
                    .fold(0.0, f64::max),
            )
        } else {
            (0.0, 0.0)
        }
    } else {
        (f64::NAN, f64::NAN)
    };
    result.limiting = if capped {
        0
    } else if mean_burn < 0.005 {
        1
    } else if block_mean >= 0.55 {
        2
    } else if material_factor < 0.70 {
        3
    } else if incoming_factor < 0.70 {
        4
    } else if observer_factor < 0.70 {
        5
    } else {
        6
    };
    let aod = if options.mode == Mode::Regional {
        aod.max(0.0)
    } else {
        aod
    };
    macro_rules! put {
        ($field:ident, $value:expr) => {
            result.values[Metric::$field as usize] = $value;
        };
    }
    put!(Index, index);
    put!(Base, coverage);
    put!(
        Brightness,
        mean(
            rows.iter()
                .zip(&active)
                .filter(|(_, a)| **a != 0)
                .map(|(r, _)| r[7])
        )
    );
    put!(CoverageEffective, mean_burn);
    put!(CoverageMean, mean_burn);
    put!(CoveragePeak, rows[peak][1]);
    put!(BurnRatioFactor, 1.0);
    put!(BurnToView, ratio);
    put!(Material, material);
    put!(CoverageBase, coverage);
    put!(CoverageScore, coverage);
    put!(DurationDelta, duration_delta);
    put!(DurationFactor, multiplier);
    put!(Duration, duration);
    put!(Window, window);
    put!(WindowPreResponse, after_ceiling);
    put!(ActiveThreshold, 0.005);
    put!(BurnEffective, mean_burn);
    put!(BurnFull, full_field(1, 1.0));
    put!(BurnMean, mean_burn);
    put!(BurnPeak, rows[peak][1]);
    put!(IncomingEffective, active_field(4, 2.5));
    put!(IncomingFull, full_field(4, 2.5));
    put!(IncomingMean, active_field(4, 2.5));
    put!(LoadEffective, active_field(2, 2.5));
    put!(LoadFull, full_field(2, 2.5));
    put!(LoadMean, active_field(2, 2.5));
    put!(LoadPeak, rows[peak][2]);
    put!(MaterialLoadEffective, active_field(3, 2.5));
    put!(MaterialLoadFull, full_field(3, 2.5));
    put!(MaterialLoadMean, active_field(3, 2.5));
    put!(MaterialLoadPeak, rows[peak][3]);
    put!(RatioEffective, ratio);
    put!(
        RatioPeak,
        if rows[peak][0] > 0.0 {
            clip(rows[peak][1] / rows[peak][0].max(1e-6), 1.0)
        } else {
            0.0
        }
    );
    put!(Radius, options.radius_km);
    put!(Sector, 90.0);
    put!(ViewEffective, mean_view);
    put!(ViewFull, full_field(0, 1.0));
    put!(ViewMean, mean_view);
    put!(ViewPeak, rows[peak][0]);
    put!(IncomingDelta, incoming_delta);
    put!(IncomingFactor, incoming_factor);
    put!(LocalAod, aod);
    put!(MaterialDelta, material_delta);
    put!(MaterialFactor, material_factor);
    put!(MaterialReference, 0.72);
    put!(
        VetoSteps,
        if options.mode == Mode::Scalar {
            rows.iter().filter(|r| r[15] < 0.5).count() as f64
        } else {
            0.0
        }
    );
    put!(ObserverDelta, observer_delta);
    put!(ObserverFactor, observer_factor);
    put!(LocalQuality, local_quality);
    put!(LocalPeak, local_peak);
    put!(LocalBurnMean, full_field(11, 1.0));
    put!(LocalBurnPeak, local_max(11));
    put!(LocalLoadMean, full_field(12, 2.5));
    put!(LocalLoadPeak, local_max(12));
    put!(LocalMaterialMean, full_field(13, 2.5));
    put!(LocalMaterialPeak, local_max(13));
    put!(LocalViewMean, full_field(10, 1.0));
    put!(LocalViewPeak, local_max(10));
    put!(
        ActiveSteps,
        active.iter().filter(|a| **a != 0).count() as f64
    );
    put!(PreResponse, pre_response);
    put!(QualityAod, aod);
    put!(ResponseDelta, index - pre_response);
    put!(ResponseExponent, 1.2);
    put!(ResponseScale, 1.225);
    put!(Raw, after_ceiling);
    put!(Max, 2.5);
    put!(BlockEffective, block_mean);
    put!(BlockMean, block_mean);
    put!(BlockPeak, block_peak);
    put!(
        Visibility,
        rows.iter()
            .zip(&active)
            .filter(|(_, a)| **a != 0)
            .map(|(r, _)| r[6])
            .fold(0.0, f64::max)
    );
    put!(Ceiling, ceiling);
    put!(CeilingDelta, after_ceiling - after_aerosol);
    Ok(result)
}

/// Fixed-format batch composition. The serial choice is retained for profiling
/// and bitwise checks; parallel errors are still reported in point order.
pub fn score_many(
    rows: &[Row],
    points: usize,
    steps: usize,
    options: Options,
    parallel: bool,
) -> Result<Vec<Quality>, String> {
    if points > 500_000
        || !(1..=empirical::MAX_SAMPLES).contains(&steps)
        || points.checked_mul(steps) != Some(rows.len())
        || rows.len() > 10_000_000
    {
        return Err("invalid observer-quality batch dimensions".into());
    }
    score_batch_with(points, options, parallel, |i, options| {
        score(&rows[i * steps..(i + 1) * steps], options, None)
    })
}

/// A per-point adapter avoids allocating a second whole-batch input tensor
/// when native float32 assessment records are already available.
pub fn score_batch_with<F>(
    points: usize,
    options: Options,
    parallel: bool,
    event: F,
) -> Result<Vec<Quality>, String>
where
    F: Fn(usize, Options) -> Result<Quality, String> + Sync,
{
    if points > 500_000 {
        return Err("quality point count exceeds limit".into());
    }
    let empty = score(&[], options, None)?;
    let mut output = vec![empty; points];
    let fill = |point: usize, target: &mut Quality| -> Result<(), String> {
        *target = event(point, options).map_err(|e| format!("event {point}: {e}"))?;
        Ok(())
    };
    if parallel && points >= 1024 {
        let results: Vec<_> = output
            .par_iter_mut()
            .enumerate()
            .with_min_len(64)
            .map(|(i, target)| fill(i, target))
            .collect();
        for result in results {
            result?;
        }
    } else {
        for (i, target) in output.iter_mut().enumerate() {
            fill(i, target)?;
        }
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn widths_and_empty_events_preserve_api_differences() {
        assert_eq!(sample_widths(&[10.0, 0.0, 5.0]), [2.5, 2.5, 5.0]);
        let options = Options {
            mode: Mode::Scalar,
            apply_duration: true,
            local_columns: false,
            score_ceiling: false,
            radius_km: 200.0,
        };
        assert!(!score(&[], options, None).unwrap().has_samples);
        let mut row = [0.0; INPUT_FIELDS];
        row[6] = 1.0;
        row[15] = 1.0;
        row[16] = 2.5;
        row[17] = 1.0;
        assert_eq!(
            score(&[row], options, None).unwrap().values[Metric::DurationFactor as usize],
            1.0
        );
        assert_eq!(
            score(
                &[row],
                Options {
                    mode: Mode::Regional,
                    ..options
                },
                None
            )
            .unwrap()
            .values[Metric::DurationFactor as usize],
            0.0
        );
    }

    fn fixture(points: usize) -> Vec<Row> {
        (0..points * 20)
            .map(|i| {
                let burn = (i % 37) as f64 / 50.0;
                [
                    0.8,
                    burn,
                    burn * 1.152,
                    burn * 1.8,
                    burn * 1.44,
                    0.5,
                    if i % 11 == 0 { 0.049 } else { 0.8 },
                    0.7,
                    (i % 20) as f64 * 5.0,
                    if i % 7 == 0 { f64::NAN } else { 0.1 },
                    0.6,
                    burn * 0.5,
                    burn * 0.576,
                    burn * 0.9,
                    burn * 0.72,
                    f64::from(i % 13 != 0),
                    0.6,
                    f64::from(i % 17 != 0),
                ]
            })
            .collect()
    }

    #[test]
    fn cached_field_reductions_match_previous_summation_bits() {
        let rows = fixture(1);
        for widths in [
            sample_widths(&(0..20).map(|i| i as f64 * 5.0).collect::<Vec<_>>()),
            vec![0.0; 20],
        ] {
            for active in [
                vec![0; 20],
                vec![1; 20],
                (0..20).map(|i| u8::from(i % 3 == 0)).collect(),
            ] {
                let actual = field_statistics(&rows, &widths, &active);
                for field in [0, 1, 2, 3, 4, 10, 11, 12, 13] {
                    let values: Vec<_> = rows.iter().map(|r| r[field]).collect();
                    let total: f64 = widths.iter().sum();
                    let active_width: f64 = widths
                        .iter()
                        .zip(&active)
                        .filter(|(_, a)| **a != 0)
                        .map(|(w, _)| w)
                        .sum();
                    let full = if total > 0.0 {
                        values.iter().zip(&widths).map(|(v, w)| v * w).sum::<f64>() / total
                    } else {
                        mean(values.iter().copied())
                    };
                    let selected = if active_width > 0.0 {
                        values
                            .iter()
                            .zip(&widths)
                            .zip(&active)
                            .filter(|(_, a)| **a != 0)
                            .map(|((v, w), _)| v * w)
                            .sum::<f64>()
                            / active_width
                    } else {
                        mean(
                            values
                                .iter()
                                .zip(&active)
                                .filter(|(_, a)| **a != 0)
                                .map(|(v, _)| *v),
                        )
                    };
                    assert_eq!(actual[0][field].to_bits(), selected.to_bits());
                    assert_eq!(actual[1][field].to_bits(), full.to_bits());
                    assert_eq!(
                        actual[2][field].to_bits(),
                        values.iter().copied().fold(0.0, f64::max).to_bits()
                    );
                }
            }
        }
    }

    #[test]
    fn ordered_parallel_composition_preserves_all_fields_and_errors() {
        for points in [1023, 1024] {
            let mut rows = fixture(points);
            for mode in [Mode::Scalar, Mode::Regional] {
                let options = Options {
                    mode,
                    apply_duration: true,
                    local_columns: true,
                    score_ceiling: mode == Mode::Scalar,
                    radius_km: 200.0,
                };
                let expected = score_many(&rows, points, 20, options, false).unwrap();
                for threads in [1, 4] {
                    let pool = rayon::ThreadPoolBuilder::new()
                        .num_threads(threads)
                        .build()
                        .unwrap();
                    let actual = pool
                        .install(|| score_many(&rows, points, 20, options, true))
                        .unwrap();
                    for (a, b) in actual.iter().zip(&expected) {
                        assert_eq!(a.flags(), b.flags());
                        for (a, b) in a.values.iter().zip(b.values) {
                            assert_eq!(a.to_bits(), b.to_bits());
                        }
                    }
                }
                rows[7 * 20 + 3][17] = 0.5;
                rows[29 * 20 + 3][17] = 0.5;
                for parallel in [false, true] {
                    assert!(score_many(&rows, points, 20, options, parallel)
                        .err()
                        .unwrap()
                        .starts_with("event 7:"));
                }
                rows[7 * 20 + 3][17] = 1.0;
                rows[29 * 20 + 3][17] = 1.0;
            }
        }
    }

    #[test]
    fn gapped_event_normalizes_ceiling_and_diagnostic_factors() {
        // Two 10-minute active bursts separated by a 20-minute inactive gap:
        // total active duration is 20 min, but any 20-min window covers at most
        // 10 active minutes (weight_sum = 0.5 < 0.70).
        let make_row = |offset: f64, burn: f64, ceiling: f64| -> Row {
            [
                0.9,
                burn,
                burn * 2.5,
                burn * 2.5,
                burn * 2.5,
                0.0,
                1.0,
                0.9,
                offset,
                0.05,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1.0,
                ceiling,
                1.0,
            ]
        };
        // Offsets spaced by 10 min => widths = [5, 10, 10, 10, 5]; active at
        // offsets 0, 10, 40, 50 => active widths [5, 10, 10, 5] (total 30 min)
        // with a 20-min inactive gap at offsets 20, 30.
        let rows = vec![
            make_row(0.0, 0.9, 2.5),
            make_row(10.0, 0.9, 2.5),
            make_row(20.0, 0.0, 2.5),
            make_row(30.0, 0.0, 2.5),
            make_row(40.0, 0.9, 2.5),
            make_row(50.0, 0.9, 2.5),
        ];
        // Active widths inside the best 20-min window: 5 + 10 = 15 min => weight_sum = 0.75.
        // Now make an even narrower burst: offsets 0, 5, 15, 25, 35, 40 with active at 0, 5, 35, 40.
        let narrow_rows = vec![
            make_row(0.0, 0.9, 2.5),
            make_row(5.0, 0.9, 2.5),
            make_row(15.0, 0.0, 2.5),
            make_row(25.0, 0.0, 2.5),
            make_row(35.0, 0.9, 2.5),
            make_row(40.0, 0.9, 2.5),
        ];
        // Widths: [2.5, 7.5, 10.0, 10.0, 7.5, 2.5]; active total = 20.0 min,
        // best 20-min window covers only one 10-min burst => weight_sum = 0.50 (< 0.70).
        let options = Options {
            mode: Mode::Scalar,
            apply_duration: true,
            local_columns: false,
            score_ceiling: true,
            radius_km: 200.0,
        };
        let without_ceiling = score(
            &narrow_rows,
            Options {
                score_ceiling: false,
                ..options
            },
            None,
        )
        .unwrap();
        let with_max_ceiling = score(&narrow_rows, options, None).unwrap();
        assert!((with_max_ceiling.values[Metric::Ceiling as usize] - 2.5).abs() < 1e-12);
        assert!((with_max_ceiling.index() - without_ceiling.index()).abs() < 1e-12);
        assert!((with_max_ceiling.values[Metric::Material as usize] - 1.0).abs() < 1e-12);
        assert!((with_max_ceiling.values[Metric::MaterialFactor as usize] - 1.0).abs() < 1e-12);
        assert!((with_max_ceiling.values[Metric::IncomingFactor as usize] - 1.0).abs() < 1e-12);
        assert!((with_max_ceiling.values[Metric::ObserverFactor as usize] - 1.0).abs() < 1e-12);
        assert_eq!(with_max_ceiling.limiting, 6);

        // When a real ceiling (1.2) is present on active steps, the reported
        // event ceiling remains 1.2 rather than being shrunk by weight_sum (0.75).
        let capped_rows: Vec<Row> = rows
            .into_iter()
            .map(|mut r| {
                r[16] = 1.2;
                r
            })
            .collect();
        let capped = score(&capped_rows, options, None).unwrap();
        assert!((capped.values[Metric::Ceiling as usize] - 1.2).abs() < 1e-12);
        assert!(capped.index() <= 1.2 + 1e-12);
    }
}
