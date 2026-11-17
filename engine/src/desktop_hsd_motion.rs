//! Native cross-correlation motion diagnostics for exported compact HSD frames.
//!
//! Cloud displacement is estimated from the energy-weighted cross-correlation
//! of the Hann-windowed B13 pair (rather than a fully whitened phase
//! correlation, which amplifies quantization/sensor noise on smooth fields).
//! The integer peak is refined to sub-pixel precision by parabolic
//! interpolation of the periodic correlation magnitudes, and the quality
//! correlation is measured at that refined shift with wrapped bilinear
//! sampling, so slow cloud drift is no longer quantized to whole pixels per
//! frame pair.
//!
//! When older frames are supplied (`earlier_manifests`), each consecutive pair
//! is estimated independently and the per-pair velocities are combined into a
//! correlation- and recency-weighted mean anchored to the latest pair. Earlier
//! pairs that contradict the latest velocity are rejected as outliers, and a
//! latest pair contradicted by a majority of earlier pairs is gated to
//! `unknown`. With only two frames the result is identical to the single-pair
//! estimate. The output contract and `strong`/`unknown` gate are unchanged.

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
const MAX_FRAMES: usize = 4;
const VELOCITY_CONSISTENCY_FLOOR: f64 = 3.0;
const VELOCITY_CONSISTENCY_RATIO: f64 = 0.6;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    previous_manifest: PathBuf,
    latest_manifest: PathBuf,
    #[serde(default)]
    earlier_manifests: Vec<PathBuf>,
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
    earlier: Vec<ManifestVersion>,
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
    earlier: &[ManifestVersion],
    min_overlap: f64,
    min_correlation: f64,
) -> Option<Value> {
    let mut cache = MOTION_CACHE.get_or_init(Mutex::default).lock().unwrap();
    let position = cache.iter().position(|entry| {
        entry.previous == *previous
            && entry.latest == *latest
            && entry.earlier == earlier
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
    earlier: Vec<ManifestVersion>,
    min_overlap: f64,
    min_correlation: f64,
    result: &Value,
) {
    let mut cache = MOTION_CACHE.get_or_init(Mutex::default).lock().unwrap();
    cache.retain(|entry| {
        entry.previous != previous
            || entry.latest != latest
            || entry.earlier != earlier
            || entry.min_overlap != min_overlap.to_bits()
            || entry.min_correlation != min_correlation.to_bits()
    });
    cache.push_back(CachedMotion {
        previous,
        latest,
        earlier,
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

fn subpixel_offset(
    map: &[Complex<f64>],
    width: usize,
    height: usize,
    peak_y: usize,
    peak_x: usize,
) -> (f64, f64) {
    let magnitude = |y: usize, x: usize| map[y * width + x].norm();
    let axis = |left: f64, center: f64, right: f64| {
        let denominator = left - 2.0 * center + right;
        if !denominator.is_finite() || denominator >= 0.0 {
            return 0.0;
        }
        (0.5 * (left - right) / denominator).clamp(-0.5, 0.5)
    };
    let center = magnitude(peak_y, peak_x);
    let offset_x = axis(
        magnitude(peak_y, (peak_x + width - 1) % width),
        center,
        magnitude(peak_y, (peak_x + 1) % width),
    );
    let offset_y = axis(
        magnitude((peak_y + height - 1) % height, peak_x),
        center,
        magnitude((peak_y + 1) % height, peak_x),
    );
    (offset_x, offset_y)
}

fn shifted_bilinear(values: &[f64], shape: [usize; 2], shift_x: f64, shift_y: f64) -> Vec<f64> {
    let [height, width] = shape;
    let mut result = Vec::with_capacity(values.len());
    for y in 0..height {
        let source_y = y as f64 - shift_y;
        let y0 = source_y.floor();
        let wy = source_y - y0;
        let row0 = (y0 as isize).rem_euclid(height as isize) as usize;
        let row1 = (y0 as isize + 1).rem_euclid(height as isize) as usize;
        for x in 0..width {
            let source_x = x as f64 - shift_x;
            let x0 = source_x.floor();
            let wx = source_x - x0;
            let col0 = (x0 as isize).rem_euclid(width as isize) as usize;
            let col1 = (x0 as isize + 1).rem_euclid(width as isize) as usize;
            result.push(
                (1.0 - wy) * (1.0 - wx) * values[row0 * width + col0]
                    + (1.0 - wy) * wx * values[row0 * width + col1]
                    + wy * (1.0 - wx) * values[row1 * width + col0]
                    + wy * wx * values[row1 * width + col1],
            );
        }
    }
    result
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
    if params.earlier_manifests.len() + 2 > MAX_FRAMES {
        return Err("HSD motion frame count exceeds the supported bound".into());
    }
    let previous_version = manifest_version(&params.previous_manifest)?;
    let latest_version = manifest_version(&params.latest_manifest)?;
    let earlier_versions = params
        .earlier_manifests
        .iter()
        .map(|path| manifest_version(path))
        .collect::<Result<Vec<_>, _>>()?;
    if let Some(result) = cached_motion(
        &previous_version,
        &latest_version,
        &earlier_versions,
        params.min_overlap,
        params.min_correlation,
    ) {
        return Ok(result);
    }
    let result = execute_uncached(&params)?;
    remember_motion(
        previous_version,
        latest_version,
        earlier_versions,
        params.min_overlap,
        params.min_correlation,
        &result,
    );
    Ok(result)
}

#[derive(Clone, Copy)]
struct PairMotion {
    shift_x: f64,
    shift_y: f64,
    correlation: f64,
    overlap_ratio: f64,
    age_minutes: f64,
    strong: bool,
}

fn pair_motion(
    previous: &Frame,
    latest: &Frame,
    min_overlap: f64,
    min_correlation: f64,
) -> Result<PairMotion, String> {
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
    let gated = PairMotion {
        shift_x: 0.0,
        shift_y: 0.0,
        correlation: 0.0,
        overlap_ratio,
        age_minutes,
        strong: false,
    };
    if overlap_ratio < min_overlap {
        return Ok(gated);
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
        return Ok(gated);
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
        correlation_map.push(*latest_value * previous_value.conj());
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
    let peak_y = peak_index / width;
    let peak_x = peak_index % width;
    let (offset_x, offset_y) = subpixel_offset(&correlation_map, width, height, peak_y, peak_x);
    let mut shift_x = peak_x as f64 + offset_x;
    let mut shift_y = peak_y as f64 + offset_y;
    if shift_x > width as f64 / 2.0 {
        shift_x -= width as f64;
    }
    if shift_y > height as f64 / 2.0 {
        shift_y -= height as f64;
    }
    let max_shift = 0.20 * height.min(width) as f64;
    if shift_x.abs() > max_shift || shift_y.abs() > max_shift {
        return Ok(gated);
    }

    let shifted = shifted_bilinear(&previous_work, previous.shape, shift_x, shift_y);
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
    Ok(PairMotion {
        shift_x,
        shift_y,
        correlation,
        overlap_ratio,
        age_minutes,
        strong: correlation >= min_correlation,
    })
}

fn pair_velocity(pair: &PairMotion) -> (f64, f64) {
    let hours = pair.age_minutes / 60.0;
    (pair.shift_x / hours, pair.shift_y / hours)
}

/// Combine per-pair velocities into a robust estimate anchored to the latest
/// pair. Earlier pairs are weighted by correlation and halved per step back in
/// time, so the result stays close to the most recent observation while still
/// averaging out sensor noise. Pairs that contradict the latest velocity are
/// dropped as outliers; a latest pair contradicted by a majority of earlier
/// pairs yields `None` (gated to `unknown`).
fn refine_velocity(pairs: &[PairMotion]) -> Option<(f64, f64)> {
    let anchor = pairs.last()?;
    let (anchor_x, anchor_y) = pair_velocity(anchor);
    let anchor_speed = (anchor_x * anchor_x + anchor_y * anchor_y).sqrt();
    let tolerance = VELOCITY_CONSISTENCY_FLOOR.max(VELOCITY_CONSISTENCY_RATIO * anchor_speed);
    let last = pairs.len() - 1;
    let mut weight_sum = anchor.correlation;
    let mut sum_x = anchor.correlation * anchor_x;
    let mut sum_y = anchor.correlation * anchor_y;
    let mut consistent = 0usize;
    let mut inconsistent = 0usize;
    for (index, pair) in pairs.iter().enumerate().take(last) {
        if !pair.strong {
            continue;
        }
        let (vx, vy) = pair_velocity(pair);
        let deviation = ((vx - anchor_x).powi(2) + (vy - anchor_y).powi(2)).sqrt();
        if deviation > tolerance {
            inconsistent += 1;
            continue;
        }
        consistent += 1;
        let weight = pair.correlation * 2f64.powi(index as i32 - last as i32);
        weight_sum += weight;
        sum_x += weight * vx;
        sum_y += weight * vy;
    }
    if inconsistent >= 2 && inconsistent > consistent {
        return None;
    }
    Some((sum_x / weight_sum, sum_y / weight_sum))
}

const FIELD_TARGET_BLOCK: usize = 96;
const FIELD_MAX_GRID: usize = 6;
const FIELD_MIN_BLOCK: usize = 12;

fn field_grid_dim(size: usize) -> usize {
    size.div_ceil(FIELD_TARGET_BLOCK).clamp(1, FIELD_MAX_GRID)
}

/// Local sub-pixel shift for one block, estimated as a residual around the
/// global displacement. The previous block is first aligned by the global
/// shift, then cross-correlated with the latest block; the residual peak is
/// refined to sub-pixel precision. Estimating the residual (rather than the
/// absolute shift) keeps uniform motion unbiased, because the Hann-windowed
/// correlation no longer has to locate a large peak away from zero. Returns
/// `None` when the block is too flat, the residual exceeds the block's motion
/// bound, or the match correlation is below the gate.
fn block_shift(
    previous: &[f64],
    latest: &[f64],
    height: usize,
    width: usize,
    min_correlation: f64,
    global_shift_x: f64,
    global_shift_y: f64,
) -> Option<(f64, f64, f64)> {
    let cells = height * width;
    let finite: Vec<bool> = previous
        .iter()
        .zip(latest)
        .map(|(left, right)| left.is_finite() && right.is_finite())
        .collect();
    let finite_count = finite.iter().filter(|value| **value).count();
    if finite_count * 10 < cells * 8 {
        return None;
    }
    let mean: f64 = previous
        .iter()
        .zip(latest)
        .zip(&finite)
        .filter_map(|((left, right), valid)| valid.then_some(left + right))
        .sum::<f64>()
        / (2 * finite_count) as f64;
    let mut previous_work = Vec::with_capacity(cells);
    let mut latest_work = Vec::with_capacity(cells);
    for ((left, right), valid) in previous.iter().zip(latest).zip(&finite) {
        previous_work.push(if *valid { *left } else { mean });
        latest_work.push(if *valid { *right } else { mean });
    }
    let previous_mean = previous_work.iter().sum::<f64>() / cells as f64;
    let latest_mean = latest_work.iter().sum::<f64>() / cells as f64;
    for (left, right) in previous_work.iter_mut().zip(&mut latest_work) {
        *left -= previous_mean;
        *right -= latest_mean;
    }
    let previous_std = (previous_work.iter().map(|v| v * v).sum::<f64>() / cells as f64).sqrt();
    let latest_std = (latest_work.iter().map(|v| v * v).sum::<f64>() / cells as f64).sqrt();
    if previous_std < 1e-4 || latest_std < 1e-4 {
        return None;
    }
    let aligned = shifted_bilinear(
        &previous_work,
        [height, width],
        global_shift_x,
        global_shift_y,
    );
    let mut planner = FftPlanner::<f64>::new();
    let row_forward = planner.plan_fft_forward(width);
    let column_forward = planner.plan_fft_forward(height);
    let row_inverse = planner.plan_fft_inverse(width);
    let column_inverse = planner.plan_fft_inverse(height);
    let mut aligned_fft = Vec::with_capacity(cells);
    let mut latest_fft = Vec::with_capacity(cells);
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
            aligned_fft.push(Complex::new(aligned[y * width + x] * window, 0.0));
            latest_fft.push(Complex::new(latest_work[y * width + x] * window, 0.0));
        }
    }
    fft2(
        &mut aligned_fft,
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
    let mut correlation_map = Vec::with_capacity(cells);
    for (latest_value, aligned_value) in latest_fft.iter().zip(&aligned_fft) {
        correlation_map.push(*latest_value * aligned_value.conj());
    }
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
    let peak_y = peak_index / width;
    let peak_x = peak_index % width;
    let (offset_x, offset_y) = subpixel_offset(&correlation_map, width, height, peak_y, peak_x);
    let mut residual_x = peak_x as f64 + offset_x;
    let mut residual_y = peak_y as f64 + offset_y;
    if residual_x > width as f64 / 2.0 {
        residual_x -= width as f64;
    }
    if residual_y > height as f64 / 2.0 {
        residual_y -= height as f64;
    }
    let max_shift = 0.20 * height.min(width) as f64;
    if residual_x.abs() > max_shift || residual_y.abs() > max_shift {
        return None;
    }
    let shifted = shifted_bilinear(&aligned, [height, width], residual_x, residual_y);
    let selected_count = finite_count as f64;
    let previous_mean = shifted
        .iter()
        .zip(&finite)
        .filter_map(|(value, valid)| valid.then_some(*value))
        .sum::<f64>()
        / selected_count;
    let latest_mean = latest_work
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
            let left = *left - previous_mean;
            let right = *right - latest_mean;
            covariance += left * right;
            previous_variance += left * left;
            latest_variance += right * right;
        }
    }
    if previous_variance <= 0.0 || latest_variance <= 0.0 {
        return None;
    }
    let correlation = (covariance / (previous_variance * latest_variance).sqrt()).clamp(0.0, 1.0);
    if correlation < min_correlation {
        return None;
    }
    Some((
        global_shift_x + residual_x,
        global_shift_y + residual_y,
        correlation,
    ))
}

/// Estimate a coarse spatially-varying motion field from the latest frame pair.
/// Each block is correlated independently; blocks that fail the local gate fall
/// back to the global velocity with zero confidence so advection can blend
/// smoothly back to the rigid estimate where the local match is unreliable.
fn estimate_block_field(
    previous: &Frame,
    latest: &Frame,
    hours: f64,
    global_dx: f64,
    global_dy: f64,
    min_correlation: f64,
) -> Value {
    let [height, width] = previous.shape;
    let grid_y = field_grid_dim(height);
    let grid_x = field_grid_dim(width);
    let global_shift_x = global_dx * hours;
    let global_shift_y = global_dy * hours;
    let mut dx = Vec::with_capacity(grid_y * grid_x);
    let mut dy = Vec::with_capacity(grid_y * grid_x);
    let mut confidence = Vec::with_capacity(grid_y * grid_x);
    for by in 0..grid_y {
        let row0 = by * height / grid_y;
        let row1 = (by + 1) * height / grid_y;
        let block_height = row1 - row0;
        for bx in 0..grid_x {
            let col0 = bx * width / grid_x;
            let col1 = (bx + 1) * width / grid_x;
            let block_width = col1 - col0;
            let mut local = None;
            if block_height >= FIELD_MIN_BLOCK && block_width >= FIELD_MIN_BLOCK {
                let mut previous_block = Vec::with_capacity(block_height * block_width);
                let mut latest_block = Vec::with_capacity(block_height * block_width);
                for y in row0..row1 {
                    for x in col0..col1 {
                        previous_block.push(previous.values[y * width + x]);
                        latest_block.push(latest.values[y * width + x]);
                    }
                }
                local = block_shift(
                    &previous_block,
                    &latest_block,
                    block_height,
                    block_width,
                    min_correlation,
                    global_shift_x,
                    global_shift_y,
                );
            }
            match local {
                Some((shift_x, shift_y, correlation)) => {
                    dx.push((shift_x / hours) as f32);
                    dy.push((shift_y / hours) as f32);
                    confidence.push(correlation as f32);
                }
                None => {
                    dx.push(global_dx as f32);
                    dy.push(global_dy as f32);
                    confidence.push(0.0);
                }
            }
        }
    }
    json!({
        "grid": [grid_y, grid_x],
        "dx_px_per_hour": dx,
        "dy_px_per_hour": dy,
        "confidence": confidence,
    })
}

fn execute_uncached(params: &Params) -> Result<Value, String> {
    let previous = frame(&params.previous_manifest)?;
    let latest = frame(&params.latest_manifest)?;
    let mut frames = Vec::with_capacity(params.earlier_manifests.len() + 2);
    for path in &params.earlier_manifests {
        frames.push(frame(path)?);
    }
    frames.push(previous);
    frames.push(latest);
    frames.sort_by_key(|frame| frame.valid_time);
    for window in frames.windows(2) {
        if window[0].valid_time >= window[1].valid_time {
            return Err("HSD motion frames must be ordered oldest to newest".into());
        }
    }
    let mut pairs = Vec::with_capacity(frames.len() - 1);
    for window in frames.windows(2) {
        pairs.push(pair_motion(
            &window[0],
            &window[1],
            params.min_overlap,
            params.min_correlation,
        )?);
    }
    let latest_pair = pairs.last().unwrap();
    let previous = &frames[frames.len() - 2];
    let latest = frames.last().unwrap();
    if !latest_pair.strong {
        return Ok(unknown(
            previous,
            latest,
            latest_pair.overlap_ratio,
            latest_pair.age_minutes,
        ));
    }
    let Some((dx, dy)) = refine_velocity(&pairs) else {
        return Ok(unknown(
            previous,
            latest,
            latest_pair.overlap_ratio,
            latest_pair.age_minutes,
        ));
    };
    let motion_field = estimate_block_field(
        previous,
        latest,
        latest_pair.age_minutes / 60.0,
        dx,
        dy,
        params.min_correlation,
    );
    Ok(json!({
        "contract": "rust-desktop-nowcast-hsd-motion-v1",
        "runtime_python_used": false,
        "dx_px_per_hour": dx,
        "dy_px_per_hour": dy,
        "correlation": latest_pair.correlation,
        "overlap_ratio": latest_pair.overlap_ratio,
        "age_minutes": latest_pair.age_minutes,
        "quality": "strong",
        "motion_field": motion_field,
        "previous_time_utc": time_contract::iso(previous.valid_time),
        "latest_time_utc": time_contract::iso(latest.valid_time),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tensor_bundle::{source_identity, BundleWriter};
    use std::sync::atomic::{AtomicU64, Ordering};

    static SEQUENCE: AtomicU64 = AtomicU64::new(0);
    const SHAPE: [usize; 2] = [256, 256];
    const HOURS: f64 = 1.0 / 6.0;

    fn directory() -> PathBuf {
        std::env::temp_dir().join(format!(
            "firesky-motion-{}-{}",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ))
    }

    fn cloud_field() -> Vec<f64> {
        let [height, width] = SHAPE;
        let mut values = Vec::with_capacity(height * width);
        for y in 0..height {
            for x in 0..width {
                let fx = x as f64 / width as f64;
                let fy = y as f64 / height as f64;
                let mut value = 250.0
                    + 12.0 * (2.0 * std::f64::consts::PI * (3.0 * fx + 2.0 * fy)).sin()
                    + 8.0 * (2.0 * std::f64::consts::PI * (5.0 * fx - 4.0 * fy + 0.7)).cos();
                for (cx, cy, sigma, amplitude) in [
                    (0.30, 0.35, 0.08, -35.0),
                    (0.65, 0.60, 0.11, -28.0),
                    (0.55, 0.25, 0.06, 20.0),
                    (0.20, 0.75, 0.09, -18.0),
                ] {
                    let dx = fx - cx;
                    let dy = fy - cy;
                    value += amplitude * (-(dx * dx + dy * dy) / (2.0 * sigma * sigma)).exp();
                }
                values.push(value);
            }
        }
        values
    }

    fn noise_field(seed: u64) -> Vec<f64> {
        let mut state = seed;
        (0..SHAPE[0] * SHAPE[1])
            .map(|_| {
                state = state
                    .wrapping_mul(6364136223846793005)
                    .wrapping_add(1442695040888963407);
                250.0 + 30.0 * ((state >> 33) as f64 / (1u64 << 31) as f64)
            })
            .collect()
    }

    fn write_frame(
        directory: &Path,
        name: &str,
        valid_time_utc: &str,
        values: Vec<f32>,
    ) -> PathBuf {
        let source = directory.join(format!("{name}.source"));
        fs::write(&source, name.as_bytes()).unwrap();
        let mut writer = BundleWriter::create(
            &directory.join(name),
            "hsd-compact-frame-v1",
            source_identity(&source).unwrap(),
            json!({"valid_time_utc": valid_time_utc}),
        )
        .unwrap();
        writer
            .f32("band_b13", &[SHAPE[0] as u64, SHAPE[1] as u64], values)
            .unwrap();
        writer.commit(&source).unwrap()
    }

    fn estimate(previous: Vec<f64>, latest: Vec<f64>) -> (Value, PathBuf) {
        let directory = directory();
        fs::create_dir_all(&directory).unwrap();
        let previous = write_frame(
            &directory,
            "previous",
            "2026-11-12T00:00:00+00:00",
            previous.into_iter().map(|value| value as f32).collect(),
        );
        let latest = write_frame(
            &directory,
            "latest",
            "2026-11-12T00:10:00+00:00",
            latest.into_iter().map(|value| value as f32).collect(),
        );
        let result = execute(Params {
            previous_manifest: previous,
            latest_manifest: latest,
            earlier_manifests: Vec::new(),
            min_overlap: DEFAULT_MIN_OVERLAP,
            min_correlation: DEFAULT_MIN_CORRELATION,
        })
        .unwrap();
        (result, directory)
    }

    fn shifted_field(shift_x: f64, shift_y: f64) -> (Value, PathBuf) {
        let field = cloud_field();
        let latest = shifted_bilinear(&field, SHAPE, shift_x, shift_y);
        estimate(field, latest)
    }

    fn shift_estimate(result: &Value) -> (f64, f64) {
        (
            result["dx_px_per_hour"].as_f64().unwrap() * HOURS,
            result["dy_px_per_hour"].as_f64().unwrap() * HOURS,
        )
    }

    #[test]
    fn integer_shift_is_recovered() {
        let (result, directory) = shifted_field(3.0, -2.0);
        let (x, y) = shift_estimate(&result);
        assert!((x - 3.0).abs() < 0.2, "dx shift {x}");
        assert!((y + 2.0).abs() < 0.2, "dy shift {y}");
        assert_eq!(result["quality"], "strong");
        assert!(result["correlation"].as_f64().unwrap() > 0.98);
        assert_eq!(result["contract"], "rust-desktop-nowcast-hsd-motion-v1");
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn subpixel_shift_beats_integer_quantization() {
        let (result, directory) = shifted_field(2.35, -1.6);
        let (x, y) = shift_estimate(&result);
        assert!((x - 2.35).abs() < 0.25, "dx shift {x}");
        assert!((y + 1.6).abs() < 0.25, "dy shift {y}");
        assert_eq!(result["quality"], "strong");
        assert!(result["correlation"].as_f64().unwrap() > 0.9);
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn slow_drift_is_strong_and_accurate() {
        let (result, directory) = shifted_field(0.45, 0.30);
        let (x, y) = shift_estimate(&result);
        assert!((x - 0.45).abs() < 0.25, "dx shift {x}");
        assert!((y - 0.30).abs() < 0.25, "dy shift {y}");
        assert_eq!(result["quality"], "strong");
        assert!(result["correlation"].as_f64().unwrap() > 0.9);
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn wrapped_shift_is_refined_across_the_edge() {
        let (result, directory) = shifted_field(SHAPE[1] as f64 - 3.5, -2.0);
        let (x, y) = shift_estimate(&result);
        assert!((x + 3.5).abs() < 0.25, "dx shift {x}");
        assert!((y + 2.0).abs() < 0.25, "dy shift {y}");
        assert_eq!(result["quality"], "strong");
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn identical_frames_have_zero_motion() {
        let field = cloud_field();
        let (result, directory) = estimate(field.clone(), field);
        let (x, y) = shift_estimate(&result);
        assert!(x.abs() < 1e-9 && y.abs() < 1e-9);
        assert_eq!(result["quality"], "strong");
        assert!(result["correlation"].as_f64().unwrap() > 0.999);
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn uncorrelated_frames_are_unknown() {
        let (result, directory) = estimate(noise_field(7), noise_field(91));
        assert_eq!(result["quality"], "unknown");
        assert_eq!(result["dx_px_per_hour"], json!(0.0));
        assert_eq!(result["dy_px_per_hour"], json!(0.0));
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn subpixel_shift_survives_sensor_noise() {
        let field = cloud_field();
        let shifted = shifted_bilinear(&field, SHAPE, 1.7, -2.4);
        let mut state = 12345u64;
        let mut noise = || {
            state = state
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            ((state >> 33) as f64 / (1u64 << 31) as f64 - 0.5) * 1.2
        };
        let previous: Vec<f64> = field.iter().map(|value| value + noise()).collect();
        let latest: Vec<f64> = shifted.iter().map(|value| value + noise()).collect();
        let (result, directory) = estimate(previous, latest);
        let (x, y) = shift_estimate(&result);
        assert!((x - 1.7).abs() < 0.3, "dx shift {x}");
        assert!((y + 2.4).abs() < 0.3, "dy shift {y}");
        assert_eq!(result["quality"], "strong");
        fs::remove_dir_all(directory).unwrap();
    }

    fn estimate_sequence(frames: &[(Vec<f64>, &str)]) -> (Value, PathBuf) {
        let directory = directory();
        fs::create_dir_all(&directory).unwrap();
        let mut manifests = Vec::new();
        for (index, (values, time)) in frames.iter().enumerate() {
            manifests.push(write_frame(
                &directory,
                &format!("frame{index}"),
                time,
                values.iter().map(|value| *value as f32).collect(),
            ));
        }
        let count = manifests.len();
        let result = execute(Params {
            previous_manifest: manifests[count - 2].clone(),
            latest_manifest: manifests[count - 1].clone(),
            earlier_manifests: manifests[..count - 2].to_vec(),
            min_overlap: DEFAULT_MIN_OVERLAP,
            min_correlation: DEFAULT_MIN_CORRELATION,
        })
        .unwrap();
        (result, directory)
    }

    fn noisy(values: &[f64], seed: u64, amplitude: f64) -> Vec<f64> {
        let mut state = seed;
        values
            .iter()
            .map(|value| {
                state = state
                    .wrapping_mul(6364136223846793005)
                    .wrapping_add(1442695040888963407);
                value + ((state >> 33) as f64 / (1u64 << 31) as f64 - 0.5) * amplitude
            })
            .collect()
    }

    #[test]
    fn multiframe_constant_velocity_is_consistent() {
        let field = cloud_field();
        let first = shifted_bilinear(&field, SHAPE, 2.0, -1.0);
        let second = shifted_bilinear(&first, SHAPE, 2.0, -1.0);
        let (result, directory) = estimate_sequence(&[
            (field, "2026-11-12T00:00:00+00:00"),
            (first, "2026-11-12T00:10:00+00:00"),
            (second, "2026-11-12T00:20:00+00:00"),
        ]);
        let (x, y) = shift_estimate(&result);
        assert!((x - 2.0).abs() < 0.25, "dx shift {x}");
        assert!((y + 1.0).abs() < 0.25, "dy shift {y}");
        assert_eq!(result["quality"], "strong");
        assert_eq!(result["contract"], "rust-desktop-nowcast-hsd-motion-v1");
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn multiframe_averages_out_a_noisy_latest_frame() {
        let field = cloud_field();
        let first = shifted_bilinear(&field, SHAPE, 2.0, -1.0);
        let second_clean = shifted_bilinear(&first, SHAPE, 2.0, -1.0);
        let second = noisy(&second_clean, 999, 6.0);
        let (single, single_dir) = estimate(first.clone(), second.clone());
        let (multi, multi_dir) = estimate_sequence(&[
            (field, "2026-11-12T00:00:00+00:00"),
            (first, "2026-11-12T00:10:00+00:00"),
            (second, "2026-11-12T00:20:00+00:00"),
        ]);
        let (sx, sy) = shift_estimate(&single);
        let (mx, my) = shift_estimate(&multi);
        let single_error = ((sx - 2.0).powi(2) + (sy + 1.0).powi(2)).sqrt();
        let multi_error = ((mx - 2.0).powi(2) + (my + 1.0).powi(2)).sqrt();
        assert_eq!(multi["quality"], "strong");
        assert!(
            multi_error < single_error,
            "multi {multi_error} should beat single {single_error}"
        );
        assert!(multi_error < 0.3, "multi error {multi_error}");
        fs::remove_dir_all(single_dir).unwrap();
        fs::remove_dir_all(multi_dir).unwrap();
    }

    #[test]
    fn multiframe_rejects_inconsistent_earlier_pair() {
        let field = cloud_field();
        let first = shifted_bilinear(&field, SHAPE, -5.0, 4.0);
        let second = shifted_bilinear(&first, SHAPE, 2.0, -1.0);
        let third = shifted_bilinear(&second, SHAPE, 2.0, -1.0);
        let (result, directory) = estimate_sequence(&[
            (field, "2026-11-12T00:00:00+00:00"),
            (first, "2026-11-12T00:10:00+00:00"),
            (second, "2026-11-12T00:20:00+00:00"),
            (third, "2026-11-12T00:30:00+00:00"),
        ]);
        let (x, y) = shift_estimate(&result);
        assert!((x - 2.0).abs() < 0.25, "dx shift {x}");
        assert!((y + 1.0).abs() < 0.25, "dy shift {y}");
        assert_eq!(result["quality"], "strong");
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn multiframe_gates_spurious_latest_pair() {
        let field = cloud_field();
        let first = shifted_bilinear(&field, SHAPE, 2.0, -1.0);
        let second = shifted_bilinear(&first, SHAPE, 2.0, -1.0);
        let third = shifted_bilinear(&second, SHAPE, -4.0, 3.0);
        let (result, directory) = estimate_sequence(&[
            (field, "2026-11-12T00:00:00+00:00"),
            (first, "2026-11-12T00:10:00+00:00"),
            (second, "2026-11-12T00:20:00+00:00"),
            (third, "2026-11-12T00:30:00+00:00"),
        ]);
        assert_eq!(result["quality"], "unknown");
        assert_eq!(result["dx_px_per_hour"], json!(0.0));
        assert_eq!(result["dy_px_per_hour"], json!(0.0));
        fs::remove_dir_all(directory).unwrap();
    }

    fn sample_wrap(values: &[f64], shape: [usize; 2], sx: f64, sy: f64) -> f64 {
        let [height, width] = shape;
        let x0 = sx.floor();
        let y0 = sy.floor();
        let wx = sx - x0;
        let wy = sy - y0;
        let r0 = (y0 as isize).rem_euclid(height as isize) as usize;
        let r1 = (y0 as isize + 1).rem_euclid(height as isize) as usize;
        let c0 = (x0 as isize).rem_euclid(width as isize) as usize;
        let c1 = (x0 as isize + 1).rem_euclid(width as isize) as usize;
        (1.0 - wy) * (1.0 - wx) * values[r0 * width + c0]
            + (1.0 - wy) * wx * values[r0 * width + c1]
            + wy * (1.0 - wx) * values[r1 * width + c0]
            + wy * wx * values[r1 * width + c1]
    }

    fn sheared_field(dx_left: f64, dx_right: f64, dy: f64) -> Vec<f64> {
        let field = cloud_field();
        let [height, width] = SHAPE;
        let mut result = Vec::with_capacity(height * width);
        for y in 0..height {
            for x in 0..width {
                let t = x as f64 / (width - 1) as f64;
                let shift_x = dx_left + (dx_right - dx_left) * t;
                result.push(sample_wrap(
                    &field,
                    SHAPE,
                    x as f64 - shift_x,
                    y as f64 - dy,
                ));
            }
        }
        result
    }

    #[test]
    fn motion_field_recovers_uniform_shift() {
        let (result, directory) = shifted_field(2.0, -1.0);
        let field = &result["motion_field"];
        let grid = field["grid"].as_array().unwrap();
        let grid_y = grid[0].as_u64().unwrap() as usize;
        let grid_x = grid[1].as_u64().unwrap() as usize;
        let dx = field["dx_px_per_hour"].as_array().unwrap();
        let dy = field["dy_px_per_hour"].as_array().unwrap();
        let confidence = field["confidence"].as_array().unwrap();
        assert_eq!(dx.len(), grid_y * grid_x);
        assert_eq!(dy.len(), grid_y * grid_x);
        assert_eq!(confidence.len(), grid_y * grid_x);
        let mean_dx = dx.iter().map(|v| v.as_f64().unwrap()).sum::<f64>() / dx.len() as f64;
        let mean_dy = dy.iter().map(|v| v.as_f64().unwrap()).sum::<f64>() / dy.len() as f64;
        assert!((mean_dx - 2.0 / HOURS).abs() < 2.0, "mean dx {mean_dx}");
        assert!((mean_dy + 1.0 / HOURS).abs() < 2.0, "mean dy {mean_dy}");
        let confident = confidence
            .iter()
            .filter(|v| v.as_f64().unwrap() > DEFAULT_MIN_CORRELATION)
            .count();
        assert!(confident >= grid_y * grid_x / 2, "confident {confident}");
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn motion_field_captures_spatial_variation() {
        let field = cloud_field();
        let latest = sheared_field(1.0, 5.0, -1.0);
        let (result, directory) = estimate(field, latest);
        assert_eq!(result["quality"], "strong");
        let field = &result["motion_field"];
        let grid_y = field["grid"][0].as_u64().unwrap() as usize;
        let grid_x = field["grid"][1].as_u64().unwrap() as usize;
        let dx = field["dx_px_per_hour"].as_array().unwrap();
        let column_mean = |column: usize| {
            (0..grid_y)
                .map(|row| dx[row * grid_x + column].as_f64().unwrap())
                .sum::<f64>()
                / grid_y as f64
        };
        let left = column_mean(0);
        let right = column_mean(grid_x - 1);
        assert!(
            right > left + 2.0 / HOURS,
            "left {left} should be slower than right {right}"
        );
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn unknown_motion_has_no_field() {
        let (result, directory) = estimate(noise_field(7), noise_field(91));
        assert_eq!(result["quality"], "unknown");
        assert!(result["motion_field"].is_null());
        fs::remove_dir_all(directory).unwrap();
    }
}
