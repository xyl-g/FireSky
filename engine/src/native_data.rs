//! Public data planning, transport and reversible cache maintenance.
use crate::{
    ads, cams, cams_data, desktop_context, desktop_inventory, ecmwf_data, gfs_data, time_contract,
};
use chrono::{Duration, NaiveDate, Utc};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::{Instant, SystemTime},
};

fn path(workspace: &Path, key: &str, default: &str) -> PathBuf {
    let visible = match default {
        ".cache/gfs" => "gfs",
        ".cache/ecmwf" => "ecmwf",
        ".cache/cams" => "cams",
        _ => default,
    };
    crate::paths::resolve(workspace, key, default, visible)
}
fn config(workspace: &Path) -> desktop_inventory::Config {
    desktop_inventory::Config {
        gfs_cache_dir: path(workspace, "FIRESKY_GFS_CACHE_DIR", ".cache/gfs"),
        ecmwf_cache_dir: path(workspace, "FIRESKY_ECMWF_CACHE_DIR", ".cache/ecmwf"),
        cams_cache_dir: path(workspace, "FIRESKY_CAMS_CACHE_DIR", ".cache/cams"),
    }
}
fn check(cancel: &AtomicBool) -> Result<(), String> {
    if cancel.load(Ordering::Acquire) {
        Err("data operation cancelled".into())
    } else {
        Ok(())
    }
}
fn now() -> chrono::DateTime<Utc> {
    SystemTime::now().into()
}

pub fn status(workspace: &Path) -> Result<Value, String> {
    let inventory =
        desktop_inventory::execute(&config(workspace), Arc::new(AtomicBool::new(false)))?;
    let datasets = inventory["datasets"].as_array().ok_or("inventory datasets missing")?
        .iter().map(|dataset| {
            let cycles = dataset["cycles"].as_object().map(|rows| rows.values().map(|row| json!({
                "cycle":row["label"],"file_count":row["files"],"total_size_mb":row["mb"],
                "forecast_hours":row["hours"],
            })).collect::<Vec<_>>()).unwrap_or_default();
            json!({
                "source":dataset["source"],"status":if cycles.is_empty() {"missing"} else {"unavailable"},
                "latest_available_cycle":"","latest_local_cycle":dataset["latest_cycle"],
                "required_files":0,"required_hours_count":0,"region_count":0,"regions":[],
                "local_files":dataset["file_count"],"missing_hours":[],"cycles":cycles,
                "prepared":false,"prepared_message":"Inventory alone does not establish request readiness",
                "capability_status":"available","capability_message":"Use request preflight for strict readiness",
            })
        }).collect::<Vec<_>>();
    let total: f64 = inventory["datasets"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|v| v["total_storage_mb"].as_f64())
        .sum();
    Ok(
        json!({"contract":"rust-data-status-v1","checked_at":time_contract::iso(now()),
        "overall_status":"missing","total_cache_size_mb":total,"datasets":datasets,"derived":[],
        "inventory":inventory,"runtime_python_used":false}),
    )
}

fn sources(payload: &Value) -> Result<BTreeSet<String>, String> {
    let defaults = vec![json!("gfs"), json!("ecmwf"), json!("cams")];
    let rows = match payload.get("sources") {
        Some(value) => value
            .as_array()
            .filter(|v| !v.is_empty())
            .ok_or("sources must be a nonempty array")?,
        None => &defaults,
    };
    rows.iter()
        .map(|row| match row.as_str() {
            Some("gfs") => Ok("gfs".into()),
            Some("ec" | "ecmwf") => Ok("ecmwf".into()),
            Some("cams") => Ok("cams".into()),
            _ => Err("unknown data source".into()),
        })
        .collect()
}

fn dates(payload: &Value) -> Result<Vec<String>, String> {
    let raw = if let Some(rows) = payload.get("target_dates") {
        rows.as_array()
            .filter(|v| !v.is_empty() && v.len() <= 7)
            .ok_or("target_dates requires 1..7 dates")?
            .clone()
    } else if let Some(date) = payload.get("target_date") {
        vec![date.clone()]
    } else {
        let days = payload
            .get("days")
            .map(|v| {
                v.as_u64()
                    .filter(|v| (1..=7).contains(v))
                    .ok_or("days must be an integer in 1..7")
            })
            .transpose()?
            .unwrap_or(3);
        let offset = payload
            .get("start_offset_days")
            .map(|v| {
                v.as_u64()
                    .filter(|v| *v <= 30)
                    .ok_or("start_offset_days must be in 0..30")
            })
            .transpose()?
            .unwrap_or(1);
        let timezone: chrono_tz::Tz = payload["timezone"]
            .as_str()
            .unwrap_or("UTC")
            .parse()
            .map_err(|_| "timezone must be an IANA timezone")?;
        let today = now().with_timezone(&timezone).date_naive();
        (0..days)
            .map(|i| json!((today + Duration::days((offset + i) as i64)).to_string()))
            .collect()
    };
    let mut result = BTreeSet::new();
    for value in raw {
        let text = value.as_str().ok_or("target date must be text")?;
        let date =
            NaiveDate::parse_from_str(text, "%Y-%m-%d").map_err(|_| "invalid target date")?;
        if date.to_string() != text {
            return Err("target date must be YYYY-MM-DD".into());
        }
        result.insert(text.to_owned());
    }
    Ok(result.into_iter().collect())
}

struct Plan {
    public: Value,
    selected: BTreeSet<String>,
}

fn plan(workspace: &Path, payload: &Value, cancel: &AtomicBool) -> Result<Plan, String> {
    if !payload.is_object() {
        return Err("data request requires an object".into());
    }
    let resources = crate::app_resources::resolve(workspace)?;
    let selected = sources(payload)?;
    if payload["cycle_overrides"].get("cams").is_some()
        || (selected.len() == 1 && selected.contains("cams") && payload.get("cycle").is_some())
    {
        return Err("explicit CAMS run pinning is unsupported; provide required valid times through event planning".into());
    }
    let dates = dates(payload)?;
    let region = payload["region"].as_str().unwrap_or("east-asia");
    if region.len() > 32
        || region.is_empty()
        || !region
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
    {
        return Err("invalid region code".into());
    }
    let known = desktop_context::REGIONS
        .iter()
        .find(|v| v.0 == region)
        .map(|v| v.2);
    let bbox = if let Some(value) = payload.get("bbox") {
        let rows = value
            .as_array()
            .filter(|v| v.len() == 4)
            .ok_or("bbox must be [west,east,north,south]")?;
        let mut b = [0.; 4];
        for (out, value) in b.iter_mut().zip(rows) {
            *out = value
                .as_f64()
                .filter(|v| v.is_finite())
                .ok_or("invalid bbox")?;
        }
        b
    } else {
        known.ok_or("unknown region requires bbox")?
    };
    let [west, east, north, south] = bbox;
    let degenerate_antimeridian =
        (west == 180.0 && east == -180.0) || (west == -180.0 && east == 180.0);
    if !(-180.0..=180.0).contains(&west)
        || !(-180.0..=180.0).contains(&east)
        || !(-90.0..=90.0).contains(&north)
        || !(-90.0..=90.0).contains(&south)
        || west == east
        || degenerate_antimeridian
        || south >= north
    {
        return Err("invalid bbox".into());
    }
    let zone = payload["timezone"].as_str().unwrap_or("UTC");
    zone.parse::<chrono_tz::Tz>()
        .map_err(|_| "timezone must be an IANA timezone")?;
    let phenomena = match payload.get("phenomena") {
        Some(rows) => rows
            .as_array()
            .filter(|v| !v.is_empty() && v.len() <= 2)
            .ok_or("phenomena requires dawn/dusk")?
            .clone(),
        None => payload
            .get("phenomenon")
            .map(|v| vec![v.clone()])
            .unwrap_or_else(|| vec![json!("dawn"), json!("dusk")]),
    };
    let mut phenomena = phenomena
        .iter()
        .map(|v| match v.as_str() {
            Some("dawn") => Ok("dawn"),
            Some("dusk") => Ok("dusk"),
            _ => Err("phenomenon must be dawn or dusk"),
        })
        .collect::<Result<Vec<_>, _>>()?;
    phenomena.sort_unstable();
    phenomena.dedup();
    let cfg = config(workspace);
    let mut cams_targets = BTreeSet::new();
    let mut cams_areas = Vec::new();
    let mut grouped = BTreeMap::<String, Value>::new();
    for model in ["gfs", "ec"] {
        let source = if model == "ec" { "ecmwf" } else { "gfs" };
        if !selected.contains(source)
            && !(selected.contains("cams") && (model == "gfs" || selected.contains("ecmwf")))
        {
            continue;
        }
        for date in &dates {
            for phenomenon in &phenomena {
                check(cancel)?;
                let mut request = json!({
                    "target_date":date,"phenomenon":phenomenon,"model_source":model,
                    "regions":["custom"],"custom_bounds":{"west":west,"east":east,"north":north,"south":south},
                    "custom_timezone_name":zone,"custom_label":payload["region_name"].as_str().unwrap_or(region),
                    "spacing_deg":payload.get("spacing_deg").cloned().unwrap_or(json!(2.0)),
                    "radius_km":payload.get("radius_km").cloned().unwrap_or(json!(1000.)),
                    "ecmwf_source":payload["ecmwf_source"].as_str().unwrap_or("ecmwf"),
                    "aerosol_source":payload["aerosol_source"].as_str().unwrap_or("cams"),
                });
                if known == Some(bbox) {
                    request["regions"] = json!([region]);
                    request.as_object_mut().unwrap().remove("custom_bounds");
                }
                let cycle = payload["cycle_overrides"]
                    .get(source)
                    .or_else(|| payload["cycle_overrides"].get(model))
                    .or_else(|| {
                        if selected.len() == 1 && selected.contains(source) {
                            payload.get("cycle")
                        } else {
                            None
                        }
                    });
                if let Some(cycle) = cycle {
                    request[format!("_{model}_cycle_override")] = cycle.clone();
                }
                let directory = tempfile::tempdir().map_err(|e| e.to_string())?;
                let file = directory.path().join("context.json");
                let input = json!({"payload":request,"regional":true,"fast":true,
                "now_utc":std::env::var("FIRESKY_CONTEXT_NOW_UTC").ok(),
                "config":{"gfs_cache_dir":cfg.gfs_cache_dir,"ecmwf_cache_dir":cfg.ecmwf_cache_dir,
                    "cams_cache_dir":cfg.cams_cache_dir,
                    "timezone_manifest":resources.timezone_manifest.clone(),
                    "city_catalog_path":resources.city_catalog.clone()}});
                serde_json::to_writer(fs::File::create(&file).map_err(|e| e.to_string())?, &input)
                    .map_err(|e| e.to_string())?;
                let context = desktop_context::resolve_path(&file)?;
                if selected.contains(source) {
                    let key = format!("{source}:{}", context["cycle"]);
                    let row = grouped.entry(key).or_insert_with(|| {
                        json!({
                            "source":source,"cycle_utc":context["cycle"],"required_hours":[],
                            "bboxes":[],"regions":[region],"cycle_source":"native-planner",
                        })
                    });
                    merge(&mut row["required_hours"], &context["hours"])?;
                    merge(&mut row["bboxes"], &json!([context["bbox"]]))?;
                }
                if selected.contains("cams") {
                    let area: [f64; 4] = serde_json::from_value(context["area"].clone())
                        .map_err(|e| format!("invalid planned CAMS area: {e}"))?;
                    for area in cams_data::split_areas(area)? {
                        let area = json!(area);
                        if !cams_areas.contains(&area) {
                            cams_areas.push(area);
                        }
                    }
                    for value in context["times"].as_array().ok_or("event times missing")? {
                        let (left, right, _) =
                            cams::bracket(value.as_str().ok_or("invalid event time")?)?;
                        cams_targets.extend([time_contract::iso(left), time_contract::iso(right)]);
                    }
                }
            }
        }
    }
    if selected.contains("cams") {
        grouped.insert("cams".into(),json!({"source":"cams","areas":cams_areas,
            "aerosol_source":payload["aerosol_source"].as_str().unwrap_or("cams"),
            "required_valid_times":cams_targets,"regions":[region],"cycle_source":"native-valid-time-resolution"}));
    }
    if let Some(raw) = payload.get("lead_hours") {
        let rows = raw
            .as_array()
            .filter(|v| !v.is_empty() && v.len() <= 64)
            .ok_or("lead_hours requires 1..64 hours")?;
        let hours = rows
            .iter()
            .map(|v| v.as_u64().filter(|v| *v <= 384).ok_or("invalid lead hour"))
            .collect::<Result<BTreeSet<_>, _>>()?;
        for value in grouped.values_mut().filter(|v| v["source"] != "cams") {
            value["required_hours"] = json!(hours);
        }
    }
    let datasets = grouped
        .into_values()
        .map(|mut value| {
            if value["source"] == "ecmwf" {
                value["ecmwf_source"] = json!(payload["ecmwf_source"].as_str().unwrap_or("ecmwf"));
            }
            if let Some(hours) = value["required_hours"].as_array_mut() {
                hours.sort_by_key(|v| v.as_u64().unwrap_or(0));
            }
            let hours = value["required_hours"].as_array().map_or(0, Vec::len);
            let regions = value["bboxes"].as_array().map_or(1, Vec::len);
            value["required_hours_count"] = json!(hours);
            value["region_count"] = json!(regions);
            value["expected_files"] = json!(if value["source"] == "cams" {
                value["required_valid_times"].as_array().map_or(0, Vec::len)
                    * value["areas"].as_array().map_or(0, Vec::len)
            } else if value["source"] == "ecmwf" {
                hours
            } else {
                hours * regions
            });
            value
        })
        .collect::<Vec<_>>();
    Ok(Plan {
        public: json!({"contract":"rust-data-plan-v1","runtime_python_used":false,
        "region":{"code":region,"name":payload["region_name"].as_str().unwrap_or(region),"bbox":bbox,"timezone":zone},
        "target_dates":dates,"phenomena":phenomena,"computed_at":time_contract::iso(now()),"datasets":datasets}),
        selected,
    })
}

fn merge(target: &mut Value, values: &Value) -> Result<(), String> {
    let rows = target.as_array_mut().ok_or("plan field is not an array")?;
    for value in values.as_array().ok_or("context field is not an array")? {
        if !rows.contains(value) {
            rows.push(value.clone());
        }
    }
    Ok(())
}

pub fn requirements(workspace: &Path, payload: &Value) -> Result<Value, String> {
    let cancel = AtomicBool::new(false);
    let plan = plan(workspace, payload, &cancel)?;
    let mut response = plan.public.clone();
    response["readiness"] = verify_plan(workspace, &plan.public, &cancel)?;
    response["ready"] = response["readiness"]["ready"].clone();
    Ok(response)
}

// Re-read the frozen plan, never select a new cycle after a download. Inventory
// counts alone do not prove that a file can be consumed for this request.
fn verify_plan(workspace: &Path, plan: &Value, cancel: &AtomicBool) -> Result<Value, String> {
    let cfg = config(workspace);
    let mut datasets = Vec::new();
    let mut codes = None;
    for dataset in plan["datasets"]
        .as_array()
        .ok_or("planned datasets missing")?
    {
        check(cancel)?;
        let source = dataset["source"].as_str().ok_or("planned source missing")?;
        let mut missing = Vec::new();
        let mut errors = Vec::new();
        let mut verified = 0usize;
        let expected = dataset["expected_files"].as_u64().unwrap_or(0);
        let mut verify = |key: Value, resolved: Result<Value, String>| -> Result<(), String> {
            check(cancel)?;
            match resolved {
                Ok(value) => {
                    let paths = value["files"].as_array().ok_or("resolved files missing")?;
                    if paths.len() != 1 {
                        missing.push(key);
                        return Ok(());
                    }
                    let file = &paths[0];
                    let path = file["path"].as_str().ok_or("resolved file path missing")?;
                    let result = (|| match source {
                        "gfs" => {
                            let cycle = time_contract::parse_utc(
                                dataset["cycle_utc"]
                                    .as_str()
                                    .ok_or("planned cycle missing")?,
                            )?;
                            let hour = key["hour"].as_u64().ok_or("planned hour missing")? as u32;
                            if codes.is_none() {
                                codes = Some(crate::grib::EcCodes::load()?);
                            }
                            codes
                                .as_ref()
                                .unwrap()
                                .decode_gfs_frame(
                                    Path::new(path),
                                    4096,
                                    5_000_000,
                                    Some((cycle, hour)),
                                )
                                .map(|_| ())
                        }
                        "cams" => {
                            let file_path = Path::new(path);
                            let decoder = if crate::geos_fp::is_fsopt_file(file_path) {
                                None
                            } else {
                                if codes.is_none() {
                                    codes = Some(crate::grib::EcCodes::load()?);
                                }
                                codes.as_ref()
                            };
                            cams::decode_path(decoder, file_path).and_then(
                                |optics| {
                                    let requested = time_contract::parse_utc(
                                        key["valid_time"]
                                            .as_str()
                                            .ok_or("planned CAMS time missing")?,
                                    )?;
                                    if optics.valid_time != requested {
                                        return Err(
                                            "CAMS GRIB valid time differs from the frozen request"
                                                .into(),
                                        );
                                    }
                                    Ok(())
                                },
                            )
                        }
                        // EC resolve validates its complete basic bundle and clock.
                        "ecmwf" => Ok(()),
                        _ => return Err("unknown planned source".into()),
                    })();
                    match result {
                        Ok(()) => verified += 1,
                        Err(error) => errors.push(json!({"requirement":key,"error":error})),
                    }
                }
                Err(error) => errors.push(json!({"requirement":key,"error":error})),
            }
            Ok(())
        };
        if source == "cams" {
            for area in dataset["areas"]
                .as_array()
                .ok_or("planned CAMS areas missing")?
            {
                for target in dataset["required_valid_times"]
                    .as_array()
                    .ok_or("planned CAMS times missing")?
                {
                    let params = serde_json::from_value(json!({
                        "operation":"resolve","cache_dir":cfg.cams_cache_dir,"area":area,
                        "target_utcs":[target],"allow_download":false,"require_exact_valid_time":true,
                        "aerosol_source":dataset["aerosol_source"].as_str().unwrap_or("cams"),
                    })).map_err(|e| e.to_string())?;
                    verify(
                        json!({"area":area,"valid_time":target}),
                        cams_data::execute(params),
                    )?;
                }
            }
        } else {
            let boxes = dataset["bboxes"]
                .as_array()
                .ok_or("planned bboxes missing")?;
            for bbox in boxes
                .iter()
                .take(if source == "ecmwf" { 1 } else { boxes.len() })
            {
                for hour in dataset["required_hours"]
                    .as_array()
                    .ok_or("planned hours missing")?
                {
                    let resolved = if source == "gfs" {
                        serde_json::from_value(json!({
                            "operation":"resolve","cache_dir":cfg.gfs_cache_dir,
                            "cycle_utc":dataset["cycle_utc"],"forecast_hours":[hour],
                            "bbox":bbox,"allow_download":false,
                        }))
                        .map_err(|e| e.to_string())
                        .and_then(gfs_data::execute)
                    } else {
                        serde_json::from_value(json!({
                            "operation":"resolve","config":{"cache_dir":cfg.ecmwf_cache_dir,
                                "source":dataset["ecmwf_source"].as_str().unwrap_or("ecmwf")},
                            "cycle_utc":dataset["cycle_utc"],"forecast_hour":hour,"allow_download":false,
                        })).map_err(|e| e.to_string()).and_then(ecmwf_data::execute)
                    };
                    verify(json!({"bbox":bbox,"hour":hour}), resolved)?;
                }
            }
        }
        let ready =
            expected > 0 && verified as u64 == expected && missing.is_empty() && errors.is_empty();
        datasets.push(json!({
            "source":source,"cycle_utc":dataset["cycle_utc"],"ready":ready,"prepared":ready,
            "required_files":expected,"verified_files":verified,
            "missing_count":missing.len()+errors.len(),"missing":missing,"errors":errors,
        }));
    }
    let ready = !datasets.is_empty() && datasets.iter().all(|row| row["ready"] == true);
    Ok(json!({
        "contract":"rust-data-readiness-v1","ready":ready,"prepared":ready,
        "checked_at":time_contract::iso(now()),"frozen_plan":true,"network_enabled":false,
        "datasets":datasets,
    }))
}

pub fn fetch(workspace: &Path, payload: &Value, cancel: &AtomicBool) -> Result<Value, String> {
    let start = Instant::now();
    let plan = plan(workspace, payload, cancel)?;
    let cfg = config(workspace);
    let mut results = Vec::new();
    let mut errors = Vec::new();
    for dataset in plan.public["datasets"].as_array().unwrap() {
        let source = dataset["source"].as_str().ok_or("model missing")?;
        if source == "cams" {
            continue;
        }
        let bboxes = dataset["bboxes"]
            .as_array()
            .ok_or("planned bboxes missing")?;
        for bbox in bboxes
            .iter()
            .take(if source == "ecmwf" { 1 } else { bboxes.len() })
        {
            for hour in dataset["required_hours"]
                .as_array()
                .ok_or("planned hours missing")?
            {
                check(cancel)?;
                let outcome = if source == "gfs" {
                    serde_json::from_value(
                        json!({"operation":"resolve","cache_dir":cfg.gfs_cache_dir,
                        "cycle_utc":dataset["cycle_utc"],"forecast_hours":[hour],"bbox":bbox,
                        "allow_download":true}),
                    )
                    .map_err(|e| e.to_string())
                    .and_then(gfs_data::execute)
                } else {
                    serde_json::from_value(json!({"operation":"resolve",
                        "config":{"cache_dir":cfg.ecmwf_cache_dir,"source":dataset["ecmwf_source"]},
                        "cycle_utc":dataset["cycle_utc"],"forecast_hour":hour,"allow_download":true}))
                        .map_err(|e|e.to_string()).and_then(ecmwf_data::execute)
                };
                match outcome {
                    Ok(value) if source == "gfs" && value["complete"] != true => {
                        errors.push(format!("gfs inputs incomplete: {}", value["missing_hours"]))
                    }
                    Ok(value) => results.push(json!({"source":source,"result":value})),
                    Err(error) => errors.push(format!("{source} hour {hour}: {error}")),
                }
            }
        }
    }
    if plan.selected.contains("cams") {
        let dataset = plan.public["datasets"]
            .as_array()
            .unwrap()
            .iter()
            .find(|v| v["source"] == "cams")
            .unwrap();
        for area in dataset["areas"].as_array().unwrap() {
            check(cancel)?;
            let params = cams_data::Params {
                operation: "resolve".into(),
                cache_dir: cfg.cams_cache_dir.clone(),
                area: area.clone(),
                target_utcs: serde_json::from_value(dataset["required_valid_times"].clone())
                    .map_err(|e| e.to_string())?,
                target_utc: None,
                config: ads::Config::default(),
                allow_download: true,
                require_exact_valid_time: true,
                refresh_before_cycle: None,
                all_leads: false,
                model_bbox: None,
                aerosol_source: dataset["aerosol_source"].as_str().map(str::to_owned),
            };
            match cams_data::execute(params) {
                Ok(value)
                    if value["missing_valid_times"]
                        .as_array()
                        .is_some_and(Vec::is_empty) =>
                {
                    results.push(json!({"source":"cams","result":value}))
                }
                Ok(value) => errors.push(format!(
                    "cams inputs incomplete: {}",
                    value["missing_valid_times"]
                )),
                Err(error) => errors.push(format!("cams: {error}")),
            }
        }
    }
    let readiness = verify_plan(workspace, &plan.public, cancel)?;
    let ready = readiness["ready"] == true;
    if !ready {
        errors.push(
            "downloaded inputs do not cover the frozen request; inspect readiness.datasets".into(),
        );
    }
    let mut response = json!({"contract":"rust-data-fetch-v1","runtime_python_used":false,
        "status":if errors.is_empty() && ready {"done"}else{"failed"},"sources":plan.selected,
        "ready":ready,"prepared":ready,"readiness":readiness,
        "completed":results.len(),"total":results.len()+errors.len(),"results":results,"errors":errors,
        "elapsed_seconds":start.elapsed().as_secs_f64(),"plan":plan.public});
    // Retention is deliberately applied only after every requested input has
    // completed successfully. A partial/failed download must never evict an
    // older usable cycle.
    if response["status"] == "done" {
        if payload.get("retention").is_some() {
            response["retention"] = crate::cache_retention::apply(workspace, payload)?;
        } else if let Some(retention) = crate::retention_settings::apply_configured(workspace)? {
            response["retention"] = retention;
        }
    }
    Ok(response)
}

pub fn prune(workspace: &Path, payload: &Value) -> Result<Value, String> {
    if !payload.is_object() {
        return Err("prune request requires an object".into());
    }
    if payload.get("retention").is_some() || payload["mode"] == "retention" {
        return crate::cache_retention::apply(workspace, payload);
    }
    let default = path(workspace, "FIRESKY_CACHE_ROOT", ".cache");
    let cfg = config(workspace);
    let root = if let Some(raw) = payload.get("cache_root") {
        let raw = raw
            .as_str()
            .filter(|v| !v.is_empty())
            .ok_or("cache_root must be a path")?;
        let value = Path::new(raw);
        if value.is_absolute() {
            value.to_owned()
        } else {
            workspace.join(value)
        }
    } else {
        default.clone()
    };
    if root.exists() {
        let canonical = root.canonicalize().map_err(|e| e.to_string())?;
        let allowed = [
            default,
            cfg.gfs_cache_dir,
            cfg.ecmwf_cache_dir,
            cfg.cams_cache_dir,
        ];
        if !allowed
            .iter()
            .any(|value| value.canonicalize().is_ok_and(|value| value == canonical))
        {
            return Err("HTTP prune is restricted to configured cache directories".into());
        }
    }
    let days = payload
        .get("max_age_days")
        .map(|v| {
            v.as_f64()
                .filter(|v| v.is_finite() && *v >= 0. && *v <= 3650.)
                .ok_or("max_age_days must be in 0..3650")
        })
        .transpose()?
        .unwrap_or(2.);
    let cutoff = SystemTime::now()
        .checked_sub(std::time::Duration::from_secs_f64(days * 86400.))
        .ok_or("cache age exceeds system clock")?;
    let trash = root.join(".trash").join(format!(
        "pruned-{}-{}",
        now().timestamp_nanos_opt().unwrap_or(0),
        std::process::id()
    ));
    let mut pending = vec![root.clone()];
    let mut removed = Vec::new();
    let mut failed = Vec::new();
    let mut scanned = 0usize;
    let mut bytes = 0u64;
    while let Some(directory) = pending.pop() {
        let entries = match fs::read_dir(&directory) {
            Ok(entries) => entries,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error.to_string()),
        };
        for entry in entries {
            let entry = entry.map_err(|e| e.to_string())?;
            scanned += 1;
            if scanned > 1_000_000 {
                return Err("prune directory exceeds one million entries".into());
            }
            let kind = entry.file_type().map_err(|e| e.to_string())?;
            if kind.is_symlink() {
                continue;
            }
            let file = entry.path();
            if kind.is_dir() {
                if entry.file_name() != ".trash" {
                    pending.push(file);
                }
                continue;
            }
            if !kind.is_file()
                || !matches!(
                    file.extension().and_then(|v| v.to_str()),
                    Some("grib" | "grib2" | "idx" | "json" | "gz")
                )
            {
                continue;
            }
            let metadata = entry.metadata().map_err(|e| e.to_string())?;
            if metadata.modified().map_err(|e| e.to_string())? > cutoff {
                continue;
            }
            let target = trash.join(file.strip_prefix(&root).map_err(|e| e.to_string())?);
            let outcome = fs::create_dir_all(target.parent().unwrap())
                .and_then(|_| fs::rename(&file, &target));
            match outcome {
                Ok(()) => {
                    bytes += metadata.len();
                    removed.push(format!("{} -> {}", file.display(), target.display()));
                }
                Err(error) => failed.push(format!("{}: {error}", file.display())),
            }
        }
    }
    Ok(
        json!({"message":"prune completed","scanned":scanned,"removed":removed,"failed":failed,
        "freed_mb":bytes as f64/1048576.,"reclaimed_disk_mb":0,"trash_root":trash,
        "runtime_python_used":false}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    fn gfs_plan() -> Value {
        json!({"datasets":[{
            "source":"gfs","cycle_utc":"2026-11-11T00:00:00Z",
            "bboxes":[[120.,120.25,30.25,30.]],"required_hours":[3],"expected_files":1,
        }]})
    }
    #[test]
    fn inventory_empty_requirements_are_not_readiness() {
        let dir = tempfile::tempdir().unwrap();
        let inventory = status(dir.path()).unwrap();
        assert!(inventory["datasets"].as_array().unwrap().iter().all(|row| {
            row["prepared"] == false
                && row["required_hours_count"] == 0
                && row["missing_hours"] == json!([])
        }));
        let readiness = verify_plan(dir.path(), &gfs_plan(), &AtomicBool::new(false)).unwrap();
        assert_eq!(readiness["ready"], false);
        assert_eq!(readiness["network_enabled"], false);
        let row = &readiness["datasets"][0];
        assert_eq!(row["required_files"], 1);
        assert_eq!(row["verified_files"], 0);
        assert_eq!(
            row["missing"][0],
            json!({"bbox":[120.,120.25,30.25,30.],"hour":3})
        );
        assert_eq!(row["cycle_utc"], "2026-11-11T00:00:00Z");
    }
    #[test]
    fn empty_or_zero_requirement_plan_is_never_ready() {
        let dir = tempfile::tempdir().unwrap();
        let cancel = AtomicBool::new(false);
        assert_eq!(
            verify_plan(dir.path(), &json!({"datasets":[]}), &cancel).unwrap()["ready"],
            false
        );
        let mut plan = gfs_plan();
        plan["datasets"][0]["required_hours"] = json!([]);
        plan["datasets"][0]["expected_files"] = json!(0);
        assert_eq!(
            verify_plan(dir.path(), &plan, &cancel).unwrap()["ready"],
            false
        );
        assert!(verify_plan(dir.path(), &plan, &AtomicBool::new(true)).is_err());
    }
    #[test]
    fn indexed_nonempty_corrupt_gfs_is_not_ready() {
        let dir = tempfile::tempdir().unwrap();
        let root = config(dir.path()).gfs_cache_dir;
        fs::create_dir_all(&root).unwrap();
        let url = gfs_data::execute(
            serde_json::from_value(json!({
                "operation":"url","cycle_utc":"2026-11-11T00:00:00Z","forecast_hour":3,
                "bbox":[120.,120.25,30.25,30.],
            }))
            .unwrap(),
        )
        .unwrap();
        fs::write(root.join("corrupt.grib2"), b"this is not a GRIB file").unwrap();
        fs::write(
            root.join("corrupt.grib2.json"),
            serde_json::to_vec(&json!({
                "model":"gfs","cycle":"2026-11-11T00:00:00Z","forecast_hour":3,
                "bbox":[120.,120.25,30.25,30.],"file":"corrupt.grib2","url":url["url"],
            }))
            .unwrap(),
        )
        .unwrap();
        let readiness = verify_plan(dir.path(), &gfs_plan(), &AtomicBool::new(false)).unwrap();
        assert_eq!(readiness["ready"], false);
        assert_eq!(readiness["datasets"][0]["verified_files"], 0);
        assert_eq!(
            readiness["datasets"][0]["errors"].as_array().unwrap().len(),
            1
        );
    }
    #[test]
    fn prune_preserves_history_locks_and_symlinks() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join(".cache");
        fs::create_dir_all(&root).unwrap();
        for name in ["old.grib2", "history.sqlite3", "live.lock"] {
            fs::write(root.join(name), b"x").unwrap();
        }
        #[cfg(unix)]
        std::os::unix::fs::symlink(root.join("old.grib2"), root.join("link.grib2")).unwrap();
        let result = prune(dir.path(), &json!({"max_age_days":0})).unwrap();
        assert_eq!(result["removed"].as_array().unwrap().len(), 1);
        assert!(root.join("history.sqlite3").is_file());
        assert!(root.join("live.lock").is_file());
        assert_eq!(result["reclaimed_disk_mb"], 0);
        assert!(prune(dir.path(), &json!({"cache_root":dir.path()})).is_err());
        assert!(prune(dir.path(), &json!({"max_age_days":-1})).is_err());
    }
    #[test]
    fn invalid_plan_rejected_before_network() {
        let dir = tempfile::tempdir().unwrap();
        for payload in [
            json!({"sources":["unknown"]}),
            json!({"bbox":[180,-180,90,-90]}),
            json!({"target_dates":["2026-2-1"]}),
            json!({"days":0}),
        ] {
            assert!(requirements(dir.path(), &payload).is_err());
        }
    }
    #[test]
    fn native_plan_contains_union_of_event_hours() {
        let dir = tempfile::tempdir().unwrap();
        let plan = requirements(
            dir.path(),
            &json!({
                "region":"tiny","bbox":[120.,120.25,30.25,30.],"timezone":"Asia/Shanghai",
                "target_dates":["2026-11-12"],"phenomena":["dawn","dusk"],"sources":["gfs"],
                "cycle":"2026-11-11T00:00:00Z",
            }),
        )
        .unwrap();
        assert_eq!(plan["runtime_python_used"], false);
        assert_eq!(plan["datasets"].as_array().unwrap().len(), 1);
        let hours = plan["datasets"][0]["required_hours"].as_array().unwrap();
        assert!(!hours.is_empty());
        assert!(hours.windows(2).all(|v| v[0].as_u64() < v[1].as_u64()));
        assert_eq!(
            plan["datasets"][0]["cycle_utc"],
            "2026-11-11T00:00:00+00:00"
        );
    }
    #[test]
    fn default_named_region_preserves_its_source_domain() {
        let dir = tempfile::tempdir().unwrap();
        let plan = requirements(
            dir.path(),
            &json!({
                "sources":["gfs"],"target_date":"2026-11-12",
                "cycle":"2026-11-11T00:00:00Z","timezone":"Asia/Shanghai",
            }),
        )
        .unwrap();
        assert_eq!(plan["region"]["code"], "east-asia");
        assert_eq!(
            plan["datasets"][0]["bboxes"],
            json!([[63., 155., 60., 12.]])
        );
        assert!(!plan["datasets"][0]["required_hours"]
            .as_array()
            .unwrap()
            .is_empty());
    }
}
