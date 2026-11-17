//! End-to-end native retrieval for one calibrated HSD scan and GFS prior.
//!
//! The Python layer owns request planning, cache warming and public object
//! assembly. This operation owns sampling geometry, decoded GFS prior
//! construction, HSD pixel selection, calibrated posterior and metrics.

use crate::desktop_hsd_retrieval::{
    frame, horizontal_metrics, retrieve as retrieve_columns, HorizontalMetrics, Level,
    OutputColumn, Prior, Sample,
};
use crate::desktop_nowcast_prior::{
    build_prior, features_at, interpolate, read_cached_decoded_grid, select_geometry, terrain_at,
    validate_feature, DecodedGrid, PriorContext,
};
use crate::tensor_bundle::F32Bundle;
use crate::time_contract;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap, VecDeque};
use std::fs::{self, OpenOptions};
use std::io::Write as _;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, OnceLock};

const MAX_SAMPLES: usize = 256;
const MAX_RESULT_BYTES: usize = 128 * 1024 * 1024;
const EARTH_RADIUS_KM: f64 = 6371.0;
const PIXEL_TOLERANCE_KM: f64 = 25.0;
const OUTER_DISTANCE_STEP_KM: f64 = 80.0;
const LATITUDE_BUCKET_DEGREES: f64 = 0.02;
const MAX_CACHED_FRAMES: usize = 4;

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    pub(crate) manifest_path: PathBuf,
    pub(crate) valid_time_utc: String,
    pub(crate) point_lat: f64,
    pub(crate) point_lon: f64,
    pub(crate) azimuth_deg: f64,
    pub(crate) radius_km: f64,
    pub(crate) motion_dx_px_per_hour: f64,
    pub(crate) motion_dy_px_per_hour: f64,
    pub(crate) cycle_time_utc: String,
    pub(crate) left_forecast_hour: u32,
    pub(crate) right_forecast_hour: u32,
    pub(crate) source: String,
    pub(crate) fraction: f64,
    pub(crate) local_radius_km: f64,
    pub(crate) left_cache_path: PathBuf,
    pub(crate) right_cache_path: PathBuf,
    pub(crate) domain_hash: String,
    pub(crate) resolution: String,
    pub(crate) output_path: Option<PathBuf>,
}

pub(crate) struct RetrievalTimings {
    pub(crate) hsd_frame_load_ms: f64,
    pub(crate) hsd_sampling_ms: f64,
    pub(crate) gfs_grid_load_ms: f64,
    pub(crate) retrieval_ms: f64,
    pub(crate) assembly_ms: f64,
}

pub(crate) struct RetrievalResult {
    pub(crate) features: Vec<FeatureRecord>,
    pub(crate) column_sets: Vec<ColumnSetRecord>,
    pub(crate) metrics: HorizontalMetrics,
    pub(crate) priors: Vec<Option<Prior>>,
    pub(crate) sample_points: Vec<(f64, f64)>,
    pub(crate) timings: RetrievalTimings,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct FeatureRecord {
    pub(crate) lat: f64,
    pub(crate) lon: f64,
    pub(crate) distance_km: f64,
    pub(crate) bearing_deg: f64,
    pub(crate) pressure_hpa: i64,
    pub(crate) geopotential_height_m: f64,
    pub(crate) rh_percent: f64,
    pub(crate) temperature_k: Option<f64>,
    pub(crate) omega_pa_s: Option<f64>,
    pub(crate) u_ms: Option<f64>,
    pub(crate) v_ms: Option<f64>,
    pub(crate) cloud_fraction_percent: Option<f64>,
    pub(crate) cloud_liquid_kg_kg: Option<f64>,
    pub(crate) cloud_ice_kg_kg: Option<f64>,
    pub(crate) source_lat: Option<f64>,
    pub(crate) source_lon: Option<f64>,
    pub(crate) source_distance_km: Option<f64>,
    pub(crate) source_bearing_deg: Option<f64>,
    pub(crate) surface_visibility_m: Option<f64>,
    pub(crate) precip_rate_kg_m2_s: Option<f64>,
    pub(crate) surface_temperature_k: Option<f64>,
    pub(crate) surface_dewpoint_k: Option<f64>,
    pub(crate) footprint_azimuth_width_deg: Option<f64>,
    pub(crate) footprint_near_distance_km: Option<f64>,
    pub(crate) footprint_far_distance_km: Option<f64>,
    pub(crate) horizontal_cloud_probability: Option<f64>,
    pub(crate) cloud_probability_given_cloud: Option<f64>,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ColumnSetRecord {
    pub(crate) schema_version: u64,
    pub(crate) valid_time_utc: String,
    pub(crate) source: String,
    pub(crate) vertical_prior_identity: String,
    pub(crate) retrieval_quality: String,
    pub(crate) low_cloud_state: String,
    pub(crate) columns: Vec<OutputColumn>,
    pub(crate) motion_dx_px_per_hour: f64,
    pub(crate) motion_dy_px_per_hour: f64,
}

#[derive(Clone, Copy)]
struct Point {
    lat: f64,
    lon: f64,
}

struct HsdFrame {
    valid_time: DateTime<Utc>,
    source: String,
    quality: String,
    latitude_bounds: (f64, f64),
    longitude_bounds: (f64, f64),
    latitudes: Vec<f32>,
    longitudes: Vec<f32>,
    bands: [Vec<f32>; 3],
    index: PixelIndex,
}

struct PixelIndex {
    buckets: BTreeMap<i64, Vec<usize>>,
    candidates: Vec<(i64, usize)>,
}

struct CachedFrame {
    version: crate::desktop_hsd_retrieval::BundleVersion,
    frame: Arc<HsdFrame>,
}

static FRAME_CACHE: OnceLock<Mutex<VecDeque<CachedFrame>>> = OnceLock::new();

fn finite(value: f64) -> bool {
    value.is_finite()
}

fn normalize_lon(value: f64) -> f64 {
    let value = (value + 180.0).rem_euclid(360.0) - 180.0;
    if value == -180.0 {
        180.0
    } else {
        value
    }
}

fn wrapped_delta(left: f64, right: f64) -> f64 {
    (left - right + 180.0).rem_euclid(360.0) - 180.0
}

fn longitude_bounds(values: &[f32]) -> Result<(f64, f64), String> {
    let mut normalized: Vec<f64> = values
        .iter()
        .map(|value| f64::from(*value).rem_euclid(360.0))
        .filter(|value| finite(*value))
        .collect();
    if normalized.is_empty() {
        return Err("HSD calibrated frame has no finite longitudes".into());
    }
    normalized.sort_by(f64::total_cmp);
    normalized.dedup_by(|left, right| *left == *right);
    if normalized.len() == 1 {
        return Ok((normalized[0], 0.0));
    }
    let mut largest_gap = f64::NEG_INFINITY;
    let mut start = normalized[0];
    for index in 0..normalized.len() {
        let following = if index + 1 == normalized.len() {
            normalized[0] + 360.0
        } else {
            normalized[index + 1]
        };
        let gap = following - normalized[index];
        if gap > largest_gap {
            largest_gap = gap;
            start = following.rem_euclid(360.0);
        }
    }
    Ok((start, 360.0 - largest_gap))
}

fn destination_point(origin: Point, bearing_deg: f64, distance_km: f64) -> Result<Point, String> {
    if !finite(origin.lat)
        || !(-90.0..=90.0).contains(&origin.lat)
        || !finite(origin.lon)
        || !(-180.0..=180.0).contains(&origin.lon)
        || !finite(bearing_deg)
        || !finite(distance_km)
        || distance_km < 0.0
    {
        return Err("nowcast sampling geometry is invalid".into());
    }
    let angular_distance = distance_km / EARTH_RADIUS_KM;
    let bearing = bearing_deg.to_radians();
    let lat1 = origin.lat.to_radians();
    let lon1 = origin.lon.to_radians();
    let lat2 = (lat1.sin() * angular_distance.cos()
        + lat1.cos() * angular_distance.sin() * bearing.cos())
    .asin();
    let lon2 = lon1
        + (bearing.sin() * angular_distance.sin() * lat1.cos())
            .atan2(angular_distance.cos() - lat1.sin() * lat2.sin());
    Ok(Point {
        lat: lat2.to_degrees(),
        lon: normalize_lon(lon2.to_degrees()),
    })
}

fn haversine_km(left: Point, right: Point) -> f64 {
    let lat1 = left.lat.to_radians();
    let lon1 = left.lon.to_radians();
    let lat2 = right.lat.to_radians();
    let lon2 = right.lon.to_radians();
    let sin_lat = ((lat2 - lat1) / 2.0).sin();
    let sin_lon = ((lon2 - lon1) / 2.0).sin();
    let h = sin_lat.powi(2) + lat1.cos() * lat2.cos() * sin_lon.powi(2);
    2.0 * EARTH_RADIUS_KM * h.sqrt().asin()
}

fn sample_points(azimuth_deg: f64, radius_km: f64) -> Result<Vec<(f64, f64)>, String> {
    if !finite(radius_km) || radius_km <= 0.0 {
        return Err("satellite sample radius must be positive".into());
    }
    if !finite(azimuth_deg) {
        return Err("satellite sample azimuth is invalid".into());
    }
    let mut distances: Vec<f64> = [0.0, 30.0, 70.0, 120.0]
        .into_iter()
        .filter(|distance| *distance <= radius_km + 1e-6)
        .collect();
    let outer_count = if radius_km < 160.0 {
        0
    } else {
        ((radius_km - 160.0) / OUTER_DISTANCE_STEP_KM).floor() as usize + 1
    };
    distances.extend((0..outer_count).map(|index| 160.0 + index as f64 * OUTER_DISTANCE_STEP_KM));
    if radius_km
        > distances
            .last()
            .copied()
            .ok_or("satellite sample distances are empty")?
            + 1e-6
    {
        distances.push(radius_km);
    }
    let mut samples = Vec::new();
    for distance in distances {
        if distance == 0.0 {
            samples.push((azimuth_deg, distance));
        } else if distance <= 120.0 {
            samples.extend((0..24).map(|bearing| (bearing as f64 * 15.0, distance)));
        } else {
            samples.extend(
                [-42.0, -28.0, -14.0, 0.0, 14.0, 28.0, 42.0]
                    .into_iter()
                    .map(|offset| ((azimuth_deg + offset).rem_euclid(360.0), distance)),
            );
        }
    }
    if samples.len() > MAX_SAMPLES {
        return Err("nowcast calibrated retrieval sample count exceeds bound".into());
    }
    Ok(samples)
}

fn load_frame_uncached(path: &std::path::Path) -> Result<HsdFrame, String> {
    let contract = frame(path)?;
    let verified = crate::tensor_bundle::verify_files(path, "hsd-compact-frame-v1")?;
    let shape = verified
        .shapes
        .get("latitudes")
        .ok_or("HSD calibrated frame has no latitude shape")?;
    if shape.len() != 2 || shape[0] == 0 || shape[1] == 0 {
        return Err("HSD calibrated frame shape is invalid".into());
    }
    let height = usize::try_from(shape[0]).map_err(|_| "HSD frame height is too large")?;
    let width = usize::try_from(shape[1]).map_err(|_| "HSD frame width is too large")?;
    if height
        .checked_mul(width)
        .is_none_or(|cells| cells > 16_000_000)
    {
        return Err("HSD calibrated frame size exceeds bound".into());
    }
    let bundle = F32Bundle::<Value>::open(path, "hsd-compact-frame-v1", &verified.source)?;
    let read_rows = |name: &str| -> Result<Vec<f32>, String> {
        bundle
            .rows::<1>(name, shape)
            .map(|rows| rows.into_iter().map(|[value]| value).collect())
    };
    let latitudes = read_rows("latitudes")?;
    let longitudes = read_rows("longitudes")?;
    let bands: [Vec<f32>; 3] = ["band_b07", "band_b13", "band_b15"]
        .into_iter()
        .map(read_rows)
        .collect::<Result<Vec<_>, _>>()?
        .try_into()
        .map_err(|_| "HSD calibrated bands are invalid")?;
    bundle.revalidate()?;
    if latitudes
        .iter()
        .chain(&longitudes)
        .any(|value| !value.is_finite())
        || bands
            .iter()
            .any(|values| values.iter().any(|value| value.is_infinite()))
    {
        return Err("HSD calibrated frame contains invalid values".into());
    }
    let index = PixelIndex::new(&latitudes, &longitudes);
    let latitude_min = latitudes
        .iter()
        .copied()
        .reduce(f32::min)
        .ok_or("HSD calibrated frame has no finite latitudes")?;
    let latitude_max = latitudes
        .iter()
        .copied()
        .reduce(f32::max)
        .ok_or("HSD calibrated frame has no finite latitudes")?;
    Ok(HsdFrame {
        valid_time: contract.valid_time,
        source: contract.source,
        quality: contract.quality,
        latitude_bounds: (f64::from(latitude_min), f64::from(latitude_max)),
        longitude_bounds: longitude_bounds(&longitudes)?,
        latitudes,
        longitudes,
        bands,
        index,
    })
}

fn load_frame(path: &std::path::Path) -> Result<Arc<HsdFrame>, String> {
    let version = crate::desktop_hsd_retrieval::bundle_version(path)?;
    {
        let mut cache = FRAME_CACHE.get_or_init(Mutex::default).lock().unwrap();
        if let Some(position) = cache.iter().position(|item| item.version == version) {
            let entry = cache.remove(position).expect("cached HSD frame exists");
            let frame = entry.frame.clone();
            cache.push_back(entry);
            return Ok(frame);
        }
    }
    let frame = Arc::new(load_frame_uncached(path)?);
    {
        let mut cache = FRAME_CACHE.get_or_init(Mutex::default).lock().unwrap();
        cache.retain(|item| item.version != version);
        cache.push_back(CachedFrame {
            version,
            frame: Arc::clone(&frame),
        });
        while cache.len() > MAX_CACHED_FRAMES {
            cache.pop_front();
        }
    }
    Ok(frame)
}

fn nearest_pixels(frame: &HsdFrame, targets: &[Point]) -> Vec<Option<usize>> {
    targets
        .iter()
        .map(|target| nearest_pixel(frame, *target))
        .collect()
}

impl PixelIndex {
    fn new(latitudes: &[f32], longitudes: &[f32]) -> Self {
        let mut buckets = BTreeMap::<i64, Vec<usize>>::new();
        for (index, (&lat, &lon)) in latitudes.iter().zip(longitudes).enumerate() {
            let lat = f64::from(lat);
            let lon = f64::from(lon);
            if finite(lat) && finite(lon) {
                buckets
                    .entry((lat / LATITUDE_BUCKET_DEGREES).floor() as i64)
                    .or_default()
                    .push(index);
            }
        }
        let buckets: BTreeMap<_, _> = buckets.into_iter().collect();
        let candidates = buckets
            .iter()
            .map(|(bucket, indices)| (*bucket, indices.len()))
            .collect();
        Self {
            buckets,
            candidates,
        }
    }
}

fn bucket_latitude_lower_bound(target_lat: f64, bucket: i64) -> f64 {
    let low = bucket as f64 * LATITUDE_BUCKET_DEGREES;
    let high = low + LATITUDE_BUCKET_DEGREES;
    if target_lat < low {
        low - target_lat
    } else if target_lat > high {
        target_lat - high
    } else {
        0.0
    }
}

fn nearest_pixel(frame: &HsdFrame, target: Point) -> Option<usize> {
    if !finite(target.lat) || !finite(target.lon) {
        return None;
    }
    if target.lat < frame.latitude_bounds.0 - 1e-6 || target.lat > frame.latitude_bounds.1 + 1e-6 {
        return None;
    }
    let longitude_offset =
        (target.lon.rem_euclid(360.0) - frame.longitude_bounds.0).rem_euclid(360.0);
    if longitude_offset > frame.longitude_bounds.1 + 1e-6 {
        return None;
    }
    let index = &frame.index;
    if index.buckets.is_empty() {
        return None;
    }
    let mut best = None;
    let mut best_distance = f64::INFINITY;
    let target_bucket = (target.lat / LATITUDE_BUCKET_DEGREES).floor() as i64;
    let mut candidates: Vec<(i64, usize)> = Vec::with_capacity(index.candidates.len());
    let insertion = index
        .candidates
        .binary_search_by_key(&target_bucket, |(bucket, _)| *bucket)
        .unwrap_or_else(|position| position);
    candidates.push(index.candidates[insertion.min(index.candidates.len() - 1)]);
    for offset in 1..index.candidates.len() {
        if let Some(candidate) = insertion.checked_sub(offset) {
            candidates.push(index.candidates[candidate]);
        }
        if let Some(candidate) = insertion.checked_add(offset) {
            if let Some(candidate) = index.candidates.get(candidate) {
                candidates.push(*candidate);
            }
        }
    }
    for (bucket, _) in candidates {
        let lower = bucket_latitude_lower_bound(target.lat, bucket);
        let lower_distance = lower * lower;
        if finite(best_distance) && lower_distance > best_distance {
            break;
        }
        let indices = &index.buckets[&bucket];
        for &candidate in indices {
            let lat = f64::from(frame.latitudes[candidate]);
            let lon = f64::from(frame.longitudes[candidate]);
            let distance = (lat - target.lat).powi(2) + wrapped_delta(lon, target.lon).powi(2);
            if distance < best_distance
                || (distance == best_distance && best.is_none_or(|current| candidate < current))
            {
                best = Some(candidate);
                best_distance = distance;
            }
        }
    }
    let candidate = best?;
    let nearest = Point {
        lat: f64::from(frame.latitudes[candidate]),
        lon: f64::from(frame.longitudes[candidate]),
    };
    (haversine_km(target, nearest) <= PIXEL_TOLERANCE_KM).then_some(candidate)
}

fn prior_value(value: &Value) -> Result<Prior, String> {
    let levels: Vec<Level> = serde_json::from_value(
        value
            .get("levels")
            .cloned()
            .ok_or("native GFS prior response has no levels")?,
    )
    .map_err(|error| format!("native GFS prior levels are invalid: {error}"))?;
    Ok(Prior {
        valid_time_utc: value
            .get("valid_time_utc")
            .and_then(Value::as_str)
            .ok_or("native GFS prior time is missing")?
            .to_owned(),
        source: value
            .get("source")
            .and_then(Value::as_str)
            .ok_or("native GFS prior source is missing")?
            .to_owned(),
        identity: value
            .get("identity")
            .and_then(Value::as_str)
            .ok_or("native GFS prior identity is missing")?
            .to_owned(),
        terrain_asl_m: value
            .get("terrain_asl_m")
            .and_then(Value::as_f64)
            .ok_or("native GFS prior terrain is missing")?,
        target_lat: value
            .get("target_lat")
            .and_then(Value::as_f64)
            .ok_or("native GFS prior latitude is missing")?,
        target_lon: value
            .get("target_lon")
            .and_then(Value::as_f64)
            .ok_or("native GFS prior longitude is missing")?,
        levels,
    })
}

fn build_prior_for_target(
    left: &DecodedGrid,
    right: Option<&DecodedGrid>,
    params: &Params,
    valid_time: DateTime<Utc>,
    cycle_time: DateTime<Utc>,
    target: Point,
) -> Result<Value, String> {
    let left_geometry = select_geometry(left, target.lat, target.lon, params.local_radius_km);
    let left_features = features_at(left, &left_geometry)?;
    let left_terrain = terrain_at(left, &left_geometry)?;
    let (right_features, right_terrain) = match right {
        Some(grid) => {
            let geometry = select_geometry(grid, target.lat, target.lon, params.local_radius_km);
            (features_at(grid, &geometry)?, terrain_at(grid, &geometry)?)
        }
        None => (left_features.clone(), left_terrain),
    };
    let identity = format!(
        "gfs-prior:{}:f{:03}-{:03}:{}:domain={}:point={:+.5},{:+.5}",
        cycle_time.format("%Y%m%d%H"),
        params.left_forecast_hour,
        params.right_forecast_hour,
        params.resolution,
        params.domain_hash,
        target.lat,
        target.lon
    );
    let features = interpolate(&left_features, &right_features, params.fraction)?;
    for feature in &features {
        validate_feature(feature)?;
    }
    build_prior(
        &PriorContext {
            valid_time,
            cycle_time,
            forecast_hour: params.left_forecast_hour,
            source: &params.source,
            fraction: params.fraction,
            features: &features,
        },
        &identity,
        left_terrain,
        right_terrain,
        target.lat,
        target.lon,
    )
}

fn prior_identity(prior: &Prior) -> &str {
    &prior.identity
}

fn round_coordinate(value: f64, scale: f64) -> f64 {
    (value * scale).round() / scale
}

#[allow(clippy::too_many_arguments)]
fn unknown_column(
    prior: Option<&Prior>,
    valid_time: DateTime<Utc>,
    target: Point,
    distance_km: f64,
    bearing_deg: f64,
    source: &str,
    motion_dx_px_per_hour: f64,
    motion_dy_px_per_hour: f64,
) -> OutputColumn {
    let fallback_identity;
    let (identity, prior_source, terrain) = if let Some(prior) = prior {
        (
            prior.identity.as_str(),
            prior.source.as_str(),
            Some(prior.terrain_asl_m),
        )
    } else {
        fallback_identity = format!("unknown-sample:{:+.5},{:+.5}", target.lat, target.lon);
        (fallback_identity.as_str(), "unknown", None)
    };
    OutputColumn::unknown(
        valid_time,
        target.lat,
        target.lon,
        distance_km,
        bearing_deg.rem_euclid(360.0),
        terrain,
        source,
        identity,
        prior_source,
        motion_dx_px_per_hour,
        motion_dy_px_per_hour,
    )
}

fn aggregate_quality(values: impl IntoIterator<Item = String>, total_columns: usize) -> String {
    let scores: Vec<f64> = values
        .into_iter()
        .filter_map(|value| match value.as_str() {
            "low" => Some(1.0),
            "medium" => Some(2.0),
            "high" => Some(3.0),
            _ => None,
        })
        .collect();
    let coverage = scores.len() as f64 / total_columns.max(1) as f64;
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

fn aggregate_low_state<'a>(values: impl IntoIterator<Item = &'a str>) -> String {
    let states: Vec<&str> = values
        .into_iter()
        .filter(|state| matches!(*state, "detected" | "clear" | "unknown"))
        .collect();
    if states.is_empty() {
        return "unknown".into();
    }
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

fn column_set_record(
    frame: &HsdFrame,
    identity: &str,
    columns: &[OutputColumn],
    motion_dx_px_per_hour: f64,
    motion_dy_px_per_hour: f64,
) -> ColumnSetRecord {
    let quality = aggregate_quality(
        columns
            .iter()
            .map(|column| column.retrieval_quality.clone()),
        columns.len(),
    );
    let low_state = aggregate_low_state(
        columns
            .iter()
            .filter(|column| column.distance_km <= 120.0)
            .map(|column| column.low_cloud_state.as_str()),
    );
    ColumnSetRecord {
        schema_version: if let Some(first) = columns.first() {
            first.schema_version
        } else {
            0
        },
        valid_time_utc: time_contract::iso(frame.valid_time),
        source: frame.source.clone(),
        vertical_prior_identity: identity.to_owned(),
        retrieval_quality: quality,
        low_cloud_state: low_state,
        columns: columns.to_vec(),
        motion_dx_px_per_hour,
        motion_dy_px_per_hour,
    }
}

fn merge_column_profile(
    column: &OutputColumn,
    prior: &Prior,
    merged_profile: &mut Vec<(i64, f64, Level)>,
    upper_low_cutoff: &mut Option<f64>,
) -> Result<(), String> {
    if column.low_cloud_state == "clear" || column.cloud_probability_by_height.is_empty() {
        return Ok(());
    }
    let horizontal = column
        .horizontal_cloud_probability
        .filter(|value| finite(*value) && *value > 0.0);
    if horizontal.is_none() {
        return Ok(());
    };
    for level in &prior.levels {
        let probability = *column
            .cloud_probability_by_height
            .get(&level.height_asl_m.to_string())
            .unwrap_or(&0.0);
        if probability < 0.08
            || column
                .top_asl_m
                .is_some_and(|top| level.height_asl_m > top + 500.0)
        {
            continue;
        }
        let entry = if let Some(entry) = merged_profile
            .iter_mut()
            .find(|entry| entry.0 == level.pressure_hpa)
        {
            entry
        } else {
            merged_profile.push((level.pressure_hpa, 0.0, level.clone()));
            merged_profile
                .last_mut()
                .expect("merged pressure level was just appended")
        };
        entry.1 = 1.0 - (1.0 - entry.1) * (1.0 - probability);
    }
    if let Some(top) = column.top_asl_m {
        if let Some(terrain) = column.terrain_asl_m {
            if top - terrain > 3500.0 {
                let cutoff = top - 2500.0;
                *upper_low_cutoff =
                    Some(upper_low_cutoff.map_or(cutoff, |existing| existing.max(cutoff)));
            }
        }
    }
    Ok(())
}

fn features_for_column(
    columns: &[OutputColumn],
    prior: &Prior,
    footprint: (f64, f64, f64),
) -> Result<Vec<FeatureRecord>, String> {
    let mut merged: Vec<(i64, f64, Level)> = Vec::new();
    let mut upper_low_cutoff = None;
    for column in columns {
        merge_column_profile(column, prior, &mut merged, &mut upper_low_cutoff)?;
    }
    let horizontal = columns
        .iter()
        .filter_map(|column| column.horizontal_cloud_probability)
        .filter(|value| finite(*value))
        .fold(0.0, f64::max);
    if merged.is_empty() || horizontal <= 0.0 {
        return Ok(Vec::new());
    }
    if let Some(cutoff) = upper_low_cutoff {
        for (_, probability, level) in &mut merged {
            if level.height_asl_m < cutoff {
                *probability = probability.min(0.20 * horizontal);
            }
        }
    }
    let Some(first) = columns.first() else {
        return Ok(Vec::new());
    };
    let mut output = Vec::new();
    for (_, probability, level) in merged {
        let conditional = (probability / horizontal.max(1e-9)).clamp(0.0, 1.0);
        output.push(FeatureRecord {
            lat: round_coordinate(first.lat, 100_000.0),
            lon: round_coordinate(first.lon, 100_000.0),
            distance_km: round_coordinate(first.distance_km, 100.0),
            bearing_deg: round_coordinate(first.bearing_deg, 100.0),
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
    Ok(output)
}

pub(crate) fn execute_retrieval(params: &Params) -> Result<RetrievalResult, String> {
    if params.local_radius_km <= 0.0
        || !finite(params.local_radius_km)
        || !finite(params.fraction)
        || !(0.0..=1.0).contains(&params.fraction)
        || !finite(params.motion_dx_px_per_hour)
        || !finite(params.motion_dy_px_per_hour)
        || params.domain_hash.is_empty()
        || params.resolution.is_empty()
    {
        return Err("nowcast calibrated retrieval parameters are invalid".into());
    }
    let valid_time = time_contract::parse_utc(&params.valid_time_utc)?;
    let cycle_time = time_contract::parse_utc(&params.cycle_time_utc)?;
    let started = std::time::Instant::now();
    let hsd = load_frame(&params.manifest_path)?;
    let frame_load_ms = started.elapsed().as_secs_f64() * 1000.0;
    if hsd.valid_time != valid_time {
        return Err("nowcast HSD frame time does not match the retrieval request".into());
    }
    let point = Point {
        lat: params.point_lat,
        lon: params.point_lon,
    };
    let sampling_started = std::time::Instant::now();
    let samples = sample_points(params.azimuth_deg, params.radius_km)?;
    let targets: Vec<(f64, f64, Point)> = samples
        .iter()
        .map(|(bearing, distance)| {
            destination_point(point, *bearing, *distance)
                .map(|target| (*bearing, *distance, target))
        })
        .collect::<Result<Vec<_>, _>>()?;
    let target_points: Vec<Point> = targets.iter().map(|(_, _, target)| *target).collect();
    let pixels = nearest_pixels(&hsd, &target_points);
    let hsd_sampling_ms = sampling_started.elapsed().as_secs_f64() * 1000.0;
    let distances: Vec<f64> = {
        let mut values: Vec<f64> = samples.iter().map(|(_, distance)| *distance).collect();
        values.sort_by(f64::total_cmp);
        values.dedup_by(|left, right| *left == *right);
        values
    };
    let started = std::time::Instant::now();
    let left = read_cached_decoded_grid(&params.left_cache_path)?;
    let right = if params.right_cache_path == params.left_cache_path {
        None
    } else {
        Some(read_cached_decoded_grid(&params.right_cache_path)?)
    };
    if right
        .as_ref()
        .is_some_and(|grid| grid.latitudes != left.latitudes || grid.longitudes != left.longitudes)
    {
        return Err("nowcast decoded GFS left/right domains do not match".into());
    }
    let gfs_load_ms = started.elapsed().as_secs_f64() * 1000.0;
    let started = std::time::Instant::now();
    let mut output: Vec<Vec<OutputColumn>> = Vec::with_capacity(samples.len());
    let mut priors: Vec<Option<Prior>> = Vec::with_capacity(samples.len());
    let mut features = Vec::new();
    for ((bearing_deg, distance_km, target), pixel) in targets.into_iter().zip(pixels) {
        let prior_result = build_prior_for_target(
            &left,
            right.as_deref(),
            params,
            valid_time,
            cycle_time,
            target,
        )
        .and_then(|prior_json| prior_value(&prior_json));
        let prior = prior_result.ok();
        let index = distances
            .binary_search_by(|value| value.total_cmp(&distance_km))
            .unwrap_or_else(|_| unreachable!("sample distance must be present"));
        let previous = if index == 0 {
            0.0
        } else {
            distances[index - 1]
        };
        let following = distances
            .get(index + 1)
            .copied()
            .unwrap_or(params.radius_km);
        let near = if index == 0 {
            0.0
        } else {
            0.5 * (previous + distance_km)
        };
        let far = params.radius_km.min(if index + 1 == distances.len() {
            distance_km
        } else {
            0.5 * (distance_km + following)
        });
        let width = if distance_km <= 120.0 { 15.0 } else { 14.0 };
        let footprint = (width, near, near.max(far));
        let Some(prior) = prior else {
            output.push(vec![unknown_column(
                None,
                valid_time,
                target,
                distance_km,
                bearing_deg,
                &hsd.source,
                params.motion_dx_px_per_hour,
                params.motion_dy_px_per_hour,
            )]);
            priors.push(None);
            continue;
        };
        let Some(pixel) = pixel else {
            output.push(vec![unknown_column(
                Some(&prior),
                valid_time,
                target,
                distance_km,
                bearing_deg,
                &hsd.source,
                params.motion_dx_px_per_hour,
                params.motion_dy_px_per_hour,
            )]);
            priors.push(Some(prior));
            continue;
        };
        let channels = [
            hsd.bands[0][pixel],
            hsd.bands[1][pixel],
            hsd.bands[2][pixel],
        ];
        let Some(channels) = channels
            .into_iter()
            .map(|value| finite(f64::from(value)).then_some(f64::from(value)))
            .collect::<Option<Vec<_>>>()
        else {
            output.push(vec![unknown_column(
                Some(&prior),
                valid_time,
                target,
                distance_km,
                bearing_deg,
                &hsd.source,
                params.motion_dx_px_per_hour,
                params.motion_dy_px_per_hour,
            )]);
            priors.push(Some(prior));
            continue;
        };
        let sample = Sample {
            lat: target.lat,
            lon: target.lon,
            distance_km,
            bearing_deg: bearing_deg.rem_euclid(360.0),
            b07: Some(channels[0]),
            b13: Some(channels[1]),
            b15: Some(channels[2]),
            motion_dx_px_per_hour: params.motion_dx_px_per_hour,
            motion_dy_px_per_hour: params.motion_dy_px_per_hour,
            prior: prior.clone(),
        };
        let columns = retrieve_columns(&sample, valid_time, &hsd.source, &hsd.quality)?;
        if columns.is_empty() {
            output.push(vec![unknown_column(
                Some(&prior),
                valid_time,
                target,
                distance_km,
                bearing_deg,
                &hsd.source,
                params.motion_dx_px_per_hour,
                params.motion_dy_px_per_hour,
            )]);
            priors.push(Some(prior));
            continue;
        }
        features.extend(features_for_column(&columns, &prior, footprint)?);
        output.push(columns);
        priors.push(Some(prior));
    }
    let retrieval_ms = started.elapsed().as_secs_f64() * 1000.0;
    let started = std::time::Instant::now();
    let mut grouped: Vec<(String, Vec<OutputColumn>)> = Vec::new();
    let mut grouped_positions = HashMap::<String, usize>::new();
    let mut unknown_sets = Vec::new();
    for (columns, prior) in output.iter().zip(&priors) {
        let usable = columns
            .iter()
            .any(|column| column.retrieval_quality != "unknown");
        let identity = prior
            .as_ref()
            .map(prior_identity)
            .unwrap_or(&columns[0].vertical_prior_identity)
            .to_owned();
        if !usable {
            unknown_sets.push(column_set_record(
                &hsd,
                &identity,
                columns,
                params.motion_dx_px_per_hour,
                params.motion_dy_px_per_hour,
            ));
            continue;
        }
        let Some(prior) = prior else {
            unreachable!("usable retrieval must have a vertical prior");
        };
        let position = if let Some(position) = grouped_positions.get(&prior.identity) {
            *position
        } else {
            let position = grouped.len();
            grouped_positions.insert(prior.identity.clone(), position);
            grouped.push((prior.identity.clone(), Vec::new()));
            position
        };
        grouped[position].1.extend(columns.iter().cloned());
    }
    let mut column_sets: Vec<ColumnSetRecord> = grouped
        .into_iter()
        .map(|(identity, columns)| {
            column_set_record(
                &hsd,
                &identity,
                &columns,
                params.motion_dx_px_per_hour,
                params.motion_dy_px_per_hour,
            )
        })
        .collect();
    column_sets.extend(unknown_sets);
    Ok(RetrievalResult {
        features,
        column_sets,
        metrics: horizontal_metrics(&output, params.azimuth_deg),
        priors,
        sample_points: samples,
        timings: RetrievalTimings {
            hsd_frame_load_ms: frame_load_ms,
            hsd_sampling_ms,
            gfs_grid_load_ms: gfs_load_ms,
            retrieval_ms,
            assembly_ms: started.elapsed().as_secs_f64() * 1000.0,
        },
    })
}

pub fn execute(params: Params) -> Result<Value, String> {
    let output_path = params.output_path.clone();
    let result = execute_retrieval(&params)?;
    let value = json!({
        "contract": "rust-desktop-nowcast-calibrated-retrieval-v1",
        "runtime_python_used": false,
        "features": result.features,
        "columns": result.column_sets,
        "metrics": json!(result.metrics),
        "timings_ms": {
            "hsd_frame_load": result.timings.hsd_frame_load_ms,
            "hsd_sampling": result.timings.hsd_sampling_ms,
            "gfs_grid_load": result.timings.gfs_grid_load_ms,
            "retrieval": result.timings.retrieval_ms,
            "assembly": result.timings.assembly_ms,
        },
        "priors": result.priors
            .iter()
            .map(|prior| prior.as_ref().map_or(Value::Null, |prior| json!(prior)))
            .collect::<Vec<_>>(),
    });
    let Some(output_path) = output_path else {
        return Ok(value);
    };
    let bytes = serde_json::to_vec(&value).map_err(|e| e.to_string())?;
    if bytes.len() > MAX_RESULT_BYTES {
        return Err("nowcast calibrated retrieval output exceeds 128 MiB".into());
    }
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&output_path)
        .map_err(|e| e.to_string())?;
    if let Err(error) = output.write_all(&bytes).and_then(|_| output.sync_all()) {
        drop(output);
        let _ = fs::remove_file(&output_path);
        return Err(error.to_string());
    }
    Ok(json!({
        "contract": "rust-desktop-nowcast-calibrated-retrieval-file-v1",
        "runtime_python_used": false,
        "output_path": output_path,
        "bytes": bytes.len(),
        "sha256": Sha256::digest(&bytes).iter().map(|byte| format!("{byte:02x}")).collect::<String>(),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame_at(latitudes: Vec<f32>, longitudes: Vec<f32>) -> HsdFrame {
        let indices = PixelIndex::new(&latitudes, &longitudes);
        let latitude_bounds = (
            latitudes.iter().copied().fold(f32::INFINITY, f32::min) as f64,
            latitudes.iter().copied().fold(f32::NEG_INFINITY, f32::max) as f64,
        );
        let longitude_bounds = longitude_bounds(&longitudes).unwrap();
        HsdFrame {
            valid_time: DateTime::parse_from_rfc3339("2026-11-09T08:00:00Z")
                .unwrap()
                .with_timezone(&Utc),
            source: "unit-test".into(),
            quality: "medium".into(),
            latitude_bounds,
            longitude_bounds,
            latitudes,
            longitudes,
            bands: [Vec::new(), Vec::new(), Vec::new()],
            index: indices,
        }
    }

    #[test]
    fn nearest_pixel_wraps_across_the_date_line() {
        let frame = frame_at(vec![10.0, 10.0], vec![179.99, -179.99]);
        let pixel = nearest_pixel(
            &frame,
            Point {
                lat: 10.0,
                lon: -179.999,
            },
        );
        assert_eq!(pixel, Some(1));
    }

    #[test]
    fn nearest_pixel_rejects_a_target_outside_the_footprint() {
        let frame = frame_at(vec![10.0, 10.0], vec![179.8, 179.9]);
        assert_eq!(
            nearest_pixel(
                &frame,
                Point {
                    lat: 10.0,
                    lon: 120.0,
                },
            ),
            None
        );
    }

    #[test]
    fn nearest_pixel_breaks_equal_distance_ties_by_storage_order() {
        let frame = frame_at(vec![10.0, 10.0], vec![100.0, 100.0]);
        assert_eq!(
            nearest_pixel(
                &frame,
                Point {
                    lat: 10.0,
                    lon: 100.0,
                },
            ),
            Some(0)
        );
    }

    #[test]
    fn nearest_pixel_enforces_pixel_tolerance() {
        let frame = frame_at(
            vec![10.0, 10.0, 10.0, 10.0, 10.0, 10.0],
            (0..6).map(|index| 100.0 + index as f32 * 0.01).collect(),
        );
        assert_eq!(
            nearest_pixel(
                &frame,
                Point {
                    lat: 10.0,
                    lon: 100.031,
                },
            ),
            Some(3)
        );
        assert_eq!(
            nearest_pixel(
                &frame,
                Point {
                    lat: 10.0,
                    lon: 100.07,
                },
            ),
            None
        );
    }
}
