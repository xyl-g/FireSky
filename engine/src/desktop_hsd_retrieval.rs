//! Native calibrated-HSD cloud-column retrieval.
//!
//! This operation owns the deterministic HSD B07/B13/B15 posterior. The
//! Python layer still owns sample-point geometry, GFS provider I/O and the
//! final `GridCellFeature` adapter. HCAI categorical fields deliberately do
//! not enter this contract.

use crate::tensor_bundle::verify_files;
use crate::time_contract;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, VecDeque};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::SystemTime;

const CACHE_SCHEMA_VERSION: u64 = 2;
const FRAME_SCHEMA_VERSION: u64 = 1;
const CACHE_ALGORITHM: &str = "ahi-hsd-calibrated-frame-v1";
const PROJECTION_IDENTITY: &str = "ahi-geos9-himawari9-full-disk-5500x5500-v1";
const CLOUD_COLUMN_SCHEMA_VERSION: u64 = 4;
const CLOUD_COLUMN_ALGORITHM: &str = "ahi-calibrated-cloud-column-v4";
const MAX_SAMPLES: usize = 256;
const MAX_LEVELS: usize = 256;
const MAX_FRAME_CACHE: usize = 64;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    manifest_path: PathBuf,
    samples: Vec<Sample>,
    #[serde(default)]
    compact: bool,
    #[serde(default)]
    metrics: bool,
    #[serde(default)]
    azimuth_deg: f64,
}

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Sample {
    pub(crate) lat: f64,
    pub(crate) lon: f64,
    pub(crate) distance_km: f64,
    pub(crate) bearing_deg: f64,
    pub(crate) b07: Option<f64>,
    pub(crate) b13: Option<f64>,
    pub(crate) b15: Option<f64>,
    pub(crate) motion_dx_px_per_hour: f64,
    pub(crate) motion_dy_px_per_hour: f64,
    pub(crate) prior: Prior,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Prior {
    pub(crate) valid_time_utc: String,
    pub(crate) source: String,
    pub(crate) identity: String,
    pub(crate) terrain_asl_m: f64,
    pub(crate) target_lat: f64,
    pub(crate) target_lon: f64,
    pub(crate) levels: Vec<Level>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Level {
    pub(crate) pressure_hpa: i64,
    pub(crate) height_asl_m: f64,
    pub(crate) temperature_k: Option<f64>,
    pub(crate) rh_percent: Option<f64>,
    pub(crate) model_cloud_fraction: Option<f64>,
}

#[derive(Clone, Serialize)]
pub(crate) struct HorizontalMetrics {
    pub(crate) near_current: f64,
    pub(crate) far_current: f64,
    pub(crate) near_projected: f64,
    pub(crate) far_projected: f64,
    pub(crate) near_previous: f64,
    pub(crate) far_previous: f64,
    pub(crate) local_cloud_ratio: f64,
    pub(crate) horizon_cloud_ratio: f64,
    pub(crate) thin_cloud_ratio: f64,
    pub(crate) thick_cloud_ratio: f64,
    pub(crate) cloud_texture_score: f64,
    pub(crate) cloud_uniformity_score: f64,
    pub(crate) cloud_blanket_score: f64,
    pub(crate) horizon_clearance_score: f64,
    pub(crate) composition_score: f64,
}

impl Default for HorizontalMetrics {
    fn default() -> Self {
        Self {
            near_current: 0.0,
            far_current: 0.0,
            near_projected: 0.0,
            far_projected: 0.0,
            near_previous: 0.0,
            far_previous: 0.0,
            local_cloud_ratio: 0.0,
            horizon_cloud_ratio: 0.0,
            thin_cloud_ratio: 0.0,
            thick_cloud_ratio: 0.0,
            cloud_texture_score: 0.0,
            cloud_uniformity_score: 0.0,
            cloud_blanket_score: 0.0,
            horizon_clearance_score: 1.0,
            composition_score: 0.0,
        }
    }
}

#[derive(Clone, Deserialize, Serialize)]
pub(crate) struct OutputColumn {
    pub(crate) schema_version: u64,
    pub(crate) algorithm: String,
    pub(crate) valid_time_utc: String,
    pub(crate) lat: f64,
    pub(crate) lon: f64,
    pub(crate) distance_km: f64,
    pub(crate) bearing_deg: f64,
    pub(crate) base_p10_asl_m: Option<f64>,
    pub(crate) base_p50_asl_m: Option<f64>,
    pub(crate) base_p90_asl_m: Option<f64>,
    pub(crate) top_asl_m: Option<f64>,
    pub(crate) terrain_asl_m: Option<f64>,
    pub(crate) phase: String,
    pub(crate) optical_depth: Option<f64>,
    pub(crate) cloud_probability_by_height: BTreeMap<String, f64>,
    pub(crate) horizontal_cloud_probability: Option<f64>,
    pub(crate) low_cloud_state: String,
    pub(crate) retrieval_quality: String,
    pub(crate) source: String,
    pub(crate) vertical_prior_identity: String,
    pub(crate) vertical_prior_source: String,
    pub(crate) cloud_type: String,
    pub(crate) base_source: String,
    pub(crate) motion_dx_px_per_hour: f64,
    pub(crate) motion_dy_px_per_hour: f64,
}

#[derive(Serialize)]
struct CompactOutputColumn {
    base_p10_asl_m: Option<f64>,
    base_p50_asl_m: Option<f64>,
    base_p90_asl_m: Option<f64>,
    top_asl_m: Option<f64>,
    phase: String,
    optical_depth: Option<f64>,
    cloud_probability_by_height: BTreeMap<String, f64>,
    horizontal_cloud_probability: Option<f64>,
    low_cloud_state: String,
    retrieval_quality: String,
    cloud_type: String,
    base_source: String,
}

impl From<OutputColumn> for CompactOutputColumn {
    fn from(column: OutputColumn) -> Self {
        Self {
            base_p10_asl_m: column.base_p10_asl_m,
            base_p50_asl_m: column.base_p50_asl_m,
            base_p90_asl_m: column.base_p90_asl_m,
            top_asl_m: column.top_asl_m,
            phase: column.phase,
            optical_depth: column.optical_depth,
            cloud_probability_by_height: column.cloud_probability_by_height,
            horizontal_cloud_probability: column.horizontal_cloud_probability,
            low_cloud_state: column.low_cloud_state,
            retrieval_quality: column.retrieval_quality,
            cloud_type: column.cloud_type,
            base_source: column.base_source,
        }
    }
}

impl From<&OutputColumn> for CompactOutputColumn {
    fn from(column: &OutputColumn) -> Self {
        Self {
            base_p10_asl_m: column.base_p10_asl_m,
            base_p50_asl_m: column.base_p50_asl_m,
            base_p90_asl_m: column.base_p90_asl_m,
            top_asl_m: column.top_asl_m,
            phase: column.phase.clone(),
            optical_depth: column.optical_depth,
            cloud_probability_by_height: column.cloud_probability_by_height.clone(),
            horizontal_cloud_probability: column.horizontal_cloud_probability,
            low_cloud_state: column.low_cloud_state.clone(),
            retrieval_quality: column.retrieval_quality.clone(),
            cloud_type: column.cloud_type.clone(),
            base_source: column.base_source.clone(),
        }
    }
}

impl OutputColumn {
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn unknown(
        valid_time: DateTime<Utc>,
        lat: f64,
        lon: f64,
        distance_km: f64,
        bearing_deg: f64,
        terrain_asl_m: Option<f64>,
        source: &str,
        vertical_prior_identity: &str,
        vertical_prior_source: &str,
        motion_dx_px_per_hour: f64,
        motion_dy_px_per_hour: f64,
    ) -> Self {
        Self {
            schema_version: CLOUD_COLUMN_SCHEMA_VERSION,
            algorithm: CLOUD_COLUMN_ALGORITHM.into(),
            valid_time_utc: time_contract::iso(valid_time),
            lat,
            lon,
            distance_km,
            bearing_deg,
            base_p10_asl_m: None,
            base_p50_asl_m: None,
            base_p90_asl_m: None,
            top_asl_m: None,
            terrain_asl_m,
            phase: "unknown".into(),
            optical_depth: None,
            cloud_probability_by_height: BTreeMap::new(),
            horizontal_cloud_probability: None,
            low_cloud_state: "unknown".into(),
            retrieval_quality: "unknown".into(),
            source: source.into(),
            vertical_prior_identity: vertical_prior_identity.into(),
            vertical_prior_source: vertical_prior_source.into(),
            cloud_type: "unknown".into(),
            base_source: "unknown".into(),
            motion_dx_px_per_hour,
            motion_dy_px_per_hour,
        }
    }
}

#[derive(Clone, PartialEq, Eq)]
struct FileVersion {
    path: PathBuf,
    bytes: u64,
    modified: SystemTime,
}

#[derive(Clone, PartialEq, Eq)]
pub(crate) struct BundleVersion {
    files: Vec<FileVersion>,
}

#[derive(Clone)]
pub(crate) struct FrameContract {
    pub(crate) valid_time: DateTime<Utc>,
    pub(crate) source: String,
    pub(crate) quality: String,
    pub(crate) range_bytes: u64,
    pub(crate) range_blocks: u64,
}

struct CachedFrame {
    version: BundleVersion,
    contract: FrameContract,
}

static FRAME_CACHE: OnceLock<Mutex<VecDeque<CachedFrame>>> = OnceLock::new();

fn regular_version(path: PathBuf) -> Result<FileVersion, String> {
    let metadata = fs::symlink_metadata(&path).map_err(|e| e.to_string())?;
    if !metadata.file_type().is_file() {
        return Err("HSD retrieval bundle file must be regular".into());
    }
    Ok(FileVersion {
        path,
        bytes: metadata.len(),
        modified: metadata.modified().map_err(|e| e.to_string())?,
    })
}

pub(crate) fn bundle_version(path: &Path) -> Result<BundleVersion, String> {
    if !path.is_absolute() {
        return Err("HSD retrieval manifest must be absolute".into());
    }
    let root = path
        .parent()
        .ok_or("HSD retrieval manifest has no parent")?;
    let files = [
        path.to_path_buf(),
        root.join("latitudes.npy"),
        root.join("longitudes.npy"),
        root.join("band_b07.npy"),
        root.join("band_b13.npy"),
        root.join("band_b15.npy"),
    ]
    .into_iter()
    .map(regular_version)
    .collect::<Result<Vec<_>, _>>()?;
    Ok(BundleVersion { files })
}

fn cached_frame(version: &BundleVersion) -> Option<FrameContract> {
    let mut cache = FRAME_CACHE.get_or_init(Mutex::default).lock().unwrap();
    let position = cache.iter().position(|entry| entry.version == *version)?;
    let entry = cache.remove(position)?;
    let contract = entry.contract.clone();
    cache.push_back(entry);
    Some(contract)
}

fn remember_frame(version: BundleVersion, contract: FrameContract) {
    let mut cache = FRAME_CACHE.get_or_init(Mutex::default).lock().unwrap();
    cache.retain(|entry| entry.version != version);
    cache.push_back(CachedFrame { version, contract });
    while cache.len() > MAX_FRAME_CACHE {
        cache.pop_front();
    }
}

fn metadata_string<'a>(metadata: &'a Value, key: &str) -> Result<&'a str, String> {
    metadata
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("HSD retrieval metadata field {key} is missing"))
}

pub(crate) fn frame(path: &Path) -> Result<FrameContract, String> {
    if !path.is_absolute() {
        return Err("HSD retrieval manifest must be absolute".into());
    }
    let verified = verify_files(path, "hsd-compact-frame-v1")?;
    let metadata = verified
        .metadata
        .as_object()
        .ok_or("HSD retrieval frame metadata is not an object")?;
    if metadata.get("cache_schema_version").and_then(Value::as_u64) != Some(CACHE_SCHEMA_VERSION)
        || metadata_string(&verified.metadata, "cache_algorithm")? != CACHE_ALGORITHM
        || metadata.get("frame_schema_version").and_then(Value::as_u64)
            != Some(FRAME_SCHEMA_VERSION)
        || metadata_string(&verified.metadata, "projection_identity")? != PROJECTION_IDENTITY
        || metadata.get("bands") != Some(&json!(["B07", "B13", "B15"]))
        || metadata.get("units") != Some(&json!({"B07": "K", "B13": "K", "B15": "K"}))
    {
        return Err("HSD retrieval frame metadata contract is invalid".into());
    }
    let expected = [
        "latitudes",
        "longitudes",
        "band_b07",
        "band_b13",
        "band_b15",
    ];
    if verified.shapes.len() != expected.len()
        || expected
            .iter()
            .any(|name| !verified.shapes.contains_key(*name))
        || verified
            .shapes
            .get("latitudes")
            .is_none_or(|shape| shape.len() != 2)
        || expected
            .iter()
            .any(|name| verified.shapes.get(*name) != verified.shapes.get("latitudes"))
    {
        return Err("HSD retrieval frame arrays do not match the HSD contract".into());
    }
    let valid_time =
        time_contract::parse_utc(metadata_string(&verified.metadata, "valid_time_utc")?)?;
    let ready_time =
        time_contract::parse_utc(metadata_string(&verified.metadata, "ready_time_utc")?)?;
    if ready_time != valid_time + chrono::Duration::minutes(5) {
        return Err("HSD retrieval frame ready time is invalid".into());
    }
    let source = metadata_string(&verified.metadata, "source")?;
    let quality = metadata_string(&verified.metadata, "quality")?;
    let range_bytes = verified
        .metadata
        .get("range_bytes")
        .and_then(Value::as_u64)
        .filter(|value| *value > 0)
        .ok_or("HSD retrieval range byte count is invalid")?;
    let range_blocks = verified
        .metadata
        .get("range_blocks")
        .and_then(Value::as_u64)
        .filter(|value| *value > 0)
        .ok_or("HSD retrieval range block count is invalid")?;
    if source.is_empty() || !matches!(quality, "medium" | "low") {
        return Err("HSD retrieval frame provenance or quality is invalid".into());
    }
    Ok(FrameContract {
        valid_time,
        source: source.to_owned(),
        quality: quality.to_owned(),
        range_bytes,
        range_blocks,
    })
}

fn finite(value: f64) -> bool {
    value.is_finite()
}

fn validate_level(level: &Level) -> Result<(), String> {
    if level.pressure_hpa <= 0
        || !finite(level.height_asl_m)
        || level
            .temperature_k
            .is_some_and(|value| !finite(value) || !(150.0..=350.0).contains(&value))
        || level
            .rh_percent
            .is_some_and(|value| !finite(value) || !(0.0..=100.0).contains(&value))
        || level
            .model_cloud_fraction
            .is_some_and(|value| !finite(value) || !(0.0..=1.0).contains(&value))
    {
        return Err("HSD retrieval prior level is invalid".into());
    }
    Ok(())
}

fn validate_prior(prior: &Prior, valid_time: DateTime<Utc>, sample: &Sample) -> Result<(), String> {
    if prior.source.is_empty()
        || prior.identity.is_empty()
        || !finite(prior.terrain_asl_m)
        || !(-500.0..=9000.0).contains(&prior.terrain_asl_m)
        || !finite(prior.target_lat)
        || !(-90.0..=90.0).contains(&prior.target_lat)
        || !finite(prior.target_lon)
        || !(-180.0..=180.0).contains(&prior.target_lon)
        || (prior.target_lat - sample.lat).abs() > 0.30
        || wrapped_delta(prior.target_lon, sample.lon).abs() > 0.30
        || prior.levels.is_empty()
        || prior.levels.len() > MAX_LEVELS
        || time_contract::parse_utc(&prior.valid_time_utc)? != valid_time
    {
        return Err("HSD retrieval prior identity or time is invalid".into());
    }
    for level in &prior.levels {
        validate_level(level)?;
    }
    if prior.levels.windows(2).any(|window| {
        window[0].height_asl_m > window[1].height_asl_m
            || window[0].pressure_hpa <= window[1].pressure_hpa
    }) {
        return Err("HSD retrieval prior levels are not ordered".into());
    }
    Ok(())
}

fn wrapped_delta(left: f64, right: f64) -> f64 {
    (left - right + 180.0).rem_euclid(360.0) - 180.0
}

fn sigmoid(value: f64) -> f64 {
    if value >= 35.0 {
        1.0
    } else if value <= -35.0 {
        0.0
    } else {
        1.0 / (1.0 + (-value).exp())
    }
}

fn nearest_level(levels: &[Level], height: f64) -> &Level {
    let mut best = &levels[0];
    let mut distance = (best.height_asl_m - height).abs();
    for level in &levels[1..] {
        let candidate = (level.height_asl_m - height).abs();
        if candidate < distance {
            best = level;
            distance = candidate;
        }
    }
    best
}

fn surface_temperature(prior: &Prior) -> Option<f64> {
    prior
        .levels
        .iter()
        .filter(|level| level.temperature_k.is_some() && level.height_asl_m >= prior.terrain_asl_m)
        .min_by(|left, right| left.height_asl_m.total_cmp(&right.height_asl_m))
        .and_then(|level| level.temperature_k)
}

fn cloud_top(prior: &Prior, brightness_temperature: f64) -> Option<f64> {
    prior
        .levels
        .iter()
        .filter(|level| level.temperature_k.is_some() && level.height_asl_m >= prior.terrain_asl_m)
        .min_by(|left, right| {
            (left.temperature_k.unwrap() - brightness_temperature)
                .abs()
                .total_cmp(&(right.temperature_k.unwrap() - brightness_temperature).abs())
        })
        .map(|level| level.height_asl_m)
}

fn candidate_layers(prior: &Prior, top: f64, probability: f64) -> Vec<Level> {
    if probability < 0.35 {
        return Vec::new();
    }
    let upper_only = top - prior.terrain_asl_m > 3500.0;
    let mut candidates: Vec<Level> = prior
        .levels
        .iter()
        .filter(|level| {
            level.height_asl_m <= top + 900.0
                && (level
                    .model_cloud_fraction
                    .is_some_and(|value| value >= 0.10)
                    || level.rh_percent.is_some_and(|value| value >= 78.0))
        })
        .cloned()
        .collect();
    if upper_only {
        let nearest = nearest_level(&prior.levels, top).clone();
        if !candidates.iter().any(|level| same_level(level, &nearest)) {
            candidates.push(nearest);
        }
        candidates.sort_by(|left, right| left.height_asl_m.total_cmp(&right.height_asl_m));
    }
    if candidates.is_empty() {
        return candidates;
    }
    let mut selected = Vec::new();
    for level in candidates {
        if selected
            .last()
            .is_none_or(|previous: &Level| level.height_asl_m - previous.height_asl_m > 1500.0)
        {
            selected.push(level);
        }
    }
    if selected.len() > 3 {
        selected.split_off(selected.len() - 3)
    } else {
        selected
    }
}

fn same_level(left: &Level, right: &Level) -> bool {
    left.pressure_hpa == right.pressure_hpa
        && left.height_asl_m.to_bits() == right.height_asl_m.to_bits()
}

fn layer_weights(levels: &[Level], prior: &Prior, top: f64) -> Vec<f64> {
    let mut raw: Vec<f64> = levels
        .iter()
        .map(|level| {
            let cloud = level.model_cloud_fraction.unwrap_or(0.0);
            let humidity = ((level.rh_percent.unwrap_or(55.0) - 55.0) / 45.0).clamp(0.0, 1.0);
            0.05_f64.max(cloud).max(humidity)
        })
        .collect();
    if top - prior.terrain_asl_m > 3500.0 {
        let low_cutoff = top - 2500.0;
        let upper_total: f64 = raw
            .iter()
            .zip(levels)
            .filter(|(_, level)| level.height_asl_m >= low_cutoff)
            .map(|(value, _)| *value)
            .sum();
        let low_total: f64 = raw
            .iter()
            .zip(levels)
            .filter(|(_, level)| level.height_asl_m < low_cutoff)
            .map(|(value, _)| *value)
            .sum();
        if upper_total > 0.0 && low_total > 0.0 {
            let scale = 1.0_f64.min(upper_total * 0.25 / low_total);
            for (value, level) in raw.iter_mut().zip(levels) {
                if level.height_asl_m < low_cutoff {
                    *value *= scale;
                }
            }
        }
    }
    let total: f64 = raw.iter().sum();
    if total > 0.0 {
        raw.into_iter().map(|value| value / total).collect()
    } else {
        vec![1.0 / levels.len() as f64; levels.len()]
    }
}

fn cloud_base(prior: &Prior, level: &Level, top: f64) -> (f64, f64, f64, &'static str) {
    let terrain = prior.terrain_asl_m;
    let low_top = top - terrain <= 3500.0;
    let (spread, p50, source) = if low_top && level.height_asl_m - terrain <= 3500.0 {
        (
            if level.rh_percent.is_some_and(|value| value >= 88.0) {
                700.0
            } else {
                1100.0
            },
            (level.height_asl_m - 250.0).max(terrain),
            "ahi-top-low+gfs-saturation-layer",
        )
    } else if !low_top && level.height_asl_m < top - 2500.0 {
        (
            2200.0,
            (top - 1600.0).max(terrain),
            "ahi-top+gfs-low-layer-uncertain",
        )
    } else {
        (
            2200.0,
            (top - 1600.0).max(terrain),
            "ahi-top+gfs-cloud-layer-unknown-phase",
        )
    };
    let mut p10 = (p50 - spread).max(terrain);
    let p90 = (p50 + spread).min(top);
    let mut middle = p50;
    if p90 < middle {
        middle = p90;
    }
    if middle < p10 {
        p10 = middle;
    }
    (p10, middle, p90, source)
}

fn height_probabilities(
    prior: &Prior,
    top: f64,
    cloud_probability: f64,
    layer: &Level,
) -> BTreeMap<String, f64> {
    let upper_only = top - prior.terrain_asl_m > 3500.0;
    let low_cutoff = top - 2500.0;
    let width = if !upper_only && layer.height_asl_m - prior.terrain_asl_m <= 3500.0 {
        950.0
    } else {
        1800.0
    };
    let gfs_floor = 0.35 - 0.20 * ((prior.terrain_asl_m - 1500.0) / 2000.0).clamp(0.0, 1.0);
    let mut result = BTreeMap::new();
    for level in &prior.levels {
        if level.height_asl_m < prior.terrain_asl_m || level.height_asl_m > top + 500.0 {
            continue;
        }
        let vertical = (-0.5 * ((level.height_asl_m - layer.height_asl_m) / width).powi(2)).exp();
        let mut model = gfs_floor;
        if let Some(value) = level.model_cloud_fraction {
            model = model.max(value.clamp(0.0, 1.0));
        }
        if let Some(value) = level.rh_percent {
            model = model.max(((value - 55.0) / 45.0).clamp(0.0, 1.0));
        }
        let mut value = (cloud_probability * vertical * model).clamp(0.0, 1.0);
        if upper_only && level.height_asl_m < low_cutoff {
            value = value.min(0.20);
        }
        result.insert(level.height_asl_m.to_string(), value);
    }
    result
}

fn clear_evidence(source: &str, prior: &Prior, b07: f64, b13: f64, b15: f64) -> bool {
    if !source.starts_with("noaa-himawari9-hsd-calibrated:") {
        return false;
    }
    let low_levels: Vec<&Level> = prior
        .levels
        .iter()
        .filter(|level| {
            level.height_asl_m >= prior.terrain_asl_m
                && level.height_asl_m - prior.terrain_asl_m <= 3500.0
        })
        .collect();
    if low_levels.is_empty()
        || low_levels.iter().any(|level| {
            level.model_cloud_fraction.is_none_or(|value| value > 0.05)
                || level.rh_percent.is_some_and(|value| value >= 72.0)
        })
    {
        return false;
    }
    (b07 - b13).abs() <= 8.0 && (b13 - b15).abs() <= 3.0
}

fn quality(frame_quality: &str, probability: f64) -> String {
    if frame_quality == "high" && probability >= 0.55 {
        "high".into()
    } else if matches!(frame_quality, "high" | "medium") && probability >= 0.30 {
        "medium".into()
    } else {
        "low".into()
    }
}

#[allow(clippy::too_many_arguments)]
fn output_column(
    sample: &Sample,
    prior: &Prior,
    valid_time: DateTime<Utc>,
    source: &str,
    base: Option<(f64, f64, f64, String)>,
    top: Option<f64>,
    profile: BTreeMap<String, f64>,
    horizontal: Option<f64>,
    low_state: &'static str,
    retrieval_quality: String,
    cloud_type: &'static str,
    base_source: String,
) -> OutputColumn {
    OutputColumn {
        schema_version: CLOUD_COLUMN_SCHEMA_VERSION,
        algorithm: CLOUD_COLUMN_ALGORITHM.into(),
        valid_time_utc: time_contract::iso(valid_time),
        lat: sample.lat,
        lon: sample.lon,
        distance_km: sample.distance_km,
        bearing_deg: sample.bearing_deg,
        base_p10_asl_m: base.as_ref().map(|value| value.0),
        base_p50_asl_m: base.as_ref().map(|value| value.1),
        base_p90_asl_m: base.as_ref().map(|value| value.2),
        top_asl_m: top,
        terrain_asl_m: Some(prior.terrain_asl_m),
        phase: "unknown".into(),
        optical_depth: None,
        cloud_probability_by_height: profile,
        horizontal_cloud_probability: horizontal,
        low_cloud_state: low_state.into(),
        retrieval_quality,
        source: source.into(),
        vertical_prior_identity: prior.identity.clone(),
        vertical_prior_source: prior.source.clone(),
        cloud_type: cloud_type.into(),
        base_source,
        motion_dx_px_per_hour: sample.motion_dx_px_per_hour,
        motion_dy_px_per_hour: sample.motion_dy_px_per_hour,
    }
}

pub(crate) fn retrieve(
    sample: &Sample,
    valid_time: DateTime<Utc>,
    source: &str,
    frame_quality: &str,
) -> Result<Vec<OutputColumn>, String> {
    validate_prior(&sample.prior, valid_time, sample)?;
    if !finite(sample.lat)
        || !(-90.0..=90.0).contains(&sample.lat)
        || !finite(sample.lon)
        || !(-180.0..=180.0).contains(&sample.lon)
        || !finite(sample.distance_km)
        || sample.distance_km < 0.0
        || !finite(sample.bearing_deg)
        || !(0.0..360.0).contains(&sample.bearing_deg)
        || !finite(sample.motion_dx_px_per_hour)
        || !finite(sample.motion_dy_px_per_hour)
    {
        return Err("HSD retrieval sample is invalid".into());
    }
    let (Some(b07), Some(b13), Some(b15)) = (sample.b07, sample.b13, sample.b15) else {
        return Ok(Vec::new());
    };
    if !finite(b07) || !finite(b13) || !finite(b15) {
        return Ok(Vec::new());
    }
    let Some(surface_temperature) = surface_temperature(&sample.prior) else {
        return Ok(Vec::new());
    };
    let cloud_probability = sigmoid((surface_temperature - b13 - 0.8) / 1.5);
    if cloud_probability < 0.20 {
        let clear = clear_evidence(source, &sample.prior, b07, b13, b15);
        let column = output_column(
            sample,
            &sample.prior,
            valid_time,
            source,
            None,
            None,
            BTreeMap::new(),
            Some(0.0),
            if clear { "clear" } else { "unknown" },
            if clear {
                frame_quality.to_owned()
            } else {
                "unknown".into()
            },
            if clear { "clear" } else { "unknown" },
            "none".into(),
        );
        return Ok(vec![column]);
    }
    let Some(top) = cloud_top(&sample.prior, b13) else {
        return Ok(Vec::new());
    };
    let mut supports = candidate_layers(&sample.prior, top, cloud_probability);
    if supports.is_empty() {
        supports.push(nearest_level(&sample.prior.levels, top).clone());
    }
    let weights = layer_weights(&supports, &sample.prior, top);
    let mut columns = Vec::new();
    for (index, level) in supports.iter().enumerate() {
        let layer_probability = cloud_probability * weights[index];
        let (p10, p50, p90, base_source) = cloud_base(&sample.prior, level, top);
        let profile = height_probabilities(&sample.prior, top, layer_probability, level);
        let low_top = top - sample.prior.terrain_asl_m <= 3500.0;
        let low_probability = profile
            .iter()
            .filter(|(height, _)| {
                let height = height.parse::<f64>().unwrap_or(f64::NAN);
                height <= top.min(level.height_asl_m + 1200.0)
            })
            .map(|(_, probability)| *probability)
            .fold(0.0, f64::max);
        let low_state = if low_top
            && low_probability
                >= 0.55 + 0.15 * ((sample.prior.terrain_asl_m - 1500.0) / 2000.0).clamp(0.0, 1.0)
            && level.height_asl_m - sample.prior.terrain_asl_m <= 3500.0
        {
            "detected"
        } else if cloud_probability < 0.30 && frame_quality == "high" {
            "clear"
        } else {
            "unknown"
        };
        columns.push(output_column(
            sample,
            &sample.prior,
            valid_time,
            source,
            Some((p10, p50, p90, base_source.to_owned())),
            Some(top),
            profile,
            Some(cloud_probability),
            low_state,
            quality(frame_quality, cloud_probability),
            "unknown",
            format!("hsd-b13-top-proxy+{base_source}"),
        ));
    }
    let mut totals: Vec<(String, f64)> = Vec::new();
    for column in &columns {
        for (height, probability) in &column.cloud_probability_by_height {
            if let Some((_, total)) = totals.iter_mut().find(|(key, _)| key == height) {
                *total += probability;
            } else {
                totals.push((height.clone(), *probability));
            }
        }
    }
    for column in &mut columns {
        for (height, probability) in &mut column.cloud_probability_by_height {
            let total = totals
                .iter()
                .find(|(key, _)| key == height)
                .map(|(_, value)| *value)
                .unwrap_or(0.0);
            if total > cloud_probability {
                *probability *= (cloud_probability / total).clamp(0.0, 1.0);
            }
        }
    }
    Ok(columns)
}

fn empty_horizontal_metrics(composition_score: f64) -> HorizontalMetrics {
    HorizontalMetrics {
        near_current: 0.0,
        far_current: 0.0,
        near_projected: 0.0,
        far_projected: 0.0,
        near_previous: 0.0,
        far_previous: 0.0,
        local_cloud_ratio: 0.0,
        horizon_cloud_ratio: 0.0,
        thin_cloud_ratio: 0.0,
        thick_cloud_ratio: 0.0,
        cloud_texture_score: 0.0,
        cloud_uniformity_score: 0.0,
        cloud_blanket_score: 0.0,
        horizon_clearance_score: 1.0,
        composition_score,
    }
}

type HorizontalKey = (i64, i64, i64, i64);
type HorizontalValue = (f64, f64, f64);

pub(crate) fn horizontal_metrics(
    samples: &[Vec<OutputColumn>],
    azimuth_deg: f64,
) -> HorizontalMetrics {
    if samples.is_empty() {
        return empty_horizontal_metrics(0.0);
    }
    let mut horizontal: BTreeMap<HorizontalKey, HorizontalValue> = BTreeMap::new();
    for columns in samples {
        for column in columns {
            if column.retrieval_quality == "unknown" {
                continue;
            }
            let Some(probability) = column.horizontal_cloud_probability else {
                continue;
            };
            if !finite(probability) {
                continue;
            }
            let key = (
                (column.lat * 100_000.0).round() as i64,
                (column.lon * 100_000.0).round() as i64,
                (column.distance_km * 100.0).round() as i64,
                (column.bearing_deg * 100.0).round() as i64,
            );
            let probability = probability.clamp(0.0, 1.0);
            horizontal
                .entry(key)
                .and_modify(|entry| entry.0 = entry.0.max(probability))
                .or_insert((probability, column.distance_km, column.bearing_deg));
        }
    }
    let values: Vec<(f64, f64, f64)> = horizontal.values().copied().collect();
    if values.is_empty() {
        return empty_horizontal_metrics(0.4);
    }
    let all: Vec<f64> = values.iter().map(|(value, _, _)| *value).collect();
    let near: Vec<f64> = values
        .iter()
        .filter(|(_, distance, _)| *distance <= 120.0)
        .map(|(value, _, _)| *value)
        .collect();
    let far: Vec<f64> = values
        .iter()
        .filter(|(_, distance, _)| *distance > 120.0)
        .map(|(value, _, _)| *value)
        .collect();
    let horizon: Vec<f64> = values
        .iter()
        .filter(|(_, distance, bearing)| {
            *distance <= 140.0
                && ((*bearing - azimuth_deg + 180.0).rem_euclid(360.0) - 180.0).abs() <= 35.0
        })
        .map(|(value, _, _)| *value)
        .collect();
    let mean = |items: &[f64]| {
        if items.is_empty() {
            0.0
        } else {
            items.iter().sum::<f64>() / items.len() as f64
        }
    };
    let local_ratio = mean(&all);
    let near_ratio = mean(&near);
    let far_ratio = mean(&far);
    let horizon_ratio = mean(if horizon.is_empty() { &near } else { &horizon });
    let standard_deviation = if all.is_empty() {
        0.0
    } else {
        let variance = all
            .iter()
            .map(|value| (value - local_ratio).powi(2))
            .sum::<f64>()
            / all.len() as f64;
        variance.sqrt()
    };
    let texture = (standard_deviation / 0.28).clamp(0.0, 1.0);
    let uniformity = (1.0 - standard_deviation / 0.22).clamp(0.0, 1.0);
    let blanket = (local_ratio * uniformity).clamp(0.0, 1.0);
    let horizon_clearance = (1.0 - horizon_ratio).clamp(0.0, 1.0);
    let canvas = (1.0 - (local_ratio - 0.46).abs() / 0.46).clamp(0.0, 1.0);
    let composition =
        (0.30 * canvas + 0.30 * texture + 0.26 * horizon_clearance + 0.14 * (1.0 - blanket)
            - 0.30 * blanket)
            .clamp(0.0, 1.0);
    HorizontalMetrics {
        near_current: near_ratio,
        far_current: far_ratio,
        near_projected: near_ratio,
        far_projected: far_ratio,
        near_previous: near_ratio,
        far_previous: far_ratio,
        local_cloud_ratio: local_ratio,
        horizon_cloud_ratio: horizon_ratio,
        thin_cloud_ratio: 0.0,
        thick_cloud_ratio: 0.0,
        cloud_texture_score: texture,
        cloud_uniformity_score: uniformity,
        cloud_blanket_score: blanket,
        horizon_clearance_score: horizon_clearance,
        composition_score: composition,
    }
}

pub fn execute(params: Params) -> Result<Value, String> {
    if params.samples.len() > MAX_SAMPLES {
        return Err("HSD retrieval sample count exceeds bound".into());
    }
    let version = bundle_version(&params.manifest_path)?;
    let contract = if let Some(cached) = cached_frame(&version) {
        cached
    } else {
        let verified = frame(&params.manifest_path)?;
        remember_frame(version, verified.clone());
        verified
    };
    let mut output_columns: Vec<Vec<OutputColumn>> = Vec::with_capacity(params.samples.len());
    let mut samples = Vec::with_capacity(params.samples.len());
    for sample in &params.samples {
        let columns = retrieve(
            sample,
            contract.valid_time,
            &contract.source,
            &contract.quality,
        )?;
        output_columns.push(columns);
        let columns = output_columns
            .last()
            .expect("output columns were just pushed");
        if params.compact {
            samples.push(json!({
                "columns": columns
                    .iter()
                    .cloned()
                    .map(CompactOutputColumn::from)
                    .collect::<Vec<_>>(),
            }));
        } else {
            samples.push(json!({"columns": columns}));
        }
    }
    let contract_name = if params.metrics {
        "rust-desktop-nowcast-hsd-retrieval-v3"
    } else if params.compact {
        "rust-desktop-nowcast-hsd-retrieval-v2"
    } else {
        "rust-desktop-nowcast-hsd-retrieval-v1"
    };
    let mut response = json!({
        "contract": contract_name,
        "runtime_python_used": false,
        "valid_time_utc": time_contract::iso(contract.valid_time),
        "source": contract.source,
        "samples": samples,
    });
    if params.metrics {
        response["metrics"] = json!(horizontal_metrics(&output_columns, params.azimuth_deg));
    }
    Ok(response)
}
