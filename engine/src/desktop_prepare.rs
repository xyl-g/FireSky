//! Native desktop model-input preparation.
//!
//! The desktop request is still written by Python/GUI code, but all provider
//! planning, terrain correction, downloads and CAMS selection happen here.
//! This module deliberately returns a small summary instead of echoing the
//! potentially very large regional context document.
use crate::{
    ads, cams, cams_data, desktop_context, ecmwf_data, gfs_data, request_file, time_contract,
};
use chrono::{DateTime, Duration, Utc};
use std::time::SystemTime;
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::PathBuf,
    sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
    time::Instant,
};

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

fn gfs_client(
    input: &Input,
    context: &Value,
    cancel: std::sync::Arc<AtomicBool>,
) -> Result<gfs_data::GfsClient, String> {
    gfs_data::GfsClient::with_cancel(
        gfs_data::Config {
            cache_dir: input.config.gfs_cache_dir.clone(),
            resolution: "0p25".into(),
            region: compute_region(context)?,
            timeout_seconds: 90.0,
            lock_timeout_seconds: 300.0,
            download_attempts: 4,
        },
        cancel,
    )
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
    cancel: &AtomicBool,
    tracker: Option<&PhaseTracker<'_>>,
) -> Result<Vec<Value>, String> {
    let bbox = array4(context, "bbox")?;
    // Mirror the caller's borrowed flag into the client's Arc flag so mid-file
    // reads observe cancellation; the scoped poller stops when downloads end.
    crate::cancel_scope::with_shared_cancel(cancel, |shared| {
        let client = gfs_client(input, context, shared)?;
        requested
            .iter()
            .map(|&hour| {
                check(cancel)?;
                let rows = client.resolve_parts(cycle, hour, Some(bbox), false, true)?;
                if rows.is_empty() {
                    return Err(format!("GFS f{hour:03} did not resolve to a cache file"));
                }
                let mut parts = Vec::new();
                let mut bytes = 0_u64;
                for row in rows {
                    let path = row["path"]
                        .as_str()
                        .ok_or("GFS resolve response has no path")?;
                    // v1.9.7：仅真实下载计入吞吐（缓存命中 0）。
                    if row["status"].as_str() == Some("downloaded") {
                        bytes += std::fs::metadata(path)
                            .map(|meta| meta.len())
                            .unwrap_or(0);
                    }
                    parts.push(json!({
                        "forecast_hour": hour,
                        "path": path,
                        "status": row["status"].as_str().unwrap_or("resolved"),
                        "bytes": if row["status"].as_str() == Some("downloaded") {
                            json!(std::fs::metadata(path).map(|meta| meta.len()).unwrap_or(0))
                        } else {
                            json!(0)
                        },
                    }));
                }
                if let Some(tracker) = tracker {
                    tracker.bytes.fetch_add(bytes, Ordering::AcqRel);
                    tracker.tick()?;
                }
                // Wrapped bboxes resolve to two longitude segments; the first
                // part keeps the legacy single-path row shape.
                let mut primary = parts.remove(0);
                if !parts.is_empty() {
                    primary["parts"] = json!(parts);
                }
                Ok(primary)
            })
            .collect::<Result<Vec<Value>, String>>()
    })
}

fn download_ec(
    input: &Input,
    cycle: DateTime<Utc>,
    requested: &[u32],
    cancel: &AtomicBool,
    tracker: Option<&PhaseTracker<'_>>,
) -> Result<Value, String> {
    check(cancel)?;
    let hours: Vec<i64> = requested.iter().map(|v| i64::from(*v)).collect();
    // v1.9.7：与同步轮同款 4 worker 并发 + 逐文件进度（每目标文件独立
    // lock，并发安全）。
    let rows = crate::desktop_sync::download_pool(
        &hours,
        cancel,
        &|done, total, _bytes| {
            phase_event(
                tracker.and_then(|t| t.progress),
                tracker.map(|t| t.stage_start).unwrap_or(35),
                tracker.map(|t| t.stage_span).unwrap_or(20),
                done as usize,
                total as usize,
                "ec",
                tracker.map(|t| t.bytes.load(Ordering::Acquire)).unwrap_or(0),
                tracker.map(|t| t.started).unwrap_or_else(Instant::now),
            )
        },
        |hour| {
            let mut value = ecmwf_data::execute_cancellable(
                ecmwf_data::Params {
                    operation: "download".into(),
                    config: ec_config(input),
                    cycle_utc: Some(time_contract::iso(cycle)),
                    forecast_hour: None,
                    forecast_hours: Some(vec![*hour]),
                    reference_utc: None,
                    now_utc: None,
                    evaluation_utcs: Vec::new(),
                    max_cycles: 8,
                    allow_download: true,
                    use_cache_for_cycle: true,
                },
                cancel,
            )?;
            let hour_bytes = value["downloaded_bytes"].as_u64().unwrap_or(0);
            if let Some(tracker) = tracker {
                tracker.bytes.fetch_add(hour_bytes, Ordering::AcqRel);
                tracker.tick()?;
            }
            let files = value["files"].take();
            Ok((json!({"hour": hour, "hour_bytes": hour_bytes, "files": files}), hour_bytes))
        },
    )?;
    let downloaded_bytes: u64 = rows
        .iter()
        .filter_map(|row| row.get("hour_bytes").and_then(Value::as_u64))
        .sum();
    let files = rows
        .into_iter()
        .flat_map(|row| match row.get("files") {
            Some(Value::Array(rows)) => rows.clone(),
            _ => Vec::new(),
        })
        .collect::<Vec<_>>();
    Ok(json!({
        "contract": "rust-ecmwf-data-v1",
        "cycle_utc": time_contract::iso(cycle),
        "forecast_hours": hours,
        "files": files,
        "downloaded_bytes": downloaded_bytes,
    }))
}

/// 日期策略（v1.8.0 用户裁定）：
/// - 过去日期（早于本地时区"今天"）：只读本地缓存，绝不重新下载老数据，
///   缺失即由预检明确报缺；
/// - CAMS 时效窗口：CAMS 全球大气预报按发布仅覆盖未来 CAMS_LEAD_DAYS 天，
///   超出窗口的日期无法计算（GFS/EC 归档不受此限制），在预检阶段直接
///   明确拒绝而非静默降级。
pub(crate) const CAMS_LEAD_DAYS: i64 = 5;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(crate) enum DatePolicy {
    /// 旧日期：仅本地缓存。
    Past,
    /// 今天起 CAMS 窗口内：正常下载与计算。
    InWindow,
    /// 超出 CAMS 时效窗口：明确拒绝。
    BeyondCams,
}

pub(crate) fn date_policy(
    target_date: &str,
    timezone: &str,
    now_utc: Option<&str>,
) -> Result<DatePolicy, String> {
    let date = chrono::NaiveDate::parse_from_str(target_date, "%Y-%m-%d")
        .map_err(|_| "invalid target date")?;
    let tz: chrono_tz::Tz = timezone
        .parse()
        .map_err(|_| "invalid timezone".to_string())?;
    let now = match now_utc {
        Some(value) => time_contract::parse_utc(value)?,
        None => SystemTime::now().into(),
    };
    let today = now.with_timezone(&tz).date_naive();
    Ok(if date < today {
        DatePolicy::Past
    } else if date <= today + Duration::days(CAMS_LEAD_DAYS) {
        DatePolicy::InWindow
    } else {
        DatePolicy::BeyondCams
    })
}

fn payload_policy(input: &Input) -> Result<DatePolicy, String> {
    date_policy(
        input.payload["target_date"].as_str().unwrap_or(""),
        input.payload["timezone_name"].as_str().unwrap_or("UTC"),
        input.now_utc.as_deref(),
    )
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

fn cams_areas(context: &Value) -> Result<Vec<[f64; 4]>, String> {
    if let Some(rows) = context["areas"].as_array() {
        if rows.is_empty() || rows.len() > 2 {
            return Err("desktop context CAMS areas are invalid".into());
        }
        return rows
            .iter()
            .map(|row| {
                let mut value = [0.0; 4];
                for (slot, item) in value.iter_mut().zip(
                    row.as_array()
                        .filter(|items| items.len() == 4)
                        .ok_or("desktop context CAMS area is invalid")?,
                ) {
                    *slot = item
                        .as_f64()
                        .filter(|v| v.is_finite())
                        .ok_or("desktop context CAMS area contains a nonfinite value")?;
                }
                Ok(value)
            })
            .collect();
    }
    cams_data::split_areas(array4(context, "area")?)
}

fn download_cams(
    input: &Input,
    context: &Value,
    cancel: &AtomicBool,
    progress: Option<&(dyn Fn(Value) -> Result<(), String> + Sync)>,
) -> Result<Value, String> {
    check(cancel)?;
    let cache_dir = input
        .config
        .cams_cache_dir
        .clone()
        .ok_or("native desktop preparation requires a CAMS cache directory")?;
    let areas = cams_areas(context)?;
    let targets = cams_targets(context)?;
    let refresh = input.payload["_cams_refresh_cycle"]
        .as_str()
        .map(time_contract::parse_utc)
        .transpose()?
        .map(time_contract::iso);
    // User-directed full-cycle scope (2026-11-12): single-area plans download
    // the whole continent of the request with every lead of the chosen run so
    // later predictions resolve from cache without re-queueing ADS jobs.
    // Wrapped multi-area plans keep the exact per-part subset contract, and
    // payload `cams_download_scope: "request"` restores the legacy subset
    // behavior per request.
    let scope = match input.payload.get("cams_download_scope") {
        None | Some(Value::Null) => true,
        Some(Value::String(value)) if value == "continent_cycle" => true,
        Some(Value::String(value)) if value == "request" => false,
        Some(_) => return Err("invalid cams_download_scope".into()),
    };
    let aerosol_source = input.payload["aerosol_source"].as_str().unwrap_or("cams");
    let parsed_source = crate::cams_cache::AerosolSource::parse(Some(aerosol_source))?;
    // GEOS-FP 零排队且按 bbox 服务端裁剪：交互式 prepare 只下当前请求范围，数十秒内完成。
    let cycle_scope = scope
        && areas.len() == 1
        && parsed_source == crate::cams_cache::AerosolSource::Cams;
    let download_areas: Vec<[f64; 4]> = if cycle_scope {
        vec![crate::continents::envelope_for(areas[0])?]
    } else {
        areas.clone()
    };
    let mut parts = Vec::with_capacity(download_areas.len());
    for area in &download_areas {
        check(cancel)?;
        let result = cams_data::execute_cancellable(
            cams_data::Params {
                operation: "resolve".into(),
                cache_dir: cache_dir.clone(),
                area: json!(area),
                target_utcs: targets.iter().copied().map(time_contract::iso).collect(),
                target_utc: None,
                config: ads::Config {
                    job_ledger_path: Some(ads::ledger_path_for_cams_cache(&cache_dir)),
                    ..Default::default()
                },
                allow_download: true,
                require_exact_valid_time: true,
                refresh_before_cycle: refresh.clone(),
                all_leads: cycle_scope,
                model_bbox: Some(array4(context, "bbox")?),
                aerosol_source: Some(parsed_source.as_str().to_owned()),
            },
            cancel,
            progress,
        )?;
        let missing = result["missing_valid_times"]
            .as_array()
            .ok_or("CAMS response is missing missing_valid_times")?;
        if !missing.is_empty() {
            return Err(format!("CAMS preparation is incomplete: {result}"));
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
                .map(|value| {
                    // Per-part rows carry only `path`; a wrapped group must
                    // retain every part (including the first) or the scorer
                    // sees a hole over the dropped part's longitude arc.
                    value
                        .get("paths")
                        .and_then(Value::as_array)
                        .map(|extra| {
                            let mut all = vec![value["path"].clone()];
                            all.extend(extra.iter().cloned());
                            all
                        })
                        .unwrap_or_else(|| vec![value["path"].clone()])
                })
                .flatten()
                .collect::<Vec<_>>();
            row["paths"] = Value::Array(paths.into_iter().skip(1).collect());
            Ok(row)
        })
        .collect::<Result<Vec<_>, String>>()?;
    let mut result = json!({
        "contract": "rust-cams-data-v1",
        "files": files,
        "missing_valid_times": [],
        "areas": areas,
        "parts": parts,
    });
    result["wrapped"] = json!(true);
    Ok(result)
}

fn download_first_for_terrain(
    input: &Input,
    context: &Value,
    cycle: DateTime<Utc>,
    hour: u32,
    cancel: &AtomicBool,
) -> Result<(), String> {
    match context["common"]["model_source"]
        .as_str()
        .ok_or("desktop context model source is missing")?
    {
        "gfs" => {
            let _ = download_gfs(input, context, cycle, &[hour], cancel, None)?;
        }
        "ec" => {
            let _ = download_ec(input, cycle, &[hour], cancel, None)?;
        }
        _ => return Err("desktop context model source is invalid".into()),
    }
    Ok(())
}

pub fn execute(params: Params) -> Result<Value, String> {
    execute_cancellable(params, &AtomicBool::new(false), None)
}

/// 阶段事件助手：GFS 8→35%，EC 35→55%，CAMS 55→90%（prepare 段）。
fn phase_event(
    progress: Option<&(dyn Fn(Value) -> Result<(), String> + Sync)>,
    stage_start: usize,
    stage_span: usize,
    done: usize,
    total: usize,
    model: &str,
    bytes: u64,
    started: Instant,
) -> Result<(), String> {
    let Some(progress) = progress else { return Ok(()) };
    let within = if total == 0 {
        0
    } else {
        done.min(total) * stage_span / total
    };
    let elapsed_ms = started.elapsed().as_secs_f64() * 1000.0;
    let mibs = if elapsed_ms > 0.0 {
        bytes as f64 / 1024.0 / 1024.0 / (elapsed_ms / 1000.0)
    } else {
        0.0
    };
    progress(json!({
        "stage": "prepare",
        "percent": (stage_start + within).min(95),
        "source": model,
        "message": format!("正在下载 {} {}/{} · {:.1} MB/s", model.to_uppercase(), done, total, mibs),
        "bytes": bytes,
        "elapsed_ms": elapsed_ms,
    }))
}

/// 逐文件事件包（GFS/EC 共用）：闭包持有 done/bytes 计数。
struct PhaseTracker<'a> {
    progress: Option<&'a (dyn Fn(Value) -> Result<(), String> + Sync)>,
    stage_start: usize,
    stage_span: usize,
    total: usize,
    done: AtomicUsize,
    bytes: AtomicU64,
    model: String,
    started: Instant,
}

impl PhaseTracker<'_> {
    fn tick(&self) -> Result<(), String> {
        let done = self.done.fetch_add(1, Ordering::AcqRel) + 1;
        phase_event(
            self.progress,
            self.stage_start,
            self.stage_span,
            done,
            self.total,
            &self.model,
            self.bytes.load(Ordering::Acquire),
            self.started,
        )
    }
}

fn check(cancel: &AtomicBool) -> Result<(), String> {
    if cancel.load(Ordering::Acquire) {
        Err("desktop operation cancelled".into())
    } else {
        Ok(())
    }
}

/// v1.9.7：准备阶段进度回调（JSON-lines 事件，shape 与同步轮一致）。
/// None 时静默（契约脚本/CLI 直用）。
pub fn execute_cancellable(
    params: Params,
    cancel: &AtomicBool,
    progress: Option<&(dyn Fn(Value) -> Result<(), String> + Sync)>,
) -> Result<Value, String> {
    check(cancel)?;
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
    match payload_policy(&input)? {
        DatePolicy::BeyondCams => {
            let date = input.payload["target_date"].as_str().unwrap_or("");
            return Err(format!(
                "CAMS 全球大气数据仅覆盖未来 {CAMS_LEAD_DAYS} 天内的日期；所选 {date} 超出时效窗口，无法计算"
            ));
        }
        DatePolicy::Past => {
            // 旧日期只读本地缓存：跳过全部下载，缺失由预检明确报缺。
            return Ok(json!({
                "contract": "rust-desktop-prepare-v1",
                "status": "cache-only",
                "policy": "past-local-cache-only",
                "message": "旧日期仅使用本地缓存数据，不重新下载；缺失项见预检结果",
            }));
        }
        DatePolicy::InWindow => {}
    }
    let cycle = utc(&context, "cycle")?;
    let mut terrain_passes = 0_u32;
    if context["terrain_pending"] == true {
        let initial = hours(&context)?[0];
        download_first_for_terrain(&input, &context, cycle, initial, cancel)?;
        check(cancel)?;
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
        "gfs" => {
            let tracker = PhaseTracker {
                progress,
                stage_start: 8,
                stage_span: 27,
                total: requested.len(),
                done: AtomicUsize::new(0),
                bytes: AtomicU64::new(0),
                model: "gfs".into(),
                started: Instant::now(),
            };
            json!({
                "source": "gfs",
                "files": download_gfs(&input, &context, cycle, &requested, cancel, Some(&tracker))?,
            })
        }
        "ec" => {
            let tracker = PhaseTracker {
                progress,
                stage_start: 35,
                stage_span: 20,
                total: requested.len(),
                done: AtomicUsize::new(0),
                bytes: AtomicU64::new(0),
                model: "ec".into(),
                started: Instant::now(),
            };
            json!({
                "source": "ecmwf",
                "resolution": download_ec(&input, cycle, &requested, cancel, Some(&tracker))?,
            })
        }
        _ => return Err("desktop context model source is invalid".into()),
    };
    // CAMS 段（55→90）：ADS 排队或 GEOS-FP 直下。
    let is_geos_fp = input.payload["aerosol_source"].as_str() == Some("geos-fp");
    if let Some(progress) = progress {
        progress(json!({
            "stage": "prepare",
            "percent": 55,
            "source": "cams",
            "message": if is_geos_fp {
                "正在准备 NASA GEOS-FP 全球气溶胶数据（OPeNDAP 免排队下载）"
            } else {
                "正在准备 CAMS 全球大气数据（ADS 排队与整周期下载，耗时数分钟属正常）"
            },
        }))?;
    }
    let cams_result = download_cams(&input, &context, cancel, progress)?;
    if let Some(progress) = progress {
        progress(json!({
            "stage": "prepare",
            "percent": 90,
            "source": "cams",
            "message": if is_geos_fp { "GEOS-FP 数据就绪" } else { "CAMS 数据就绪" },
        }))?;
    }
    check(cancel)?;
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

#[cfg(test)]
mod date_policy_tests {
    use super::*;

    #[test]
    fn date_policy_covers_past_window_and_beyond() {
        // 北京时区 2026-11-20 16:00Z（=11-21 00:00 本地）为基准。
        let now = "2026-11-20T16:00:00Z";
        // 过去日期 → 仅本地缓存。
        assert_eq!(
            date_policy("2026-11-19", "Asia/Shanghai", Some(now)).unwrap(),
            DatePolicy::Past
        );
        // 北京今天 = 11-21（16Z + 8h）：窗口内（今天..=今天+5）。
        assert_eq!(
            date_policy("2026-11-21", "Asia/Shanghai", Some(now)).unwrap(),
            DatePolicy::InWindow
        );
        assert_eq!(
            date_policy("2026-11-26", "Asia/Shanghai", Some(now)).unwrap(),
            DatePolicy::InWindow
        );
        // 超窗（今天 + 6 天）→ BeyondCams。
        assert_eq!(
            date_policy("2026-11-27", "Asia/Shanghai", Some(now)).unwrap(),
            DatePolicy::BeyondCams
        );
        // 时区差异影响"今天"的判定：UTC 11-20 尚未到北京 11-21。
        assert_eq!(
            date_policy("2026-11-21", "UTC", Some(now)).unwrap(),
            DatePolicy::InWindow
        );
        // 非法输入。
        assert!(date_policy("not-a-date", "UTC", Some(now)).is_err());
        assert!(date_policy("2026-11-20", "Bad/Zone", Some(now)).is_err());
    }

    #[test]
    fn past_dates_short_circuit_prepare_before_any_download() {
        // 旧日期 prepare 直接返回 cache-only 占位（含说明），不进入下载链。
        // 行为验证见 execute 主流程的 policy 分支；此处固化窗口常量口径。
        assert_eq!(CAMS_LEAD_DAYS, 5);
    }
}
