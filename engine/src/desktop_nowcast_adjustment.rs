//! Multi-scan calibrated nowcast adjustment assembly.
//!
//! One bounded request owns frame deduplication, five-minute timeline assignment,
//! per-scan retrieval, primary-scan selection, coverage, aggregate provenance
//! and the vertical display profile. Large output is written to a receipt-backed
//! file rather than the 1 MiB RPC line.

use crate::desktop_hsd_retrieval::{
    frame, horizontal_metrics, FrameContract, HorizontalMetrics, Level, OutputColumn, Prior,
};
use crate::desktop_nowcast_retrieval::{
    execute_retrieval, ColumnSetRecord, FeatureRecord, Params as RetrievalParams,
};
use crate::request_file;
use crate::tensor_bundle::{verify_files, F32Bundle};
use crate::time_contract;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fs::{self, OpenOptions};
use std::io::Write as _;
use std::path::{Path, PathBuf};

const MAX_REQUEST_BYTES: u64 = 4 * 1024 * 1024;
const MAX_RESULT_BYTES: usize = 128 * 1024 * 1024;
const MAX_FRAMES: usize = 16;
const MAX_FRAME_WORKERS: usize = 4;
const COVERAGE_PROXIMITY_TAU_MINUTES: f64 = 15.0;
// Mirrors the motion module's default acceptance gate; advection only runs above it.
const MOTION_ACCEPT_CORRELATION: f64 = 0.35;
const MOTION_RELIABILITY_FLOOR: f64 = 0.7;
const NOWCAST_ALGORITHM: &str =
    "satellite-nowcast-2026.11.04.87-hsd-only-multi-observation-aerosol-ground-touchdown-coverage-gate-v4";
const CLOUD_COLUMN_SCHEMA_VERSION: u64 = 4;
const SOURCE_KIND: &str = "hsd";
const SOURCE_LABEL: &str = "noaa-himawari9-hsd-calibrated+gfs-prior";
const IMAGE_CHANNEL: &str = "hsd-b07+b13+b15";
const IMAGE_AREA: &str = "Himawari HSD segmented footprint";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    request_path: PathBuf,
    output_path: PathBuf,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct AdjustmentRequest {
    target_time_utc: String,
    timezone_name: String,
    point_lat: f64,
    point_lon: f64,
    radius_km: f64,
    azimuth_deg: f64,
    display_frame: Frame,
    frames: Vec<Frame>,
    source_times_utc: Vec<String>,
    motion_dx_px_per_hour: f64,
    motion_dy_px_per_hour: f64,
    motion_correlation: f64,
    motion_quality: String,
    motion_age_minutes: Option<f64>,
    #[serde(default)]
    lagrangian_b13_trend_k_per_hour: Option<f64>,
    advection: Option<AdvectionPlan>,
    single_historical_observation: bool,
}

#[derive(Serialize)]
struct AdjustmentOutput {
    contract: &'static str,
    runtime_python_used: bool,
    target_time_utc: String,
    nowcast_algorithm: &'static str,
    source: &'static str,
    image_valid_time_utc: String,
    image_valid_time_local: String,
    image_channel: &'static str,
    image_area: &'static str,
    near_cloud_ratio: f64,
    far_cloud_ratio: f64,
    trend_score: f64,
    visibility_penalty: f64,
    target_bonus: f64,
    confidence: f64,
    summary: Vec<String>,
    motion_dx_px_per_hour: f64,
    motion_dy_px_per_hour: f64,
    motion_correlation: f64,
    projection_hours: f64,
    satellite_source_times_utc: Vec<String>,
    satellite_motion_age_minutes: Option<f64>,
    satellite_motion_quality: String,
    satellite_advection_horizon_hours: f64,
    infrared_near_cloud_ratio: Option<f64>,
    infrared_far_cloud_ratio: Option<f64>,
    visible_near_cloud_ratio: Option<f64>,
    visible_far_cloud_ratio: Option<f64>,
    channel_fusion_weight_visible: f64,
    local_cloud_ratio: f64,
    horizon_cloud_ratio: f64,
    thin_cloud_ratio: f64,
    thick_cloud_ratio: f64,
    cloud_texture_score: f64,
    cloud_uniformity_score: f64,
    cloud_blanket_score: f64,
    horizon_clearance_score: f64,
    composition_score: f64,
    observed_features_by_offset: BTreeMap<String, Vec<FeatureRecord>>,
    observed_feature_times_by_offset: BTreeMap<String, String>,
    cloud_column_schema_version: u64,
    cloud_column_sets: Vec<Value>,
    vertical_prior_identity: String,
    vertical_prior_source: String,
    satellite_retrieval_quality: String,
    satellite_low_cloud_state: String,
    satellite_range_bytes: u64,
    satellite_range_blocks: u64,
    nowcast_point_lat: f64,
    nowcast_point_lon: f64,
    nowcast_cache_identity: String,
    satellite_vertical_profile: Value,
    calibrated_sample_coverage_ratio: f64,
}

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct Frame {
    manifest_path: PathBuf,
    valid_time_utc: String,
    retrieval: RetrievalContext,
}

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct AdvectionPlan {
    horizon_hours: f64,
    scale: f64,
    applied: bool,
    reason: String,
    motion_age_minutes: Option<f64>,
    motion_quality: String,
}

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct RetrievalContext {
    cycle_time_utc: String,
    left_forecast_hour: u32,
    right_forecast_hour: u32,
    fraction: f64,
    left_cache_path: PathBuf,
    right_cache_path: PathBuf,
    local_radius_km: f64,
    domain_hash: String,
    resolution: String,
}

struct RetrievedFrame {
    valid_time: DateTime<Utc>,
    offset_minutes: i64,
    features: Vec<FeatureRecord>,
    column_sets: Vec<ColumnSetRecord>,
    metrics: HorizontalMetrics,
    priors: Vec<Option<Prior>>,
    sample_points: Vec<(f64, f64)>,
}

fn parse_utc(value: &str, label: &str) -> Result<DateTime<Utc>, String> {
    time_contract::parse_utc(value).map_err(|error| format!("{label} is invalid: {error}"))
}

fn column_set_value(set: &ColumnSetRecord) -> Value {
    let mut value = json!({
        "schema_version": set.schema_version,
        "valid_time_utc": set.valid_time_utc,
        "source": set.source,
        "vertical_prior_identity": set.vertical_prior_identity,
        "retrieval_quality": set.retrieval_quality,
        "low_cloud_state": set.low_cloud_state,
        "columns": set.columns,
        "motion_dx_px_per_hour": set.motion_dx_px_per_hour,
        "motion_dy_px_per_hour": set.motion_dy_px_per_hour,
    });
    // Cloud-column consumers require explicit AGL diagnostics alongside ASL.
    let encoded = value["columns"]
        .as_array_mut()
        .expect("columns are an array");
    for (raw, column) in encoded.iter_mut().zip(&set.columns) {
        let object = raw.as_object_mut().expect("column is an object");
        let terrain = column.terrain_asl_m;
        for (asl, agl) in [
            (column.base_p10_asl_m, "base_p10_agl_m"),
            (column.base_p50_asl_m, "base_p50_agl_m"),
            (column.base_p90_asl_m, "base_p90_agl_m"),
            (column.top_asl_m, "top_agl_m"),
        ] {
            object.insert(
                agl.into(),
                asl.zip(terrain)
                    .map(|(height, ground)| (height - ground).max(0.0))
                    .map_or(Value::Null, |height| json!(height)),
            );
        }
    }
    value
}

fn aggregate_quality<'a>(values: impl IntoIterator<Item = &'a str>, total: usize) -> String {
    let scores: Vec<f64> = values
        .into_iter()
        .filter_map(|value| match value {
            "low" => Some(1.0),
            "medium" => Some(2.0),
            "high" => Some(3.0),
            _ => None,
        })
        .collect();
    let coverage = scores.len() as f64 / total.max(1) as f64;
    if scores.is_empty() || coverage < 0.25 {
        return "unknown".into();
    }
    let mean = scores.iter().sum::<f64>() / scores.len() as f64;
    if mean >= 2.5 && coverage >= 0.75 {
        "high".into()
    } else if mean >= 1.5 && coverage >= 0.50 {
        "medium".into()
    } else {
        "low".into()
    }
}

fn low_state<'a>(states: impl IntoIterator<Item = &'a str>) -> String {
    let states: Vec<&str> = states
        .into_iter()
        .filter(|state| matches!(*state, "detected" | "clear" | "unknown"))
        .collect();
    let known: Vec<&str> = states
        .iter()
        .copied()
        .filter(|state| *state != "unknown")
        .collect();
    if !known.is_empty()
        && known.iter().filter(|state| **state == "detected").count() as f64 / known.len() as f64
            >= 0.34
    {
        return "detected".into();
    }
    if states.contains(&"clear") && !states.contains(&"unknown") {
        "clear".into()
    } else {
        "unknown".into()
    }
}

fn sha_prefix(values: &BTreeSet<String>, length: usize) -> String {
    let joined = values.iter().cloned().collect::<Vec<_>>().join("|");
    Sha256::digest(joined.as_bytes())[..length]
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn aggregate_identity<'a>(sets: impl IntoIterator<Item = &'a ColumnSetRecord>) -> String {
    let identities: BTreeSet<String> = sets
        .into_iter()
        .map(|set| set.vertical_prior_identity.clone())
        .filter(|value| !value.is_empty())
        .collect();
    match identities.len() {
        0 => "unknown".into(),
        1 => identities.first().expect("one identity").clone(),
        _ => format!("gfs-prior-set:{}", sha_prefix(&identities, 12)),
    }
}

fn aggregate_source<'a>(sets: impl IntoIterator<Item = &'a ColumnSetRecord>) -> String {
    let sources: BTreeSet<String> = sets
        .into_iter()
        .flat_map(|set| set.columns.iter())
        .map(|column| column.vertical_prior_source.clone())
        .filter(|value| !value.is_empty())
        .collect();
    match sources.len() {
        0 => "unknown".into(),
        1 => sources.first().expect("one source").clone(),
        _ => format!("mixed-prior:{}", sha_prefix(&sources, 8)),
    }
}

fn sample_usable(column: &OutputColumn) -> bool {
    column.retrieval_quality != "unknown"
        && column.vertical_prior_identity != "unknown"
        && column.vertical_prior_source.starts_with("gfs")
}

fn rounded_scaled(value: f64, scale: f64) -> i64 {
    (value * scale).round_ties_even() as i64
}

fn calibrated_coverage(sets: &[ColumnSetRecord], samples: &[(f64, f64)]) -> f64 {
    let mut statuses = BTreeMap::<(i64, i64), bool>::new();
    for column in sets.iter().flat_map(|set| set.columns.iter()) {
        let key = (
            rounded_scaled(column.distance_km, 100.0),
            rounded_scaled(column.bearing_deg, 100.0),
        );
        statuses
            .entry(key)
            .and_modify(|usable| *usable |= sample_usable(column))
            .or_insert_with(|| sample_usable(column));
    }
    if statuses.is_empty() || samples.is_empty() {
        return 0.0;
    }
    let usable = samples
        .iter()
        .filter(|(bearing, distance)| {
            statuses
                .iter()
                .any(|((column_distance, column_bearing), usable)| {
                    *usable
                        && ((*bearing - (*column_bearing as f64 / 100.0) + 180.0).rem_euclid(360.0)
                            - 180.0)
                            .abs()
                            <= 1.0
                        && (*distance - (*column_distance as f64 / 100.0)).abs() <= 5.0
                })
        })
        .count();
    usable as f64 / samples.len() as f64
}

fn frame_proximity_weight(valid_time_utc: &str, target: DateTime<Utc>) -> f64 {
    match parse_utc(valid_time_utc, "column.valid_time_utc") {
        Ok(valid_time) => {
            let offset_minutes = (valid_time - target).num_seconds() as f64 / 60.0;
            (-(offset_minutes / COVERAGE_PROXIMITY_TAU_MINUTES).powi(2)).exp()
        }
        Err(_) => 1.0,
    }
}

/// Usable cloud-column fraction weighted by each scan's temporal proximity to the
/// target; reduces to the unweighted fraction for a single scan or equal coverage.
fn cloud_column_coverage<'a>(
    sets: impl IntoIterator<Item = &'a ColumnSetRecord>,
    target: DateTime<Utc>,
) -> f64 {
    let mut statuses = BTreeMap::<(String, i64, i64, i64, i64), String>::new();
    for column in sets.into_iter().flat_map(|set| set.columns.iter()) {
        let key = (
            column.valid_time_utc.clone(),
            rounded_scaled(column.lat, 100_000.0),
            rounded_scaled(column.lon, 100_000.0),
            rounded_scaled(column.distance_km, 100.0),
            rounded_scaled(column.bearing_deg, 100.0),
        );
        statuses
            .entry(key)
            .and_modify(|quality| {
                if quality == "unknown" && column.retrieval_quality != "unknown" {
                    *quality = column.retrieval_quality.clone();
                }
            })
            .or_insert_with(|| column.retrieval_quality.clone());
    }
    if statuses.is_empty() {
        return 0.0;
    }
    let mut weights = BTreeMap::<String, f64>::new();
    let mut weighted_usable = 0.0;
    let mut weighted_total = 0.0;
    for ((valid_time_utc, ..), quality) in &statuses {
        let weight = *weights
            .entry(valid_time_utc.clone())
            .or_insert_with(|| frame_proximity_weight(valid_time_utc, target));
        weighted_total += weight;
        if quality != "unknown" {
            weighted_usable += weight;
        }
    }
    if weighted_total <= 0.0 {
        0.0
    } else {
        weighted_usable / weighted_total
    }
}

/// Reliability credit for the motion estimate used to advect clouds: correlation
/// normalized over the acceptance range, floored so accepted motion keeps baseline
/// credit. Returns 1.0 when no advection is applied (motion never used).
fn motion_reliability_factor(request: &AdjustmentRequest) -> f64 {
    if !request.advection.as_ref().is_some_and(|plan| plan.applied) {
        return 1.0;
    }
    let correlation = request.motion_correlation.clamp(0.0, 1.0);
    let normalized = ((correlation - MOTION_ACCEPT_CORRELATION)
        / (1.0 - MOTION_ACCEPT_CORRELATION))
        .clamp(0.0, 1.0);
    MOTION_RELIABILITY_FLOOR + (1.0 - MOTION_RELIABILITY_FLOOR) * normalized
}

const PIXEL_TOLERANCE_KM: f64 = 25.0;
const EARTH_RADIUS_KM: f64 = 6371.0;
const LATITUDE_BUCKET_DEG: f64 = 0.05;

fn haversine_km(left: PointValue, right: PointValue) -> f64 {
    let lat1 = left.lat.to_radians();
    let lon1 = left.lon.to_radians();
    let lat2 = right.lat.to_radians();
    let lon2 = right.lon.to_radians();
    let sin_lat = ((lat2 - lat1) / 2.0).sin();
    let sin_lon = ((lon2 - lon1) / 2.0).sin();
    let h = sin_lat.powi(2) + lat1.cos() * lat2.cos() * sin_lon.powi(2);
    2.0 * EARTH_RADIUS_KM * h.sqrt().asin()
}

fn sample_hsd_bands(
    manifest_path: &Path,
    targets: &[(f64, f64, PointValue)],
) -> Result<Vec<Option<[f64; 3]>>, String> {
    let verified = verify_files(manifest_path, "hsd-compact-frame-v1")?;
    let shape = verified
        .shapes
        .get("latitudes")
        .ok_or("HSD band sampling missing latitudes shape")?;
    if shape.len() != 2 || shape[0] == 0 || shape[1] == 0 {
        return Ok(vec![None; targets.len()]);
    }
    let bundle = F32Bundle::<Value>::open(manifest_path, "hsd-compact-frame-v1", &verified.source)?;
    let read_rows = |name: &str| -> Result<Vec<f32>, String> {
        bundle
            .rows::<1>(name, shape)
            .map(|rows| rows.into_iter().map(|[value]| value).collect())
    };
    let latitudes = read_rows("latitudes")?;
    let longitudes = read_rows("longitudes")?;
    let b07 = read_rows("band_b07")?;
    let b13 = read_rows("band_b13")?;
    let b15 = read_rows("band_b15")?;
    bundle.revalidate()?;

    let mut buckets = BTreeMap::<i64, Vec<usize>>::new();
    for (idx, (&lat, &lon)) in latitudes.iter().zip(&longitudes).enumerate() {
        let lat = f64::from(lat);
        let lon = f64::from(lon);
        if lat.is_finite() && lon.is_finite() {
            buckets
                .entry((lat / LATITUDE_BUCKET_DEG).floor() as i64)
                .or_default()
                .push(idx);
        }
    }
    let search_span = (PIXEL_TOLERANCE_KM / 111.0 / LATITUDE_BUCKET_DEG).ceil() as i64 + 1;
    let mut sampled = Vec::with_capacity(targets.len());
    for (_, _, target) in targets {
        let target_bucket = (target.lat / LATITUDE_BUCKET_DEG).floor() as i64;
        let mut best: Option<(usize, f64)> = None;
        for bucket in (target_bucket - search_span)..=(target_bucket + search_span) {
            let Some(indices) = buckets.get(&bucket) else {
                continue;
            };
            for &idx in indices {
                let lat = f64::from(latitudes[idx]);
                let lon = f64::from(longitudes[idx]);
                let dlon = (lon - target.lon + 180.0).rem_euclid(360.0) - 180.0;
                let dist2 = (lat - target.lat).powi(2) + dlon.powi(2);
                if best.is_none_or(|(_, old)| dist2 < old) {
                    best = Some((idx, dist2));
                }
            }
        }
        let reading = best.and_then(|(idx, _)| {
            let nearest = PointValue {
                lat: f64::from(latitudes[idx]),
                lon: f64::from(longitudes[idx]),
            };
            if haversine_km(*target, nearest) > PIXEL_TOLERANCE_KM {
                return None;
            }
            let vals = [
                f64::from(b07[idx]),
                f64::from(b13[idx]),
                f64::from(b15[idx]),
            ];
            vals.iter().all(|v| v.is_finite()).then_some(vals)
        });
        sampled.push(reading);
    }
    Ok(sampled)
}

fn surface_temperature_k(prior: &Prior) -> Option<f64> {
    prior
        .levels
        .iter()
        .filter(|level| level.temperature_k.is_some() && level.height_asl_m >= prior.terrain_asl_m)
        .min_by(|a, b| a.height_asl_m.total_cmp(&b.height_asl_m))
        .and_then(|level| level.temperature_k)
}

fn level_prior_support(level: &Level, rh_floor: f64) -> f64 {
    let cloud = level.model_cloud_fraction.unwrap_or(0.0).clamp(0.0, 1.0);
    let rh = level
        .rh_percent
        .map_or(0.0, |v| ((v - rh_floor) / (100.0 - rh_floor)).clamp(0.0, 1.0));
    cloud.max(rh)
}

/// Refine a single sample's calibrated cloud columns and vertical features using
/// Himawari AHI B13-B15 split-window (thin cirrus) and B07-B13 (warm low-cloud /
/// fog) signatures fused with the same-cycle GFS vertical prior.
///
/// Never fabricates clear sky from unknown/missing pixels and never labels
/// satellite posteriors as sounding or official cloud-height products.
/// Returns `(updated_any, is_thin, is_thick, is_usable)` for horizontal metrics.
fn refine_multispectral_sample(
    columns: &mut [&mut OutputColumn],
    features: &mut Vec<FeatureRecord>,
    prior: &Prior,
    bands: [f64; 3],
    footprint: (f64, f64, f64),
) -> (bool, bool, bool, bool) {
    let [b07, b13, b15] = bands;
    if columns.is_empty() || !b07.is_finite() || !b13.is_finite() || !b15.is_finite() {
        return (false, false, false, false);
    }
    let Some(sfc_temp) = surface_temperature_k(prior) else {
        return (false, false, false, false);
    };
    let btd_split = b13 - b15;
    let btd_swir = b07 - b13;
    let terrain = prior.terrain_asl_m;
    let mut updated = false;

    // 1. Thin cirrus split-window signature (B13 - B15 > 1.5..4.0 K) fused with
    //    upper-tropospheric GFS prior so semi-transparent cirrus is not collapsed
    //    into warm lower levels or dropped below the B13-only threshold.
    let upper_levels: Vec<&Level> = prior
        .levels
        .iter()
        .filter(|level| {
            level.pressure_hpa <= 500
                && level.height_asl_m >= terrain + 4500.0
                && level.temperature_k.is_none_or(|t| t <= 260.0)
        })
        .collect();
    let max_upper_prior = upper_levels
        .iter()
        .map(|level| level_prior_support(level, 45.0))
        .fold(0.0, f64::max);
    let split_signal = ((btd_split - 1.5) / 2.5).clamp(0.0, 1.0);
    let cirrus_supported = split_signal > 0.0
        && (max_upper_prior >= 0.15 || (btd_split >= 2.8 && (sfc_temp - b13) >= 2.0));
    if cirrus_supported && !upper_levels.is_empty() {
        let cirrus_prob = (split_signal * (0.45 + 0.55 * max_upper_prior)).clamp(0.0, 0.85);
        if cirrus_prob >= 0.15 {
            let peak_level = upper_levels
                .iter()
                .max_by(|a, b| {
                    level_prior_support(a, 45.0)
                        .total_cmp(&level_prior_support(b, 45.0))
                        .then_with(|| {
                            (b.height_asl_m - (terrain + 9000.0))
                                .abs()
                                .total_cmp(&(a.height_asl_m - (terrain + 9000.0)).abs())
                        })
                })
                .copied()
                .unwrap();
            let peak_height = peak_level.height_asl_m;
            let primary_col = &mut columns[0];
            let prev_horiz = primary_col.horizontal_cloud_probability.unwrap_or(0.0);
            let new_horiz = prev_horiz.max(cirrus_prob);
            primary_col.horizontal_cloud_probability = Some(new_horiz);
            primary_col.top_asl_m = Some(
                primary_col
                    .top_asl_m
                    .map_or(peak_height, |old| old.max(peak_height)),
            );
            if primary_col.retrieval_quality == "unknown" {
                primary_col.retrieval_quality = if cirrus_prob >= 0.35 && max_upper_prior >= 0.35 {
                    "medium".into()
                } else {
                    "low".into()
                };
            }
            if primary_col.cloud_type == "unknown" || primary_col.cloud_type == "clear" {
                primary_col.cloud_type = "high_thin_cirrus_proxy".into();
            }
            if primary_col.base_source == "none" || primary_col.base_source == "unknown" {
                primary_col.base_source = "hsd-b13-b15-split-window+gfs-upper-prior".into();
            }
            if primary_col.low_cloud_state == "clear" {
                primary_col.low_cloud_state = "unknown".into();
            }
            for level in &upper_levels {
                let vertical =
                    (-0.5 * ((level.height_asl_m - peak_height) / 1800.0).powi(2)).exp();
                let support = level_prior_support(level, 45.0).max(0.22);
                let add_prob = (cirrus_prob * vertical * support).clamp(0.0, new_horiz);
                if add_prob >= 0.08 {
                    let key = level.height_asl_m.to_string();
                    let old_p = primary_col
                        .cloud_probability_by_height
                        .get(&key)
                        .copied()
                        .unwrap_or(0.0);
                    let fused = (1.0 - (1.0 - old_p) * (1.0 - add_prob)).min(new_horiz);
                    primary_col.cloud_probability_by_height.insert(key, fused);
                }
            }
            updated = true;
        }
    }

    // 2. Warm low-cloud / fog contrast (B07 - B13 liquid-water emissivity/solar
    //    signature) fused with low-level GFS prior so low stratus near surface
    //    temperature is not underweighted when B13 alone is close to T_sfc.
    let low_levels: Vec<&Level> = prior
        .levels
        .iter()
        .filter(|level| {
            level.height_asl_m >= terrain && level.height_asl_m - terrain <= 3500.0
        })
        .collect();
    let max_low_prior = low_levels
        .iter()
        .map(|level| level_prior_support(level, 60.0))
        .fold(0.0, f64::max);
    let night_fog_signal = ((-btd_swir - 1.5) / 2.5).clamp(0.0, 1.0);
    let day_stratus_signal = if (sfc_temp - b13) >= -1.0 {
        ((btd_swir - 4.0) / 6.0).clamp(0.0, 1.0)
    } else {
        0.0
    };
    let low_btd_signal = night_fog_signal.max(day_stratus_signal);
    if low_btd_signal > 0.0
        && max_low_prior >= 0.35
        && (sfc_temp - b13) <= 12.0
        && !low_levels.is_empty()
    {
        let low_prob = (low_btd_signal * (0.40 + 0.60 * max_low_prior)).clamp(0.0, 0.85);
        if low_prob >= 0.20 {
            let peak_low = low_levels
                .iter()
                .max_by(|a, b| {
                    level_prior_support(a, 60.0).total_cmp(&level_prior_support(b, 60.0))
                })
                .copied()
                .unwrap();
            let peak_height = peak_low.height_asl_m;
            let primary_col = &mut columns[0];
            let prev_horiz = primary_col.horizontal_cloud_probability.unwrap_or(0.0);
            let new_horiz = prev_horiz.max(low_prob);
            primary_col.horizontal_cloud_probability = Some(new_horiz);
            primary_col.top_asl_m = Some(
                primary_col
                    .top_asl_m
                    .map_or(peak_height, |old| old.max(peak_height)),
            );
            if primary_col.retrieval_quality == "unknown" {
                primary_col.retrieval_quality = if low_prob >= 0.45 && max_low_prior >= 0.55 {
                    "medium".into()
                } else {
                    "low".into()
                };
            }
            if low_prob >= 0.50 && max_low_prior >= 0.55 && primary_col.low_cloud_state != "detected"
            {
                primary_col.low_cloud_state = "detected".into();
            } else if primary_col.low_cloud_state == "clear" {
                primary_col.low_cloud_state = "unknown".into();
            }
            if primary_col.base_source == "none" || primary_col.base_source == "unknown" {
                primary_col.base_source = "hsd-b07-b13-low-contrast+gfs-low-prior".into();
            }
            for level in &low_levels {
                let vertical =
                    (-0.5 * ((level.height_asl_m - peak_height) / 950.0).powi(2)).exp();
                let support = level_prior_support(level, 60.0).max(0.25);
                let add_prob = (low_prob * vertical * support).clamp(0.0, new_horiz);
                if add_prob >= 0.08 {
                    let key = level.height_asl_m.to_string();
                    let old_p = primary_col
                        .cloud_probability_by_height
                        .get(&key)
                        .copied()
                        .unwrap_or(0.0);
                    let fused = (1.0 - (1.0 - old_p) * (1.0 - add_prob)).min(new_horiz);
                    primary_col.cloud_probability_by_height.insert(key, fused);
                }
            }
            updated = true;
        }
    }

    // Keep feature records synchronized with refined cloud_probability_by_height
    // so both satellite_vertical_profile and observed_features_by_offset see
    // the multispectral refinement.
    if updated {
        let primary_col = &columns[0];
        let horizontal = columns
            .iter()
            .filter_map(|col| col.horizontal_cloud_probability)
            .filter(|v| v.is_finite())
            .fold(0.0, f64::max);
        if horizontal > 0.0 {
            let round_lat = (primary_col.lat * 100_000.0).round() / 100_000.0;
            let round_lon = (primary_col.lon * 100_000.0).round() / 100_000.0;
            let round_dist = (primary_col.distance_km * 100.0).round() / 100.0;
            let round_bearing = (primary_col.bearing_deg * 100.0).round() / 100.0;
            let sample_ckey = column_key(round_lat, round_lon, round_dist, round_bearing);
            for level in &prior.levels {
                let mut merged_p = 0.0;
                for col in columns.iter() {
                    let p = col
                        .cloud_probability_by_height
                        .get(&level.height_asl_m.to_string())
                        .copied()
                        .unwrap_or(0.0);
                    if p >= 0.08 {
                        merged_p = 1.0 - (1.0 - merged_p) * (1.0 - p);
                    }
                }
                if merged_p < 0.08 {
                    continue;
                }
                let conditional = (merged_p / horizontal.max(1e-9)).clamp(0.0, 1.0);
                if let Some(existing) = features.iter_mut().find(|f| {
                    column_key(f.lat, f.lon, f.distance_km, f.bearing_deg) == sample_ckey
                        && f.pressure_hpa == level.pressure_hpa
                }) {
                    existing.cloud_fraction_percent = Some(conditional * 100.0);
                    existing.horizontal_cloud_probability = Some(horizontal);
                    existing.cloud_probability_given_cloud = Some(conditional);
                } else {
                    features.push(FeatureRecord {
                        lat: round_lat,
                        lon: round_lon,
                        distance_km: round_dist,
                        bearing_deg: round_bearing,
                        pressure_hpa: level.pressure_hpa,
                        geopotential_height_m: level.height_asl_m,
                        rh_percent: level.rh_percent.unwrap_or(70.0),
                        temperature_k: level.temperature_k,
                        omega_pa_s: None,
                        u_ms: None,
                        v_ms: None,
                        cloud_fraction_percent: Some(conditional * 100.0),
                        cloud_liquid_kg_kg: None,
                        cloud_ice_kg_kg: None,
                        source_lat: None,
                        source_lon: None,
                        source_distance_km: None,
                        source_bearing_deg: None,
                        surface_visibility_m: None,
                        precip_rate_kg_m2_s: None,
                        surface_temperature_k: None,
                        surface_dewpoint_k: None,
                        footprint_azimuth_width_deg: Some(footprint.0),
                        footprint_near_distance_km: Some(footprint.1),
                        footprint_far_distance_km: Some(footprint.2),
                        horizontal_cloud_probability: Some(horizontal),
                        cloud_probability_given_cloud: Some(conditional),
                    });
                }
            }
        }
    }

    let usable = columns.iter().any(|c| c.retrieval_quality != "unknown");
    let horiz = columns
        .iter()
        .filter_map(|c| c.horizontal_cloud_probability)
        .filter(|v| v.is_finite())
        .fold(0.0, f64::max);
    let is_thin = usable && horiz >= 0.20 && (btd_split >= 1.5 || (horiz < 0.60 && btd_split >= 1.0));
    let is_thick = usable && horiz >= 0.65 && btd_split < 1.2;
    (updated, is_thin, is_thick, usable)
}

#[allow(clippy::too_many_arguments)]
fn refine_multispectral_frame(
    column_sets: &mut [ColumnSetRecord],
    features: &mut Vec<FeatureRecord>,
    metrics: &mut HorizontalMetrics,
    sample_points: &[(f64, f64)],
    priors: &[Option<Prior>],
    bands: &[Option<[f64; 3]>],
    azimuth_deg: f64,
    radius_km: f64,
) {
    let mut distances: Vec<f64> = sample_points.iter().map(|(_, d)| *d).collect();
    distances.sort_by(f64::total_cmp);
    distances.dedup_by(|a, b| *a == *b);

    let mut any_updated = false;
    let mut thin_count = 0usize;
    let mut thick_count = 0usize;
    let mut usable_count = 0usize;

    for (idx, &(bearing_deg, distance_km)) in sample_points.iter().enumerate() {
        let Some(Some(prior)) = priors.get(idx) else {
            continue;
        };
        let Some(Some(band_vals)) = bands.get(idx) else {
            continue;
        };
        let dist_key = rounded_scaled(distance_km, 100.0);
        let bearing_key = rounded_scaled(bearing_deg.rem_euclid(360.0), 100.0);
        let d_idx = distances
            .binary_search_by(|v| v.total_cmp(&distance_km))
            .unwrap_or(0);
        let prev_d = if d_idx == 0 { 0.0 } else { distances[d_idx - 1] };
        let next_d = distances.get(d_idx + 1).copied().unwrap_or(radius_km);
        let near = if d_idx == 0 {
            0.0
        } else {
            0.5 * (prev_d + distance_km)
        };
        let far = radius_km.min(if d_idx + 1 == distances.len() {
            distance_km
        } else {
            0.5 * (distance_km + next_d)
        });
        let width = if distance_km <= 120.0 { 15.0 } else { 14.0 };
        let footprint = (width, near, near.max(far));

        let mut matched: Vec<&mut OutputColumn> = column_sets
            .iter_mut()
            .flat_map(|set| set.columns.iter_mut())
            .filter(|col| {
                rounded_scaled(col.distance_km, 100.0) == dist_key
                    && rounded_scaled(col.bearing_deg.rem_euclid(360.0), 100.0) == bearing_key
            })
            .collect();
        if matched.is_empty() {
            continue;
        }
        let (updated, is_thin, is_thick, usable) =
            refine_multispectral_sample(&mut matched, features, prior, *band_vals, footprint);
        any_updated |= updated;
        if usable {
            usable_count += 1;
            if is_thin {
                thin_count += 1;
            }
            if is_thick {
                thick_count += 1;
            }
        }
    }

    if any_updated {
        for set in column_sets.iter_mut() {
            set.retrieval_quality = aggregate_quality(
                set.columns.iter().map(|c| c.retrieval_quality.as_str()),
                set.columns.len(),
            );
            set.low_cloud_state = low_state(
                set.columns
                    .iter()
                    .filter(|c| c.distance_km <= 120.0)
                    .map(|c| c.low_cloud_state.as_str()),
            );
        }
        let grouped: Vec<Vec<OutputColumn>> = column_sets
            .iter()
            .map(|set| set.columns.clone())
            .collect();
        *metrics = horizontal_metrics(&grouped, azimuth_deg);
    }
    if usable_count > 0 {
        metrics.thin_cloud_ratio = (thin_count as f64 / usable_count as f64).clamp(0.0, 1.0);
        metrics.thick_cloud_ratio = (thick_count as f64 / usable_count as f64).clamp(0.0, 1.0);
    }
}

/// Physically grounded Lagrangian trend score in `[0.0, 1.0]` combining:
/// 1. `lagrangian_b13_trend_k_per_hour` (regime-aware: moderate cooling favors
///    thin/mid-high canvas arrival when sky is open, while warming favors
///    clearing/thinning when sky is overcast or horizon-blocked; rapid warming
///    of sparse clouds or thickening of overcast scores `< 0.5`), and
/// 2. Multi-frame cloud-ratio evolution across scoring frames (near-horizon
///    clearing and far-cloud convergence toward the target band score `> 0.5`).
/// Falls back cleanly to `0.5` when single-frame and no Lagrangian rate is given.
fn compute_trend_score(
    request: &AdjustmentRequest,
    results: &[RetrievedFrame],
    primary: &RetrievedFrame,
) -> f64 {
    let m = &primary.metrics;
    let overcast_regime = ((m.local_cloud_ratio.max(m.cloud_blanket_score) - 0.55) / 0.25)
        .clamp(0.0, 1.0);
    let horizon_blocking_regime = ((m.horizon_cloud_ratio - 0.55) / 0.25).clamp(0.0, 1.0);
    let blocking_regime = overcast_regime.max(horizon_blocking_regime);

    let b13_delta = request
        .lagrangian_b13_trend_k_per_hour
        .filter(|rate| rate.is_finite())
        .map(|rate| {
            let sparse_signal = if rate <= 0.0 {
                let cooling = (-rate / 6.0).tanh() * 0.35;
                let convective_blowup = ((-rate - 10.0) / 10.0).clamp(0.0, 1.0) * 0.55;
                cooling - convective_blowup
            } else {
                -(rate / 6.0).tanh() * 0.35
            };
            let overcast_signal = (rate / 6.0).tanh() * 0.38;
            (1.0 - blocking_regime) * sparse_signal + blocking_regime * overcast_signal
        });

    let frame_delta = if results.len() >= 2 {
        let earliest = results.iter().min_by_key(|frame| frame.valid_time);
        let latest = results.iter().max_by_key(|frame| frame.valid_time);
        match (earliest, latest) {
            (Some(first), Some(last)) => {
                let dt_hours =
                    (last.valid_time - first.valid_time).num_seconds() as f64 / 3600.0;
                if dt_hours >= 1.0 / 60.0 {
                    let d_horizon_per_h =
                        (last.metrics.horizon_cloud_ratio - first.metrics.horizon_cloud_ratio)
                            / dt_hours;
                    let horizon_term = (-d_horizon_per_h / 0.40).tanh() * 0.22;

                    let before_err = (first.metrics.far_projected - 0.42).abs();
                    let after_err = (last.metrics.far_projected - 0.42).abs();
                    let target_improvement_per_h = (before_err - after_err) / dt_hours;
                    let far_term = (target_improvement_per_h / 0.35).tanh() * 0.20;

                    let d_local_per_h =
                        (last.metrics.local_cloud_ratio - first.metrics.local_cloud_ratio)
                            / dt_hours;
                    let blanket_term =
                        if last.metrics.local_cloud_ratio > 0.72 && d_local_per_h > 0.0 {
                            -(d_local_per_h / 0.40).tanh() * 0.12
                        } else {
                            0.0
                        };
                    Some((horizon_term + far_term + blanket_term).clamp(-0.45, 0.45))
                } else {
                    None
                }
            }
            _ => None,
        }
    } else {
        None
    };

    let delta = match (b13_delta, frame_delta) {
        (Some(b13), Some(frame_d)) => 0.65 * b13 + 0.35 * frame_d,
        (Some(b13), None) => b13,
        (None, Some(frame_d)) => frame_d,
        (None, None) => 0.0,
    };
    (0.5 + delta).clamp(0.0, 1.0)
}

fn run_frame(
    frame: &Frame,
    request: &AdjustmentRequest,
    target: DateTime<Utc>,
    motion_dx: f64,
    motion_dy: f64,
) -> Result<RetrievedFrame, String> {
    let valid_time = parse_utc(&frame.valid_time_utc, "frame.valid_time_utc")?;
    let params = RetrievalParams {
        manifest_path: frame.manifest_path.clone(),
        valid_time_utc: frame.valid_time_utc.clone(),
        point_lat: request.point_lat,
        point_lon: request.point_lon,
        azimuth_deg: request.azimuth_deg,
        radius_km: request.radius_km,
        motion_dx_px_per_hour: motion_dx,
        motion_dy_px_per_hour: motion_dy,
        cycle_time_utc: frame.retrieval.cycle_time_utc.clone(),
        left_forecast_hour: frame.retrieval.left_forecast_hour,
        right_forecast_hour: frame.retrieval.right_forecast_hour,
        source: "gfs-interpolated-vertical-prior".into(),
        fraction: frame.retrieval.fraction,
        local_radius_km: frame.retrieval.local_radius_km,
        left_cache_path: frame.retrieval.left_cache_path.clone(),
        right_cache_path: frame.retrieval.right_cache_path.clone(),
        domain_hash: frame.retrieval.domain_hash.clone(),
        resolution: frame.retrieval.resolution.clone(),
        output_path: None,
    };
    let mut result = execute_retrieval(&params)?;
    let origin = PointValue {
        lat: request.point_lat,
        lon: request.point_lon,
    };
    let targets = sample_targets(&result.sample_points, origin);
    if let Ok(bands) = sample_hsd_bands(&frame.manifest_path, &targets) {
        refine_multispectral_frame(
            &mut result.column_sets,
            &mut result.features,
            &mut result.metrics,
            &result.sample_points,
            &result.priors,
            &bands,
            request.azimuth_deg,
            request.radius_km,
        );
    }
    let offset_minutes = timeline_offset_minutes(valid_time, target);
    Ok(RetrievedFrame {
        valid_time,
        offset_minutes,
        features: result.features,
        // execute_retrieval already stamps the effective motion on every set
        // and column. Cloning the complete nested result only to repeat that
        // assignment multiplied memory traffic for every additional scan.
        column_sets: result.column_sets,
        metrics: result.metrics,
        priors: result.priors,
        sample_points: result.sample_points,
    })
}

fn build_profile(
    sets: &[ColumnSetRecord],
    records: &[FeatureRecord],
    samples: &[(f64, f64)],
    priors: &[Option<Prior>],
    point: PointValue,
    valid_time: DateTime<Utc>,
) -> Result<Value, String> {
    let mut feature_map = HashMap::<(i64, i64, i64, i64, i64), &FeatureRecord>::new();
    let mut pressures = BTreeSet::<i64>::new();
    for feature in records {
        let key = coordinate_key(
            feature.lat,
            feature.lon,
            feature.distance_km,
            feature.bearing_deg,
            feature.pressure_hpa,
        );
        feature_map.insert(key, feature);
        pressures.insert(feature.pressure_hpa);
    }
    let targets = sample_targets(samples, point);
    let prior_map: HashMap<(i64, i64), &Prior> = targets
        .iter()
        .zip(priors)
        .filter_map(|((_, _, point), prior)| {
            prior.as_ref().map(|item| {
                (
                    (
                        rounded_scaled(point.lat, 100_000.0),
                        rounded_scaled(point.lon, 100_000.0),
                    ),
                    item,
                )
            })
        })
        .collect();
    for prior in prior_map.values() {
        for level in &prior.levels {
            pressures.insert(level.pressure_hpa);
        }
    }
    let mut by_sample = HashMap::<(i64, i64, i64, i64), &OutputColumn>::new();
    for column in sets.iter().flat_map(|set| set.columns.iter()) {
        let key = column_key(
            column.lat,
            column.lon,
            column.distance_km,
            column.bearing_deg,
        );
        let replace = by_sample.get(&key).is_none_or(|current| {
            quality_rank(&column.retrieval_quality) > quality_rank(&current.retrieval_quality)
        });
        if replace {
            by_sample.insert(key, column);
        }
    }
    let mut ordered: Vec<_> = by_sample.into_iter().collect();
    ordered.sort_by(|left, right| {
        left.1
            .distance_km
            .total_cmp(&right.1.distance_km)
            .then(left.1.bearing_deg.total_cmp(&right.1.bearing_deg))
    });
    let mut encoded_samples = Vec::new();
    let mut usable_count = 0usize;
    for (key, column) in ordered {
        let prior = prior_map.get(&(key.0, key.1)).copied();
        let usable = sample_usable(column)
            && prior.is_some_and(|value| {
                value.identity != "unknown" && value.source.starts_with("gfs")
            });
        if usable {
            usable_count += 1;
        }
        let mut levels = Vec::new();
        for pressure in pressures.iter().rev() {
            let feature = feature_map.get(&coordinate_key(
                key.0 as f64 / 100_000.0,
                key.1 as f64 / 100_000.0,
                key.2 as f64 / 100.0,
                key.3 as f64 / 100.0,
                *pressure,
            ));
            let prior_level = prior.and_then(|prior| {
                prior
                    .levels
                    .iter()
                    .find(|level| level.pressure_hpa == *pressure)
            });
            let posterior = if usable {
                feature.and_then(|feature| feature.cloud_fraction_percent)
            } else {
                None
            };
            let horizontal = if usable {
                feature.and_then(|feature| feature.horizontal_cloud_probability)
            } else {
                None
            };
            levels.push(json!({
                "pressure_hpa": pressure,
                "height_m": prior_level.map(|level| level.height_asl_m),
                "rh_percent": prior_level.and_then(|level| level.rh_percent),
                "temperature_c": prior_level.and_then(|level| level.temperature_k.map(|value| value - 273.15)),
                "model_cloud_fraction_percent": prior_level.and_then(|level| level.model_cloud_fraction.map(|value| value * 100.0)),
                "model_cloud_fraction": prior_level.and_then(|level| level.model_cloud_fraction),
                "hsd_posterior_percent": posterior,
                "hsd_posterior": posterior.map(|value| value / 100.0),
                "cloud_probability_percent": posterior,
                "horizontal_cloud_probability": horizontal,
                "status": if posterior.is_some() { "retrieved" } else { "unknown" },
            }));
        }
        encoded_samples.push(json!({
            "distance_km": column.distance_km,
            "bearing_deg": column.bearing_deg,
            "status": if usable { "retrieved" } else { "unknown" },
            "retrieval_quality": column.retrieval_quality,
            "vertical_prior_identity": prior.map_or(column.vertical_prior_identity.as_str(), |value| value.identity.as_str()),
            "vertical_prior_source": prior.map_or(column.vertical_prior_source.as_str(), |value| value.source.as_str()),
            "terrain_elevation_asl_m": prior.map(|value| value.terrain_asl_m),
            "levels": levels,
        }));
    }
    let denominator = samples.len().max(encoded_samples.len()).max(1);
    let coverage = usable_count as f64 / denominator as f64;
    Ok(json!({
        "schema_version": 1,
        "status": if encoded_samples.is_empty() { "unavailable" } else if usable_count < denominator { "partial_unknown" } else { "available" },
        "source": "Himawari HSD B07/B13/B15 calibrated posterior + same-cycle GFS vertical prior",
        "valid_time_utc": time_contract::iso(valid_time),
        "bands": ["B07", "B13", "B15"],
        "prior_source": "gfs",
        "sample_count": encoded_samples.len(),
        "usable_sample_count": usable_count,
        "unknown_sample_count": encoded_samples.len().saturating_sub(usable_count),
        "coverage": coverage,
        "numeric_clear_sky_allowed": false,
        "pressure_levels_hpa": pressures.iter().rev().collect::<Vec<_>>(),
        "samples": encoded_samples,
    }))
}

fn column_key(lat: f64, lon: f64, distance: f64, bearing: f64) -> (i64, i64, i64, i64) {
    (
        rounded_scaled(lat, 100_000.0),
        rounded_scaled(lon, 100_000.0),
        rounded_scaled(distance, 100.0),
        rounded_scaled(bearing, 100.0),
    )
}

fn coordinate_key(
    lat: f64,
    lon: f64,
    distance: f64,
    bearing: f64,
    pressure: i64,
) -> (i64, i64, i64, i64, i64) {
    let key = column_key(lat, lon, distance, bearing);
    (key.0, key.1, key.2, key.3, pressure)
}

fn quality_rank(value: &str) -> u8 {
    match value {
        "low" => 1,
        "medium" => 2,
        "high" => 3,
        _ => 0,
    }
}

#[derive(Clone, Copy)]
struct PointValue {
    lat: f64,
    lon: f64,
}

fn destination(origin: PointValue, bearing: f64, distance: f64) -> PointValue {
    let angular = distance / 6371.0;
    let bearing = bearing.to_radians();
    let lat1 = origin.lat.to_radians();
    let lon1 = origin.lon.to_radians();
    let lat2 = (lat1.sin() * angular.cos() + lat1.cos() * angular.sin() * bearing.cos()).asin();
    let lon2 = lon1
        + (bearing.sin() * angular.sin() * lat1.cos())
            .atan2(angular.cos() - lat1.sin() * lat2.sin());
    let lon = (lon2.to_degrees() + 180.0).rem_euclid(360.0) - 180.0;
    PointValue {
        lat: lat2.to_degrees(),
        lon: if lon == -180.0 { 180.0 } else { lon },
    }
}

fn sample_targets(samples: &[(f64, f64)], point: PointValue) -> Vec<(f64, f64, PointValue)> {
    samples
        .iter()
        .map(|(bearing, distance)| (*bearing, *distance, destination(point, *bearing, *distance)))
        .collect()
}

fn relative_seconds(start: DateTime<Utc>, target: DateTime<Utc>) -> i64 {
    (start - target).num_seconds()
}

fn timeline_offset_minutes(valid_time: DateTime<Utc>, target: DateTime<Utc>) -> i64 {
    (relative_seconds(valid_time, target) as f64 / 300.0).round_ties_even() as i64 * 5
}

fn select_primary<T>(
    frames: &[(DateTime<Utc>, T)],
    target: DateTime<Utc>,
) -> Option<DateTime<Utc>> {
    frames
        .iter()
        .map(|(valid, _)| *valid)
        .min_by_key(|valid| relative_seconds(*valid, target).abs())
}

fn select_scoring_frames(
    frames: &[Frame],
    target: DateTime<Utc>,
) -> Result<Vec<(DateTime<Utc>, &Frame)>, String> {
    let mut seen = BTreeSet::new();
    let mut candidates = Vec::new();
    for item in frames {
        let valid = parse_utc(&item.valid_time_utc, "frame.valid_time_utc")?;
        // The Python provider may surface the same scan through both raw and
        // compact cache views. One UTC instant can contribute only one frame.
        if seen.insert(valid) {
            candidates.push((valid, item));
        }
    }
    let mut scoring: Vec<_> = candidates
        .into_iter()
        .filter(|(valid, _)| {
            let offset = timeline_offset_minutes(*valid, target);
            (-30..=30).contains(&offset)
        })
        .collect();
    scoring.sort_by_key(|(valid, _)| *valid);
    if scoring.is_empty() {
        return Err(
            "Himawari calibrated scan is outside the supported five-minute nowcast window".into(),
        );
    }
    Ok(scoring)
}

fn validate_timeline_offsets(results: &[RetrievedFrame]) -> Result<(), String> {
    let mut offsets = BTreeMap::new();
    for result in results {
        if let Some(previous) = offsets.insert(result.offset_minutes, result.valid_time) {
            if previous != result.valid_time {
                return Err("independent HSD scans collapse to one nowcast timeline offset".into());
            }
        }
    }
    Ok(())
}

fn require_usable_coverage(coverage: f64) -> Result<(), String> {
    if coverage <= 0.0 {
        return Err("nowcast has no usable calibrated sample coverage".into());
    }
    Ok(())
}

fn effective_motion(request: &AdjustmentRequest) -> (f64, f64) {
    if request.motion_quality == "strong" {
        (request.motion_dx_px_per_hour, request.motion_dy_px_per_hour)
    } else {
        (0.0, 0.0)
    }
}

fn parallel_map_ordered<T, R, F>(
    items: &[T],
    workers: usize,
    operation: F,
) -> Result<Vec<R>, String>
where
    T: Sync,
    R: Send,
    F: Fn(&T) -> Result<R, String> + Sync,
{
    if items.is_empty() {
        return Ok(Vec::new());
    }
    let workers = workers.clamp(1, items.len());
    if workers == 1 || items.len() == 1 {
        return items.iter().map(operation).collect();
    }
    let mut output = Vec::with_capacity(items.len());
    for batch in items.chunks(workers) {
        let values = std::thread::scope(|scope| {
            let handles = batch
                .iter()
                .map(|item| scope.spawn(|| operation(item)))
                .collect::<Vec<_>>();
            let mut values = Vec::with_capacity(handles.len());
            for handle in handles {
                values.push(
                    handle
                        .join()
                        .map_err(|_| "nowcast frame retrieval worker panicked")??,
                );
            }
            Ok::<Vec<R>, String>(values)
        })?;
        output.extend(values);
    }
    Ok(output)
}

fn build_result(request: AdjustmentRequest) -> Result<Value, String> {
    let target = parse_utc(&request.target_time_utc, "target_time_utc")?;
    if !request.point_lat.is_finite()
        || !(-90.0..=90.0).contains(&request.point_lat)
        || !request.point_lon.is_finite()
        || !(-180.0..=180.0).contains(&request.point_lon)
        || !request.radius_km.is_finite()
        || request.radius_km <= 0.0
        || !request.azimuth_deg.is_finite()
        || request.frames.is_empty()
        || request.frames.len() > MAX_FRAMES
        || request.timezone_name.len() > 128
        || request.timezone_name.parse::<chrono_tz::Tz>().is_err()
        || !request.motion_correlation.is_finite()
        || !(0.0..=1.0).contains(&request.motion_correlation)
        || !matches!(request.motion_quality.as_str(), "strong" | "unknown")
        || request
            .motion_age_minutes
            .is_some_and(|value| !value.is_finite() || value <= 0.0)
        || request
            .lagrangian_b13_trend_k_per_hour
            .is_some_and(|value| !value.is_finite())
        || request.advection.as_ref().is_some_and(|plan| {
            !plan.horizon_hours.is_finite()
                || plan.horizon_hours < 0.0
                || !plan.scale.is_finite()
                || !(0.0..=1.0).contains(&plan.scale)
                || plan.applied && plan.scale <= 0.0
                || !matches!(
                    plan.motion_quality.as_str(),
                    "observation" | "strong" | "decayed" | "unknown" | "diagnostic-only"
                )
                || plan
                    .motion_age_minutes
                    .is_some_and(|value| !value.is_finite() || value <= 0.0)
        })
    {
        return Err("nowcast adjustment request is invalid".into());
    }
    let display_valid = parse_utc(
        &request.display_frame.valid_time_utc,
        "display_frame.valid_time_utc",
    )?;
    let display_contract = frame(&request.display_frame.manifest_path)?;
    if display_contract.valid_time != display_valid {
        return Err("nowcast display frame time does not match its manifest".into());
    }
    let scoring = select_scoring_frames(&request.frames, target)?;
    // Python walks the UTC-sorted scans and replaces the primary only on a
    // strictly shorter distance, so an exact tie keeps the earlier scan.
    let primary_valid = select_primary(&scoring, target).ok_or("nowcast has no scoring frames")?;
    let (effective_motion_dx, effective_motion_dy) = effective_motion(&request);
    let workers = std::thread::available_parallelism()
        .map_or(1, std::num::NonZeroUsize::get)
        .min(MAX_FRAME_WORKERS);
    let results = parallel_map_ordered(&scoring, workers, |(_, frame)| {
        run_frame(
            frame,
            &request,
            target,
            effective_motion_dx,
            effective_motion_dy,
        )
    })?;
    validate_timeline_offsets(&results)?;
    let primary = results
        .iter()
        .find(|result| result.valid_time == primary_valid)
        .ok_or("nowcast primary frame was not retrieved")?;
    let coverage = calibrated_coverage(&primary.column_sets, &primary.sample_points);
    require_usable_coverage(coverage)?;
    assemble_result(
        &request,
        target,
        display_valid,
        &display_contract,
        primary_valid,
        &results,
        coverage,
    )
}

#[allow(clippy::too_many_arguments)]
fn assemble_result(
    request: &AdjustmentRequest,
    target: DateTime<Utc>,
    display_valid: DateTime<Utc>,
    display_contract: &FrameContract,
    primary_valid: DateTime<Utc>,
    results: &[RetrievedFrame],
    coverage: f64,
) -> Result<Value, String> {
    let (effective_motion_dx, effective_motion_dy) = effective_motion(request);
    let primary = results
        .iter()
        .find(|result| result.valid_time == primary_valid)
        .ok_or("nowcast primary frame was not retrieved")?;
    let all_sets: Vec<&ColumnSetRecord> = results
        .iter()
        .flat_map(|result| result.column_sets.iter())
        .collect();
    let identity = aggregate_identity(all_sets.iter().copied());
    let source = aggregate_source(all_sets.iter().copied());
    let quality = aggregate_quality(
        all_sets.iter().map(|set| set.retrieval_quality.as_str()),
        all_sets.len(),
    );
    let low = low_state(all_sets.iter().flat_map(|set| {
        set.columns
            .iter()
            .filter(|column| column.distance_km <= 120.0)
            .map(|column| column.low_cloud_state.as_str())
    }));
    let mut observed_features = BTreeMap::new();
    let mut observed_times = BTreeMap::new();
    for result in results {
        observed_features.insert(result.offset_minutes.to_string(), result.features.clone());
        observed_times.insert(
            result.offset_minutes.to_string(),
            time_contract::iso(result.valid_time),
        );
    }
    let mut source_times = BTreeSet::new();
    for item in &request.source_times_utc {
        source_times.insert(parse_utc(item, "source_times_utc")?);
    }
    if source_times.is_empty() {
        source_times.insert(display_valid);
    }
    let source_times: Vec<_> = source_times.into_iter().rev().collect();
    let sample_points = primary.sample_points.clone();
    let profile_value = build_profile(
        &primary.column_sets,
        &primary.features,
        &sample_points,
        &primary.priors,
        PointValue {
            lat: request.point_lat,
            lon: request.point_lon,
        },
        display_valid,
    )?;
    let metrics = &primary.metrics;
    let near = metrics.near_projected;
    let far = metrics.far_projected;
    let local = metrics.local_cloud_ratio;
    let horizon = metrics.horizon_cloud_ratio;
    let thin = metrics.thin_cloud_ratio;
    let thick = metrics.thick_cloud_ratio;
    let texture = metrics.cloud_texture_score;
    let uniformity = metrics.cloud_uniformity_score;
    let blanket = metrics.cloud_blanket_score;
    let clearance = metrics.horizon_clearance_score;
    let composition = metrics.composition_score;
    let confidence_factor = request
        .advection
        .as_ref()
        .filter(|plan| plan.motion_quality == "decayed")
        .map_or(1.0, |plan| 0.55 + 0.45 * plan.scale);
    let historical_decay = if request.single_historical_observation {
        (-request
            .advection
            .as_ref()
            .map_or(0.0, |plan| plan.horizon_hours)
            * 60.0
            / 45.0)
            .exp()
    } else {
        1.0
    };
    let confidence = (0.62
        * (0.55 + 0.45 * cloud_column_coverage(all_sets.iter().copied(), target))
        * confidence_factor
        * motion_reliability_factor(request)
        * historical_decay)
        .clamp(0.0, 1.0);
    let observed_time = source_times.first().copied().unwrap_or(display_valid);
    let mut summary = vec![
        format!(
            "NOAA Himawari {} 校准观测时次 {}；HSD 使用 B07/B13/B15 校准观测；同周期 GFS 只提供模式垂直先验，不把结果标为探空或官方云高/云相产品。",
            SOURCE_KIND.to_uppercase(),
            observed_time.format("%Y-%m-%d %H:%MZ")
        ),
        format!(
            "分段缓存下载 {:.1} MiB、{} 个校准通道分段；在线评分只读取本地紧凑缓存。",
            display_contract.range_bytes as f64 / 1024.0 / 1024.0,
            display_contract.range_blocks
        ),
    ];
    if display_valid != observed_time {
        summary[0].push_str(&format!(
            " 平流后的有效云场时次 {}。",
            display_valid.format("%Y-%m-%d %H:%MZ")
        ));
    }
    if let Some(advection) = &request.advection {
        let source_text = source_times
            .iter()
            .map(|value| value.format("%H:%MZ").to_string())
            .collect::<Vec<_>>()
            .join(", ");
        let source_text = if source_text.is_empty() {
            display_valid.format("%H:%MZ").to_string()
        } else {
            source_text
        };
        let motion_age = advection
            .motion_age_minutes
            .map_or_else(String::new, |value| format!("，运动年龄 {value:.1} 分钟"));
        summary.push(format!(
            "{} 来源时次 [{}]；运动质量 {}{}。",
            SOURCE_KIND.to_uppercase(),
            source_text,
            advection.motion_quality,
            motion_age
        ));
        if request.single_historical_observation {
            summary.push(format!(
                "历史事件只获得一帧完整校准 {} 扫描（距事件 {:.0} 分钟）；按事件前最近观测直接评分，不进行云场运动外推。",
                SOURCE_KIND.to_uppercase(),
                advection.horizon_hours * 60.0
            ));
        } else {
            summary.push(format!(
                "目标时次外推 {:.2} 小时，计划 {}；超过 4 小时仅保留实况诊断。",
                advection.horizon_hours, advection.reason
            ));
        }
    }
    if low == "detected" {
        summary.push("校准多光谱与 GFS 先验共同支持观测者附近低云。".into());
    } else if low == "unknown" {
        summary.push("低云状态仍不确定；没有把缺测或暖低云歧义回填为晴空。".into());
    }
    summary.push(format!(
        "校准采样覆盖率 {:.0}/100：{}",
        coverage * 100.0,
        if coverage >= 0.95 {
            "全视图可用，按满覆盖计分。"
        } else {
            "部分采样模糊（拂晓/晨昏分类歧义或云边），未覆盖样本按 unknown 处理、不参与评分。"
        }
    ));
    let source_times_text = source_times
        .iter()
        .map(|value| time_contract::iso(*value))
        .collect::<Vec<_>>()
        .join(",");
    let cache_identity = format!(
        "{}|point={:+.5},{:+.5}|radius={:.3}|valid={}|source={}|vertical-prior={}|source-times={}",
        NOWCAST_ALGORITHM,
        request.point_lat,
        request.point_lon,
        request.radius_km,
        time_contract::iso(display_valid),
        display_contract.source,
        identity,
        source_times_text
    );
    let zone: chrono_tz::Tz = request
        .timezone_name
        .parse()
        .map_err(|_| "timezone_name is invalid")?;
    let all_set_values = all_sets
        .iter()
        .map(|set| column_set_value(set))
        .collect::<Vec<_>>();
    let advection_quality = request
        .advection
        .as_ref()
        .map_or_else(|| "unknown".to_string(), |plan| plan.motion_quality.clone());
    let trend_score = compute_trend_score(request, results, primary);
    serde_json::to_value(AdjustmentOutput {
        contract: "rust-desktop-nowcast-adjustment-v1",
        runtime_python_used: false,
        target_time_utc: time_contract::iso(target),
        nowcast_algorithm: NOWCAST_ALGORITHM,
        source: SOURCE_LABEL,
        image_valid_time_utc: time_contract::iso(display_valid),
        image_valid_time_local: crate::astronomy::local_iso(display_valid, zone)?,
        image_channel: IMAGE_CHANNEL,
        image_area: IMAGE_AREA,
        near_cloud_ratio: near,
        far_cloud_ratio: far,
        trend_score,
        visibility_penalty: (1.0 - clearance).clamp(0.0, 1.0),
        target_bonus: (1.0 - (far - 0.42).abs() / 0.42).clamp(0.0, 1.0),
        confidence,
        summary,
        motion_dx_px_per_hour: effective_motion_dx,
        motion_dy_px_per_hour: effective_motion_dy,
        motion_correlation: request.motion_correlation,
        projection_hours: 0.0,
        satellite_source_times_utc: source_times
            .iter()
            .map(|value| time_contract::iso(*value))
            .collect(),
        satellite_motion_age_minutes: request.motion_age_minutes,
        satellite_motion_quality: advection_quality,
        satellite_advection_horizon_hours: request
            .advection
            .as_ref()
            .map_or(0.0, |plan| plan.horizon_hours),
        infrared_near_cloud_ratio: Some(near),
        infrared_far_cloud_ratio: Some(far),
        visible_near_cloud_ratio: None,
        visible_far_cloud_ratio: None,
        channel_fusion_weight_visible: 0.0,
        local_cloud_ratio: local,
        horizon_cloud_ratio: horizon,
        thin_cloud_ratio: thin,
        thick_cloud_ratio: thick,
        cloud_texture_score: texture,
        cloud_uniformity_score: uniformity,
        cloud_blanket_score: blanket,
        horizon_clearance_score: clearance,
        composition_score: composition,
        observed_features_by_offset: observed_features,
        observed_feature_times_by_offset: observed_times,
        cloud_column_schema_version: CLOUD_COLUMN_SCHEMA_VERSION,
        cloud_column_sets: all_set_values,
        vertical_prior_identity: identity,
        vertical_prior_source: source,
        satellite_retrieval_quality: quality,
        satellite_low_cloud_state: low,
        satellite_range_bytes: display_contract.range_bytes,
        satellite_range_blocks: display_contract.range_blocks,
        nowcast_point_lat: request.point_lat,
        nowcast_point_lon: request.point_lon,
        nowcast_cache_identity: cache_identity,
        satellite_vertical_profile: profile_value,
        calibrated_sample_coverage_ratio: coverage,
    })
    .map_err(|error| error.to_string())
}

pub(crate) fn compute(request: Value) -> Result<Value, String> {
    let request = serde_json::from_value(request).map_err(|error| error.to_string())?;
    serde_json::to_value(build_result(request)?).map_err(|error| error.to_string())
}

pub fn execute(params: Params) -> Result<Value, String> {
    let (request, identity) =
        request_file::read::<AdjustmentRequest>(&params.request_path, MAX_REQUEST_BYTES)?;
    let result = build_result(request)?;
    let bytes = serde_json::to_vec(&result).map_err(|error| error.to_string())?;
    if bytes.len() > MAX_RESULT_BYTES {
        return Err("nowcast adjustment output exceeds 128 MiB".into());
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
        "contract": "rust-desktop-nowcast-adjustment-file-v1",
        "runtime_python_used": false,
        "output_path": params.output_path,
        "bytes": bytes.len(),
        "sha256": Sha256::digest(&bytes).iter().map(|byte| format!("{byte:02x}")).collect::<String>(),
        "request_sha256": identity.sha256,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::desktop_hsd_retrieval::Level;

    fn test_adjustment_request() -> AdjustmentRequest {
        AdjustmentRequest {
            target_time_utc: "2026-11-10T08:00:00Z".into(),
            timezone_name: "UTC".into(),
            point_lat: 30.2741,
            point_lon: 120.1551,
            radius_km: 900.0,
            azimuth_deg: 25.0,
            display_frame: test_frame("2026-11-10T08:05:00Z"),
            frames: vec![],
            source_times_utc: vec!["2026-11-10T08:00:00Z".into(), "2026-11-10T07:55:00Z".into()],
            motion_dx_px_per_hour: 3.5,
            motion_dy_px_per_hour: -2.0,
            motion_correlation: 0.8,
            motion_quality: "strong".into(),
            motion_age_minutes: Some(10.0),
            lagrangian_b13_trend_k_per_hour: None,
            advection: Some(AdvectionPlan {
                horizon_hours: 0.25,
                scale: 0.9,
                applied: true,
                reason: "fixture".into(),
                motion_age_minutes: None,
                motion_quality: "strong".into(),
            }),
            single_historical_observation: false,
        }
    }

    fn test_prior(valid_time: DateTime<Utc>) -> Prior {
        Prior {
            valid_time_utc: time_contract::iso(valid_time),
            source: "gfs-interpolated-vertical-prior".into(),
            identity: "gfs-prior:fixture".into(),
            terrain_asl_m: 8.0,
            target_lat: 30.2741,
            target_lon: 120.1551,
            levels: vec![Level {
                pressure_hpa: 850,
                height_asl_m: 1450.0,
                temperature_k: Some(283.0),
                rh_percent: Some(80.0),
                model_cloud_fraction: Some(0.5),
            }],
        }
    }

    fn test_column(valid_time: DateTime<Utc>) -> OutputColumn {
        let mut column = OutputColumn::unknown(
            valid_time,
            30.2741,
            120.1551,
            0.0,
            0.0,
            Some(8.0),
            "fixture",
            "gfs-prior:fixture",
            "gfs-interpolated-vertical-prior",
            3.5,
            -2.0,
        );
        column.base_p10_asl_m = Some(1200.0);
        column.base_p50_asl_m = Some(1300.0);
        column.base_p90_asl_m = Some(1400.0);
        column.top_asl_m = Some(1800.0);
        column.phase = "water".into();
        column.horizontal_cloud_probability = Some(0.7);
        column.low_cloud_state = "detected".into();
        column.retrieval_quality = "high".into();
        column.cloud_type = "low".into();
        column.base_source = "fixture".into();
        column
            .cloud_probability_by_height
            .insert("1450".into(), 0.6);
        column
    }

    fn test_feature(valid_time: DateTime<Utc>, cloud_fraction: f64) -> FeatureRecord {
        let _ = valid_time;
        FeatureRecord {
            lat: 30.2741,
            lon: 120.1551,
            distance_km: 0.0,
            bearing_deg: 0.0,
            pressure_hpa: 850,
            geopotential_height_m: 1450.0,
            rh_percent: 80.0,
            temperature_k: Some(283.0),
            omega_pa_s: None,
            u_ms: None,
            v_ms: None,
            cloud_fraction_percent: Some(cloud_fraction),
            cloud_liquid_kg_kg: None,
            cloud_ice_kg_kg: None,
            source_lat: None,
            source_lon: None,
            source_distance_km: None,
            source_bearing_deg: None,
            surface_visibility_m: None,
            precip_rate_kg_m2_s: None,
            surface_temperature_k: None,
            surface_dewpoint_k: None,
            footprint_azimuth_width_deg: None,
            footprint_near_distance_km: None,
            footprint_far_distance_km: None,
            horizontal_cloud_probability: Some(0.7),
            cloud_probability_given_cloud: Some(0.8),
        }
    }

    fn retrieved_test_frame(valid_time: DateTime<Utc>, cloud_fraction: f64) -> RetrievedFrame {
        let prior = test_prior(valid_time);
        RetrievedFrame {
            valid_time,
            offset_minutes: timeline_offset_minutes(
                valid_time,
                parse_utc("2026-11-10T08:00:00Z", "target").unwrap(),
            ),
            features: vec![test_feature(valid_time, cloud_fraction)],
            column_sets: vec![ColumnSetRecord {
                schema_version: 4,
                valid_time_utc: time_contract::iso(valid_time),
                source: "fixture".into(),
                vertical_prior_identity: "gfs-prior:fixture".into(),
                retrieval_quality: "high".into(),
                low_cloud_state: "detected".into(),
                columns: vec![test_column(valid_time)],
                motion_dx_px_per_hour: 3.5,
                motion_dy_px_per_hour: -2.0,
            }],
            metrics: HorizontalMetrics {
                near_projected: cloud_fraction,
                far_projected: cloud_fraction + 0.1,
                local_cloud_ratio: cloud_fraction,
                horizon_cloud_ratio: cloud_fraction / 2.0,
                cloud_texture_score: 0.25,
                cloud_uniformity_score: 0.75,
                cloud_blanket_score: 0.4,
                horizon_clearance_score: 0.7,
                composition_score: 0.6,
                ..HorizontalMetrics::default()
            },
            priors: vec![Some(prior)],
            sample_points: vec![(0.0, 0.0)],
        }
    }

    #[test]
    fn assembly_separates_primary_metrics_from_display_identity() {
        let request = test_adjustment_request();
        let target = parse_utc(&request.target_time_utc, "target").unwrap();
        let display_valid = parse_utc(&request.display_frame.valid_time_utc, "display").unwrap();
        let primary_valid = parse_utc("2026-11-10T08:00:00Z", "primary").unwrap();
        let older_valid = parse_utc("2026-11-10T07:55:00Z", "older").unwrap();
        let primary = retrieved_test_frame(primary_valid, 0.4);
        let older = retrieved_test_frame(older_valid, 0.9);
        let contract = FrameContract {
            valid_time: display_valid,
            source: "display-fixture".into(),
            quality: "medium".into(),
            range_bytes: 4096,
            range_blocks: 7,
        };
        let result = assemble_result(
            &request,
            target,
            display_valid,
            &contract,
            primary_valid,
            &[primary, older],
            1.0,
        )
        .unwrap();

        assert_eq!(result["image_valid_time_utc"], "2026-11-10T08:05:00+00:00");
        assert_eq!(result["target_time_utc"], "2026-11-10T08:00:00+00:00");
        assert_eq!(result["near_cloud_ratio"], 0.4);
        assert_eq!(result["far_cloud_ratio"].as_f64().unwrap(), 0.5);
        assert_eq!(result["satellite_range_bytes"], 4096);
        assert_eq!(
            result["observed_features_by_offset"]["0"][0]["cloud_fraction_percent"],
            0.4
        );
        assert_eq!(
            result["observed_features_by_offset"]["-5"][0]["cloud_fraction_percent"],
            0.9
        );
        assert_eq!(
            result["observed_feature_times_by_offset"]["0"],
            "2026-11-10T08:00:00+00:00"
        );
        assert_eq!(
            result["observed_feature_times_by_offset"]["-5"],
            "2026-11-10T07:55:00+00:00"
        );
        assert_eq!(
            result["satellite_vertical_profile"]["valid_time_utc"],
            "2026-11-10T08:05:00+00:00"
        );
        let sets = result["cloud_column_sets"].as_array().unwrap();
        assert_eq!(sets.len(), 2);
        assert_eq!(sets[0]["valid_time_utc"], "2026-11-10T08:00:00+00:00");
        assert_eq!(sets[1]["valid_time_utc"], "2026-11-10T07:55:00+00:00");
        assert_eq!(result["vertical_prior_identity"], "gfs-prior:fixture");
        assert_eq!(
            result["vertical_prior_source"],
            "gfs-interpolated-vertical-prior"
        );
        assert_eq!(result["satellite_retrieval_quality"], "high");
        assert_eq!(result["satellite_low_cloud_state"], "detected");
        assert_eq!(result["satellite_motion_age_minutes"], 10.0);
        let summary = result["summary"].as_array().unwrap();
        assert!(summary
            .iter()
            .all(|value| !value.as_str().unwrap().contains("0.0 分钟")));
    }

    #[test]
    fn offset_uses_python_rounding_to_five_minutes() {
        let target = parse_utc("2026-11-10T08:00:00Z", "target").unwrap();
        let frame = parse_utc("2026-11-10T08:02:30Z", "frame").unwrap();
        assert_eq!(timeline_offset_minutes(frame, target), 0);
        assert_eq!(
            timeline_offset_minutes(parse_utc("2026-11-10T08:02:29Z", "frame").unwrap(), target),
            0
        );
        assert_eq!(
            timeline_offset_minutes(parse_utc("2026-11-10T08:02:00Z", "frame").unwrap(), target),
            0
        );
        assert_eq!(
            timeline_offset_minutes(parse_utc("2026-11-10T08:07:30Z", "frame").unwrap(), target),
            10
        );
        assert_eq!(
            timeline_offset_minutes(parse_utc("2026-11-10T08:07:29Z", "frame").unwrap(), target),
            5
        );
    }

    #[test]
    fn duplicate_utc_frames_collapse() {
        let target = parse_utc("2026-11-10T08:00:00Z", "target").unwrap();
        let canonical = test_frame("2026-11-10T08:00:00Z");
        let duplicate = test_frame("2026-11-10T08:00:00Z");
        let frames = [canonical, duplicate];
        let scoring = select_scoring_frames(&frames, target).unwrap();
        assert_eq!(scoring.len(), 1);
        assert_eq!(scoring[0].0, target);
        assert!(std::ptr::eq(scoring[0].1, &frames[0]));
    }

    #[test]
    fn stale_display_frame_does_not_enter_scoring_window() {
        let target = parse_utc("2026-11-10T08:00:00Z", "target").unwrap();
        let frames = [test_frame("2026-11-10T07:20:00Z")];
        let error = select_scoring_frames(&frames, target).err().unwrap();
        assert_eq!(
            error,
            "Himawari calibrated scan is outside the supported five-minute nowcast window"
        );
    }

    #[test]
    fn different_frames_collapsing_to_one_offset_are_rejected() {
        let left = RetrievedFrame {
            valid_time: parse_utc("2026-11-10T08:00:01Z", "left").unwrap(),
            offset_minutes: 0,
            features: vec![],
            column_sets: vec![],
            metrics: HorizontalMetrics::default(),
            priors: vec![],
            sample_points: vec![],
        };
        let right = RetrievedFrame {
            valid_time: parse_utc("2026-11-10T08:00:02Z", "right").unwrap(),
            offset_minutes: 0,
            features: vec![],
            column_sets: vec![],
            metrics: HorizontalMetrics::default(),
            priors: vec![],
            sample_points: vec![],
        };
        assert_eq!(
            validate_timeline_offsets(&[left, right]).unwrap_err(),
            "independent HSD scans collapse to one nowcast timeline offset"
        );
    }

    #[test]
    fn zero_primary_coverage_fails_closed() {
        assert_eq!(
            require_usable_coverage(0.0).unwrap_err(),
            "nowcast has no usable calibrated sample coverage"
        );
        assert!(require_usable_coverage(0.01).is_ok());
    }

    #[test]
    fn sample_destination_matches_geometry_contract() {
        let point = PointValue {
            lat: 30.2741,
            lon: 120.1551,
        };
        let actual = destination(point, 0.0, 0.0);
        assert!((actual.lat - 30.2741).abs() < 1e-12);
        assert!((actual.lon - 120.1551).abs() < 1e-12);
    }

    fn test_frame(valid_time: &str) -> Frame {
        Frame {
            manifest_path: PathBuf::from(format!("/tmp/{valid_time}.json")),
            valid_time_utc: valid_time.into(),
            retrieval: RetrievalContext {
                cycle_time_utc: "2026-11-10T00:00:00Z".into(),
                left_forecast_hour: 8,
                right_forecast_hour: 8,
                fraction: 0.0,
                left_cache_path: PathBuf::from("/tmp/left"),
                right_cache_path: PathBuf::from("/tmp/left"),
                local_radius_km: 35.0,
                domain_hash: "fixture".into(),
                resolution: "0p25".into(),
            },
        }
    }

    #[test]
    fn exact_time_tie_selects_earlier_sorted_scan() {
        let target = parse_utc("2026-11-10T08:00:00Z", "target").unwrap();
        let mut frames = vec![
            (
                parse_utc("2026-11-10T08:05:00Z", "later").unwrap(),
                test_frame("2026-11-10T08:05:00Z"),
            ),
            (
                parse_utc("2026-11-10T07:55:00Z", "earlier").unwrap(),
                test_frame("2026-11-10T07:55:00Z"),
            ),
        ];
        assert_eq!(select_primary(&frames, target), Some(frames[0].0));
        frames.sort_by_key(|(valid, _)| *valid);
        assert_eq!(select_primary(&frames, target), Some(frames[0].0));
        assert_eq!(
            frames[0].0,
            parse_utc("2026-11-10T07:55:00Z", "earlier").unwrap()
        );
    }

    #[test]
    fn rounding_keys_match_python_ties_even() {
        assert_eq!(rounded_scaled(0.005, 100.0), 0);
        assert_eq!(rounded_scaled(0.015, 100.0), 2);
        assert_eq!(rounded_scaled(-0.005, 100.0), 0);
        assert_eq!(rounded_scaled(-0.015, 100.0), -2);
    }

    #[test]
    fn parallel_frame_retrieval_preserves_input_order() {
        let values = parallel_map_ordered(&[4, 1, 3, 2], 3, |value| Ok(value * 2)).unwrap();
        assert_eq!(values, [8, 2, 6, 4]);
    }

    #[test]
    fn parallel_frame_retrieval_returns_first_error_in_input_order() {
        let error = parallel_map_ordered(&[1, 2, 3, 4], 3, |value| {
            if *value >= 3 {
                Err(format!("failed {value}"))
            } else {
                Ok(*value)
            }
        })
        .unwrap_err();
        assert_eq!(error, "failed 3");
    }

    fn coverage_set(valid_time: DateTime<Utc>, quality: &str) -> ColumnSetRecord {
        let mut column = test_column(valid_time);
        column.retrieval_quality = quality.into();
        ColumnSetRecord {
            schema_version: 4,
            valid_time_utc: time_contract::iso(valid_time),
            source: "fixture".into(),
            vertical_prior_identity: "gfs-prior:fixture".into(),
            retrieval_quality: quality.into(),
            low_cloud_state: "detected".into(),
            columns: vec![column],
            motion_dx_px_per_hour: 3.5,
            motion_dy_px_per_hour: -2.0,
        }
    }

    #[test]
    fn coverage_weights_nearer_frames_more_than_distant_ones() {
        let target = parse_utc("2026-11-10T08:00:00Z", "target").unwrap();
        let near = coverage_set(target, "high");
        let far = coverage_set(target - chrono::Duration::minutes(60), "unknown");

        // Unweighted this is 1 usable / 2 total = 0.5; the fresh usable scan
        // should dominate because the distant scan carries large advection error.
        let weighted = cloud_column_coverage([&near, &far], target);
        assert!(weighted > 0.9, "weighted coverage was {weighted}");

        // Reversed: the fresh scan is unusable, so coverage collapses toward 0
        // even though the distant scan is usable.
        let near_unknown = coverage_set(target, "unknown");
        let far_usable = coverage_set(target - chrono::Duration::minutes(60), "high");
        let reversed = cloud_column_coverage([&near_unknown, &far_usable], target);
        assert!(reversed < 0.1, "reversed coverage was {reversed}");
    }

    #[test]
    fn coverage_reduces_to_unweighted_for_equal_quality_frames() {
        let target = parse_utc("2026-11-10T08:00:00Z", "target").unwrap();
        let near = coverage_set(target, "high");
        let far = coverage_set(target - chrono::Duration::minutes(30), "high");
        assert_eq!(cloud_column_coverage([&near, &far], target), 1.0);
        assert_eq!(cloud_column_coverage([&near], target), 1.0);

        let unknown = coverage_set(target, "unknown");
        assert_eq!(cloud_column_coverage([&unknown], target), 0.0);
        assert_eq!(cloud_column_coverage(std::iter::empty(), target), 0.0);
    }

    #[test]
    fn motion_reliability_rises_with_correlation_when_advection_applied() {
        let mut request = test_adjustment_request();
        request.motion_correlation = MOTION_ACCEPT_CORRELATION;
        assert_eq!(
            motion_reliability_factor(&request),
            MOTION_RELIABILITY_FLOOR
        );
        request.motion_correlation = 1.0;
        assert_eq!(motion_reliability_factor(&request), 1.0);

        request.motion_correlation = 0.5;
        let low = motion_reliability_factor(&request);
        request.motion_correlation = 0.8;
        let high = motion_reliability_factor(&request);
        assert!(low < high && high < 1.0);
        assert!(low > MOTION_RELIABILITY_FLOOR);
    }

    #[test]
    fn motion_reliability_is_neutral_when_advection_not_applied() {
        let mut request = test_adjustment_request();
        request.motion_correlation = 0.0;
        request.advection.as_mut().unwrap().applied = false;
        assert_eq!(motion_reliability_factor(&request), 1.0);
        request.advection = None;
        assert_eq!(motion_reliability_factor(&request), 1.0);
    }

    #[test]
    fn motion_reliability_clamps_out_of_range_correlation() {
        let mut request = test_adjustment_request();
        request.motion_correlation = -1.0;
        assert_eq!(
            motion_reliability_factor(&request),
            MOTION_RELIABILITY_FLOOR
        );
        request.motion_correlation = 5.0;
        assert_eq!(motion_reliability_factor(&request), 1.0);
    }

    #[test]
    fn trend_score_responds_to_lagrangian_cooling_and_warming_by_regime() {
        let target = parse_utc("2026-11-10T08:00:00Z", "target").unwrap();
        // 1. Single frame with no Lagrangian rate falls back cleanly to 0.5.
        let mut request = test_adjustment_request();
        request.lagrangian_b13_trend_k_per_hour = None;
        let open_frame = retrieved_test_frame(target, 0.30);
        assert_eq!(
            compute_trend_score(&request, std::slice::from_ref(&open_frame), &open_frame),
            0.5
        );

        // 2. Open/moderate sky: moderate cooling (-4.5 K/h, mid-high cloud arrival) > 0.5,
        //    warming (+4.5 K/h, target cloud dissipation) < 0.5.
        request.lagrangian_b13_trend_k_per_hour = Some(-4.5);
        let cooling_open =
            compute_trend_score(&request, std::slice::from_ref(&open_frame), &open_frame);
        request.lagrangian_b13_trend_k_per_hour = Some(4.5);
        let warming_open =
            compute_trend_score(&request, std::slice::from_ref(&open_frame), &open_frame);
        assert!(
            cooling_open > 0.6,
            "expected favorable cooling on open sky, got {cooling_open}"
        );
        assert!(
            warming_open < 0.4,
            "expected unfavorable warming on open sky, got {warming_open}"
        );

        // 3. Overcast/blocking sky: warming (+4.5 K/h, clearing/thinning) > 0.5,
        //    cooling (-4.5 K/h, thickening overcast) < 0.5.
        let mut overcast_frame = retrieved_test_frame(target, 0.85);
        overcast_frame.metrics.cloud_blanket_score = 0.82;
        overcast_frame.metrics.horizon_cloud_ratio = 0.85;
        request.lagrangian_b13_trend_k_per_hour = Some(4.5);
        let warming_overcast = compute_trend_score(
            &request,
            std::slice::from_ref(&overcast_frame),
            &overcast_frame,
        );
        request.lagrangian_b13_trend_k_per_hour = Some(-4.5);
        let cooling_overcast = compute_trend_score(
            &request,
            std::slice::from_ref(&overcast_frame),
            &overcast_frame,
        );
        assert!(
            warming_overcast > 0.6,
            "expected favorable clearing on overcast sky, got {warming_overcast}"
        );
        assert!(
            cooling_overcast < 0.4,
            "expected unfavorable thickening on overcast sky, got {cooling_overcast}"
        );

        // 4. Multi-frame clearing of near-horizon blocking clouds with no B13 rate > 0.5.
        request.lagrangian_b13_trend_k_per_hour = None;
        let earlier = retrieved_test_frame(target - chrono::Duration::minutes(20), 0.85);
        let latest = retrieved_test_frame(target, 0.35);
        let frames = [earlier, latest];
        let clearing_multi = compute_trend_score(&request, &frames, &frames[1]);
        assert!(
            clearing_multi > 0.55,
            "expected multi-frame clearing trend > 0.55, got {clearing_multi}"
        );
    }

    #[test]
    fn multispectral_refinement_recovers_thin_cirrus_and_warm_low_cloud_profile() {
        let valid_time = parse_utc("2026-11-10T08:00:00Z", "valid").unwrap();
        let prior = Prior {
            valid_time_utc: time_contract::iso(valid_time),
            source: "gfs-interpolated-vertical-prior".into(),
            identity: "gfs-prior:multispectral".into(),
            terrain_asl_m: 10.0,
            target_lat: 30.2741,
            target_lon: 120.1551,
            levels: vec![
                Level {
                    pressure_hpa: 925,
                    height_asl_m: 780.0,
                    temperature_k: Some(288.0),
                    rh_percent: Some(92.0),
                    model_cloud_fraction: Some(0.65),
                },
                Level {
                    pressure_hpa: 500,
                    height_asl_m: 5600.0,
                    temperature_k: Some(258.0),
                    rh_percent: Some(50.0),
                    model_cloud_fraction: Some(0.10),
                },
                Level {
                    pressure_hpa: 300,
                    height_asl_m: 9300.0,
                    temperature_k: Some(232.0),
                    rh_percent: Some(82.0),
                    model_cloud_fraction: Some(0.55),
                },
            ],
        };

        // Case A: Thin cirrus with strong B13 - B15 split-window signature (+3.2 K)
        // where B13 alone is near surface temperature so column initially collapsed.
        let mut cirrus_col = OutputColumn::unknown(
            valid_time,
            30.2741,
            120.1551,
            180.0,
            25.0,
            Some(10.0),
            "noaa-himawari9-hsd-calibrated:fixture",
            &prior.identity,
            &prior.source,
            3.0,
            -1.0,
        );
        let mut cirrus_features = Vec::new();
        let (updated, is_thin, is_thick, usable) = refine_multispectral_sample(
            &mut [&mut cirrus_col],
            &mut cirrus_features,
            &prior,
            [284.0, 285.0, 281.8], // B13 - B15 = +3.2 K, B07 - B13 = -1.0 K
            (14.0, 140.0, 220.0),
        );
        assert!(updated);
        assert!(usable);
        assert!(is_thin);
        assert!(!is_thick);
        assert!(
            cirrus_col.cloud_probability_by_height.get("9300").copied().unwrap_or(0.0) > 0.25,
            "expected upper-level thin cirrus posterior at 9300 m, got {:?}",
            cirrus_col.cloud_probability_by_height
        );
        assert!(
            cirrus_features.iter().any(|f| f.pressure_hpa == 300
                && f.cloud_fraction_percent.is_some_and(|v| v > 25.0)),
            "expected synchronized 300 hPa cirrus feature record"
        );

        // Case B: Warm nighttime fog/low stratus with B07 - B13 = -3.2 K and moist 925 hPa prior.
        let mut fog_col = OutputColumn::unknown(
            valid_time,
            30.2741,
            120.1551,
            30.0,
            25.0,
            Some(10.0),
            "noaa-himawari9-hsd-calibrated:fixture",
            &prior.identity,
            &prior.source,
            3.0,
            -1.0,
        );
        let mut fog_features = Vec::new();
        let (updated_fog, _, _, usable_fog) = refine_multispectral_sample(
            &mut [&mut fog_col],
            &mut fog_features,
            &prior,
            [283.8, 287.0, 286.6], // B07 - B13 = -3.2 K, B13 - B15 = +0.4 K
            (15.0, 15.0, 50.0),
        );
        assert!(updated_fog);
        assert!(usable_fog);
        assert_eq!(fog_col.low_cloud_state, "detected");
        assert!(
            fog_col.cloud_probability_by_height.get("780").copied().unwrap_or(0.0) > 0.25,
            "expected low-level warm cloud posterior at 780 m, got {:?}",
            fog_col.cloud_probability_by_height
        );
        assert!(
            fog_features.iter().any(|f| f.pressure_hpa == 925
                && f.cloud_fraction_percent.is_some_and(|v| v > 25.0)),
            "expected synchronized 925 hPa low-cloud feature record"
        );
    }
}
