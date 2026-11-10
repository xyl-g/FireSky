//! Native desktop model-input preparation.
//!
//! The desktop request is still written by Python/GUI code, but all provider
//! planning, terrain correction, downloads and CAMS selection happen here.
//! This module deliberately returns a small summary instead of echoing the
//! potentially very large regional context document.
use crate::{
    ads, cams, cams_data, desktop_context, ecmwf_data, gfs_data, request_file, time_contract,
};
use chrono::{DateTime, Utc};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{collections::BTreeSet, path::PathBuf, time::Instant};

const MAX_JSON: u64 = 128 * 1024 * 1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    pub path: PathBuf,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    payload: Value,
    #[serde(default)]
    regional: bool,
    #[serde(default)]
    fast: bool,
    config: desktop_context::Config,
    now_utc: Option<String>,
}

fn utc(value: &Value, key: &str) -> Result<DateTime<Utc>, String> {
    time_contract::parse_utc(
        value[key]
            .as_str()
            .ok_or_else(|| format!("missing {key}"))?,
    )
}

fn array4(value: &Value, key: &str) -> Result<[f64; 4], String> {
    let row = value[key]
        .as_array()
        .filter(|row| row.len() == 4)
        .ok_or_else(|| format!("missing {key} bbox"))?;
    let mut result = [0.0; 4];
    for (slot, item) in result.iter_mut().zip(row) {
        *slot = item
            .as_f64()
            .filter(|v| v.is_finite())
            .ok_or_else(|| format!("{key} bbox contains a nonfinite coordinate"))?;
    }
    Ok(result)
}

fn hours(value: &Value) -> Result<Vec<u32>, String> {
    let row = value["hours"]
        .as_array()
        .filter(|row| !row.is_empty() && row.len() <= 256)
        .ok_or("desktop context has no bounded model hours")?;
    let mut result = row
        .iter()
        .map(|item| {
            item.as_u64()
                .and_then(|v| u32::try_from(v).ok())
                .filter(|v| *v <= 10_000)
                .ok_or("desktop context contains an invalid model hour")
        })
        .collect::<Result<Vec<_>, _>>()?;
    result.sort_unstable();
    result.dedup();
    if result.is_empty() {
        return Err("desktop context has no model hours".into());
    }
    Ok(result)
}

fn compute_region(context: &Value) -> Result<Option<String>, String> {
    match &context["common"]["_compute_region"] {
        Value::Null => Ok(None),
        Value::String(value) if !value.is_empty() => Ok(Some(value.clone())),
        _ => Err("desktop context compute region is invalid".into()),
    }
}

fn gfs_client(input: &Input, context: &Value) -> Result<gfs_data::GfsClient, String> {
    gfs_data::GfsClient::new(gfs_data::Config {
        cache_dir: input.config.gfs_cache_dir.clone(),
        resolution: "0p25".into(),
        region: compute_region(context)?,
        timeout_seconds: 90.0,
        lock_timeout_seconds: 300.0,
        download_attempts: 4,
    })
}

fn ec_config(input: &Input) -> ecmwf_data::Config {
    let source = input.payload["ecmwf_source"]
        .as_str()
        .unwrap_or("ecmwf")
        .to_string();
    ecmwf_data::Config {
        cache_dir: input.config.ecmwf_cache_dir.clone(),
        source,
        ..Default::default()
    }
}

fn download_gfs(
    input: &Input,
    context: &Value,
    cycle: DateTime<Utc>,
    requested: &[u32],
) -> Result<Vec<Value>, String> {
    let bbox = array4(context, "bbox")?;
    let client = gfs_client(input, context)?;
    requested
        .iter()
        .map(|&hour| {
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
}

fn download_ec(input: &Input, cycle: DateTime<Utc>, requested: &[u32]) -> Result<Value, String> {
    ecmwf_data::execute(ecmwf_data::Params {
        operation: "download".into(),
        config: ec_config(input),
        cycle_utc: Some(time_contract::iso(cycle)),
        forecast_hour: None,
        forecast_hours: Some(requested.iter().map(|v| i64::from(*v)).collect()),
        reference_utc: None,
        now_utc: None,
        evaluation_utcs: Vec::new(),
        max_cycles: 8,
        allow_download: true,
        use_cache_for_cycle: true,
    })
}

fn cams_targets(context: &Value) -> Result<Vec<DateTime<Utc>>, String> {
    let rows = context["times"]
        .as_array()
        .filter(|rows| !rows.is_empty())
        .ok_or("desktop context has no CAMS event times")?;
    let mut targets = BTreeSet::new();
    for value in rows {
        let sample = time_contract::parse_utc(
            value
                .as_str()
                .ok_or("desktop context contains an invalid event time")?,
        )?;
        let (left, right, _) = cams::bracket(&time_contract::iso(sample))?;
        targets.extend([left, right]);
    }
    if targets.is_empty() || targets.len() > 256 {
        return Err("desktop CAMS requirements exceed the bounded target limit".into());
    }
    Ok(targets.into_iter().collect())
}

fn download_cams(input: &Input, context: &Value) -> Result<Value, String> {
    let cache_dir = input
        .config
        .cams_cache_dir
        .clone()
        .ok_or("native desktop preparation requires a CAMS cache directory")?;
    let area = array4(context, "area")?;
    let targets = cams_targets(context)?;
    let refresh = input.payload["_cams_refresh_cycle"]
        .as_str()
        .map(time_contract::parse_utc)
        .transpose()?
        .map(time_contract::iso);
    let result = cams_data::execute(cams_data::Params {
        operation: "resolve".into(),
        cache_dir,
        area: json!(area),
        target_utcs: targets.iter().copied().map(time_contract::iso).collect(),
        target_utc: None,
        config: ads::Config::default(),
        allow_download: true,
        require_exact_valid_time: true,
        refresh_before_cycle: refresh,
    })?;
    let missing = result["missing_valid_times"]
        .as_array()
        .ok_or("CAMS response is missing missing_valid_times")?;
    if !missing.is_empty() {
        return Err(format!("CAMS preparation is incomplete: {result}"));
    }
    Ok(result)
}

fn download_first_for_terrain(
    input: &Input,
    context: &Value,
    cycle: DateTime<Utc>,
    hour: u32,
) -> Result<(), String> {
    match context["common"]["model_source"]
        .as_str()
        .ok_or("desktop context model source is missing")?
    {
        "gfs" => {
            let _ = download_gfs(input, context, cycle, &[hour])?;
        }
        "ec" => {
            let _ = download_ec(input, cycle, &[hour])?;
        }
        _ => return Err("desktop context model source is invalid".into()),
    }
    Ok(())
}

pub fn execute(params: Params) -> Result<Value, String> {
    let started = Instant::now();
    let (input, _) = request_file::read::<Input>(&params.path, MAX_JSON)?;
    if input.fast {
        return Err("native desktop preparation requires a non-fast context".into());
    }
    if !input.payload.is_object() {
        return Err("desktop preparation payload must be an object".into());
    }
    // These fields are part of the shared desktop request contract; context
    // resolution consumes them while this phase only needs the resolved plan.
    let _ = (input.regional, input.now_utc.as_deref());
    let mut context = desktop_context::resolve_path(&params.path)?;
    let cycle = utc(&context, "cycle")?;
    let mut terrain_passes = 0_u32;
    if context["terrain_pending"] == true {
        let initial = hours(&context)?[0];
        download_first_for_terrain(&input, &context, cycle, initial)?;
        terrain_passes = 1;
        context = desktop_context::resolve_path(&params.path)?;
        if context["terrain_pending"] == true {
            return Err("native terrain prepass did not produce a final event plan".into());
        }
    }
    let cycle = utc(&context, "cycle")?;
    let requested = hours(&context)?;
    let model = context["common"]["model_source"]
        .as_str()
        .ok_or("desktop context model source is missing")?;
    let model_result = match model {
        "gfs" => json!({
            "source": "gfs",
            "files": download_gfs(&input, &context, cycle, &requested)?,
        }),
        "ec" => json!({
            "source": "ecmwf",
            "resolution": download_ec(&input, cycle, &requested)?,
        }),
        _ => return Err("desktop context model source is invalid".into()),
    };
    let cams_result = download_cams(&input, &context)?;
    Ok(json!({
        "contract": "rust-desktop-prepare-v1",
        "runtime_python_used": false,
        "status": "ready",
        "model_source": model,
        "cycle": time_contract::iso(cycle),
        "hours": requested,
        "terrain_passes": terrain_passes,
        "terrain_pending": false,
        "model": model_result,
        "cams": cams_result,
        "elapsed_ms": started.elapsed().as_secs_f64() * 1000.0,
    }))
}
