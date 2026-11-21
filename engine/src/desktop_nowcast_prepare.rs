//! Native desktop nowcast input preparation.
//!
//! Owns HSD discovery/calibration/cache warming and strict native GFS/CAMS
//! resolution and downloads for the nowcast event window.
use crate::{ads, cams_data, desktop_hsd, desktop_nowcast, gfs_data, request_file, time_contract};
use chrono::{DateTime, Utc};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::PathBuf,
    sync::atomic::{AtomicBool, Ordering},
    time::Instant,
};

const MAX_JSON: u64 = 128 * 1024 * 1024;
const MAX_HOURS: usize = 64;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    pub path: PathBuf,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    gfs_cache_dir: PathBuf,
    cams_cache_dir: PathBuf,
    satellite_cache_dir: PathBuf,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    payload: Value,
    #[serde(default)]
    fast: bool,
    config: Config,
}

fn parse_time(value: &Value, key: &str) -> Result<DateTime<Utc>, String> {
    time_contract::parse_utc(
        value[key]
            .as_str()
            .ok_or_else(|| format!("nowcast context is missing {key}"))?,
    )
}

fn request_number(context: &Value, key: &str) -> Result<f64, String> {
    context["request"][key]
        .as_f64()
        .filter(|value| value.is_finite())
        .ok_or_else(|| format!("nowcast context request has invalid {key}"))
}

pub(crate) fn model_hours(
    context: &Value,
    frame_times: &[DateTime<Utc>],
) -> Result<Vec<u32>, String> {
    let cycle = parse_time(context, "cycle")?;
    let mut hours = BTreeSet::new();
    let mut add = |value: DateTime<Utc>| {
        let elapsed = (value - cycle).num_milliseconds() as f64 / 3_600_000.0;
        if elapsed < -1e-6 {
            return;
        }
        let left = elapsed.floor().max(0.0) as u32;
        hours.insert(left);
        if (elapsed - f64::from(left)).abs() >= 1e-6 {
            hours.insert(left.saturating_add(1));
        }
    };
    for value in context["times"]
        .as_array()
        .ok_or("nowcast context has no event times")?
    {
        add(time_contract::parse_utc(
            value
                .as_str()
                .ok_or("nowcast context contains an invalid event time")?,
        )?);
    }
    for value in frame_times {
        add(*value);
    }
    let result: Vec<_> = hours.into_iter().collect();
    if result.is_empty() || result.len() > MAX_HOURS {
        return Err("nowcast GFS requirements exceed the bounded hour limit".into());
    }
    Ok(result)
}

fn hsd_frames(input: &Input, context: &Value) -> Result<Value, String> {
    let lookup = parse_time(context, "lookup")?;
    let mut result = desktop_hsd::execute(desktop_hsd::Params {
        cache_dir: input.config.satellite_cache_dir.clone(),
        reference_time_utc: time_contract::iso(lookup),
        lat: request_number(context, "lat")?,
        lon: request_number(context, "lon")?,
        radius_km: request_number(context, "radius_km")?,
        count: 4,
        max_age_minutes: 30.0,
        output_dir: None,
    })?;
    if !input.fast
        && result["frames"]
            .as_array()
            .is_none_or(|frames| frames.len() < 2)
    {
        result = crate::hsd_provider::execute(json!({
            "cache_dir":input.config.satellite_cache_dir,
            "reference_time_utc":time_contract::iso(lookup),
            "lat":request_number(context,"lat")?,
            "lon":request_number(context,"lon")?,
            "radius_km":request_number(context,"radius_km")?,
            "count":4,"max_age_minutes":30.0
        }))?;
    }
    if result["frames"]
        .as_array()
        .is_none_or(|frames| frames.len() < 2)
    {
        return Err("native nowcast preparation requires two recent HSD frames".into());
    }
    Ok(result)
}

fn gfs_files(
    input: &Input,
    context: &Value,
    cycle: DateTime<Utc>,
    hours: &[u32],
    cancel: &AtomicBool,
) -> Result<Vec<Value>, String> {
    let bbox = context["bbox"]
        .as_array()
        .filter(|row| row.len() == 4)
        .ok_or("nowcast context has no GFS bbox")?
        .iter()
        .map(|value| {
            value
                .as_f64()
                .filter(|v| v.is_finite())
                .ok_or("nowcast context GFS bbox is invalid")
        })
        .collect::<Result<Vec<_>, _>>()?;
    let bbox = [bbox[0], bbox[1], bbox[2], bbox[3]];
    let region = context["region"].as_str().map(str::to_owned);
    crate::cancel_scope::with_shared_cancel(cancel, |shared| {
        let client = gfs_data::GfsClient::with_cancel(
            gfs_data::Config {
                cache_dir: input.config.gfs_cache_dir.clone(),
                resolution: "0p25".into(),
                region,
                timeout_seconds: 90.0,
                lock_timeout_seconds: 300.0,
                download_attempts: 4,
            },
            shared,
        )?;
        hours
            .iter()
            .map(|&hour| {
                check(cancel)?;
                let row = client
                    .resolve(cycle, hour, Some(bbox), false, true)?
                    .ok_or_else(|| format!("GFS f{hour:03} did not resolve to a cache file"))?;
                let path = row["path"]
                    .as_str()
                    .ok_or("GFS resolve response has no path")?;
                Ok(json!({
                    "forecast_hour": hour,
                    "path": path,
                    "status": row["status"].as_str().unwrap_or("resolved"),
                }))
            })
            .collect()
    })
}

fn cams_result(
    input: &Input,
    context: &Value,
    payload: &Value,
    cancel: &AtomicBool,
    progress: Option<&(dyn Fn(Value) -> Result<(), String> + Sync)>,
) -> Result<Value, String> {
    let areas = desktop_nowcast::cams_areas(context)?;
    let targets = context["cams_valid_times"]
        .as_array()
        .filter(|rows| !rows.is_empty() && rows.len() <= 256)
        .ok_or("nowcast context has no CAMS target times")?
        .iter()
        .map(|value| {
            time_contract::parse_utc(
                value
                    .as_str()
                    .ok_or("nowcast context contains an invalid CAMS target")?,
            )
            .map(time_contract::iso)
        })
        .collect::<Result<Vec<_>, _>>()?;
    let refresh = payload["_cams_refresh_cycle"]
        .as_str()
        .map(time_contract::parse_utc)
        .transpose()?
        .map(time_contract::iso);
    let mut parts = Vec::with_capacity(areas.len());
    for area in &areas {
        check(cancel)?;
        let result = cams_data::execute_cancellable(
            cams_data::Params {
                operation: "resolve".into(),
                cache_dir: input.config.cams_cache_dir.clone(),
                area: area.clone(),
                target_utcs: targets.clone(),
                target_utc: None,
                config: ads::Config::default(),
                allow_download: true,
                require_exact_valid_time: true,
                refresh_before_cycle: refresh.clone(),
                all_leads: false,
                model_bbox: None,
            },
            cancel,
            progress,
        )?;
        let missing = result["missing_valid_times"]
            .as_array()
            .ok_or("CAMS response is missing missing_valid_times")?;
        if !missing.is_empty() {
            return Err(format!(
                "native nowcast CAMS preparation is incomplete: {result}"
            ));
        }
        parts.push(result);
    }
    if parts.len() == 1 {
        return Ok(parts.pop().unwrap());
    }
    let mut grouped = BTreeMap::<String, Vec<Value>>::new();
    for part in &parts {
        for row in part["files"]
            .as_array()
            .ok_or("CAMS response is missing files")?
        {
            let key = row["requested_valid_time"]
                .as_str()
                .ok_or("CAMS response contains an invalid requested time")?
                .to_owned();
            grouped.entry(key).or_default().push(row.clone());
        }
    }
    let files = grouped
        .into_iter()
        .map(|(_, rows)| {
            let mut row = rows
                .first()
                .cloned()
                .ok_or("CAMS response contains an empty file group")?;
            let paths = rows
                .iter()
                .map(|value| value["path"].clone())
                .collect::<Vec<_>>();
            row["paths"] = Value::Array(paths.into_iter().skip(1).collect());
            Ok(row)
        })
        .collect::<Result<Vec<_>, String>>()?;
    Ok(json!({
        "contract": "rust-cams-data-v1",
        "files": files,
        "missing_valid_times": [],
        "areas": areas,
        "parts": parts,
        "wrapped": true,
    }))
}

pub fn execute(params: Params) -> Result<Value, String> {
    execute_cancellable(params, &AtomicBool::new(false), None)
}

fn check(cancel: &AtomicBool) -> Result<(), String> {
    if cancel.load(Ordering::Acquire) {
        Err("nowcast preparation cancelled".into())
    } else {
        Ok(())
    }
}

pub fn execute_cancellable(
    params: Params,
    cancel: &AtomicBool,
    progress: Option<&(dyn Fn(Value) -> Result<(), String> + Sync)>,
) -> Result<Value, String> {
    let started = Instant::now();
    check(cancel)?;
    let (input, _) = request_file::read::<Input>(&params.path, MAX_JSON)?;
    if input.fast {
        return Err("native nowcast preparation requires a non-fast context".into());
    }
    if !input.payload.is_object() {
        return Err("nowcast preparation payload must be an object".into());
    }
    let context = desktop_nowcast::execute(desktop_nowcast::Params {
        payload: input.payload.clone(),
        fast: false,
    })?;
    check(cancel)?;
    let hsd = hsd_frames(&input, &context)?;
    check(cancel)?;
    let frame_times = hsd["frames"]
        .as_array()
        .ok_or("native HSD response is missing frames")?
        .iter()
        .map(|frame| {
            time_contract::parse_utc(
                frame["valid_time_utc"]
                    .as_str()
                    .ok_or("native HSD frame is missing valid_time_utc")?,
            )
        })
        .collect::<Result<Vec<_>, _>>()?;
    let cycle = parse_time(&context, "cycle")?;
    let hours = model_hours(&context, &frame_times)?;
    let gfs = gfs_files(&input, &context, cycle, &hours, cancel)?;
    check(cancel)?;
    let cams = cams_result(&input, &context, &input.payload, cancel, progress)?;
    check(cancel)?;
    Ok(json!({
        "contract": "rust-desktop-nowcast-prepare-v1",
        "runtime_python_used": false,
        "status": "ready",
        "cycle": time_contract::iso(cycle),
        "hours": hours,
        "gfs": {"files": gfs},
        "cams": cams,
        "hsd": hsd,
        "elapsed_ms": started.elapsed().as_secs_f64() * 1000.0,
    }))
}
