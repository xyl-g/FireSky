//! Independent EC point event planning and one-request basic/CAMS resolution.
use crate::native::timeline;
use crate::{
    assessment::{self, Elevation, Frame, Observer, OpticalFile},
    astronomy, cams, cams_cache, cams_data, ecmwf_data, ecmwf_point, request_file,
    tensor_bundle::{source_identity, SourceIdentity},
    time_contract,
};
use chrono::{DateTime, Utc};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
    time::Instant,
};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    date: String,
    phenomenon: astronomy::Phenomenon,
    point: Observer,
    radius_km: f64,
    cycle_utc: Option<String>,
    now_utc: Option<String>,
    #[serde(default)]
    ec: ecmwf_data::Config,
    cams_cache_dir: PathBuf,
    cams_download: Option<crate::ads::Config>,
    #[serde(default)]
    allow_ec_download: bool,
    #[serde(default = "auto_backend")]
    compute_backend: String,
    #[serde(default)]
    derived_cache: Option<crate::derived_cache::Config>,
    #[serde(default)]
    aerosol_source: Option<String>,
}
fn auto_backend() -> String {
    "auto".into()
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    pub path: PathBuf,
}
struct Prepared {
    input: assessment::Input,
    source: SourceIdentity,
    summary: Value,
    codes: Option<crate::grib::EcCodes>,
}

struct CamsPlan {
    areas: Vec<[f64; 4]>,
    selected: BTreeMap<DateTime<Utc>, Vec<cams_cache::Selection>>,
    download_reports: Vec<Value>,
}

fn cams_plan(
    root: &Path,
    area: [f64; 4],
    targets: &BTreeSet<DateTime<Utc>>,
    download: Option<&crate::ads::Config>,
    source: cams_cache::AerosolSource,
) -> Result<CamsPlan, String> {
    let areas = cams_data::split_areas(area)?;
    let mut selected_by_time = BTreeMap::<DateTime<Utc>, Vec<cams_cache::Selection>>::new();
    let mut download_reports = Vec::new();
    for sub_area in &areas {
        let key = cams_cache::area_key(*sub_area)?;
        let mut selected = cams_cache::select_with_key_for_source(
            root,
            *sub_area,
            targets,
            &key,
            None,
            &[],
            source,
        )?;
        if selected.len() < targets.len() {
            if let Some(config) = download {
                download_reports.push(cams_data::resolve_with_source(
                    root,
                    *sub_area,
                    targets.iter().copied().collect(),
                    config.clone(),
                    Some(source.as_str().to_owned()),
                )?);
                selected = cams_cache::select_with_key_for_source(
                    root,
                    *sub_area,
                    targets,
                    &key,
                    None,
                    &[],
                    source,
                )?;
            }
        }
        for target in targets {
            if let Some(value) = selected.get(target) {
                selected_by_time
                    .entry(*target)
                    .or_default()
                    .push(value.clone());
            }
        }
    }
    Ok(CamsPlan {
        areas,
        selected: selected_by_time,
        download_reports,
    })
}
pub fn execute(params: Params) -> Result<Value, String> {
    Ok(prepare(&params.path)?.summary)
}
pub fn forecast(path: &Path, output: &Path) -> Result<PathBuf, String> {
    forecast_mode(path, output, false, &|_: u128| {})
}
pub fn forecast_service(path: &Path, output: &Path) -> Result<PathBuf, String> {
    forecast_mode(path, output, true, &|_: u128| {})
}
pub fn forecast_with_progress(
    path: &Path,
    output: &Path,
    progress: &(dyn Fn(u128) + Sync),
) -> Result<PathBuf, String> {
    forecast_mode(path, output, false, progress)
}
fn forecast_mode(
    path: &Path,
    output: &Path,
    service: bool,
    progress: &(dyn Fn(u128) + Sync),
) -> Result<PathBuf, String> {
    let started = Instant::now();
    let prepared = prepare(path)?;
    // Hold the shared library through assessment as well as input resolution.
    // Unloading between phases discards ecCodes' definition caches.
    let _codes = prepared.codes;
    if prepared.summary["inputs_located"] != true {
        return Err(format!(
            "EC/CAMS event inputs are incomplete: {}",
            prepared.summary
        ));
    }
    let forecast = if service {
        assessment::planned_service_to_files
    } else {
        assessment::planned_point_to_files
    };
    forecast(
        path,
        output,
        prepared.input,
        prepared.source,
        prepared.summary,
        started,
        progress,
    )
}

struct Context {
    input: Input,
    source: SourceIdentity,
    domain: [f64; 3],
    bbox: [f64; 4],
    evaluation: Vec<DateTime<Utc>>,
    valid: Vec<u8>,
    anchor: DateTime<Utc>,
}

pub(crate) struct ServiceRequest {
    pub identity: Value,
    pub source: SourceIdentity,
    context: Context,
    cycle: DateTime<Utc>,
}
pub(crate) fn service_request(path: &Path) -> Result<ServiceRequest, String> {
    let context = context(path)?;
    let input = &context.input;
    let cycle = ecmwf_data::snapshot_cycle(
        &input.ec,
        input.cycle_utc.as_deref(),
        context.anchor,
        input.now_utc.as_deref(),
        &context.evaluation,
    )?;
    if context
        .evaluation
        .iter()
        .any(|v| *v < cycle || *v > cycle + chrono::Duration::hours(360))
    {
        return Err("EC point event lies outside published 360-hour horizon".into());
    }
    let p = &input.point;
    let identity = json!({
        "contract":"rust-ecmwf-response-cache-v1","model_source":"ec",
        "model":"ifs","member":"ecmwf-ifs-basic","forecast_mode":"model","data_contract":"public-open-data/basic",
        "target_date":input.date,"phenomenon":input.phenomenon,
        "observer":{"name":p.name,"lat":p.lat,"lon":p.lon,"elevation_m":p.elevation_m,"timezone_name":p.timezone_name},
        "radius_km":input.radius_km,"compute_backend_requested":input.compute_backend,"compute_backend":"cpu",
        "cycle_utc":time_contract::iso(cycle),
        "ec_cache_dir":std::path::absolute(&input.ec.cache_dir).map_err(|e|e.to_string())?,
        "cams_cache_dir":std::path::absolute(&input.cams_cache_dir).map_err(|e|e.to_string())?,
        "provider":{"source":input.ec.source,"resolution":input.ec.resolution,"base_url":input.ec.base_url},
        "engine_source":env!("FIRESKY_ENGINE_SOURCE_ID"),
        "timezone_database":chrono_tz::IANA_TZDB_VERSION,"native_abi":crate::native::NATIVE_ABI_VERSION,
        "architecture":std::env::consts::ARCH,"os":std::env::consts::OS,
    });
    Ok(ServiceRequest {
        identity,
        source: context.source.clone(),
        context,
        cycle,
    })
}
pub(crate) fn service_from_request(
    path: &Path,
    output: &Path,
    request: ServiceRequest,
    started: Instant,
) -> Result<PathBuf, String> {
    let prepared = prepare_context(path, request.context, Some(request.cycle), started)?;
    let _codes = prepared.codes;
    if prepared.summary["inputs_located"] != true {
        return Err(format!(
            "EC/CAMS event inputs are incomplete: {}",
            prepared.summary
        ));
    }
    assessment::planned_service_to_files(
        path,
        output,
        prepared.input,
        prepared.source,
        prepared.summary,
        started,
        &|_: u128| {},
    )
}

fn context(path: &Path) -> Result<Context, String> {
    let (mut input, source): (Input, _) = request_file::read(path, 1024 * 1024)?;
    let p = &input.point;
    let elevation = match (p.elevation_source, p.elevation_m) {
        (Elevation::Observer, Some(v)) if v.is_finite() && (-500.0..=9000.0).contains(&v) => v,
        _ => return Err("EC point planning requires explicit observer elevation".into()),
    };
    if p.name
        .as_ref()
        .is_none_or(|n| n.is_empty() || n.len() > 256)
        || p.timezone_name.len() > 128
        || !p.lat.is_finite()
        || !(-90.0..=90.0).contains(&p.lat)
        || !p.lon.is_finite()
        || !(-180.0..=180.0).contains(&p.lon)
        || input.cams_cache_dir.as_os_str().is_empty()
        || input.ec.cache_dir.as_os_str().is_empty()
        || !matches!(input.compute_backend.as_str(), "auto" | "cpu")
    {
        return Err("invalid EC point planning observer/cache".into());
    }
    let domain = ecmwf_point::domain(input.radius_km)?;
    let zone = p
        .timezone_name
        .parse()
        .map_err(|_| "unknown EC observer timezone")?;
    let date = time_contract::parse_date(&input.date)?;
    let solar = timeline::prepare(
        &[timeline::Point {
            lat: p.lat,
            lon: p.lon,
            elevation_m: elevation,
            timezone: zone,
        }],
        date,
        matches!(input.phenomenon, astronomy::Phenomenon::Dawn),
        None,
    )
    .map_err(|e| e.to_string())?;
    let evaluation: Vec<_> = solar.rows[0]
        .evaluation
        .iter()
        .map(|&t| assessment::from_epoch(t))
        .collect::<Result<_, _>>()?;
    let anchor = assessment::from_epoch(solar.rows[0].anchor)?;
    let normalize = |lon: f64| {
        let value = (lon + 180.0).rem_euclid(360.0) - 180.0;
        if value == -180.0 && lon > 0.0 {
            180.0
        } else {
            value
        }
    };
    let lat_pad = domain[2] / 111.0;
    let lon_pad = domain[2] / (111.0 * p.lat.to_radians().cos()).max(25.0);
    let bbox = [
        normalize(p.lon - lon_pad),
        normalize(p.lon + lon_pad),
        (p.lat + lat_pad).min(90.0),
        (p.lat - lat_pad).max(-90.0),
    ];
    if bbox[0] == bbox[1] {
        return Err("EC point planning requires a nonempty source bbox".into());
    }
    let absolute = std::path::absolute(path).map_err(|e| e.to_string())?;
    let base = absolute.parent().unwrap_or(Path::new("."));
    if let Some(config) = &mut input.derived_cache {
        config.resolve(base)?;
    }
    if input.ec.cache_dir.is_relative() {
        input.ec.cache_dir = base.join(&input.ec.cache_dir);
    }
    if input.cams_cache_dir.is_relative() {
        input.cams_cache_dir = base.join(&input.cams_cache_dir);
    }
    if let Some(config) = &mut input.cams_download {
        crate::ads::resolve_credentials_path(config, base);
        config.validate()?;
    }
    Ok(Context {
        input,
        source,
        domain,
        bbox,
        evaluation,
        anchor,
        valid: solar.rows[0].valid.to_vec(),
    })
}

fn prepare(path: &Path) -> Result<Prepared, String> {
    let started = Instant::now();
    prepare_context(path, context(path)?, None, started)
}
fn prepare_context(
    path: &Path,
    context: Context,
    fixed_cycle: Option<DateTime<Utc>>,
    started: Instant,
) -> Result<Prepared, String> {
    let Context {
        input,
        source,
        domain,
        bbox,
        evaluation,
        valid,
        anchor,
    } = context;
    let phase = Instant::now();
    let plan = ecmwf_data::execute(ecmwf_data::Params {
        operation: "plan".into(),
        config: input.ec.clone(),
        cycle_utc: fixed_cycle
            .map(time_contract::iso)
            .or_else(|| input.cycle_utc.clone()),
        forecast_hour: None,
        forecast_hours: None,
        reference_utc: Some(time_contract::iso(anchor)),
        now_utc: input.now_utc.clone(),
        evaluation_utcs: evaluation.iter().copied().map(time_contract::iso).collect(),
        max_cycles: 8,
        allow_download: input.allow_ec_download,
        use_cache_for_cycle: true,
    })?;
    let cycle =
        time_contract::parse_utc(plan["cycle_utc"].as_str().ok_or("missing EC plan cycle")?)?;
    if evaluation
        .iter()
        .any(|v| *v < cycle || *v > cycle + chrono::Duration::hours(360))
    {
        return Err("EC point event lies outside published 360-hour horizon".into());
    }
    let hours: Vec<u32> =
        serde_json::from_value(plan["forecast_hours"].clone()).map_err(|e| e.to_string())?;
    let mut optical_hours = BTreeSet::new();
    for (&target, &valid) in evaluation.iter().zip(&valid) {
        if valid >= 1 {
            let (a, b, _) = ecmwf_data::source_bracket(cycle, target)?;
            optical_hours.extend([a, b]);
        }
    }
    if optical_hours.is_empty() {
        return Err("EC scoring requires at least one valid solar sample".into());
    }
    let mut frames = Vec::new();
    let mut missing = Vec::new();
    for row in plan["files"].as_array().ok_or("missing EC plan files")? {
        let hour = row["forecast_hour"]
            .as_u64()
            .ok_or("invalid EC plan hour")? as u32;
        if row["present_nonempty"] == true || input.allow_ec_download {
            frames.push(Frame {
                hour,
                path: row["path"].as_str().ok_or("missing EC plan path")?.into(),
                paths: Vec::new(),
            });
        } else {
            missing.push(hour);
        }
    }
    // Admission, header probes and ecCodes initialization are shared across
    // all hours in one request, not one subprocess/resource context per hour.
    let codes = if missing.is_empty() {
        Some(crate::grib::EcCodes::load()?)
    } else {
        None
    };
    let resolved = if missing.is_empty() {
        Some(ecmwf_data::execute(ecmwf_data::Params {
            operation: "resolve".into(),
            config: input.ec.clone(),
            cycle_utc: Some(time_contract::iso(cycle)),
            forecast_hour: None,
            forecast_hours: Some(hours.iter().map(|v| i64::from(*v)).collect()),
            reference_utc: None,
            now_utc: None,
            evaluation_utcs: Vec::new(),
            max_cycles: 8,
            allow_download: input.allow_ec_download,
            use_cache_for_cycle: true,
        })?)
    } else {
        None
    };
    let ec_ms = phase.elapsed().as_secs_f64() * 1000.0;
    let mut required = BTreeSet::<DateTime<Utc>>::new();
    for hour in &optical_hours {
        let (a, b, _) = cams::bracket(&time_contract::iso(cycle + chrono::Duration::hours(*hour)))?;
        required.extend([a, b]);
    }
    let (a, b, _) = cams::bracket(&time_contract::iso(anchor))?;
    required.extend([a, b]);
    let phase = Instant::now();
    let mut area = None;
    let mut cams_selection = None;
    let mut cams_download = Value::Null;
    if missing.is_empty() {
        let selected_bbox = codes
            .as_ref()
            .ok_or("EC decoder lifetime missing")?
            .first_subset_area(&frames[0].path, bbox)?;
        let round = |v: f64| (v * 1000.0).round_ties_even() / 1000.0;
        let value = [
            round((selected_bbox[0] + 0.6).min(90.0)),
            round(selected_bbox[1] - 0.6),
            round((selected_bbox[2] - 0.6).max(-90.0)),
            round(selected_bbox[3] + 0.6),
        ];
        area = Some(value);
        let aerosol_source = cams_cache::AerosolSource::parse(input.aerosol_source.as_deref())?;
        let plan = cams_plan(
            &input.cams_cache_dir,
            value,
            &required,
            input.cams_download.as_ref(),
            aerosol_source,
        )?;
        if plan.download_reports.len() == 1 {
            cams_download = plan.download_reports[0].clone();
        } else if !plan.download_reports.is_empty() {
            cams_download = Value::Array(plan.download_reports.clone());
        }
        cams_selection = Some(plan);
    }
    let empty_selected = BTreeMap::new();
    let selected = cams_selection
        .as_ref()
        .map(|plan| &plan.selected)
        .unwrap_or(&empty_selected);
    let cams_areas = cams_selection
        .as_ref()
        .map(|plan| plan.areas.clone())
        .unwrap_or_default();
    let missing_cams: Vec<_> = required
        .iter()
        .filter(|v| {
            selected
                .get(v)
                .is_none_or(|values| values.len() != cams_areas.len())
        })
        .copied()
        .map(time_contract::iso)
        .collect();
    let optics: Vec<_> = selected
        .values()
        .filter(|values| values.len() == cams_areas.len())
        .map(|values| {
            let paths = values.iter().map(|s| s.path.clone()).collect::<Vec<_>>();
            OpticalFile {
                valid_time: time_contract::iso(values[0].valid_time),
                path: paths[0].clone(),
                paths: paths.into_iter().skip(1).collect(),
            }
        })
        .collect();
    let cams_area_keys = cams_areas
        .iter()
        .map(|area| cams_cache::area_key(*area))
        .collect::<Result<Vec<_>, _>>()?;
    let summary = json!({
        "contract":"rust-ecmwf-input-plan-v1","model_source":"ec","cycle_utc":time_contract::iso(cycle),
        "provider":{"model":"ifs","member":"ecmwf-ifs-basic","source":input.ec.source,"resolution":input.ec.resolution},
        "cycle_selection":if input.cycle_utc.is_some(){"explicit"}else{"event-or-now-minus-7h-00-12z-404-fallback"},
        "publication_probe_required":input.cycle_utc.is_none(),"date":input.date,"phenomenon":input.phenomenon,
        "time_policy":"ec-point-canonical-twenty-sample","anchor_time_utc":time_contract::iso(anchor),
        "evaluation_times_utc":evaluation.iter().copied().map(time_contract::iso).collect::<Vec<_>>(),
        "forecast_hours":hours,"model_files":frames,"missing_hours":missing,
        "requested_radius_km":input.radius_km,"candidate_radius_km":domain[0],"native_solar_trace_radius_km":domain[1],
        "source_domain_radius_km":domain[2],"bbox":bbox,"cams_area":area,
        "cams_areas":cams_areas,
        "cams_area_key":if cams_area_keys.len() == 1 {json!(cams_area_keys[0])} else {Value::Null},
        "cams_area_keys":cams_area_keys,
        "cams_valid_times":required.iter().copied().map(time_contract::iso).collect::<Vec<_>>(),
        "cams_model_forecast_hours":optical_hours,
        "cams_files":optics,
        "cams_selections":selected.values().flatten().collect::<Vec<_>>(),
        "missing_cams_valid_times":missing_cams,"cams_download":cams_download,
        "ec_download_enabled":input.allow_ec_download,"cams_download_enabled":input.cams_download.is_some(),
        "ec_resolution":resolved.as_ref().unwrap_or(&plan),
        "model_cache_hits_validated":missing.is_empty(),"cams_profile_contents_validated":true,
        "derived_cache":input.derived_cache,
        "cams_filename_grib_time_verified":false,"inputs_located":missing.is_empty()&&missing_cams.is_empty(),
        "production_input_lifecycle_complete":false,"public_forecast_complete":false,
        "timings_ms":{"ec_resolution":ec_ms,"cams_selection":phase.elapsed().as_secs_f64()*1000.0},
        "elapsed_ms":started.elapsed().as_secs_f64()*1000.0,
    });
    if source_identity(path)? != source {
        return Err("EC planning request changed during execution".into());
    }
    Ok(Prepared {
        source,
        summary,
        codes,
        input: assessment::Input {
            model: assessment::Model::EcmwfBasic,
            cycle_utc: time_contract::iso(cycle),
            date: input.date,
            phenomenon: input.phenomenon,
            points: vec![input.point],
            frames,
            cams: optics,
            radius_km: domain[0],
            bbox: Some(bbox),
            include_point_core: true,
            point_spacing_deg: None,
            compute_backend_requested: Some(input.compute_backend),
            derived_cache: input.derived_cache,
            compact_results: false,
        },
    })
}
