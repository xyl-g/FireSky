//! Native HSD advection over validated compact tensor bundles.
//!
//! The Python provider still owns remote discovery and retrieval policy. This
//! module owns only the deterministic, continuous bilinear projection of the
//! calibrated B07/B13/B15 arrays. Output bundles are source-bound to the input
//! manifest so Python can verify the derived result without re-reading NPZ.

use crate::tensor_bundle::{verify_files, BundleWriter, F32Bundle, SourceIdentity};
use crate::time_contract;
use chrono::{DateTime, Duration, Utc};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};

const CACHE_SCHEMA_VERSION: u64 = 2;
const FRAME_SCHEMA_VERSION: u64 = 1;
const CACHE_ALGORITHM: &str = "ahi-hsd-calibrated-frame-v1";
const PROJECTION_IDENTITY: &str = "ahi-geos9-himawari9-full-disk-5500x5500-v1";
const MAX_CELLS: usize = 16_000_000;
const MAX_HORIZON_HOURS: f64 = 4.0;
const MAX_MOTION_COMPONENT: f64 = 10_000.0;
const MAX_FIELD_GRID: usize = 64;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MotionField {
    grid: [usize; 2],
    dx_px_per_hour: Vec<f64>,
    dy_px_per_hour: Vec<f64>,
    confidence: Vec<f64>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    manifest_path: PathBuf,
    output_dir: PathBuf,
    target_time_utc: String,
    horizon_hours: f64,
    scale: f64,
    applied: bool,
    motion_dx_px_per_hour: f64,
    motion_dy_px_per_hour: f64,
    motion_quality: String,
    reason: String,
    #[serde(default)]
    motion_field: Option<MotionField>,
}

struct Frame {
    manifest_path: PathBuf,
    source: SourceIdentity,
    shape: [usize; 2],
    latitudes: Vec<f32>,
    longitudes: Vec<f32>,
    bands: [Vec<f32>; 3],
}

struct FrameHeader {
    manifest_path: PathBuf,
    manifest_identity: SourceIdentity,
    bundle_source: SourceIdentity,
    metadata: Value,
    valid_time: DateTime<Utc>,
    shape: [usize; 2],
}

fn metadata_string<'a>(metadata: &'a Value, key: &str) -> Result<&'a str, String> {
    metadata
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("HSD advection metadata field {key} is missing"))
}

fn validate_metadata(metadata: &Value) -> Result<DateTime<Utc>, String> {
    let object = metadata
        .as_object()
        .ok_or("HSD advection frame metadata is not an object")?;
    if object.get("cache_schema_version").and_then(Value::as_u64) != Some(CACHE_SCHEMA_VERSION)
        || metadata_string(metadata, "cache_algorithm")? != CACHE_ALGORITHM
        || object.get("frame_schema_version").and_then(Value::as_u64) != Some(FRAME_SCHEMA_VERSION)
        || metadata_string(metadata, "source")?.is_empty()
        || metadata_string(metadata, "projection_identity")? != PROJECTION_IDENTITY
        || object.get("bands") != Some(&json!(["B07", "B13", "B15"]))
        || object.get("units") != Some(&json!({"B07": "K", "B13": "K", "B15": "K"}))
    {
        return Err("HSD advection frame metadata contract is invalid".into());
    }
    let valid_time = time_contract::parse_utc(metadata_string(metadata, "valid_time_utc")?)?;
    let ready_time = time_contract::parse_utc(metadata_string(metadata, "ready_time_utc")?)?;
    if ready_time != valid_time + Duration::minutes(5) {
        return Err("HSD advection frame ready time must be five minutes after valid time".into());
    }
    Ok(valid_time)
}

fn load_header(path: &Path) -> Result<FrameHeader, String> {
    if !path.is_absolute() {
        return Err("HSD advection manifest must be absolute".into());
    }
    let verified = verify_files(path, "hsd-compact-frame-v1")?;
    let valid_time = validate_metadata(&verified.metadata)?;
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
    {
        return Err("HSD advection frame arrays do not match the HSD contract".into());
    }
    let coordinate_shape = verified
        .shapes
        .get("latitudes")
        .ok_or("HSD advection latitudes are missing")?;
    if coordinate_shape.len() != 2
        || verified.shapes.get("longitudes") != Some(coordinate_shape)
        || expected
            .iter()
            .any(|name| verified.shapes.get(*name) != Some(coordinate_shape))
    {
        return Err("HSD advection arrays must share one 2-D shape".into());
    }
    let height =
        usize::try_from(coordinate_shape[0]).map_err(|_| "HSD advection height is too large")?;
    let width =
        usize::try_from(coordinate_shape[1]).map_err(|_| "HSD advection width is too large")?;
    let cells = height
        .checked_mul(width)
        .ok_or("HSD advection shape overflows")?;
    if height == 0 || width == 0 || cells > MAX_CELLS {
        return Err("HSD advection shape exceeds bounds".into());
    }
    Ok(FrameHeader {
        manifest_path: path.to_owned(),
        manifest_identity: verified.manifest,
        bundle_source: verified.source,
        metadata: verified.metadata,
        valid_time,
        shape: [height, width],
    })
}

fn materialize_frame(header: FrameHeader) -> Result<Frame, String> {
    let [height, width] = header.shape;
    let bundle = F32Bundle::<Value>::open(
        &header.manifest_path,
        "hsd-compact-frame-v1",
        &header.bundle_source,
    )?;
    let latitudes = bundle
        .rows::<1>(
            "latitudes",
            &[header.shape[0] as u64, header.shape[1] as u64],
        )?
        .into_iter()
        .map(|[value]| value)
        .collect::<Vec<_>>();
    let longitudes = bundle
        .rows::<1>(
            "longitudes",
            &[header.shape[0] as u64, header.shape[1] as u64],
        )?
        .into_iter()
        .map(|[value]| value)
        .collect::<Vec<_>>();
    let mut bands = Vec::with_capacity(3);
    for name in ["band_b07", "band_b13", "band_b15"] {
        let values = bundle
            .rows::<1>(name, &[header.shape[0] as u64, header.shape[1] as u64])?
            .into_iter()
            .map(|[value]| value)
            .collect::<Vec<_>>();
        bands.push(values);
    }
    bundle.revalidate()?;
    if latitudes.iter().any(|value| !value.is_finite())
        || longitudes.iter().any(|value| !value.is_finite())
        || bands
            .iter()
            .any(|values| values.iter().any(|value| value.is_infinite()))
    {
        return Err("HSD advection arrays contain invalid values".into());
    }
    let bands: [Vec<f32>; 3] = bands
        .try_into()
        .map_err(|_| "HSD advection band contract is invalid")?;
    Ok(Frame {
        manifest_path: header.manifest_path,
        source: header.manifest_identity,
        shape: [height, width],
        latitudes,
        longitudes,
        bands,
    })
}

fn shifted(values: &[f32], shape: [usize; 2], dx: f64, dy: f64) -> Vec<f32> {
    let [height, width] = shape;
    let mut result = vec![f32::NAN; values.len()];
    for y in 0..height {
        for x in 0..width {
            // Match NumPy's float32 coordinate grid: the scalar motion is
            // cast during subtraction before bilinear weights are widened.
            let source_x = f64::from(x as f32 - dx as f32);
            let source_y = f64::from(y as f32 - dy as f32);
            let x0 = source_x.floor() as isize;
            let y0 = source_y.floor() as isize;
            let x1 = x0 + 1;
            let y1 = y0 + 1;
            if x0 < 0 || x1 >= width as isize || y0 < 0 || y1 >= height as isize {
                continue;
            }
            let index = |row: isize, column: isize| row as usize * width + column as usize;
            let v00 = values[index(y0, x0)];
            let v01 = values[index(y0, x1)];
            let v10 = values[index(y1, x0)];
            let v11 = values[index(y1, x1)];
            if !v00.is_finite() || !v01.is_finite() || !v10.is_finite() || !v11.is_finite() {
                continue;
            }
            let wx = source_x - x0 as f64;
            let wy = source_y - y0 as f64;
            result[y * width + x] = ((1.0 - wy) * (1.0 - wx) * f64::from(v00)
                + (1.0 - wy) * wx * f64::from(v01)
                + wy * (1.0 - wx) * f64::from(v10)
                + wy * wx * f64::from(v11)) as f32;
        }
    }
    result
}

fn validate_motion_field(field: &MotionField) -> Result<(), String> {
    let [grid_y, grid_x] = field.grid;
    if grid_y == 0 || grid_x == 0 || grid_y > MAX_FIELD_GRID || grid_x > MAX_FIELD_GRID {
        return Err("HSD advection motion field grid is out of bounds".into());
    }
    let cells = grid_y * grid_x;
    if field.dx_px_per_hour.len() != cells
        || field.dy_px_per_hour.len() != cells
        || field.confidence.len() != cells
    {
        return Err("HSD advection motion field arrays do not match its grid".into());
    }
    if field
        .dx_px_per_hour
        .iter()
        .chain(&field.dy_px_per_hour)
        .any(|value| !value.is_finite() || value.abs() > MAX_MOTION_COMPONENT)
    {
        return Err("HSD advection motion field velocity is invalid".into());
    }
    if field
        .confidence
        .iter()
        .any(|value| !value.is_finite() || !(0.0..=1.0).contains(value))
    {
        return Err("HSD advection motion field confidence is invalid".into());
    }
    Ok(())
}

fn field_fingerprint(field: &MotionField) -> String {
    let mut input = format!("{}x{}", field.grid[0], field.grid[1]);
    for value in field
        .dx_px_per_hour
        .iter()
        .chain(&field.dy_px_per_hour)
        .chain(&field.confidence)
    {
        input.push_str(&format!("|{value:.17e}"));
    }
    Sha256::digest(input.as_bytes())
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

/// Per-pixel displacement from a coarse motion field, bilinearly interpolated
/// over block centers and blended toward the rigid global velocity by local
/// confidence, so unreliable blocks advect exactly like the global estimate.
fn field_displacements(
    shape: [usize; 2],
    horizon_hours: f64,
    global_dx: f64,
    global_dy: f64,
    field: &MotionField,
) -> (Vec<f64>, Vec<f64>) {
    let [height, width] = shape;
    let [grid_y, grid_x] = field.grid;
    let mut disp_x = Vec::with_capacity(height * width);
    let mut disp_y = Vec::with_capacity(height * width);
    for y in 0..height {
        let gy = ((y as f64 + 0.5) * grid_y as f64 / height as f64 - 0.5)
            .clamp(0.0, (grid_y - 1) as f64);
        let y0 = gy.floor() as usize;
        let y1 = (y0 + 1).min(grid_y - 1);
        let wy = gy - y0 as f64;
        for x in 0..width {
            let gx = ((x as f64 + 0.5) * grid_x as f64 / width as f64 - 0.5)
                .clamp(0.0, (grid_x - 1) as f64);
            let x0 = gx.floor() as usize;
            let x1 = (x0 + 1).min(grid_x - 1);
            let wx = gx - x0 as f64;
            let index = |row: usize, column: usize| row * grid_x + column;
            let interp = |values: &[f64]| {
                (1.0 - wy) * (1.0 - wx) * values[index(y0, x0)]
                    + (1.0 - wy) * wx * values[index(y0, x1)]
                    + wy * (1.0 - wx) * values[index(y1, x0)]
                    + wy * wx * values[index(y1, x1)]
            };
            let confidence = interp(&field.confidence).clamp(0.0, 1.0);
            let vx = confidence * interp(&field.dx_px_per_hour) + (1.0 - confidence) * global_dx;
            let vy = confidence * interp(&field.dy_px_per_hour) + (1.0 - confidence) * global_dy;
            disp_x.push(vx * horizon_hours);
            disp_y.push(vy * horizon_hours);
        }
    }
    (disp_x, disp_y)
}

fn shifted_by_displacements(
    values: &[f32],
    shape: [usize; 2],
    disp_x: &[f64],
    disp_y: &[f64],
) -> Vec<f32> {
    let [height, width] = shape;
    let mut result = vec![f32::NAN; values.len()];
    for y in 0..height {
        for x in 0..width {
            let source_x = f64::from(x as f32 - disp_x[y * width + x] as f32);
            let source_y = f64::from(y as f32 - disp_y[y * width + x] as f32);
            let x0 = source_x.floor() as isize;
            let y0 = source_y.floor() as isize;
            let x1 = x0 + 1;
            let y1 = y0 + 1;
            if x0 < 0 || x1 >= width as isize || y0 < 0 || y1 >= height as isize {
                continue;
            }
            let index = |row: isize, column: isize| row as usize * width + column as usize;
            let v00 = values[index(y0, x0)];
            let v01 = values[index(y0, x1)];
            let v10 = values[index(y1, x0)];
            let v11 = values[index(y1, x1)];
            if !v00.is_finite() || !v01.is_finite() || !v10.is_finite() || !v11.is_finite() {
                continue;
            }
            let wx = source_x - x0 as f64;
            let wy = source_y - y0 as f64;
            result[y * width + x] = ((1.0 - wy) * (1.0 - wx) * f64::from(v00)
                + (1.0 - wy) * wx * f64::from(v01)
                + wy * (1.0 - wx) * f64::from(v10)
                + wy * wx * f64::from(v11)) as f32;
        }
    }
    result
}

fn output_metadata(
    metadata: &Value,
    target_time: DateTime<Utc>,
    reason: &str,
    horizon_hours: f64,
) -> Result<Value, String> {
    let source = metadata_string(metadata, "source")?;
    let mut updated = metadata
        .as_object()
        .ok_or("HSD advection frame metadata is not an object")?
        .clone();
    updated.insert(
        "valid_time_utc".into(),
        Value::String(time_contract::iso(target_time)),
    );
    updated.insert(
        "ready_time_utc".into(),
        Value::String(time_contract::iso(target_time + Duration::minutes(5))),
    );
    updated.insert(
        "source".into(),
        Value::String(format!(
            "{source}|advection={reason}|horizon={horizon_hours:.3}h"
        )),
    );
    Ok(Value::Object(updated))
}

fn output_key(
    source: &SourceIdentity,
    valid_time: DateTime<Utc>,
    target_time: DateTime<Utc>,
    params: &Params,
    dx: f64,
    dy: f64,
) -> String {
    let field_part = params
        .motion_field
        .as_ref()
        .map(field_fingerprint)
        .unwrap_or_default();
    let input = format!(
        "{}|{}|{target_time}|{horizon_hours:.17e}|{scale:.17e}|{dx:.17e}|{dy:.17e}|{reason}|{field_part}",
        source.sha256,
        time_contract::iso(valid_time),
        horizon_hours = params.horizon_hours,
        scale = params.scale,
        reason = params.reason,
    );
    Sha256::digest(input.as_bytes())
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn response(manifest_path: PathBuf, metadata: &Value, applied: bool) -> Result<Value, String> {
    Ok(json!({
        "contract": "rust-desktop-nowcast-hsd-advection-v1",
        "runtime_python_used": false,
        "manifest_path": manifest_path,
        "applied": applied,
        "valid_time_utc": metadata_string(metadata, "valid_time_utc")?,
        "source": metadata_string(metadata, "source")?,
        "quality": metadata_string(metadata, "quality")?,
    }))
}

pub fn execute(params: Params) -> Result<Value, String> {
    if !params.output_dir.is_absolute()
        || !fs::symlink_metadata(&params.output_dir)
            .map_err(|e| e.to_string())?
            .file_type()
            .is_dir()
    {
        return Err("HSD advection output directory must be an existing directory".into());
    }
    if !params.horizon_hours.is_finite()
        || !(0.0..=MAX_HORIZON_HOURS).contains(&params.horizon_hours)
        || !params.scale.is_finite()
        || !(0.0..=1.0).contains(&params.scale)
        || !params.motion_dx_px_per_hour.is_finite()
        || !params.motion_dx_px_per_hour.abs().le(&MAX_MOTION_COMPONENT)
        || !params.motion_dy_px_per_hour.is_finite()
        || !params.motion_dy_px_per_hour.abs().le(&MAX_MOTION_COMPONENT)
        || params.reason.is_empty()
        || params.reason.len() > 64
        || !params.reason.is_ascii()
        || !matches!(
            params.motion_quality.as_str(),
            "strong" | "decayed" | "observation" | "unknown" | "diagnostic-only"
        )
    {
        return Err("invalid HSD advection request".into());
    }
    if let Some(field) = &params.motion_field {
        validate_motion_field(field)?;
    }
    let header = load_header(&params.manifest_path)?;
    let target_time = time_contract::parse_utc(&params.target_time_utc)?;
    let expected_horizon = target_time
        .signed_duration_since(header.valid_time)
        .num_microseconds()
        .ok_or("HSD advection target interval overflows")? as f64
        / 3_600_000_000.0;
    if (expected_horizon - params.horizon_hours).abs() > 1e-6 {
        return Err("HSD advection horizon does not match target time".into());
    }
    if !params.applied {
        return response(params.manifest_path, &header.metadata, false);
    }
    if target_time <= header.valid_time
        || params.scale <= 0.0
        || !matches!(params.motion_quality.as_str(), "strong" | "decayed")
    {
        return Err("applied HSD advection has an invalid plan".into());
    }
    let metadata = output_metadata(
        &header.metadata,
        target_time,
        &params.reason,
        params.horizon_hours,
    )?;
    let dx = params.motion_dx_px_per_hour * params.horizon_hours;
    let dy = params.motion_dy_px_per_hour * params.horizon_hours;
    let root = params.output_dir.join(format!(
        "advection-{}",
        output_key(
            &header.manifest_identity,
            header.valid_time,
            target_time,
            &params,
            dx,
            dy,
        )
    ));
    let manifest_path = root.join("manifest.json");
    if fs::symlink_metadata(&manifest_path)
        .ok()
        .is_some_and(|metadata| metadata.file_type().is_file())
    {
        let existing = verify_files(&manifest_path, "hsd-compact-frame-v1")?;
        if existing.source != header.manifest_identity {
            return Err("HSD advection bundle source identity changed".into());
        }
        return response(manifest_path, &metadata, true);
    }
    if fs::symlink_metadata(&root).is_ok() {
        return Err("HSD advection output bundle already exists but is incomplete".into());
    }
    let frame = materialize_frame(header)?;
    let bands: [Vec<f32>; 3] = if let Some(field) = &params.motion_field {
        let (disp_x, disp_y) = field_displacements(
            frame.shape,
            params.horizon_hours,
            params.motion_dx_px_per_hour,
            params.motion_dy_px_per_hour,
            field,
        );
        [
            shifted_by_displacements(&frame.bands[0], frame.shape, &disp_x, &disp_y),
            shifted_by_displacements(&frame.bands[1], frame.shape, &disp_x, &disp_y),
            shifted_by_displacements(&frame.bands[2], frame.shape, &disp_x, &disp_y),
        ]
    } else {
        [
            shifted(&frame.bands[0], frame.shape, dx, dy),
            shifted(&frame.bands[1], frame.shape, dx, dy),
            shifted(&frame.bands[2], frame.shape, dx, dy),
        ]
    };
    let mut writer = BundleWriter::create(
        &root,
        "hsd-compact-frame-v1",
        frame.source.clone(),
        metadata.clone(),
    )?;
    let shape = [frame.shape[0] as u64, frame.shape[1] as u64];
    writer.f32("latitudes", &shape, frame.latitudes)?;
    writer.f32("longitudes", &shape, frame.longitudes)?;
    let [band_b07, band_b13, band_b15] = bands;
    writer.f32("band_b07", &shape, band_b07)?;
    writer.f32("band_b13", &shape, band_b13)?;
    writer.f32("band_b15", &shape, band_b15)?;
    let manifest = writer.commit(&frame.manifest_path)?;
    response(manifest, &metadata, true)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn field(grid: [usize; 2], dx: Vec<f64>, dy: Vec<f64>, confidence: Vec<f64>) -> MotionField {
        MotionField {
            grid,
            dx_px_per_hour: dx,
            dy_px_per_hour: dy,
            confidence,
        }
    }

    #[test]
    fn field_validation_rejects_mismatched_lengths() {
        let bad = field([2, 2], vec![0.0; 3], vec![0.0; 4], vec![1.0; 4]);
        assert!(validate_motion_field(&bad).is_err());
    }

    #[test]
    fn field_validation_rejects_bad_confidence() {
        let bad = field([1, 1], vec![0.0], vec![0.0], vec![1.5]);
        assert!(validate_motion_field(&bad).is_err());
        let good = field([1, 1], vec![0.0], vec![0.0], vec![1.0]);
        assert!(validate_motion_field(&good).is_ok());
    }

    #[test]
    fn zero_confidence_field_matches_global() {
        let shape = [8, 16];
        let f = field([2, 2], vec![99.0; 4], vec![-99.0; 4], vec![0.0; 4]);
        let (dx, dy) = field_displacements(shape, 2.0, 3.0, -2.0, &f);
        assert!(dx.iter().all(|v| (*v - 6.0).abs() < 1e-9));
        assert!(dy.iter().all(|v| (*v + 4.0).abs() < 1e-9));
    }

    #[test]
    fn uniform_full_confidence_field_matches_global() {
        let shape = [8, 16];
        let f = field([2, 2], vec![3.0; 4], vec![-2.0; 4], vec![1.0; 4]);
        let (dx, dy) = field_displacements(shape, 2.0, 3.0, -2.0, &f);
        assert!(dx.iter().all(|v| (*v - 6.0).abs() < 1e-9));
        assert!(dy.iter().all(|v| (*v + 4.0).abs() < 1e-9));
    }

    #[test]
    fn varying_field_interpolates_across_blocks() {
        let shape = [8, 16];
        let f = field([1, 2], vec![0.0, 10.0], vec![0.0, 0.0], vec![1.0, 1.0]);
        let (dx, _dy) = field_displacements(shape, 1.0, 0.0, 0.0, &f);
        assert!(dx[0] < dx[7]);
        assert!(dx[7] < dx[15]);
        assert!((dx[15] - 10.0).abs() < 1e-9);
        assert!(dx[0].abs() < 1e-9);
    }

    #[test]
    fn uniform_displacements_match_global_shift() {
        let shape = [16, 16];
        let values: Vec<f32> = (0..256).map(|i| (i % 17) as f32 * 3.0).collect();
        let (dx, dy) = (2.5_f64, -1.5_f64);
        let disp_x = vec![dx; 256];
        let disp_y = vec![dy; 256];
        let field_shifted = shifted_by_displacements(&values, shape, &disp_x, &disp_y);
        let global_shifted = shifted(&values, shape, dx, dy);
        assert_eq!(field_shifted.len(), global_shifted.len());
        for (left, right) in field_shifted.iter().zip(&global_shifted) {
            match (left.is_finite(), right.is_finite()) {
                (true, true) => assert!((left - right).abs() < 1e-6),
                (false, false) => {}
                _ => panic!("finite mismatch"),
            }
        }
    }
}
