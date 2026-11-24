//! Native construction of one interpolated GFS vertical prior.
//!
//! The provider owns cache lookup and GRIB decoding. The native batch entry
//! point below can also consume the validated Python decoded-grid sidecar
//! directly, avoiding repeated whole-grid extraction for each sample.

use chrono::{DateTime, Utc};
use npyz::NpyFile;
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::{BTreeMap, VecDeque};
use std::fs::{self, File};
use std::io::{BufReader, Read};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::SystemTime;

const MAX_FEATURES: usize = 50_000;
const MAX_LEVELS: usize = 256;
const MAX_DOMAIN_POINTS: usize = 5_000_000;
const MAX_DOMAIN_FIELDS: usize = 1_024;
const MAX_SAMPLES: usize = 2_000;
const DECODED_GRID_CACHE_VERSION: i64 = 7;
const MAX_CACHED_GRIDS: usize = 4;
const EARTH_RADIUS_KM: f64 = 6371.0;
const PRESSURE_LEVELS: [i64; 11] = [200, 250, 300, 400, 500, 600, 700, 750, 800, 850, 925];
const DECODED_GRID_FILES: [&str; 3] = ["manifest.json", "coordinates.npy", "fields.npy"];

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    valid_time_utc: String,
    cycle_time_utc: String,
    forecast_hour: u32,
    source: String,
    identity: String,
    terrain_left_asl_m: f64,
    terrain_right_asl_m: f64,
    target_lat: f64,
    target_lon: f64,
    fraction: f64,
    left_features: Vec<Feature>,
    right_features: Vec<Feature>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BatchParams {
    requests: Vec<Params>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SharedBatchParams {
    valid_time_utc: String,
    cycle_time_utc: String,
    forecast_hour: u32,
    source: String,
    fraction: f64,
    left_features: Vec<Feature>,
    right_features: Vec<Feature>,
    requests: Vec<SampleParams>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SampleParams {
    identity: String,
    terrain_left_asl_m: f64,
    terrain_right_asl_m: f64,
    target_lat: f64,
    target_lon: f64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecodedBatchParams {
    valid_time_utc: String,
    cycle_time_utc: String,
    left_forecast_hour: u32,
    right_forecast_hour: u32,
    source: String,
    fraction: f64,
    local_radius_km: f64,
    left_cache_path: PathBuf,
    right_cache_path: PathBuf,
    domain_hash: String,
    resolution: String,
    requests: Vec<DecodedSampleParams>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DecodedSampleParams {
    target_lat: f64,
    target_lon: f64,
}

pub(crate) struct PriorContext<'a> {
    pub(crate) valid_time: DateTime<Utc>,
    pub(crate) cycle_time: DateTime<Utc>,
    pub(crate) forecast_hour: u32,
    pub(crate) source: &'a str,
    pub(crate) fraction: f64,
    pub(crate) features: &'a [Feature],
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DecodedManifest {
    version: i64,
    fields: Vec<DecodedField>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DecodedField {
    level_type: String,
    level: i64,
    name: String,
    index: usize,
}

pub(crate) struct DecodedGrid {
    pub(crate) latitudes: Vec<f32>,
    pub(crate) longitudes: Vec<f32>,
    fields: Vec<Vec<f32>>,
    keys: BTreeMap<(String, i64, String), usize>,
}

#[derive(Clone, PartialEq, Eq)]
struct DecodedGridVersion {
    root: PathBuf,
    files: Vec<(PathBuf, u64, SystemTime)>,
}

struct CachedDecodedGrid {
    version: DecodedGridVersion,
    grid: Arc<DecodedGrid>,
}

static DECODED_GRID_CACHE: OnceLock<Mutex<VecDeque<CachedDecodedGrid>>> = OnceLock::new();

pub(crate) struct Geometry {
    indices: Vec<usize>,
    distance_km: Vec<f64>,
}

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Feature {
    pub(crate) lat: f64,
    pub(crate) lon: f64,
    pub(crate) pressure_hpa: i64,
    pub(crate) geopotential_height_m: f64,
    pub(crate) rh_percent: f64,
    pub(crate) temperature_k: Option<f64>,
    pub(crate) cloud_fraction_percent: Option<f64>,
}

fn utc(value: &str, name: &str) -> Result<DateTime<Utc>, String> {
    crate::time_contract::parse_utc(value).map_err(|error| format!("{name} is invalid: {error}"))
}

fn finite(value: f64) -> bool {
    value.is_finite()
}

fn decoded_grid_version(root: &Path) -> Result<DecodedGridVersion, String> {
    if !root.is_absolute() {
        return Err("decoded GFS cache path must be absolute".into());
    }
    let root_metadata = fs::symlink_metadata(root).map_err(|e| e.to_string())?;
    if !root_metadata.file_type().is_dir() {
        return Err("decoded GFS cache must be a directory".into());
    }
    let files = DECODED_GRID_FILES
        .into_iter()
        .map(|name| {
            let path = root.join(name);
            let metadata = fs::symlink_metadata(&path).map_err(|e| e.to_string())?;
            if !metadata.file_type().is_file() {
                return Err("decoded GFS cache file must be a regular file".into());
            }
            Ok((
                path,
                metadata.len(),
                metadata.modified().map_err(|e| e.to_string())?,
            ))
        })
        .collect::<Result<Vec<_>, String>>()?;
    Ok(DecodedGridVersion {
        root: root.to_path_buf(),
        files,
    })
}

pub(crate) fn read_cached_decoded_grid(root: &Path) -> Result<Arc<DecodedGrid>, String> {
    let version = decoded_grid_version(root)?;
    {
        let mut cache = DECODED_GRID_CACHE
            .get_or_init(Mutex::default)
            .lock()
            .map_err(|_| "decoded GFS cache lock is poisoned")?;
        if let Some(position) = cache.iter().position(|item| item.version == version) {
            let entry = cache
                .remove(position)
                .ok_or("cached decoded GFS grid disappeared")?;
            let grid = Arc::clone(&entry.grid);
            cache.push_back(entry);
            return Ok(grid);
        }
    }
    let grid = Arc::new(read_decoded_grid(root)?);
    let mut cache = DECODED_GRID_CACHE
        .get_or_init(Mutex::default)
        .lock()
        .map_err(|_| "decoded GFS cache lock is poisoned")?;
    cache.retain(|item| item.version != version);
    cache.push_back(CachedDecodedGrid {
        version,
        grid: Arc::clone(&grid),
    });
    while cache.len() > MAX_CACHED_GRIDS {
        cache.pop_front();
    }
    Ok(grid)
}

fn coordinate_key(lat: f64, lon: f64, pressure_hpa: i64) -> (i64, i64, i64) {
    (
        (lat * 100_000.0).round() as i64,
        (lon * 100_000.0).round() as i64,
        pressure_hpa,
    )
}

fn read_npy_f32(path: &Path) -> Result<(Vec<f32>, Vec<u64>), String> {
    let metadata = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if !metadata.file_type().is_file() {
        return Err("decoded GFS array must be a regular file, not a symlink".into());
    }
    let mut file = File::open(path).map_err(|e| e.to_string())?;
    let array =
        NpyFile::new(BufReader::with_capacity(64 * 1024, &mut file)).map_err(|e| e.to_string())?;
    if array.dtype().descr() != "'<f4'" {
        return Err("decoded GFS array dtype mismatch".into());
    }
    let shape = array.shape().to_vec();
    let values = array.into_vec::<f32>().map_err(|e| e.to_string())?;
    Ok((values, shape))
}

pub(crate) fn read_decoded_grid(root: &Path) -> Result<DecodedGrid, String> {
    let root_meta = fs::symlink_metadata(root).map_err(|e| e.to_string())?;
    if !root_meta.file_type().is_dir() {
        return Err("decoded GFS cache must be a directory".into());
    }
    let manifest_path = root.join("manifest.json");
    let manifest_meta = fs::symlink_metadata(&manifest_path).map_err(|e| e.to_string())?;
    if !manifest_meta.file_type().is_file() {
        return Err("decoded GFS manifest must be a regular file".into());
    }
    let mut manifest_file = File::open(&manifest_path).map_err(|e| e.to_string())?;
    let mut manifest_bytes = Vec::new();
    (&mut manifest_file)
        .take(256 * 1024 + 1)
        .read_to_end(&mut manifest_bytes)
        .map_err(|e| e.to_string())?;
    if manifest_bytes.len() > 256 * 1024 {
        return Err("decoded GFS manifest exceeds bound".into());
    }
    let manifest: DecodedManifest =
        serde_json::from_slice(&manifest_bytes).map_err(|e| e.to_string())?;
    if manifest.version != DECODED_GRID_CACHE_VERSION
        || manifest.fields.is_empty()
        || manifest.fields.len() > MAX_DOMAIN_FIELDS
    {
        return Err("decoded GFS cache contract mismatch".into());
    }
    let (coordinates, coordinate_shape) = read_npy_f32(&root.join("coordinates.npy"))?;
    if coordinate_shape.len() != 2
        || coordinate_shape[0] != 2
        || coordinate_shape[1] == 0
        || coordinates.len() as u64 != coordinate_shape.iter().product::<u64>()
    {
        return Err("decoded GFS coordinate array has an invalid shape".into());
    }
    let points = coordinate_shape[1] as usize;
    if points > MAX_DOMAIN_POINTS {
        return Err("decoded GFS coordinate array exceeds bound".into());
    }
    let (flat_fields, field_shape) = read_npy_f32(&root.join("fields.npy"))?;
    if field_shape != [manifest.fields.len() as u64, points as u64]
        || flat_fields.len() as u64 != field_shape.iter().product::<u64>()
    {
        return Err("decoded GFS field array has an invalid shape".into());
    }
    let latitudes = coordinates[..points].to_vec();
    let longitudes = coordinates[points..].to_vec();
    if latitudes
        .iter()
        .chain(longitudes.iter())
        .any(|value| !value.is_finite())
    {
        return Err("decoded GFS coordinates are non-finite".into());
    }
    let mut keys = BTreeMap::new();
    for field in &manifest.fields {
        let key = (field.level_type.clone(), field.level, field.name.clone());
        if field.index >= manifest.fields.len() || keys.insert(key, field.index).is_some() {
            return Err("decoded GFS field identity is invalid or duplicated".into());
        }
    }
    if keys.len() != manifest.fields.len()
        || (0..manifest.fields.len())
            .any(|index| !manifest.fields.iter().any(|field| field.index == index))
    {
        return Err("decoded GFS field indexes are incomplete".into());
    }
    let fields = flat_fields
        .chunks_exact(points)
        .map(<[f32]>::to_vec)
        .collect();
    Ok(DecodedGrid {
        latitudes,
        longitudes,
        fields,
        keys,
    })
}

fn bounding_box(lat: f64, lon: f64, radius_km: f64) -> (f64, f64, f64, f64) {
    let lat_pad = radius_km / 111.0;
    let lon_pad = radius_km / (111.0 * lat.to_radians().cos()).max(25.0);
    let normalize = |value: f64| (value + 180.0).rem_euclid(360.0) - 180.0;
    (
        normalize(lon - lon_pad),
        normalize(lon + lon_pad),
        (lat + lat_pad).min(90.0),
        (lat - lat_pad).max(-90.0),
    )
}

pub(crate) fn select_geometry(
    grid: &DecodedGrid,
    target_lat: f64,
    target_lon: f64,
    radius_km: f64,
) -> Geometry {
    let (left, right, top, bottom) = bounding_box(target_lat, target_lon, radius_km);
    let observer_lat = target_lat.to_radians();
    let observer_lon = target_lon.to_radians();
    let mut indices = Vec::new();
    let mut distance_km = Vec::new();
    for (index, (&lat, &lon)) in grid.latitudes.iter().zip(&grid.longitudes).enumerate() {
        let lat_f64 = f64::from(lat);
        let lon_f64 = f64::from(lon);
        if !(lat <= top as f32
            && lat >= bottom as f32
            && if left <= right {
                lon >= left as f32 && lon <= right as f32
            } else {
                lon >= left as f32 || lon <= right as f32
            })
        {
            continue;
        }
        let target_lat_rad = lat_f64.to_radians();
        let target_lon_rad = lon_f64.to_radians();
        let delta_lat = target_lat_rad - observer_lat;
        let delta_lon = (target_lon_rad - observer_lon + std::f64::consts::PI)
            .rem_euclid(2.0 * std::f64::consts::PI)
            - std::f64::consts::PI;
        let a = (delta_lat / 2.0).sin().powi(2)
            + observer_lat.cos() * target_lat_rad.cos() * (delta_lon / 2.0).sin().powi(2);
        let distance = EARTH_RADIUS_KM * 2.0 * a.sqrt().atan2((1.0 - a).max(0.0).sqrt());
        indices.push(index);
        distance_km.push(distance);
    }
    Geometry {
        indices,
        distance_km,
    }
}

pub(crate) fn terrain_at(grid: &DecodedGrid, geometry: &Geometry) -> Result<f64, String> {
    let terrain = grid
        .keys
        .get(&("surface".into(), 0, "orog".into()))
        .and_then(|&index| grid.fields.get(index))
        .ok_or("GFS vertical prior has no terrain field")?;
    let mut candidates: Vec<_> = geometry
        .indices
        .iter()
        .copied()
        .zip(geometry.distance_km.iter().copied())
        .filter(|(index, distance)| distance.is_finite() && terrain[*index].is_finite())
        .collect();
    if candidates.is_empty() {
        return Err("GFS terrain field has no finite local samples".into());
    }
    candidates.sort_by(|left, right| {
        left.1
            .partial_cmp(&right.1)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| left.0.cmp(&right.0))
    });
    let nearest = &candidates[..candidates.len().min(4)];
    let exact = nearest
        .iter()
        .find(|(_, distance)| *distance <= 1e-6)
        .map(|(index, _)| f64::from(terrain[*index]))
        .filter(|value| value.is_finite() && (-500.0..=9000.0).contains(value));
    if let Some(value) = exact {
        return Ok(value);
    }
    let weights: Vec<f64> = nearest
        .iter()
        .map(|(_, distance)| 1.0 / distance.max(1e-3))
        .collect();
    let weight_sum: f64 = weights.iter().sum();
    if !weight_sum.is_finite() || weight_sum <= 0.0 {
        return Err("GFS terrain field has invalid local weights".into());
    }
    let value = nearest
        .iter()
        .zip(weights)
        .map(|((index, _), weight)| f64::from(terrain[*index]) * weight)
        .sum::<f64>()
        / weight_sum;
    if !value.is_finite() || !(-500.0..=9000.0).contains(&value) {
        return Err("GFS terrain field is invalid".into());
    }
    Ok(value)
}

fn field<'a>(
    grid: &'a DecodedGrid,
    level_type: &str,
    level: i64,
    names: &[&str],
) -> Option<&'a [f32]> {
    names.iter().find_map(|name| {
        grid.keys
            .get(&(level_type.to_string(), level, (*name).to_string()))
            .and_then(|&index| grid.fields.get(index))
            .map(Vec::as_slice)
    })
}

pub(crate) fn features_at(grid: &DecodedGrid, geometry: &Geometry) -> Result<Vec<Feature>, String> {
    if geometry.indices.is_empty() {
        return Ok(Vec::new());
    }
    let high_cloud = field(grid, "highCloudLayer", 0, &["hcc", "hcdc", "tcc", "tcdc"]);
    let mut output = Vec::new();
    for level in PRESSURE_LEVELS {
        let height = field(grid, "isobaricInhPa", level, &["gh"]);
        let humidity = field(grid, "isobaricInhPa", level, &["r"]);
        if height.is_none() && humidity.is_none() {
            continue;
        }
        let height =
            height.ok_or_else(|| format!("GFS vertical prior field missing at {level} hPa: gh"))?;
        let humidity = humidity
            .ok_or_else(|| format!("GFS vertical prior field missing at {level} hPa: r"))?;
        if geometry
            .indices
            .iter()
            .any(|&index| !height[index].is_finite() || !humidity[index].is_finite())
        {
            return Err(format!(
                "GFS vertical prior field is non-finite at {level} hPa"
            ));
        }
        let temperature = field(grid, "isobaricInhPa", level, &["t"]);
        let cloud = field(grid, "isobaricInhPa", level, &["tcc", "tcdc"]);
        for (&index, _) in geometry.indices.iter().zip(&geometry.distance_km) {
            let mut cloud_value = cloud.map(|values| f64::from(values[index]));
            if level == 300
                && high_cloud.is_some_and(|values| values[index].is_finite())
                && cloud_value.is_none_or(|value| !value.is_finite())
            {
                cloud_value = high_cloud.map(|values| f64::from(values[index]));
            }
            let rh = f64::from(humidity[index]);
            output.push(Feature {
                lat: f64::from(grid.latitudes[index]),
                lon: f64::from(grid.longitudes[index]),
                pressure_hpa: level,
                geopotential_height_m: f64::from(height[index]),
                rh_percent: if (-5.0..=120.0).contains(&rh) {
                    rh.clamp(0.0, 100.0)
                } else {
                    rh
                },
                temperature_k: temperature.map(|values| f64::from(values[index])),
                cloud_fraction_percent: cloud_value,
            });
        }
    }
    Ok(output)
}

pub(crate) fn validate_feature(feature: &Feature) -> Result<(), String> {
    if !finite(feature.lat)
        || !(-90.0..=90.0).contains(&feature.lat)
        || !finite(feature.lon)
        || !(-180.0..=180.0).contains(&feature.lon)
        || feature.pressure_hpa <= 0
        || !finite(feature.geopotential_height_m)
        || !finite(feature.rh_percent)
        || !(0.0..=100.0).contains(&feature.rh_percent)
    {
        return Err("GFS vertical prior feature is invalid".into());
    }
    if feature.temperature_k.is_some_and(|value| !finite(value)) {
        return Err("GFS vertical prior temperature is invalid".into());
    }
    if feature
        .cloud_fraction_percent
        .is_some_and(|value| !finite(value))
    {
        return Err("GFS vertical prior cloud fraction is invalid".into());
    }
    Ok(())
}

pub(crate) fn interpolate(
    left: &[Feature],
    right: &[Feature],
    fraction: f64,
) -> Result<Vec<Feature>, String> {
    if fraction <= 0.0 {
        return Ok(left.to_vec());
    }
    if right.is_empty() {
        return Err("GFS vertical prior interpolation right domain is missing".into());
    }
    let right_map: BTreeMap<_, _> = right
        .iter()
        .map(|item| (coordinate_key(item.lat, item.lon, item.pressure_hpa), item))
        .collect();
    let left_keys: std::collections::BTreeSet<_> = left
        .iter()
        .map(|item| coordinate_key(item.lat, item.lon, item.pressure_hpa))
        .collect();
    if left_keys.len() != left.len()
        || right_map.len() != right.len()
        || left_keys != right_map.keys().copied().collect()
    {
        return Err("GFS vertical prior interpolation domains do not match".into());
    }
    left.iter()
        .map(|item| {
            let counterpart = right_map
                .get(&coordinate_key(item.lat, item.lon, item.pressure_hpa))
                .ok_or("GFS vertical prior interpolation level is missing")?;
            let interpolate_optional = |left: Option<f64>, right: Option<f64>| match (left, right) {
                (None, None) => Ok(None),
                (Some(_), None) | (None, Some(_)) => {
                    Err("GFS vertical prior field is missing on only one time side".to_string())
                }
                (Some(left), Some(right)) => Ok(Some(left + fraction * (right - left))),
            };
            Ok(Feature {
                lat: item.lat,
                lon: item.lon,
                pressure_hpa: item.pressure_hpa,
                geopotential_height_m: item.geopotential_height_m
                    + fraction * (counterpart.geopotential_height_m - item.geopotential_height_m),
                rh_percent: item.rh_percent + fraction * (counterpart.rh_percent - item.rh_percent),
                temperature_k: interpolate_optional(item.temperature_k, counterpart.temperature_k)?,
                cloud_fraction_percent: interpolate_optional(
                    item.cloud_fraction_percent,
                    counterpart.cloud_fraction_percent,
                )?,
            })
        })
        .collect()
}

fn validate_common(
    source: &str,
    fraction: f64,
    terrain_left_asl_m: f64,
    terrain_right_asl_m: f64,
    target_lat: f64,
    target_lon: f64,
) -> Result<(), String> {
    if source.is_empty()
        || !finite(fraction)
        || !(0.0..=1.0).contains(&fraction)
        || !finite(terrain_left_asl_m)
        || !finite(terrain_right_asl_m)
        || !(-500.0..=9000.0).contains(&terrain_left_asl_m)
        || !(-500.0..=9000.0).contains(&terrain_right_asl_m)
        || !finite(target_lat)
        || !(-90.0..=90.0).contains(&target_lat)
        || !finite(target_lon)
        || !(-180.0..=180.0).contains(&target_lon)
    {
        return Err("GFS vertical prior request is invalid".into());
    }
    Ok(())
}

fn haversine_km(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let (phi1, phi2) = (lat1.to_radians(), lat2.to_radians());
    let delta_phi = phi2 - phi1;
    let delta_lambda = (lon2.to_radians() - lon1.to_radians() + std::f64::consts::PI)
        .rem_euclid(2.0 * std::f64::consts::PI)
        - std::f64::consts::PI;
    let a = (delta_phi / 2.0).sin().powi(2)
        + phi1.cos() * phi2.cos() * (delta_lambda / 2.0).sin().powi(2);
    EARTH_RADIUS_KM * 2.0 * a.sqrt().atan2((1.0 - a).max(0.0).sqrt())
}

/// Downscale the coarse GFS columns to the target by inverse-distance weighting the
/// nearest grid columns, mirroring the terrain interpolation. Shared weights across
/// pressure levels preserve the strict height/pressure ordering of every column. A
/// lone column, or a grid point exactly at the target, is returned unchanged.
fn downscaled_column(
    features: &[Feature],
    target_lat: f64,
    target_lon: f64,
) -> Result<Vec<Feature>, String> {
    let mut columns: BTreeMap<(i64, i64), Vec<&Feature>> = BTreeMap::new();
    for feature in features
        .iter()
        .filter(|feature| finite(feature.lat) && finite(feature.lon))
    {
        let key = (
            (feature.lat * 100_000.0).round() as i64,
            (feature.lon * 100_000.0).round() as i64,
        );
        columns.entry(key).or_default().push(feature);
    }
    let mut located: Vec<(f64, &Vec<&Feature>)> = columns
        .values()
        .map(|column| {
            let head = column[0];
            (
                haversine_km(target_lat, target_lon, head.lat, head.lon),
                column,
            )
        })
        .filter(|(distance, _)| distance.is_finite())
        .collect();
    if located.is_empty() {
        return Err("GFS vertical prior has no horizontal samples".into());
    }
    located.sort_by(|left, right| left.0.total_cmp(&right.0));
    let nearest = &located[..located.len().min(4)];
    if nearest[0].0 <= 1e-6 {
        return Ok(nearest[0]
            .1
            .iter()
            .map(|feature| (*feature).clone())
            .collect());
    }
    let weights: Vec<f64> = nearest
        .iter()
        .map(|(distance, _)| 1.0 / distance.max(1e-3))
        .collect();
    let weight_sum: f64 = weights.iter().sum();
    if !weight_sum.is_finite() || weight_sum <= 0.0 {
        return Err("GFS vertical prior has invalid local weights".into());
    }
    let maps: Vec<BTreeMap<i64, &Feature>> = nearest
        .iter()
        .map(|(_, column)| {
            column
                .iter()
                .map(|feature| (feature.pressure_hpa, *feature))
                .collect()
        })
        .collect();
    let mut pressures: Vec<i64> = maps[0].keys().copied().collect();
    for map in &maps[1..] {
        pressures.retain(|pressure| map.contains_key(pressure));
    }
    if pressures.is_empty() {
        return Err("GFS vertical prior columns share no pressure levels".into());
    }
    let mut output = Vec::with_capacity(pressures.len());
    for pressure in pressures {
        let entries: Vec<&Feature> = maps.iter().map(|map| map[&pressure]).collect();
        let head = entries[0];
        let weighted = |get: fn(&Feature) -> f64| -> f64 {
            entries
                .iter()
                .zip(&weights)
                .map(|(feature, weight)| get(feature) * weight)
                .sum::<f64>()
                / weight_sum
        };
        let optional = |get: fn(&Feature) -> Option<f64>| -> Option<f64> {
            let values: Vec<Option<f64>> = entries.iter().map(|feature| get(feature)).collect();
            if values.iter().all(Option::is_some) {
                Some(
                    values
                        .iter()
                        .zip(&weights)
                        .map(|(value, weight)| value.unwrap() * weight)
                        .sum::<f64>()
                        / weight_sum,
                )
            } else if values.iter().all(Option::is_none) {
                None
            } else {
                get(head)
            }
        };
        output.push(Feature {
            lat: target_lat,
            lon: target_lon,
            pressure_hpa: pressure,
            geopotential_height_m: weighted(|feature| feature.geopotential_height_m),
            rh_percent: weighted(|feature| feature.rh_percent),
            temperature_k: optional(|feature| feature.temperature_k),
            cloud_fraction_percent: optional(|feature| feature.cloud_fraction_percent),
        });
    }
    Ok(output)
}

pub(crate) fn build_prior(
    context: &PriorContext<'_>,
    identity: &str,
    terrain_left_asl_m: f64,
    terrain_right_asl_m: f64,
    target_lat: f64,
    target_lon: f64,
) -> Result<Value, String> {
    validate_common(
        context.source,
        context.fraction,
        terrain_left_asl_m,
        terrain_right_asl_m,
        target_lat,
        target_lon,
    )?;
    if identity.is_empty() {
        return Err("GFS vertical prior request identity is empty".into());
    }
    let terrain =
        terrain_left_asl_m + context.fraction * (terrain_right_asl_m - terrain_left_asl_m);
    let column = downscaled_column(context.features, target_lat, target_lon)?;
    let mut levels: Vec<&Feature> = column
        .iter()
        .filter(|feature| feature.geopotential_height_m >= terrain - 100.0)
        .collect();
    levels.sort_by(|left, right| {
        left.geopotential_height_m
            .partial_cmp(&right.geopotential_height_m)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    if levels.is_empty() || levels.len() > MAX_LEVELS {
        return Err("GFS vertical prior has no levels above terrain".into());
    }
    if levels.windows(2).any(|window| {
        window[0].pressure_hpa <= window[1].pressure_hpa
            || window[0].geopotential_height_m >= window[1].geopotential_height_m
    }) {
        return Err("GFS vertical prior levels are not ordered".into());
    }
    let output_levels: Vec<Value> = levels
        .iter()
        .map(|level| {
            json!({
                "pressure_hpa": level.pressure_hpa,
                "height_asl_m": level.geopotential_height_m,
                "temperature_k": level.temperature_k.filter(|value| value.is_finite() && (150.0..=350.0).contains(value)),
                "rh_percent": level.rh_percent.is_finite().then(|| level.rh_percent.clamp(0.0, 100.0)),
                "model_cloud_fraction": level.cloud_fraction_percent.filter(|value| value.is_finite()).map(|value| (value / 100.0).clamp(0.0, 1.0)),
            })
        })
        .collect();
    Ok(json!({
        "contract": "rust-desktop-nowcast-gfs-prior-v1",
        "runtime_python_used": false,
        "valid_time_utc": crate::time_contract::iso(context.valid_time),
        "cycle_time_utc": crate::time_contract::iso(context.cycle_time),
        "forecast_hour": context.forecast_hour,
        "source": context.source,
        "identity": identity,
        "terrain_asl_m": terrain,
        "target_lat": target_lat,
        "target_lon": target_lon,
        "levels": output_levels,
    }))
}

fn validate_feature_arrays(
    left_features: &[Feature],
    right_features: &[Feature],
) -> Result<(), String> {
    if left_features.len() > MAX_FEATURES || right_features.len() > MAX_FEATURES {
        return Err("GFS vertical prior feature count exceeds bound".into());
    }
    if left_features.is_empty() {
        return Err("GFS vertical prior has no horizontal samples".into());
    }
    for feature in left_features.iter().chain(right_features.iter()) {
        validate_feature(feature)?;
    }
    Ok(())
}

pub fn execute(params: Params) -> Result<Value, String> {
    let valid_time = utc(&params.valid_time_utc, "valid_time_utc")?;
    let cycle_time = utc(&params.cycle_time_utc, "cycle_time_utc")?;
    validate_common(
        &params.source,
        params.fraction,
        params.terrain_left_asl_m,
        params.terrain_right_asl_m,
        params.target_lat,
        params.target_lon,
    )?;
    validate_feature_arrays(&params.left_features, &params.right_features)?;
    let features = interpolate(
        &params.left_features,
        &params.right_features,
        params.fraction,
    )?;
    build_prior(
        &PriorContext {
            valid_time,
            cycle_time,
            forecast_hour: params.forecast_hour,
            source: &params.source,
            fraction: params.fraction,
            features: &features,
        },
        &params.identity,
        params.terrain_left_asl_m,
        params.terrain_right_asl_m,
        params.target_lat,
        params.target_lon,
    )
}

pub fn execute_batch(params: BatchParams) -> Result<Value, String> {
    if params.requests.len() > 128 {
        return Err("GFS vertical prior batch exceeds 128 requests".into());
    }
    let results = params
        .requests
        .into_iter()
        .map(execute)
        .collect::<Result<Vec<_>, _>>()?;
    Ok(json!({
        "contract": "rust-desktop-nowcast-gfs-prior-batch-v1",
        "runtime_python_used": false,
        "results": results,
    }))
}

pub fn execute_shared_batch(params: SharedBatchParams) -> Result<Value, String> {
    if params.requests.is_empty() || params.requests.len() > 128 {
        return Err("GFS shared vertical prior batch must contain 1..128 requests".into());
    }
    let valid_time = utc(&params.valid_time_utc, "valid_time_utc")?;
    let cycle_time = utc(&params.cycle_time_utc, "cycle_time_utc")?;
    validate_common(&params.source, params.fraction, 0.0, 0.0, 0.0, 0.0)?;
    validate_feature_arrays(&params.left_features, &params.right_features)?;
    let features = interpolate(
        &params.left_features,
        &params.right_features,
        params.fraction,
    )?;
    let context = PriorContext {
        valid_time,
        cycle_time,
        forecast_hour: params.forecast_hour,
        source: &params.source,
        fraction: params.fraction,
        features: &features,
    };
    let results = params
        .requests
        .into_iter()
        .map(|request| {
            build_prior(
                &context,
                &request.identity,
                request.terrain_left_asl_m,
                request.terrain_right_asl_m,
                request.target_lat,
                request.target_lon,
            )
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(json!({
        "contract": "rust-desktop-nowcast-gfs-prior-shared-batch-v1",
        "runtime_python_used": false,
        "valid_time_utc": crate::time_contract::iso(valid_time),
        "cycle_time_utc": crate::time_contract::iso(cycle_time),
        "forecast_hour": params.forecast_hour,
        "source": params.source,
        "results": results,
    }))
}

pub fn execute_decoded_batch(params: DecodedBatchParams) -> Result<Value, String> {
    if params.requests.is_empty() || params.requests.len() > MAX_SAMPLES {
        return Err("GFS decoded vertical prior batch must contain 1..2000 samples".into());
    }
    if !params.local_radius_km.is_finite()
        || !(0.0..=20_000.0).contains(&params.local_radius_km)
        || params.local_radius_km <= 0.0
    {
        return Err("GFS decoded local radius is invalid".into());
    }
    if params.domain_hash.is_empty()
        || params.domain_hash.len() > 128
        || params.resolution.is_empty()
    {
        return Err("GFS decoded domain identity is invalid".into());
    }
    let valid_time = utc(&params.valid_time_utc, "valid_time_utc")?;
    let cycle_time = utc(&params.cycle_time_utc, "cycle_time_utc")?;
    validate_common(&params.source, params.fraction, 0.0, 0.0, 0.0, 0.0)?;
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
        return Err("GFS decoded left/right coordinate domains do not match".into());
    }
    let mut results = Vec::with_capacity(params.requests.len());
    for request in params.requests {
        if !request.target_lat.is_finite()
            || !(-90.0..=90.0).contains(&request.target_lat)
            || !request.target_lon.is_finite()
            || !(-180.0..=180.0).contains(&request.target_lon)
        {
            return Err("GFS decoded sample target is invalid".into());
        }
        let left_geometry = select_geometry(
            &left,
            request.target_lat,
            request.target_lon,
            params.local_radius_km,
        );
        let left_features = features_at(&left, &left_geometry)?;
        let (right_features, right_terrain) = match &right {
            None => (left_features.clone(), terrain_at(&left, &left_geometry)?),
            Some(grid) => {
                let geometry = select_geometry(
                    grid,
                    request.target_lat,
                    request.target_lon,
                    params.local_radius_km,
                );
                let features = features_at(grid, &geometry)?;
                let terrain = terrain_at(grid, &geometry)?;
                (features, terrain)
            }
        };
        let features = interpolate(&left_features, &right_features, params.fraction)?;
        let left_terrain = terrain_at(&left, &left_geometry)?;
        let cycle_stamp = cycle_time.format("%Y%m%d%H");
        let identity = format!(
            "gfs-prior:{cycle_stamp}:f{:03}-{:03}:{}:domain={}:point={:+.5},{:+.5}",
            params.left_forecast_hour,
            params.right_forecast_hour,
            params.resolution,
            params.domain_hash,
            request.target_lat,
            request.target_lon
        );
        results.push(build_prior(
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
            request.target_lat,
            request.target_lon,
        )?);
    }
    Ok(json!({
        "contract": "rust-desktop-nowcast-gfs-prior-decoded-batch-v1",
        "runtime_python_used": false,
        "valid_time_utc": crate::time_contract::iso(valid_time),
        "cycle_time_utc": crate::time_contract::iso(cycle_time),
        "forecast_hour": params.left_forecast_hour,
        "source": params.source,
        "results": results,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn feature(lat: f64, lon: f64, pressure_hpa: i64, height: f64) -> Feature {
        Feature {
            lat,
            lon,
            pressure_hpa,
            geopotential_height_m: height,
            rh_percent: 60.0,
            temperature_k: Some(280.0),
            cloud_fraction_percent: Some(50.0),
        }
    }

    fn column(lat: f64, lon: f64, base_height: f64) -> Vec<Feature> {
        vec![
            feature(lat, lon, 850, base_height),
            feature(lat, lon, 500, base_height + 4000.0),
            feature(lat, lon, 200, base_height + 10500.0),
        ]
    }

    fn level_at(column: &[Feature], pressure_hpa: i64) -> &Feature {
        column
            .iter()
            .find(|feature| feature.pressure_hpa == pressure_hpa)
            .unwrap()
    }

    #[test]
    fn downscale_returns_a_lone_column_unchanged() {
        let features = column(30.0, 120.0, 1500.0);
        let result = downscaled_column(&features, 30.1, 120.1).unwrap();
        assert_eq!(result.len(), features.len());
        assert_eq!(level_at(&result, 850).geopotential_height_m, 1500.0);
    }

    #[test]
    fn downscale_returns_the_exact_grid_column_unchanged() {
        let mut features = column(30.0, 120.0, 1500.0);
        features.extend(column(30.0, 120.25, 1600.0));
        let result = downscaled_column(&features, 30.0, 120.25).unwrap();
        assert_eq!(level_at(&result, 850).geopotential_height_m, 1600.0);
        assert_eq!(level_at(&result, 850).lat, 30.0);
    }

    #[test]
    fn downscale_blends_nearest_columns_toward_the_closer_one() {
        let mut features = column(30.0, 120.0, 1500.0);
        features.extend(column(30.0, 120.25, 1600.0));
        // Target sits near the first column, so the blend must favor 1500 m.
        let result = downscaled_column(&features, 30.0, 120.05).unwrap();
        let height = level_at(&result, 850).geopotential_height_m;
        assert!(height > 1500.0 && height < 1550.0, "height was {height}");
        assert_eq!(level_at(&result, 850).lat, 30.0);
        assert_eq!(level_at(&result, 850).lon, 120.05);
    }

    #[test]
    fn downscale_falls_back_to_nearest_for_mixed_optional_fields() {
        let mut near = column(30.0, 120.0, 1500.0);
        let mut far = column(30.0, 120.25, 1600.0);
        for feature in &mut far {
            feature.temperature_k = None;
        }
        near.extend(far);
        let result = downscaled_column(&near, 30.0, 120.05).unwrap();
        // Mixed presence resolves to the nearer column's value, not a blend.
        assert_eq!(level_at(&result, 850).temperature_k, Some(280.0));
    }

    #[test]
    fn build_prior_downscales_and_keeps_levels_ordered() {
        let mut features = column(30.0, 120.0, 1500.0);
        features.extend(column(30.0, 120.25, 1600.0));
        let valid_time = crate::time_contract::parse_utc("2026-11-10T08:00:00Z").unwrap();
        let prior = build_prior(
            &PriorContext {
                valid_time,
                cycle_time: valid_time,
                forecast_hour: 0,
                source: "gfs-interpolated-vertical-prior",
                fraction: 0.0,
                features: &features,
            },
            "gfs-prior:test",
            0.0,
            0.0,
            30.0,
            120.05,
        )
        .unwrap();
        assert_eq!(prior["contract"], "rust-desktop-nowcast-gfs-prior-v1");
        let levels = prior["levels"].as_array().unwrap();
        assert_eq!(levels.len(), 3);
        for window in levels.windows(2) {
            assert!(window[0]["pressure_hpa"].as_i64() > window[1]["pressure_hpa"].as_i64());
            assert!(window[0]["height_asl_m"].as_f64() < window[1]["height_asl_m"].as_f64());
        }
        let base = levels[0]["height_asl_m"].as_f64().unwrap();
        assert!(base > 1500.0 && base < 1550.0, "base was {base}");
    }

    #[test]
    fn build_prior_clamps_supersaturated_and_negative_rh() {
        let mut features = column(30.0, 120.0, 1500.0);
        features[0].rh_percent = 103.5;
        features[1].rh_percent = -0.2;
        let valid_time = crate::time_contract::parse_utc("2026-11-10T08:00:00Z").unwrap();
        let prior = build_prior(
            &PriorContext {
                valid_time,
                cycle_time: valid_time,
                forecast_hour: 0,
                source: "gfs-interpolated-vertical-prior",
                fraction: 0.0,
                features: &features,
            },
            "gfs-prior:rh-clamp",
            0.0,
            0.0,
            30.0,
            120.0,
        )
        .unwrap();
        let levels = prior["levels"].as_array().unwrap();
        assert_eq!(levels[0]["rh_percent"].as_f64(), Some(100.0));
        assert_eq!(levels[1]["rh_percent"].as_f64(), Some(0.0));
        assert_eq!(levels[2]["rh_percent"].as_f64(), Some(60.0));
    }
}
