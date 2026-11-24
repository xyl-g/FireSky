//! Strict HSD compact-cache discovery for desktop nowcast checks.
//!
//! The remote HSD provider remains outside the engine. This module reads the
//! existing NPZ compact contract, validates its metadata and calibrated
//! arrays, and either returns recent-frame identities or exports source-bound
//! tensor bundles for the native frame consumer.

use crate::tensor_bundle::{source_identity, BundleWriter};
use crate::time_contract;
use chrono::{DateTime, Duration, Utc};
use npyz::npz::NpzArchive;
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, VecDeque};
use std::fs::{self, File, OpenOptions};
use std::io::BufReader;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::SystemTime;

const CACHE_SCHEMA_VERSION: u64 = 2;
const FRAME_SCHEMA_VERSION: u64 = 1;
const CACHE_ALGORITHM: &str = "ahi-hsd-calibrated-frame-v1";
const PROJECTION_IDENTITY: &str = "ahi-geos9-himawari9-full-disk-5500x5500-v1";
const MAX_FRAME_BYTES: u64 = 512 * 1024 * 1024;
const MAX_CANDIDATES: usize = 4096;
const MAX_DECODED_BYTES: usize = 128 * 1024 * 1024;
const DEFAULT_COUNT: usize = 2;
const DEFAULT_MAX_AGE_MINUTES: f64 = 30.0;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    pub(crate) cache_dir: PathBuf,
    pub(crate) reference_time_utc: String,
    pub(crate) lat: f64,
    pub(crate) lon: f64,
    pub(crate) radius_km: f64,
    #[serde(default = "default_count")]
    pub(crate) count: usize,
    #[serde(default = "default_max_age_minutes")]
    pub(crate) max_age_minutes: f64,
    #[serde(default)]
    pub(crate) output_dir: Option<PathBuf>,
}

fn default_count() -> usize {
    DEFAULT_COUNT
}

fn default_max_age_minutes() -> f64 {
    DEFAULT_MAX_AGE_MINUTES
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Metadata {
    cache_schema_version: u64,
    cache_algorithm: String,
    frame_schema_version: u64,
    valid_time_utc: String,
    ready_time_utc: String,
    source: String,
    quality: String,
    range_bytes: u64,
    range_blocks: u64,
    target_lat: f64,
    target_lon: f64,
    radius_km: f64,
    projection_identity: String,
    projection_hash: String,
    bands: Vec<String>,
    units: BTreeMap<String, String>,
}

#[derive(Clone, PartialEq, Eq)]
struct FileVersion {
    path: PathBuf,
    bytes: u64,
    modified: SystemTime,
}

#[derive(Clone)]
struct CachedFrame {
    version: FileVersion,
    valid_time: DateTime<Utc>,
    source: String,
    quality: String,
    range_bytes: u64,
    range_blocks: u64,
    target_lat: f64,
    target_lon: f64,
    radius_km: f64,
}

#[derive(Clone)]
struct FramePayload {
    frame: CachedFrame,
    shape: Vec<usize>,
    latitudes: Vec<f32>,
    longitudes: Vec<f32>,
    bands: BTreeMap<String, Vec<f32>>,
    source: crate::tensor_bundle::SourceIdentity,
}

struct RequestParts {
    reference: DateTime<Utc>,
    max_age: Duration,
    root: PathBuf,
    point: (f64, f64),
}

static VALIDATED_FRAMES: OnceLock<Mutex<VecDeque<CachedFrame>>> = OnceLock::new();
static DECODED_FRAMES: OnceLock<Mutex<VecDeque<FramePayload>>> = OnceLock::new();

fn file_version(path: &Path) -> Result<FileVersion, String> {
    let metadata = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if !metadata.file_type().is_file() || metadata.len() > MAX_FRAME_BYTES {
        return Err("HSD compact cache must be a regular file within the size bound".into());
    }
    Ok(FileVersion {
        path: path.to_path_buf(),
        bytes: metadata.len(),
        modified: metadata.modified().map_err(|e| e.to_string())?,
    })
}

fn regular_file(path: &Path) -> Result<(File, FileVersion), String> {
    let version = file_version(path)?;
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    let file = options.open(path).map_err(|e| e.to_string())?;
    Ok((file, version))
}

fn same_file_version(path: &Path, version: &FileVersion) -> bool {
    file_version(path).is_ok_and(|current| current == *version)
}

fn frame_value(frame: &CachedFrame) -> Value {
    json!({
        "valid_time_utc": time_contract::iso(frame.valid_time),
        "source": frame.source,
        "quality": frame.quality,
        "range_bytes": frame.range_bytes,
        "range_blocks": frame.range_blocks,
        "path": frame.version.path,
    })
}

fn cached_frame(
    version: &FileVersion,
    point: (f64, f64),
    radius_km: f64,
    reference: DateTime<Utc>,
    max_age: Duration,
) -> Option<Option<Value>> {
    let mut cache = VALIDATED_FRAMES.get_or_init(Mutex::default).lock().unwrap();
    let position = cache.iter().position(|frame| frame.version == *version)?;
    let frame = cache.remove(position)?;
    let matches_request = (frame.target_lat - point.0).abs() <= 1e-4
        && (frame.target_lon - point.1).abs() <= 1e-4
        && (frame.radius_km - radius_km).abs() <= 1e-3;
    if !matches_request {
        cache.push_back(frame);
        return None;
    }
    let in_window = frame.valid_time + max_age >= reference
        && frame.valid_time + Duration::minutes(5) <= reference;
    let result = in_window.then(|| frame_value(&frame));
    cache.push_back(frame);
    Some(result)
}

fn remember_frame(frame: CachedFrame) {
    let mut cache = VALIDATED_FRAMES.get_or_init(Mutex::default).lock().unwrap();
    cache.retain(|old| old.version.path != frame.version.path);
    cache.push_back(frame);
    while cache.len() > 256 {
        cache.pop_front();
    }
}

fn decoded_bytes(payload: &FramePayload) -> usize {
    payload
        .latitudes
        .len()
        .saturating_add(payload.longitudes.len())
        .saturating_add(payload.bands.values().map(Vec::len).sum::<usize>())
        .saturating_mul(std::mem::size_of::<f32>())
}

fn cached_payload(
    version: &FileVersion,
    point: (f64, f64),
    radius_km: f64,
    reference: DateTime<Utc>,
    max_age: Duration,
) -> Option<Option<FramePayload>> {
    let mut cache = DECODED_FRAMES.get_or_init(Mutex::default).lock().unwrap();
    let position = cache.iter().position(|payload| {
        payload.frame.version == *version
            && (payload.frame.target_lat - point.0).abs() <= 1e-4
            && (payload.frame.target_lon - point.1).abs() <= 1e-4
            && (payload.frame.radius_km - radius_km).abs() <= 1e-3
    })?;
    let payload = cache.remove(position)?;
    let in_window = payload.frame.valid_time + max_age >= reference
        && payload.frame.valid_time + Duration::minutes(5) <= reference;
    cache.push_back(payload.clone());
    Some(in_window.then_some(payload))
}

fn remember_payload(payload: FramePayload) {
    let bytes = decoded_bytes(&payload);
    if bytes > MAX_DECODED_BYTES {
        return;
    }
    let mut cache = DECODED_FRAMES.get_or_init(Mutex::default).lock().unwrap();
    cache.retain(|old| old.frame.version.path != payload.frame.version.path);
    cache.push_back(payload);
    while cache.iter().map(decoded_bytes).sum::<usize>() > MAX_DECODED_BYTES {
        cache.pop_front();
    }
}

fn read_f32_array(
    archive: &mut NpzArchive<BufReader<File>>,
    name: &str,
) -> Result<(Vec<usize>, Vec<f32>), String> {
    let npy = archive
        .by_name(name)
        .map_err(|e| e.to_string())?
        .ok_or_else(|| format!("HSD compact cache is missing {name}"))?;
    if npy.order() != npyz::Order::C || npy.dtype().descr() != "'<f4'" {
        return Err(format!(
            "HSD compact cache array {name} has an invalid dtype/order"
        ));
    }
    let shape = npy
        .shape()
        .iter()
        .map(|value| usize::try_from(*value))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| format!("HSD compact cache array {name} shape is too large"))?;
    if shape.is_empty() || shape.len() > 2 {
        return Err(format!(
            "HSD compact cache array {name} has an invalid shape"
        ));
    }
    let count = shape
        .iter()
        .try_fold(1usize, |total, value| total.checked_mul(*value))
        .ok_or("HSD compact cache array size overflow")?;
    if count == 0 || count > 16_000_000 {
        return Err(format!("HSD compact cache array {name} exceeds bounds"));
    }
    let values = npy.into_vec::<f32>().map_err(|e| e.to_string())?;
    if values.len() != count {
        return Err(format!("HSD compact cache array {name} length mismatch"));
    }
    Ok((shape, values))
}

fn read_metadata(archive: &mut NpzArchive<BufReader<File>>) -> Result<Metadata, String> {
    let npy = archive
        .by_name("metadata")
        .map_err(|e| e.to_string())?
        .ok_or("HSD compact cache is missing metadata")?;
    if npy.order() != npyz::Order::C || !npy.shape().is_empty() {
        return Err("HSD compact metadata must be a scalar C-order string".into());
    }
    let values = npy.into_vec::<String>().map_err(|e| e.to_string())?;
    let raw = values
        .into_iter()
        .next()
        .ok_or("HSD compact metadata is empty")?;
    serde_json::from_str(&raw).map_err(|e| e.to_string())
}

fn validate_metadata(
    metadata: &Metadata,
    point: (f64, f64),
    radius_km: f64,
    reference: DateTime<Utc>,
    max_age: Duration,
) -> Result<Option<DateTime<Utc>>, String> {
    let projection_hash = Sha256::digest(PROJECTION_IDENTITY.as_bytes())
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    if metadata.cache_schema_version != CACHE_SCHEMA_VERSION
        || metadata.cache_algorithm != CACHE_ALGORITHM
        || metadata.frame_schema_version != FRAME_SCHEMA_VERSION
    {
        return Err("compact HSD cache schema is obsolete".into());
    }
    if metadata.quality != "medium" && metadata.quality != "low" {
        return Err("compact HSD quality is invalid".into());
    }
    if metadata.source.is_empty()
        || metadata.range_bytes == 0
        || metadata.range_blocks == 0
        || !metadata.target_lat.is_finite()
        || !metadata.target_lon.is_finite()
        || !metadata.radius_km.is_finite()
        || metadata.radius_km <= 0.0
        || metadata.projection_identity != PROJECTION_IDENTITY
        || metadata.projection_hash != projection_hash
        || metadata.bands != vec!["B07".to_string(), "B13".to_string(), "B15".to_string()]
        || metadata.units
            != BTreeMap::from([
                ("B07".to_string(), "K".to_string()),
                ("B13".to_string(), "K".to_string()),
                ("B15".to_string(), "K".to_string()),
            ])
    {
        return Err("compact HSD cache metadata is invalid".into());
    }
    if (metadata.target_lat - point.0).abs() > 1e-4
        || (metadata.target_lon - point.1).abs() > 1e-4
        || (metadata.radius_km - radius_km).abs() > 1e-3
    {
        return Err("compact HSD footprint identity is invalid".into());
    }
    let valid = time_contract::parse_utc(&metadata.valid_time_utc)?;
    let ready = time_contract::parse_utc(&metadata.ready_time_utc)?;
    if ready != valid + Duration::minutes(5) {
        return Err("compact HSD cache ready time must be five minutes after valid time".into());
    }
    if ready > reference || reference - valid > max_age {
        return Ok(None);
    }
    Ok(Some(valid))
}

fn validate_arrays(archive: &mut NpzArchive<BufReader<File>>) -> Result<(), String> {
    let (coordinate_shape, latitudes) = read_f32_array(archive, "latitudes")?;
    let (longitude_shape, longitudes) = read_f32_array(archive, "longitudes")?;
    if coordinate_shape.len() != 2
        || coordinate_shape != longitude_shape
        || latitudes.iter().any(|value| !value.is_finite())
        || longitudes.iter().any(|value| !value.is_finite())
    {
        return Err("HSD compact coordinate arrays are invalid".into());
    }
    for name in ["band__B07", "band__B13", "band__B15"] {
        let (shape, values) = read_f32_array(archive, name)?;
        if shape != coordinate_shape || values.iter().any(|value| value.is_infinite()) {
            return Err(format!("HSD compact band {name} is invalid"));
        }
        let finite = values.iter().filter(|value| value.is_finite()).count();
        if finite * 100 < values.len() * 95
            || values
                .iter()
                .any(|value| value.is_finite() && !(*value >= 100.0 && *value <= 400.0))
        {
            return Err(format!("HSD compact band {name} is invalid"));
        }
    }
    Ok(())
}

fn read_frame(
    path: &Path,
    point: (f64, f64),
    radius_km: f64,
    reference: DateTime<Utc>,
    max_age: Duration,
) -> Result<Option<Value>, String> {
    let (file, version) = regular_file(path)?;
    if !same_file_version(path, &version) {
        return Err("HSD compact cache changed while it was being opened".into());
    }
    if let Some(cached) = cached_frame(&version, point, radius_km, reference, max_age) {
        return Ok(cached);
    }
    let mut archive = NpzArchive::new(BufReader::new(file)).map_err(|e| e.to_string())?;
    let metadata = read_metadata(&mut archive)?;
    let Some(valid) = validate_metadata(&metadata, point, radius_km, reference, max_age)? else {
        return Ok(None);
    };
    validate_arrays(&mut archive)?;
    if !same_file_version(path, &version) {
        return Err("HSD compact cache changed while it was being validated".into());
    }
    let frame = CachedFrame {
        version,
        valid_time: valid,
        source: metadata.source,
        quality: metadata.quality,
        range_bytes: metadata.range_bytes,
        range_blocks: metadata.range_blocks,
        target_lat: metadata.target_lat,
        target_lon: metadata.target_lon,
        radius_km: metadata.radius_km,
    };
    let value = frame_value(&frame);
    remember_frame(frame);
    Ok(Some(value))
}

fn read_frame_payload(
    path: &Path,
    point: (f64, f64),
    radius_km: f64,
    reference: DateTime<Utc>,
    max_age: Duration,
) -> Result<Option<FramePayload>, String> {
    let (file, version) = regular_file(path)?;
    if !same_file_version(path, &version) {
        return Err("HSD compact cache changed while it was being opened".into());
    }
    if let Some(cached) = cached_payload(&version, point, radius_km, reference, max_age) {
        return Ok(cached);
    }
    let mut archive = NpzArchive::new(BufReader::new(file)).map_err(|e| e.to_string())?;
    let metadata = read_metadata(&mut archive)?;
    let Some(valid) = validate_metadata(&metadata, point, radius_km, reference, max_age)? else {
        return Ok(None);
    };
    let (coordinate_shape, latitudes) = read_f32_array(&mut archive, "latitudes")?;
    let (longitude_shape, longitudes) = read_f32_array(&mut archive, "longitudes")?;
    if coordinate_shape.len() != 2
        || coordinate_shape != longitude_shape
        || latitudes.iter().any(|value| !value.is_finite())
        || longitudes.iter().any(|value| !value.is_finite())
    {
        return Err("HSD compact coordinate arrays are invalid".into());
    }
    let mut bands = BTreeMap::new();
    for name in ["B07", "B13", "B15"] {
        let array_name = format!("band__{name}");
        let (shape, values) = read_f32_array(&mut archive, &array_name)?;
        if shape != coordinate_shape || values.iter().any(|value| value.is_infinite()) {
            return Err(format!("HSD compact band {name} is invalid"));
        }
        let finite = values.iter().filter(|value| value.is_finite()).count();
        if finite * 100 < values.len() * 95
            || values
                .iter()
                .any(|value| value.is_finite() && !(*value >= 100.0 && *value <= 400.0))
        {
            return Err(format!("HSD compact band {name} is invalid"));
        }
        bands.insert(name.to_string(), values);
    }
    if !same_file_version(path, &version) {
        return Err("HSD compact cache changed while it was being validated".into());
    }
    let source = source_identity(path)?;
    let frame = CachedFrame {
        version,
        valid_time: valid,
        source: metadata.source,
        quality: metadata.quality,
        range_bytes: metadata.range_bytes,
        range_blocks: metadata.range_blocks,
        target_lat: metadata.target_lat,
        target_lon: metadata.target_lon,
        radius_km: metadata.radius_km,
    };
    remember_frame(frame.clone());
    let payload = FramePayload {
        frame,
        shape: coordinate_shape,
        latitudes,
        longitudes,
        bands,
        source,
    };
    remember_payload(payload.clone());
    Ok(Some(payload))
}

fn candidate_paths(root: &Path, point: (f64, f64), radius_km: f64) -> Result<Vec<PathBuf>, String> {
    if !root.is_dir() {
        return Ok(Vec::new());
    }
    let suffix = format!("-{:+.5}-{:+.5}-{:.3}.npz", point.0, point.1, radius_km);
    let mut paths = Vec::new();
    for entry in fs::read_dir(root).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path();
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if name.starts_with("frame-") && name.ends_with(&suffix) {
            paths.push(path);
            if paths.len() > MAX_CANDIDATES {
                return Err("HSD compact cache candidate count exceeds bound".into());
            }
        }
    }
    paths.sort_by(|left, right| right.file_name().cmp(&left.file_name()));
    Ok(paths)
}

fn request_parts(params: &Params) -> Result<RequestParts, String> {
    if !params.lat.is_finite()
        || !(-90.0..=90.0).contains(&params.lat)
        || !params.lon.is_finite()
        || !(-180.0..=180.0).contains(&params.lon)
        || !params.radius_km.is_finite()
        || !(100.0..=1000.0).contains(&params.radius_km)
        || !(1..=8).contains(&params.count)
        || !params.max_age_minutes.is_finite()
        || !(0.1..=1440.0).contains(&params.max_age_minutes)
    {
        return Err("invalid HSD compact-cache request".into());
    }
    let reference = time_contract::parse_utc(&params.reference_time_utc)?;
    let max_age = Duration::milliseconds((params.max_age_minutes * 60_000.0).round() as i64);
    Ok(RequestParts {
        reference,
        max_age,
        root: params.cache_dir.join("compact"),
        point: (params.lat, params.lon),
    })
}

fn frame_metadata(frame: &CachedFrame) -> Value {
    json!({
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "cache_algorithm": CACHE_ALGORITHM,
        "frame_schema_version": FRAME_SCHEMA_VERSION,
        "valid_time_utc": time_contract::iso(frame.valid_time),
        "ready_time_utc": time_contract::iso(frame.valid_time + Duration::minutes(5)),
        "source": frame.source,
        "quality": frame.quality,
        "range_bytes": frame.range_bytes,
        "range_blocks": frame.range_blocks,
        "target_lat": frame.target_lat,
        "target_lon": frame.target_lon,
        "radius_km": frame.radius_km,
        "projection_identity": PROJECTION_IDENTITY,
        "projection_hash": Sha256::digest(PROJECTION_IDENTITY.as_bytes())
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>(),
        "bands": ["B07", "B13", "B15"],
        "units": {"B07": "K", "B13": "K", "B15": "K"},
    })
}

fn export_frame(mut payload: FramePayload, output_dir: &Path) -> Result<Value, String> {
    if !output_dir.is_absolute()
        || !fs::symlink_metadata(output_dir)
            .map_err(|e| e.to_string())?
            .file_type()
            .is_dir()
    {
        return Err("HSD tensor output directory must be an existing regular directory".into());
    }
    let source_path = &payload.frame.version.path;
    let root = output_dir.join(format!("frame-{}", payload.source.sha256));
    if fs::symlink_metadata(&root)
        .ok()
        .is_some_and(|metadata| metadata.file_type().is_dir())
        && fs::symlink_metadata(root.join("manifest.json"))
            .ok()
            .is_some_and(|metadata| metadata.file_type().is_file())
    {
        return Ok(json!({
            "manifest_path": root.join("manifest.json"),
            "path": source_path,
            "valid_time_utc": time_contract::iso(payload.frame.valid_time),
            "source": payload.frame.source,
            "quality": payload.frame.quality,
            "range_bytes": payload.frame.range_bytes,
            "range_blocks": payload.frame.range_blocks,
        }));
    }
    let mut writer = BundleWriter::create(
        &root,
        "hsd-compact-frame-v1",
        payload.source.clone(),
        frame_metadata(&payload.frame),
    )?;
    let shape = payload
        .shape
        .iter()
        .map(|value| u64::try_from(*value).map_err(|_| "HSD tensor shape is too large".to_string()))
        .collect::<Result<Vec<_>, _>>()?;
    writer.f32("latitudes", &shape, payload.latitudes)?;
    writer.f32("longitudes", &shape, payload.longitudes)?;
    for name in ["B07", "B13", "B15"] {
        writer.f32(
            &format!("band_{}", name.to_lowercase()),
            &shape,
            payload
                .bands
                .remove(name)
                .ok_or_else(|| format!("HSD compact band {name} is missing"))?,
        )?;
    }
    let manifest = writer.commit(source_path)?;
    if !same_file_version(source_path, &payload.frame.version) {
        return Err("HSD compact cache changed after tensor export".into());
    }
    Ok(json!({
        "manifest_path": manifest,
        "path": source_path,
        "valid_time_utc": time_contract::iso(payload.frame.valid_time),
        "source": payload.frame.source,
        "quality": payload.frame.quality,
        "range_bytes": payload.frame.range_bytes,
        "range_blocks": payload.frame.range_blocks,
    }))
}

pub fn execute(params: Params) -> Result<Value, String> {
    let parts = request_parts(&params)?;
    let reference = parts.reference;
    let max_age = parts.max_age;
    let root = parts.root;
    let point = parts.point;
    if !root.is_dir() {
        return Ok(json!({
            "contract": "rust-desktop-nowcast-hsd-cache-v1",
            "runtime_python_used": false,
            "frames": [],
        }));
    }
    let paths = candidate_paths(&root, point, params.radius_km)?;
    let mut frames = Vec::new();
    for path in paths {
        if let Some(frame) = read_frame(&path, point, params.radius_km, reference, max_age)? {
            frames.push(frame);
        }
        if frames.len() >= params.count {
            break;
        }
    }
    Ok(json!({
        "contract": "rust-desktop-nowcast-hsd-cache-v1",
        "runtime_python_used": false,
        "frames": frames,
    }))
}

pub fn export(params: Params) -> Result<Value, String> {
    let parts = request_parts(&params)?;
    let reference = parts.reference;
    let max_age = parts.max_age;
    let root = parts.root;
    let point = parts.point;
    let output_dir = params
        .output_dir
        .as_deref()
        .ok_or("HSD tensor export requires output_dir")?;
    let paths = candidate_paths(&root, point, params.radius_km)?;
    let mut frames = Vec::new();
    for path in paths {
        let Some(payload) = read_frame_payload(&path, point, params.radius_km, reference, max_age)?
        else {
            continue;
        };
        frames.push(export_frame(payload, output_dir)?);
        if frames.len() >= params.count {
            break;
        }
    }
    Ok(json!({
        "contract": "rust-desktop-nowcast-hsd-export-v1",
        "runtime_python_used": false,
        "frames": frames,
    }))
}
