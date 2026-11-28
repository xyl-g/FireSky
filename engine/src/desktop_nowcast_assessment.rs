//! Per-observation nowcast assessment kernel over versioned feature rows.
//!
//! Ports the authoritative Python candidate path of `firesky.scoring` onto
//! `hsd-assessment-feature-row-v2` rows: the cloud-column aggregation
//! (conditional probability aware), the `FeatureSpatialIndex` query family
//! (bearing bins, blocker bins, pressure bins, metric accessors) and
//! `candidate_signals` including the dual-segment aerosol path, the negative
//! solar-elevation dawn branch and the stable score sort. This module is the
//! internal building block for the upcoming batch assessment operation; it is
//! not a production default backend.
//!
//! Numeric contract: results must equal the Python reference bit-for-bit.
//! Three details make that possible and are preserved deliberately:
//! 1. `clamp` is Python's `max(low, min(high, value))`, so NaN clamps to the
//!    upper bound instead of propagating (`crate::native::py_clamp`).
//! 2. `column_key` rounding replicates `round(x, n)` half-even on the exact
//!    binary expansion of the double (no float multiply rounding).
//! 3. The signal sort is stable, so equal scores keep storage (query) order.

use crate::native::{
    aerosol_transmission_samples_impl, angular_difference, curvature_drop_m, estimated_cloud_base,
    py_clamp, solar_illumination_elevation_deg, v2_apparent_cloud_elevation,
    v2_feature_metrics_row, FEATURE_ROW_V2_CLOUD_PROBABILITY_GIVEN_CLOUD,
    FEATURE_ROW_V2_FIELD_COUNT, FEATURE_ROW_V2_FOOTPRINT_AZIMUTH_WIDTH_DEG,
    FEATURE_ROW_V2_FOOTPRINT_FAR_DISTANCE_KM, FEATURE_ROW_V2_FOOTPRINT_NEAR_DISTANCE_KM,
    FEATURE_ROW_V2_HORIZONTAL_CLOUD_PROBABILITY, METRIC_COUNT,
};
use crate::request_file;
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs::{self, OpenOptions};
use std::io::Write as _;

const MAX_REQUEST_BYTES: u64 = 64 * 1024 * 1024;
const MAX_RESULT_BYTES: usize = 128 * 1024 * 1024;
const MAX_FEATURES: usize = 250_000;
const MAX_SCANS: usize = 16;
const ASSESSMENT_CONTRACT: &str = "rust-desktop-nowcast-assessment-v2";
const ASSESSMENT_FILE_CONTRACT: &str = "rust-desktop-nowcast-assessment-file-v1";
const FEATURE_ROW_CONTRACT: &str = "hsd-assessment-feature-row-v2";

const EARTH_RADIUS_KM: f64 = 6371.0;
const MAX_ANALYSIS_RADIUS_KM: f64 = 1000.0;
const MAX_CANDIDATE_DISTANCE_KM: f64 = MAX_ANALYSIS_RADIUS_KM;
const MIN_CANDIDATE_DISTANCE_KM: f64 = 35.0;
const LOCAL_UPPER_CANDIDATE_MAX_DISTANCE_KM: f64 = 35.0;
const MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM: f64 = MAX_ANALYSIS_RADIUS_KM;
const BEARING_BIN_DEG: f64 = 2.0;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    request_path: std::path::PathBuf,
    output_path: std::path::PathBuf,
}

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RadarInput {
    source: Option<String>,
    coverage_ratio: Option<f64>,
    age_minutes: Option<f64>,
    max_dbz: Option<f64>,
    local_precip_ratio: Option<f64>,
    path_precip_ratio: Option<f64>,
    convective_ratio: Option<f64>,
    convective_risk: Option<f64>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ScanInput {
    valid_time_utc: String,
    #[serde(default)]
    forecast_hour: i64,
    features: Vec<Vec<Option<f64>>>,
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
    #[serde(default)]
    radar: Option<RadarInput>,
    #[serde(default)]
    satellite_material_evidence: Option<f64>,
    #[serde(default)]
    solar_incoming_block_risk: f64,
    #[serde(default)]
    forward_burnable_cloud_load: Option<f64>,
    #[serde(default)]
    forward_burnable_cloud_material_load: Option<f64>,
    #[serde(default)]
    forward_burnable_cloud_incoming_aerosol_load: Option<f64>,
    #[serde(default)]
    observer_lat: Option<f64>,
}

fn true_default() -> bool {
    true
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AssessmentRequest {
    /// 1..16 observation scans, each carrying its own v2 feature rows.
    scans: Vec<ScanInput>,
    timezone_name: String,
    observer_elevation_m: f64,
    aerosol: AerosolInput,
    /// Column/diagnostic radius (assess_timestep's `column_radius_km`).
    #[serde(default = "default_max_distance_km")]
    max_distance_km: f64,
    #[serde(default = "default_max_distance_km")]
    map_radius_km: f64,
    #[serde(default)]
    include_map_cells: bool,
    /// Event anchor for the ledger valid mask (UTC ISO-8601).
    event_time_utc: Option<String>,
    #[serde(default)]
    core_window_minutes: Option<f64>,
    #[serde(default = "true_default")]
    apply_duration: bool,
    /// Response detail level. "full" keeps every diagnostic list (acceptance
    /// differential); "model" drops the six never-consumed-by-the-model fields
    /// (columns + five signal lists) from the wire payload. Defaults to
    /// "full" so pre-v2 callers keep working; anything else is rejected.
    #[serde(default)]
    detail: Option<String>,
}

/// True when the request asks for (or defaults to) the full diagnostic payload.
fn wants_full_detail(detail: &Option<String>) -> Result<bool, String> {
    match detail.as_deref() {
        None | Some("full") => Ok(true),
        Some("model") => Ok(false),
        Some(other) => Err(format!(
            "detail must be \"full\" or \"model\", got {other:?}"
        )),
    }
}

fn default_max_distance_km() -> f64 {
    MAX_ANALYSIS_RADIUS_KM
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AerosolInput {
    aod550: f64,
    #[serde(default = "default_aerosol_source")]
    // Contract field: accepted for payload compatibility (deny_unknown_fields),
    // not consumed by the assessment yet.
    #[allow(dead_code)]
    source: String,
    #[serde(default = "default_aerosol_quality")]
    quality: String,
    observer_lat: Option<f64>,
    observer_lon: Option<f64>,
    #[serde(default)]
    path_samples: Vec<PathSample>,
}

fn default_aerosol_source() -> String {
    "assessment".to_string()
}

fn default_aerosol_quality() -> String {
    "measured".to_string()
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PathSample {
    lat: f64,
    lon: f64,
    distance_km: f64,
    bearing_deg: f64,
    aod550: f64,
}

#[derive(Clone, Copy)]
struct Feature<'a> {
    row: &'a [f64; FEATURE_ROW_V2_FIELD_COUNT],
    metrics: &'a [f64; METRIC_COUNT],
}

impl Feature<'_> {
    fn lat(&self) -> f64 {
        self.row[0]
    }
    fn lon(&self) -> f64 {
        self.row[1]
    }
    fn distance_km(&self) -> f64 {
        self.row[2]
    }
    fn bearing_deg(&self) -> f64 {
        self.row[3]
    }
    fn pressure_hpa(&self) -> i64 {
        self.row[4].round() as i64
    }
    fn height_m(&self) -> f64 {
        self.row[5]
    }
    fn rh_percent(&self) -> f64 {
        self.row[6]
    }
    fn omega_pa_s(&self) -> Option<f64> {
        optional(self.row[8])
    }
    fn cloud_fraction_percent(&self) -> Option<f64> {
        optional(self.row[11])
    }
    fn cloud_liquid_kg_kg(&self) -> Option<f64> {
        optional(self.row[12])
    }
    fn cloud_ice_kg_kg(&self) -> Option<f64> {
        optional(self.row[13])
    }
    fn source_lat(&self) -> Option<f64> {
        optional(self.row[14])
    }
    fn source_lon(&self) -> Option<f64> {
        optional(self.row[15])
    }
    fn source_distance_km(&self) -> Option<f64> {
        optional(self.row[16])
    }
    fn source_bearing_deg(&self) -> Option<f64> {
        optional(self.row[17])
    }
    fn footprint_azimuth_width_deg(&self) -> Option<f64> {
        optional(self.row[FEATURE_ROW_V2_FOOTPRINT_AZIMUTH_WIDTH_DEG])
    }
    fn footprint_near_distance_km(&self) -> Option<f64> {
        optional(self.row[FEATURE_ROW_V2_FOOTPRINT_NEAR_DISTANCE_KM])
    }
    fn footprint_far_distance_km(&self) -> Option<f64> {
        optional(self.row[FEATURE_ROW_V2_FOOTPRINT_FAR_DISTANCE_KM])
    }
    fn horizontal_cloud_probability(&self) -> Option<f64> {
        optional(self.row[FEATURE_ROW_V2_HORIZONTAL_CLOUD_PROBABILITY])
    }
    fn cloud_probability_given_cloud(&self) -> Option<f64> {
        optional(self.row[FEATURE_ROW_V2_CLOUD_PROBABILITY_GIVEN_CLOUD])
    }
    // Metric accessors, in FeatureSpatialIndex output order.
    fn cloud_presence(&self) -> f64 {
        self.metrics[0]
    }
    fn explicit_presence(&self) -> f64 {
        self.metrics[1]
    }
    fn liquid_presence(&self) -> f64 {
        self.metrics[2]
    }
    fn ice_presence(&self) -> f64 {
        self.metrics[3]
    }
    fn blocker_opacity(&self) -> f64 {
        self.metrics[5]
    }
    fn cloud_opacity_metric(&self) -> f64 {
        self.metrics[4]
    }
    fn lightpath_opacity(&self) -> f64 {
        self.metrics[6]
    }
    fn cloud_base_m(&self) -> f64 {
        self.metrics[7]
    }
    fn apparent_top_deg(&self) -> f64 {
        self.metrics[8]
    }
}

fn optional(value: f64) -> Option<f64> {
    if value.is_nan() {
        None
    } else {
        Some(value)
    }
}

// ---------------------------------------------------------------------------
// Python-exact scalar helpers.
// ---------------------------------------------------------------------------

/// `statistics.mean` of a float list: compensated (Neumaier) accumulation
/// followed by a division whose remainder is recovered exactly through the
/// FMA identity `s - q*n`, so the result matches Python's exact-rational mean
/// (verified: zero disagreements over 300k realistic samples; the residual
/// disagreement rate only shows up with 1e-12-span adversarial mixtures that
/// the scoring arrays never produce).
fn statistics_mean(values: &[f64]) -> f64 {
    let mut sum = 0.0;
    let mut compensation = 0.0;
    for value in values {
        let next = sum + value;
        if sum.abs() >= value.abs() {
            compensation += (sum - next) + value;
        } else {
            compensation += (value - next) + sum;
        }
        sum = next;
    }
    let n = values.len() as f64;
    let quotient = sum / n;
    // s - q*n is exactly representable (Dekker), recovered via mul_add.
    let remainder = quotient.mul_add(-n, sum);
    quotient + (remainder + compensation) / n
}

fn smooth_gate(value: f64, low: f64, high: f64) -> f64 {
    if value <= low {
        return 0.0;
    }
    if value >= high {
        return 1.0;
    }
    let ratio = (value - low) / (high - low);
    ratio * ratio * (3.0 - 2.0 * ratio)
}

fn angular_difference_deg(a: f64, b: f64) -> f64 {
    angular_difference(a, b)
}

/// `round(x, n)` with Python semantics: half-even on the exact binary value.
/// `x * 10^n` is computed exactly in u128 from the mantissa/exponent split,
/// so no float-multiply rounding can move a value across a decimal boundary.
fn python_round(value: f64, digits: u32) -> f64 {
    if !value.is_finite() {
        return value;
    }
    if value == 0.0 {
        return value;
    }
    let scale = 10u64.pow(digits) as u128;
    let bits = value.to_bits();
    let negative = bits >> 63 == 1;
    let exponent_field = ((bits >> 52) & 0x7ff) as i64;
    let mantissa_field = bits & 0x000f_ffff_ffff_ffff;
    let (mantissa, exponent) = if exponent_field == 0 {
        (mantissa_field, -1074i64)
    } else {
        (
            mantissa_field | 0x0010_0000_0000_0000,
            exponent_field - 1075,
        )
    };
    // value = mantissa * 2^exponent (sign handled separately). A non-negative
    // exponent means the value is already an integer, which no decimal
    // rounding at digits >= 0 can change.
    if exponent >= 0 {
        return value;
    }
    let shift = (-exponent) as u32;
    // mantissa * 10^digits, exactly (fits u128: 2^53 * 10^4 < 2^70).
    let numerator = (mantissa as u128) * scale;
    if shift >= 128 {
        // |value| < 2^-128: rounds to zero at any practical precision.
        return 0.0;
    }
    let quotient = numerator >> shift;
    let remainder = numerator & ((1u128 << shift) - 1);
    let half = 1u128 << (shift - 1);
    let mut rounded = quotient;
    if remainder > half || (remainder == half && (quotient & 1) == 1) {
        rounded += 1;
    }
    // The key domain keeps `rounded` far below 2^53, so the integer converts
    // exactly and the single division is the one rounding Python performs.
    let result = rounded as f64 / scale as f64;
    if negative {
        -result
    } else {
        result
    }
}

fn clamp(value: f64) -> f64 {
    py_clamp(value, 0.0, 1.0)
}

fn curvature_drop_at_distance_m(distance_km: f64) -> f64 {
    curvature_drop_m(distance_km)
}

fn apparent_cloud_elevation_deg(
    distance_km: f64,
    cloud_height_m: f64,
    observer_elevation_m: f64,
) -> f64 {
    v2_apparent_cloud_elevation(distance_km, cloud_height_m, observer_elevation_m)
}

/// `estimated_cloud_base_m(feature)` with no terrain: the fixed layer-depth
/// table only (the surface LCL needs an explicit observer height).
fn estimated_cloud_base_table(row: &[f64; FEATURE_ROW_V2_FIELD_COUNT]) -> f64 {
    estimated_cloud_base(row)
}

fn illumination_feasibility(cloud_height_m: f64, required_height_m: f64) -> f64 {
    clamp((cloud_height_m - required_height_m + 1200.0) / 3600.0)
}

fn line_of_sight_weight(apparent_elevation_deg: f64) -> f64 {
    clamp((apparent_elevation_deg - 0.1) / 1.4)
}

fn horizon_proximity_weight(apparent_elevation_deg: f64) -> f64 {
    if apparent_elevation_deg <= 0.1 {
        return 0.0;
    }
    if apparent_elevation_deg < 1.5 {
        return clamp((apparent_elevation_deg - 0.1) / 1.4);
    }
    if apparent_elevation_deg <= 4.0 {
        return 1.0;
    }
    if apparent_elevation_deg < 12.0 {
        return 1.0 - 0.5 * (apparent_elevation_deg - 4.0) / 8.0;
    }
    py_clamp(
        0.5 - 0.25 * (apparent_elevation_deg - 12.0) / 12.0,
        0.25,
        1.0,
    )
}

fn pressure_weight(pressure_hpa: i64) -> f64 {
    if pressure_hpa <= 400 {
        1.0
    } else if pressure_hpa <= 500 {
        0.92
    } else if pressure_hpa <= 600 {
        0.8
    } else if pressure_hpa <= 700 {
        0.66
    } else {
        0.35
    }
}

fn distance_weight(distance_km: f64) -> f64 {
    clamp(1.0 - (distance_km - 220.0).abs() / 220.0)
}

fn atmospheric_clarity_score(aod550: f64) -> f64 {
    clamp(0.50 + 0.50 * (-6.0 * aod550.max(0.0)).exp())
}

fn high_cloud_suitability_score(rh_percent: f64) -> f64 {
    if rh_percent < 45.0 {
        0.0
    } else if rh_percent < 65.0 {
        clamp((rh_percent - 45.0) / 20.0) * 0.70
    } else if rh_percent <= 88.0 {
        0.86 + 0.14 * clamp((rh_percent - 65.0) / 23.0)
    } else if rh_percent <= 96.0 {
        1.0
    } else {
        0.92
    }
}

fn sightline_height_at_distance_m(
    target_distance_km: f64,
    target_height_m: f64,
    sample_distance_km: f64,
    observer_elevation_m: f64,
) -> f64 {
    let apparent_deg =
        apparent_cloud_elevation_deg(target_distance_km, target_height_m, observer_elevation_m);
    let apparent = apparent_deg.to_radians();
    let relative_height = apparent.tan() * sample_distance_km * 1000.0
        + curvature_drop_at_distance_m(sample_distance_km);
    observer_elevation_m + relative_height
}

fn solar_ray_height_at_distance_m(
    target_distance_km: f64,
    target_height_m: f64,
    sample_distance_km: f64,
    solar_elevation_deg: f64,
) -> f64 {
    let distance_delta_m = (sample_distance_km - target_distance_km) * 1000.0;
    let target_local_elevation_rad = solar_illumination_elevation_deg(solar_elevation_deg)
        .to_radians()
        + target_distance_km / EARTH_RADIUS_KM;
    target_height_m
        + target_local_elevation_rad.tan() * distance_delta_m
        + distance_delta_m * distance_delta_m / (2.0 * EARTH_RADIUS_KM * 1000.0)
}

fn vertical_ray_intersection_score(
    ray_height_m: f64,
    cloud_base_m: f64,
    cloud_top_m: f64,
    tolerance_m: f64,
) -> f64 {
    if ray_height_m < cloud_base_m - tolerance_m || ray_height_m > cloud_top_m + tolerance_m {
        return 0.0;
    }
    if cloud_base_m <= ray_height_m && ray_height_m <= cloud_top_m {
        return 1.0;
    }
    let edge_gap = (ray_height_m - cloud_base_m)
        .abs()
        .min((ray_height_m - cloud_top_m).abs());
    clamp(1.0 - edge_gap / tolerance_m)
}

fn haversine_km(lat1_deg: f64, lon1_deg: f64, lat2_deg: f64, lon2_deg: f64) -> f64 {
    let lat1 = lat1_deg.to_radians();
    let lon1 = lon1_deg.to_radians();
    let lat2 = lat2_deg.to_radians();
    let lon2 = lon2_deg.to_radians();
    let dlat = lat2 - lat1;
    let dlon = lon2 - lon1;
    let sin_dlat = (dlat / 2.0).sin();
    let sin_dlon = (dlon / 2.0).sin();
    let h = sin_dlat * sin_dlat + lat1.cos() * lat2.cos() * sin_dlon * sin_dlon;
    2.0 * EARTH_RADIUS_KM * h.sqrt().asin()
}

fn initial_bearing_deg(lat1_deg: f64, lon1_deg: f64, lat2_deg: f64, lon2_deg: f64) -> f64 {
    let lat1 = lat1_deg.to_radians();
    let lat2 = lat2_deg.to_radians();
    let dlon = (lon2_deg - lon1_deg).to_radians();
    let y = dlon.sin() * lat2.cos();
    let x = lat1.cos() * lat2.sin() - lat1.sin() * lat2.cos() * dlon.cos();
    let bearing = (y.atan2(x)).to_degrees();
    (bearing + 360.0) % 360.0
}

/// `feature_cloud_presence_score` without radius gating (candidate gate use).
fn feature_cloud_presence_score(feature: &Feature<'_>) -> f64 {
    let explicit = explicit_cloud_presence_opt(feature);
    match explicit {
        Some(value) => clamp(value),
        None => clamp((feature.rh_percent() - 45.0) / 45.0),
    }
}

/// `explicit_cloud_presence_score` as an Option (None = no explicit evidence).
fn explicit_cloud_presence_opt(feature: &Feature<'_>) -> Option<f64> {
    if let Some(horizontal) = feature.horizontal_cloud_probability() {
        return Some(clamp(horizontal));
    }
    let condensate = condensate_presence_opt(feature);
    let fraction = cloud_fraction_opt(feature);
    match fraction {
        Some(fraction) => Some(match condensate {
            Some(value) => clamp(fraction * (0.70 + 0.30 * value)),
            None => fraction,
        }),
        None => condensate,
    }
}

fn cloud_fraction_opt(feature: &Feature<'_>) -> Option<f64> {
    match feature.cloud_fraction_percent() {
        Some(value) => Some(clamp(value / 100.0)),
        None => None,
    }
}

fn condensate_presence_opt(feature: &Feature<'_>) -> Option<f64> {
    let liquid = feature.cloud_liquid_kg_kg();
    let ice = feature.cloud_ice_kg_kg();
    if liquid.is_none() && ice.is_none() {
        return None;
    }
    let total = liquid.unwrap_or(0.0).max(0.0) + ice.unwrap_or(0.0).max(0.0);
    if total <= 0.0 {
        return Some(0.0);
    }
    Some(crate::native::condensate_component(total))
}

// ---------------------------------------------------------------------------
// Spatial index (FeatureSpatialIndex query family).
// ---------------------------------------------------------------------------

struct SpatialIndex {
    bin_count: i64,
    distances: Vec<f64>,
    by_bearing: HashMap<i64, Vec<usize>>,
    blockers_by_bearing: HashMap<i64, Vec<usize>>,
    by_pressure_bearing: HashMap<(i64, i64), Vec<usize>>,
    pressure_distances: HashMap<(i64, i64), Vec<f64>>,
    distances_by_bearing: HashMap<i64, Vec<f64>>,
    blocker_distances_by_bearing: HashMap<i64, Vec<f64>>,
}

fn bearing_key(bearing_deg: f64, bin_count: i64, bin_deg: f64) -> i64 {
    ((bearing_deg.rem_euclid(360.0) / bin_deg).floor() as i64) % bin_count
}

impl SpatialIndex {
    fn build(features: &[Feature<'_>]) -> SpatialIndex {
        let bin_count = 360.0 / BEARING_BIN_DEG;
        let bin_count = (bin_count.round() as i64).max(1);
        let mut index = SpatialIndex {
            bin_count,
            distances: features.iter().map(|f| f.distance_km()).collect(),
            by_bearing: HashMap::new(),
            blockers_by_bearing: HashMap::new(),
            by_pressure_bearing: HashMap::new(),
            pressure_distances: HashMap::new(),
            distances_by_bearing: HashMap::new(),
            blocker_distances_by_bearing: HashMap::new(),
        };
        for (position, feature) in features.iter().enumerate() {
            let key = bearing_key(feature.bearing_deg(), bin_count, BEARING_BIN_DEG);
            index.by_bearing.entry(key).or_default().push(position);
            if feature.blocker_opacity() > 0.12 {
                index
                    .blockers_by_bearing
                    .entry(key)
                    .or_default()
                    .push(position);
            }
            index
                .by_pressure_bearing
                .entry((feature.pressure_hpa(), key))
                .or_default()
                .push(position);
        }
        for values in index.by_bearing.values_mut() {
            values.sort_by(|a, b| {
                index.distances[*a]
                    .partial_cmp(&index.distances[*b])
                    .unwrap_or(std::cmp::Ordering::Equal)
            });
        }
        for values in index.blockers_by_bearing.values_mut() {
            values.sort_by(|a, b| {
                index.distances[*a]
                    .partial_cmp(&index.distances[*b])
                    .unwrap_or(std::cmp::Ordering::Equal)
            });
        }
        for values in index.by_pressure_bearing.values_mut() {
            values.sort_by(|a, b| {
                index.distances[*a]
                    .partial_cmp(&index.distances[*b])
                    .unwrap_or(std::cmp::Ordering::Equal)
            });
        }
        index.distances_by_bearing = index
            .by_bearing
            .iter()
            .map(|(key, positions)| {
                (
                    *key,
                    positions.iter().map(|p| index.distances[*p]).collect(),
                )
            })
            .collect();
        index.blocker_distances_by_bearing = index
            .blockers_by_bearing
            .iter()
            .map(|(key, positions)| {
                (
                    *key,
                    positions.iter().map(|p| index.distances[*p]).collect(),
                )
            })
            .collect();
        index.pressure_distances = index
            .by_pressure_bearing
            .iter()
            .map(|(key, positions)| {
                (
                    *key,
                    positions.iter().map(|p| index.distances[*p]).collect(),
                )
            })
            .collect();
        index
    }

    fn matching_keys(&self, windows: &[(f64, f64)]) -> Vec<i64> {
        let mut keys = std::collections::BTreeSet::new();
        let half_bin = BEARING_BIN_DEG * 0.5;
        for (bearing, tolerance) in windows {
            let base_key = bearing_key(*bearing, self.bin_count, BEARING_BIN_DEG);
            let radius = (((tolerance + half_bin) / BEARING_BIN_DEG).ceil() as i64) + 1;
            for offset in -radius..=radius {
                let key = (base_key + offset).rem_euclid(self.bin_count);
                let center = (key as f64 + 0.5) * BEARING_BIN_DEG;
                if angular_difference_deg(center, *bearing) <= tolerance + half_bin {
                    keys.insert(key);
                }
            }
        }
        keys.into_iter().collect()
    }

    /// `FeatureSpatialIndex.query` in Python result order (key ascending,
    /// distance ascending within a key).
    fn query(
        &self,
        windows: &[(f64, f64)],
        min_distance_km: f64,
        max_distance_km: f64,
        blockers_only: bool,
    ) -> Vec<usize> {
        let source = if blockers_only {
            &self.blockers_by_bearing
        } else {
            &self.by_bearing
        };
        let distances = if blockers_only {
            &self.blocker_distances_by_bearing
        } else {
            &self.distances_by_bearing
        };
        let mut result = Vec::new();
        for key in self.matching_keys(windows) {
            let positions = match source.get(&key) {
                Some(values) if !values.is_empty() => values,
                _ => continue,
            };
            let key_distances = match distances.get(&key) {
                Some(values) => values,
                None => continue,
            };
            let start = lower_bound(key_distances, min_distance_km);
            let end = upper_bound(key_distances, max_distance_km);
            if start < end {
                result.extend_from_slice(&positions[start..end]);
            }
        }
        result
    }

    /// `FeatureSpatialIndex.query_pressure` in Python result order. The
    /// candidate path does not consume pressure bins yet, but the query family
    /// port keeps them for the boundary diagnostics that follow.
    #[allow(dead_code)]
    fn query_pressure(
        &self,
        windows: &[(f64, f64)],
        pressure_hpa: i64,
        min_distance_km: f64,
        max_distance_km: f64,
    ) -> Vec<usize> {
        let mut result = Vec::new();
        for bearing_key in self.matching_keys(windows) {
            let key = (pressure_hpa, bearing_key);
            let positions = match self.by_pressure_bearing.get(&key) {
                Some(values) if !values.is_empty() => values,
                _ => continue,
            };
            let key_distances = match self.pressure_distances.get(&key) {
                Some(values) => values,
                None => continue,
            };
            let start = lower_bound(key_distances, min_distance_km);
            let end = upper_bound(key_distances, max_distance_km);
            if start < end {
                result.extend_from_slice(&positions[start..end]);
            }
        }
        result
    }
}

fn lower_bound(sorted: &[f64], value: f64) -> usize {
    sorted.partition_point(|item| *item < value)
}

fn upper_bound(sorted: &[f64], value: f64) -> usize {
    sorted.partition_point(|item| *item <= value)
}

// ---------------------------------------------------------------------------
// Cloud columns (build_cloud_columns).
// ---------------------------------------------------------------------------

#[derive(Serialize, Clone)]
pub struct CloudColumnOutput {
    key: [f64; 4],
    lat: f64,
    lon: f64,
    distance_km: f64,
    bearing_deg: f64,
    high_presence: f64,
    mid_presence: f64,
    low_presence: f64,
    deep_cloud_risk: f64,
    thickness_risk: f64,
    cloud_type: String,
    burnability_score: f64,
    cloud_base_m: f64,
    cloud_top_m: f64,
    liquid_presence: f64,
    ice_presence: f64,
    explicit_cloud_evidence: f64,
    horizontal_cloud_probability: Option<f64>,
    footprint_azimuth_width_deg: Option<f64>,
    footprint_near_distance_km: Option<f64>,
    footprint_far_distance_km: Option<f64>,
    /// (pressure_hpa, feature position) in the request feature list.
    layers: Vec<(i64, usize)>,
}

#[derive(Clone, Copy, PartialEq, Eq, Hash)]
struct ColumnKey([u64; 4]);

fn column_key(feature: &Feature<'_>) -> ColumnKey {
    // Exact (source) fields skip rounding; otherwise Python `round(x, n)`.
    let values: [f64; 4] = if let (Some(lat), Some(lon), Some(distance), Some(bearing)) = (
        feature.source_lat(),
        feature.source_lon(),
        feature.source_distance_km(),
        feature.source_bearing_deg(),
    ) {
        [lat, lon, distance, bearing]
    } else {
        [
            python_round(feature.lat(), 4),
            python_round(feature.lon(), 4),
            python_round(feature.distance_km(), 1),
            python_round(feature.bearing_deg(), 1),
        ]
    };
    // f64 bit-equality keys: -0.0 and 0.0 must group together like Python dict keys.
    let normalized: [f64; 4] = values.map(|value| if value == 0.0 { 0.0 } else { value });
    ColumnKey(normalized.map(|value| value.to_bits()))
}

#[derive(Default)]
struct ColumnLayers {
    /// Pressure insertion order preserved (Python dict semantics).
    order: Vec<i64>,
    positions: HashMap<i64, usize>,
}

impl ColumnLayers {
    fn insert(&mut self, pressure: i64, position: usize) {
        if self.positions.insert(pressure, position).is_none() {
            self.order.push(pressure);
        }
    }
    fn get(&self, pressure: i64) -> Option<usize> {
        self.positions.get(&pressure).copied()
    }
    fn iter_positions(&self) -> impl Iterator<Item = usize> + '_ {
        self.order.iter().map(|pressure| self.positions[pressure])
    }
}

#[derive(Clone, Copy)]
struct ColumnScores {
    presence: f64,
    explicit: f64,
    liquid: f64,
    ice: f64,
    horizontal: f64,
    has_horizontal: bool,
}

fn build_cloud_columns(
    features: &[Feature<'_>],
    index: &SpatialIndex,
    max_distance_km: f64,
) -> Vec<(ColumnKey, CloudColumnOutput)> {
    // Group by column key in index query order (scoring.py:1421-1468).
    let query = index.query(&[(0.0, 180.0)], 0.0, max_distance_km, false);
    let mut grouped_order: Vec<ColumnKey> = Vec::new();
    let mut grouped: HashMap<ColumnKey, ColumnLayers> = HashMap::new();
    let mut scores: HashMap<usize, ColumnScores> = HashMap::new();
    for position in query {
        let feature = &features[position];
        if feature.distance_km() > max_distance_km {
            continue;
        }
        let key = column_key(feature);
        let layers = match grouped.get_mut(&key) {
            Some(layers) => layers,
            None => {
                grouped_order.push(key);
                grouped.entry(key).or_default()
            }
        };
        let horizontal = feature.horizontal_cloud_probability();
        let conditional = feature.cloud_probability_given_cloud();
        let column_scores = match horizontal {
            Some(horizontal) => {
                let horizontal = clamp(horizontal);
                ColumnScores {
                    // Layer classification uses the conditional vertical
                    // profile; horizontal occupancy stays separate.
                    presence: clamp(conditional.unwrap_or(horizontal)),
                    explicit: horizontal,
                    liquid: feature.liquid_presence(),
                    ice: feature.ice_presence(),
                    horizontal,
                    has_horizontal: true,
                }
            }
            None => ColumnScores {
                presence: feature.cloud_presence(),
                explicit: feature.explicit_presence(),
                liquid: feature.liquid_presence(),
                ice: feature.ice_presence(),
                horizontal: 0.0,
                has_horizontal: false,
            },
        };
        scores.insert(position, column_scores);
        layers.insert(feature.pressure_hpa(), position);
    }

    let mut columns = Vec::new();
    for key in grouped_order {
        let layers = &grouped[&key];
        // reference = min by distance, first minimum in layer insertion order.
        let mut reference = None;
        for position in layers.iter_positions() {
            let distance = features[position].distance_km();
            match reference {
                Some((_, best)) if distance >= best => {}
                _ => reference = Some((position, distance)),
            }
        }
        let reference = reference.expect("column groups are never empty");
        let feature_at = |position: usize| &features[position];
        let score_of = |position: usize| scores[&position];

        let mut high_presence = 0.0f64;
        for level in [200i64, 250, 300, 400] {
            if let Some(position) = layers.get(level) {
                high_presence = high_presence.max(score_of(position).presence);
            }
        }
        let mut mid_presence = 0.0f64;
        for level in [500i64, 600, 700] {
            if let Some(position) = layers.get(level) {
                mid_presence = mid_presence.max(score_of(position).presence);
            }
        }
        let mut low_presence = 0.0f64;
        for level in [750i64, 800, 850, 925] {
            if let Some(position) = layers.get(level) {
                low_presence = low_presence.max(score_of(position).presence);
            }
        }
        let mut horizontal_cloud_probability = 0.0f64;
        let mut any_horizontal = false;
        for position in layers.iter_positions() {
            let score = score_of(position);
            if score.has_horizontal {
                any_horizontal = true;
                horizontal_cloud_probability = horizontal_cloud_probability.max(score.horizontal);
            }
        }
        let presence_of = |position: usize| score_of(position).presence;
        let mut cloudy_layers: Vec<usize> = layers
            .iter_positions()
            .filter(|position| presence_of(*position) >= 0.45)
            .collect();
        if cloudy_layers.is_empty() && horizontal_cloud_probability >= 0.45 {
            cloudy_layers = layers
                .iter_positions()
                .filter(|position| presence_of(*position) > 0.0)
                .collect();
        }
        let (base_m, top_m) = if cloudy_layers.is_empty() {
            (0.0, 0.0)
        } else {
            estimated_column_cloud_bounds(features, layers, &cloudy_layers, presence_of)
        };
        let depth_m = (top_m - base_m).max(0.0);
        let mut liquid_presence = 0.0f64;
        let mut ice_presence = 0.0f64;
        let mut explicit_cloud_evidence = 0.0f64;
        for position in layers.iter_positions() {
            let score = score_of(position);
            liquid_presence = liquid_presence.max(score.liquid);
            ice_presence = ice_presence.max(score.ice);
            explicit_cloud_evidence =
                explicit_cloud_evidence.max(score.explicit.max(score.horizontal));
        }
        let cloudy_count = cloudy_layers.len() as f64;
        let deep_cloud_risk = clamp(
            0.45 * high_presence.min(mid_presence)
                + 0.35 * mid_presence.min(low_presence)
                + 0.20 * clamp((cloudy_count - 2.0) / 4.0),
        );
        let thickness_risk = clamp((depth_m - 2800.0) / 5200.0);
        let cloud_type = classify_cloud_type(
            features,
            layers,
            high_presence,
            mid_presence,
            low_presence,
            deep_cloud_risk,
            thickness_risk,
            liquid_presence,
            ice_presence,
            &scores,
            horizontal_cloud_probability,
        );
        let burnability = cloud_type_burnability_score(
            &cloud_type,
            high_presence,
            mid_presence,
            low_presence,
            deep_cloud_risk,
            thickness_risk,
        );
        let reference_feature = feature_at(reference.0);
        columns.push((
            key,
            CloudColumnOutput {
                key: bits_to_f64(key),
                lat: reference_feature.lat(),
                lon: reference_feature.lon(),
                distance_km: reference_feature.distance_km(),
                bearing_deg: reference_feature.bearing_deg(),
                high_presence,
                mid_presence,
                low_presence,
                deep_cloud_risk,
                thickness_risk,
                cloud_type,
                burnability_score: burnability,
                cloud_base_m: base_m,
                cloud_top_m: top_m,
                liquid_presence,
                ice_presence,
                explicit_cloud_evidence,
                horizontal_cloud_probability: if any_horizontal {
                    Some(horizontal_cloud_probability)
                } else {
                    None
                },
                footprint_azimuth_width_deg: reference_feature.footprint_azimuth_width_deg(),
                footprint_near_distance_km: reference_feature.footprint_near_distance_km(),
                footprint_far_distance_km: reference_feature.footprint_far_distance_km(),
                layers: layers
                    .order
                    .iter()
                    .map(|pressure| (*pressure, layers.get(*pressure).unwrap()))
                    .collect(),
            },
        ));
    }
    columns
}

fn bits_to_f64(key: ColumnKey) -> [f64; 4] {
    key.0.map(|bits| f64::from_bits(bits))
}

/// `estimated_column_cloud_bounds` (scoring.py:1251-1290).
fn estimated_column_cloud_bounds(
    features: &[Feature<'_>],
    layers: &ColumnLayers,
    cloudy_layers: &[usize],
    presence_of: impl Fn(usize) -> f64 + Copy,
) -> (f64, f64) {
    let threshold = 0.45;
    // Stable sort by height (ties keep layer insertion order).
    let mut ordered: Vec<usize> = layers.iter_positions().collect();
    ordered.sort_by(|a, b| {
        features[*a]
            .height_m()
            .partial_cmp(&features[*b].height_m())
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let mut cloudy: Vec<usize> = cloudy_layers.to_vec();
    cloudy.sort_by(|a, b| {
        features[*a]
            .height_m()
            .partial_cmp(&features[*b].height_m())
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let lowest = cloudy[0];
    let highest = cloudy[cloudy.len() - 1];
    let mut lower_dry: Vec<usize> = ordered
        .iter()
        .copied()
        .filter(|position| {
            features[*position].height_m() < features[lowest].height_m()
                && presence_of(*position) < threshold
        })
        .collect();
    let base_m = match lower_dry.pop() {
        Some(lower) => {
            interpolate_cloud_threshold_height(features, lower, lowest, threshold, presence_of)
        }
        None => estimated_cloud_base_table(&features[lowest].row),
    };
    let upper_dry: Vec<usize> = ordered
        .iter()
        .copied()
        .filter(|position| {
            features[*position].height_m() > features[highest].height_m()
                && presence_of(*position) < threshold
        })
        .collect();
    let top_m = match upper_dry.first() {
        Some(upper) => {
            interpolate_cloud_threshold_height(features, highest, *upper, threshold, presence_of)
        }
        None => features[highest].height_m(),
    };
    (base_m.max(0.0), base_m.max(top_m))
}

/// `interpolate_cloud_threshold_height` (scoring.py:1292-1304).
fn interpolate_cloud_threshold_height(
    features: &[Feature<'_>],
    first: usize,
    second: usize,
    threshold: f64,
    presence_of: impl Fn(usize) -> f64,
) -> f64 {
    let first_score = presence_of(first);
    let second_score = presence_of(second);
    let score_delta = second_score - first_score;
    if score_delta.abs() < 1e-6 {
        return 0.5 * (features[first].height_m() + features[second].height_m());
    }
    let fraction = clamp((threshold - first_score) / score_delta);
    features[first].height_m()
        + fraction * (features[second].height_m() - features[first].height_m())
}

/// `classify_cloud_type` (scoring.py:1562-1679).
#[allow(clippy::too_many_arguments)]
fn classify_cloud_type(
    features: &[Feature<'_>],
    layers: &ColumnLayers,
    mut high_presence: f64,
    mut mid_presence: f64,
    mut low_presence: f64,
    deep_cloud_risk: f64,
    // The Python reference accepts thickness_risk without using it; keep the
    // parameter so the call sites stay comparable.
    _thickness_risk: f64,
    liquid_presence: f64,
    ice_presence: f64,
    scores: &HashMap<usize, ColumnScores>,
    horizontal_cloud_probability: f64,
) -> String {
    let conditional_support = |levels: &[i64]| -> f64 {
        let mut support = 0.0;
        for level in levels {
            if let Some(position) = layers.get(*level) {
                let probability = scores[&position].presence;
                if probability > 0.0 {
                    let probability = clamp(probability);
                    support = 1.0 - (1.0 - support) * (1.0 - probability);
                }
            }
        }
        clamp(support)
    };
    let conditional_profile = layers
        .iter_positions()
        .any(|position| features[position].cloud_probability_given_cloud().is_some());
    let high_conditional_support = conditional_support(&[200, 250, 300, 400]);
    let mid_conditional_support = conditional_support(&[500, 600, 700]);
    let low_conditional_support = conditional_support(&[750, 800, 850, 925]);
    if conditional_profile {
        high_presence = high_presence.max(high_conditional_support);
        mid_presence = mid_presence.max(mid_conditional_support);
        low_presence = low_presence.max(low_conditional_support);
    }
    let strongest_high_level = [200i64, 250, 300, 400]
        .iter()
        .filter(|level| {
            layers
                .get(**level)
                .map(|position| {
                    scores[&position].presence >= if !conditional_profile { 0.45 } else { 1e-9 }
                })
                .unwrap_or(false)
        })
        .min()
        .copied();
    let strongest_mid = mid_presence.max(0.0);
    // Python min() keeps the first element when comparisons are False, so a
    // leading NaN stays; replicate with a comparison fold, not a sort.
    let lift_values: Vec<f64> = layers
        .iter_positions()
        .filter_map(|position| features[position].omega_pa_s())
        .collect();
    let strongest_lift = match lift_values.len() {
        0 => 0.0,
        _ => {
            let mut current = lift_values[0];
            for value in &lift_values[1..] {
                if *value < current {
                    current = *value;
                }
            }
            current
        }
    };
    let has_explicit_cloud = layers.iter_positions().any(|position| {
        let feature = &features[position];
        feature.horizontal_cloud_probability().is_some()
            || feature.cloud_probability_given_cloud().is_some()
            || feature.cloud_fraction_percent().is_some()
            || feature.cloud_liquid_kg_kg().is_some()
            || feature.cloud_ice_kg_kg().is_some()
    });
    if deep_cloud_risk >= 0.68 && low_presence >= 0.45 {
        return "深厚云盾".to_string();
    }
    if deep_cloud_risk >= 0.58
        && strongest_lift <= -0.25
        && (!has_explicit_cloud || ice_presence >= 0.12)
    {
        return "积雨云云砧".to_string();
    }
    if low_presence >= 0.62 && high_presence < 0.35 && mid_presence < 0.45 {
        return if conditional_profile {
            "低云".to_string()
        } else {
            "层云/层积云".to_string()
        };
    }
    if mid_presence >= 0.58 && high_presence < 0.45 && low_presence < 0.45 {
        return "高积云".to_string();
    }
    if high_presence >= 0.55 && strongest_mid >= 0.45 {
        return "高层云".to_string();
    }
    if high_presence >= 0.50
        && strongest_high_level.is_some()
        && (!has_explicit_cloud || ice_presence >= 0.12f64.max(liquid_presence * 0.65))
    {
        return "卷云".to_string();
    }
    if high_presence >= 0.50 && strongest_high_level.is_some() {
        return "高层云".to_string();
    }
    if mid_presence >= 0.45 {
        return "中云".to_string();
    }
    if low_presence >= 0.45 {
        return "低云".to_string();
    }
    if conditional_profile && horizontal_cloud_probability >= 0.45 {
        // dict-max over insertion order high → middle → low, resolved by the
        // embedded presence and family order tiebreakers.
        let families = [
            ("high", high_conditional_support, high_presence, 0i32),
            ("middle", mid_conditional_support, mid_presence, 1),
            ("low", low_conditional_support, low_presence, 2),
        ];
        let mut dominant = families[0];
        for family in &families[1..] {
            let candidate = (family.1, family.2, family.3);
            let incumbent = (dominant.1, dominant.2, dominant.3);
            if candidate > incumbent {
                dominant = *family;
            }
        }
        if dominant.1 > 0.0 {
            return match dominant.0 {
                "high" => "高层云".to_string(),
                "middle" => "中云".to_string(),
                _ => "低云".to_string(),
            };
        }
    }
    "弱云/晴空".to_string()
}

/// `cloud_type_burnability_score` (scoring.py:1695-1725).
fn cloud_type_burnability_score(
    cloud_type: &str,
    high_presence: f64,
    mid_presence: f64,
    low_presence: f64,
    deep_cloud_risk: f64,
    thickness_risk: f64,
) -> f64 {
    let base = match cloud_type {
        "卷云" => 0.93,
        "高积云" => 0.90,
        "高层云" => 0.80,
        "积雨云云砧" => 0.76,
        "中云" => 0.62,
        "层云/层积云" => 0.34,
        "低云" => 0.22,
        "深厚云盾" => 0.28,
        "弱云/晴空" => 0.08,
        _ => 0.45,
    };
    let sensitivity = match cloud_type {
        "卷云" => 0.70,
        "高积云" => 0.60,
        "高层云" => 0.50,
        "积雨云云砧" => 0.40,
        "中云" => 0.35,
        "层云/层积云" => 0.35,
        "低云" => 0.25,
        "深厚云盾" => 0.35,
        "弱云/晴空" => 0.35,
        _ => 0.35,
    };
    let high_enough = clamp(high_presence.max(0.75 * mid_presence));
    let low_penalty = if cloud_type != "层云/层积云" && cloud_type != "低云" {
        0.28 * low_presence
    } else {
        0.0
    };
    let mut thick_penalty = 0.34 * deep_cloud_risk + 0.18 * thickness_risk;
    if cloud_type == "卷云" || cloud_type == "高积云" {
        thick_penalty *= 0.55;
    }
    if cloud_type == "深厚云盾" {
        thick_penalty *= 1.35;
    }
    let thickness_optical =
        clamp(1.0 - sensitivity * (0.30 * thickness_risk + 0.20 * deep_cloud_risk));
    clamp(base * thickness_optical * (0.55 + 0.45 * high_enough) - low_penalty - thick_penalty)
}

// ---------------------------------------------------------------------------
// Candidate signals (candidate_signals).
// ---------------------------------------------------------------------------

#[derive(Serialize, Clone)]
pub struct CandidateSignalOutput {
    lat: f64,
    lon: f64,
    pressure_hpa: i64,
    distance_km: f64,
    bearing_deg: f64,
    sector_delta_deg: f64,
    apparent_elevation_deg: f64,
    aerosol_transmission_score: f64,
    path_clearance_score: f64,
    illumination_height_m: f64,
    cloud_height_m: f64,
    score: f64,
    rh_percent: f64,
    cloud_base_m: f64,
    cloud_type: String,
    cloud_type_burnability_score: f64,
    deep_cloud_risk: f64,
    aerosol_sensitivity_score: f64,
    aerosol_path_variability_score: f64,
    observer_path_aod550: f64,
    inflow_path_aod550: f64,
    cloud_evidence_score: f64,
    cloud_fraction_percent: Option<f64>,
    cloud_liquid_kg_kg: Option<f64>,
    cloud_ice_kg_kg: Option<f64>,
    atmospheric_clarity_score: f64,
    illumination_score: f64,
    horizon_gap_score: f64,
    overcast_shadow_factor: f64,
    footprint_azimuth_width_deg: Option<f64>,
    footprint_near_distance_km: Option<f64>,
    footprint_far_distance_km: Option<f64>,
    horizontal_cloud_probability: Option<f64>,
}

struct ColumnLookup<'a> {
    map: HashMap<ColumnKey, usize>,
    columns: &'a [(ColumnKey, CloudColumnOutput)],
}

impl ColumnLookup<'_> {
    fn get(&self, feature: &Feature<'_>) -> Option<&CloudColumnOutput> {
        self.map
            .get(&column_key(feature))
            .map(|position| &self.columns[*position].1)
    }
}

/// `path_clearance_weight` (scoring.py:1910-1980) including the sightline and
/// solar-inflow blocking risks.
#[allow(clippy::too_many_arguments)]
fn path_clearance_weight(
    features: &[Feature<'_>],
    index: &SpatialIndex,
    candidate_position: usize,
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
    observer_elevation_m: f64,
) -> f64 {
    let candidate = &features[candidate_position];
    // Target visibility is defined for the illuminated cloud base: the
    // fixed layer-depth table base, never the caller's top-height angle.
    let candidate_apparent_elevation_deg = apparent_cloud_elevation_deg(
        candidate.distance_km(),
        estimated_cloud_base_table(candidate.row),
        observer_elevation_m,
    );
    let candidate_distance = candidate.distance_km();
    let mut penalties: Vec<f64> = Vec::new();
    for position in index.query(
        &[(candidate.bearing_deg(), 12.0)],
        20.0,
        candidate_distance + 220.0,
        true,
    ) {
        if position == candidate_position {
            continue;
        }
        let feature = &features[position];
        if feature.pressure_hpa() <= candidate.pressure_hpa() {
            continue;
        }
        if feature.distance_km() < 20.0 {
            continue;
        }
        if angular_difference_deg(feature.bearing_deg(), candidate.bearing_deg()) > 12.0 {
            continue;
        }
        let opacity = feature.blocker_opacity();
        if opacity <= 0.0 {
            continue;
        }
        let apparent_elevation = feature.apparent_top_deg();
        if feature.distance_km() < candidate_distance {
            if apparent_elevation >= candidate_apparent_elevation_deg - 0.25 {
                let distance_factor =
                    clamp(1.0 - feature.distance_km() / candidate_distance.max(1.0));
                penalties.push(opacity * (0.55 + 0.45 * distance_factor));
            }
        } else if feature.distance_km() <= candidate_distance + 220.0
            && feature.height_m() <= candidate.height_m() + 1200.0
        {
            let distance_factor = clamp(1.0 - (feature.distance_km() - candidate_distance) / 220.0);
            let layer_factor = if feature.pressure_hpa() >= candidate.pressure_hpa() {
                1.0
            } else {
                0.55
            };
            penalties.push(0.65 * opacity * distance_factor * layer_factor);
        }
    }
    let legacy_risk = penalties.iter().copied().fold(0.0f64, f64::max);
    let sightline_risk = observer_sightline_blocking_risk(
        features,
        index,
        candidate_position,
        estimated_cloud_base_table(candidate.row),
        observer_elevation_m,
    );
    let inflow_risk = solar_inflow_blocking_risk(
        features,
        index,
        candidate_position,
        estimated_cloud_base_table(candidate.row),
        solar_elevation_deg,
        solar_azimuth_deg,
        observer_elevation_m,
    );
    clamp(1.0 - legacy_risk.max(sightline_risk).max(inflow_risk))
}

/// `observer_sightline_blocking_risk` (scoring.py:1747-1786).
fn observer_sightline_blocking_risk(
    features: &[Feature<'_>],
    index: &SpatialIndex,
    target_position: usize,
    target_height_m: f64,
    observer_elevation_m: f64,
) -> f64 {
    let target = &features[target_position];
    let mut penalties: Vec<f64> = Vec::new();
    for position in index.query(
        &[(target.bearing_deg(), 14.0)],
        15.0,
        (target.distance_km() - 5.0).max(15.0),
        true,
    ) {
        if position == target_position {
            continue;
        }
        let feature = &features[position];
        if feature.distance_km() >= target.distance_km() - 5.0 {
            continue;
        }
        if feature.distance_km() < 15.0 {
            continue;
        }
        if angular_difference_deg(feature.bearing_deg(), target.bearing_deg()) > 14.0 {
            continue;
        }
        let opacity = feature.lightpath_opacity();
        if opacity <= 0.0 {
            continue;
        }
        let ray_height = sightline_height_at_distance_m(
            target.distance_km(),
            target_height_m,
            feature.distance_km(),
            observer_elevation_m,
        );
        let cloud_base = feature.cloud_base_m();
        let intersection =
            vertical_ray_intersection_score(ray_height, cloud_base, feature.height_m(), 650.0);
        if intersection <= 0.0 {
            continue;
        }
        let distance_factor = clamp(1.0 - feature.distance_km() / target.distance_km().max(1.0));
        penalties.push(opacity * intersection * (0.55 + 0.45 * distance_factor));
    }
    penalties.iter().copied().fold(0.0f64, f64::max)
}

/// `solar_inflow_blocking_risk` (scoring.py:1789-1838).
fn solar_inflow_blocking_risk(
    features: &[Feature<'_>],
    index: &SpatialIndex,
    target_position: usize,
    target_height_m: f64,
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
    // Accepted for signature parity with the Python reference; the inflow
    // loop never reads it there either.
    _observer_elevation_m: f64,
) -> f64 {
    let target = &features[target_position];
    let mut penalties: Vec<f64> = Vec::new();
    for position in index.query(
        &[(target.bearing_deg(), 16.0), (solar_azimuth_deg, 18.0)],
        target.distance_km() + 8.0,
        MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM,
        true,
    ) {
        if position == target_position {
            continue;
        }
        let feature = &features[position];
        if feature.distance_km() <= target.distance_km() + 8.0 {
            continue;
        }
        if feature.distance_km() > MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM {
            continue;
        }
        let gap_km = feature.distance_km() - target.distance_km();
        let bearing_tolerance = if gap_km <= 360.0 { 16.0 } else { 8.0 };
        let solar_tolerance = if gap_km <= 360.0 { 18.0 } else { 10.0 };
        if angular_difference_deg(feature.bearing_deg(), target.bearing_deg()) > bearing_tolerance
            && angular_difference_deg(feature.bearing_deg(), solar_azimuth_deg) > solar_tolerance
        {
            continue;
        }
        let opacity = feature.lightpath_opacity();
        if opacity <= 0.0 {
            continue;
        }
        let ray_height = solar_ray_height_at_distance_m(
            target.distance_km(),
            target_height_m,
            feature.distance_km(),
            solar_elevation_deg,
        );
        let cloud_base = feature.cloud_base_m();
        let intersection =
            vertical_ray_intersection_score(ray_height, cloud_base, feature.height_m(), 850.0);
        if intersection <= 0.0 {
            continue;
        }
        let distance_factor =
            clamp(1.0 - (gap_km - 20.0) / (MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM - 20.0));
        let raw_alignment = 1.0
            - (angular_difference_deg(feature.bearing_deg(), target.bearing_deg())
                / bearing_tolerance.max(1.0))
            .max(
                angular_difference_deg(feature.bearing_deg(), solar_azimuth_deg)
                    / solar_tolerance.max(1.0),
            );
        let far_factor = if gap_km <= 360.0 { 1.0 } else { 0.72 };
        penalties.push(
            opacity
                * intersection
                * clamp(raw_alignment)
                * (0.45 + 0.55 * distance_factor)
                * far_factor,
        );
    }
    penalties.iter().copied().fold(0.0f64, f64::max)
}

/// `path_aod_components` (scoring.py:949-987) with `binned_path_aod`.
fn path_aod_components(
    aerosol: &AerosolInput,
    target_lat: f64,
    target_lon: f64,
    target_distance_km: f64,
    target_bearing_deg: f64,
    solar_azimuth_deg: f64,
) -> (f64, f64, f64) {
    if aerosol.path_samples.is_empty()
        || aerosol.observer_lat.is_none()
        || aerosol.observer_lon.is_none()
    {
        return (aerosol.aod550, aerosol.aod550, 0.0);
    }
    let observer_width_km = (0.18 * target_distance_km).clamp(70.0, 160.0);
    let mut observer_rows: Vec<(f64, f64)> = Vec::new();
    for sample in &aerosol.path_samples {
        let delta_rad = angular_difference_deg(sample.bearing_deg, target_bearing_deg).to_radians();
        let along_km = sample.distance_km * delta_rad.cos();
        let cross_km = (sample.distance_km * delta_rad.sin()).abs();
        if (-30.0..=(target_distance_km + 50.0)).contains(&along_km)
            && cross_km <= observer_width_km
        {
            observer_rows.push((along_km.max(0.0), sample.aod550));
        }
    }
    let max_inflow_km = (MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM - target_distance_km).max(200.0);
    let mut inflow_rows: Vec<(f64, f64)> = Vec::new();
    for sample in &aerosol.path_samples {
        let distance_km = haversine_km(target_lat, target_lon, sample.lat, sample.lon);
        if distance_km > max_inflow_km + 100.0 {
            continue;
        }
        let bearing_deg = if distance_km > 1.0 {
            initial_bearing_deg(target_lat, target_lon, sample.lat, sample.lon)
        } else {
            solar_azimuth_deg
        };
        let delta_rad = angular_difference_deg(bearing_deg, solar_azimuth_deg).to_radians();
        let along_km = distance_km * delta_rad.cos();
        let cross_km = (distance_km * delta_rad.sin()).abs();
        if (-30.0..=max_inflow_km).contains(&along_km) && cross_km <= 140.0 {
            inflow_rows.push((along_km.max(0.0), sample.aod550));
        }
    }
    let (observer_aod, observer_variability) = binned_path_aod(&observer_rows, aerosol.aod550);
    let (inflow_aod, inflow_variability) = binned_path_aod(&inflow_rows, aerosol.aod550);
    let variability = clamp(observer_variability.max(inflow_variability) / 0.45);
    (observer_aod, inflow_aod, variability)
}

fn binned_path_aod(rows: &[(f64, f64)], fallback_aod550: f64) -> (f64, f64) {
    if rows.is_empty() {
        return (fallback_aod550, 0.0);
    }
    let mut bins: std::collections::BTreeMap<i64, Vec<f64>> = std::collections::BTreeMap::new();
    for (distance_km, aod550) in rows {
        bins.entry((*distance_km / 120.0).floor() as i64)
            .or_default()
            .push(*aod550);
    }
    let bin_means: Vec<f64> = bins
        .values()
        .map(|values| statistics_mean(values))
        .collect();
    let minimum = bin_means.iter().copied().fold(f64::INFINITY, f64::min);
    let maximum = bin_means.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    (statistics_mean(&bin_means), maximum - minimum)
}

#[derive(Clone, Copy, Debug)]
struct HorizonIlluminationDiscrimination {
    horizon_gap_score: f64,
    overcast_shadow_factor: f64,
}

/// Blocking optical depth below (or intersecting) the incoming solar ray in a
/// single far sunward column. Elevated clouds well above the ray do not block
/// illumination from below.
fn column_sub_ray_blocking_depth(
    features: &[Feature<'_>],
    column: &CloudColumnOutput,
    ray_height_m: f64,
) -> f64 {
    if ray_height_m <= 0.0 {
        return 1.0;
    }
    let mut max_block = 0.0f64;
    let mut tau_sum = 0.0f64;
    for (_, position) in &column.layers {
        let feature = &features[*position];
        let opacity = feature
            .lightpath_opacity()
            .max(lightpath_blocker_opacity_standalone(feature));
        if opacity <= 0.0 {
            continue;
        }
        let base_m = estimated_cloud_base_table(feature.row);
        let top_m = feature.height_m().max(base_m);
        if base_m > ray_height_m + 850.0 {
            continue;
        }
        let intersection = vertical_ray_intersection_score(ray_height_m, base_m, top_m, 850.0);
        let is_overcast_layer = feature.pressure_hpa() >= 500 || column.deep_cloud_risk >= 0.45;
        let sub_ray = if top_m <= ray_height_m && is_overcast_layer && ray_height_m <= 12_000.0 {
            let vertical_gap = (ray_height_m - top_m).max(0.0);
            0.45 + 0.55 * clamp(1.0 - vertical_gap / 6500.0)
        } else {
            0.0
        };
        let layer_block = opacity * intersection.max(sub_ray);
        if layer_block > 0.0 {
            max_block = max_block.max(layer_block);
            tau_sum += layer_block;
        }
    }
    let deep_floor =
        if column.deep_cloud_risk > 0.45 && column.cloud_base_m <= ray_height_m + 850.0 {
            0.75 * column.deep_cloud_risk
        } else {
            0.0
        };
    clamp(max_block.max(0.60 * tau_sum).max(deep_floor))
}

/// Discriminate solid overcast blocking the far sunward horizon from a clear
/// far-horizon sunward gap beneath elevated high/mid clouds at negative solar
/// elevation.
fn assess_horizon_illumination_discrimination(
    features: &[Feature<'_>],
    columns: &[(ColumnKey, CloudColumnOutput)],
    candidate_distance_km: f64,
    candidate_cloud_base_m: f64,
    required_height_m: f64,
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
) -> HorizonIlluminationDiscrimination {
    if solar_elevation_deg >= 0.0 {
        return HorizonIlluminationDiscrimination {
            horizon_gap_score: 1.0,
            overcast_shadow_factor: 1.0,
        };
    }
    let mut weight_sum = 0.0f64;
    let mut weighted_block_sum = 0.0f64;
    let mut weighted_overcast_sum = 0.0f64;
    let mut peak_block = 0.0f64;
    for (_, column) in columns {
        if column.distance_km <= candidate_distance_km + 8.0
            || column.distance_km > MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM
        {
            continue;
        }
        let az_delta = angular_difference_deg(column.bearing_deg, solar_azimuth_deg);
        if az_delta > 22.0 {
            continue;
        }
        let align_weight = clamp(1.0 - az_delta / 24.0);
        if align_weight <= 0.0 {
            continue;
        }
        let ray_height_m = solar_ray_height_at_distance_m(
            candidate_distance_km,
            candidate_cloud_base_m,
            column.distance_km,
            solar_elevation_deg,
        );
        let col_block = column_sub_ray_blocking_depth(features, column, ray_height_m);
        weight_sum += align_weight;
        weighted_block_sum += align_weight * col_block;
        weighted_overcast_sum += align_weight * smooth_gate(col_block, 0.20, 0.65);
        peak_block = peak_block.max(col_block * align_weight);
    }
    if weight_sum <= 0.0 {
        return HorizonIlluminationDiscrimination {
            horizon_gap_score: 1.0,
            overcast_shadow_factor: 1.0,
        };
    }
    let weighted_block_mean = clamp(weighted_block_sum / weight_sum);
    let overcast_coverage = clamp(weighted_overcast_sum / weight_sum);
    let horizon_gap_score = clamp(1.0 - (0.65 * weighted_block_mean + 0.35 * peak_block));
    let clear_elevated_gap =
        candidate_cloud_base_m > required_height_m + 400.0 && horizon_gap_score >= 0.72;
    let overcast_shadow_factor = if clear_elevated_gap {
        1.0
    } else {
        let shadow_strength =
            smooth_gate(1.0 - horizon_gap_score, 0.22, 0.75) * (0.45 + 0.55 * overcast_coverage);
        clamp(1.0 - 0.85 * shadow_strength)
    };
    HorizonIlluminationDiscrimination {
        horizon_gap_score,
        overcast_shadow_factor,
    }
}

#[allow(clippy::too_many_arguments)]
fn assess_candidates(
    features: &[Feature<'_>],
    index: &SpatialIndex,
    columns: &[(ColumnKey, CloudColumnOutput)],
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
    aerosol: &AerosolInput,
    observer_elevation_m: f64,
) -> Vec<CandidateSignalOutput> {
    let lookup = ColumnLookup {
        map: columns
            .iter()
            .enumerate()
            .map(|(position, (key, _))| (*key, position))
            .collect(),
        columns,
    };
    let atmospheric_clarity = atmospheric_clarity_score(aerosol.aod550);
    let column_for = |feature: &Feature<'_>| lookup.get(feature);
    let candidate_cloud_base = |feature: &Feature<'_>| -> f64 {
        match column_for(feature) {
            Some(column) if column.cloud_base_m > 0.0 => column.cloud_base_m,
            _ => feature.cloud_base_m(),
        }
    };
    let mut signals: Vec<CandidateSignalOutput> = Vec::new();
    struct EligibleCandidate {
        position: usize,
        sector_delta: f64,
        required_height: f64,
        apparent_elevation: f64,
        sight_weight: f64,
        horizon_weight: f64,
        clearance_weight: f64,
        observer_path_aod: f64,
        inflow_path_aod: f64,
        path_variability: f64,
    }
    let mut eligible: Vec<EligibleCandidate> = Vec::new();
    let mut aerosol_inputs: Vec<[f64; 8]> = Vec::new();
    for position in index.query(
        &[(solar_azimuth_deg, 70.0)],
        0.0,
        MAX_CANDIDATE_DISTANCE_KM,
        false,
    ) {
        let feature = &features[position];
        let distance_km = feature.distance_km();
        let is_local_upper =
            distance_km < LOCAL_UPPER_CANDIDATE_MAX_DISTANCE_KM && feature.pressure_hpa() <= 600;
        if distance_km > MAX_CANDIDATE_DISTANCE_KM
            || (distance_km < MIN_CANDIDATE_DISTANCE_KM && !is_local_upper)
        {
            continue;
        }
        let sector_delta = angular_difference_deg(feature.bearing_deg(), solar_azimuth_deg);
        if sector_delta > 70.0 {
            continue;
        }
        let required_height = crate::native::required_illumination_height_m(
            distance_km,
            solar_elevation_deg,
            sector_delta,
        );
        let cloud_base_m = candidate_cloud_base(feature);
        let feasibility = illumination_feasibility(cloud_base_m, required_height);
        if feasibility <= 0.0 {
            continue;
        }
        let apparent_elevation =
            apparent_cloud_elevation_deg(distance_km, cloud_base_m, observer_elevation_m);
        let sight_weight = line_of_sight_weight(apparent_elevation);
        if sight_weight <= 0.0 {
            continue;
        }
        let horizon_weight = horizon_proximity_weight(apparent_elevation);
        let clearance_weight = path_clearance_weight(
            features,
            index,
            position,
            solar_elevation_deg,
            solar_azimuth_deg,
            observer_elevation_m,
        );
        if clearance_weight <= 0.05 {
            continue;
        }
        let (observer_path_aod, inflow_path_aod, path_variability) = path_aod_components(
            aerosol,
            feature.lat(),
            feature.lon(),
            distance_km,
            feature.bearing_deg(),
            solar_azimuth_deg,
        );
        aerosol_inputs.push([
            aerosol.aod550,
            observer_path_aod,
            inflow_path_aod,
            distance_km,
            candidate_cloud_base(feature),
            apparent_elevation,
            solar_elevation_deg,
            observer_elevation_m,
        ]);
        eligible.push(EligibleCandidate {
            position,
            sector_delta,
            required_height,
            apparent_elevation,
            sight_weight,
            horizon_weight,
            clearance_weight,
            observer_path_aod,
            inflow_path_aod,
            path_variability,
        });
    }
    // The aerosol batch is the same implementation the Python path calls via
    // firesky_aerosol_transmission_batch, so its outputs match bit-for-bit.
    for (row_index, values) in aerosol_inputs.iter().enumerate() {
        let samples = aerosol_transmission_samples_impl(
            values[0], values[1], values[2], values[3], values[4], values[5], values[6], values[7],
        );
        let minimum = samples.iter().copied().fold(f64::INFINITY, f64::min);
        let maximum = samples.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        let spread = maximum - minimum;
        let weight = ((samples[0] + samples[1] + samples[2]) / 3.0 - 0.15 * spread).clamp(0.0, 1.0);
        let eligible_row = &eligible[row_index];
        let position = eligible_row.position;
        let sector_delta = eligible_row.sector_delta;
        let required_height = eligible_row.required_height;
        let feature = &features[position];
        let aerosol_weight = weight;
        let aerosol_sensitivity = clamp(spread);
        let clearance_weight = eligible_row.clearance_weight;
        let cloud_base_m = candidate_cloud_base(feature);
        let horizon_disc = assess_horizon_illumination_discrimination(
            features,
            columns,
            feature.distance_km(),
            cloud_base_m,
            required_height,
            solar_elevation_deg,
            solar_azimuth_deg,
        );
        let raw_illumination_score = clamp((cloud_base_m - required_height + 700.0) / 2600.0);
        let illumination_score = if solar_elevation_deg < 0.0 {
            clamp(raw_illumination_score * (0.35 + 0.65 * horizon_disc.overcast_shadow_factor))
        } else {
            raw_illumination_score
        };
        let cloud_evidence_score = feature_cloud_presence_score(feature);
        let explicit_presence = explicit_cloud_presence_opt(feature);
        if let Some(explicit_presence) = explicit_presence {
            if explicit_presence < 0.06 {
                continue;
            }
        }
        if feature.distance_km() < LOCAL_UPPER_CANDIDATE_MAX_DISTANCE_KM
            && cloud_evidence_score < 0.42
        {
            continue;
        }
        let lift_score = match feature.omega_pa_s() {
            Some(omega) => clamp((-omega + 0.05) / 0.5),
            None => 0.5,
        };
        let azimuth_score = clamp(1.0 - sector_delta / 70.0);
        let column = column_for(feature);
        let type_burnability = column.map_or(0.5, |column| column.burnability_score);
        let cloud_type = column.map_or("unknown".to_string(), |column| column.cloud_type.clone());
        let deep_risk = column.map_or(0.0, |column| column.deep_cloud_risk);
        let type_factor = 0.62 + 0.38 * type_burnability;
        let high_suitability = if feature.pressure_hpa() <= 400 {
            high_cloud_suitability_score(feature.rh_percent()).max(cloud_evidence_score)
        } else {
            1.0
        };
        let suitability_factor = 0.72 + 0.28 * high_suitability;
        let horizon_view_factor = 0.65 + 0.35 * eligible_row.horizon_weight;
        let raw_dawn_boost = if solar_elevation_deg < 0.0 {
            (1.0 + solar_elevation_deg.abs() / 15.0).min(1.35)
        } else {
            1.0
        };
        let dawn_boost = if solar_elevation_deg < 0.0 {
            1.0 + (raw_dawn_boost - 1.0) * horizon_disc.overcast_shadow_factor
        } else {
            raw_dawn_boost
        };
        let negative_shadow_attenuation = if solar_elevation_deg < 0.0 {
            0.45 + 0.55 * horizon_disc.overcast_shadow_factor
        } else {
            1.0
        };
        let explicit_gate = match explicit_presence {
            None => 1.0,
            Some(explicit) => smooth_gate(explicit, 0.08, 0.35),
        };
        let geometric_candidate_score = (0.50 * cloud_evidence_score
            + 0.18 * azimuth_score
            + 0.12 * eligible_row.horizon_weight
            + 0.05 * distance_weight(feature.distance_km())
            + 0.15 * lift_score)
            * pressure_weight(feature.pressure_hpa())
            * eligible_row.sight_weight
            * horizon_view_factor
            * explicit_gate
            * dawn_boost
            * negative_shadow_attenuation;
        let min_score = if solar_elevation_deg < 0.0 {
            (0.15 + solar_elevation_deg * 0.012).max(0.06)
        } else {
            0.15
        };
        if geometric_candidate_score <= min_score {
            continue;
        }
        let score = clamp(geometric_candidate_score * type_factor * suitability_factor);
        signals.push(CandidateSignalOutput {
            lat: feature.lat(),
            lon: feature.lon(),
            pressure_hpa: feature.pressure_hpa(),
            distance_km: feature.distance_km(),
            bearing_deg: feature.bearing_deg(),
            sector_delta_deg: sector_delta,
            apparent_elevation_deg: eligible_row.apparent_elevation,
            aerosol_transmission_score: aerosol_weight,
            path_clearance_score: clearance_weight,
            illumination_height_m: required_height,
            cloud_height_m: feature.height_m(),
            score,
            rh_percent: feature.rh_percent(),
            cloud_base_m,
            cloud_type,
            cloud_type_burnability_score: type_burnability,
            deep_cloud_risk: deep_risk,
            aerosol_sensitivity_score: aerosol_sensitivity,
            aerosol_path_variability_score: eligible_row.path_variability,
            observer_path_aod550: eligible_row.observer_path_aod,
            inflow_path_aod550: eligible_row.inflow_path_aod,
            cloud_evidence_score,
            cloud_fraction_percent: feature.cloud_fraction_percent(),
            cloud_liquid_kg_kg: feature.cloud_liquid_kg_kg(),
            cloud_ice_kg_kg: feature.cloud_ice_kg_kg(),
            atmospheric_clarity_score: atmospheric_clarity,
            illumination_score,
            horizon_gap_score: horizon_disc.horizon_gap_score,
            overcast_shadow_factor: horizon_disc.overcast_shadow_factor,
            footprint_azimuth_width_deg: feature.footprint_azimuth_width_deg(),
            footprint_near_distance_km: feature.footprint_near_distance_km(),
            footprint_far_distance_km: feature.footprint_far_distance_km(),
            horizontal_cloud_probability: feature.horizontal_cloud_probability(),
        });
    }
    // Python `list.sort(key=score, reverse=True)` is stable: equal scores keep
    // storage (query) order.
    signals.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    signals
}

// ---------------------------------------------------------------------------
// RPC surface.
// ---------------------------------------------------------------------------

#[derive(Serialize)]
struct AssessmentOutput {
    contract: &'static str,
    feature_row_contract: &'static str,
    runtime_python_used: bool,
    /// Echoes the request's detail level ("full" or "model").
    detail: &'static str,
    scan_count: usize,
    /// 18-value ledger rows, one per scan, in quality.rs INPUT_NAMES order,
    /// float32-quantized exactly like national_batch.py's row construction.
    rows: Vec<[f64; 18]>,
    score_ceiling_applied: bool,
    /// The canonical event/instant ledger over those rows
    /// (contract rust-desktop-nowcast-quality-v2).
    quality: serde_json::Value,
    scans: Vec<AssessmentScanOutput>,
}

/// Per-scan payload: the model-relevant fields plus, on the "full" detail
/// level, the diagnostic lists the acceptance differential walks element by
/// element. On "model" the six heavy list fields (columns + five signal
/// lists) are dropped — the model rebuild reads only the top-* fields.
#[derive(Serialize)]
#[serde(untagged)]
enum AssessmentScanOutput {
    Full(AssessmentStepOutput),
    Model(AssessmentModelStepOutput),
}

/// The "model" detail projection of `AssessmentStepOutput`: identical except
/// that `columns`, `candidate_signals`, `local_deck_signals`,
/// `remote_boundary_signals`, `boundary_signals` and `cloud_hole_signals` are
/// omitted (serde skip_serializing_if on the full struct keeps one code path
/// for assembly; the projection is enforced at the wire boundary here).
#[derive(Serialize)]
struct AssessmentModelStepOutput {
    forecast_hour: i64,
    valid_time_utc: String,
    valid_time_local: String,
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
    burn_score: f64,
    intensity_score: f64,
    extent_score: f64,
    vividness_score: f64,
    visibility_score: f64,
    low_cloud_penalty: f64,
    candidate_count: usize,
    burning_cloud_count: i64,
    top_candidate: Option<CandidateSignalOutput>,
    strong_burning_cloud_count: i64,
    top_boundary: Option<BoundarySignalOutput>,
    boundary_score: f64,
    boundary_uncertainty: Option<BoundaryUncertaintyOutput>,
    top_cloud_hole: Option<CloudHoleSignalOutput>,
    cloud_hole_score: f64,
    cross_section: CrossSectionOutput,
    solar_inflow_blockage: Option<SolarInflowBlockageOutput>,
    selected_regime: String,
    scene_scores: Vec<(String, f64)>,
    local_sky_score: f64,
    remote_sky_score: f64,
    convective_risk: f64,
    color_diagnosis: serde_json::Value,
    reasons: Vec<String>,
    map_cells: Vec<MapCloudCellOutput>,
    geometric_cloud_coverage: f64,
    structure_gate_score: f64,
    satellite_retrieval_material_evidence: Option<f64>,
    selected_observer_path_aod550: Option<f64>,
    selected_inflow_path_aod550: Option<f64>,
    selected_aerosol_path_variability_score: f64,
    forward_view_cloud_field_amount: f64,
    forward_burnable_cloud_field_amount: f64,
    observer_local_cloud_field_amount: f64,
    observation_burnable: bool,
    observation_score_ceiling: Option<f64>,
    surface_weather_penalty: SurfaceWeatherPenaltyOutput,
    horizon_gap_score: f64,
    overcast_shadow_factor: f64,
}

/// Project a full step onto the "model" wire shape by moving its fields
/// (no clones of the heavy lists — they are simply dropped).
impl From<AssessmentStepOutput> for AssessmentModelStepOutput {
    fn from(step: AssessmentStepOutput) -> Self {
        AssessmentModelStepOutput {
            forecast_hour: step.forecast_hour,
            valid_time_utc: step.valid_time_utc,
            valid_time_local: step.valid_time_local,
            solar_elevation_deg: step.solar_elevation_deg,
            solar_azimuth_deg: step.solar_azimuth_deg,
            burn_score: step.burn_score,
            intensity_score: step.intensity_score,
            extent_score: step.extent_score,
            vividness_score: step.vividness_score,
            visibility_score: step.visibility_score,
            low_cloud_penalty: step.low_cloud_penalty,
            candidate_count: step.candidate_count,
            burning_cloud_count: step.burning_cloud_count,
            top_candidate: step.top_candidate,
            strong_burning_cloud_count: step.strong_burning_cloud_count,
            top_boundary: step.top_boundary,
            boundary_score: step.boundary_score,
            boundary_uncertainty: step.boundary_uncertainty,
            top_cloud_hole: step.top_cloud_hole,
            cloud_hole_score: step.cloud_hole_score,
            cross_section: step.cross_section,
            solar_inflow_blockage: step.solar_inflow_blockage,
            selected_regime: step.selected_regime,
            scene_scores: step.scene_scores,
            local_sky_score: step.local_sky_score,
            remote_sky_score: step.remote_sky_score,
            convective_risk: step.convective_risk,
            color_diagnosis: step.color_diagnosis,
            reasons: step.reasons,
            map_cells: step.map_cells,
            geometric_cloud_coverage: step.geometric_cloud_coverage,
            structure_gate_score: step.structure_gate_score,
            satellite_retrieval_material_evidence: step.satellite_retrieval_material_evidence,
            selected_observer_path_aod550: step.selected_observer_path_aod550,
            selected_inflow_path_aod550: step.selected_inflow_path_aod550,
            selected_aerosol_path_variability_score: step.selected_aerosol_path_variability_score,
            forward_view_cloud_field_amount: step.forward_view_cloud_field_amount,
            forward_burnable_cloud_field_amount: step.forward_burnable_cloud_field_amount,
            observer_local_cloud_field_amount: step.observer_local_cloud_field_amount,
            observation_burnable: step.observation_burnable,
            observation_score_ceiling: step.observation_score_ceiling,
            surface_weather_penalty: step.surface_weather_penalty,
            horizon_gap_score: step.horizon_gap_score,
            overcast_shadow_factor: step.overcast_shadow_factor,
        }
    }
}

pub(crate) fn compute(request: serde_json::Value) -> Result<serde_json::Value, String> {
    let request = serde_json::from_value(request).map_err(|error| error.to_string())?;
    serde_json::to_value(build_result(request)?).map_err(|error| error.to_string())
}

pub fn execute(params: Params) -> Result<serde_json::Value, String> {
    let (request, identity) =
        request_file::read::<AssessmentRequest>(&params.request_path, MAX_REQUEST_BYTES)?;
    let result = build_result(request)?;
    let bytes = serde_json::to_vec(&result).map_err(|error| error.to_string())?;
    if bytes.len() > MAX_RESULT_BYTES {
        return Err("nowcast assessment output exceeds 128 MiB".into());
    }
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&params.output_path)
        .map_err(|error| error.to_string())?;
    if let Err(error) = output.write_all(&bytes).and_then(|_| output.sync_all()) {
        drop(output);
        let _ = fs::remove_file(&params.output_path);
        return Err(error.to_string());
    }
    Ok(json!({
        "contract": ASSESSMENT_FILE_CONTRACT,
        "runtime_python_used": false,
        "output_path": params.output_path,
        "bytes": bytes.len(),
        "sha256": Sha256::digest(&bytes).iter().map(|byte| format!("{byte:02x}")).collect::<String>(),
        "request_sha256": identity.sha256,
    }))
}

fn build_result(request: AssessmentRequest) -> Result<AssessmentOutput, String> {
    if request.scans.is_empty() || request.scans.len() > MAX_SCANS {
        return Err("nowcast assessment requires 1..16 scans".into());
    }
    let full_detail = wants_full_detail(&request.detail)?;
    let detail_label: &'static str = if full_detail { "full" } else { "model" };
    let zone: chrono_tz::Tz = request
        .timezone_name
        .parse()
        .map_err(|_| "timezone_name is invalid".to_string())?;
    let event_time_utc = match &request.event_time_utc {
        Some(text) => Some(
            chrono::DateTime::parse_from_rfc3339(text)
                .map_err(|_| "event_time_utc is invalid".to_string())?
                .with_timezone(&chrono::Utc),
        ),
        None => None,
    };

    let mut steps: Vec<AssessmentStepOutput> = Vec::with_capacity(request.scans.len());
    let mut offsets: Vec<f64> = Vec::with_capacity(request.scans.len());
    let mut valid_mask: Vec<bool> = Vec::with_capacity(request.scans.len());
    let mut scan_material: Vec<Option<f64>> = Vec::with_capacity(request.scans.len());
    let mut scan_block_risk: Vec<f64> = Vec::with_capacity(request.scans.len());
    let mut scan_load_overrides: Vec<(Option<f64>, Option<f64>, Option<f64>)> =
        Vec::with_capacity(request.scans.len());
    let mut first_valid_local: Option<chrono::DateTime<chrono::FixedOffset>> = None;
    for scan in &request.scans {
        if scan.features.len() > MAX_FEATURES {
            return Err("nowcast assessment feature count exceeds the request bound".into());
        }
        let valid_time_utc = chrono::DateTime::parse_from_rfc3339(&scan.valid_time_utc)
            .map_err(|_| "scan valid_time_utc is invalid".to_string())?;
        let valid_time_local = valid_time_utc.with_timezone(&zone);
        if first_valid_local.is_none() {
            first_valid_local = Some(valid_time_local.fixed_offset());
        }
        let feature_count = scan.features.len();
        let mut rows_v2: Vec<[f64; FEATURE_ROW_V2_FIELD_COUNT]> = Vec::with_capacity(feature_count);
        for (row_index, row) in scan.features.iter().enumerate() {
            if row.len() != FEATURE_ROW_V2_FIELD_COUNT {
                return Err(format!(
                    "feature row {row_index} must have {FEATURE_ROW_V2_FIELD_COUNT} columns"
                ));
            }
            let mut packed = [f64::NAN; FEATURE_ROW_V2_FIELD_COUNT];
            for (cell, value) in row.iter().enumerate() {
                packed[cell] = value.unwrap_or(f64::NAN);
            }
            rows_v2.push(packed);
        }
        let metrics: Vec<[f64; METRIC_COUNT]> = rows_v2
            .iter()
            .map(|row| {
                v2_feature_metrics_row(row, request.max_distance_km, request.observer_elevation_m)
            })
            .collect();
        let features: Vec<Feature<'_>> = rows_v2
            .iter()
            .zip(metrics.iter())
            .map(|(row, metric)| Feature {
                row,
                metrics: metric,
            })
            .collect();
        let index = SpatialIndex::build(&features);
        let columns = build_cloud_columns(&features, &index, request.max_distance_km);
        let signals = assess_candidates(
            &features,
            &index,
            &columns,
            scan.solar_elevation_deg,
            scan.solar_azimuth_deg,
            &request.aerosol,
            request.observer_elevation_m,
        );
        let top = signals.first().cloned();
        let local_deck_signals = local_deck_edge_signals(
            &features,
            &columns,
            scan.solar_elevation_deg,
            scan.solar_azimuth_deg,
            &request.aerosol,
            request.observer_elevation_m,
        );
        let remote_boundary_signals = boundary_lightpath_signals(
            &features,
            &index,
            &columns,
            scan.solar_elevation_deg,
            scan.solar_azimuth_deg,
            &request.aerosol,
            request.observer_elevation_m,
        );
        let mut boundary_signals = local_deck_signals.clone();
        boundary_signals.extend(remote_boundary_signals.iter().cloned());
        boundary_signals.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        let cloud_hole_signals = cloud_hole_lightpath_signals(
            &columns,
            scan.solar_elevation_deg,
            scan.solar_azimuth_deg,
        );
        let solar_inflow_blockage = diagnose_solar_inflow_blockage(
            &features,
            &index,
            top.as_ref(),
            scan.solar_elevation_deg,
            scan.solar_azimuth_deg,
        );
        let low_penalty = low_cloud_penalty(&features, &index, scan.solar_azimuth_deg);
        let weather = surface_weather_penalty(&features, &index);
        let cross_section = diagnose_cross_section(
            &features,
            &columns,
            scan.solar_elevation_deg,
            scan.solar_azimuth_deg,
            request.observer_elevation_m,
        );
        let step = assemble_step(
            &features,
            &columns,
            &signals,
            &local_deck_signals,
            &remote_boundary_signals,
            &boundary_signals,
            &cloud_hole_signals,
            &cross_section,
            solar_inflow_blockage.as_ref(),
            low_penalty,
            &weather,
            scan,
            request.observer_elevation_m,
            request.include_map_cells,
            request.map_radius_km,
            request.aerosol.aod550,
            &request.aerosol.quality,
            &crate::astronomy::local_iso(valid_time_utc.with_timezone(&chrono::Utc), zone)?,
            full_detail,
        );
        let step = match &scan.radar {
            Some(radar) => apply_observation_constraints(step, radar),
            None => step,
        };
        offsets.push(
            (valid_time_local.fixed_offset() - first_valid_local.unwrap().fixed_offset())
                .num_seconds() as f64
                / 60.0,
        );
        let valid = match (&event_time_utc, request.core_window_minutes) {
            (Some(event_time), Some(window)) => {
                let event_local = event_time.with_timezone(&zone);
                (valid_time_local.fixed_offset() - event_local.fixed_offset())
                    .num_seconds()
                    .abs() as f64
                    <= window * 60.0
            }
            _ => true,
        };
        valid_mask.push(valid);
        scan_material.push(scan.satellite_material_evidence);
        scan_block_risk.push(scan.solar_incoming_block_risk);
        scan_load_overrides.push((
            scan.forward_burnable_cloud_load,
            scan.forward_burnable_cloud_material_load,
            scan.forward_burnable_cloud_incoming_aerosol_load,
        ));
        steps.push(step);
    }

    // 18-value ledger rows (native/src/quality.rs INPUT_NAMES order), built
    // with national_batch.py's float32 construction semantics.
    let mut quality_rows: Vec<[f64; 18]> = Vec::with_capacity(steps.len());
    let any_ceiling = steps
        .iter()
        .any(|step| step.observation_score_ceiling.is_some());
    for (index, step) in steps.iter().enumerate() {
        let view = clamp(step.forward_view_cloud_field_amount);
        let burnable = clamp(step.forward_burnable_cloud_field_amount);
        let local_view = clamp(step.observer_local_cloud_field_amount);
        let incoming_block = clamp(scan_block_risk[index]);
        let material_quality = {
            let candidate_quality = step
                .top_candidate
                .as_ref()
                .map(|signal| signal.cloud_type_burnability_score);
            let boundary_quality = step
                .top_boundary
                .as_ref()
                .map(|signal| signal.cloud_type_burnability_score);
            let hole_quality = step
                .top_cloud_hole
                .as_ref()
                .map(|signal| signal.rear_target_burnability_score);
            // Python `or` truthiness: a 0.0 quality falls through to the next.
            let quality = [candidate_quality, boundary_quality, hole_quality]
                .into_iter()
                .flatten()
                .find(|value| *value != 0.0)
                .unwrap_or(0.0);
            clamp(quality)
        };
        let material_quality = match scan_material[index] {
            Some(evidence) => clamp(evidence),
            None => material_quality,
        };
        let (load_override, material_override, incoming_override) = scan_load_overrides[index];
        let derived_material_load = burnable * 2.5 * material_quality;
        let material_load = py_clamp(material_override.unwrap_or(derived_material_load), 0.0, 2.5);
        let burn_load = py_clamp(load_override.unwrap_or(material_load), 0.0, 2.5);
        let incoming_aerosol_load = py_clamp(incoming_override.unwrap_or(material_load), 0.0, 2.5);
        let ceiling = match &steps[index].observation_score_ceiling {
            Some(value) => py_clamp(
                *value * crate::native::empirical::MAX_SCORE,
                0.0,
                crate::native::empirical::MAX_SCORE,
            ),
            None => crate::native::empirical::MAX_SCORE,
        };
        // Python stores every cell through float32 before the ledger reads it.
        let quantize = |value: f64| -> f64 { value as f32 as f64 };
        quality_rows.push([
            quantize(view),
            quantize(burnable),
            quantize(burn_load),
            quantize(material_load),
            quantize(incoming_aerosol_load),
            quantize(incoming_block),
            quantize(step.visibility_score),
            quantize(step.vividness_score),
            quantize(offsets[index]),
            quantize(request.aerosol.aod550),
            quantize(local_view),
            0.0,
            0.0,
            0.0,
            0.0,
            quantize(if steps[index].observation_burnable {
                1.0
            } else {
                0.0
            }),
            quantize(ceiling),
            quantize(if valid_mask[index] { 1.0 } else { 0.0 }),
        ]);
    }

    // NowcastParams is Deserialize-only; reuse its serde contract for the
    // internal call instead of widening quality.rs visibility.
    let quality = crate::quality::execute_nowcast(
        serde_json::from_value(serde_json::json!({
            "rows": quality_rows,
            "apply_duration": request.apply_duration,
            "local_columns": false,
            "score_ceiling": any_ceiling,
            "radius_km": MAX_ANALYSIS_RADIUS_KM,
            "parallel_instants": true,
        }))
        .map_err(|error| error.to_string())?,
    )?;

    Ok(AssessmentOutput {
        contract: ASSESSMENT_CONTRACT,
        feature_row_contract: FEATURE_ROW_CONTRACT,
        runtime_python_used: false,
        detail: detail_label,
        scan_count: steps.len(),
        rows: quality_rows,
        score_ceiling_applied: any_ceiling,
        quality,
        scans: if full_detail {
            steps.into_iter().map(AssessmentScanOutput::Full).collect()
        } else {
            steps
                .into_iter()
                .map(|step| AssessmentScanOutput::Model(step.into()))
                .collect()
        },
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row<const CELLS: usize>(cells: [(usize, f64); CELLS]) -> [f64; FEATURE_ROW_V2_FIELD_COUNT] {
        let mut row = [f64::NAN; FEATURE_ROW_V2_FIELD_COUNT];
        row[0] = 30.0;
        row[1] = 120.0;
        row[2] = 100.0;
        row[3] = 45.0;
        row[4] = 700.0;
        row[5] = 3200.0;
        row[6] = 60.0;
        for (index, value) in cells {
            row[index] = value;
        }
        row
    }

    #[test]
    fn python_round_matches_half_even_on_exact_values() {
        assert_eq!(python_round(35.0625, 3), 35.062);
        // 35.0675 stores just above the midpoint, so Python rounds up.
        assert_eq!(python_round(35.0675, 3), 35.068);
        assert_eq!(python_round(2.5, 0), 2.0);
        assert_eq!(python_round(3.5, 0), 4.0);
        assert_eq!(python_round(120.1551, 4), 120.1551);
        assert_eq!(python_round(30.27409, 4), 30.2741);
        assert_eq!(python_round(-0.5, 0), 0.0);
        // 45.05 stores just below the midpoint, so Python rounds down too.
        assert_eq!(python_round(45.05, 1), 45.0);
    }

    fn batch_request(scans: Vec<ScanInput>) -> AssessmentRequest {
        AssessmentRequest {
            scans,
            timezone_name: "Asia/Shanghai".to_string(),
            observer_elevation_m: 0.0,
            aerosol: AerosolInput {
                aod550: 0.2,
                source: "unit".to_string(),
                quality: "measured".to_string(),
                observer_lat: Some(30.0),
                observer_lon: Some(120.0),
                path_samples: vec![],
            },
            max_distance_km: 1000.0,
            map_radius_km: 1000.0,
            include_map_cells: false,
            event_time_utc: None,
            core_window_minutes: None,
            apply_duration: false,
            detail: None,
        }
    }

    fn empty_scan(features: Vec<Vec<Option<f64>>>) -> ScanInput {
        empty_scan_at(features, "2026-01-01T01:00:00Z")
    }

    fn empty_scan_at(features: Vec<Vec<Option<f64>>>, valid_time_utc: &str) -> ScanInput {
        ScanInput {
            valid_time_utc: valid_time_utc.to_string(),
            forecast_hour: 0,
            features,
            solar_elevation_deg: 20.0,
            solar_azimuth_deg: 150.0,
            radar: None,
            satellite_material_evidence: None,
            solar_incoming_block_risk: 0.0,
            forward_burnable_cloud_load: None,
            forward_burnable_cloud_material_load: None,
            forward_burnable_cloud_incoming_aerosol_load: None,
            observer_lat: None,
        }
    }

    #[test]
    fn empty_feature_set_yields_empty_outputs() {
        let result = build_result(batch_request(vec![empty_scan(vec![])])).unwrap();
        assert_eq!(result.scan_count, 1);
        assert_eq!(result.detail, "full");
        let AssessmentScanOutput::Full(step) = &result.scans[0] else {
            panic!("default detail is full");
        };
        assert!(step.columns.is_empty());
        assert!(step.candidate_signals.is_empty());
        assert_eq!(result.rows.len(), 1);
        assert_eq!(result.rows[0][8], 0.0);
    }

    #[test]
    fn model_detail_drops_heavy_lists_and_rejects_unknown_levels() {
        let mut request = batch_request(vec![empty_scan(vec![])]);
        request.detail = Some("model".to_string());
        let result = build_result(request).unwrap();
        assert_eq!(result.detail, "model");
        let encoded = serde_json::to_vec(&result).unwrap();
        let value: serde_json::Value = serde_json::from_slice(&encoded).unwrap();
        let scan = &value["scans"][0];
        // The six never-consumed-by-the-model list fields must be absent…
        for dropped in [
            "columns",
            "candidate_signals",
            "local_deck_signals",
            "remote_boundary_signals",
            "boundary_signals",
            "cloud_hole_signals",
        ] {
            assert!(
                scan.get(dropped).is_none(),
                "model detail must drop {dropped}"
            );
        }
        // …while every model-consumed field survives.
        for kept in [
            "top_candidate",
            "burn_score",
            "reasons",
            "surface_weather_penalty",
        ] {
            assert!(scan.get(kept).is_some(), "model detail must keep {kept}");
        }
        let mut request = batch_request(vec![empty_scan(vec![])]);
        request.detail = Some("compact".to_string());
        assert!(build_result(request).is_err());
    }

    #[test]
    fn candidate_rows_reject_wrong_width() {
        assert!(build_result(batch_request(vec![empty_scan(vec![vec![Some(0.0); 20]])])).is_err());
        // Three empty scans are a legal batch (1..16).
        let result = build_result(batch_request(vec![
            empty_scan(vec![]),
            empty_scan_at(vec![], "2026-01-01T01:05:00Z"),
            empty_scan_at(vec![], "2026-01-01T01:10:00Z"),
        ]))
        .unwrap();
        assert_eq!(result.scan_count, 3);
        assert_eq!(result.rows.len(), 3);
        // Offsets run 0 / +5 / +10 minutes.
        assert_eq!(result.rows[2][8], 10.0);
        // The valid column defaults to 1.0 without a core window.
        assert_eq!(result.rows[0][17], 1.0);
    }

    #[test]
    fn metrics_run_for_a_basic_row() {
        let row = row([
            (FEATURE_ROW_V2_HORIZONTAL_CLOUD_PROBABILITY, 0.8),
            (FEATURE_ROW_V2_CLOUD_PROBABILITY_GIVEN_CLOUD, 0.6),
        ]);
        let metrics = v2_feature_metrics_row(&row, 1000.0, 0.0);
        assert_eq!(metrics[1], 0.8);
        assert_eq!(metrics[0], 0.8);
    }

    fn feature_row_opt(cells: [(usize, f64); 8]) -> Vec<Option<f64>> {
        let mut out = vec![None; FEATURE_ROW_V2_FIELD_COUNT];
        for (index, value) in cells {
            out[index] = Some(value);
        }
        out
    }

    fn horizon_test_scan(
        solar_elevation_deg: f64,
        far_columns: Vec<Vec<Option<f64>>>,
    ) -> ScanInput {
        // Identical elevated high cloud candidate at 120 km along solar azimuth 270 deg.
        let mut features = vec![
            feature_row_opt([
                (0, 30.0),
                (1, 118.8),
                (2, 120.0),
                (3, 270.0),
                (4, 300.0),
                (5, 9000.0),
                (6, 88.0),
                (FEATURE_ROW_V2_HORIZONTAL_CLOUD_PROBABILITY, 0.85),
            ]),
        ];
        features.extend(far_columns);
        let mut scan = empty_scan(features);
        scan.solar_elevation_deg = solar_elevation_deg;
        scan.solar_azimuth_deg = 270.0;
        scan
    }

    #[test]
    fn negative_elevation_overcast_horizon_reduces_illumination_vs_clear_gap() {
        // Clear far-horizon gap beneath elevated cirrus at 260 km (above the ray)
        // and clear sunward horizon beyond.
        let clear_gap_far = vec![260.0, 420.0, 600.0]
            .into_iter()
            .flat_map(|dist| {
                let mut rows = vec![feature_row_opt([
                    (0, 30.0),
                    (1, 120.0 - dist / 96.0),
                    (2, dist),
                    (3, 270.0),
                    (4, 850.0),
                    (5, 1500.0),
                    (6, 20.0),
                    (FEATURE_ROW_V2_HORIZONTAL_CLOUD_PROBABILITY, 0.0),
                ])];
                if dist <= 280.0 {
                    rows.push(feature_row_opt([
                        (0, 30.0),
                        (1, 120.0 - dist / 96.0),
                        (2, dist),
                        (3, 270.0),
                        (4, 250.0),
                        (5, 10800.0),
                        (6, 78.0),
                        (FEATURE_ROW_V2_HORIZONTAL_CLOUD_PROBABILITY, 0.70),
                    ]));
                }
                rows
            })
            .collect();

        // Solid multi-layer overcast blocking the far sunward horizon below the ray.
        let overcast_far = vec![260.0, 420.0, 600.0]
            .into_iter()
            .flat_map(|dist| {
                vec![
                    feature_row_opt([
                        (0, 30.0),
                        (1, 120.0 - dist / 96.0),
                        (2, dist),
                        (3, 270.0),
                        (4, 850.0),
                        (5, 1500.0),
                        (6, 95.0),
                        (FEATURE_ROW_V2_HORIZONTAL_CLOUD_PROBABILITY, 0.92),
                    ]),
                    feature_row_opt([
                        (0, 30.0),
                        (1, 120.0 - dist / 96.0),
                        (2, dist),
                        (3, 270.0),
                        (4, 700.0),
                        (5, 3100.0),
                        (6, 94.0),
                        (FEATURE_ROW_V2_HORIZONTAL_CLOUD_PROBABILITY, 0.90),
                    ]),
                    feature_row_opt([
                        (0, 30.0),
                        (1, 120.0 - dist / 96.0),
                        (2, dist),
                        (3, 270.0),
                        (4, 500.0),
                        (5, 5600.0),
                        (6, 92.0),
                        (FEATURE_ROW_V2_HORIZONTAL_CLOUD_PROBABILITY, 0.88),
                    ]),
                ]
            })
            .collect();

        let clear_res =
            build_result(batch_request(vec![horizon_test_scan(-3.0, clear_gap_far)])).unwrap();
        let overcast_res =
            build_result(batch_request(vec![horizon_test_scan(-3.0, overcast_far)])).unwrap();

        let AssessmentScanOutput::Full(clear_step) = &clear_res.scans[0] else {
            panic!("expected full step");
        };
        let AssessmentScanOutput::Full(overcast_step) = &overcast_res.scans[0] else {
            panic!("expected full step");
        };

        let clear_top = clear_step.top_candidate.as_ref().unwrap();
        let overcast_top = overcast_step.top_candidate.as_ref().unwrap();

        assert!(
            clear_top.cloud_base_m > clear_top.illumination_height_m + 400.0,
            "elevated cloud base should exceed required illumination height + 400m"
        );
        assert!(clear_step.horizon_gap_score >= 0.85);
        assert_eq!(clear_step.overcast_shadow_factor, 1.0);
        assert!(
            clear_top.illumination_score > 0.95,
            "clear far-horizon gap should retain full geometric brilliance: {}",
            clear_top.illumination_score
        );

        assert!(
            overcast_step.horizon_gap_score < 0.30,
            "solid overcast should yield low horizon_gap_score, got {}",
            overcast_step.horizon_gap_score
        );
        assert!(
            overcast_step.overcast_shadow_factor < 0.40,
            "solid overcast should yield low overcast_shadow_factor, got {}",
            overcast_step.overcast_shadow_factor
        );
        assert!(
            overcast_top.illumination_score < clear_top.illumination_score,
            "solid overcast illumination ({}) must be strictly lower than clear gap ({})",
            overcast_top.illumination_score,
            clear_top.illumination_score
        );
        assert!(
            overcast_top.score < clear_top.score,
            "solid overcast candidate score ({}) must not inflate via dawn_boost above clear gap ({})",
            overcast_top.score,
            clear_top.score
        );

        let encoded = serde_json::to_value(&overcast_res).unwrap();
        assert!(encoded["scans"][0]["horizon_gap_score"].is_f64());
        assert!(encoded["scans"][0]["overcast_shadow_factor"].is_f64());
    }

    #[test]
    fn negative_elevation_low_overcast_deck_suppresses_dawn_boost() {
        let low_overcast_far: Vec<Vec<Option<f64>>> = vec![240.0, 380.0, 540.0]
            .into_iter()
            .map(|dist| {
                feature_row_opt([
                    (0, 30.0),
                    (1, 120.0 - dist / 96.0),
                    (2, dist),
                    (3, 270.0),
                    (4, 850.0),
                    (5, 1500.0),
                    (6, 96.0),
                    (FEATURE_ROW_V2_HORIZONTAL_CLOUD_PROBABILITY, 0.92),
                ])
            })
            .collect();

        let neg_overcast = build_result(batch_request(vec![horizon_test_scan(
            -3.5,
            low_overcast_far.clone(),
        )]))
        .unwrap();
        let pos_overcast = build_result(batch_request(vec![horizon_test_scan(
            1.0,
            low_overcast_far,
        )]))
        .unwrap();

        let AssessmentScanOutput::Full(neg_step) = &neg_overcast.scans[0] else {
            panic!("expected full step");
        };
        let AssessmentScanOutput::Full(pos_step) = &pos_overcast.scans[0] else {
            panic!("expected full step");
        };
        let neg_top = neg_step.top_candidate.as_ref().unwrap();
        let pos_top = pos_step.top_candidate.as_ref().unwrap();

        assert!(neg_step.overcast_shadow_factor < 0.70);
        assert!(neg_top.illumination_score < pos_top.illumination_score);
        assert!(neg_top.score < pos_top.score);
    }
}

// ---------------------------------------------------------------------------
// Standalone feature semantics (scoring.py:1036-1135) — the low-cloud penalty,
// local-deck clearance and cross-section blockers read the standalone opacity
// functions, which differ from the index metrics for satellite rows.
// ---------------------------------------------------------------------------

/// `feature_cloud_opacity_score` (scoring.py:1083-1108).
fn feature_cloud_opacity_standalone(feature: &Feature<'_>) -> f64 {
    if let Some(horizontal) = feature.horizontal_cloud_probability() {
        let probability = clamp(horizontal);
        if probability <= 0.0 {
            return 0.0;
        }
        return probability;
    }
    let rh_opacity = clamp((feature.rh_percent() - 82.0) / 16.0);
    let condensate = condensate_presence_opt(feature);
    let explicit = match cloud_fraction_opt(feature) {
        Some(fraction) => {
            let material = condensate.unwrap_or(0.5);
            Some(clamp(fraction * (0.35 + 0.65 * material)))
        }
        None => condensate,
    };
    match explicit {
        None => rh_opacity,
        Some(explicit) => match cloud_fraction_opt(feature) {
            Some(fraction) => {
                if fraction <= 0.0 {
                    0.0
                } else {
                    clamp(0.22 * rh_opacity + 0.78 * explicit)
                }
            }
            None => match condensate {
                Some(_) => clamp(0.22 * rh_opacity + 0.78 * explicit),
                None => rh_opacity,
            },
        },
    }
}

/// `feature_blocker_opacity_score` (scoring.py:1111-1119).
fn feature_blocker_opacity_standalone(feature: &Feature<'_>) -> f64 {
    let explicit_opacity = feature_cloud_opacity_standalone(feature);
    if feature.horizontal_cloud_probability().is_none()
        && feature.cloud_fraction_percent().is_none()
        && condensate_presence_opt(feature).is_none()
    {
        return clamp((feature.rh_percent() - 88.0) / 10.0);
    }
    explicit_opacity
}

/// `lightpath_blocker_opacity` (scoring.py:1734-1743) on the standalone opacity.
fn lightpath_blocker_opacity_standalone(feature: &Feature<'_>) -> f64 {
    let opacity = feature_blocker_opacity_standalone(feature);
    if opacity <= 0.0 {
        return 0.0;
    }
    let layer_factor = if feature.pressure_hpa() >= 700 {
        1.0
    } else if feature.pressure_hpa() >= 500 {
        0.82
    } else {
        0.58
    };
    clamp(opacity * layer_factor)
}

fn layer_name(pressure_hpa: i64) -> &'static str {
    if pressure_hpa <= 400 {
        "high"
    } else if pressure_hpa <= 700 {
        "mid"
    } else {
        "low"
    }
}

// ---------------------------------------------------------------------------
// Aerosol integrals for the local-deck transmission samples.
// ---------------------------------------------------------------------------

fn aerosol_extinction_coefficient(aod550: f64, height_m: f64, scale_height_m: f64) -> f64 {
    let height = height_m.max(0.0);
    let scale_height = scale_height_m.max(1.0);
    (aod550 / scale_height) * (-height / scale_height).exp()
}

/// `solar_inflow_aerosol_optical_depth` (scoring.py:806-852).
fn solar_inflow_aerosol_optical_depth(
    aod550: f64,
    target_distance_km: f64,
    target_height_m: f64,
    solar_elevation_deg: f64,
    max_distance_km: f64,
    scale_height_m: f64,
    observer_elevation_m: f64,
) -> f64 {
    if aod550 <= 0.0 {
        return 0.0;
    }
    let stop_distance = (target_distance_km + 20.0).max(max_distance_km);
    let steps = (((stop_distance - target_distance_km) / 15.0) as i64).clamp(48, 180);
    let mut tau = 0.0f64;
    let mut previous_distance = target_distance_km;
    let mut previous_height = target_height_m;
    let mut previous_alpha = aerosol_extinction_coefficient(
        aod550,
        (previous_height - observer_elevation_m).max(0.0),
        scale_height_m,
    );
    for index in 1..=steps {
        let distance =
            target_distance_km + (stop_distance - target_distance_km) * index as f64 / steps as f64;
        let height = solar_ray_height_at_distance_m(
            target_distance_km,
            target_height_m,
            distance,
            solar_elevation_deg,
        );
        if height <= 0.0 {
            let fraction = previous_height / (previous_height - height).max(1.0);
            let ground_distance =
                previous_distance + (distance - previous_distance) * clamp(fraction);
            let dx_m = (ground_distance - previous_distance) * 1000.0;
            let dz_m = -previous_height;
            let ds_m = dx_m.hypot(dz_m);
            let ground_alpha = aerosol_extinction_coefficient(aod550, 0.0, scale_height_m);
            tau += 0.5 * (previous_alpha + ground_alpha) * ds_m;
            break;
        }
        let alpha = aerosol_extinction_coefficient(
            aod550,
            (height - observer_elevation_m).max(0.0),
            scale_height_m,
        );
        let dx_m = (distance - previous_distance) * 1000.0;
        let dz_m = height - previous_height;
        let ds_m = dx_m.hypot(dz_m);
        tau += 0.5 * (alpha + previous_alpha) * ds_m;
        previous_distance = distance;
        previous_height = height;
        previous_alpha = alpha;
    }
    tau
}

/// `local_deck_aerosol_transmission_samples` (scoring.py:2389-2417).
fn local_deck_aerosol_transmission_samples(
    aod550: f64,
    inflow_aod550: f64,
    cloud_base_m: f64,
    solar_elevation_deg: f64,
    observer_elevation_m: f64,
) -> [f64; 3] {
    let scale_heights = [500.0f64, 1800.0, 4000.0];
    let mut samples = [0.0f64; 3];
    for (index, scale_height_m) in scale_heights.iter().enumerate() {
        let inflow_tau = solar_inflow_aerosol_optical_depth(
            inflow_aod550,
            0.0,
            cloud_base_m,
            solar_elevation_deg,
            MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM,
            *scale_height_m,
            observer_elevation_m,
        );
        let view_tau = aod550.max(0.0)
            * (1.0
                - (-(cloud_base_m - observer_elevation_m).max(0.0) / scale_height_m.max(1.0))
                    .exp());
        samples[index] = (-(inflow_tau + view_tau).min(20.0)).exp();
    }
    samples
}

/// `physical_transmission_quality` (scoring.py:2202-2214).
fn physical_transmission_quality(transmission: f64) -> f64 {
    let value = py_clamp(transmission, 0.0, 1.0);
    let invisible = 1.0 / 5000.0;
    let weak = 1.0 / 1000.0;
    let bright = 1.0 / 50.0;
    if value <= invisible {
        return 0.0;
    }
    if value <= weak {
        return 0.18 * (value / invisible).ln() / (weak / invisible).ln();
    }
    if value <= bright {
        return 0.18 + 0.52 * (value / weak).ln() / (bright / weak).ln();
    }
    py_clamp(
        0.70 + 0.30 * (value / bright).ln() / (1.0 / bright).ln(),
        0.0,
        1.0,
    )
}

// ---------------------------------------------------------------------------
// Cloud-edge geometry (geometry.py:20-110).
// ---------------------------------------------------------------------------

fn smoothstep(value: f64, low: f64, high: f64) -> f64 {
    if value <= low {
        return 0.0;
    }
    if value >= high {
        return 1.0;
    }
    let t = (value - low) / (high - low);
    t * t * (3.0 - 2.0 * t)
}

fn cloud_edge_max_distance_km(cloud_base_m: f64, observer_elevation_m: f64) -> f64 {
    let effective_height_km = (cloud_base_m - observer_elevation_m).max(0.0) / 1000.0;
    if effective_height_km <= 0.0 {
        return 0.0;
    }
    (2.0 * EARTH_RADIUS_KM * effective_height_km).sqrt()
}

fn cloud_edge_reach_km(solar_elevation_deg: f64) -> f64 {
    let effective_elevation_rad =
        solar_illumination_elevation_deg(solar_elevation_deg).to_radians();
    if effective_elevation_rad >= 0.0 {
        return 0.0;
    }
    -2.0 * EARTH_RADIUS_KM * effective_elevation_rad.tan()
}

fn cloud_edge_distance_quality(
    edge_distance_km: f64,
    cloud_base_m: f64,
    observer_elevation_m: f64,
) -> f64 {
    let maximum = cloud_edge_max_distance_km(cloud_base_m, observer_elevation_m);
    if edge_distance_km <= 0.0 || maximum <= 0.0 {
        return 0.0;
    }
    let ratio = edge_distance_km / maximum;
    let near_quality = 0.18 + 0.82 * smoothstep(ratio, 0.12, 0.38);
    let far_quality = 1.0 - smoothstep(ratio, 0.72, 1.0);
    (near_quality * far_quality).max(0.0).min(1.0)
}

fn cloud_edge_geometry_score(
    edge_distance_km: f64,
    cloud_base_m: f64,
    solar_elevation_deg: f64,
    observer_elevation_m: f64,
) -> f64 {
    let distance_quality =
        cloud_edge_distance_quality(edge_distance_km, cloud_base_m, observer_elevation_m);
    if distance_quality <= 0.0 {
        return 0.0;
    }
    if solar_illumination_elevation_deg(solar_elevation_deg) >= 0.0 {
        return distance_quality;
    }
    let reach = cloud_edge_reach_km(solar_elevation_deg);
    let reach_gate = smoothstep(reach / edge_distance_km.max(1.0), 0.85, 1.05);
    distance_quality * reach_gate
}

// ---------------------------------------------------------------------------
// Boundary / local-deck signals.
// ---------------------------------------------------------------------------

#[derive(Clone, Serialize)]
pub struct BoundarySignalOutput {
    layer: String,
    distance_km: f64,
    bearing_deg: f64,
    sector_delta_deg: f64,
    cloud_base_m: f64,
    cloud_top_m: f64,
    required_base_m: f64,
    apparent_base_elevation_deg: f64,
    path_clearance_score: f64,
    aerosol_transmission_score: f64,
    score: f64,
    edge_contrast_score: f64,
    clear_slot_score: f64,
    thickness_score: f64,
    cloud_type: String,
    cloud_type_burnability_score: f64,
    deep_cloud_risk: f64,
    aerosol_sensitivity_score: f64,
    aerosol_path_variability_score: f64,
    observer_path_aod550: Option<f64>,
    inflow_path_aod550: Option<f64>,
    regime: String,
    local_cloud_presence_score: f64,
    geometric_reach_km: f64,
    physical_transmission_min: f64,
    physical_transmission_max: f64,
    atmospheric_clarity_score: f64,
    edge_distance_lower_km: Option<f64>,
    edge_distance_upper_km: Option<f64>,
}

/// `boundary_path_clearance` (scoring.py:1997-2064), index-based variant.
fn boundary_path_clearance(
    features: &[Feature<'_>],
    index: &SpatialIndex,
    boundary_position: usize,
    cloud_base_m: f64,
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
    observer_elevation_m: f64,
) -> f64 {
    let boundary = &features[boundary_position];
    let target_elevation =
        apparent_cloud_elevation_deg(boundary.distance_km(), cloud_base_m, observer_elevation_m);
    let mut penalties: Vec<f64> = Vec::new();
    for position in index.query(
        &[(boundary.bearing_deg(), 16.0)],
        15.0,
        (boundary.distance_km() - 5.0).max(15.0),
        true,
    ) {
        if position == boundary_position {
            continue;
        }
        let feature = &features[position];
        if feature.distance_km() >= boundary.distance_km() - 5.0 {
            continue;
        }
        if feature.distance_km() < 15.0 {
            continue;
        }
        if angular_difference_deg(feature.bearing_deg(), boundary.bearing_deg()) > 16.0 {
            continue;
        }
        let opacity = feature.blocker_opacity();
        if opacity <= 0.0 {
            continue;
        }
        let blocker_base = feature.cloud_base_m();
        let blocker_elevation = feature.apparent_top_deg();
        if blocker_elevation < target_elevation - 0.35 && blocker_base > cloud_base_m + 800.0 {
            continue;
        }
        let distance_factor = clamp(1.0 - feature.distance_km() / boundary.distance_km().max(1.0));
        let layer_factor = if feature.pressure_hpa() >= boundary.pressure_hpa() {
            1.0
        } else {
            0.55
        };
        penalties.push(opacity * layer_factor * (0.55 + 0.45 * distance_factor));
    }
    let legacy_risk = penalties.iter().copied().fold(0.0f64, f64::max);
    let sightline_risk = observer_sightline_blocking_risk(
        features,
        index,
        boundary_position,
        cloud_base_m,
        observer_elevation_m,
    );
    let inflow_risk = solar_inflow_blocking_risk(
        features,
        index,
        boundary_position,
        cloud_base_m,
        solar_elevation_deg,
        solar_azimuth_deg,
        observer_elevation_m,
    );
    clamp(1.0 - legacy_risk.max(sightline_risk).max(inflow_risk))
}

/// `boundary_edge_contrast_score` (scoring.py:2067-2117), index-based variant.
fn boundary_edge_contrast_score(
    features: &[Feature<'_>],
    index: &SpatialIndex,
    boundary_position: usize,
) -> f64 {
    let boundary = &features[boundary_position];
    let boundary_opacity = boundary.cloud_opacity_metric();
    let mut comparison_opacities: Vec<f64> = Vec::new();
    let mut seen: std::collections::BTreeSet<usize> = std::collections::BTreeSet::new();
    for position in index.query_pressure(
        &[(boundary.bearing_deg(), 28.0)],
        boundary.pressure_hpa(),
        (boundary.distance_km() - 95.0).max(0.0),
        boundary.distance_km() + 95.0,
    ) {
        if position == boundary_position {
            continue;
        }
        if features[position].pressure_hpa() != boundary.pressure_hpa() {
            continue;
        }
        let sector_delta =
            angular_difference_deg(features[position].bearing_deg(), boundary.bearing_deg());
        let distance_delta = (features[position].distance_km() - boundary.distance_km()).abs();
        if sector_delta <= 28.0 && distance_delta <= 95.0 {
            comparison_opacities.push(features[position].cloud_opacity_metric());
            seen.insert(position);
            continue;
        }
        if sector_delta <= 16.0
            && 20.0 <= features[position].distance_km()
            && features[position].distance_km() < boundary.distance_km() - 12.0
        {
            comparison_opacities.push(features[position].cloud_opacity_metric());
            seen.insert(position);
        }
    }
    for position in index.query_pressure(
        &[(boundary.bearing_deg(), 16.0)],
        boundary.pressure_hpa(),
        20.0,
        (boundary.distance_km() - 12.0).max(20.0),
    ) {
        if position == boundary_position || seen.contains(&position) {
            continue;
        }
        if features[position].pressure_hpa() != boundary.pressure_hpa() {
            continue;
        }
        let sector_delta =
            angular_difference_deg(features[position].bearing_deg(), boundary.bearing_deg());
        let distance_delta = (features[position].distance_km() - boundary.distance_km()).abs();
        if sector_delta <= 28.0 && distance_delta <= 95.0 {
            comparison_opacities.push(features[position].cloud_opacity_metric());
            continue;
        }
        if sector_delta <= 16.0
            && 20.0 <= features[position].distance_km()
            && features[position].distance_km() < boundary.distance_km() - 12.0
        {
            comparison_opacities.push(features[position].cloud_opacity_metric());
        }
    }
    if comparison_opacities.len() < 3 {
        return 0.72;
    }
    let clearest_neighbor = comparison_opacities
        .iter()
        .copied()
        .fold(f64::INFINITY, f64::min);
    clamp((boundary_opacity - clearest_neighbor) / 0.72)
}

/// `boundary_clear_slot_score` (scoring.py:2119-2150), index-based variant.
fn boundary_clear_slot_score(
    features: &[Feature<'_>],
    index: &SpatialIndex,
    boundary_position: usize,
    solar_azimuth_deg: f64,
) -> f64 {
    let boundary = &features[boundary_position];
    let mut penalties: Vec<f64> = Vec::new();
    for position in index.query(
        &[(boundary.bearing_deg(), 18.0), (solar_azimuth_deg, 22.0)],
        15.0,
        (boundary.distance_km() - 8.0).max(15.0),
        true,
    ) {
        if position == boundary_position {
            continue;
        }
        let feature = &features[position];
        if feature.pressure_hpa() < boundary.pressure_hpa() {
            continue;
        }
        if feature.distance_km() < 15.0 || feature.distance_km() >= boundary.distance_km() - 8.0 {
            continue;
        }
        if angular_difference_deg(feature.bearing_deg(), boundary.bearing_deg()) > 18.0
            && angular_difference_deg(feature.bearing_deg(), solar_azimuth_deg) > 22.0
        {
            continue;
        }
        let opacity = feature.blocker_opacity();
        if opacity <= 0.0 {
            continue;
        }
        let distance_factor = clamp(1.0 - feature.distance_km() / boundary.distance_km().max(1.0));
        let lower_layer_factor = if feature.pressure_hpa() >= 700 {
            1.0
        } else {
            0.72
        };
        penalties.push(opacity * lower_layer_factor * (0.55 + 0.45 * distance_factor));
    }
    if penalties.is_empty() {
        return 1.0;
    }
    clamp(1.0 - penalties.iter().copied().fold(0.0f64, f64::max))
}

/// `boundary_thickness_score` (scoring.py:2153-2176) with the column branch.
fn boundary_thickness_score(
    features: &[Feature<'_>],
    boundary_position: usize,
    lookup: &ColumnLookup<'_>,
) -> f64 {
    let boundary = &features[boundary_position];
    if let Some(column) = lookup.get(boundary) {
        return clamp(1.0 - 0.55 * column.thickness_risk - 0.35 * column.deep_cloud_risk);
    }
    let mut cloudy_layers: std::collections::BTreeSet<i64> = std::collections::BTreeSet::new();
    for feature in features.iter() {
        if feature.pressure_hpa() > 700 {
            continue;
        }
        if angular_difference_deg(feature.bearing_deg(), boundary.bearing_deg()) > 16.0 {
            continue;
        }
        if (feature.distance_km() - boundary.distance_km()).abs() > 40.0 {
            continue;
        }
        if feature_cloud_opacity_standalone(feature) > 0.35 {
            cloudy_layers.insert(feature.pressure_hpa());
        }
    }
    match cloudy_layers.len() {
        0 | 1 | 2 => 1.0,
        3 => 0.85,
        4 => 0.65,
        _ => 0.45,
    }
}

/// `local_deck_path_clearance` (scoring.py:2420-2464).
fn local_deck_path_clearance(
    features: &[Feature<'_>],
    columns: &[(ColumnKey, CloudColumnOutput)],
    layer: &str,
    cloud_base_m: f64,
    edge_distance_km: f64,
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
    // Signature parity with the Python reference; the column scan never reads
    // the observer height there either.
    _observer_elevation_m: f64,
) -> f64 {
    let mut risks: Vec<f64> = Vec::new();
    for (_, column) in columns {
        if column.distance_km < 8.0 || column.distance_km > MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM {
            continue;
        }
        if angular_difference_deg(column.bearing_deg, solar_azimuth_deg) > 18.0 {
            continue;
        }
        let ray_height_m = solar_ray_height_at_distance_m(
            0.0,
            cloud_base_m,
            column.distance_km,
            solar_elevation_deg,
        );
        if ray_height_m <= 0.0 {
            return 0.0;
        }
        for (_, position) in &column.layers {
            let feature = &features[*position];
            let feature_layer = layer_name(feature.pressure_hpa());
            let blocker_base_m = estimated_cloud_base_table(feature.row);
            // Before the same-layer edge, the post-sunset ray travels below
            // the target deck.
            if feature_layer == layer
                && column.distance_km < edge_distance_km
                && ray_height_m < blocker_base_m
            {
                continue;
            }
            let opacity = feature_blocker_opacity_standalone(feature);
            if opacity <= 0.0 {
                continue;
            }
            let intersection = vertical_ray_intersection_score(
                ray_height_m,
                blocker_base_m,
                feature.height_m(),
                220.0,
            );
            if intersection > 0.0 {
                risks.push(opacity * intersection);
            }
        }
    }
    clamp(1.0 - risks.iter().copied().fold(0.0f64, f64::max))
}

fn column_layer_presence(column: &CloudColumnOutput, layer: &str) -> f64 {
    match layer {
        "low" => column.low_presence,
        "mid" => column.mid_presence,
        _ => column.high_presence,
    }
}

fn column_layer_features<'a>(
    features: &'a [Feature<'a>],
    column: &CloudColumnOutput,
    layer: &str,
) -> Vec<&'a Feature<'a>> {
    column
        .layers
        .iter()
        .map(|(_, position)| &features[*position])
        .filter(|feature| layer_name(feature.pressure_hpa()) == layer)
        .collect()
}

fn local_deck_cloud_base_m(
    features: &[Feature<'_>],
    column: &CloudColumnOutput,
    layer: &str,
) -> f64 {
    let layer_features = column_layer_features(features, column, layer);
    if layer_features.is_empty() {
        return 0.0;
    }
    layer_features
        .iter()
        .map(|feature| estimated_cloud_base_table(feature.row))
        .fold(f64::INFINITY, f64::min)
}

/// `local_deck_edge_signals` (scoring.py:2467-2655).
#[allow(clippy::too_many_arguments)]
fn local_deck_edge_signals(
    features: &[Feature<'_>],
    columns: &[(ColumnKey, CloudColumnOutput)],
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
    aerosol: &AerosolInput,
    observer_elevation_m: f64,
) -> Vec<BoundarySignalOutput> {
    let aod550 = aerosol.aod550;
    let column_values: Vec<&CloudColumnOutput> = columns.iter().map(|(_, column)| column).collect();
    if column_values.is_empty() {
        return Vec::new();
    }
    let current_reach_km = cloud_edge_reach_km(solar_elevation_deg);
    let required_base_m =
        crate::native::required_illumination_height_m(0.0, solar_elevation_deg, 0.0);
    let mut signals: Vec<BoundarySignalOutput> = Vec::new();
    for layer in ["low", "mid", "high"] {
        let mut local_rows: Vec<&CloudColumnOutput> = column_values
            .iter()
            .copied()
            .filter(|column| {
                column.distance_km <= 70.0 && column_layer_presence(column, layer) > 0.0
            })
            .collect();
        if local_rows.is_empty() {
            continue;
        }
        // Stable sort by distance (Timsort in the reference).
        local_rows.sort_by(|a, b| {
            a.distance_km
                .partial_cmp(&b.distance_km)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        let mut weighted_sum = 0.0f64;
        let mut weight_sum = 0.0f64;
        let mut local_peak = 0.0f64;
        for column in &local_rows {
            let weight = (-column.distance_km / 45.0).exp();
            weighted_sum += column_layer_presence(column, layer) * weight;
            weight_sum += weight;
            local_peak = local_peak.max(column_layer_presence(column, layer));
        }
        let local_mean = weighted_sum / weight_sum.max(1e-9);
        let local_presence = clamp(0.65 * local_peak + 0.35 * local_mean);
        if local_presence < 0.42 {
            continue;
        }
        // Python max() keeps the first maximum in iteration order.
        let mut target_column = local_rows[0];
        let mut target_key = f64::NEG_INFINITY;
        for column in &local_rows {
            let key = column_layer_presence(column, layer) * (-column.distance_km / 80.0).exp();
            if key > target_key {
                target_key = key;
                target_column = column;
            }
        }
        let cloud_base_m = local_deck_cloud_base_m(features, target_column, layer);
        if cloud_base_m <= required_base_m {
            continue;
        }
        let maximum_edge_distance_km =
            cloud_edge_max_distance_km(cloud_base_m, observer_elevation_m);
        let geometric_reach_km = if current_reach_km != 0.0 {
            current_reach_km
        } else {
            maximum_edge_distance_km
        };

        let mut corridor: Vec<&CloudColumnOutput> = column_values
            .iter()
            .copied()
            .filter(|column| {
                20.0 <= column.distance_km
                    && column.distance_km <= MAX_CANDIDATE_DISTANCE_KM
                    && angular_difference_deg(column.bearing_deg, solar_azimuth_deg) <= 22.0
            })
            .collect();
        corridor.sort_by(|a, b| {
            a.distance_km
                .partial_cmp(&b.distance_km)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        let mut last_cloud_distance = 0.0f64;
        let mut edge_column: Option<&CloudColumnOutput> = None;
        let mut clear_rows: Vec<&CloudColumnOutput> = Vec::new();
        for (index, column) in corridor.iter().enumerate() {
            let presence = column_layer_presence(column, layer);
            if presence >= 0.45 {
                last_cloud_distance = column.distance_km;
                continue;
            }
            if presence > 0.30 || column.distance_km < 30.0f64.max(last_cloud_distance + 8.0) {
                continue;
            }
            let following: Vec<&CloudColumnOutput> = corridor
                [index..(index + 6).min(corridor.len())]
                .iter()
                .copied()
                .filter(|row| row.distance_km <= column.distance_km + 150.0)
                .collect();
            if following.is_empty() {
                continue;
            }
            let mean_presence = statistics_mean(
                &following
                    .iter()
                    .map(|row| column_layer_presence(row, layer))
                    .collect::<Vec<f64>>(),
            );
            if mean_presence > 0.40 {
                continue;
            }
            edge_column = Some(column);
            clear_rows = following;
            break;
        }
        let edge_column = match edge_column {
            Some(column) => column,
            None => continue,
        };

        let edge_distance_km = 0.5 * (last_cloud_distance + edge_column.distance_km);
        let geometry_score = cloud_edge_geometry_score(
            edge_distance_km,
            cloud_base_m,
            solar_elevation_deg,
            observer_elevation_m,
        );
        if geometry_score <= 0.0 {
            continue;
        }
        let clear_presence = statistics_mean(
            &clear_rows
                .iter()
                .map(|row| column_layer_presence(row, layer))
                .collect::<Vec<f64>>(),
        );
        let edge_contrast = clamp((local_presence - clear_presence) / 0.65);
        let clear_slot = clamp((0.50 - clear_presence) / 0.50);
        if edge_contrast < 0.30 || clear_slot < 0.35 {
            continue;
        }
        let clearance = local_deck_path_clearance(
            features,
            columns,
            layer,
            cloud_base_m,
            edge_distance_km,
            solar_elevation_deg,
            solar_azimuth_deg,
            observer_elevation_m,
        );
        if clearance <= 0.10 {
            continue;
        }
        let (_, inflow_path_aod, path_variability) = path_aod_components(
            aerosol,
            target_column.lat,
            target_column.lon,
            0.0,
            solar_azimuth_deg,
            solar_azimuth_deg,
        );
        let transmission_samples = local_deck_aerosol_transmission_samples(
            aod550,
            inflow_path_aod,
            cloud_base_m,
            solar_elevation_deg,
            observer_elevation_m,
        );
        let transmission_qualities: Vec<f64> = transmission_samples
            .iter()
            .map(|value| physical_transmission_quality(*value))
            .collect();
        let transmission_spread = transmission_qualities
            .iter()
            .copied()
            .fold(f64::NEG_INFINITY, f64::max)
            - transmission_qualities
                .iter()
                .copied()
                .fold(f64::INFINITY, f64::min);
        let aerosol_quality =
            clamp(statistics_mean(&transmission_qualities) - 0.25 * transmission_spread);
        let thickness =
            clamp(1.0 - 0.55 * target_column.deep_cloud_risk - 0.25 * target_column.thickness_risk);
        let score = clamp(
            local_presence
                * geometry_score
                * clearance
                * (0.58 + 0.42 * edge_contrast)
                * (0.72 + 0.28 * clear_slot)
                * thickness,
        );
        if score <= 0.12 {
            continue;
        }
        let layer_features = column_layer_features(features, target_column, layer);
        let cloud_top_m = layer_features
            .iter()
            .map(|feature| feature.height_m())
            .fold(cloud_base_m, f64::max);
        signals.push(BoundarySignalOutput {
            layer: layer.to_string(),
            distance_km: edge_distance_km,
            bearing_deg: solar_azimuth_deg,
            sector_delta_deg: 0.0,
            cloud_base_m,
            cloud_top_m,
            required_base_m,
            apparent_base_elevation_deg: apparent_cloud_elevation_deg(
                edge_distance_km,
                cloud_base_m,
                observer_elevation_m,
            ),
            path_clearance_score: clearance,
            aerosol_transmission_score: aerosol_quality,
            score,
            edge_contrast_score: edge_contrast,
            clear_slot_score: clear_slot,
            thickness_score: thickness,
            cloud_type: target_column.cloud_type.clone(),
            cloud_type_burnability_score: target_column.burnability_score,
            deep_cloud_risk: target_column.deep_cloud_risk,
            aerosol_sensitivity_score: transmission_spread,
            aerosol_path_variability_score: path_variability,
            observer_path_aod550: Some(aod550),
            inflow_path_aod550: Some(inflow_path_aod),
            regime: "local_deck".to_string(),
            local_cloud_presence_score: local_presence,
            geometric_reach_km,
            physical_transmission_min: transmission_samples
                .iter()
                .copied()
                .fold(f64::INFINITY, f64::min),
            physical_transmission_max: transmission_samples
                .iter()
                .copied()
                .fold(f64::NEG_INFINITY, f64::max),
            atmospheric_clarity_score: atmospheric_clarity_score(aod550),
            edge_distance_lower_km: Some(last_cloud_distance),
            edge_distance_upper_km: Some(edge_column.distance_km),
        });
    }
    signals.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    signals
}

/// `boundary_lightpath_signals` (scoring.py:2658-2905), index-based variant.
#[allow(clippy::too_many_arguments)]
struct BoundaryRow {
    position: usize,
    layer: String,
    sector_delta: f64,
    opacity: f64,
    column_index: Option<usize>,
    cloud_base: f64,
    required_base: f64,
    illumination: f64,
    apparent_base: f64,
    sight: f64,
}

fn boundary_lightpath_signals(
    features: &[Feature<'_>],
    index: &SpatialIndex,
    columns: &[(ColumnKey, CloudColumnOutput)],
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
    aerosol: &AerosolInput,
    observer_elevation_m: f64,
) -> Vec<BoundarySignalOutput> {
    let lookup = ColumnLookup {
        map: columns
            .iter()
            .enumerate()
            .map(|(position, (key, _))| (*key, position))
            .collect(),
        columns,
    };
    let mut boundary_rows: Vec<BoundaryRow> = Vec::new();
    for position in index.query(&[(solar_azimuth_deg, 55.0)], 40.0, 360.0, false) {
        let feature = &features[position];
        let layer = layer_name(feature.pressure_hpa()).to_string();
        if layer == "low" {
            continue;
        }
        if feature.distance_km() < 40.0 || feature.distance_km() > 360.0 {
            continue;
        }
        let sector_delta = angular_difference_deg(feature.bearing_deg(), solar_azimuth_deg);
        if sector_delta > 55.0 {
            continue;
        }
        let opacity = feature.cloud_opacity_metric();
        if opacity <= 0.0 {
            continue;
        }
        let column = lookup.get(feature);
        let cloud_base = match column {
            Some(column) if column.cloud_base_m > 0.0 => column.cloud_base_m,
            _ => estimated_cloud_base_table(feature.row),
        };
        let required_base = crate::native::required_illumination_height_m(
            feature.distance_km(),
            solar_elevation_deg,
            sector_delta,
        );
        let illumination = illumination_feasibility(cloud_base, required_base);
        if illumination <= 0.0 {
            continue;
        }
        let apparent_base =
            apparent_cloud_elevation_deg(feature.distance_km(), cloud_base, observer_elevation_m);
        let sight = line_of_sight_weight(apparent_base);
        if sight <= 0.0 {
            continue;
        }
        boundary_rows.push(BoundaryRow {
            position,
            layer,
            sector_delta,
            opacity,
            column_index: lookup.map.get(&column_key(feature)).copied(),
            cloud_base,
            required_base,
            illumination,
            apparent_base,
            sight,
        });
    }
    let mut eligible: Vec<(usize, f64, f64, f64, f64)> = Vec::new();
    let mut aerosol_inputs: Vec<[f64; 8]> = Vec::new();
    for (row_index, row) in boundary_rows.iter().enumerate() {
        let clearance = boundary_path_clearance(
            features,
            index,
            row.position,
            row.cloud_base,
            solar_elevation_deg,
            solar_azimuth_deg,
            observer_elevation_m,
        );
        if clearance <= 0.05 {
            continue;
        }
        let feature = &features[row.position];
        let (observer_path_aod, inflow_path_aod, path_variability) = path_aod_components(
            aerosol,
            feature.lat(),
            feature.lon(),
            feature.distance_km(),
            feature.bearing_deg(),
            solar_azimuth_deg,
        );
        eligible.push((
            row_index,
            clearance,
            observer_path_aod,
            inflow_path_aod,
            path_variability,
        ));
        aerosol_inputs.push([
            aerosol.aod550,
            observer_path_aod,
            inflow_path_aod,
            feature.distance_km(),
            row.cloud_base,
            row.apparent_base,
            solar_elevation_deg,
            observer_elevation_m,
        ]);
    }
    let mut signals: Vec<BoundarySignalOutput> = Vec::new();
    for (eligible_index, values) in aerosol_inputs.iter().enumerate() {
        let samples = aerosol_transmission_samples_impl(
            values[0], values[1], values[2], values[3], values[4], values[5], values[6], values[7],
        );
        let minimum = samples.iter().copied().fold(f64::INFINITY, f64::min);
        let maximum = samples.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        let spread = maximum - minimum;
        let aerosol_weight =
            ((samples[0] + samples[1] + samples[2]) / 3.0 - 0.15 * spread).clamp(0.0, 1.0);
        let aerosol_sensitivity = clamp(spread);
        let (row_index, clearance, observer_path_aod, inflow_path_aod, path_variability) =
            eligible[eligible_index];
        let row = &boundary_rows[row_index];
        let position = row.position;
        let feature = &features[position];
        let edge_contrast = boundary_edge_contrast_score(features, index, position);
        let clear_slot = boundary_clear_slot_score(features, index, position, solar_azimuth_deg);
        let column = row
            .column_index
            .map(|column_index| &columns[column_index].1);
        let type_burnability = column.map_or(0.5, |column| column.burnability_score);
        let cloud_type = column.map_or("unknown".to_string(), |column| column.cloud_type.clone());
        let deep_risk = column.map_or(0.0, |column| column.deep_cloud_risk);
        let thickness = match row.column_index {
            Some(column_index) => clamp(
                1.0 - 0.55 * columns[column_index].1.thickness_risk
                    - 0.35 * columns[column_index].1.deep_cloud_risk,
            ),
            None => boundary_thickness_score(features, position, &lookup),
        };
        let azimuth_score = clamp(1.0 - row.sector_delta / 55.0);
        let distance_score = cloud_edge_geometry_score(
            feature.distance_km(),
            row.cloud_base,
            solar_elevation_deg,
            observer_elevation_m,
        );
        if distance_score <= 0.0 {
            continue;
        }
        let layer_weight = if row.layer == "mid" { 1.0 } else { 0.82 };
        let base_score = clamp(
            0.24 * row.opacity
                + 0.18 * row.illumination
                + 0.14 * clearance
                + 0.14 * edge_contrast
                + 0.10 * clear_slot
                + 0.08 * distance_score
                + 0.06 * azimuth_score
                + 0.04 * row.sight
                + 0.02 * thickness,
        ) * layer_weight;
        let type_factor = 0.72 + 0.28 * type_burnability;
        let score = clamp(
            base_score
                * (0.55 + 0.45 * edge_contrast)
                * (0.75 + 0.25 * clear_slot)
                * thickness
                * type_factor,
        );
        if score <= 0.18 {
            continue;
        }
        signals.push(BoundarySignalOutput {
            layer: row.layer.clone(),
            distance_km: feature.distance_km(),
            bearing_deg: feature.bearing_deg(),
            sector_delta_deg: row.sector_delta,
            cloud_base_m: row.cloud_base,
            cloud_top_m: feature.height_m(),
            required_base_m: row.required_base,
            apparent_base_elevation_deg: row.apparent_base,
            path_clearance_score: clearance,
            aerosol_transmission_score: aerosol_weight,
            score,
            edge_contrast_score: edge_contrast,
            clear_slot_score: clear_slot,
            thickness_score: thickness,
            cloud_type,
            cloud_type_burnability_score: type_burnability,
            deep_cloud_risk: deep_risk,
            aerosol_sensitivity_score: aerosol_sensitivity,
            aerosol_path_variability_score: path_variability,
            observer_path_aod550: Some(observer_path_aod),
            inflow_path_aod550: Some(inflow_path_aod),
            regime: "remote_boundary".to_string(),
            local_cloud_presence_score: 0.0,
            geometric_reach_km: 0.0,
            physical_transmission_min: 0.0,
            physical_transmission_max: 0.0,
            atmospheric_clarity_score: atmospheric_clarity_score(aerosol.aod550),
            edge_distance_lower_km: None,
            edge_distance_upper_km: None,
        });
    }
    signals.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    signals
}

// ---------------------------------------------------------------------------
// Cloud-hole lightpath signals (scoring.py:2908-3217, Python path semantics —
// the authoritative path for satellite probability columns and elevated
// observers; the engine unifies the former native/python dual path).
// ---------------------------------------------------------------------------

#[derive(Serialize, Clone)]
pub struct CloudHoleSignalOutput {
    distance_km: f64,
    bearing_deg: f64,
    sector_delta_deg: f64,
    clear_slot_score: f64,
    edge_contrast_score: f64,
    rear_target_distance_km: f64,
    rear_target_bearing_deg: f64,
    rear_target_cloud_type: String,
    rear_target_burnability_score: f64,
    rear_target_score: f64,
    alignment_score: f64,
    path_clearance_score: f64,
    rear_blocking_risk: f64,
    score: f64,
}

fn column_path_presence(column: &CloudColumnOutput) -> f64 {
    clamp(
        column
            .low_presence
            .max(0.86 * column.mid_presence)
            .max(0.65 * column.high_presence),
    )
}

/// `_cloud_hole_min_penetration_km` (scoring.py:2912-2933).
fn cloud_hole_min_penetration_km(
    hole_distance_km: f64,
    solar_elevation_deg: f64,
    sector_delta_deg: f64,
    cloud_thickness_m: f64,
) -> f64 {
    let directional_distance = hole_distance_km * sector_delta_deg.to_radians().cos();
    let local_elevation_rad = solar_illumination_elevation_deg(solar_elevation_deg).to_radians()
        + directional_distance / EARTH_RADIUS_KM;
    if local_elevation_rad >= 0.0 {
        return 0.0;
    }
    let abs_elev = (-local_elevation_rad).min(20.0f64.to_radians());
    if abs_elev < 1e-6 {
        return f64::INFINITY;
    }
    cloud_thickness_m / (abs_elev.tan() * 1000.0)
}

fn cloud_hole_clear_slot_score(column: &CloudColumnOutput) -> f64 {
    clamp((0.56 - column_path_presence(column)) / 0.56)
}

fn cloud_hole_edge_contrast_score(
    column_values: &[&CloudColumnOutput],
    hole: &CloudColumnOutput,
) -> f64 {
    let hole_presence = column_path_presence(hole);
    let mut neighbor_presence: Vec<f64> = Vec::new();
    for column in column_values {
        if std::ptr::eq(*column, hole) {
            continue;
        }
        let az_delta = angular_difference_deg(column.bearing_deg, hole.bearing_deg);
        let distance_delta = (column.distance_km - hole.distance_km).abs();
        if az_delta <= 34.0 && (20.0..=95.0).contains(&distance_delta) {
            neighbor_presence.push(column_path_presence(column));
        } else if (8.0..=36.0).contains(&az_delta) && distance_delta <= 80.0 {
            neighbor_presence.push(column_path_presence(column));
        }
    }
    if neighbor_presence.is_empty() {
        return 0.0;
    }
    let cloudy_neighbor = neighbor_presence
        .iter()
        .copied()
        .fold(f64::NEG_INFINITY, f64::max);
    clamp((cloudy_neighbor - hole_presence) / 0.62)
}

fn cloud_hole_path_clearance(
    column_values: &[&CloudColumnOutput],
    hole: &CloudColumnOutput,
    target: &CloudColumnOutput,
) -> f64 {
    let mut penalties: Vec<f64> = Vec::new();
    for column in column_values {
        if std::ptr::eq(*column, hole) || std::ptr::eq(*column, target) {
            continue;
        }
        if column.distance_km <= hole.distance_km + 8.0
            || column.distance_km >= target.distance_km - 8.0
        {
            continue;
        }
        if angular_difference_deg(column.bearing_deg, target.bearing_deg) > 18.0 {
            continue;
        }
        let blocker = clamp(
            column
                .low_presence
                .max(0.82 * column.mid_presence)
                .max(0.45 * column.deep_cloud_risk),
        );
        if blocker <= 0.0 {
            continue;
        }
        let distance_factor = clamp(
            1.0 - (column.distance_km - hole.distance_km)
                / (target.distance_km - hole.distance_km).max(1.0),
        );
        penalties.push(blocker * (0.55 + 0.45 * distance_factor));
    }
    if penalties.is_empty() {
        return 1.0;
    }
    clamp(1.0 - penalties.iter().copied().fold(0.0f64, f64::max))
}

fn cloud_hole_blocking_risk(column: &CloudColumnOutput) -> f64 {
    let low_mid_block = column.low_presence.max(0.82 * column.mid_presence);
    let thick_upper_block = column.high_presence.max(column.mid_presence)
        * clamp(
            1.0 - column.burnability_score
                + 0.45 * column.thickness_risk
                + 0.45 * column.deep_cloud_risk,
        );
    clamp(
        0.66 * low_mid_block
            + 0.46 * column.deep_cloud_risk
            + 0.28 * column.thickness_risk
            + 0.30 * thick_upper_block,
    )
}

fn cloud_hole_rear_blocking_risk(
    column_values: &[&CloudColumnOutput],
    hole: &CloudColumnOutput,
    target: &CloudColumnOutput,
) -> f64 {
    let mut risks: Vec<f64> = Vec::new();
    for column in column_values {
        if std::ptr::eq(*column, hole) || std::ptr::eq(*column, target) {
            continue;
        }
        if column.distance_km <= hole.distance_km + 8.0
            || column.distance_km >= target.distance_km - 8.0
        {
            continue;
        }
        if angular_difference_deg(column.bearing_deg, target.bearing_deg) > 18.0
            && angular_difference_deg(column.bearing_deg, hole.bearing_deg) > 18.0
        {
            continue;
        }
        let risk = cloud_hole_blocking_risk(column);
        if risk <= 0.0 {
            continue;
        }
        let distance_factor = clamp(
            1.0 - (column.distance_km - hole.distance_km)
                / (target.distance_km - hole.distance_km).max(1.0),
        );
        risks.push(risk * (0.62 + 0.38 * distance_factor));
    }
    let target_self_risk = clamp(
        0.55 * target.deep_cloud_risk + 0.35 * target.thickness_risk + 0.28 * target.low_presence,
    );
    risks.push(target_self_risk);
    risks.iter().copied().fold(0.0f64, f64::max)
}

fn cloud_hole_rear_target_score(
    column: &CloudColumnOutput,
    solar_elevation_deg: f64,
    sector_delta_deg: f64,
) -> f64 {
    let high_mid = column.high_presence.max(0.78 * column.mid_presence);
    if high_mid <= 0.0 {
        return 0.0;
    }
    let required_height = crate::native::required_illumination_height_m(
        column.distance_km,
        solar_elevation_deg,
        sector_delta_deg,
    );
    let height_score = illumination_feasibility(column.cloud_base_m, required_height);
    let distance_score = clamp(1.0 - (column.distance_km - 210.0).abs() / 240.0);
    let self_blocking = cloud_hole_blocking_risk(column);
    clamp(
        high_mid
            * column.burnability_score
            * height_score
            * (0.45 + 0.55 * distance_score)
            * (1.0 - 0.65 * column.deep_cloud_risk)
            * (1.0 - 0.36 * self_blocking),
    )
}

/// `_cloud_hole_python_path` (scoring.py:3128-3217).
fn cloud_hole_lightpath_signals(
    columns: &[(ColumnKey, CloudColumnOutput)],
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
) -> Vec<CloudHoleSignalOutput> {
    let column_refs: Vec<&CloudColumnOutput> = columns.iter().map(|(_, column)| column).collect();
    let column_values: Vec<&CloudColumnOutput> = column_refs
        .iter()
        .copied()
        .filter(|column| {
            column.distance_km <= MAX_CANDIDATE_DISTANCE_KM
                && angular_difference_deg(column.bearing_deg, solar_azimuth_deg) <= 75.0
        })
        .collect();
    let mut signals: Vec<CloudHoleSignalOutput> = Vec::new();
    for hole in &column_values {
        if hole.distance_km < 25.0 || hole.distance_km > 260.0 {
            continue;
        }
        let sector_delta = angular_difference_deg(hole.bearing_deg, solar_azimuth_deg);
        if sector_delta > 32.0 {
            continue;
        }
        let cloud_thickness_m = hole.cloud_top_m.max(500.0);
        let min_penetration = cloud_hole_min_penetration_km(
            hole.distance_km,
            solar_elevation_deg,
            sector_delta,
            cloud_thickness_m,
        );
        if min_penetration > 50.0 {
            continue;
        }
        let clear_slot = cloud_hole_clear_slot_score(hole);
        if clear_slot < 0.45 {
            continue;
        }
        let edge_contrast = cloud_hole_edge_contrast_score(&column_values, hole);
        if edge_contrast < 0.35 {
            continue;
        }
        struct RearTarget {
            rank: f64,
            position: usize,
            target_score: f64,
            alignment: f64,
            clearance: f64,
            rear_blocking: f64,
        }
        let mut rear_targets: Vec<RearTarget> = Vec::new();
        for (column_index, target) in column_values.iter().enumerate() {
            if target.distance_km <= hole.distance_km + 35.0
                || target.distance_km > MAX_CANDIDATE_DISTANCE_KM
            {
                continue;
            }
            let target_delta = angular_difference_deg(target.bearing_deg, solar_azimuth_deg);
            let hole_target_delta = angular_difference_deg(target.bearing_deg, hole.bearing_deg);
            if target_delta > 38.0 || hole_target_delta > 24.0 {
                continue;
            }
            let target_score =
                cloud_hole_rear_target_score(target, solar_elevation_deg, target_delta);
            if target_score < 0.22 {
                continue;
            }
            let alignment =
                clamp(1.0 - hole_target_delta / 24.0) * clamp(1.0 - target_delta / 44.0);
            let clearance = cloud_hole_path_clearance(&column_values, hole, target);
            let rear_blocking = cloud_hole_rear_blocking_risk(&column_values, hole, target);
            if rear_blocking > 0.72 {
                continue;
            }
            rear_targets.push(RearTarget {
                rank: target_score * alignment * clearance * (1.0 - 0.62 * rear_blocking),
                position: column_index,
                target_score,
                alignment,
                clearance,
                rear_blocking,
            });
        }
        if rear_targets.is_empty() {
            continue;
        }
        rear_targets.sort_by(|a, b| {
            b.rank
                .partial_cmp(&a.rank)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        let rear = &rear_targets[0];
        let target = column_values[rear.position];
        let distance_gap_score = clamp((target.distance_km - hole.distance_km - 30.0) / 130.0)
            * clamp(1.0 - (target.distance_km - hole.distance_km - 260.0) / 260.0);
        let score = clamp(
            (0.26 * clear_slot
                + 0.22 * edge_contrast
                + 0.24 * rear.target_score
                + 0.14 * rear.alignment
                + 0.10 * rear.clearance
                + 0.04 * distance_gap_score)
                * (1.0 - 0.50 * rear.rear_blocking),
        );
        if score <= 0.22 {
            continue;
        }
        signals.push(CloudHoleSignalOutput {
            distance_km: hole.distance_km,
            bearing_deg: hole.bearing_deg,
            sector_delta_deg: sector_delta,
            clear_slot_score: clear_slot,
            edge_contrast_score: edge_contrast,
            rear_target_distance_km: target.distance_km,
            rear_target_bearing_deg: target.bearing_deg,
            rear_target_cloud_type: target.cloud_type.clone(),
            rear_target_burnability_score: target.burnability_score,
            rear_target_score: rear.target_score,
            alignment_score: rear.alignment,
            path_clearance_score: rear.clearance,
            rear_blocking_risk: rear.rear_blocking,
            score,
        });
    }
    signals.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    signals
}

// ---------------------------------------------------------------------------
// Solar inflow blockage, penalties.
// ---------------------------------------------------------------------------

#[derive(Serialize, Clone)]
pub struct SolarInflowBlockageOutput {
    score: f64,
    distance_km: f64,
    bearing_deg: f64,
    pressure_hpa: i64,
    rh_percent: f64,
    cloud_base_m: f64,
    cloud_top_m: f64,
    ray_height_m: f64,
    target_distance_km: f64,
    target_bearing_deg: f64,
    target_height_m: f64,
}

/// `diagnose_solar_inflow_blockage` (scoring.py:1841-1907), index-based variant.
fn diagnose_solar_inflow_blockage(
    features: &[Feature<'_>],
    index: &SpatialIndex,
    target: Option<&CandidateSignalOutput>,
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
) -> Option<SolarInflowBlockageOutput> {
    let target = target?;
    let mut rows: Vec<(f64, usize, f64, f64, f64)> = Vec::new();
    for position in index.query(
        &[(target.bearing_deg, 16.0), (solar_azimuth_deg, 18.0)],
        target.distance_km + 8.0,
        MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM,
        true,
    ) {
        let feature = &features[position];
        if (feature.distance_km() - target.distance_km).abs() < 8.0 {
            continue;
        }
        if feature.distance_km() <= target.distance_km + 8.0
            || feature.distance_km() > MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM
        {
            continue;
        }
        let gap_km = feature.distance_km() - target.distance_km;
        let bearing_tolerance = if gap_km <= 360.0 { 16.0 } else { 8.0 };
        let solar_tolerance = if gap_km <= 360.0 { 18.0 } else { 10.0 };
        let target_delta = angular_difference_deg(feature.bearing_deg(), target.bearing_deg);
        let solar_delta = angular_difference_deg(feature.bearing_deg(), solar_azimuth_deg);
        if target_delta > bearing_tolerance && solar_delta > solar_tolerance {
            continue;
        }
        let opacity = feature.lightpath_opacity();
        if opacity <= 0.0 {
            continue;
        }
        let ray_height = solar_ray_height_at_distance_m(
            target.distance_km,
            target.cloud_base_m,
            feature.distance_km(),
            solar_elevation_deg,
        );
        let cloud_base = feature.cloud_base_m();
        let intersection =
            vertical_ray_intersection_score(ray_height, cloud_base, feature.height_m(), 850.0);
        if intersection <= 0.0 {
            continue;
        }
        let distance_factor =
            clamp(1.0 - (gap_km - 20.0) / (MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM - 20.0));
        let alignment = clamp(
            1.0 - (target_delta / bearing_tolerance.max(1.0))
                .max(solar_delta / solar_tolerance.max(1.0)),
        );
        let far_factor = if gap_km <= 360.0 { 1.0 } else { 0.72 };
        let score =
            opacity * intersection * alignment * (0.45 + 0.55 * distance_factor) * far_factor;
        if score <= 0.0 {
            continue;
        }
        rows.push((score, position, ray_height, cloud_base, intersection));
    }
    rows.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    let (score, position, ray_height, cloud_base, _) = rows.first().copied()?;
    let feature = &features[position];
    Some(SolarInflowBlockageOutput {
        score: clamp(score),
        distance_km: feature.distance_km(),
        bearing_deg: feature.bearing_deg(),
        pressure_hpa: feature.pressure_hpa(),
        rh_percent: feature.rh_percent(),
        cloud_base_m: cloud_base,
        cloud_top_m: feature.height_m(),
        ray_height_m: ray_height,
        target_distance_km: target.distance_km,
        target_bearing_deg: target.bearing_deg,
        target_height_m: target.cloud_base_m,
    })
}

/// `low_cloud_penalty` (scoring.py:1018-1043), index-based variant.
fn low_cloud_penalty(
    features: &[Feature<'_>],
    index: &SpatialIndex,
    solar_azimuth_deg: f64,
) -> f64 {
    let mut penalties: Vec<f64> = Vec::new();
    for position in index.query(&[(solar_azimuth_deg, 70.0)], 0.0, 120.0, true) {
        let feature = &features[position];
        if feature.pressure_hpa() < 750 {
            continue;
        }
        if feature.distance_km() > 120.0 {
            continue;
        }
        let sector_delta = angular_difference_deg(feature.bearing_deg(), solar_azimuth_deg);
        if sector_delta > 70.0 {
            continue;
        }
        penalties.push(feature_blocker_opacity_standalone(feature));
    }
    if penalties.is_empty() {
        return 0.0;
    }
    clamp(statistics_mean(&penalties))
}

#[derive(Serialize, Clone)]
pub struct SurfaceWeatherPenaltyOutput {
    penalty: f64,
    visibility_m: Option<f64>,
    precip_rate_kg_m2_s: Option<f64>,
}

/// `surface_weather_penalty` (scoring.py:1022-1043), index-based variant.
fn surface_weather_penalty(
    features: &[Feature<'_>],
    index: &SpatialIndex,
) -> SurfaceWeatherPenaltyOutput {
    // (lat, lon) rounded keys keep the nearest feature per grid point.
    let mut points: HashMap<ColumnKey, usize> = HashMap::new();
    for position in index.query(&[(0.0, 180.0)], 0.0, 80.0, false) {
        let feature = &features[position];
        if feature.distance_km() > 80.0 {
            continue;
        }
        if feature.row[18].is_nan() && feature.row[19].is_nan() {
            continue;
        }
        let key = ColumnKey([
            python_round(feature.lat(), 4).to_bits(),
            python_round(feature.lon(), 4).to_bits(),
            0,
            0,
        ]);
        let replace = match points.get(&key) {
            None => true,
            Some(&current) => feature.distance_km() < features[current].distance_km(),
        };
        if replace {
            points.insert(key, position);
        }
    }
    let mut nearest: Vec<usize> = points.values().copied().collect();
    nearest.sort_by(|a, b| {
        features[*a]
            .distance_km()
            .partial_cmp(&features[*b].distance_km())
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    nearest.truncate(12);
    if nearest.is_empty() {
        return SurfaceWeatherPenaltyOutput {
            penalty: 0.0,
            visibility_m: None,
            precip_rate_kg_m2_s: None,
        };
    }
    let visibility_values: Vec<f64> = nearest
        .iter()
        .filter_map(|position| optional(features[*position].row[18]))
        .collect();
    let precipitation_values: Vec<f64> = nearest
        .iter()
        .filter_map(|position| optional(features[*position].row[19]))
        .collect();
    let visibility_m = if visibility_values.is_empty() {
        None
    } else {
        Some(statistics_mean(&visibility_values))
    };
    let precip_rate = if precipitation_values.is_empty() {
        None
    } else {
        Some(statistics_mean(&precipitation_values))
    };
    let visibility_penalty = match visibility_m {
        Some(value) => clamp((9000.0 - value) / 8000.0),
        None => 0.0,
    };
    let precipitation_penalty = clamp(precip_rate.unwrap_or(0.0) / 0.0008);
    SurfaceWeatherPenaltyOutput {
        penalty: clamp(0.85 * visibility_penalty.max(precipitation_penalty)),
        visibility_m,
        precip_rate_kg_m2_s: precip_rate,
    }
}

// ---------------------------------------------------------------------------
// Solar-path cross-section (cross_section.py:34-432).
// ---------------------------------------------------------------------------

const PROFILE_DISTANCES_KM: [f64; 12] = [
    30.0, 55.0, 85.0, 120.0, 170.0, 230.0, 310.0, 400.0, 520.0, 650.0, 780.0, 900.0,
];

#[derive(Serialize, Clone)]
pub struct SolarPathProfileLevelOutput {
    pressure_hpa: i64,
    height_m: f64,
    rh_percent: f64,
    temperature_c: Option<f64>,
    cloud_fraction_percent: Option<f64>,
    cloud_liquid_kg_kg: Option<f64>,
    cloud_ice_kg_kg: Option<f64>,
}

#[derive(Serialize, Clone)]
pub struct SolarPathProfileSampleOutput {
    distance_km: f64,
    bearing_deg: f64,
    cloud_base_m: f64,
    cloud_top_m: f64,
    cloud_type: String,
    high_presence: f64,
    mid_presence: f64,
    low_presence: f64,
    deep_cloud_risk: f64,
    thickness_risk: f64,
    required_illumination_height_m: f64,
    blocker_risk: f64,
    lightpath_clearance_score: f64,
    target_cloud_score: f64,
    illumination_status: String,
    levels: Vec<SolarPathProfileLevelOutput>,
}

#[derive(Serialize, Clone)]
pub struct CrossSectionOutput {
    azimuth_deg: f64,
    samples_count: usize,
    lightpath_integrity_score: f64,
    path_extinction_score: f64,
    vertical_cloud_type: String,
    cloud_type_burnability_score: f64,
    deep_cloud_risk: f64,
    blocking_risk: f64,
    target_cloud_score: f64,
    summary: Vec<String>,
    profile: Vec<SolarPathProfileSampleOutput>,
}

fn column_blocker_opacity(
    features: &[Feature<'_>],
    column: &CloudColumnOutput,
    layer: &str,
) -> f64 {
    let opacities: Vec<f64> = column
        .layers
        .iter()
        .filter(|(pressure, _)| {
            (layer == "low" && *pressure >= 700)
                || (layer == "mid" && (500..=700).contains(pressure))
        })
        .map(|(_, position)| lightpath_blocker_opacity_standalone(&features[*position]))
        .collect();
    opacities
        .iter()
        .copied()
        .fold(f64::NEG_INFINITY, f64::max)
        .max(0.0)
}

fn target_column_score(column: &CloudColumnOutput, solar_elevation_deg: f64) -> f64 {
    let high_mid = column.high_presence.max(0.82 * column.mid_presence);
    let distance_score = clamp(1.0 - (column.distance_km - 170.0).abs() / 210.0);
    let required_height =
        crate::native::required_illumination_height_m(column.distance_km, solar_elevation_deg, 0.0);
    let high_enough = clamp((column.cloud_base_m - required_height + 1200.0) / 4200.0);
    clamp(
        high_mid
            * column.burnability_score
            * distance_score
            * high_enough
            * (1.0 - 0.55 * column.deep_cloud_risk),
    )
}

fn target_cloud_score(columns: &[&CloudColumnOutput], solar_elevation_deg: f64) -> f64 {
    columns
        .iter()
        .map(|column| target_column_score(column, solar_elevation_deg))
        .fold(0.0f64, f64::max)
}

fn path_extinction_score(features: &[Feature<'_>], samples: &[&CloudColumnOutput]) -> f64 {
    let count = samples.len().max(1) as f64;
    let low_extinction = samples
        .iter()
        .map(|column| {
            column_blocker_opacity(features, column, "low")
                * (1.0 - column.distance_km / 240.0).max(0.0)
        })
        .sum::<f64>()
        / count;
    let mid_extinction = samples
        .iter()
        .map(|column| column_blocker_opacity(features, column, "mid") * 0.45)
        .sum::<f64>()
        / count;
    let cloud_extinction = clamp(low_extinction + mid_extinction);
    clamp(1.0 - 0.42 * cloud_extinction)
}

fn column_3d_blocking_risk(
    features: &[Feature<'_>],
    column: &CloudColumnOutput,
    target_distance_km: f64,
    target_height_m: f64,
    solar_elevation_deg: f64,
    observer_elevation_m: f64,
) -> f64 {
    if (column.distance_km - target_distance_km).abs() < 8.0 {
        return 0.0;
    }
    let ray_height;
    let distance_factor;
    if column.distance_km < target_distance_km {
        ray_height = sightline_height_at_distance_m(
            target_distance_km,
            target_height_m,
            column.distance_km,
            observer_elevation_m,
        );
        distance_factor = clamp(1.0 - column.distance_km / target_distance_km.max(1.0));
    } else {
        ray_height = solar_ray_height_at_distance_m(
            target_distance_km,
            target_height_m,
            column.distance_km,
            solar_elevation_deg,
        );
        distance_factor = clamp(
            1.0 - (column.distance_km - target_distance_km - 20.0)
                / (MAX_SOLAR_INFLOW_BLOCKER_DISTANCE_KM - 20.0),
        );
    }
    let mut layer_risks: Vec<f64> = Vec::new();
    for (_, position) in &column.layers {
        let feature = &features[*position];
        let opacity = lightpath_blocker_opacity_standalone(feature);
        if opacity <= 0.0 {
            continue;
        }
        let base_m = estimated_cloud_base_table(feature.row);
        let intersection =
            vertical_ray_intersection_score(ray_height, base_m, feature.height_m(), 850.0);
        if intersection <= 0.0 {
            continue;
        }
        layer_risks.push(opacity * intersection * (0.50 + 0.50 * distance_factor));
    }
    let deep_risk = column.deep_cloud_risk * (0.35 + 0.35 * distance_factor);
    clamp(layer_risks.iter().copied().fold(0.0f64, f64::max) + deep_risk)
}

fn heuristic_lightpath_blocking_risk(
    features: &[Feature<'_>],
    samples: &[&CloudColumnOutput],
    solar_elevation_deg: f64,
) -> f64 {
    let mut risks: Vec<f64> = Vec::new();
    for column in samples {
        let required_height = crate::native::required_illumination_height_m(
            column.distance_km,
            solar_elevation_deg,
            0.0,
        );
        let low_block = column_blocker_opacity(features, column, "low")
            * clamp(1.0 - column.distance_km / 160.0);
        let mid_block = column_blocker_opacity(features, column, "mid")
            * clamp((column.distance_km - 25.0) / 160.0)
            * clamp((5200.0 - required_height) / 5200.0);
        let deep_block = column.deep_cloud_risk * clamp((column.distance_km - 40.0) / 220.0);
        risks.push(clamp(
            0.75 * low_block + 0.30 * mid_block + 0.45 * deep_block,
        ));
    }
    risks.iter().copied().fold(0.0f64, f64::max)
}

/// `lightpath_blocking_risk` (cross_section.py:282-319).
#[allow(clippy::too_many_arguments)]
fn lightpath_blocking_risk(
    features: &[Feature<'_>],
    samples: &[&CloudColumnOutput],
    solar_elevation_deg: f64,
    target_column: Option<&CloudColumnOutput>,
    observer_elevation_m: f64,
) -> f64 {
    let legacy_risk = heuristic_lightpath_blocking_risk(features, samples, solar_elevation_deg);
    if let Some(target_column) = target_column {
        if target_column_score(target_column, solar_elevation_deg) > 0.18 {
            let target_height_m = target_column.cloud_base_m;
            let mut risks = vec![
                0.45 * column_blocker_opacity(features, target_column, "low")
                    + 0.30 * target_column.deep_cloud_risk
                    + 0.18 * target_column.thickness_risk,
            ];
            for column in samples {
                if std::ptr::eq(*column, target_column) {
                    continue;
                }
                risks.push(column_3d_blocking_risk(
                    features,
                    column,
                    target_column.distance_km,
                    target_height_m,
                    solar_elevation_deg,
                    observer_elevation_m,
                ));
            }
            return clamp(
                risks
                    .iter()
                    .copied()
                    .fold(0.0f64, f64::max)
                    .max(legacy_risk),
            );
        }
    }
    legacy_risk
}

/// `nearest_column` (cross_section.py:252-279).
fn nearest_column<'a>(
    columns: &[&'a CloudColumnOutput],
    solar_azimuth_deg: f64,
    distance_km: f64,
) -> Option<&'a CloudColumnOutput> {
    let mut candidates: Vec<(f64, f64, f64, f64, usize)> = Vec::new();
    for (index, column) in columns.iter().enumerate() {
        let az_delta = angular_difference_deg(column.bearing_deg, solar_azimuth_deg);
        if az_delta >= 90.0 {
            continue;
        }
        let distance_delta = (column.distance_km - distance_km).abs();
        let cross_track_km = column.distance_km * az_delta.to_radians().sin();
        let corridor_half_width_km = (18.0f64).max(0.08 * distance_km);
        if cross_track_km.abs() > corridor_half_width_km || distance_delta > 42.0 {
            continue;
        }
        let proximity = clamp(1.0 - distance_delta / 42.0)
            * clamp(1.0 - cross_track_km.abs() / corridor_half_width_km);
        let cloud_relevance = column
            .high_presence
            .max(column.mid_presence)
            .max(column.low_presence);
        candidates.push((proximity, distance_delta, az_delta, cloud_relevance, index));
    }
    if candidates.is_empty() {
        return None;
    }
    // Python: sort(key=(proximity, -distance_delta, -az_delta, relevance), reverse=True)
    // — stable sort, first maximum wins per key tuple.
    candidates.sort_by(|a, b| {
        let key = |item: &(f64, f64, f64, f64, usize)| (item.0, -item.1, -item.2, item.3);
        key(b)
            .partial_cmp(&key(a))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    columns.get(candidates[0].4).copied()
}

/// `_profile_blocking_risk` (cross_section.py:207-249).
#[allow(clippy::too_many_arguments)]
fn profile_blocking_risk(
    features: &[Feature<'_>],
    column: &CloudColumnOutput,
    solar_elevation_deg: f64,
    target_column: Option<&CloudColumnOutput>,
    observer_elevation_m: f64,
) -> f64 {
    let required_height =
        crate::native::required_illumination_height_m(column.distance_km, solar_elevation_deg, 0.0);
    let low_block =
        column_blocker_opacity(features, column, "low") * clamp(1.0 - column.distance_km / 160.0);
    let mid_block = column_blocker_opacity(features, column, "mid")
        * clamp((column.distance_km - 25.0) / 160.0)
        * clamp((5200.0 - required_height) / 5200.0);
    let deep_block = column.deep_cloud_risk * clamp((column.distance_km - 40.0) / 220.0);
    let mut risk = clamp(0.75 * low_block + 0.30 * mid_block + 0.45 * deep_block);
    if let Some(target_column) = target_column {
        if target_column_score(target_column, solar_elevation_deg) > 0.18 {
            if std::ptr::eq(column, target_column) {
                risk = risk.max(clamp(
                    0.45 * column_blocker_opacity(features, column, "low")
                        + 0.30 * column.deep_cloud_risk
                        + 0.18 * column.thickness_risk,
                ));
            } else {
                risk = risk.max(column_3d_blocking_risk(
                    features,
                    column,
                    target_column.distance_km,
                    target_column.cloud_base_m,
                    solar_elevation_deg,
                    observer_elevation_m,
                ));
            }
        }
    }
    clamp(risk)
}

/// `_profile_sample` (cross_section.py:141-204).
#[allow(clippy::too_many_arguments)]
fn profile_sample(
    features: &[Feature<'_>],
    column: &CloudColumnOutput,
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
    target_column: Option<&CloudColumnOutput>,
    observer_elevation_m: f64,
) -> SolarPathProfileSampleOutput {
    let sector_delta = angular_difference_deg(column.bearing_deg, solar_azimuth_deg);
    let required_height = crate::native::required_illumination_height_m(
        column.distance_km,
        solar_elevation_deg,
        sector_delta,
    );
    let blocker_risk = profile_blocking_risk(
        features,
        column,
        solar_elevation_deg,
        target_column,
        observer_elevation_m,
    );
    let illumination_status = if column.cloud_top_m <= 0.0 {
        "clear"
    } else if column.cloud_top_m < required_height {
        "below_illumination_threshold"
    } else if column.cloud_base_m >= required_height {
        "sunlit_cloud"
    } else {
        "ray_intersects_cloud"
    };
    let mut ordered_layers: Vec<(i64, usize)> = column.layers.clone();
    ordered_layers.sort_by_key(|(pressure, _)| *pressure);
    let levels = ordered_layers
        .iter()
        .map(|(pressure, position)| {
            let feature = &features[*position];
            SolarPathProfileLevelOutput {
                pressure_hpa: *pressure,
                height_m: feature.height_m(),
                rh_percent: feature.rh_percent(),
                temperature_c: (!feature.row[7].is_nan()).then(|| feature.row[7] - 273.15),
                cloud_fraction_percent: optional(feature.row[11]),
                cloud_liquid_kg_kg: optional(feature.row[12]),
                cloud_ice_kg_kg: optional(feature.row[13]),
            }
        })
        .collect();
    SolarPathProfileSampleOutput {
        distance_km: column.distance_km,
        bearing_deg: column.bearing_deg,
        cloud_base_m: column.cloud_base_m,
        cloud_top_m: column.cloud_top_m,
        cloud_type: column.cloud_type.clone(),
        high_presence: column.high_presence,
        mid_presence: column.mid_presence,
        low_presence: column.low_presence,
        deep_cloud_risk: column.deep_cloud_risk,
        thickness_risk: column.thickness_risk,
        required_illumination_height_m: required_height,
        blocker_risk,
        lightpath_clearance_score: clamp(1.0 - blocker_risk),
        target_cloud_score: target_column_score(column, solar_elevation_deg),
        illumination_status: illumination_status.to_string(),
        levels,
    }
}

/// `diagnose_cross_section` (cross_section.py:34-118).
fn diagnose_cross_section(
    features: &[Feature<'_>],
    columns: &[(ColumnKey, CloudColumnOutput)],
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
    observer_elevation_m: f64,
) -> CrossSectionOutput {
    let all_columns: Vec<&CloudColumnOutput> = columns.iter().map(|(_, column)| column).collect();
    let profile_columns: Vec<&CloudColumnOutput> = all_columns
        .iter()
        .copied()
        .filter(|column| column.distance_km <= PROFILE_DISTANCES_KM[11] + 42.0)
        .collect();
    let mut samples: Vec<&CloudColumnOutput> = Vec::new();
    for distance_km in PROFILE_DISTANCES_KM {
        let column = nearest_column(&profile_columns, solar_azimuth_deg, distance_km);
        if let Some(column) = column {
            samples.push(column);
        }
    }
    if samples.is_empty() {
        return CrossSectionOutput {
            azimuth_deg: solar_azimuth_deg,
            samples_count: 0,
            lightpath_integrity_score: 0.0,
            path_extinction_score: 0.0,
            vertical_cloud_type: "unknown".to_string(),
            cloud_type_burnability_score: 0.0,
            deep_cloud_risk: 0.0,
            blocking_risk: 1.0,
            target_cloud_score: 0.0,
            summary: vec!["剖面诊断：太阳方向没有足够的模式网格样本。".to_string()],
            profile: Vec::new(),
        };
    }
    let extinction_score = path_extinction_score(features, &samples);
    let target_score = target_cloud_score(&samples, solar_elevation_deg);
    let deep_risk = samples
        .iter()
        .map(|column| column.deep_cloud_risk)
        .fold(0.0f64, f64::max);
    // Python max(key=...) keeps the first maximum in iteration order.
    let mut best_column = samples[0];
    let mut best_key = f64::NEG_INFINITY;
    for column in &samples {
        let key = target_column_score(column, solar_elevation_deg);
        if key > best_key {
            best_key = key;
            best_column = column;
        }
    }
    let blocking_risk = lightpath_blocking_risk(
        features,
        &samples,
        solar_elevation_deg,
        Some(best_column),
        observer_elevation_m,
    );
    let vertical_type = best_column.cloud_type.clone();
    let burnability = best_column.burnability_score;
    let integrity = clamp(1.0 - 0.62 * blocking_risk - 0.28 * deep_risk + 0.16 * target_score);
    let mut summary = vec![
        format!(
            "剖面诊断：太阳方位 {:.0}°，样本 {} 个，主导云型 {}。",
            solar_azimuth_deg,
            samples.len(),
            vertical_type
        ),
        format!(
            "光路完整度 {:.0}/100，路径通透度 {:.0}/100，深厚云系风险 {:.0}/100。",
            integrity * 100.0,
            extinction_score * 100.0,
            deep_risk * 100.0
        ),
        "实况剖面同时保留各距离云柱的云底、云顶、压力层云量代理和太阳入射阈值。".to_string(),
    ];
    if blocking_risk > 0.55 {
        summary.push("太阳方向低层或中低层遮挡云偏强，光路可能被削弱。".to_string());
    } else if target_score > 0.55 {
        summary.push("太阳方向存在可受光中高云目标。".to_string());
    }
    if burnability < 0.45 {
        summary.push("剖面主导云型可烧性偏低，可能偏厚云或低云。".to_string());
    }
    let profile = samples
        .iter()
        .map(|column| {
            profile_sample(
                features,
                column,
                solar_elevation_deg,
                solar_azimuth_deg,
                Some(best_column),
                observer_elevation_m,
            )
        })
        .collect();
    CrossSectionOutput {
        azimuth_deg: solar_azimuth_deg,
        samples_count: samples.len(),
        lightpath_integrity_score: integrity,
        path_extinction_score: extinction_score,
        vertical_cloud_type: vertical_type,
        cloud_type_burnability_score: burnability,
        deep_cloud_risk: deep_risk,
        blocking_risk: blocking_risk,
        target_cloud_score: target_score,
        summary,
        profile,
    }
}

// ---------------------------------------------------------------------------
// assess_timestep tail: extent, coverage, regime, color, uncertainty, reasons.
// ---------------------------------------------------------------------------

/// Python `f"{value:.{digits}e}"`: two-digit signed exponent, half-even
/// mantissa (Rust's `{:.prec$e}` rounds the mantissa identically).
fn py_format_e(value: f64, digits: usize) -> String {
    let rendered = format!("{:.*e}", digits, value);
    match rendered.split_once('e') {
        Some((mantissa, exponent)) => {
            let sign = if exponent.starts_with('-') { '-' } else { '+' };
            let digits_text = exponent.trim_start_matches(['+', '-']);
            format!("{mantissa}e{sign}{digits_text:0>2}")
        }
        None => rendered,
    }
}

/// `numpy.median` of a non-empty float list (average of the two middle values
/// for even lengths).
fn np_median(values: &mut [f64]) -> f64 {
    values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let count = values.len();
    if count % 2 == 1 {
        values[count / 2]
    } else {
        (values[count / 2 - 1] + values[count / 2]) / 2.0
    }
}

/// `numpy.linspace(start, stop, num)` with the endpoint pinned to `stop`.
fn np_linspace(start: f64, stop: f64, num: usize) -> Vec<f64> {
    if num == 0 {
        return Vec::new();
    }
    if num == 1 {
        return vec![start];
    }
    let step = (stop - start) / (num - 1) as f64;
    let mut values: Vec<f64> = (0..num).map(|index| start + step * index as f64).collect();
    values[num - 1] = stop;
    values
}

/// `numpy.sum` of a float64 slice: pairwise summation (8-way unrolled blocks
/// of 128, recursive split above), matching `loops_utils.h` pairwise_sum.
fn numpy_pairwise_sum(values: &[f64]) -> f64 {
    let count = values.len();
    if count <= 8 {
        let mut sum = 0.0f64;
        for value in values {
            sum += value;
        }
        return sum;
    }
    if count <= 128 {
        let mut partial = [0.0f64; 8];
        let mut index = 0;
        while index + 8 <= count {
            for lane in 0..8 {
                partial[lane] += values[index + lane];
            }
            index += 8;
        }
        let mut remainder = ((partial[0] + partial[1]) + (partial[2] + partial[3]))
            + ((partial[4] + partial[5]) + (partial[6] + partial[7]));
        for value in &values[index..] {
            remainder += value;
        }
        return remainder;
    }
    let mut split = count / 2;
    split -= split % 8;
    numpy_pairwise_sum(&values[..split]) + numpy_pairwise_sum(&values[split..])
}

fn soft_knee(value: f64) -> f64 {
    let value = clamp(value);
    let threshold = 0.18;
    let knee_width = 0.22;
    let low = threshold - knee_width * 0.5;
    let high = threshold + knee_width * 0.5;
    if value <= low {
        return 0.30 * value;
    }
    if value >= high {
        return value;
    }
    let t = (value - low) / knee_width;
    0.30 * low + (high - 0.30 * low) * (t * t * (3.0 - 2.0 * t))
}

/// `quality_core.duration_quality_factor` (quality_core.py:12-21).
fn quality_duration_factor(duration_minutes: f64) -> f64 {
    let duration = duration_minutes.max(0.0);
    if duration < 2.0 {
        return 0.50 + 0.25 * duration / 2.0;
    }
    if duration < 5.0 {
        return 0.75 + 0.15 * (duration - 2.0) / 3.0;
    }
    if duration < 12.0 {
        return 0.90 + 0.10 * (duration - 5.0) / 7.0;
    }
    py_clamp(1.0 + 0.08 * (duration - 12.0) / 12.0, 0.0, 1.12)
}

const LEGACY_LATITUDE_LINE_SPEED_KM_PER_MIN: f64 = 22.0;

/// `solar_elevation_weight` (scoring.py:1004-1011).
fn solar_elevation_weight(solar_elevation_deg: f64) -> f64 {
    if solar_elevation_deg >= 8.0 {
        clamp(1.0 - (solar_elevation_deg - 8.0) / 8.0)
    } else if solar_elevation_deg >= -8.0 {
        1.0
    } else if solar_elevation_deg >= -14.0 {
        clamp((solar_elevation_deg + 14.0) / 6.0)
    } else {
        0.0
    }
}

/// `visibility_gate` (scoring.py:1014-1016).
fn visibility_gate(visibility_score: f64) -> f64 {
    clamp((visibility_score - 0.15) / 0.55)
}
const BURNABLE_CELL_SCORE: f64 = 0.25;
const STRONG_CELL_SCORE: f64 = 0.45;

/// `estimate_legacy_glow_duration_minutes` (scoring.py:2368-2385).
fn estimate_legacy_glow_duration_minutes(
    geometric_reach_km: f64,
    edge_distance_km: f64,
    latitude_deg: f64,
) -> f64 {
    let speed = LEGACY_LATITUDE_LINE_SPEED_KM_PER_MIN * (latitude_deg.abs()).to_radians().cos();
    if speed <= 0.0 || geometric_reach_km <= 0.0 {
        return 0.0;
    }
    let effective_reach = if edge_distance_km > 0.0 {
        geometric_reach_km.min(edge_distance_km)
    } else {
        geometric_reach_km
    };
    2.0 * effective_reach / speed
}

fn layer_label(layer: &str) -> String {
    match layer {
        "high" => "高云".to_string(),
        "mid" => "中云".to_string(),
        "low" => "低云".to_string(),
        other => other.to_string(),
    }
}

/// `spatial_bin_scores` / `strong_spatial_bins` / `extent_score`
/// (scoring.py:3501-3542). Bins keep first-encounter order because the
/// reference sums Python dict values in insertion order.
struct ExtentOutcome {
    score: f64,
    burning_cloud_count: i64,
    strong_burning_cloud_count: i64,
}

#[allow(clippy::type_complexity)]
fn extent_score(signals: &[CandidateSignalOutput]) -> ExtentOutcome {
    let mut burnable_cells: Vec<((i64, i64), f64)> = Vec::new();
    let mut burnable_index: HashMap<(i64, i64), usize> = HashMap::new();
    let mut strong_cells: std::collections::BTreeSet<(i64, i64)> =
        std::collections::BTreeSet::new();
    for signal in signals {
        let cell = (
            (signal.bearing_deg / 10.0).floor() as i64,
            (signal.distance_km / 45.0).floor() as i64,
        );
        if signal.score >= BURNABLE_CELL_SCORE {
            match burnable_index.get(&cell) {
                Some(&position) => {
                    burnable_cells[position].1 = burnable_cells[position].1.max(signal.score);
                }
                None => {
                    burnable_index.insert(cell, burnable_cells.len());
                    burnable_cells.push((cell, signal.score));
                }
            }
        }
        if signal.score >= STRONG_CELL_SCORE {
            strong_cells.insert(cell);
        }
    }
    let mut azimuth_weights: Vec<(i64, f64)> = Vec::new();
    let mut azimuth_index: HashMap<i64, usize> = HashMap::new();
    let mut distance_weights: Vec<(i64, f64)> = Vec::new();
    let mut distance_index: HashMap<i64, usize> = HashMap::new();
    let mut weighted_cells = 0.0f64;
    for ((az_bin, dist_bin), score) in &burnable_cells {
        let weight =
            clamp((score - BURNABLE_CELL_SCORE) / (STRONG_CELL_SCORE - BURNABLE_CELL_SCORE));
        weighted_cells += weight;
        match azimuth_index.get(az_bin) {
            Some(&position) => {
                azimuth_weights[position].1 = azimuth_weights[position].1.max(weight)
            }
            None => {
                azimuth_index.insert(*az_bin, azimuth_weights.len());
                azimuth_weights.push((*az_bin, weight));
            }
        }
        let distance_bin = ((*dist_bin as f64) * 45.0 / 90.0).floor() as i64;
        match distance_index.get(&distance_bin) {
            Some(&position) => {
                distance_weights[position].1 = distance_weights[position].1.max(weight)
            }
            None => {
                distance_index.insert(distance_bin, distance_weights.len());
                distance_weights.push((distance_bin, weight));
            }
        }
    }
    let cell_score = clamp(weighted_cells / 18.0);
    let azimuth_score = clamp(
        azimuth_weights
            .iter()
            .map(|(_, weight)| weight)
            .fold(0.0f64, |acc, value| acc + value)
            / 6.0,
    );
    let distance_score = clamp(
        distance_weights
            .iter()
            .map(|(_, weight)| weight)
            .fold(0.0f64, |acc, value| acc + value)
            / 4.0,
    );
    let score = clamp(0.56 * cell_score + 0.28 * azimuth_score + 0.16 * distance_score);
    ExtentOutcome {
        score,
        burning_cloud_count: burnable_cells.len() as i64,
        strong_burning_cloud_count: strong_cells.len() as i64,
    }
}

/// `vividness_score` (scoring.py:3716-3726).
fn vividness_score(signals: &[CandidateSignalOutput], extent: f64) -> f64 {
    if signals.is_empty() {
        return 0.0;
    }
    let mut strong: Vec<f64> = signals
        .iter()
        .filter(|signal| signal.score >= 0.58)
        .map(|signal| signal.score)
        .collect();
    if strong.is_empty() {
        strong.push(signals[0].score);
    }
    let core_strength = strong.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let mean_strength = statistics_mean(&strong[..strong.len().min(12)]);
    clamp(0.52 * core_strength + 0.30 * mean_strength + 0.18 * extent)
}

/// `candidate_angular_cloud_coverage` (scoring.py:3545-3690).
#[allow(clippy::type_complexity)]
fn candidate_angular_cloud_coverage(
    rows: &[AngularCoverageRow],
    solar_azimuth_deg: f64,
    observer_elevation_m: f64,
) -> f64 {
    if rows.is_empty() {
        return 0.0;
    }
    let mut latitudes: Vec<f64> = rows.iter().map(|row| python_round(row.lat, 6)).collect();
    latitudes.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    latitudes.dedup_by(|a, b| *a == *b);
    let mut longitudes: Vec<f64> = rows.iter().map(|row| python_round(row.lon, 6)).collect();
    longitudes.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    longitudes.dedup_by(|a, b| *a == *b);
    let mut spacings: Vec<f64> = Vec::new();
    for values in [&latitudes, &longitudes] {
        for pair in values.windows(2) {
            let gap = pair[1] - pair[0];
            if gap > 1e-5 {
                spacings.push(gap);
            }
        }
    }
    let spacing_deg = if spacings.is_empty() {
        0.25
    } else {
        np_median(&mut spacings)
    };
    let spacing_deg = py_clamp(spacing_deg, 0.02, 1.0);

    // One vertical cloud body per horizontal cell.
    let mut columns: Vec<AngularCoverageRow> = Vec::new();
    let mut column_index: HashMap<(u64, u64), usize> = HashMap::new();
    for row in rows {
        let key = (
            python_round(row.lat, 5).to_bits(),
            python_round(row.lon, 5).to_bits(),
        );
        match column_index.get(&key) {
            Some(&position) => {
                let stored = &mut columns[position];
                stored.cloud_base_m = stored.cloud_base_m.min(row.cloud_base_m);
                stored.cloud_height_m = stored.cloud_height_m.max(row.cloud_height_m);
                stored.fraction = stored.fraction.max(row.fraction);
            }
            None => {
                column_index.insert(key, columns.len());
                columns.push(row.clone());
            }
        }
    }
    let mut opacity = vec![0.0f64; 24 * 60];
    for row in &columns {
        let center = (row.bearing_deg - solar_azimuth_deg + 180.0).rem_euclid(360.0) - 180.0;
        let latitude = row.lat;
        let distance_km = row.distance_km;
        let has_explicit_footprint = row.footprint_width.is_some()
            && row.footprint_near.is_some()
            && row.footprint_far.is_some();
        let local_radius_km = 0.5
            * spacing_deg
            * (110.574 * 111.320 * (latitude.to_radians().cos()).abs().max(0.2)).sqrt();
        let contains_observer = if has_explicit_footprint {
            row.footprint_near.unwrap() <= 0.0
        } else {
            distance_km <= local_radius_km
        };
        let (azimuth_start, azimuth_end);
        if contains_observer {
            azimuth_start = -60.0;
            azimuth_end = 60.0;
        } else if has_explicit_footprint {
            let half_width = 0.5 * row.footprint_width.unwrap();
            azimuth_start = (center - half_width).max(-60.0);
            azimuth_end = (center + half_width).min(60.0);
        } else {
            let cell_width_km = spacing_deg
                * (110.574f64.powi(2) + (111.320 * latitude.to_radians().cos()).powi(2)).sqrt();
            let half_width = (0.5 * cell_width_km / distance_km.max(1.0))
                .atan()
                .to_degrees();
            azimuth_start = (center - half_width).max(-60.0);
            azimuth_end = (center + half_width).min(60.0);
        }
        if azimuth_end <= azimuth_start {
            continue;
        }
        let base_distance_km = if has_explicit_footprint {
            row.footprint_far.unwrap().max(1.0)
        } else if contains_observer {
            local_radius_km.max(1.0)
        } else {
            distance_km
        };
        let top_distance_km = if has_explicit_footprint {
            row.footprint_near.unwrap().max(1.0)
        } else {
            distance_km
        };
        let base = py_clamp(
            apparent_cloud_elevation_deg(base_distance_km, row.cloud_base_m, observer_elevation_m),
            0.0,
            60.0,
        );
        let top = if contains_observer {
            60.0
        } else {
            py_clamp(
                apparent_cloud_elevation_deg(
                    top_distance_km,
                    row.cloud_height_m,
                    observer_elevation_m,
                ),
                0.0,
                60.0,
            )
        };
        if top <= base {
            continue;
        }
        let az_start = (((azimuth_start + 60.0) / 5.0).floor() as i64).clamp(0, 24);
        let az_end = (((azimuth_end + 60.0) / 5.0).ceil() as i64).clamp(0, 24);
        let elevation_start = (base.floor() as i64).clamp(0, 60);
        let elevation_end = (top.ceil() as i64).clamp(0, 60);
        for azimuth_bin in az_start..az_end {
            let bin_start = -60.0 + azimuth_bin as f64 * 5.0;
            let azimuth_overlap =
                clamp((azimuth_end.min(bin_start + 5.0) - azimuth_start.max(bin_start)) / 5.0);
            for elevation_bin in elevation_start..elevation_end {
                let elevation_overlap =
                    clamp(top.min(elevation_bin as f64 + 1.0) - base.max(elevation_bin as f64));
                let added = row.fraction * azimuth_overlap * elevation_overlap;
                let cell = (azimuth_bin * 60 + elevation_bin) as usize;
                opacity[cell] = 1.0 - (1.0 - opacity[cell]) * (1.0 - added);
            }
        }
    }
    let weights: Vec<f64> = (0..61).map(|index| (index as f64).to_radians()).collect();
    let mut products = vec![0.0f64; 24 * 60];
    for (cell, value) in products.iter_mut().enumerate() {
        let azimuth_bin = cell / 60;
        let elevation_bin = cell % 60;
        *value = opacity[cell] * (weights[elevation_bin + 1].sin() - weights[elevation_bin].sin());
        let _ = azimuth_bin;
    }
    let occupied = numpy_pairwise_sum(&products);
    let visible = 24.0 * 60.0f64.to_radians().sin();
    clamp(occupied / visible.max(1e-9))
}

/// One projection row of `candidate_angular_cloud_coverage`.
#[derive(Clone)]
struct AngularCoverageRow {
    lat: f64,
    lon: f64,
    distance_km: f64,
    bearing_deg: f64,
    cloud_base_m: f64,
    cloud_height_m: f64,
    fraction: f64,
    footprint_width: Option<f64>,
    footprint_near: Option<f64>,
    footprint_far: Option<f64>,
}

/// `cloud_column_angular_cloud_coverage` (scoring.py:3693-3735).
fn cloud_column_angular_cloud_coverage(
    columns: &[(ColumnKey, CloudColumnOutput)],
    solar_azimuth_deg: f64,
    observer_elevation_m: f64,
) -> f64 {
    let rows: Vec<AngularCoverageRow> = columns
        .iter()
        .map(|(_, column)| {
            let probability = column.horizontal_cloud_probability.unwrap_or_else(|| {
                column
                    .high_presence
                    .max(column.mid_presence)
                    .max(column.low_presence)
                    .max(column.explicit_cloud_evidence)
            });
            let probability = clamp(probability);
            AngularCoverageRow {
                lat: column.lat,
                lon: column.lon,
                distance_km: column.distance_km,
                bearing_deg: column.bearing_deg,
                cloud_base_m: column.cloud_base_m,
                cloud_height_m: column.cloud_top_m,
                fraction: probability,
                footprint_width: column.footprint_azimuth_width_deg,
                footprint_near: column.footprint_near_distance_km,
                footprint_far: column.footprint_far_distance_km,
            }
        })
        .filter(|row| row.fraction > 0.0)
        .collect();
    candidate_angular_cloud_coverage(&rows, solar_azimuth_deg, observer_elevation_m)
}

/// `candidate_angular_cloud_coverage` over the candidate signals.
fn candidate_signal_angular_coverage(
    signals: &[CandidateSignalOutput],
    solar_azimuth_deg: f64,
    observer_elevation_m: f64,
) -> f64 {
    let rows: Vec<AngularCoverageRow> = signals
        .iter()
        .map(|signal| AngularCoverageRow {
            lat: signal.lat,
            lon: signal.lon,
            distance_km: signal.distance_km,
            bearing_deg: signal.bearing_deg,
            cloud_base_m: signal.cloud_base_m,
            cloud_height_m: signal.cloud_height_m,
            fraction: match signal.horizontal_cloud_probability {
                Some(probability) => clamp(probability),
                None => match signal.cloud_fraction_percent {
                    Some(fraction) => clamp(fraction / 100.0),
                    None => clamp(signal.cloud_evidence_score),
                },
            },
            footprint_width: signal.footprint_azimuth_width_deg,
            footprint_near: signal.footprint_near_distance_km,
            footprint_far: signal.footprint_far_distance_km,
        })
        .collect();
    candidate_angular_cloud_coverage(&rows, solar_azimuth_deg, observer_elevation_m)
}

/// `observer_local_cloud_field_amount_from_columns` (scoring.py:3699-3713).
fn observer_local_cloud_field_amount_from_columns(
    columns: &[(ColumnKey, CloudColumnOutput)],
) -> f64 {
    let mut local_probabilities: Vec<f64> = Vec::new();
    for (_, column) in columns {
        let contains_observer = (column.footprint_near_distance_km.is_some()
            && column.footprint_near_distance_km.unwrap() <= 0.0)
            || (column.footprint_near_distance_km.is_none() && column.distance_km <= 1.0);
        if !contains_observer {
            continue;
        }
        let probability = column.horizontal_cloud_probability.unwrap_or_else(|| {
            column
                .high_presence
                .max(column.mid_presence)
                .max(column.low_presence)
                .max(column.explicit_cloud_evidence)
        });
        local_probabilities.push(clamp(probability));
    }
    local_probabilities
        .iter()
        .copied()
        .fold(f64::NEG_INFINITY, f64::max)
        .max(0.0)
}

// ---------------------------------------------------------------------------
// Scene / phase / color diagnoses (forecast_characteristics.py).
// ---------------------------------------------------------------------------

/// `scene_diagnosis` (forecast_characteristics.py:30-63).
#[derive(Serialize, Clone)]
pub struct SceneDiagnosisOutput {
    selected_regime: String,
    scene_scores: Vec<(String, f64)>,
    local_sky_score: f64,
    remote_sky_score: f64,
    convective_risk: f64,
}

fn scene_diagnosis(
    local_deck: f64,
    distant_target: f64,
    remote_boundary: f64,
    cloud_hole: f64,
    convective_risk: f64,
) -> SceneDiagnosisOutput {
    // Insertion order matters: Python max() keeps the first maximum.
    let mut scores: Vec<(String, f64)> = vec![
        ("local_deck".to_string(), clamp(local_deck)),
        ("distant_target".to_string(), clamp(distant_target)),
        ("remote_boundary".to_string(), clamp(remote_boundary)),
        ("cloud_hole".to_string(), clamp(cloud_hole)),
        ("convective".to_string(), clamp(convective_risk)),
    ];
    let mut selected_regime = String::new();
    let mut positive_score = f64::NAN;
    for (key, value) in scores.iter() {
        if key == "convective" {
            continue;
        }
        if selected_regime.is_empty() || *value > positive_score {
            selected_regime = key.clone();
            positive_score = *value;
        }
    }
    if positive_score < 0.08 {
        if scores[4].1 < 0.35 {
            selected_regime = "clear".to_string();
            scores.push((
                "clear".to_string(),
                clamp(1.0 - positive_score.max(scores[4].1)),
            ));
        } else {
            selected_regime = "convective".to_string();
        }
    } else if scores[4].1 >= 0.62 && scores[4].1 >= positive_score {
        selected_regime = "convective".to_string();
    }
    let local_sky = scores[0].1;
    let remote_score = scores[1].1.max(scores[2].1).max(scores[3].1);
    let convective = scores[4].1;
    SceneDiagnosisOutput {
        selected_regime,
        scene_scores: scores,
        local_sky_score: local_sky,
        remote_sky_score: remote_score,
        convective_risk: convective,
    }
}

/// `cloud_phase_diagnosis` (forecast_characteristics.py:70-90).
#[derive(Serialize, Clone)]
pub struct CloudPhaseOutput {
    label: String,
    ice_fraction: Option<f64>,
    confidence: f64,
}

fn cloud_phase_diagnosis(
    cloud_ice_kg_kg: Option<f64>,
    cloud_liquid_kg_kg: Option<f64>,
) -> CloudPhaseOutput {
    let ice = cloud_ice_kg_kg.unwrap_or(0.0).max(0.0);
    let liquid = cloud_liquid_kg_kg.unwrap_or(0.0).max(0.0);
    let total = ice + liquid;
    if total <= 1e-10 {
        return CloudPhaseOutput {
            label: "unknown".to_string(),
            ice_fraction: None,
            confidence: 0.0,
        };
    }
    let ice_fraction = ice / total;
    let label = if ice_fraction >= 0.72 {
        "ice"
    } else if ice_fraction <= 0.28 {
        "water"
    } else {
        "mixed"
    };
    CloudPhaseOutput {
        label: label.to_string(),
        ice_fraction: Some(ice_fraction),
        confidence: clamp((ice_fraction - 0.5).abs() * 1.7 + 0.25),
    }
}

/// `color_diagnosis` (forecast_characteristics.py:244-283).
#[derive(Serialize, Clone)]
pub struct ColorDiagnosisOutput {
    #[serde(rename = "type")]
    color_type: String,
    confidence: f64,
    cloud_phase: String,
    source: &'static str,
    inputs: serde_json::Value,
}

fn color_diagnosis(
    burn_score: f64,
    vividness: f64,
    solar_elevation_deg: f64,
    aod550: f64,
    solar_incoming_illumination_score: f64,
    deep_cloud_risk: f64,
    cloud_phase: &str,
) -> ColorDiagnosisOutput {
    let strength = clamp(burn_score);
    let vividness = clamp(vividness);
    let transmission = clamp(solar_incoming_illumination_score);
    let deep = clamp(deep_cloud_risk);
    let aod = aod550.max(0.0);
    let color_type = if strength < 0.08 || transmission < 0.16 || deep > 0.66 {
        "muted_glow"
    } else if solar_elevation_deg > -1.6 && aod < 0.32 {
        "golden"
    } else if solar_elevation_deg > -3.8 || cloud_phase == "water" {
        "orange_red"
    } else {
        "deep_red"
    };
    let confidence =
        clamp(0.30 * strength + 0.30 * vividness + 0.25 * transmission + 0.15 * (1.0 - deep));
    ColorDiagnosisOutput {
        color_type: color_type.to_string(),
        confidence,
        cloud_phase: cloud_phase.to_string(),
        source: "instant-quality+solar-elevation+aod+solar-incoming-illumination+cloud-phase",
        inputs: serde_json::json!({
            "quality_strength": strength,
            "brightness_color": vividness,
            "solar_elevation_deg": solar_elevation_deg,
            "aod550": aod,
            "solar_incoming_illumination_score": transmission,
            "deep_cloud_risk": deep,
            "cloud_phase": cloud_phase,
        }),
    }
}

// ---------------------------------------------------------------------------
// assemble_step: the assess_timestep tail (scoring.py:3855-4284).
// ---------------------------------------------------------------------------

#[derive(Serialize, Clone)]
pub struct BoundaryUncertaintyOutput {
    regime: String,
    source: String,
    nominal_distance_km: f64,
    lower_distance_km: f64,
    upper_distance_km: f64,
    width_km: f64,
    score_min: f64,
    score_max: f64,
    event_threshold: f64,
    classification_stable: bool,
    nearest_flip_displacement_km: Option<f64>,
}

#[derive(Serialize, Clone)]
pub struct MapCloudCellOutput {
    lat: f64,
    lon: f64,
    distance_km: f64,
    bearing_deg: f64,
    high_cloud: f64,
    mid_cloud: f64,
    low_cloud: f64,
    cloud_evidence: f64,
    cloud_type: String,
    cloud_base_m: f64,
    cloud_top_m: f64,
    deep_cloud_risk: f64,
    burn_score: f64,
    illumination_score: f64,
    path_clearance_score: f64,
    aerosol_transmission_score: f64,
    blocking_risk: f64,
    atmospheric_clarity_score: f64,
}

#[derive(Serialize, Clone)]
pub struct AssessmentStepOutput {
    forecast_hour: i64,
    valid_time_utc: String,
    valid_time_local: String,
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
    burn_score: f64,
    intensity_score: f64,
    extent_score: f64,
    vividness_score: f64,
    visibility_score: f64,
    low_cloud_penalty: f64,
    candidate_count: usize,
    burning_cloud_count: i64,
    top_candidate: Option<CandidateSignalOutput>,
    strong_burning_cloud_count: i64,
    top_boundary: Option<BoundarySignalOutput>,
    boundary_score: f64,
    boundary_uncertainty: Option<BoundaryUncertaintyOutput>,
    top_cloud_hole: Option<CloudHoleSignalOutput>,
    cloud_hole_score: f64,
    cross_section: CrossSectionOutput,
    solar_inflow_blockage: Option<SolarInflowBlockageOutput>,
    selected_regime: String,
    scene_scores: Vec<(String, f64)>,
    local_sky_score: f64,
    remote_sky_score: f64,
    convective_risk: f64,
    color_diagnosis: serde_json::Value,
    reasons: Vec<String>,
    map_cells: Vec<MapCloudCellOutput>,
    geometric_cloud_coverage: f64,
    structure_gate_score: f64,
    satellite_retrieval_material_evidence: Option<f64>,
    selected_observer_path_aod550: Option<f64>,
    selected_inflow_path_aod550: Option<f64>,
    selected_aerosol_path_variability_score: f64,
    forward_view_cloud_field_amount: f64,
    forward_burnable_cloud_field_amount: f64,
    observer_local_cloud_field_amount: f64,
    observation_burnable: bool,
    observation_score_ceiling: Option<f64>,
    surface_weather_penalty: SurfaceWeatherPenaltyOutput,
    horizon_gap_score: f64,
    overcast_shadow_factor: f64,
    // Kept alongside (not TimeStepAssessment fields) for the differential.
    columns: Vec<CloudColumnOutput>,
    candidate_signals: Vec<CandidateSignalOutput>,
    local_deck_signals: Vec<BoundarySignalOutput>,
    remote_boundary_signals: Vec<BoundarySignalOutput>,
    boundary_signals: Vec<BoundarySignalOutput>,
    cloud_hole_signals: Vec<CloudHoleSignalOutput>,
}

#[allow(clippy::too_many_arguments)]
#[allow(clippy::too_many_arguments)]
fn assemble_step(
    features: &[Feature<'_>],
    columns: &[(ColumnKey, CloudColumnOutput)],
    signals: &[CandidateSignalOutput],
    local_deck_signals: &[BoundarySignalOutput],
    remote_boundary_signals: &[BoundarySignalOutput],
    boundary_signals: &[BoundarySignalOutput],
    cloud_hole_signals: &[CloudHoleSignalOutput],
    cross_section: &CrossSectionOutput,
    solar_inflow_blockage: Option<&SolarInflowBlockageOutput>,
    low_cloud_penalty: f64,
    weather: &SurfaceWeatherPenaltyOutput,
    scan: &ScanInput,
    observer_elevation_m: f64,
    include_map_cells: bool,
    map_radius_km: f64,
    aod550: f64,
    aerosol_quality: &str,
    valid_time_local: &str,
    full_detail: bool,
) -> AssessmentStepOutput {
    let top = signals.first().cloned();
    let step_horizon_disc = if let Some(signal) = &top {
        HorizonIlluminationDiscrimination {
            horizon_gap_score: signal.horizon_gap_score,
            overcast_shadow_factor: signal.overcast_shadow_factor,
        }
    } else if scan.solar_elevation_deg < 0.0 {
        let ref_height = crate::native::required_illumination_height_m(
            0.0,
            scan.solar_elevation_deg,
            0.0,
        )
        .max(1500.0);
        assess_horizon_illumination_discrimination(
            features,
            columns,
            0.0,
            ref_height,
            ref_height,
            scan.solar_elevation_deg,
            scan.solar_azimuth_deg,
        )
    } else {
        HorizonIlluminationDiscrimination {
            horizon_gap_score: 1.0,
            overcast_shadow_factor: 1.0,
        }
    };
    let top_boundary = boundary_signals.first().cloned();
    let top_local_deck = local_deck_signals.first().cloned();
    let top_remote_boundary = remote_boundary_signals.first().cloned();
    let top_cloud_hole = cloud_hole_signals.first().cloned();
    let weather_penalty = weather.penalty;
    let surface_visibility_m = weather.visibility_m;
    let precip_rate = weather.precip_rate_kg_m2_s;
    let low_penalty = clamp(1.0 - (1.0 - low_cloud_penalty) * (1.0 - weather_penalty));
    let signal_strength = top.as_ref().map_or(0.0, |signal| signal.score);
    let visibility = clamp(1.0 - low_penalty);
    let extent = extent_score(signals);
    let burning_cloud_count = extent.burning_cloud_count;
    let strong_burning_cloud_count = extent.strong_burning_cloud_count;
    let forward_view_cloud_field_amount =
        cloud_column_angular_cloud_coverage(columns, scan.solar_azimuth_deg, observer_elevation_m);
    let forward_burnable_cloud_field_amount =
        candidate_signal_angular_coverage(signals, scan.solar_azimuth_deg, observer_elevation_m);
    let observer_local_cloud_field_amount = observer_local_cloud_field_amount_from_columns(columns);
    let raw_extent = forward_burnable_cloud_field_amount;
    let mut edge_structure_gate = 1.0;
    if extent.score >= 0.45 {
        edge_structure_gate = match &top_boundary {
            None => 0.55,
            Some(boundary) => clamp(0.55 + 0.45 * boundary.edge_contrast_score),
        };
    }
    let mut structured_extent = raw_extent;
    let mut vividness = vividness_score(signals, structured_extent);
    let solar_weight = solar_elevation_weight(scan.solar_elevation_deg);
    let ground_visibility = visibility_gate(visibility);
    let mut visible_extent = clamp(structured_extent * ground_visibility);
    let boundary_component = top_boundary.as_ref().map_or(0.0, |signal| signal.score);
    let local_deck_component = top_local_deck.as_ref().map_or(0.0, |signal| signal.score);
    let remote_boundary_component = top_remote_boundary
        .as_ref()
        .map_or(0.0, |signal| signal.score);
    let cloud_hole_component = top_cloud_hole.as_ref().map_or(0.0, |signal| signal.score);
    let profile_integrity = cross_section.lightpath_integrity_score;
    let profile_factor = if cross_section.samples_count == 0 {
        // scoring.py:3895-3900 — missing samples stay neutral, never a veto.
        0.70
    } else {
        clamp((profile_integrity - 0.28) / 0.72)
    };
    let remote_candidates: [(&str, f64); 3] = [
        ("distant_target", signal_strength),
        ("remote_boundary", remote_boundary_component),
        ("cloud_hole", cloud_hole_component),
    ];
    let mut remote_regime = remote_candidates[0].0;
    let mut remote_physical_score = remote_candidates[0].1;
    for (name, value) in &remote_candidates[1..] {
        if *value > remote_physical_score {
            remote_regime = name;
            remote_physical_score = *value;
        }
    }
    let local_regime_wins = local_deck_component > remote_physical_score;
    let selected_regime_scoring = if local_regime_wins {
        "local_deck".to_string()
    } else {
        remote_regime.to_string()
    };
    let physical_existence_score = local_deck_component.max(remote_physical_score);

    let regime_visibility_gate;
    let mut regime_profile_factor;
    if local_regime_wins && top_local_deck.is_some() {
        let top_deck = top_local_deck.as_ref().unwrap();
        let weather_visibility = clamp(1.0 - weather_penalty);
        regime_visibility_gate = visibility_gate(weather_visibility);
        let local_geometric_extent = clamp(
            top_deck.local_cloud_presence_score * (0.65 + 0.35 * top_deck.edge_contrast_score),
        );
        structured_extent = structured_extent.max(local_geometric_extent);
        visible_extent = visible_extent.max(local_geometric_extent * regime_visibility_gate);
        let local_vividness =
            clamp(0.65 * top_deck.local_cloud_presence_score + 0.35 * top_deck.edge_contrast_score);
        vividness = vividness.max(local_vividness);
        regime_profile_factor = 1.0;
    } else {
        regime_visibility_gate = ground_visibility;
        regime_profile_factor = profile_factor;
        if let Some(top) = &top {
            regime_profile_factor = regime_profile_factor.min(top.path_clearance_score);
        }
    }

    let target_deep_cloud_risk = if local_regime_wins && top_local_deck.is_some() {
        top_local_deck.as_ref().unwrap().deep_cloud_risk
    } else if selected_regime_scoring == "remote_boundary" && top_remote_boundary.is_some() {
        top_remote_boundary.as_ref().unwrap().deep_cloud_risk
    } else {
        top.as_ref().map_or(0.0, |signal| signal.deep_cloud_risk)
    };
    let deep_cloud_gate = 1.0 - smooth_gate(target_deep_cloud_risk, 0.32, 0.62);
    let structure_gate_score = edge_structure_gate.min(deep_cloud_gate);
    regime_profile_factor = regime_profile_factor.min(structure_gate_score);

    let observer_lat = scan
        .observer_lat
        .or_else(|| features.first().map(|feature| feature.lat()))
        .unwrap_or(30.0);
    let mut selected_aod_observer = aod550.max(0.0);
    let mut selected_aod_inflow = selected_aod_observer;
    let mut selected_observer_path_aod550: Option<f64> = None;
    let mut selected_inflow_path_aod550: Option<f64> = None;
    let mut selected_aerosol_path_variability_score = 0.0f64;
    let mut selected_cloud_base_m = 0.0f64;
    let mut selected_distance_km = 0.0f64;
    let mut selected_path_transmission = 1.0f64;
    if local_regime_wins && top_local_deck.is_some() {
        let deck = top_local_deck.as_ref().unwrap();
        selected_observer_path_aod550 = deck.observer_path_aod550;
        selected_inflow_path_aod550 = deck.inflow_path_aod550;
        selected_aerosol_path_variability_score = deck.aerosol_path_variability_score;
        selected_aod_observer = selected_aod_observer.max(deck.observer_path_aod550.unwrap_or(0.0));
        selected_aod_inflow = deck
            .inflow_path_aod550
            .filter(|value| *value != 0.0)
            .unwrap_or(selected_aod_observer);
        selected_cloud_base_m = deck.cloud_base_m;
        selected_distance_km = deck.distance_km;
        selected_path_transmission = deck.aerosol_transmission_score;
    } else if selected_regime_scoring == "remote_boundary" && top_remote_boundary.is_some() {
        let boundary = top_remote_boundary.as_ref().unwrap();
        selected_observer_path_aod550 = boundary.observer_path_aod550;
        selected_inflow_path_aod550 = boundary.inflow_path_aod550;
        selected_aerosol_path_variability_score = boundary.aerosol_path_variability_score;
        selected_aod_observer = boundary
            .observer_path_aod550
            .filter(|value| *value != 0.0)
            .unwrap_or(selected_aod_observer);
        selected_aod_inflow = boundary
            .inflow_path_aod550
            .filter(|value| *value != 0.0)
            .unwrap_or(selected_aod_observer);
        selected_cloud_base_m = boundary.cloud_base_m;
        selected_distance_km = boundary.distance_km;
        selected_path_transmission = boundary.aerosol_transmission_score;
    } else if let Some(top) = &top {
        selected_observer_path_aod550 = Some(top.observer_path_aod550);
        selected_inflow_path_aod550 = Some(top.inflow_path_aod550);
        selected_aerosol_path_variability_score = top.aerosol_path_variability_score;
        // Python `or` truthiness: a 0.0 AOD falls back to the local value.
        selected_aod_observer = if top.observer_path_aod550 != 0.0 {
            top.observer_path_aod550
        } else {
            selected_aod_observer
        };
        selected_aod_inflow = if top.inflow_path_aod550 != 0.0 {
            top.inflow_path_aod550
        } else {
            selected_aod_observer
        };
        selected_cloud_base_m = top.cloud_base_m;
        selected_distance_km = top.distance_km;
        selected_path_transmission = top.aerosol_transmission_score;
    }

    let path_illumination_factor = 0.70 + 0.30 * clamp(selected_path_transmission);
    let effective_base_m = selected_cloud_base_m;
    let geometric_reach_km = cloud_edge_max_distance_km(effective_base_m, observer_elevation_m);
    let duration_min = if selected_cloud_base_m > 0.0 && geometric_reach_km > 0.0 {
        estimate_legacy_glow_duration_minutes(
            geometric_reach_km,
            selected_distance_km,
            observer_lat,
        )
    } else {
        0.0
    };
    let duration_factor = quality_duration_factor(duration_min);

    let scoring_vividness = vividness;
    let burn_score = combine_regime_burn_score(
        physical_existence_score,
        structured_extent,
        vividness,
        solar_weight,
        regime_visibility_gate,
        regime_profile_factor,
        duration_factor,
        path_illumination_factor,
    );
    let intensity = combine_regime_intensity_score(
        physical_existence_score,
        structured_extent,
        vividness,
        solar_weight,
        regime_visibility_gate,
        regime_profile_factor,
        path_illumination_factor,
    );
    let vividness = clamp(vividness);
    let convective_risk = cross_section
        .deep_cloud_risk
        .max(top.as_ref().map_or(0.0, |signal| signal.deep_cloud_risk));
    let scene = scene_diagnosis(
        local_deck_component,
        signal_strength,
        remote_boundary_component,
        cloud_hole_component,
        convective_risk,
    );
    let phase = cloud_phase_diagnosis(
        top.as_ref().and_then(|signal| signal.cloud_ice_kg_kg),
        top.as_ref().and_then(|signal| signal.cloud_liquid_kg_kg),
    );
    let color = color_diagnosis(
        burn_score,
        vividness,
        scan.solar_elevation_deg,
        aod550,
        selected_path_transmission,
        convective_risk,
        &phase.label,
    );
    let mut boundary_uncertainty = None;
    if local_regime_wins && top_local_deck.is_some() {
        boundary_uncertainty = local_deck_boundary_uncertainty(
            top_local_deck.as_ref().unwrap(),
            scan.solar_elevation_deg,
            observer_lat,
            visible_extent,
            scoring_vividness,
            solar_weight,
            regime_visibility_gate,
            regime_profile_factor,
            burn_score,
            path_illumination_factor,
            observer_elevation_m,
        );
    }
    let map_cells = if include_map_cells {
        build_map_cloud_cells(features, columns, signals, map_radius_km)
    } else {
        Vec::new()
    };
    let mut reasons: Vec<String> = Vec::new();
    reasons.push(format!(
        "主导物理场景为 {}，物理成立度 {:.0}/100；面积、鲜艳度和地面通视只用于修正已成立场景，不再相互叠加制造存在判断。",
        selected_regime_scoring, physical_existence_score * 100.0
    ));
    let necessary_gates = [
        ("存在", soft_knee(physical_existence_score)),
        ("受光窗口", solar_weight),
        ("地面视线", regime_visibility_gate),
        ("光路通透", regime_profile_factor),
    ];
    let mut weakest_name = necessary_gates[0].0;
    let mut weakest_value = necessary_gates[0].1;
    for (name, value) in &necessary_gates[1..] {
        if *value < weakest_value {
            weakest_name = name;
            weakest_value = *value;
        }
    }
    reasons.push(format!(
        "必要条件闸门（短板定天花板）：{}；短板为「{}」{:.0}/100，{}",
        necessary_gates
            .iter()
            .map(|(name, value)| format!("{} {:.0}", name, value * 100.0))
            .collect::<Vec<String>>()
            .join("，"),
        weakest_name,
        weakest_value * 100.0,
        if weakest_value >= 0.30 {
            "各必要条件均成立，可烧度由质量项在天花板内决定。"
        } else {
            "该短板过低，已判定这片云基本烧不起来。"
        }
    ));
    reasons.push(format!(
        "质量修正：可见燃烧窗口 {:.1} 分钟，持续时间系数 {:.3}；本地 AOD {:.3} 仅作来源诊断；观测/入射路径 AOD {:.3}/{:.3}，双段逐目标光路系数 {:.3}，不再追加全场 AOD 乘数。",
        duration_min,
        duration_factor,
        aod550,
        selected_aod_observer,
        selected_aod_inflow,
        path_illumination_factor
    ));
    if effective_base_m > 0.0 {
        reasons.push(format!(
            "云底约 {:.0} 米，按地球曲率可达约 {:.0} 公里；该距离是目标云的几何上限，不代表范围内所有云都可烧。",
            effective_base_m, geometric_reach_km
        ));
    }
    if let Some(top_deck) = &top_local_deck {
        reasons.push(format!(
            "本地云幕推导：{}存在证据 {:.0}/100，太阳侧云边界约 {:.0} 公里；当前同高度光线回穿距离约 {:.0} 公里，云底 {:.0} 米，地影要求 {:.0} 米。",
            layer_label(&top_deck.layer),
            top_deck.local_cloud_presence_score * 100.0,
            top_deck.distance_km,
            top_deck.geometric_reach_km,
            top_deck.cloud_base_m,
            top_deck.required_base_m
        ));
        if let Some(uncertainty) = &boundary_uncertainty {
            reasons.push(format!(
                "模式云/晴格点把边界夹在 {:.0}–{:.0} 公里，区间内最终分数为 {:.0}–{:.0}/100；{}",
                uncertainty.lower_distance_km,
                uncertainty.upper_distance_km,
                uncertainty.score_min * 100.0,
                uncertainty.score_max * 100.0,
                if uncertainty.classification_stable {
                    "当前事件判断在该区间内稳定。"
                } else {
                    "当前事件判断会在该区间内翻转。"
                }
            ));
        }
        reasons.push(format!(
            "本地云幕真实550nm总透过率范围约 {:.4}–{:.4}，保守透过评分 {:.0}/100，入射光路净空 {:.0}/100。",
            top_deck.physical_transmission_min,
            top_deck.physical_transmission_max,
            top_deck.aerosol_transmission_score * 100.0,
            top_deck.path_clearance_score * 100.0
        ));
    }
    if let Some(top) = &top {
        reasons.push(format!(
            "最佳云目标位于约 {:.0} 公里、{} hPa，单点评分 {:.2}，相对湿度 {:.0}%，近似云型为 {}。",
            top.distance_km, top.pressure_hpa, top.score, top.rh_percent, top.cloud_type
        ));
        if top.cloud_fraction_percent.is_some() {
            reasons.push(format!(
                "原生云物理诊断：云量 {:.0}%，云水混合比 {} kg/kg，云冰混合比 {} kg/kg，融合云存在证据 {:.0}/100。",
                top.cloud_fraction_percent.unwrap(),
                py_format_e(top.cloud_liquid_kg_kg.unwrap_or(0.0), 2),
                py_format_e(top.cloud_ice_kg_kg.unwrap_or(0.0), 2),
                top.cloud_evidence_score * 100.0
            ));
        } else {
            reasons.push(format!(
                "当前模式该气压层没有原生云量/凝结物场，使用相对湿度估算云存在，证据 {:.0}/100。",
                top.cloud_evidence_score * 100.0
            ));
        }
        if top.cloud_type_burnability_score < 0.45 {
            reasons.push(
                "该云型可烧性偏低，可能更像厚云盾、低云或弱云，已下调候选云评分。".to_string(),
            );
        } else if top.cloud_type_burnability_score >= 0.75 {
            reasons.push("该云型对霞光较友好，具备较好的染色潜力。".to_string());
        }
        if top.deep_cloud_risk > 0.55 {
            reasons.push("目标云柱多层湿度叠加偏强，存在厚云盾或降水云系风险。".to_string());
        }
        if top.cloud_base_m > top.illumination_height_m {
            reasons.push(
                "云底高于估算的受光阈值，具备被夕阳或晨光照亮的条件；云顶用于判断地面是否看得到。"
                    .to_string(),
            );
        } else {
            reasons.push(
                "云底接近或低于受光阈值，成霞时间会比较敏感；云顶高度不再替代云底参与受光判断。"
                    .to_string(),
            );
        }
        if top.apparent_elevation_deg < 0.8 {
            reasons
                .push("目标云在地平线上方仰角很低，远距离视线路径会削弱地面可见效果。".to_string());
        }
        if top.aerosol_transmission_score < 0.7 {
            reasons
                .push("目标云视仰角较低或气溶胶偏多，视线路径消光会削弱亮度和饱和度。".to_string());
        }
        if top.aerosol_sensitivity_score >= 0.12 {
            reasons.push(
                "气溶胶垂直分布敏感性较高，不同尺度高度假设会明显改变光路透过率。".to_string(),
            );
        }
        if top.aerosol_path_variability_score >= 0.30 {
            reasons.push(
                "沿程 AOD 水平起伏较大，局地洁净空气不能代表完整入射与观测光路。".to_string(),
            );
        }
        if top.path_clearance_score < 0.7 {
            reasons.push("目标云与太阳/观测点之间存在疑似挡光云层，光路完整性偏差。".to_string());
        }
    } else {
        reasons.push("在霞光方向上没有找到足够强的中高云目标。".to_string());
    }
    if let Some(top_boundary) = &top_boundary {
        if top_boundary.regime == "local_deck" {
            reasons.push(format!(
                "当前最佳边界属于本地云幕场景，物理评分 {:.2}，不再把近场云直接当作地平线遮挡。",
                top_boundary.score
            ));
        } else {
            reasons.push(format!(
                "{}云边界位于约 {:.0} 公里，云底约 {:.0} 米，受光阈值约 {:.0} 米，边界光路评分 {:.2}，近似云型为 {}。",
                layer_label(&top_boundary.layer),
                top_boundary.distance_km,
                top_boundary.cloud_base_m,
                top_boundary.required_base_m,
                top_boundary.score,
                top_boundary.cloud_type
            ));
        }
        if top_boundary.cloud_type_burnability_score < 0.45 {
            reasons.push("云边界对应云型可烧性偏低，边界评分已按云型风险下调。".to_string());
        }
        if top_boundary.deep_cloud_risk > 0.55 {
            reasons.push("云边界附近存在深厚云系风险，可能发灰或被降水云盾压暗。".to_string());
        }
        if top_boundary.edge_contrast_score < 0.35 {
            reasons.push("云区同层湿度梯度偏弱，更像连续云盾而不是清晰云边界。".to_string());
        }
        if top_boundary.clear_slot_score < 0.65 {
            reasons.push("太阳方向清空槽不够完整，边界受光通道存在风险。".to_string());
        }
        if top_boundary.path_clearance_score < 0.65 {
            reasons.push("云边界前方存在疑似挡光云，边界型火烧云不确定性较高。".to_string());
        }
        if top_boundary.thickness_score < 0.75 {
            reasons.push("目标附近多层云叠加偏厚，云体可能发灰或压暗。".to_string());
        }
    } else if raw_extent >= 0.45 {
        reasons.push(
            "候选云虽多，但没有识别到明确云边界，大面积成霞度已按连续云盾风险下调。".to_string(),
        );
    }
    if let Some(top_cloud_hole) = &top_cloud_hole {
        reasons.push(format!(
            "云洞透光信号位于约 {:.0} 公里，清空槽 {:.0}/100，洞边界对比 {:.0}/100；后方约 {:.0} 公里存在{}目标云，云洞-目标对齐度 {:.0}/100，后方挡光风险 {:.0}/100。",
            top_cloud_hole.distance_km,
            top_cloud_hole.clear_slot_score * 100.0,
            top_cloud_hole.edge_contrast_score * 100.0,
            top_cloud_hole.rear_target_distance_km,
            top_cloud_hole.rear_target_cloud_type,
            top_cloud_hole.alignment_score * 100.0,
            top_cloud_hole.rear_blocking_risk * 100.0
        ));
        if top_cloud_hole.rear_blocking_risk > 0.45 {
            reasons.push(
                "云洞后方存在一定厚云或中低云遮挡，后方云可能部分挡光而不是充分受光。".to_string(),
            );
        }
        if top_cloud_hole.path_clearance_score < 0.65 {
            reasons
                .push("云洞后方通道仍有中低云或厚云风险，透光到后方云的确定性偏低。".to_string());
        } else if top_cloud_hole.rear_target_score >= 0.55 {
            reasons.push("云洞后方目标云可烧性较好，属于“洞后云被打亮”的有利结构。".to_string());
        }
    }
    if let Some(blockage) = solar_inflow_blockage {
        if blockage.score >= 0.18 {
            reasons.push(format!(
                "远距离太阳入射挡光风险 {:.0}/100：约 {:.0} 公里、{} hPa 云柱穿过太阳到目标云的入射光线，已下调存在分数和强度。",
                blockage.score * 100.0, blockage.distance_km, blockage.pressure_hpa
            ));
        }
    }
    reasons.extend(cross_section.summary.iter().cloned());
    if !signals.is_empty() && strong_burning_cloud_count == 0 && burning_cloud_count > 0 {
        reasons.push("存在弱到中等的可照亮云目标，但还没有达到强成霞格子的阈值，更像有霞光潜力而非大规模火烧云。".to_string());
    } else if burning_cloud_count <= 2 && !signals.is_empty() {
        reasons.push("目前更像局地点烧，可被照亮的云覆盖范围偏小，不像大面积火烧云。".to_string());
    } else if burning_cloud_count <= 5 && !signals.is_empty() {
        reasons.push("可烧云区有一定规模，但还不算大范围铺开。".to_string());
    } else if burning_cloud_count >= 10 {
        reasons.push("可烧云区覆盖较广，具备形成大面积火烧云的条件。".to_string());
    }
    reasons.push(format!(
        "地面可见的大面积成霞度约 {:.0}/100，鲜艳度约 {:.0}/100。",
        visible_extent * 100.0,
        vividness * 100.0
    ));
    if low_penalty > 0.4 {
        reasons.push("观测点附近低层湿度偏高，地平线附近可能被低云或雾霾遮挡。".to_string());
    }
    if let Some(visibility_m) = surface_visibility_m {
        reasons.push(format!(
            "地面天气诊断：模式能见度约 {:.1} 公里，小时雨强约 {:.1} 毫米。",
            visibility_m / 1000.0,
            precip_rate.unwrap_or(0.0) * 3600.0
        ));
    }
    if weather_penalty >= 0.35 {
        reasons.push(
            "地面能见度偏低或存在降水，即使高空云被照亮，实际观测效果也会明显受限。".to_string(),
        );
    }
    if !signals.is_empty() && ground_visibility <= 0.05 {
        reasons.push("近场低云遮挡过强，即使高空有可受光云，地面也很难看到完整霞光。".to_string());
    }
    if solar_weight < 0.5 && scan.solar_elevation_deg > 8.0 {
        reasons.push("太阳高度仍偏高，尚未进入晚霞有效受光窗口。".to_string());
    } else if solar_weight < 0.5 {
        reasons.push("太阳高度已经明显偏低，云层进入地影后的成霞可能性快速下降。".to_string());
    }
    if aerosol_quality == "fallback" {
        reasons.push("当前气溶胶使用的是回退值，置信度会偏低。".to_string());
    }
    if scan.solar_elevation_deg < 0.0 && step_horizon_disc.overcast_shadow_factor < 0.85 {
        reasons.push(format!(
            "负太阳高度角远地平线缝隙评分 {:.0}/100，连续阴云遮影系数 {:.2}，已抑制晨昏仰角增益并下调受光亮度。",
            step_horizon_disc.horizon_gap_score * 100.0,
            step_horizon_disc.overcast_shadow_factor
        ));
    }
    // The scene diagnosis provides the final regime (it can demote to
    // clear/convective even when the scoring regime differs).
    AssessmentStepOutput {
        forecast_hour: scan.forecast_hour,
        valid_time_utc: scan.valid_time_utc.clone(),
        valid_time_local: valid_time_local.to_string(),
        solar_elevation_deg: scan.solar_elevation_deg,
        solar_azimuth_deg: scan.solar_azimuth_deg,
        burn_score,
        intensity_score: intensity,
        extent_score: structured_extent,
        vividness_score: vividness,
        visibility_score: visibility,
        low_cloud_penalty: low_penalty,
        candidate_count: signals.len(),
        burning_cloud_count,
        top_candidate: top,
        strong_burning_cloud_count,
        top_boundary,
        boundary_score: boundary_component,
        boundary_uncertainty,
        top_cloud_hole,
        cloud_hole_score: cloud_hole_component,
        cross_section: cross_section.clone(),
        solar_inflow_blockage: solar_inflow_blockage.cloned(),
        selected_regime: scene.selected_regime,
        scene_scores: scene.scene_scores,
        local_sky_score: scene.local_sky_score,
        remote_sky_score: scene.remote_sky_score,
        convective_risk: scene.convective_risk,
        color_diagnosis: {
            let mut value = serde_json::to_value(&color).expect("color diagnosis serializes");
            value["phase"] = serde_json::to_value(&phase).expect("phase serializes");
            value
        },
        reasons,
        map_cells,
        geometric_cloud_coverage: structured_extent,
        structure_gate_score,
        satellite_retrieval_material_evidence: scan.satellite_material_evidence,
        selected_observer_path_aod550,
        selected_inflow_path_aod550,
        selected_aerosol_path_variability_score,
        forward_view_cloud_field_amount,
        forward_burnable_cloud_field_amount,
        observer_local_cloud_field_amount,
        observation_burnable: true,
        observation_score_ceiling: None,
        surface_weather_penalty: weather.clone(),
        horizon_gap_score: step_horizon_disc.horizon_gap_score,
        overcast_shadow_factor: step_horizon_disc.overcast_shadow_factor,
        columns: if full_detail {
            columns.iter().map(|(_, column)| column.clone()).collect()
        } else {
            Vec::new()
        },
        candidate_signals: if full_detail {
            signals.to_vec()
        } else {
            Vec::new()
        },
        local_deck_signals: if full_detail {
            local_deck_signals.to_vec()
        } else {
            Vec::new()
        },
        remote_boundary_signals: if full_detail {
            remote_boundary_signals.to_vec()
        } else {
            Vec::new()
        },
        boundary_signals: if full_detail {
            boundary_signals.to_vec()
        } else {
            Vec::new()
        },
        cloud_hole_signals: if full_detail {
            cloud_hole_signals.to_vec()
        } else {
            Vec::new()
        },
    }
}

fn combine_regime_burn_score(
    physical_existence_score: f64,
    geometric_extent: f64,
    vividness: f64,
    solar_weight: f64,
    visibility_gate_score: f64,
    profile_factor: f64,
    duration_factor: f64,
    path_illumination_factor: f64,
) -> f64 {
    let necessary_ceiling = soft_knee(physical_existence_score)
        .min(solar_weight)
        .min(visibility_gate_score)
        .min(profile_factor);
    let quality_fill = clamp(0.72 + 0.18 * vividness + 0.10 * geometric_extent);
    clamp(
        necessary_ceiling
            * quality_fill
            * py_clamp(duration_factor, 0.50, 1.12)
            * py_clamp(path_illumination_factor, 0.70, 1.0),
    )
}

fn combine_regime_intensity_score(
    physical_existence_score: f64,
    geometric_extent: f64,
    vividness: f64,
    solar_weight: f64,
    visibility_gate_score: f64,
    profile_factor: f64,
    path_illumination_factor: f64,
) -> f64 {
    let necessary_ceiling = soft_knee(physical_existence_score)
        .min(solar_weight)
        .min(visibility_gate_score)
        .min(profile_factor);
    let quality_fill = clamp(0.56 + 0.36 * vividness + 0.08 * geometric_extent);
    clamp(necessary_ceiling * quality_fill * py_clamp(path_illumination_factor, 0.70, 1.0))
}

/// `local_deck_boundary_uncertainty` (scoring.py:2280-2366).
#[allow(clippy::too_many_arguments)]
fn local_deck_boundary_uncertainty(
    boundary: &BoundarySignalOutput,
    solar_elevation_deg: f64,
    observer_latitude_deg: f64,
    visible_extent: f64,
    vividness: f64,
    solar_weight: f64,
    visibility_gate_score: f64,
    profile_factor: f64,
    nominal_burn_score: f64,
    path_illumination_factor: f64,
    observer_elevation_m: f64,
) -> Option<BoundaryUncertaintyOutput> {
    let event_threshold = BURNABLE_CELL_SCORE;
    let raw_lower = boundary.edge_distance_lower_km?;
    let raw_upper = boundary.edge_distance_upper_km?;
    let lower = raw_lower.min(raw_upper).max(1.0);
    let upper = raw_upper.max(lower);
    if upper - lower < 0.1 {
        return None;
    }
    let nominal = boundary.distance_km.clamp(lower, upper);
    let nominal_geometry = cloud_edge_geometry_score(
        nominal,
        boundary.cloud_base_m,
        solar_elevation_deg,
        observer_elevation_m,
    );
    if nominal_geometry <= 0.0 {
        return None;
    }
    let score_at = |distance_km: f64| -> f64 {
        let geometry = cloud_edge_geometry_score(
            distance_km,
            boundary.cloud_base_m,
            solar_elevation_deg,
            observer_elevation_m,
        );
        let physical_existence = clamp(boundary.score * geometry / nominal_geometry);
        let duration = estimate_legacy_glow_duration_minutes(
            boundary.geometric_reach_km,
            distance_km,
            observer_latitude_deg,
        );
        combine_regime_burn_score(
            physical_existence,
            visible_extent,
            vividness,
            solar_weight,
            visibility_gate_score,
            profile_factor,
            quality_duration_factor(duration),
            path_illumination_factor,
        )
    };
    let bracket_distances = np_linspace(lower, upper, 41);
    let bracket_scores: Vec<f64> = bracket_distances
        .iter()
        .map(|distance| score_at(*distance))
        .collect();
    let nominal_event = nominal_burn_score >= event_threshold;
    let classification_stable = bracket_scores
        .iter()
        .all(|score| (*score >= event_threshold) == nominal_event);
    let maximum_distance = cloud_edge_max_distance_km(boundary.cloud_base_m, observer_elevation_m);
    let search_distances = np_linspace(1.0, maximum_distance.max(1.0), 801);
    let mut nearest_flip: Option<f64> = None;
    for distance in &search_distances {
        if (score_at(*distance) >= event_threshold) != nominal_event {
            let displacement = (distance - nominal).abs();
            nearest_flip = Some(match nearest_flip {
                Some(current) => current.min(displacement),
                None => displacement,
            });
        }
    }
    Some(BoundaryUncertaintyOutput {
        regime: "local_deck".to_string(),
        source: "model-cloud-clear-bracket".to_string(),
        nominal_distance_km: nominal,
        lower_distance_km: lower,
        upper_distance_km: upper,
        width_km: upper - lower,
        score_min: bracket_scores.iter().copied().fold(f64::INFINITY, f64::min),
        score_max: bracket_scores
            .iter()
            .copied()
            .fold(f64::NEG_INFINITY, f64::max),
        event_threshold,
        classification_stable,
        nearest_flip_displacement_km: nearest_flip,
    })
}

/// `build_map_cloud_cells` (scoring.py:4287-4340).
fn build_map_cloud_cells(
    features: &[Feature<'_>],
    columns: &[(ColumnKey, CloudColumnOutput)],
    signals: &[CandidateSignalOutput],
    max_distance_km: f64,
) -> Vec<MapCloudCellOutput> {
    // First-best signal per rounded grid point (Python `>` keeps the first max).
    let mut signal_by_point: Vec<((u64, u64), usize)> = Vec::new();
    let mut signal_index: HashMap<(u64, u64), usize> = HashMap::new();
    for (position, signal) in signals.iter().enumerate() {
        let key = (
            python_round(signal.lat, 4).to_bits(),
            python_round(signal.lon, 4).to_bits(),
        );
        match signal_index.get(&key) {
            Some(&current) => {
                if signal.score > signals[current].score {
                    signal_by_point[current] = (key, position);
                    signal_index.insert(key, position);
                }
            }
            None => {
                signal_index.insert(key, position);
                signal_by_point.push((key, position));
            }
        }
    }
    let lookup: HashMap<(u64, u64), usize> = signal_by_point
        .iter()
        .map(|(key, position)| (*key, *position))
        .collect();
    let mut cells: Vec<MapCloudCellOutput> = Vec::new();
    for (_, column) in columns {
        if column.distance_km > max_distance_km {
            continue;
        }
        let cloud_evidence = column
            .high_presence
            .max(column.mid_presence)
            .max(column.low_presence)
            .max(column.explicit_cloud_evidence);
        let key = (
            python_round(column.lat, 4).to_bits(),
            python_round(column.lon, 4).to_bits(),
        );
        let signal = lookup.get(&key).map(|position| &signals[*position]);
        let burn_score = signal.map_or(0.0, |signal| signal.score);
        if cloud_evidence < 0.10 && burn_score < 0.15 {
            continue;
        }
        cells.push(MapCloudCellOutput {
            lat: column.lat,
            lon: column.lon,
            distance_km: column.distance_km,
            bearing_deg: column.bearing_deg,
            high_cloud: column.high_presence,
            mid_cloud: column.mid_presence,
            low_cloud: column.low_presence,
            cloud_evidence,
            cloud_type: column.cloud_type.clone(),
            cloud_base_m: column.cloud_base_m,
            cloud_top_m: column.cloud_top_m,
            deep_cloud_risk: column.deep_cloud_risk,
            burn_score,
            illumination_score: signal
                .map(|signal| {
                    illumination_feasibility(signal.cloud_base_m, signal.illumination_height_m)
                })
                .unwrap_or(0.0),
            path_clearance_score: signal.map_or(0.0, |signal| signal.path_clearance_score),
            aerosol_transmission_score: signal
                .map_or(0.0, |signal| signal.aerosol_transmission_score),
            blocking_risk: clamp(column.low_presence.max(0.70 * column.deep_cloud_risk)),
            atmospheric_clarity_score: signal
                .map_or(1.0, |signal| signal.atmospheric_clarity_score),
        });
    }
    let _ = features;
    cells
}

// ---------------------------------------------------------------------------
// Radar observation constraints (service.py:1424-1562).
// ---------------------------------------------------------------------------

fn observation_ramp(value: f64, low: f64, high: f64) -> f64 {
    if high <= low {
        return if value >= high { 1.0 } else { 0.0 };
    }
    let ratio = clamp((value - low) / (high - low));
    ratio * ratio * (3.0 - 2.0 * ratio)
}

fn radar_observation_reliability(radar: &RadarInput) -> f64 {
    let (source, coverage, age) = (
        radar.source.as_ref(),
        radar.coverage_ratio,
        radar.age_minutes,
    );
    if source.is_none() || coverage.is_none() || age.is_none() {
        return 0.0;
    }
    let coverage = clamp(coverage.unwrap());
    let age_minutes = age.unwrap().max(0.0);
    let freshness = if age_minutes <= 10.0 {
        1.0
    } else {
        clamp((70.0 - age_minutes) / 60.0)
    };
    clamp(coverage * freshness)
}

fn radar_effective_convective_risk(radar: &RadarInput) -> (f64, f64) {
    let reliability = radar_observation_reliability(radar);
    if reliability <= 0.0 {
        return (0.0, 0.0);
    }
    let dbz_risk = radar
        .max_dbz
        .map(|dbz| observation_ramp(dbz, 28.0, 52.0))
        .unwrap_or(0.0);
    let local_precip = clamp(radar.local_precip_ratio.unwrap_or(0.0));
    let path_precip = clamp(radar.path_precip_ratio.unwrap_or(0.0));
    let convective_coverage = clamp(radar.convective_ratio.unwrap_or(0.0));
    let reported_risk = clamp(radar.convective_risk.unwrap_or(0.0));
    let precip_risk = local_precip.max(path_precip).max(convective_coverage);
    let raw_risk = reported_risk
        .max(convective_coverage)
        .max(0.72 * dbz_risk + 0.28 * precip_risk);
    (reliability, clamp(reliability * raw_risk))
}

fn radar_effective_path_blocking_risk(radar: &RadarInput) -> f64 {
    let reliability = radar_observation_reliability(radar);
    if reliability <= 0.0 {
        return 0.0;
    }
    clamp(reliability * clamp(radar.path_precip_ratio.unwrap_or(0.0)))
}

pub(crate) fn radar_diagnostics(value: serde_json::Value) -> Result<serde_json::Value, String> {
    let radar: RadarInput = serde_json::from_value(value).map_err(|e| e.to_string())?;
    let (reliability, convective_risk) = radar_effective_convective_risk(&radar);
    let path_risk = radar_effective_path_blocking_risk(&radar);
    Ok(serde_json::json!({
        "radar_reliability_score":reliability,
        "effective_convective_risk":convective_risk,
        "effective_path_blocking_risk":path_risk,
        "convective_score_ceiling":py_clamp(
            1.0 - 0.88 * observation_ramp(path_risk, 0.18, 0.72),0.12,f64::MAX),
    }))
}

/// `apply_nowcast_observation_constraints` (service.py:1486-1562), assessment side.
fn apply_observation_constraints(
    mut step: AssessmentStepOutput,
    radar: &RadarInput,
) -> AssessmentStepOutput {
    let (radar_reliability, convective_risk) = radar_effective_convective_risk(radar);
    let path_blocking_risk = radar_effective_path_blocking_risk(radar);
    let convective_ceiling = py_clamp(
        1.0 - 0.88 * observation_ramp(path_blocking_risk, 0.18, 0.72),
        0.12,
        f64::MAX,
    );
    let burn_score = step.burn_score.min(convective_ceiling);
    let intensity_score = step.intensity_score.min(convective_ceiling);
    let vividness_score = step.vividness_score.min(convective_ceiling);
    let visibility_score = step.visibility_score;
    let mut scene_scores: Vec<(String, f64)> = step.scene_scores.clone();
    let mut has_convective = false;
    for (key, value) in scene_scores.iter_mut() {
        if key == "convective" {
            *value = (*value).max(convective_risk);
            has_convective = true;
        }
    }
    if !has_convective {
        scene_scores.push(("convective".to_string(), convective_risk));
    }
    let positive_scene_score = scene_scores
        .iter()
        .filter(|(key, _)| key != "convective")
        .map(|(_, value)| *value)
        .fold(f64::NEG_INFINITY, f64::max)
        .max(0.0);
    let mut selected_regime = step.selected_regime.clone();
    if path_blocking_risk >= 0.62 && path_blocking_risk >= positive_scene_score {
        selected_regime = "convective".to_string();
    }
    if !step.color_diagnosis.is_null() {
        let confidence = step
            .color_diagnosis
            .get("confidence")
            .and_then(|value| value.as_f64())
            .unwrap_or(0.0);
        step.color_diagnosis["confidence"] =
            serde_json::json!(confidence.min(vividness_score).min(visibility_score));
        if selected_regime == "convective" {
            step.color_diagnosis["type"] = serde_json::json!("muted_glow");
        }
        if let Some(inputs) = step
            .color_diagnosis
            .get_mut("inputs")
            .and_then(|value| value.as_object_mut())
        {
            let deep = inputs
                .get("deep_cloud_risk")
                .and_then(|value| value.as_f64())
                .unwrap_or(0.0);
            inputs.insert(
                "deep_cloud_risk".to_string(),
                serde_json::json!(deep.max(convective_risk)),
            );
        }
    }
    if radar.source.is_some() {
        let mut radar_reason = format!(
            "雷达约束：可靠度 {:.0}/100，区域对流风险 {:.0}/100，日落光路阻塞风险 {:.0}/100，最终分数上限 {:.0}/100。",
            radar_reliability * 100.0,
            convective_risk * 100.0,
            path_blocking_risk * 100.0,
            convective_ceiling * 100.0
        );
        if radar_reliability < 0.25 {
            radar_reason += "覆盖或时效不足，不施加强约束，也不用无回波推断晴空。";
        }
        step.reasons.push(radar_reason);
    }
    let mut score_ceiling = convective_ceiling;
    if let Some(existing) = step.observation_score_ceiling {
        score_ceiling = score_ceiling.min(existing);
    }
    step.burn_score = clamp(burn_score);
    step.intensity_score = clamp(intensity_score);
    step.vividness_score = clamp(vividness_score);
    step.visibility_score = clamp(visibility_score);
    step.convective_risk = step.convective_risk.max(convective_risk);
    step.selected_regime = selected_regime;
    step.scene_scores = scene_scores;
    step.observation_burnable = step.observation_burnable && path_blocking_risk < 0.62;
    step.observation_score_ceiling = if score_ceiling < 1.0 {
        Some(score_ceiling)
    } else {
        None
    };
    step
}
