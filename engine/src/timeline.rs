//! Stored-request regional solar/time-axis preparation, without Python.
use crate::astronomy::Phenomenon;
use crate::native::timeline::{self, ModelTimes, Point, Timeline, SAMPLE_FIELDS, STEPS};
use crate::tensor_bundle::{BundleWriter, SourceIdentity};
use crate::time_contract::{parse_date, parse_utc};
use serde::Deserialize;
use serde_json::json;
use std::path::{Path, PathBuf};

const MAX_REQUEST_BYTES: u64 = 64 * 1024 * 1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct InputPoint {
    lat: f64,
    lon: f64,
    elevation_m: f64,
    timezone_name: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    points: Vec<InputPoint>,
    date: String,
    phenomenon: Phenomenon,
    cycle_utc: Option<String>,
    hours: Option<Vec<f64>>,
}

pub struct Prepared {
    pub source: SourceIdentity,
    pub timeline: Timeline,
    pub date: String,
    pub rising: bool,
    pub cycle: Option<String>,
    pub hours: Option<Vec<f64>>,
}

pub fn read(path: &Path) -> Result<Prepared, String> {
    let (input, source): (Input, _) = crate::request_file::read(path, MAX_REQUEST_BYTES)?;
    if input.points.is_empty() || input.points.len() > timeline::MAX_POINTS {
        return Err("regional request needs 1..500000 points".into());
    }
    let date = parse_date(&input.date)?;
    let rising = matches!(input.phenomenon, Phenomenon::Dawn);
    let points = input
        .points
        .into_iter()
        .map(|point| {
            if point.timezone_name.len() > 128 {
                return Err("timezone name exceeds limit".to_string());
            }
            Ok(Point {
                lat: point.lat,
                lon: point.lon,
                elevation_m: point.elevation_m,
                timezone: point
                    .timezone_name
                    .parse()
                    .map_err(|_| format!("unknown IANA timezone: {}", point.timezone_name))?,
            })
        })
        .collect::<Result<Vec<_>, String>>()?;
    let model = match (&input.cycle_utc, &input.hours) {
        (None, None) => None,
        (Some(cycle), Some(hours)) => {
            let time = parse_utc(cycle)?;
            Some(ModelTimes {
                cycle_epoch: time.timestamp() as f64
                    + f64::from(time.timestamp_subsec_micros()) / 1e6,
                hours,
            })
        }
        _ => return Err("cycle_utc and hours must be supplied together".into()),
    };
    let timeline = timeline::prepare(&points, date, rising, model).map_err(|e| e.to_string())?;
    Ok(Prepared {
        source,
        timeline,
        date: input.date,
        rising,
        cycle: input.cycle_utc,
        hours: input.hours,
    })
}

pub fn prepare_file(path: &Path, output: &Path) -> Result<PathBuf, String> {
    let prepared = read(path)?;
    let points = prepared.timeline.rows.len() as u64;
    let offsets: Vec<i32> = (0..STEPS as i32)
        .map(|i| {
            if prepared.rising {
                -75 + i * 5
            } else {
                -20 + i * 5
            }
        })
        .collect();
    let rows = &prepared.timeline.rows;
    let metadata = json!({
        "backend":"rust-regional-solar-v1",
        "timezone_database":chrono_tz::IANA_TZDB_VERSION,
        "date":prepared.date,"phenomenon":if prepared.rising {"dawn"} else {"dusk"},
        "point_count":points,"step_count":STEPS,"offsets_minutes":offsets,
        "sample_fields":SAMPLE_FIELDS,"cycle_utc":prepared.cycle,"hours":prepared.hours,
        "local_date_fallback_count":rows.iter().filter(|row| row.local_date_fallback).count(),
        "polar_policy":"legacy-hour-angle-clamp",
        "force_anchor_valid":true,
        "local_aod":"placeholder-0.12-requires-CAMS-before-scoring",
    });
    let expected_size = prepared.source.bytes;
    let mut writer = BundleWriter::create(output, "regional-solar-v1", prepared.source, metadata)?;
    writer.f32(
        "observers",
        &[points, 3],
        rows.iter().flat_map(|row| row.observer),
    )?;
    writer.f64(
        "elevation",
        &[points, STEPS as u64],
        rows.iter().flat_map(|row| row.elevation),
    )?;
    writer.f64(
        "azimuth",
        &[points, STEPS as u64],
        rows.iter().flat_map(|row| row.azimuth),
    )?;
    writer.f32(
        "valid",
        &[points, STEPS as u64],
        rows.iter().flat_map(|row| row.valid).map(f32::from),
    )?;
    writer.f64(
        "evaluation_epoch",
        &[points, STEPS as u64],
        rows.iter().flat_map(|row| row.evaluation),
    )?;
    writer.f64("anchor_epoch", &[points], rows.iter().map(|row| row.anchor))?;
    if let Some(samples) = &prepared.timeline.samples {
        writer.f32(
            "samples",
            &[points, STEPS as u64, SAMPLE_FIELDS as u64],
            samples.iter().flat_map(|row| *row),
        )?;
    }
    let metadata = std::fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if !metadata.file_type().is_file() || metadata.len() != expected_size {
        return Err("regional source changed before publication".into());
    }
    writer.commit(path)
}
