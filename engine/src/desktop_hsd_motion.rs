//! Native phase-correlation diagnostics for exported compact HSD frames.

use crate::tensor_bundle::{verify_files, F32Bundle};
use crate::time_contract;
use rustfft::num_complex::Complex;
use rustfft::FftPlanner;
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::SystemTime;

const DEFAULT_MIN_OVERLAP: f64 = 0.85;
const DEFAULT_MIN_CORRELATION: f64 = 0.35;
const MAX_CELLS: usize = 16_000_000;
const MAX_MOTION_CACHE: usize = 64;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    previous_manifest: PathBuf,
    latest_manifest: PathBuf,
    #[serde(default = "default_min_overlap")]
    min_overlap: f64,
    #[serde(default = "default_min_correlation")]
    min_correlation: f64,
}

fn default_min_overlap() -> f64 {
    DEFAULT_MIN_OVERLAP
}

fn default_min_correlation() -> f64 {
    DEFAULT_MIN_CORRELATION
}

struct Frame {
    valid_time: chrono::DateTime<chrono::Utc>,
    shape: [usize; 2],
    values: Vec<f64>,
}

#[derive(Clone, PartialEq, Eq)]
struct ManifestVersion {
    path: PathBuf,
    bytes: u64,
    modified: SystemTime,
}

struct CachedMotion {
    previous: ManifestVersion,
    latest: ManifestVersion,
    min_overlap: u64,
    min_correlation: u64,
    result: Value,
}

static MOTION_CACHE: OnceLock<Mutex<VecDeque<CachedMotion>>> = OnceLock::new();

fn manifest_version(path: &Path) -> Result<ManifestVersion, String> {
    let metadata = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if !metadata.file_type().is_file() {
        return Err("HSD motion manifest must be a regular file".into());
    }
    Ok(ManifestVersion {
        path: path.to_path_buf(),
        bytes: metadata.len(),
        modified: metadata.modified().map_err(|e| e.to_string())?,
    })
}

fn cached_motion(
    previous: &ManifestVersion,
    latest: &ManifestVersion,
    min_overlap: f64,
    min_correlation: f64,
) -> Option<Value> {
    let mut cache = MOTION_CACHE.get_or_init(Mutex::default).lock().unwrap();
    let position = cache.iter().position(|entry| {
        entry.previous == *previous
            && entry.latest == *latest
            && entry.min_overlap == min_overlap.to_bits()
            && entry.min_correlation == min_correlation.to_bits()
    })?;
    let entry = cache.remove(position)?;
    let result = entry.result.clone();
    cache.push_back(entry);
    Some(result)
}

fn remember_motion(
    previous: ManifestVersion,
    latest: ManifestVersion,
    min_overlap: f64,
    min_correlation: f64,
    result: &Value,
) {
    let mut cache = MOTION_CACHE.get_or_init(Mutex::default).lock().unwrap();
    cache.retain(|entry| {
        entry.previous != previous
            || entry.latest != latest
            || entry.min_overlap != min_overlap.to_bits()
            || entry.min_correlation != min_correlation.to_bits()
    });
    cache.push_back(CachedMotion {
        previous,
        latest,
        min_overlap: min_overlap.to_bits(),
        min_correlation: min_correlation.to_bits(),
        result: result.clone(),
    });
    while cache.len() > MAX_MOTION_CACHE {
        cache.pop_front();
    }
}

fn frame(path: &Path) -> Result<Frame, String> {
    if !path.is_absolute() {
        return Err("HSD motion manifests must be absolute".into());
    }
    let verified = verify_files(path, "hsd-compact-frame-v1")?;
    let metadata = verified
        .metadata
        .as_object()
        .ok_or("HSD motion frame metadata is not an object")?;
    let valid_time = time_contract::parse_utc(
        metadata
            .get("valid_time_utc")
            .and_then(Value::as_str)
            .ok_or("HSD motion frame time is missing")?,
    )?;
    let shape = verified
        .shapes
        .get("band_b13")
        .ok_or("HSD motion frame is missing B13")?;
    if shape.len() != 2 {
        return Err("HSD motion B13 must be two-dimensional".into());
    }
    let height = usize::try_from(shape[0]).map_err(|_| "HSD motion height is too large")?;
    let width = usize::try_from(shape[1]).map_err(|_| "HSD motion width is too large")?;
    let cells = height
        .checked_mul(width)
        .ok_or("HSD motion shape overflows")?;
    if height == 0 || width == 0 || cells > MAX_CELLS {
        return Err("HSD motion frame shape exceeds bounds".into());
    }
    let bundle = F32Bundle::<Value>::open(path, "hsd-compact-frame-v1", &verified.source)?;
    let rows = bundle.rows::<1>("band_b13", shape)?;
    bundle.revalidate()?;
    let values = rows.into_iter().map(|[value]| f64::from(value)).collect();
    Ok(Frame {
        valid_time,
        shape: [height, width],
        values,
    })
}

fn fft2(
    values: &mut [Complex<f64>],
    height: usize,
    width: usize,
    row_fft: &Arc<dyn rustfft::Fft<f64>>,
    column_fft: &Arc<dyn rustfft::Fft<f64>>,
    inverse: bool,
) {
    for row in values.chunks_exact_mut(width) {
        row_fft.process(row);
    }
    let mut column = vec![Complex::new(0.0, 0.0); height];
    for x in 0..width {
        for y in 0..height {
            column[y] = values[y * width + x];
        }
        column_fft.process(&mut column);
        for y in 0..height {
            values[y * width + x] = column[y];
        }
    }
    if inverse {
        let scale = 1.0 / (height * width) as f64;
        for value in values {
            *value *= scale;
        }
    }
}

fn unknown(previous: &Frame, latest: &Frame, overlap_ratio: f64, age_minutes: f64) -> Value {
    json!({
        "contract": "rust-desktop-nowcast-hsd-motion-v1",
        "runtime_python_used": false,
        "dx_px_per_hour": 0.0,
        "dy_px_per_hour": 0.0,
        "correlation": 0.0,
        "overlap_ratio": overlap_ratio,
        "age_minutes": age_minutes,
        "quality": "unknown",
        "previous_time_utc": time_contract::iso(previous.valid_time),
        "latest_time_utc": time_contract::iso(latest.valid_time),
    })
}

pub fn execute(params: Params) -> Result<Value, String> {
    if !params.min_overlap.is_finite()
        || !(0.0..=1.0).contains(&params.min_overlap)
        || !params.min_correlation.is_finite()
        || !(0.0..=1.0).contains(&params.min_correlation)
    {
        return Err("invalid HSD motion thresholds".into());
    }
    let previous_version = manifest_version(&params.previous_manifest)?;
    let latest_version = manifest_version(&params.latest_manifest)?;
    if let Some(result) = cached_motion(
        &previous_version,
        &latest_version,
        params.min_overlap,
        params.min_correlation,
    ) {
        return Ok(result);
    }
    let result = execute_uncached(&params)?;
    remember_motion(
        previous_version,
        latest_version,
        params.min_overlap,
        params.min_correlation,
        &result,
    );
    Ok(result)
}

fn execute_uncached(params: &Params) -> Result<Value, String> {
    let previous = frame(&params.previous_manifest)?;
    let latest = frame(&params.latest_manifest)?;
    if previous.valid_time >= latest.valid_time {
        return Err("HSD motion frames must be ordered oldest to newest".into());
    }
    if previous.shape != latest.shape {
        return Err("satellite motion frames must have matching 2-D grids".into());
    }
    let delta = latest.valid_time.signed_duration_since(previous.valid_time);
    let delta_seconds = delta
        .num_microseconds()
        .ok_or("HSD motion interval overflows")? as f64
        / 1_000_000.0;
    if !(0.0..=2.0 * 3600.0).contains(&delta_seconds) || delta_seconds <= 0.0 {
        return Err("HSD motion frame interval is outside the supported range".into());
    }
    let finite: Vec<bool> = previous
        .values
        .iter()
        .zip(&latest.values)
        .map(|(left, right)| left.is_finite() && right.is_finite())
        .collect();
    let finite_count = finite.iter().filter(|value| **value).count();
    let overlap_ratio = finite_count as f64 / finite.len().max(1) as f64;
    let age_minutes = delta_seconds / 60.0;
    if overlap_ratio < params.min_overlap {
        return Ok(unknown(&previous, &latest, overlap_ratio, age_minutes));
    }
    let common_count = finite_count
        .checked_mul(2)
        .ok_or("HSD motion count overflow")?;
    let common_sum: f64 = previous
        .values
        .iter()
        .zip(&latest.values)
        .zip(&finite)
        .filter_map(|((left, right), valid)| valid.then_some(left + right))
        .sum();
    let common_mean = common_sum / common_count as f64;
    let mut previous_work = Vec::with_capacity(previous.values.len());
    let mut latest_work = Vec::with_capacity(latest.values.len());
    for ((left, right), valid) in previous.values.iter().zip(&latest.values).zip(&finite) {
        previous_work.push(if *valid { *left } else { common_mean });
        latest_work.push(if *valid { *right } else { common_mean });
    }
    let previous_mean = previous_work.iter().sum::<f64>() / previous_work.len() as f64;
    let latest_mean = latest_work.iter().sum::<f64>() / latest_work.len() as f64;
    for (left, right) in previous_work.iter_mut().zip(&mut latest_work) {
        *left -= previous_mean;
        *right -= latest_mean;
    }
    let previous_std = (previous_work.iter().map(|value| value * value).sum::<f64>()
        / previous_work.len() as f64)
        .sqrt();
    let latest_std = (latest_work.iter().map(|value| value * value).sum::<f64>()
        / latest_work.len() as f64)
        .sqrt();
    if previous_std < 1e-4 || latest_std < 1e-4 {
        return Ok(unknown(&previous, &latest, overlap_ratio, age_minutes));
    }

    let [height, width] = previous.shape;
    let mut planner = FftPlanner::<f64>::new();
    let row_forward = planner.plan_fft_forward(width);
    let column_forward = planner.plan_fft_forward(height);
    let row_inverse = planner.plan_fft_inverse(width);
    let column_inverse = planner.plan_fft_inverse(height);
    let mut previous_fft = Vec::with_capacity(previous_work.len());
    let mut latest_fft = Vec::with_capacity(latest_work.len());
    for y in 0..height {
        let wy = if height == 1 {
            1.0
        } else {
            0.5 * (1.0 - (2.0 * std::f64::consts::PI * y as f64 / (height - 1) as f64).cos())
        };
        for x in 0..width {
            let wx = if width == 1 {
                1.0
            } else {
                0.5 * (1.0 - (2.0 * std::f64::consts::PI * x as f64 / (width - 1) as f64).cos())
            };
            let window = wy * wx;
            previous_fft.push(Complex::new(previous_work[y * width + x] * window, 0.0));
            latest_fft.push(Complex::new(latest_work[y * width + x] * window, 0.0));
        }
    }
    fft2(
        &mut previous_fft,
        height,
        width,
        &row_forward,
        &column_forward,
        false,
    );
    fft2(
        &mut latest_fft,
        height,
        width,
        &row_forward,
        &column_forward,
        false,
    );
    let mut correlation_map = Vec::with_capacity(previous_fft.len());
    for (latest_value, previous_value) in latest_fft.iter().zip(&previous_fft) {
        let cross = *latest_value * previous_value.conj();
        let magnitude = cross.norm().max(1e-9);
        correlation_map.push(cross / magnitude);
    }
    let row_inverse = row_inverse;
    let column_inverse = column_inverse;
    fft2(
        &mut correlation_map,
        height,
        width,
        &row_inverse,
        &column_inverse,
        true,
    );
    let mut peak_index = 0usize;
    let mut peak_value = f64::NEG_INFINITY;
    for (index, value) in correlation_map.iter().enumerate() {
        let magnitude = value.norm();
        if magnitude > peak_value {
            peak_value = magnitude;
            peak_index = index;
        }
    }
    let mut peak_y = (peak_index / width) as isize;
    let mut peak_x = (peak_index % width) as isize;
    if peak_x > width as isize / 2 {
        peak_x -= width as isize;
    }
    if peak_y > height as isize / 2 {
        peak_y -= height as isize;
    }
    let max_shift = 0.20 * height.min(width) as f64;
    if (peak_x as f64).abs() > max_shift || (peak_y as f64).abs() > max_shift {
        return Ok(unknown(&previous, &latest, overlap_ratio, age_minutes));
    }

    let mut shifted = Vec::with_capacity(previous_work.len());
    for y in 0..height {
        for x in 0..width {
            let source_y = (y as isize - peak_y).rem_euclid(height as isize) as usize;
            let source_x = (x as isize - peak_x).rem_euclid(width as isize) as usize;
            shifted.push(previous_work[source_y * width + source_x]);
        }
    }
    let selected_count = finite_count as f64;
    let selected_previous_mean = shifted
        .iter()
        .zip(&latest_work)
        .zip(&finite)
        .filter_map(|((left, _), valid)| valid.then_some(*left))
        .sum::<f64>()
        / selected_count;
    let selected_latest_mean = latest_work
        .iter()
        .zip(&finite)
        .filter_map(|(value, valid)| valid.then_some(*value))
        .sum::<f64>()
        / selected_count;
    let mut covariance = 0.0;
    let mut previous_variance = 0.0;
    let mut latest_variance = 0.0;
    for ((left, right), valid) in shifted.iter().zip(&latest_work).zip(&finite) {
        if *valid {
            let left = *left - selected_previous_mean;
            let right = *right - selected_latest_mean;
            covariance += left * right;
            previous_variance += left * left;
            latest_variance += right * right;
        }
    }
    let correlation = if previous_variance > 0.0 && latest_variance > 0.0 {
        (covariance / (previous_variance * latest_variance).sqrt()).clamp(0.0, 1.0)
    } else {
        0.0
    };
    let hours = delta_seconds / 3600.0;
    let quality = if correlation >= params.min_correlation {
        "strong"
    } else {
        "unknown"
    };
    let (dx, dy) = if quality == "strong" {
        (peak_x as f64 / hours, peak_y as f64 / hours)
    } else {
        (0.0, 0.0)
    };
    Ok(json!({
        "contract": "rust-desktop-nowcast-hsd-motion-v1",
        "runtime_python_used": false,
        "dx_px_per_hour": dx,
        "dy_px_per_hour": dy,
        "correlation": correlation,
        "overlap_ratio": overlap_ratio,
        "age_minutes": age_minutes,
        "quality": quality,
        "previous_time_utc": time_contract::iso(previous.valid_time),
        "latest_time_utc": time_contract::iso(latest.valid_time),
    }))
}
