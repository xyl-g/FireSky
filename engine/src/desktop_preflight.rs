//! Desktop cache checks for resolved event plans. This does not create sessions.
use crate::{
    cams_cache, cams_data,
    desktop_inventory::{self, AreaReader},
    gfs_data::{self, GfsClient},
    time_contract,
};
use chrono::{DateTime, Timelike, Utc};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
    time::SystemTime,
};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Plan {
    model_source: String,
    cycle_utc: String,
    event_start: String,
    bbox: [f64; 4],
    area: Value,
    hours: Vec<u32>,
    cams_valid_times: Vec<String>,
    point_count: usize,
    terrain_pending: bool,
    #[serde(default)]
    notes: Vec<String>,
    compute_region: Option<String>,
}
#[derive(Default, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Publication {
    cycle: Option<String>,
    label: Option<String>,
    verified: bool,
    error: Option<String>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    plans: Vec<Plan>,
    #[serde(default)]
    config: desktop_inventory::Config,
    #[serde(default)]
    publications: BTreeMap<String, Publication>,
    #[serde(default)]
    fast: bool,
    /// 日期策略（v1.8.0）：runtime 在入口计算并传入；"past" 时 datasets
    /// 标注 cache-only（旧日期仅本地缓存），"in-window" 正常。
    #[serde(default)]
    date_policy: Option<String>,
}

fn check(cancel: &AtomicBool) -> Result<(), String> {
    if cancel.load(Ordering::Acquire) {
        Err("desktop preflight cancelled".into())
    } else {
        Ok(())
    }
}
fn iso(time: DateTime<Utc>) -> String {
    let base = time.format("%Y-%m-%dT%H:%M:%S");
    if time.timestamp_subsec_micros() == 0 {
        format!("{base}+00:00")
    } else {
        format!("{base}.{:06}+00:00", time.timestamp_subsec_micros())
    }
}
struct CamsArea {
    bounds: [f64; 4],
    key: String,
    tuple: String,
}
struct ResolvedPlan {
    input: Plan,
    cycle: DateTime<Utc>,
    event_start: DateTime<Utc>,
    areas: Vec<CamsArea>,
    targets: BTreeSet<DateTime<Utc>>,
}
impl ResolvedPlan {
    fn new(input: Plan) -> Result<Self, String> {
        if !matches!(input.model_source.as_str(), "gfs" | "ec")
            || !(1..=100_000).contains(&input.point_count)
            || input.hours.is_empty()
            || input.hours.len() > 256
            || input.hours.iter().any(|h| *h > 10_000)
            || input.cams_valid_times.is_empty()
            || input.cams_valid_times.len() > 256
            || input.notes.len() > 32
            || input.notes.iter().any(|v| v.len() > 4096)
        {
            return Err("invalid bounded resolved desktop plan".into());
        }
        let bbox = input.bbox;
        if bbox.iter().any(|v| !v.is_finite())
            || bbox[0] < -180.0
            || bbox[0] > 180.0
            || bbox[1] < -180.0
            || bbox[1] > 180.0
            || bbox[0] == bbox[1]
            || (bbox[0] == 180.0 && bbox[1] == -180.0)
            || bbox[2] > 90.0
            || bbox[2] < -90.0
            || bbox[3] < -90.0
            || bbox[3] > bbox[2]
        {
            return Err("invalid desktop plan bbox".into());
        }
        let (bounds, key, tuple) = cams_data::area_identity(input.area.clone())?;
        let split = cams_data::split_areas(bounds)?;
        let areas = if split.len() == 1 && split[0] == bounds {
            vec![CamsArea { bounds, key, tuple }]
        } else {
            split
                .into_iter()
                .map(|bounds| {
                    let (bounds, key, tuple) = cams_data::area_identity(json!(bounds))?;
                    Ok(CamsArea { bounds, key, tuple })
                })
                .collect::<Result<Vec<_>, String>>()?
        };
        let cycle = time_contract::parse_utc(&input.cycle_utc)?;
        let event_start = time_contract::parse_utc(&input.event_start)?;
        if event_start < cycle {
            return Err("desktop event starts before its resolved cycle".into());
        }
        let targets = input
            .cams_valid_times
            .iter()
            .map(|v| {
                let time = time_contract::parse_utc(v)?;
                if time.hour() % 3 != 0
                    || time.minute() != 0
                    || time.second() != 0
                    || time.timestamp_subsec_nanos() != 0
                {
                    return Err("CAMS requirements must be exact three-hour brackets".into());
                }
                Ok(time)
            })
            .collect::<Result<BTreeSet<_>, String>>()?;
        Ok(Self {
            input,
            cycle,
            event_start,
            areas,
            targets,
        })
    }
}

struct CamsCatalog {
    candidates: Vec<cams_cache::Selection>,
    areas: AreaReader,
    codes: Option<crate::grib::EcCodes>,
    /// Memoized true-longitude-arc coverage for wrapped multi-area candidates.
    arc_checks: BTreeMap<(PathBuf, [u64; 4]), bool>,
}
impl CamsCatalog {
    fn new(paths: Vec<PathBuf>) -> Self {
        let candidates = paths.iter().filter_map(|p| cams_cache::parse(p)).collect();
        Self {
            candidates,
            areas: AreaReader::default(),
            codes: None,
            arc_checks: BTreeMap::new(),
        }
    }
    /// Whether the candidate's actual longitude arc (not its normalized
    /// min/max) covers `bounds`. ADS can answer a wrapped part request with a
    /// global grid stored under the part's hash; the min/max then spans both
    /// parts and cannot prove coverage of either. Read the unique longitudes
    /// and require a contiguous 0.4-degree-spaced run spanning the part.
    fn arc_covers(&mut self, path: &Path, bounds: [f64; 4], cancel: &AtomicBool) -> bool {
        let key = (path.to_owned(), bounds.map(|v| v.to_bits()));
        if let Some(&cached) = self.arc_checks.get(&key) {
            return cached;
        }
        let result = self.compute_arc_covers(path, bounds, cancel);
        self.arc_checks.insert(key, result);
        result
    }
    fn compute_arc_covers(&mut self, path: &Path, bounds: [f64; 4], cancel: &AtomicBool) -> bool {
        if check(cancel).is_err() {
            return false;
        }
        // Shared with the download/resolve selector so both layers apply the
        // same true-arc coverage contract for wrapped (antimeridian) plans.
        cams_cache::arc_covers(path, bounds)
    }
    fn select(
        &mut self,
        plan: &ResolvedPlan,
        area: &CamsArea,
        fast: bool,
        cancel: &AtomicBool,
    ) -> Result<BTreeMap<DateTime<Utc>, cams_cache::Selection>, String> {
        let suffix = format!("-{}.grib", area.key);
        let mut candidates: Vec<_> = self
            .candidates
            .iter()
            .filter(|v| plan.targets.contains(&v.valid_time))
            .map(|v| {
                (
                    v.clone(),
                    v.path
                        .file_name()
                        .is_some_and(|n| n.to_string_lossy().ends_with(&suffix)),
                )
            })
            .collect();
        candidates.sort_by(|(a, ae), (b, be)| b.run_time_utc.cmp(&a.run_time_utc).then(be.cmp(ae)));
        // Wrapped groups share the physical +/-180 boundary column. Gather
        // the newest sibling-part candidate per valid time so the seam bridge
        // can close the (179.6, 180] gap exactly like the scorer's merge.
        let sibling_groups: BTreeMap<DateTime<Utc>, Vec<std::path::PathBuf>> = if plan.areas.len()
            > 1
        {
            let mut newest: BTreeMap<(DateTime<Utc>, String), (DateTime<Utc>, std::path::PathBuf)> =
                BTreeMap::new();
            for (candidate, exact) in &candidates {
                if *exact {
                    continue;
                }
                let name = candidate
                    .path
                    .file_name()
                    .map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_default();
                let sibling = plan.areas.iter().any(|other| {
                    other.key != area.key && name.ends_with(&format!("-{}.grib", other.key))
                });
                if !sibling {
                    continue;
                }
                let entry = newest
                    .entry((candidate.valid_time, name))
                    .or_insert((candidate.run_time_utc, candidate.path.clone()));
                if candidate.run_time_utc > entry.0 {
                    *entry = (candidate.run_time_utc, candidate.path.clone());
                }
            }
            let mut groups: BTreeMap<DateTime<Utc>, Vec<std::path::PathBuf>> = BTreeMap::new();
            for ((time, _), (_, path)) in newest {
                groups.entry(time).or_default().push(path);
            }
            groups
        } else {
            BTreeMap::new()
        };
        let mut selected = BTreeMap::new();
        for (candidate, exact) in candidates {
            check(cancel)?;
            if selected.contains_key(&candidate.valid_time) {
                continue;
            }
            let Ok(before) = fs::metadata(&candidate.path) else {
                continue;
            };
            if !before.is_file() || before.len() == 0 || before.len() > 512 * 1024 * 1024 {
                continue;
            }
            if !exact {
                let Some([north, west, south, east]) = self.areas.read(&candidate.path, cancel)
                else {
                    continue;
                };
                let tolerance = 0.2 + 1e-9;
                if north + tolerance < area.bounds[0]
                    || west - tolerance > area.bounds[1]
                    || south - tolerance > area.bounds[2]
                    || east + tolerance < area.bounds[3]
                {
                    continue;
                }
                // A wrapped plan splits into two parts, but ADS may answer a
                // part request with a broader (even global) longitude grid that
                // is stored under that part's area hash. The normalized min/max
                // bounds then span both parts, so the sibling part must still
                // accept the file when its true longitude arc covers the part.
                // This mirrors the download path's coverage contract and keeps
                // preflight from reporting spurious missing CAMS targets.
                if plan.areas.len() > 1 && !self.arc_covers(&candidate.path, area.bounds, cancel) {
                    continue;
                }
            }
            if !fast && !cams_cache::optics_valid(&candidate.path, &mut self.codes) {
                continue;
            }
            // Enforce the scorer's fail-closed model-grid coverage contract so
            // a newer run whose tile misses the padded model boundary falls
            // through to the next candidate instead of failing the forecast.
            if !fast {
                // The assessment maps CAMS onto the padded prepared bbox, not
                // the raw request bbox; enforce coverage on the same grid.
                let prepared = gfs_data::canonical_bbox(plan.input.bbox, "0p25")?;
                let (lats, lons) = cams_cache::model_axes(prepared);
                // A wrapped plan splits into parts; each part's tile only has
                // to cover the model cells inside its own longitude arc.
                let (part_west, part_east) = if area.bounds[1] <= area.bounds[3] {
                    (area.bounds[1], area.bounds[3])
                } else {
                    (area.bounds[1], area.bounds[3] + 360.0)
                };
                let part_lons: Vec<f64> = lons
                    .iter()
                    .copied()
                    .filter(|&lon| {
                        let unwrapped = if lon < part_west { lon + 360.0 } else { lon };
                        unwrapped >= part_west && unwrapped <= part_east
                    })
                    .collect();
                if part_lons.len() < 2 {
                    continue;
                }
                let coverage = cams_cache::ModelCoverage {
                    lats: &lats,
                    lons: &part_lons,
                };
                let codes = match &mut self.codes {
                    Some(codes) => codes,
                    None => self.codes.insert(crate::grib::EcCodes::load()?),
                };
                let mut group = vec![candidate.path.clone()];
                if let Some(siblings) = sibling_groups.get(&candidate.valid_time) {
                    group.extend(siblings.iter().cloned());
                }
                if !cams_cache::model_grid_covers_group(&group, codes, &coverage) {
                    continue;
                }
            }
            let Ok(after) = fs::metadata(&candidate.path) else {
                continue;
            };
            if !after.is_file()
                || after.len() != before.len()
                || after.modified().ok() != before.modified().ok()
            {
                continue;
            }
            selected.insert(candidate.valid_time, candidate.clone());
            if selected.len() == plan.targets.len() {
                break;
            }
        }
        check(cancel)?;
        Ok(selected)
    }
}

fn scan(
    source: &str,
    root: &Path,
    local: &mut BTreeMap<String, Value>,
    cancel: &AtomicBool,
) -> Result<(), String> {
    if !local.contains_key(source) {
        local.insert(
            source.into(),
            desktop_inventory::local_snapshot(source, root, cancel)?.0,
        );
    }
    Ok(())
}
fn report(
    plan: &ResolvedPlan,
    config: &desktop_inventory::Config,
    local: &BTreeMap<String, Value>,
    catalog: &mut CamsCatalog,
    publications: &BTreeMap<String, Publication>,
    fast: bool,
    cancel: &AtomicBool,
    date_policy: Option<&str>,
) -> Result<Value, String> {
    let past = date_policy == Some("past");
    let source = if plan.input.model_source == "ec" {
        "ecmwf"
    } else {
        "gfs"
    };
    let cycle_label = plan.cycle.format("%Y-%m-%d %HZ").to_string();
    let mut missing = Vec::new();
    let hours: BTreeSet<_> = plan.input.hours.iter().copied().collect();
    if source == "gfs" {
        let client = GfsClient::new(gfs_data::Config {
            cache_dir: config.gfs_cache_dir.clone(),
            resolution: "0p25".into(),
            region: plan.input.compute_region.clone(),
            timeout_seconds: 45.0,
            lock_timeout_seconds: 300.0,
            download_attempts: 4,
        })?;
        for hour in hours {
            check(cancel)?;
            // Wrapped envelopes are served as two longitude segments; the
            // hour is ready only when every segment has a covering cache.
            let mut covered = true;
            for part in gfs_data::request_bboxes(plan.input.bbox, "0p25")? {
                let url = gfs_data::build_url(plan.cycle, hour, Some(part), "0p25", false)?;
                if client
                    .find(plan.cycle, hour, Some(part), Some(&url))?
                    .is_none()
                {
                    covered = false;
                    break;
                }
            }
            if !covered {
                missing.push(hour);
            }
        }
    } else {
        let matched = local[source]["cycles"].as_object().and_then(|cycles| {
            cycles
                .values()
                .find(|c| c["label"].as_str() == Some(cycle_label.as_str()))
        });
        for hour in hours {
            check(cancel)?;
            if !matched.is_some_and(|c| {
                c["hours"]
                    .as_array()
                    .is_some_and(|a| a.iter().any(|h| h.as_u64() == Some(u64::from(hour))))
            }) {
                missing.push(hour);
            }
        }
    }
    let mut missing_cams = BTreeSet::new();
    let mut missing_cams_keys = BTreeSet::new();
    let mut cams_cycles = BTreeSet::new();
    for area in &plan.areas {
        let selected = catalog.select(plan, area, fast, cancel)?;
        for target in &plan.targets {
            if !selected.contains_key(target) {
                missing_cams.insert(*target);
                missing_cams_keys.insert(format!("{}:{}", area.tuple, iso(*target)));
            }
        }
        cams_cycles.extend(
            selected
                .values()
                .map(|s| s.run_time_utc.format("%Y-%m-%d %HZ").to_string()),
        );
    }
    let requested_cams = if cams_cycles.is_empty() {
        "无可用事件数据".into()
    } else {
        cams_cycles.iter().cloned().collect::<Vec<_>>().join(" / ")
    };
    let mut datasets = Vec::new();
    for (key, requested, missing_count) in [
        (source, cycle_label, missing.len()),
        ("cams", requested_cams, missing_cams_keys.len()),
    ] {
        let publication = publications.get(key);
        let verified = publication.is_some_and(|p| p.verified);
        let label = publication
            .and_then(|p| p.label.as_deref())
            .unwrap_or("未核验");
        let latest = verified
            && missing_count == 0
            && if key == "cams" {
                cams_cycles.len() == 1 && cams_cycles.contains(label)
            } else {
                requested == label
            };
        datasets.push(json!({
            "source":key,"local_files":local[key]["file_count"],
            "policy":if past {"past-local-cache-only"} else {"in-window"},
            "request_cycle":requested,"latest_local_cycle":local[key]["latest_cycle"],
            "latest_available_cycle":label,
            "latest_available_cycle_utc":publication.and_then(|p|p.cycle.as_deref()),
            "verified":verified,"is_latest":latest,"missing_count":missing_count,
            "missing_hours":if key=="cams" { vec![] } else { missing.clone() },
            "missing_valid_times":if key=="cams" {missing_cams.iter().map(|t|iso(*t)).collect::<Vec<_>>()} else {vec![]},
            "missing_keys":if key=="cams" {missing_cams_keys.iter().cloned().collect::<Vec<_>>()} else {vec![]},
            "ready":missing_count==0,
        }));
    }
    let cams_cycle = publications
        .get("cams")
        .filter(|p| p.verified)
        .and_then(|p| p.cycle.as_deref());
    check(cancel)?;
    Ok(json!({
        "ready":datasets.iter().all(|v|v["ready"]==true),"datasets":datasets,
        "checked_at":iso(DateTime::<Utc>::from(SystemTime::now())),
        "point_count":plan.input.point_count,"terrain_pending":plan.input.terrain_pending,
        "bbox":plan.input.bbox,"model_cycle":iso(plan.cycle),"model_source":plan.input.model_source,
        "cams_update_cycle":cams_cycle,"notes":plan.input.notes,"event_start":iso(plan.event_start),
    }))
}
fn strings<'a>(row: &'a Value, field: &str) -> impl Iterator<Item = String> + 'a {
    row[field]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_owned)
}
fn grouped(reports: &[Value]) -> Vec<Value> {
    let mut result = Vec::new();
    for source in ["gfs", "ecmwf", "cams"] {
        let parts: Vec<_> = reports
            .iter()
            .flat_map(|r| {
                r["datasets"].as_array().unwrap().iter().map(move |row| {
                    let mut row = row.clone();
                    row["for_model"] = r["model_source"].clone();
                    row
                })
            })
            .filter(|row| row["source"] == source)
            .collect();
        let Some(first) = parts.first() else {
            continue;
        };
        let mut combined = first.clone();
        combined.as_object_mut().unwrap().remove("for_model");
        for field in ["ready", "verified", "is_latest"] {
            combined[field] = json!(parts.iter().all(|row| row[field] == true));
        }
        combined["request_cycle"] = json!(parts
            .iter()
            .flat_map(|r| r["request_cycle"]
                .as_str()
                .unwrap()
                .split(" / ")
                .map(str::to_owned))
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect::<Vec<_>>()
            .join(" / "));
        let keys: BTreeSet<_> = parts
            .iter()
            .flat_map(|r| strings(r, "missing_keys"))
            .collect();
        combined["missing_count"] = json!(if keys.is_empty() {
            parts
                .iter()
                .filter_map(|r| r["missing_count"].as_u64())
                .max()
                .unwrap_or(0) as usize
        } else {
            keys.len()
        });
        combined["missing_hours"] = json!(parts
            .iter()
            .flat_map(|r| r["missing_hours"]
                .as_array()
                .unwrap()
                .iter()
                .filter_map(Value::as_u64))
            .collect::<BTreeSet<_>>());
        combined["missing_valid_times"] = json!(parts
            .iter()
            .flat_map(|r| strings(r, "missing_valid_times"))
            .collect::<BTreeSet<_>>());
        combined["requirements"] = json!(parts);
        result.push(combined);
    }
    result
}

pub fn execute(params: Params) -> Result<Value, String> {
    execute_cancellable(params, &AtomicBool::new(false))
}
pub(crate) fn execute_cancellable(params: Params, cancel: &AtomicBool) -> Result<Value, String> {
    check(cancel)?;
    params.config.validate()?;
    if params.plans.is_empty() || params.plans.len() > 2 {
        return Err("desktop preflight requires one or two model plans".into());
    }
    for (source, publication) in &params.publications {
        if !matches!(source.as_str(), "gfs" | "ecmwf" | "cams")
            || publication.label.as_ref().is_some_and(|v| v.len() > 256)
            || publication.cycle.as_ref().is_some_and(|v| v.len() > 128)
            || publication.error.as_ref().is_some_and(|v| v.len() > 4096)
            || (publication.verified
                && (publication.label.is_none() || publication.cycle.is_none()))
        {
            return Err("invalid desktop publication metadata".into());
        }
    }
    let mut plans = params
        .plans
        .into_iter()
        .map(ResolvedPlan::new)
        .collect::<Result<Vec<_>, _>>()?;
    plans.sort_by_key(|p| if p.input.model_source == "gfs" { 0 } else { 1 });
    if plans.len() == 2 && plans[0].input.model_source == plans[1].input.model_source {
        return Err("desktop preflight model plans must be distinct".into());
    }
    let mut local = BTreeMap::new();
    for plan in &plans {
        if plan.input.model_source == "gfs" {
            scan("gfs", &params.config.gfs_cache_dir, &mut local, cancel)?;
        } else {
            scan("ecmwf", &params.config.ecmwf_cache_dir, &mut local, cancel)?;
        }
    }
    let (cams, paths) =
        desktop_inventory::local_snapshot("cams", &params.config.cams_cache_dir, cancel)?;
    local.insert("cams".into(), cams);
    let mut catalog = CamsCatalog::new(paths);
    let reports = plans
        .iter()
        .map(|p| {
            report(
                p,
                &params.config,
                &local,
                &mut catalog,
                &params.publications,
                params.fast,
                cancel,
                params.date_policy.as_deref(),
            )
        })
        .collect::<Result<Vec<_>, _>>()?;
    check(cancel)?;
    let value = if reports.len() == 1 {
        reports.into_iter().next().unwrap()
    } else {
        let cycles: BTreeMap<_, _> = reports
            .iter()
            .map(|r| (r["model_source"].as_str().unwrap(), &r["model_cycle"]))
            .collect();
        let windows: BTreeMap<_, _> = reports
            .iter()
            .map(|r| (r["model_source"].as_str().unwrap(), &r["event_start"]))
            .collect();
        json!({
            "ready":reports.iter().all(|r|r["ready"]==true),
            "datasets":grouped(&reports),"model_cycles":cycles,
            "terrain_pending":reports.iter().any(|r|r["terrain_pending"]==true),
            "cams_update_cycle":reports[0]["cams_update_cycle"],
            "notes":reports.iter().flat_map(|r|strings(r,"notes")).collect::<Vec<_>>(),
            "model_windows":windows,
        })
    };
    Ok(
        json!({"contract":"rust-desktop-preflight-v1","report":value,
        "runtime_python_used":false,"resolved_plan_only":true}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nowcast_dateline_preflight_uses_the_context_frozen_split() {
        let context = crate::desktop_nowcast::execute(crate::desktop_nowcast::Params {
            payload: json!({
                "lat":10.0,"lon":179.0,"elevation_m":0,"timezone_name":"UTC",
                "target_date":"2026-11-12","phenomenon":"dusk","radius_km":1000,
                "reference_time_utc":"2026-11-13T00:00:00Z",
                "_gfs_cycle_override":"2026-11-11T00:00:00Z",
            }),
            fast: true,
        })
        .unwrap();
        let plan: Plan = serde_json::from_value(json!({
            "model_source":"gfs","cycle_utc":context["cycle"],
            "event_start":context["times"][0],"bbox":context["bbox"],
            "area":context["area"],"hours":[24,25],"cams_valid_times":context["cams_valid_times"],
            "point_count":1,"terrain_pending":false,"compute_region":context["region"],
        }))
        .unwrap();
        let resolved = ResolvedPlan::new(plan).unwrap();
        assert_eq!(
            json!(resolved
                .areas
                .iter()
                .map(|area| area.bounds)
                .collect::<Vec<_>>()),
            json!(crate::desktop_nowcast::cams_areas(&context).unwrap()),
        );
        assert_eq!(resolved.areas.len(), 2);
    }

    #[test]
    fn cancellation_precedes_plan_or_filesystem_work() {
        let input = serde_json::from_value(json!({"plans":[]})).unwrap();
        assert!(execute_cancellable(input, &AtomicBool::new(true))
            .unwrap_err()
            .contains("cancelled"));
    }

    /// Regression for the wrapped multi-region CAMS preflight: ADS answers the
    /// dateline part request with a global longitude grid stored under that
    /// part's area hash. The sibling part must accept the file via true-arc
    /// coverage instead of reporting spurious missing targets.
    #[test]
    fn wrapped_multi_area_cams_accepts_global_grid_sibling() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
        let cams = root.join(".cache/cams");
        // The global-grid part file cached for the GFS east-asia envelope.
        if !cams
            .join("2026-11-13/00Z/cams-optics-v2-2026-11-13-0000-003-689bc94c330f.grib")
            .is_file()
        {
            return;
        }
        let plan: Plan = serde_json::from_value(json!({
            "model_source":"gfs",
            "cycle_utc":"2026-11-13T00:00:00Z",
            "event_start":"2026-11-13T09:00:00Z",
            "bbox":[44.75,173.25,69.25,2.75],
            "area":[78.85,26.15,-6.85,-168.15],
            "hours":[9],
            "cams_valid_times":["2026-11-13T03:00:00Z","2026-11-13T06:00:00Z",
                "2026-11-13T09:00:00Z","2026-11-13T12:00:00Z","2026-11-13T15:00:00Z"],
            "point_count":71217,
            "terrain_pending":false,
            "compute_region":null,
        }))
        .unwrap();
        let params = Params {
            plans: vec![plan],
            config: desktop_inventory::Config {
                gfs_cache_dir: root.join(".cache/gfs"),
                ecmwf_cache_dir: root.join(".cache/ecmwf"),
                cams_cache_dir: cams,
            },
            publications: BTreeMap::new(),
            fast: true,
                date_policy: None,
    };
        let result = execute_cancellable(params, &AtomicBool::new(false)).expect("preflight");
        let report = &result["report"];
        let cams_row = report["datasets"]
            .as_array()
            .unwrap()
            .iter()
            .find(|d| d["source"] == "cams")
            .expect("cams dataset");
        assert_eq!(
            cams_row["missing_count"],
            json!(0),
            "wrapped sibling part must be covered by the global grid: {cams_row}"
        );
        assert_eq!(cams_row["ready"], json!(true));
    }

    #[test]
    fn wrapped_longitude_bbox_is_valid_but_zero_width_is_not() {
        let base = json!({
            "model_source": "gfs",
            "cycle_utc": "2026-11-12T00:00:00Z",
            "event_start": "2026-11-12T06:00:00Z",
            "bbox": [170.0, -170.0, 1.0, -1.0],
            "area": [1.0, 170.0, -1.0, -170.0],
            "hours": [0],
            "cams_valid_times": ["2026-11-12T06:00:00Z"],
            "point_count": 2,
            "terrain_pending": false
        });
        let wrapped: Plan = serde_json::from_value(base.clone()).unwrap();
        assert!(ResolvedPlan::new(wrapped).is_ok());

        let mut zero = base;
        zero["bbox"] = json!([180.0, 180.0, 1.0, -1.0]);
        let zero: Plan = serde_json::from_value(zero).unwrap();
        assert!(ResolvedPlan::new(zero).is_err());
    }
}
