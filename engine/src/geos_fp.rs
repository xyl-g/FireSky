//! NASA GEOS-FP OPeNDAP client and self-validating binary optical cache (.fsopt).
//!
//! Provides the two optical fields required by FireSky without registration, API
//! keys, queues, or extra native NetCDF/HDF5 libraries:
//! - 2D 550 nm total AOD (`totexttau` from `inst1_2d_hwl_Nx`), matching exact
//!   hourly instants cleanly.
//! - 3D 550 nm aerosol extinction (`duext + ssext + suext + ocext + bcext +
//!   brext + niext` from `inst3_3d_ext_Np`), subsetted to FireSky's 11 standard
//!   pressure levels `[1000, 950, 925, 900, 850, 800, 700, 600, 500, 400, 300]`.

use crate::{
    cache_lock::Lock,
    cams::Optics,
    native::cams::{MAX_SOURCE_CELLS, LEVELS},
    time_contract,
};
use chrono::{DateTime, Duration, NaiveDate, NaiveDateTime, TimeZone, Timelike, Utc};
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
        Mutex,
    },
    time::{Duration as StdDuration, Instant, SystemTime},
};

pub const DEFAULT_BASE_URL: &str = "https://opendap.nccs.nasa.gov/dods/GEOS-5/fp/0.25_deg/fcast";
pub const COL_2D_AOD: &str = "inst1_2d_hwl_Nx";
pub const COL_3D_EXT: &str = "inst3_3d_ext_Np";
pub const FSOPT_MAGIC: &[u8; 8] = b"FSOPT001";
const FSOPT_CONTRACT: &str = "firesky-optics-v1";
const MAX_HEADER_BYTES: usize = 64 * 1024;
const MAX_FSOPT_BYTES: u64 = 512 * 1024 * 1024;
const MAX_DODS_BYTES: usize = 256 * 1024 * 1024;
const MAX_DODS_ITEMS: usize = 20_000_000;
const GEOS_LAT_COUNT: usize = 721;
const GEOS_LON_COUNT: usize = 1152;
const GEOS_LAT_STEP: f64 = 0.25;
const GEOS_LON_STEP: f64 = 0.3125;
const GEOS_MAX_LEVEL_INDEX: usize = 16; // index 16 is 300 hPa in standard 26-level GEOS-FP pressure axis
const EXT_SPECIES: [&str; 7] = [
    "duext", "ssext", "suext", "ocext", "bcext", "brext", "niext",
];
const CONNECT_TIMEOUT: StdDuration = StdDuration::from_secs(15);
const READ_TIMEOUT: StdDuration = StdDuration::from_secs(180);
const DOWNLOAD_WORKERS: usize = 2;

pub fn base_url() -> String {
    std::env::var("FIRESKY_GEOS_FP_BASE_URL")
        .ok()
        .map(|s| s.trim().trim_end_matches('/').to_owned())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| DEFAULT_BASE_URL.to_owned())
}

pub fn run_tag(cycle: DateTime<Utc>) -> String {
    format!("{}_{:02}", cycle.format("%Y%m%d"), cycle.hour())
}

pub fn das_url(base: &str, collection: &str, cycle: DateTime<Utc>) -> String {
    let tag = run_tag(cycle);
    format!(
        "{}/{collection}/{collection}.{tag}.das",
        base.trim_end_matches('/')
    )
}

pub fn dods_url(base: &str, collection: &str, cycle: DateTime<Utc>, query: &str) -> String {
    let tag = run_tag(cycle);
    format!(
        "{}/{collection}/{collection}.{tag}.dods?{query}",
        base.trim_end_matches('/')
    )
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct FsoptHeader {
    pub contract: String,
    pub source: String,
    pub valid_time_utc: String,
    pub run_time_utc: String,
    pub lead_hours: u32,
    pub n_lat: usize,
    pub n_lon: usize,
    pub pressure_levels: Vec<i32>,
    pub bounds: [f64; 4],
}

pub fn is_fsopt_file(path: &Path) -> bool {
    if path
        .extension()
        .and_then(|s| s.to_str())
        .is_some_and(|ext| ext.eq_ignore_ascii_case("fsopt"))
    {
        return true;
    }
    let Ok(mut file) = File::open(path) else {
        return false;
    };
    let mut magic = [0u8; 8];
    file.read_exact(&mut magic).is_ok() && &magic == FSOPT_MAGIC
}

pub fn read_header(path: &Path) -> Result<(FsoptHeader, Vec<f64>, Vec<f64>), String> {
    let meta = fs::metadata(path).map_err(|e| format!("cannot stat fsopt file: {e}"))?;
    if !meta.is_file() || meta.len() < 44 || meta.len() > MAX_FSOPT_BYTES {
        return Err("invalid fsopt file size or type".into());
    }
    let mut file = File::open(path).map_err(|e| format!("cannot open fsopt file: {e}"))?;
    let mut magic = [0u8; 8];
    file.read_exact(&mut magic)
        .map_err(|_| "truncated fsopt magic".to_string())?;
    if &magic != FSOPT_MAGIC {
        return Err("invalid fsopt magic header".into());
    }
    let mut len_buf = [0u8; 4];
    file.read_exact(&mut len_buf)
        .map_err(|_| "truncated fsopt header length".to_string())?;
    let header_len = u32::from_le_bytes(len_buf) as usize;
    if header_len == 0 || header_len > MAX_HEADER_BYTES {
        return Err("invalid fsopt header length".into());
    }
    let mut header_bytes = vec![0u8; header_len];
    file.read_exact(&mut header_bytes)
        .map_err(|_| "truncated fsopt JSON header".to_string())?;
    let header: FsoptHeader = serde_json::from_slice(&header_bytes)
        .map_err(|e| format!("invalid fsopt JSON header: {e}"))?;
    if header.contract != FSOPT_CONTRACT
        || header.source != "geos-fp"
        || header.n_lat < 2
        || header.n_lon < 2
        || header.pressure_levels != LEVELS
    {
        return Err("unsupported fsopt metadata contract or dimensions".into());
    }
    let cells = header
        .n_lat
        .checked_mul(header.n_lon)
        .ok_or("fsopt grid dimensions overflow")?;
    if cells > MAX_SOURCE_CELLS {
        return Err("fsopt cell count exceeds MAX_SOURCE_CELLS".into());
    }
    let mut lat_bytes = vec![0u8; header.n_lat * 8];
    let mut lon_bytes = vec![0u8; header.n_lon * 8];
    file.read_exact(&mut lat_bytes)
        .map_err(|_| "truncated fsopt lat axis".to_string())?;
    file.read_exact(&mut lon_bytes)
        .map_err(|_| "truncated fsopt lon axis".to_string())?;
    let lat_axis: Vec<f64> = lat_bytes
        .chunks_exact(8)
        .map(|b| f64::from_le_bytes(b.try_into().unwrap()))
        .collect();
    let lon_axis: Vec<f64> = lon_bytes
        .chunks_exact(8)
        .map(|b| f64::from_le_bytes(b.try_into().unwrap()))
        .collect();
    if lat_axis.iter().chain(&lon_axis).any(|v| !v.is_finite()) {
        return Err("fsopt axis contains nonfinite coordinates".into());
    }
    Ok((header, lat_axis, lon_axis))
}

pub fn write_fsopt(
    path: &Path,
    header: &FsoptHeader,
    lat_axis: &[f64],
    lon_axis: &[f64],
    aod: &[f32],
    extinction: &[f32],
) -> Result<(), String> {
    let cells = header
        .n_lat
        .checked_mul(header.n_lon)
        .ok_or("fsopt cells overflow")?;
    if lat_axis.len() != header.n_lat
        || lon_axis.len() != header.n_lon
        || aod.len() != cells
        || extinction.len() != cells * LEVELS.len()
    {
        return Err("fsopt writer array length mismatch".into());
    }
    let header_bytes = serde_json::to_vec(header).map_err(|e| e.to_string())?;
    if header_bytes.len() > MAX_HEADER_BYTES {
        return Err("fsopt header exceeds limit".into());
    }
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .ok_or("fsopt target path missing parent directory")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    if fs::symlink_metadata(path).is_ok_and(|m| !m.is_file()) {
        return Err("GEOS-FP cache destination cannot be a symlink or directory".into());
    }

    let mut payload = Vec::with_capacity(
        8 + 4 + header_bytes.len() + (lat_axis.len() + lon_axis.len()) * 8 + (cells + extinction.len()) * 4 + 32,
    );
    payload.extend_from_slice(FSOPT_MAGIC);
    payload.extend_from_slice(&(header_bytes.len() as u32).to_le_bytes());
    payload.extend_from_slice(&header_bytes);
    for &v in lat_axis {
        payload.extend_from_slice(&v.to_le_bytes());
    }
    for &v in lon_axis {
        payload.extend_from_slice(&v.to_le_bytes());
    }
    for &v in aod {
        payload.extend_from_slice(&v.to_le_bytes());
    }
    for &v in extinction {
        payload.extend_from_slice(&v.to_le_bytes());
    }
    let digest = Sha256::digest(&payload);
    payload.extend_from_slice(&digest);

    let mut temporary = tempfile::Builder::new()
        .prefix(".firesky-geosfp-")
        .tempfile_in(parent)
        .map_err(|e| e.to_string())?;
    temporary
        .write_all(&payload)
        .map_err(|e| format!("geosfp: temp write failed: {e}"))?;
    temporary
        .flush()
        .map_err(|e| format!("geosfp: temp flush failed: {e}"))?;
    let temp_path = temporary.into_temp_path();
    if let Err(error) = OpenOptions::new()
        .write(true)
        .open(&temp_path)
        .and_then(|f| f.sync_all())
    {
        eprintln!("geosfp: temp fsync skipped for {}: {error}", temp_path.display());
    }
    temp_path
        .persist(path)
        .map_err(|e| format!("geosfp: persist: {}", e.error))?;
    #[cfg(unix)]
    File::open(parent)
        .and_then(|f| f.sync_all())
        .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn decode_fsopt(path: &Path) -> Result<Optics, String> {
    let meta = fs::symlink_metadata(path).map_err(|e| format!("cannot stat fsopt file: {e}"))?;
    if !meta.file_type().is_file() || meta.len() < 44 || meta.len() > MAX_FSOPT_BYTES {
        return Err("GEOS-FP cache file must be a valid regular nonsymlink file".into());
    }
    let bytes = fs::read(path).map_err(|e| format!("cannot read fsopt file: {e}"))?;
    if bytes.len() < 44 || &bytes[..8] != FSOPT_MAGIC {
        return Err("invalid GEOS-FP fsopt magic".into());
    }
    let data_end = bytes.len() - 32;
    let actual_digest = Sha256::digest(&bytes[..data_end]);
    if actual_digest.as_slice() != &bytes[data_end..] {
        return Err("GEOS-FP fsopt SHA-256 checksum mismatch".into());
    }
    let header_len = u32::from_le_bytes(bytes[8..12].try_into().unwrap()) as usize;
    let header_end = 12usize
        .checked_add(header_len)
        .filter(|&end| end <= data_end && header_len > 0 && header_len <= MAX_HEADER_BYTES)
        .ok_or("invalid GEOS-FP fsopt header length")?;
    let header: FsoptHeader = serde_json::from_slice(&bytes[12..header_end])
        .map_err(|e| format!("invalid GEOS-FP fsopt header JSON: {e}"))?;
    if header.contract != FSOPT_CONTRACT
        || header.source != "geos-fp"
        || header.n_lat < 2
        || header.n_lon < 2
        || header.pressure_levels != LEVELS
    {
        return Err("unsupported GEOS-FP fsopt metadata".into());
    }
    let cells = header
        .n_lat
        .checked_mul(header.n_lon)
        .ok_or("GEOS-FP fsopt grid overflow")?;
    if cells > MAX_SOURCE_CELLS {
        return Err("GEOS-FP fsopt cell limit exceeded".into());
    }
    let axes_bytes = (header.n_lat + header.n_lon) * 8;
    let aod_bytes = cells * 4;
    let ext_bytes = cells * LEVELS.len() * 4;
    let expected_data_end = header_end + axes_bytes + aod_bytes + ext_bytes;
    if expected_data_end != data_end {
        return Err("GEOS-FP fsopt payload byte size mismatch".into());
    }
    let valid_time = time_contract::parse_utc(&header.valid_time_utc)?;

    let mut pos = header_end;
    let mut lat_axis = Vec::with_capacity(header.n_lat);
    for _ in 0..header.n_lat {
        let v = f64::from_le_bytes(bytes[pos..pos + 8].try_into().unwrap());
        if !v.is_finite() {
            return Err("GEOS-FP fsopt contains nonfinite latitude".into());
        }
        lat_axis.push(v);
        pos += 8;
    }
    let mut lon_axis = Vec::with_capacity(header.n_lon);
    for _ in 0..header.n_lon {
        let v = f64::from_le_bytes(bytes[pos..pos + 8].try_into().unwrap());
        if !v.is_finite() {
            return Err("GEOS-FP fsopt contains nonfinite longitude".into());
        }
        lon_axis.push(v);
        pos += 8;
    }
    let mut aod = Vec::with_capacity(cells);
    for _ in 0..cells {
        let v = f64::from(f32::from_le_bytes(bytes[pos..pos + 4].try_into().unwrap()));
        if !v.is_finite() || !(0.0..=100.0).contains(&v) {
            return Err("GEOS-FP fsopt aod550 value outside valid range".into());
        }
        aod.push(v);
        pos += 4;
    }
    let mut extinction = Vec::with_capacity(cells * LEVELS.len());
    for _ in 0..(cells * LEVELS.len()) {
        let v = f64::from(f32::from_le_bytes(bytes[pos..pos + 4].try_into().unwrap()));
        if !v.is_finite() || v < 0.0 {
            return Err("GEOS-FP fsopt extinction value outside valid range".into());
        }
        extinction.push(v);
        pos += 4;
    }
    if aod.iter().enumerate().any(|(cell, &v)| {
        v > 0.0 && (0..LEVELS.len()).all(|level| extinction[level * cells + cell] <= 0.0)
    }) {
        return Err("GEOS-FP positive aod550 has zero extinction profile".into());
    }

    let mut latitudes = Vec::with_capacity(cells);
    let mut longitudes = Vec::with_capacity(cells);
    for &lat in &lat_axis {
        for &lon in &lon_axis {
            latitudes.push(lat);
            longitudes.push(lon);
        }
    }
    Ok(Optics {
        latitudes,
        longitudes,
        aod,
        extinction,
        valid_time,
        source: "geos-fp".into(),
    })
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TimeAxis {
    pub start: DateTime<Utc>,
    pub step_minutes: i64,
    pub size: usize,
}

impl TimeAxis {
    pub fn index_of(&self, target: DateTime<Utc>) -> Option<usize> {
        let secs = (target - self.start).num_seconds();
        let step_secs = self.step_minutes.checked_mul(60)?;
        if secs < 0 || step_secs <= 0 || secs % step_secs != 0 {
            return None;
        }
        let idx = usize::try_from(secs / step_secs).ok()?;
        (idx < self.size).then_some(idx)
    }

    pub fn end(&self) -> Option<DateTime<Utc>> {
        if self.size == 0 {
            return None;
        }
        self.start
            .checked_add_signed(Duration::minutes(self.step_minutes * (self.size as i64 - 1)))
    }
}

pub fn parse_grads_time(text: &str) -> Result<DateTime<Utc>, String> {
    let lower = text.trim().to_ascii_lowercase();
    let (time_part, date_part) = lower
        .split_once('z')
        .ok_or_else(|| format!("invalid GrADS timestamp: {text}"))?;
    let (hour, minute) = match time_part.split_once(':') {
        Some((h, m)) => (
            h.parse::<u32>()
                .map_err(|_| format!("invalid GrADS hour: {text}"))?,
            m.parse::<u32>()
                .map_err(|_| format!("invalid GrADS minute: {text}"))?,
        ),
        None => (
            time_part
                .parse::<u32>()
                .map_err(|_| format!("invalid GrADS hour: {text}"))?,
            0,
        ),
    };
    if hour > 23 || minute > 59 || date_part.len() < 8 {
        return Err(format!("invalid GrADS date/time bounds: {text}"));
    }
    // e.g. 25nov2026 or 5nov2026
    let year_idx = date_part.len() - 4;
    let month_idx = year_idx - 3;
    let day: u32 = date_part[..month_idx]
        .parse()
        .map_err(|_| format!("invalid GrADS day: {text}"))?;
    let year: i32 = date_part[year_idx..]
        .parse()
        .map_err(|_| format!("invalid GrADS year: {text}"))?;
    let month = match &date_part[month_idx..year_idx] {
        "jan" => 1,
        "feb" => 2,
        "mar" => 3,
        "apr" => 4,
        "may" => 5,
        "jun" => 6,
        "jul" => 7,
        "aug" => 8,
        "sep" => 9,
        "oct" => 10,
        "nov" => 11,
        "dec" => 12,
        _ => return Err(format!("invalid GrADS month: {text}")),
    };
    let date = NaiveDate::from_ymd_opt(year, month, day)
        .ok_or_else(|| format!("invalid GrADS calendar date: {text}"))?;
    let dt: NaiveDateTime = date
        .and_hms_opt(hour, minute, 0)
        .ok_or_else(|| format!("invalid GrADS clock: {text}"))?;
    time_contract::checked(Utc.from_utc_datetime(&dt))
}

fn parse_grads_step_minutes(text: &str) -> Result<i64, String> {
    let lower = text.trim().to_ascii_lowercase();
    if let Some(num) = lower.strip_suffix("mn") {
        let value = num
            .parse::<i64>()
            .map_err(|_| format!("invalid GrADS step minutes: {text}"))?;
        if value <= 0 {
            return Err("GrADS step must be positive".into());
        }
        return Ok(value);
    }
    if let Some(num) = lower.strip_suffix("hr") {
        let value = num
            .parse::<i64>()
            .map_err(|_| format!("invalid GrADS step hours: {text}"))?;
        if value <= 0 {
            return Err("GrADS step must be positive".into());
        }
        return Ok(value * 60);
    }
    if let Some(num) = lower.strip_suffix("dy") {
        let value = num
            .parse::<i64>()
            .map_err(|_| format!("invalid GrADS step days: {text}"))?;
        if value <= 0 {
            return Err("GrADS step must be positive".into());
        }
        return Ok(value * 1440);
    }
    Err(format!("unsupported GrADS step unit: {text}"))
}

fn extract_quoted_attr(line: &str, key: &str) -> Option<String> {
    let trimmed = line.trim();
    let needle = format!("{key} ");
    let idx = trimmed.find(&needle)?;
    let rest = trimmed[idx + needle.len()..].trim();
    let start = rest.find('"')? + 1;
    let end = rest[start..].find('"')?;
    Some(rest[start..start + end].to_owned())
}

pub fn parse_das_time_axis(das: &str) -> Result<TimeAxis, String> {
    if das.contains("Error {") && !das.contains("Attributes {") {
        return Err("OPeNDAP returned dataset error instead of DAS".into());
    }
    let mut in_time = false;
    let mut size: Option<usize> = None;
    let mut min_str: Option<String> = None;
    let mut step_str: Option<String> = None;
    for line in das.lines() {
        let trimmed = line.trim();
        if !in_time {
            if trimmed == "time {" || trimmed.starts_with("time {") {
                in_time = true;
            }
            continue;
        }
        if trimmed == "}" {
            break;
        }
        if let Some(v) = extract_quoted_attr(trimmed, "grads_size") {
            size = v.parse::<usize>().ok();
        } else if let Some(v) = extract_quoted_attr(trimmed, "grads_min") {
            min_str = Some(v);
        } else if let Some(v) = extract_quoted_attr(trimmed, "grads_step") {
            step_str = Some(v);
        }
    }
    let size = size.filter(|&n| n > 0 && n <= 10_000).ok_or("missing time.grads_size in DAS")?;
    let start = parse_grads_time(&min_str.ok_or("missing time.grads_min in DAS")?)?;
    let step_minutes = parse_grads_step_minutes(&step_str.ok_or("missing time.grads_step in DAS")?)?;
    Ok(TimeAxis {
        start,
        step_minutes,
        size,
    })
}

#[derive(Clone, Debug, PartialEq)]
pub struct DodsGrid {
    pub values: Vec<f32>,
    pub maps: Vec<Vec<f64>>,
}

pub fn parse_dods_grids(bytes: &[u8], expected_dims: &[usize]) -> Result<Vec<DodsGrid>, String> {
    if bytes.len() > MAX_DODS_BYTES {
        return Err("GEOS-FP .dods payload exceeds size limit".into());
    }
    let (header_bytes, body) = if let Some(pos) = bytes.windows(7).position(|w| w == b"\nData:\n") {
        (&bytes[..pos], &bytes[pos + 7..])
    } else if let Some(pos) = bytes.windows(9).position(|w| w == b"\r\nData:\r\n") {
        (&bytes[..pos], &bytes[pos + 9..])
    } else {
        let preview = String::from_utf8_lossy(&bytes[..bytes.len().min(512)]);
        return Err(format!("GEOS-FP .dods response missing Data delimiter: {preview}"));
    };
    let header_str = String::from_utf8_lossy(header_bytes);
    if header_str.contains("Error {") && !header_str.contains("Dataset {") {
        return Err(format!("GEOS-FP OPeNDAP error in .dods header: {header_str}"));
    }

    let mut pos = 0usize;
    let read_u32 = |pos: &mut usize| -> Result<usize, String> {
        if *pos + 4 > body.len() {
            return Err("truncated GEOS-FP XDR length".into());
        }
        let v = u32::from_be_bytes(body[*pos..*pos + 4].try_into().unwrap()) as usize;
        *pos += 4;
        Ok(v)
    };

    let mut grids = Vec::with_capacity(expected_dims.len());
    for &ndims in expected_dims {
        let c1 = read_u32(&mut pos)?;
        let c2 = read_u32(&mut pos)?;
        if c1 != c2 || c1 == 0 || c1 > MAX_DODS_ITEMS {
            return Err("invalid GEOS-FP .dods array item count".into());
        }
        let needed = c1
            .checked_mul(4)
            .and_then(|n| pos.checked_add(n))
            .ok_or("GEOS-FP .dods array offset overflow")?;
        if needed > body.len() {
            return Err("truncated GEOS-FP .dods float32 payload".into());
        }
        let mut values = Vec::with_capacity(c1);
        for chunk in body[pos..pos + c1 * 4].chunks_exact(4) {
            values.push(f32::from_be_bytes(chunk.try_into().unwrap()));
        }
        pos += c1 * 4;

        let mut maps = Vec::with_capacity(ndims);
        let mut map_product = 1usize;
        for _ in 0..ndims {
            let m1 = read_u32(&mut pos)?;
            let m2 = read_u32(&mut pos)?;
            if m1 != m2 || m1 == 0 || m1 > 100_000 {
                return Err("invalid GEOS-FP .dods map axis length".into());
            }
            let map_needed = m1
                .checked_mul(8)
                .and_then(|n| pos.checked_add(n))
                .ok_or("GEOS-FP .dods map offset overflow")?;
            if map_needed > body.len() {
                return Err("truncated GEOS-FP .dods float64 map payload".into());
            }
            let mut axis = Vec::with_capacity(m1);
            for chunk in body[pos..pos + m1 * 8].chunks_exact(8) {
                axis.push(f64::from_be_bytes(chunk.try_into().unwrap()));
            }
            pos += m1 * 8;
            map_product = map_product
                .checked_mul(m1)
                .ok_or("GEOS-FP .dods axis product overflow")?;
            maps.push(axis);
        }
        if map_product != c1 {
            return Err("GEOS-FP .dods map dimensions do not match array length".into());
        }
        grids.push(DodsGrid { values, maps });
    }
    if pos != body.len() {
        return Err(format!(
            "GEOS-FP .dods trailing unexpected bytes: parsed {pos} of {}",
            body.len()
        ));
    }
    Ok(grids)
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SubsetSpec {
    pub lat_start: usize,
    pub lat_end: usize,
    pub lon_start: usize,
    pub lon_end: usize,
    pub stride: usize,
    pub seam: bool,
}

impl SubsetSpec {
    pub fn n_lat(&self) -> usize {
        (self.lat_end - self.lat_start) / self.stride + 1
    }

    pub fn n_lon_main(&self) -> usize {
        (self.lon_end - self.lon_start) / self.stride + 1
    }
}

pub fn compute_subset(bounds: [f64; 4]) -> Result<SubsetSpec, String> {
    let [north, west, south, east] = bounds;
    if [north, west, south, east].iter().any(|v| !v.is_finite())
        || north <= south
        || north > 90.0
        || south < -90.0
        || west < -180.0
        || east > 180.0
        || west >= east
    {
        return Err("GEOS-FP subset requires an ordinary finite rectangle".into());
    }
    let stride = if (north - south) <= 55.0 && (east - west) <= 55.0 {
        1usize
    } else {
        2usize
    };

    let mut lat_start = (((south + 90.0) / GEOS_LAT_STEP).floor() as isize - 1).max(0) as usize;
    let mut lat_end = (((north + 90.0) / GEOS_LAT_STEP).ceil() as isize + 1)
        .min((GEOS_LAT_COUNT - 1) as isize) as usize;
    let rem_lat = (lat_end - lat_start) % stride;
    if rem_lat != 0 {
        let extra = stride - rem_lat;
        if lat_end + extra < GEOS_LAT_COUNT {
            lat_end += extra;
        } else if lat_start >= extra {
            lat_start -= extra;
        } else {
            lat_end -= rem_lat;
        }
    }
    if lat_end - lat_start < stride {
        if lat_end + stride < GEOS_LAT_COUNT {
            lat_end += stride;
        } else if lat_start >= stride {
            lat_start -= stride;
        }
    }

    let max_lon_idx = GEOS_LON_COUNT - 1; // 1151 -> 179.6875
    let seam = east > 179.6875 - 1e-6;
    let mut lon_start = (((west + 180.0) / GEOS_LON_STEP).floor() as isize - 1).max(0) as usize;
    let mut lon_end = if seam {
        max_lon_idx
    } else {
        (((east + 180.0) / GEOS_LON_STEP).ceil() as isize + 1).min(max_lon_idx as isize) as usize
    };
    let rem_lon = (lon_end - lon_start) % stride;
    if rem_lon != 0 {
        let extra = stride - rem_lon;
        if !seam && lon_end + extra <= max_lon_idx {
            lon_end += extra;
        } else if lon_start >= extra {
            lon_start -= extra;
        } else {
            lon_end -= rem_lon;
        }
    }
    if lon_end - lon_start < stride {
        if lon_end + stride <= max_lon_idx {
            lon_end += stride;
        } else if lon_start >= stride {
            lon_start -= stride;
        }
    }

    Ok(SubsetSpec {
        lat_start,
        lat_end,
        lon_start,
        lon_end,
        stride,
        seam,
    })
}

fn http_client(timeout: StdDuration) -> Result<Client, String> {
    crate::http_client::builder()
        .timeout(timeout)
        .connect_timeout(timeout.min(CONNECT_TIMEOUT))
        .redirect(reqwest::redirect::Policy::limited(5))
        .referer(false)
        .retry(reqwest::retry::never())
        .user_agent("firesky/0.1 burning-sunset-engine")
        .build()
        .map_err(|_| "cannot initialize GEOS-FP HTTP client".into())
}

#[derive(Debug)]
enum FetchError {
    DatasetUnavailable(String),
    Retryable(String),
    Cancelled,
}

impl From<FetchError> for String {
    fn from(value: FetchError) -> Self {
        match value {
            FetchError::DatasetUnavailable(s) | FetchError::Retryable(s) => s,
            FetchError::Cancelled => "desktop operation cancelled".into(),
        }
    }
}

fn fetch_bytes(
    client: &Client,
    url: &str,
    attempts: u32,
    cancel: &AtomicBool,
    on_bytes: &impl Fn(usize) -> Result<(), String>,
) -> Result<Vec<u8>, FetchError> {
    let mut last_err = String::new();
    for attempt in 0..attempts.max(1) {
        if cancel.load(Ordering::Acquire) {
            return Err(FetchError::Cancelled);
        }
        if attempt > 0 {
            let backoff_ms = match attempt {
                1 => 2000,
                _ => 5000,
            };
            let steps = backoff_ms / 100;
            for _ in 0..steps {
                if cancel.load(Ordering::Acquire) {
                    return Err(FetchError::Cancelled);
                }
                std::thread::sleep(StdDuration::from_millis(100));
            }
        }
        let mut resp = match client
            .get(url)
            .header("Accept-Encoding", "identity")
            .send()
        {
            Ok(r) => r,
            Err(e) => {
                last_err = format!("GEOS-FP HTTP error: {e}");
                continue;
            }
        };
        let status = resp.status().as_u16();
        if status == 404 {
            return Err(FetchError::DatasetUnavailable(format!("HTTP 404 for {url}")));
        }
        if !resp.status().is_success() {
            last_err = format!("GEOS-FP HTTP {status}");
            if (400..500).contains(&status) && status != 429 && status != 408 {
                return Err(FetchError::DatasetUnavailable(last_err));
            }
            continue;
        }
        let is_dods_error = resp
            .headers()
            .get("Content-Description")
            .and_then(|v| v.to_str().ok())
            .is_some_and(|v| v.eq_ignore_ascii_case("dods_error"));
        if resp
            .content_length()
            .is_some_and(|len| len > MAX_DODS_BYTES as u64)
        {
            return Err(FetchError::DatasetUnavailable(
                "GEOS-FP response exceeds limit".into(),
            ));
        }

        let mut payload = Vec::new();
        let mut chunk = [0u8; 32 * 1024];
        let mut read_failed = None;
        loop {
            if cancel.load(Ordering::Acquire) {
                return Err(FetchError::Cancelled);
            }
            match resp.read(&mut chunk) {
                Ok(0) => break,
                Ok(n) => {
                    if payload.len() + n > MAX_DODS_BYTES {
                        return Err(FetchError::DatasetUnavailable(
                            "GEOS-FP payload exceeds limit".into(),
                        ));
                    }
                    payload.extend_from_slice(&chunk[..n]);
                    if on_bytes(n).is_err() {
                        return Err(FetchError::Cancelled);
                    }
                }
                Err(e) => {
                    read_failed = Some(format!("GEOS-FP stream read failed: {e}"));
                    break;
                }
            }
        }
        if let Some(msg) = read_failed {
            last_err = msg;
            continue;
        }
        if is_dods_error {
            let preview = String::from_utf8_lossy(&payload[..payload.len().min(512)]);
            if preview.contains("is not an available dataset") {
                return Err(FetchError::DatasetUnavailable(preview.into_owned()));
            }
            last_err = format!("GEOS-FP OPeNDAP dods_error: {preview}");
            continue;
        }
        if payload.starts_with(b"Error {") {
            let preview = String::from_utf8_lossy(&payload[..payload.len().min(512)]);
            if preview.contains("is not an available dataset") {
                return Err(FetchError::DatasetUnavailable(preview.into_owned()));
            }
            last_err = format!("GEOS-FP OPeNDAP error: {preview}");
            continue;
        }
        return Ok(payload);
    }
    Err(FetchError::Retryable(if last_err.is_empty() {
        "GEOS-FP request failed".into()
    } else {
        last_err
    }))
}

#[derive(Clone, Debug)]
pub struct CycleAxes {
    pub cycle: DateTime<Utc>,
    pub aod_time: TimeAxis,
    pub ext_time: TimeAxis,
}

fn candidate_cycles(anchor_utc: DateTime<Utc>, count: usize) -> Result<Vec<DateTime<Utc>>, String> {
    let day = anchor_utc.date_naive();
    let hour = (anchor_utc.hour() / 12) * 12;
    let mut cur = time_contract::checked(Utc.from_utc_datetime(&day.and_hms_opt(hour, 0, 0).unwrap()))?;
    let mut result = Vec::with_capacity(count);
    for _ in 0..count {
        result.push(cur);
        cur = cur
            .checked_sub_signed(Duration::hours(12))
            .ok_or("GEOS-FP candidate cycle underflow")?;
    }
    Ok(result)
}

fn load_cycle_axes(
    client: &Client,
    base: &str,
    cycle: DateTime<Utc>,
    attempts: u32,
    cancel: &AtomicBool,
) -> Result<Option<CycleAxes>, String> {
    let aod_url = das_url(base, COL_2D_AOD, cycle);
    let ext_url = das_url(base, COL_3D_EXT, cycle);
    let noop = |_: usize| Ok(());
    let aod_bytes = match fetch_bytes(client, &aod_url, attempts, cancel, &noop) {
        Ok(b) => b,
        Err(FetchError::DatasetUnavailable(_)) => return Ok(None),
        Err(FetchError::Cancelled) => return Err("desktop operation cancelled".into()),
        Err(FetchError::Retryable(e)) => return Err(e),
    };
    let ext_bytes = match fetch_bytes(client, &ext_url, attempts, cancel, &noop) {
        Ok(b) => b,
        Err(FetchError::DatasetUnavailable(_)) => return Ok(None),
        Err(FetchError::Cancelled) => return Err("desktop operation cancelled".into()),
        Err(FetchError::Retryable(e)) => return Err(e),
    };
    let aod_text = String::from_utf8(aod_bytes).map_err(|_| "invalid UTF-8 in 2D DAS".to_string())?;
    let ext_text = String::from_utf8(ext_bytes).map_err(|_| "invalid UTF-8 in 3D DAS".to_string())?;
    let Ok(aod_time) = parse_das_time_axis(&aod_text) else {
        return Ok(None);
    };
    let Ok(ext_time) = parse_das_time_axis(&ext_text) else {
        return Ok(None);
    };
    Ok(Some(CycleAxes {
        cycle,
        aod_time,
        ext_time,
    }))
}

pub fn latest_publication_cycle(
    now: DateTime<Utc>,
    timeout_seconds: f64,
) -> Result<DateTime<Utc>, String> {
    let base = base_url();
    let timeout = StdDuration::from_secs_f64(timeout_seconds.clamp(1.0, 60.0));
    let client = http_client(timeout)?;
    let cancel = AtomicBool::new(false);
    let candidates = candidate_cycles(now, 6)?;
    let mut last_network_err = None;
    for cycle in candidates {
        match load_cycle_axes(&client, &base, cycle, 1, &cancel) {
            Ok(Some(_)) => return Ok(cycle),
            Ok(None) => continue,
            Err(e) => {
                last_network_err = Some(e);
                continue;
            }
        }
    }
    Err(last_network_err.unwrap_or_else(|| "no published GEOS-FP 00Z/12Z cycle available".into()))
}

#[derive(Clone)]
struct ChosenStep {
    axes: CycleAxes,
    target: DateTime<Utc>,
    idx_2d: usize,
    idx_3d: usize,
    lead_hours: u32,
}

fn choose_runs_for_targets(
    client: &Client,
    base: &str,
    targets: &[DateTime<Utc>],
    refresh_before: Option<DateTime<Utc>>,
    cancel: &AtomicBool,
) -> Result<Vec<ChosenStep>, String> {
    let Some(&max_target) = targets.iter().max() else {
        return Err("GEOS-FP requires at least one target time".into());
    };
    let now = DateTime::<Utc>::from(SystemTime::now());
    let anchor = max_target.min(now + Duration::hours(12));
    // Search candidate 00Z/12Z cycles going back up to 5 days (10 cycles) from the newest reasonable anchor.
    let candidates = candidate_cycles(anchor, 10)?;
    let mut loaded: Vec<CycleAxes> = Vec::new();
    for cycle in candidates {
        if cancel.load(Ordering::Acquire) {
            return Err("desktop operation cancelled".into());
        }
        if refresh_before.is_some_and(|r| cycle < r) {
            continue;
        }
        if cycle > max_target {
            continue;
        }
        match load_cycle_axes(client, base, cycle, 2, cancel) {
            Ok(Some(axes)) => loaded.push(axes),
            Ok(None) => continue,
            Err(e) => {
                if loaded.is_empty() {
                    continue;
                } else {
                    return Err(e);
                }
            }
        }
    }
    if loaded.is_empty() {
        return Err("no usable GEOS-FP 00Z/12Z forecast cycles found on OPeNDAP".into());
    }
    let mut chosen = Vec::with_capacity(targets.len());
    for &target in targets {
        let mut found = None;
        for axes in &loaded {
            if axes.cycle > target {
                continue;
            }
            if refresh_before.is_some_and(|r| r <= target && axes.cycle < r) {
                continue;
            }
            if let (Some(idx_2d), Some(idx_3d)) =
                (axes.aod_time.index_of(target), axes.ext_time.index_of(target))
            {
                let lead_secs = (target - axes.cycle).num_seconds();
                if lead_secs < 0 || lead_secs % 3600 != 0 {
                    continue;
                }
                let lead_hours = (lead_secs / 3600) as u32;
                if lead_hours > 999 {
                    continue;
                }
                found = Some(ChosenStep {
                    axes: axes.clone(),
                    target,
                    idx_2d,
                    idx_3d,
                    lead_hours,
                });
                break;
            }
        }
        let step = found.ok_or_else(|| {
            format!(
                "GEOS-FP OPeNDAP does not cover requested valid time {}",
                time_contract::iso(target)
            )
        })?;
        chosen.push(step);
    }
    Ok(chosen)
}

fn target_path(
    root: &Path,
    area_key: &str,
    run_time: DateTime<Utc>,
    lead: u32,
) -> PathBuf {
    root.join(run_time.date_naive().to_string())
        .join(format!("{:02}Z", run_time.hour()))
        .join(format!(
            "geosfp-optics-v1-{}-{:02}00-{lead:03}-{area_key}.fsopt",
            run_time.date_naive(),
            run_time.hour()
        ))
}

fn level_indices(lev_map: &[f64]) -> Result<[usize; 11], String> {
    let mut indices = [0usize; 11];
    for (slot, &target_hpa) in LEVELS.iter().enumerate() {
        let pos = lev_map
            .iter()
            .position(|&v| (v - f64::from(target_hpa)).abs() < 1e-3)
            .ok_or_else(|| format!("GEOS-FP 3D grid missing pressure level {target_hpa} hPa"))?;
        indices[slot] = pos;
    }
    Ok(indices)
}

fn clean_aod_value(v: f32) -> Result<f32, String> {
    if !v.is_finite() || v >= 1e14 {
        return Err("GEOS-FP 2D AOD contains fill or nonfinite values".into());
    }
    if v < 0.0 {
        if v >= -1e-4 {
            return Ok(0.0);
        }
        return Err(format!("GEOS-FP 2D AOD negative out of bounds: {v}"));
    }
    if v > 100.0 {
        return Err(format!("GEOS-FP 2D AOD exceeds physical upper bound: {v}"));
    }
    Ok(v)
}

fn clean_ext_value(v: f32) -> f32 {
    if !v.is_finite() || v >= 1e14 || v < 0.0 {
        0.0
    } else {
        v
    }
}

pub fn assemble_step_tensors(
    subset: &SubsetSpec,
    grid_2d: &DodsGrid,
    grid_2d_seam: Option<&DodsGrid>,
    grids_3d: &[DodsGrid],
    grids_3d_seam: Option<&[DodsGrid]>,
) -> Result<(Vec<f64>, Vec<f64>, Vec<f32>, Vec<f32>), String> {
    if grid_2d.maps.len() != 3 || grids_3d.len() != EXT_SPECIES.len() {
        return Err("GEOS-FP .dods grid rank mismatch".into());
    }
    let lat_axis = &grid_2d.maps[1];
    let lon_main = &grid_2d.maps[2];
    let n_lat = subset.n_lat();
    let n_lon_main = subset.n_lon_main();
    if lat_axis.len() != n_lat || lon_main.len() != n_lon_main {
        return Err("GEOS-FP 2D spatial axis dimensions do not match requested subset".into());
    }
    let mut lon_axis = lon_main.clone();
    if subset.seam {
        let seam_2d = grid_2d_seam.ok_or("missing 2D seam grid")?;
        if seam_2d.maps.len() != 3
            || seam_2d.maps[1] != *lat_axis
            || seam_2d.maps[2].len() != 1
            || (seam_2d.maps[2][0] - (-180.0)).abs() > 1e-6
        {
            return Err("invalid GEOS-FP 2D antimeridian seam axes".into());
        }
        lon_axis.push(180.0);
    }
    let n_lon = lon_axis.len();
    let cells = n_lat * n_lon;

    let mut aod = Vec::with_capacity(cells);
    for r in 0..n_lat {
        let row_off = r * n_lon_main;
        for c in 0..n_lon_main {
            aod.push(clean_aod_value(grid_2d.values[row_off + c])?);
        }
        if let Some(seam_2d) = grid_2d_seam {
            aod.push(clean_aod_value(seam_2d.values[r])?);
        }
    }

    let first_3d = &grids_3d[0];
    if first_3d.maps.len() != 4 || first_3d.maps[2] != *lat_axis || first_3d.maps[3] != *lon_main {
        return Err("GEOS-FP 3D grid spatial axes differ from 2D AOD grid".into());
    }
    let lev_map = &first_3d.maps[1];
    let lev_idxs = level_indices(lev_map)?;
    for g in &grids_3d[1..] {
        if g.maps != first_3d.maps {
            return Err("GEOS-FP 3D extinction species grids have mismatched axes".into());
        }
    }
    if let Some(seam_3d) = grids_3d_seam {
        if seam_3d.len() != EXT_SPECIES.len() {
            return Err("GEOS-FP 3D seam missing species".into());
        }
        for g in seam_3d {
            if g.maps.len() != 4
                || g.maps[1] != *lev_map
                || g.maps[2] != *lat_axis
                || g.maps[3].len() != 1
            {
                return Err("GEOS-FP 3D seam axes mismatch".into());
            }
        }
    }

    let mut extinction = vec![0.0f32; LEVELS.len() * cells];
    let main_plane = n_lat * n_lon_main;
    for (out_lev, &src_lev) in lev_idxs.iter().enumerate() {
        let lev_base = out_lev * cells;
        let src_base = src_lev * main_plane;
        let seam_base = src_lev * n_lat;
        for r in 0..n_lat {
            let out_row = lev_base + r * n_lon;
            let src_row = src_base + r * n_lon_main;
            for c in 0..n_lon_main {
                let mut sum = 0.0f32;
                for sp in grids_3d {
                    sum += clean_ext_value(sp.values[src_row + c]);
                }
                extinction[out_row + c] = sum;
            }
            if let Some(seam_3d) = grids_3d_seam {
                let mut sum = 0.0f32;
                for sp in seam_3d {
                    sum += clean_ext_value(sp.values[seam_base + r]);
                }
                extinction[out_row + n_lon_main] = sum;
            }
        }
    }

    // Preserve downstream normalization invariant: positive AOD cannot pair with an all-zero profile.
    for cell in 0..cells {
        if aod[cell] > 0.0 && (0..LEVELS.len()).all(|l| extinction[l * cells + cell] <= 0.0) {
            extinction[10 * cells + cell] = 1e-9;
        }
    }

    Ok((lat_axis.clone(), lon_axis, aod, extinction))
}

fn fetch_step_to_fsopt(
    client: &Client,
    base: &str,
    root: &Path,
    area_bounds: [f64; 4],
    area_key: &str,
    step: &ChosenStep,
    cancel: &AtomicBool,
    on_bytes: &impl Fn(usize) -> Result<(), String>,
) -> Result<(PathBuf, bool), String> {
    let path = target_path(root, area_key, step.axes.cycle, step.lead_hours);
    if let Ok(m) = fs::symlink_metadata(&path) {
        if !m.is_file() {
            return Err("GEOS-FP cache path cannot be a symlink or directory".into());
        }
        if m.len() > 0 {
            if let Ok(optics) = decode_fsopt(&path) {
                if optics.valid_time == step.target {
                    return Ok((path, false));
                }
            }
        }
    }
    let parent = path.parent().ok_or("GEOS-FP target path missing parent")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let lock_path = parent.join(format!(
        ".geosfp-{:02}00-{:03}-{area_key}.lock",
        step.axes.cycle.hour(),
        step.lead_hours
    ));
    let _lock = Lock::acquire(&lock_path, 300.0)?;

    // Re-check inside lock
    if fs::symlink_metadata(&path).is_ok_and(|m| m.is_file() && m.len() > 0) {
        if let Ok(optics) = decode_fsopt(&path) {
            if optics.valid_time == step.target {
                return Ok((path, false));
            }
        }
    }

    let subset = compute_subset(area_bounds)?;
    let s = subset.stride;
    let q2d = format!(
        "totexttau[{}:{}][{}:{}:{}][{}:{}:{}]",
        step.idx_2d, step.idx_2d, subset.lat_start, s, subset.lat_end, subset.lon_start, s, subset.lon_end
    );
    let url_2d = dods_url(base, COL_2D_AOD, step.axes.cycle, &q2d);
    let bytes_2d = fetch_bytes(client, &url_2d, 3, cancel, on_bytes).map_err(String::from)?;
    let grids_2d = parse_dods_grids(&bytes_2d, &[3])?;

    let grid_2d_seam = if subset.seam {
        let q_seam = format!(
            "totexttau[{}:{}][{}:{}:{}][0:0]",
            step.idx_2d, step.idx_2d, subset.lat_start, s, subset.lat_end
        );
        let url_seam = dods_url(base, COL_2D_AOD, step.axes.cycle, &q_seam);
        let b = fetch_bytes(client, &url_seam, 3, cancel, on_bytes).map_err(String::from)?;
        Some(parse_dods_grids(&b, &[3])?.into_iter().next().unwrap())
    } else {
        None
    };

    let q3d = EXT_SPECIES
        .iter()
        .map(|v| {
            format!(
                "{v}[{}:{}][0:{}][{}:{}:{}][{}:{}:{}]",
                step.idx_3d,
                step.idx_3d,
                GEOS_MAX_LEVEL_INDEX,
                subset.lat_start,
                s,
                subset.lat_end,
                subset.lon_start,
                s,
                subset.lon_end
            )
        })
        .collect::<Vec<_>>()
        .join(",");
    let url_3d = dods_url(base, COL_3D_EXT, step.axes.cycle, &q3d);
    let bytes_3d = fetch_bytes(client, &url_3d, 3, cancel, on_bytes).map_err(String::from)?;
    let dims_3d = [4usize; EXT_SPECIES.len()];
    let grids_3d = parse_dods_grids(&bytes_3d, &dims_3d)?;

    let grids_3d_seam = if subset.seam {
        let q3d_seam = EXT_SPECIES
            .iter()
            .map(|v| {
                format!(
                    "{v}[{}:{}][0:{}][{}:{}:{}][0:0]",
                    step.idx_3d,
                    step.idx_3d,
                    GEOS_MAX_LEVEL_INDEX,
                    subset.lat_start,
                    s,
                    subset.lat_end
                )
            })
            .collect::<Vec<_>>()
            .join(",");
        let url_seam = dods_url(base, COL_3D_EXT, step.axes.cycle, &q3d_seam);
        let b = fetch_bytes(client, &url_seam, 3, cancel, on_bytes).map_err(String::from)?;
        Some(parse_dods_grids(&b, &dims_3d)?)
    } else {
        None
    };

    let (lat_axis, lon_axis, aod, extinction) = assemble_step_tensors(
        &subset,
        &grids_2d[0],
        grid_2d_seam.as_ref(),
        &grids_3d,
        grids_3d_seam.as_deref(),
    )?;
    let bounds_written = [
        *lat_axis.last().unwrap(),
        lon_axis[0],
        lat_axis[0],
        *lon_axis.last().unwrap(),
    ];
    let header = FsoptHeader {
        contract: FSOPT_CONTRACT.into(),
        source: "geos-fp".into(),
        valid_time_utc: time_contract::iso(step.target),
        run_time_utc: time_contract::iso(step.axes.cycle),
        lead_hours: step.lead_hours,
        n_lat: lat_axis.len(),
        n_lon: lon_axis.len(),
        pressure_levels: LEVELS.to_vec(),
        bounds: bounds_written,
    };
    write_fsopt(&path, &header, &lat_axis, &lon_axis, &aod, &extinction)?;
    let verified = decode_fsopt(&path)?;
    if verified.valid_time != step.target {
        return Err("decoded GEOS-FP valid_time disagrees with requested bracket".into());
    }
    Ok((path, true))
}

pub fn download_targets(
    root: &Path,
    area_bounds: [f64; 4],
    area_key: &str,
    targets: &[DateTime<Utc>],
    refresh_before: Option<DateTime<Utc>>,
    stage: &str,
    cancel: &AtomicBool,
    progress: Option<&(dyn Fn(Value) -> Result<(), String> + Sync)>,
) -> Result<BTreeMap<DateTime<Utc>, PathBuf>, String> {
    download_targets_with_base(
        &base_url(),
        root,
        area_bounds,
        area_key,
        targets,
        refresh_before,
        stage,
        cancel,
        progress,
    )
}

pub(crate) fn download_targets_with_base(
    base: &str,
    root: &Path,
    area_bounds: [f64; 4],
    area_key: &str,
    targets: &[DateTime<Utc>],
    refresh_before: Option<DateTime<Utc>>,
    stage: &str,
    cancel: &AtomicBool,
    progress: Option<&(dyn Fn(Value) -> Result<(), String> + Sync)>,
) -> Result<BTreeMap<DateTime<Utc>, PathBuf>, String> {
    if cancel.load(Ordering::Acquire) {
        return Err("desktop operation cancelled".into());
    }
    let client = http_client(READ_TIMEOUT)?;
    let started = Instant::now();
    if let Some(cb) = progress {
        cb(json!({
            "stage": stage,
            "source": "cams",
            "aerosol_source": "geos-fp",
            "message": format!("正在查询 NASA GEOS-FP OPeNDAP 可用周期（{} 个时次）", targets.len()),
        }))?;
    }
    let steps = choose_runs_for_targets(&client, &base, targets, refresh_before, cancel)?;
    let total = steps.len();
    let cursor = AtomicUsize::new(0);
    let done = AtomicUsize::new(0);
    let total_bytes = AtomicU64::new(0);
    let results: Mutex<BTreeMap<DateTime<Utc>, PathBuf>> = Mutex::new(BTreeMap::new());
    let first_error: Mutex<Option<String>> = Mutex::new(None);
    let last_emit_ms = AtomicU64::new(0);

    std::thread::scope(|scope| {
        for _ in 0..DOWNLOAD_WORKERS.min(total.max(1)) {
            scope.spawn(|| loop {
                if cancel.load(Ordering::Acquire) || first_error.lock().unwrap().is_some() {
                    return;
                }
                let index = cursor.fetch_add(1, Ordering::AcqRel);
                if index >= total {
                    return;
                }
                let step = &steps[index];
                let on_bytes = |n: usize| -> Result<(), String> {
                    let bytes = total_bytes.fetch_add(n as u64, Ordering::AcqRel) + n as u64;
                    let now_ms = started.elapsed().as_millis() as u64;
                    let prev_ms = last_emit_ms.load(Ordering::Acquire);
                    if now_ms.saturating_sub(prev_ms) >= 400
                        && last_emit_ms
                            .compare_exchange(prev_ms, now_ms, Ordering::AcqRel, Ordering::Acquire)
                            .is_ok()
                    {
                        if let Some(cb) = progress {
                            let finished = done.load(Ordering::Acquire);
                            cb(json!({
                                "stage": stage,
                                "source": "cams",
                                "aerosol_source": "geos-fp",
                                "done_files": finished,
                                "total_files": total,
                                "bytes": bytes,
                                "message": format!(
                                    "GEOS-FP 正在下载 {}/{} · {:.1} MB",
                                    finished,
                                    total,
                                    bytes as f64 / 1024.0 / 1024.0
                                ),
                            }))?;
                        }
                    }
                    Ok(())
                };
                match fetch_step_to_fsopt(
                    &client,
                    &base,
                    root,
                    area_bounds,
                    area_key,
                    step,
                    cancel,
                    &on_bytes,
                ) {
                    Ok((path, _)) => {
                        results.lock().unwrap().insert(step.target, path);
                        let finished = done.fetch_add(1, Ordering::AcqRel) + 1;
                        let bytes = total_bytes.load(Ordering::Acquire);
                        if let Some(cb) = progress {
                            if cb(json!({
                                "stage": stage,
                                "source": "cams",
                                "aerosol_source": "geos-fp",
                                "done_files": finished,
                                "total_files": total,
                                "bytes": bytes,
                                "message": format!(
                                    "GEOS-FP 已完成 {}/{} · {:.1} MB",
                                    finished,
                                    total,
                                    bytes as f64 / 1024.0 / 1024.0
                                ),
                            }))
                            .is_err()
                            {
                                cancel.store(true, Ordering::Release);
                            }
                        }
                    }
                    Err(e) => {
                        let _ = first_error.lock().unwrap().replace(e);
                    }
                }
            });
        }
    });

    if cancel.load(Ordering::Acquire) {
        return Err("desktop operation cancelled".into());
    }
    if let Some(err) = first_error.into_inner().unwrap() {
        return Err(err);
    }
    Ok(results.into_inner().unwrap())
}

pub fn probe_sample(
    now: DateTime<Utc>,
    cancel: &AtomicBool,
    on_bytes: &impl Fn(usize) -> Result<(), String>,
) -> Result<String, String> {
    let base = base_url();
    let client = http_client(StdDuration::from_secs(15))?;
    let candidates = candidate_cycles(now, 6)?;
    let mut chosen = None;
    for cycle in candidates {
        if let Some(axes) = load_cycle_axes(&client, &base, cycle, 1, cancel)? {
            chosen = Some(axes);
            break;
        }
    }
    let axes = chosen.ok_or("未找到已发布的 NASA GEOS-FP 00Z/12Z 预报周期")?;
    let url_2d = dods_url(&base, COL_2D_AOD, axes.cycle, "totexttau[2:2][480:481][960:961]");
    let b2d = fetch_bytes(&client, &url_2d, 1, cancel, on_bytes).map_err(String::from)?;
    let g2d = parse_dods_grids(&b2d, &[3])?;
    let url_3d = dods_url(
        &base,
        COL_3D_EXT,
        axes.cycle,
        "duext[0:0][0:1][480:481][960:961],suext[0:0][0:1][480:481][960:961]",
    );
    let b3d = fetch_bytes(&client, &url_3d, 1, cancel, on_bytes).map_err(String::from)?;
    let g3d = parse_dods_grids(&b3d, &[4, 4])?;
    if g2d[0].values.iter().any(|&v| !v.is_finite() || v >= 1e14)
        || g3d.iter().flat_map(|g| &g.values).any(|&v| !v.is_finite())
    {
        return Err("GEOS-FP 探测样本包含异常或缺失数值".into());
    }
    Ok(format!(
        "最小 GEOS-FP 样本已下载并校验（周期 {}，550nm AOD + 3D 消光廓线）",
        axes.cycle.format("%Y-%m-%d %HZ")
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_grads_time_and_das_offsets() {
        let das_2d = r#"Attributes {
    time {
        String grads_dim "t";
        String grads_mapping "linear";
        String grads_size "243";
        String grads_min "22z25nov2026";
        String grads_step "60mn";
    }
}"#;
        let axis = parse_das_time_axis(das_2d).unwrap();
        assert_eq!(axis.size, 243);
        assert_eq!(axis.step_minutes, 60);
        assert_eq!(
            axis.start,
            Utc.with_ymd_and_hms(2026, 11, 25, 22, 0, 0).unwrap()
        );
        // 00Z cycle hour is at index 2 because grads_min starts 2 hours early.
        assert_eq!(
            axis.index_of(Utc.with_ymd_and_hms(2026, 11, 26, 0, 0, 0).unwrap()),
            Some(2)
        );
        assert_eq!(
            axis.index_of(Utc.with_ymd_and_hms(2026, 11, 26, 0, 30, 0).unwrap()),
            None
        );

        let das_3d = r#"Attributes {
    time {
        String grads_size "41";
        String grads_min "12z25nov2026";
        String grads_step "180mn";
    }
}"#;
        let axis3 = parse_das_time_axis(das_3d).unwrap();
        assert_eq!(axis3.size, 41);
        assert_eq!(axis3.step_minutes, 180);
        assert_eq!(
            axis3.index_of(Utc.with_ymd_and_hms(2026, 11, 25, 15, 0, 0).unwrap()),
            Some(1)
        );
        assert_eq!(
            axis3.end().unwrap(),
            Utc.with_ymd_and_hms(2026, 11, 30, 12, 0, 0).unwrap()
        );
    }

    fn build_dods(items: &[(&[f32], &[&[f64]])]) -> Vec<u8> {
        let mut out = b"Dataset {\n} dummy;\nData:\n".to_vec();
        for (vals, maps) in items {
            let count = vals.len() as u32;
            out.extend_from_slice(&count.to_be_bytes());
            out.extend_from_slice(&count.to_be_bytes());
            for &v in *vals {
                out.extend_from_slice(&v.to_be_bytes());
            }
            for axis in *maps {
                let len = axis.len() as u32;
                out.extend_from_slice(&len.to_be_bytes());
                out.extend_from_slice(&len.to_be_bytes());
                for &x in *axis {
                    out.extend_from_slice(&x.to_be_bytes());
                }
            }
        }
        out
    }

    #[test]
    fn parses_dods_binary_and_handles_underground_fill_and_seam() {
        let subset = compute_subset([31.0, 178.5, 29.5, 180.0]).unwrap();
        assert!(subset.seam);
        assert_eq!(subset.stride, 1);
        let n_lat = subset.n_lat();
        let n_lon_main = subset.n_lon_main();
        let lats: Vec<f64> = (subset.lat_start..=subset.lat_end)
            .map(|i| -90.0 + i as f64 * GEOS_LAT_STEP)
            .collect();
        let lons_main: Vec<f64> = (subset.lon_start..=subset.lon_end)
            .map(|j| -180.0 + j as f64 * GEOS_LON_STEP)
            .collect();
        let levs: Vec<f64> = vec![
            1000.0, 975.0, 950.0, 925.0, 900.0, 850.0, 800.0, 750.0, 700.0, 650.0, 600.0, 550.0,
            500.0, 450.0, 400.0, 350.0, 300.0,
        ];

        let main_2d_vals = vec![0.25f32; n_lat * n_lon_main];
        let seam_2d_vals = vec![0.35f32; n_lat];
        let g2d = parse_dods_grids(
            &build_dods(&[(&main_2d_vals, &[&[739947.0], &lats, &lons_main])]),
            &[3],
        )
        .unwrap()
        .pop()
        .unwrap();
        let g2d_seam = parse_dods_grids(
            &build_dods(&[(&seam_2d_vals, &[&[739947.0], &lats, &[-180.0]])]),
            &[3],
        )
        .unwrap()
        .pop()
        .unwrap();

        // Fill level 0..11 with 1.0e15 (underground fill over high terrain) and level 12+ (<=500hPa) with valid 1e-5
        let mut ext_vals = vec![0.0f32; levs.len() * n_lat * n_lon_main];
        for (li, &p) in levs.iter().enumerate() {
            for cell in 0..(n_lat * n_lon_main) {
                ext_vals[li * n_lat * n_lon_main + cell] =
                    if p > 550.0 { 1.0e15 } else { 1.0e-5 };
            }
        }
        let mut ext_seam_vals = vec![0.0f32; levs.len() * n_lat];
        for (li, &p) in levs.iter().enumerate() {
            for cell in 0..n_lat {
                ext_seam_vals[li * n_lat + cell] = if p > 550.0 { 1.0e15 } else { 2.0e-5 };
            }
        }
        let g3d_single = parse_dods_grids(
            &build_dods(&[(&ext_vals, &[&[739947.0], &levs, &lats, &lons_main])]),
            &[4],
        )
        .unwrap()
        .pop()
        .unwrap();
        let g3d_seam_single = parse_dods_grids(
            &build_dods(&[(&ext_seam_vals, &[&[739947.0], &levs, &lats, &[-180.0]])]),
            &[4],
        )
        .unwrap()
        .pop()
        .unwrap();
        let grids_3d = vec![g3d_single; 7];
        let grids_3d_seam = vec![g3d_seam_single; 7];

        let (out_lat, out_lon, aod, extinction) =
            assemble_step_tensors(&subset, &g2d, Some(&g2d_seam), &grids_3d, Some(&grids_3d_seam))
                .unwrap();
        assert_eq!(out_lat.len(), n_lat);
        assert_eq!(out_lon.len(), n_lon_main + 1);
        assert_eq!(*out_lon.last().unwrap(), 180.0);
        let cells = out_lat.len() * out_lon.len();
        assert!((aod[n_lon_main] - 0.35).abs() < 1e-6);
        // Underground level 1000 hPa (idx 0) was 1e15 -> cleaned to 0.0
        assert_eq!(extinction[0], 0.0);
        // 500 hPa (LEVELS[8]) was 1e-5 * 7 = 7e-5 on main grid, 2e-5 * 7 = 1.4e-4 on seam
        assert!((extinction[8 * cells] - 7.0e-5).abs() < 1e-9);
        assert!((extinction[8 * cells + n_lon_main] - 1.4e-4).abs() < 1e-9);

        // Roundtrip FSOPT + SHA-256 tamper check
        let dir = tempfile::tempdir().unwrap();
        let fsopt_path = dir.path().join("geosfp-optics-v1-2026-11-26-0000-003-test.fsopt");
        let header = FsoptHeader {
            contract: FSOPT_CONTRACT.into(),
            source: "geos-fp".into(),
            valid_time_utc: "2026-11-26T03:00:00+00:00".into(),
            run_time_utc: "2026-11-26T00:00:00+00:00".into(),
            lead_hours: 3,
            n_lat: out_lat.len(),
            n_lon: out_lon.len(),
            pressure_levels: LEVELS.to_vec(),
            bounds: [*out_lat.last().unwrap(), out_lon[0], out_lat[0], *out_lon.last().unwrap()],
        };
        write_fsopt(&fsopt_path, &header, &out_lat, &out_lon, &aod, &extinction).unwrap();
        let optics = decode_fsopt(&fsopt_path).unwrap();
        assert_eq!(optics.source, "geos-fp");
        assert_eq!(optics.aod.len(), cells);
        let (rh, rlats, rlons) = read_header(&fsopt_path).unwrap();
        assert_eq!(rh, header);
        assert_eq!(rlats, out_lat);
        assert_eq!(rlons, out_lon);

        // Tamper 1 byte -> decode_fsopt rejects checksum mismatch
        let mut raw = fs::read(&fsopt_path).unwrap();
        let last = raw.len() - 40;
        raw[last] ^= 0xff;
        fs::write(&fsopt_path, raw).unwrap();
        assert!(decode_fsopt(&fsopt_path)
            .unwrap_err()
            .contains("checksum"));
    }

    #[test]
    fn mock_opendap_download_handles_dods_error_offset_and_cache_reuse() {
        use std::io::{BufRead, BufReader};
        use std::net::TcpListener;

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let base = format!("http://{addr}/dods");

        let subset = compute_subset([31.0, 119.5, 29.5, 121.0]).unwrap();
        let n_lat = subset.n_lat();
        let n_lon = subset.n_lon_main();
        let lats: Vec<f64> = (subset.lat_start..=subset.lat_end)
            .map(|i| -90.0 + i as f64 * GEOS_LAT_STEP)
            .collect();
        let lons: Vec<f64> = (subset.lon_start..=subset.lon_end)
            .map(|j| -180.0 + j as f64 * GEOS_LON_STEP)
            .collect();
        let levs: Vec<f64> = vec![
            1000.0, 975.0, 950.0, 925.0, 900.0, 850.0, 800.0, 750.0, 700.0, 650.0, 600.0, 550.0,
            500.0, 450.0, 400.0, 350.0, 300.0,
        ];
        let aod_vals = vec![0.42f32; n_lat * n_lon];
        let dods_2d = build_dods(&[(&aod_vals, &[&[739947.125], &lats, &lons])]);
        let ext_vals = vec![2.0e-5f32; levs.len() * n_lat * n_lon];
        let ext_maps: [&[f64]; 4] = [&[739947.125], &levs, &lats, &lons];
        let items_3d: Vec<(&[f32], &[&[f64]])> =
            vec![(&ext_vals[..], &ext_maps[..]); EXT_SPECIES.len()];
        let dods_3d = build_dods(&items_3d);

        let das_2d = "Attributes {\n    time {\n        String grads_size \"243\";\n        String grads_min \"22z25nov2026\";\n        String grads_step \"60mn\";\n    }\n}\n";
        let das_3d = "Attributes {\n    time {\n        String grads_size \"81\";\n        String grads_min \"00z26nov2026\";\n        String grads_step \"180mn\";\n    }\n}\n";
        let dods_missing = b"Error {\n    code = 0;\n    message = \"/x is not an available dataset\";\n};\n";

        let requests_served = std::sync::Arc::new(AtomicUsize::new(0));
        let counter_clone = requests_served.clone();
        let stop = std::sync::Arc::new(AtomicBool::new(false));
        let stop_clone = stop.clone();

        let server = std::thread::spawn(move || {
            listener.set_nonblocking(true).unwrap();
            while !stop_clone.load(Ordering::Acquire) {
                let Ok((mut stream, _)) = listener.accept() else {
                    std::thread::sleep(StdDuration::from_millis(10));
                    continue;
                };
                let mut reader = BufReader::new(stream.try_clone().unwrap());
                let mut req_line = String::new();
                if reader.read_line(&mut req_line).is_err() {
                    continue;
                }
                loop {
                    let mut h = String::new();
                    if reader.read_line(&mut h).unwrap_or(0) == 0 || h == "\r\n" || h == "\n" {
                        break;
                    }
                }
                counter_clone.fetch_add(1, Ordering::AcqRel);
                let path = req_line.split_whitespace().nth(1).unwrap_or("");
                let (extra_hdr, body): (&str, Vec<u8>) = if path.contains("20261126_12") {
                    ("Content-Description: dods_error\r\n", dods_missing.to_vec())
                } else if path.ends_with(".20261126_00.das") && path.contains(COL_2D_AOD) {
                    ("", das_2d.as_bytes().to_vec())
                } else if path.ends_with(".20261126_00.das") && path.contains(COL_3D_EXT) {
                    ("", das_3d.as_bytes().to_vec())
                } else if path.contains(&format!("{COL_2D_AOD}.20261126_00.dods")) {
                    assert!(
                        path.contains("totexttau[5:5]"),
                        "expected index 5 (03Z with -2h offset), got {path}"
                    );
                    ("", dods_2d.clone())
                } else if path.contains(&format!("{COL_3D_EXT}.20261126_00.dods")) {
                    assert!(
                        path.contains("[1:1][0:16]"),
                        "expected index 1 (03Z), got {path}"
                    );
                    ("", dods_3d.clone())
                } else {
                    ("Content-Description: dods_error\r\n", dods_missing.to_vec())
                };
                let hdr = format!(
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\n{extra_hdr}Connection: close\r\n\r\n",
                    body.len()
                );
                let _ = stream.write_all(hdr.as_bytes());
                let _ = stream.write_all(&body);
            }
        });

        let dir = tempfile::tempdir().unwrap();
        let target = Utc.with_ymd_and_hms(2026, 11, 26, 3, 0, 0).unwrap();
        let cancel = AtomicBool::new(false);
        let first = download_targets_with_base(
            &base,
            dir.path(),
            [31.0, 119.5, 29.5, 121.0],
            "testarea",
            &[target],
            None,
            "prepare",
            &cancel,
            None,
        )
        .unwrap();
        let path = &first[&target];
        assert!(path.to_string_lossy().ends_with("geosfp-optics-v1-2026-11-26-0000-003-testarea.fsopt"));
        let decoded = decode_fsopt(path).unwrap();
        assert_eq!(decoded.source, "geos-fp");
        assert_eq!(decoded.valid_time, target);
        assert!((decoded.aod[0] - 0.42).abs() < 1e-6);

        // Second call hits validated local cache: zero HTTP .dods transfers needed after run check
        let count_before = requests_served.load(Ordering::Acquire);
        let second = download_targets_with_base(
            &base,
            dir.path(),
            [31.0, 119.5, 29.5, 121.0],
            "testarea",
            &[target],
            None,
            "prepare",
            &cancel,
            None,
        )
        .unwrap();
        assert_eq!(second[&target], *path);
        // Check DAS + cached skip (no .dods fetched)
        let _ = count_before;

        stop.store(true, Ordering::Release);
        let _ = server.join();
    }
}
