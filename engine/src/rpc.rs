use crate::grib::EcCodes;
use crate::tensor_bundle::{source_identity, BundleWriter};
use crate::{
    assessment, astronomy, cams, characteristics, diagnostics, ecmwf, profile_diagnostics, quality,
    spectral, time_contract, timeline,
};
use firesky_native::empirical;
use firesky_native::{cloud, derivation, gfs::GfsGrid};
use serde::Deserialize;
use serde_json::{json, Value};
use std::io::{self, BufRead, Write};
use std::path::Path;
use std::sync::{atomic::AtomicBool, Arc};

const MAX_LINE_BYTES: usize = 1024 * 1024;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    id: String,
    method: String,
    #[serde(default = "empty_object")]
    params: Value,
}

fn empty_object() -> Value {
    json!({})
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WindowParams {
    values: Vec<f64>,
    active: Vec<bool>,
    widths: Vec<f64>,
    offsets: Option<Vec<f64>>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct GribParams {
    path: String,
    #[serde(default)]
    include_values: bool,
    #[serde(default = "default_messages")]
    max_messages: usize,
    #[serde(default = "default_points")]
    max_points: usize,
}

fn default_messages() -> usize {
    128
}
fn default_points() -> usize {
    5_000_000
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct GfsParams {
    path: String,
    #[serde(default = "gfs_max_messages")]
    max_messages: usize,
    #[serde(default = "default_points")]
    max_points: usize,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct GfsFileParams {
    path: String,
    output_dir: String,
    #[serde(default = "gfs_max_messages")]
    max_messages: usize,
    #[serde(default = "default_points")]
    max_points: usize,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EcFileParams {
    path: String,
    output_dir: String,
    bbox: Option<[f64; 4]>,
    expected_cycle_utc: Option<String>,
    expected_forecast_hour: Option<i64>,
    #[serde(default = "gfs_max_messages")]
    max_messages: usize,
    #[serde(default = "default_points")]
    max_points: usize,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EcFullFileParams {
    path: String,
    output_dir: String,
    bbox: Option<[f64; 4]>,
    expected_cycle_utc: Option<String>,
    expected_forecast_hour: Option<i64>,
    #[serde(default = "full_model_levels")]
    model_levels: Vec<i32>,
    #[serde(default = "gfs_max_messages")]
    max_messages: usize,
    #[serde(default = "default_points")]
    max_points: usize,
}

fn full_model_levels() -> Vec<i32> {
    (1..=ecmwf::DEFAULT_MODEL_LEVELS as i32).collect()
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CamsFileParams {
    path: String,
    #[serde(default)]
    paths: Vec<String>,
    output_dir: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CamsMapParams {
    path: String,
    output_dir: String,
    target_manifest: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CamsInterpolateParams {
    left_path: String,
    right_path: String,
    target_manifest: String,
    target_time: String,
    output_dir: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SpectralFileParams {
    path: String,
    output_dir: String,
    expected_valid_time: Option<String>,
    #[serde(default = "gfs_max_messages")]
    max_messages: usize,
    #[serde(default = "default_points")]
    max_points: usize,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SpectralDiagnosticParams {
    path: String,
    observer: [f64; 2],
    wavelengths_nm: Vec<f64>,
    solar_elevation_deg: f64,
    scattering_angle_deg: f64,
    expected_valid_time: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RegionalSolarParams {
    path: String,
    output_dir: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DesktopLocationParams {
    method: String,
    params: Value,
    config: crate::location::Config,
    #[serde(default = "default_allow_download")]
    allow_download: bool,
}

fn default_allow_download() -> bool {
    true
}

fn gfs_max_messages() -> usize {
    4096
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct FeatureParams {
    path: String,
    observer: [f64; 2],
    radius_km: f64,
}

fn grid_payload(grid: &GfsGrid) -> Value {
    let fields: Vec<Value> = grid
        .fields
        .iter()
        .map(|((level_type, level, name), values)| {
            json!({
                "level_type": level_type, "level": level, "name": name, "values": values,
            })
        })
        .collect();
    json!({"latitudes": grid.latitudes, "longitudes": grid.longitudes, "fields": fields})
}

pub fn dispatch(method: &str, params: Value) -> Result<Value, String> {
    match method {
        "decode_raw_hsd_to_files" => {
            crate::hsd_raw::execute(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "warm_hsd_cache" => crate::hsd_provider::execute(params),
        "assess_radar_images" => crate::radar::metrics_request(params),
        "hello" => Ok(json!({
            "protocol": 1,
            "engine": "firesky-rust",
            "source_id": env!("FIRESKY_ENGINE_SOURCE_ID"),
            "migration_stage": "desktop-data-preflight",
            "native_abi": firesky_native::NATIVE_ABI_VERSION,
            "astronomy": {"contract": "rust-astronomy-v1", "timezone_database": chrono_tz::IANA_TZDB_VERSION},
            "capabilities": ["score_window", "decode_grib", "decode_gfs", "prepare_gfs_features", "derive_gfs",
                "decode_gfs_to_files", "derive_gfs_to_files", "decode_ecmwf_basic_to_files", "derive_ecmwf_basic_to_files",
                "decode_cams_to_files", "map_cams_to_files", "interpolate_cams_to_files",
                "decode_cams_spectral_to_files", "diagnose_cams_spectral",
                "decode_ecmwf_full_to_files", "time_contract", "solar_events", "solar_snapshots",
                "solar_crossing", "solar_shadow_speed", "solar_glow_window", "prepare_regional_solar_to_files",
                "assess_stored_regional_to_files", "score_observer_fields", "score_nowcast_fields",
                "score_stored_regional_to_files",
                "diagnose_observer_fields", "diagnose_stored_regional_to_files", "forecast_characteristics",
                "diagnose_profile_columns", "diagnose_stored_profiles_to_files", "build_stored_frames_to_files",
                "assemble_observer_frames", "quantize_frame_fields",
                "validate_point_core", "materialize_point_core", "forecast_stored_point_to_files",
                "gfs_data", "plan_gfs_inputs", "forecast_cached_gfs_point_to_files", "cams_data", "calculate_earth_engine_aod", "ecmwf_data",
                "materialize_ecmwf_point", "plan_ecmwf_inputs", "forecast_cached_ecmwf_point_to_files",
                "build_stored_cross_section", "render_html_report", "forecast_ecmwf_service_to_files",
                "read_ecmwf_service_cross_section", "forecast_ecmwf_service_cached",
                "read_cached_ecmwf_service_cross_section", "location", "check_desktop_data",
                "desktop_inventory", "desktop_location", "desktop_publications", "desktop_prepare", "desktop_session",
                "desktop_nowcast_gfs_plan", "desktop_nowcast_prior",
                "desktop_nowcast_prior_batch", "desktop_nowcast_prior_shared_batch",
                "desktop_nowcast_prior_decoded_batch",
                "desktop_nowcast_calibrated_retrieval",
                "desktop_nowcast_adjustment",
                "desktop_nowcast_assessment",
                "desktop_nowcast_prepare",
                "desktop_nowcast_hsd_cache",
                "desktop_nowcast_hsd_export", "desktop_nowcast_hsd_motion",
                "desktop_nowcast_hsd_advection", "desktop_nowcast_hsd_retrieval",
                "decode_raw_hsd_to_files", "warm_hsd_cache", "assess_radar_images"],
        })),
        "desktop_context" => crate::desktop_context::execute(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "desktop_nowcast_context" => crate::desktop_nowcast::execute(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "desktop_nowcast_gfs_plan" => crate::desktop_nowcast::gfs_plan(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "desktop_nowcast_prior" => crate::desktop_nowcast_prior::execute(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "desktop_nowcast_prior_batch" => crate::desktop_nowcast_prior::execute_batch(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "desktop_nowcast_prior_shared_batch" => crate::desktop_nowcast_prior::execute_shared_batch(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "desktop_nowcast_prior_decoded_batch" => {
            crate::desktop_nowcast_prior::execute_decoded_batch(
                serde_json::from_value(params).map_err(|e| e.to_string())?,
            )
        }
        "desktop_nowcast_calibrated_retrieval" => crate::desktop_nowcast_retrieval::execute(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "desktop_nowcast_adjustment" => crate::desktop_nowcast_adjustment::execute(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "desktop_nowcast_assessment" => crate::desktop_nowcast_assessment::execute(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "desktop_nowcast_prepare" => crate::desktop_nowcast_prepare::execute(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "desktop_nowcast_hsd_cache" => {
            crate::desktop_hsd::execute(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "desktop_nowcast_hsd_export" => {
            crate::desktop_hsd::export(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "desktop_nowcast_hsd_motion" => crate::desktop_hsd_motion::execute(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "desktop_nowcast_hsd_advection" => crate::desktop_hsd_advection::execute(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "desktop_nowcast_hsd_retrieval" => crate::desktop_hsd_retrieval::execute(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "check_desktop_data" => crate::desktop_preflight::execute(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "desktop_inventory" => {
            let config: crate::desktop_inventory::Config =
                serde_json::from_value(params.get("config").cloned().unwrap_or_default())
                    .map_err(|e| e.to_string())?;
            let value =
                crate::desktop_inventory::execute(&config, Arc::new(AtomicBool::new(false)))?;
            let datasets = value
                .get("datasets")
                .cloned()
                .ok_or("desktop inventory response is missing datasets")?;
            Ok(json!({
                "contract": "rust-desktop-inventory-v1",
                "runtime_python_used": false,
                "datasets": datasets,
            }))
        }
        "desktop_location" => {
            let request: DesktopLocationParams =
                serde_json::from_value(params).map_err(|e| e.to_string())?;
            let result = crate::location::desktop_dispatch(
                &request.method,
                &request.params,
                &request.config,
                request.allow_download,
                Arc::new(AtomicBool::new(false)),
            )?;
            Ok(json!({
                "contract": "rust-desktop-location-v1",
                "runtime_python_used": false,
                "result": result,
            }))
        }
        "desktop_publications" => crate::desktop_publications::execute(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "desktop_prepare" => crate::desktop_prepare::execute(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "desktop_session" => crate::desktop_session::execute(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "gfs_data" => {
            crate::gfs_data::execute(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "cams_data" => {
            crate::cams_data::execute(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "calculate_earth_engine_aod" => {
            crate::earth_engine_aod::execute(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "ecmwf_data" => {
            crate::ecmwf_data::execute(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "materialize_ecmwf_point" => {
            crate::ecmwf_point::execute(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "build_stored_cross_section" => crate::cross_section::execute(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "render_html_report" => {
            crate::html_report::execute(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "forecast_ecmwf_service_cached" => crate::response_cache::execute(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "location" => {
            let params = serde_json::from_value(params).map_err(|e| e.to_string())?;
            crate::location::execute(params)
        }
        "read_cached_ecmwf_service_cross_section" => {
            crate::response_cache::read(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "read_ecmwf_service_cross_section" => crate::ecmwf_service::read_cross_section(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "forecast_ecmwf_service_to_files" => {
            let params: RegionalSolarParams =
                serde_json::from_value(params).map_err(|e| e.to_string())?;
            Ok(json!({"manifest_path":crate::ecmwf_plan::forecast_service(
                Path::new(&params.path),Path::new(&params.output_dir))?}))
        }
        "plan_ecmwf_inputs" => {
            crate::ecmwf_plan::execute(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "forecast_cached_ecmwf_point_to_files" => {
            let params: RegionalSolarParams =
                serde_json::from_value(params).map_err(|e| e.to_string())?;
            Ok(json!({"manifest_path":crate::ecmwf_plan::forecast(
                Path::new(&params.path),Path::new(&params.output_dir))?}))
        }
        "plan_gfs_inputs" => {
            crate::input_plan::execute(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "forecast_cached_gfs_point_to_files" => {
            let params: RegionalSolarParams =
                serde_json::from_value(params).map_err(|e| e.to_string())?;
            Ok(json!({"manifest_path":crate::input_plan::forecast(
                Path::new(&params.path),Path::new(&params.output_dir))?}))
        }
        "forecast_stored_point_to_files" => {
            let params: RegionalSolarParams =
                serde_json::from_value(params).map_err(|e| e.to_string())?;
            Ok(
                json!({"manifest_path":assessment::point_to_files(Path::new(&params.path),Path::new(&params.output_dir))?}),
            )
        }
        "validate_point_core" => crate::point_core::execute_validate(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "materialize_point_core" => crate::point_core::execute_materialize(
            serde_json::from_value(params).map_err(|e| e.to_string())?,
        ),
        "assemble_observer_frames" => {
            crate::frames::execute(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "quantize_frame_fields" => {
            crate::frames::quantize(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "build_stored_frames_to_files" => {
            let params: RegionalSolarParams =
                serde_json::from_value(params).map_err(|e| e.to_string())?;
            Ok(
                json!({"manifest_path":assessment::frames_to_files(Path::new(&params.path),Path::new(&params.output_dir))?}),
            )
        }
        "diagnose_stored_profiles_to_files" => {
            let params: RegionalSolarParams =
                serde_json::from_value(params).map_err(|e| e.to_string())?;
            let path = assessment::diagnose_profiles_to_files(
                Path::new(&params.path),
                Path::new(&params.output_dir),
            )?;
            Ok(json!({"manifest_path":path}))
        }
        "diagnose_profile_columns" => {
            profile_diagnostics::execute(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "forecast_characteristics" => {
            characteristics::execute(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "diagnose_observer_fields" => {
            diagnostics::execute(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "diagnose_stored_regional_to_files" => {
            let params: RegionalSolarParams =
                serde_json::from_value(params).map_err(|e| e.to_string())?;
            let path = assessment::diagnose_to_files(
                Path::new(&params.path),
                Path::new(&params.output_dir),
            )?;
            Ok(json!({"manifest_path":path}))
        }
        "score_observer_fields" => {
            quality::execute(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "score_nowcast_fields" => {
            quality::execute_nowcast(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "score_stored_regional_to_files" => {
            let params: RegionalSolarParams =
                serde_json::from_value(params).map_err(|e| e.to_string())?;
            let path =
                assessment::score_to_files(Path::new(&params.path), Path::new(&params.output_dir))?;
            Ok(json!({"manifest_path":path}))
        }
        "assess_stored_regional_to_files" => {
            let params: RegionalSolarParams =
                serde_json::from_value(params).map_err(|e| e.to_string())?;
            let path =
                assessment::run_to_files(Path::new(&params.path), Path::new(&params.output_dir))?;
            Ok(json!({"manifest_path":path}))
        }
        "prepare_regional_solar_to_files" => {
            let params: RegionalSolarParams =
                serde_json::from_value(params).map_err(|e| e.to_string())?;
            let path =
                timeline::prepare_file(Path::new(&params.path), Path::new(&params.output_dir))?;
            Ok(json!({"manifest_path":path}))
        }
        "time_contract" => {
            time_contract::execute(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "solar_events" => {
            astronomy::events(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "solar_snapshots" => {
            astronomy::snapshots(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "solar_crossing" => {
            astronomy::crossing_payload(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "solar_shadow_speed" => {
            astronomy::shadow_payload(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "solar_glow_window" => {
            astronomy::glow_window(serde_json::from_value(params).map_err(|e| e.to_string())?)
        }
        "score_window" => {
            let params: WindowParams = serde_json::from_value(params).map_err(|e| e.to_string())?;
            let active: Vec<u8> = params.active.iter().map(|&value| u8::from(value)).collect();
            let window = empirical::event_window_weights(
                &params.values,
                &active,
                &params.widths,
                params.offsets.as_deref(),
            )?;
            let duration: f64 = params
                .widths
                .iter()
                .zip(active)
                .map(|(&width, active)| width.max(0.0) * f64::from(active))
                .sum();
            let mean: f64 = window
                .weights
                .iter()
                .zip(&params.values)
                .map(|(weight, value)| weight * value)
                .sum();
            let factor = empirical::duration_factor(duration);
            let pre = mean * factor;
            let score = empirical::calibrated_quality_response(pre);
            if !pre.is_finite() {
                return Err("score overflow".into());
            }
            Ok(json!({
                "weights": window.weights, "window_minutes": window.window_minutes,
                "duration_minutes": duration, "duration_factor": factor,
                "window_pre_response_index": mean, "pre_response_index": pre,
                "index": score, "quality_level": empirical::quality_level(score),
            }))
        }
        "decode_grib" => {
            let params: GribParams = serde_json::from_value(params).map_err(|e| e.to_string())?;
            let fields = EcCodes::load()?.decode_file(
                Path::new(&params.path),
                params.include_values,
                params.max_messages,
                params.max_points,
            )?;
            Ok(json!({"fields": fields}))
        }
        "decode_gfs" => {
            let params: GfsParams = serde_json::from_value(params).map_err(|e| e.to_string())?;
            let grid = EcCodes::load()?.decode_gfs_file(
                Path::new(&params.path),
                params.max_messages,
                params.max_points,
            )?;
            Ok(grid_payload(&grid))
        }
        "derive_gfs" => {
            let params: GfsParams = serde_json::from_value(params).map_err(|e| e.to_string())?;
            let grid = EcCodes::load()?.decode_gfs_file(
                Path::new(&params.path),
                params.max_messages,
                params.max_points,
            )?;
            let raw = derivation::RawGrid::from_gfs(&grid)?;
            if raw.latitudes.len()
                * (derivation::COLUMN_FIELDS + raw.levels.len() * derivation::PROFILE_FIELDS)
                > 10_000_000
            {
                return Err("GFS derived array output limit exceeded (10000000 values)".into());
            }
            let derived = derivation::derive(&raw)?;
            let columns: Vec<&[f32]> = derived.columns.iter().map(|row| row.as_slice()).collect();
            let profiles: Vec<Vec<&[f32]>> = derived
                .profiles
                .chunks(derived.levels.len())
                .map(|cell| cell.iter().map(|row| row.as_slice()).collect())
                .collect();
            Ok(json!({
                "latitudes": grid.latitudes, "longitudes": grid.longitudes,
                "pressure_levels": derived.levels, "columns": columns, "profiles": profiles,
                "column_fields": derivation::COLUMN_FIELDS, "profile_fields": derivation::PROFILE_FIELDS,
            }))
        }
        "decode_gfs_to_files" | "derive_gfs_to_files" => {
            let params: GfsFileParams =
                serde_json::from_value(params).map_err(|e| e.to_string())?;
            let path = Path::new(&params.path);
            let source = source_identity(path)?;
            let grid =
                EcCodes::load()?.decode_gfs_file(path, params.max_messages, params.max_points)?;
            let cells = grid.latitudes.len() as u64;
            let mut writer = if method == "derive_gfs_to_files" {
                let raw = derivation::RawGrid::from_gfs(&grid)?;
                let derived = derivation::derive(&raw)?;
                let mut writer = BundleWriter::create(
                    Path::new(&params.output_dir),
                    "gfs-derived-v1",
                    source,
                    json!({
                        "pressure_levels": derived.levels,
                        "column_fields": derivation::COLUMN_FIELDS,
                        "profile_fields": derivation::PROFILE_FIELDS,
                        "cloud_backend": "rust-f32-v3",
                    }),
                )?;
                writer.f32(
                    "columns",
                    &[cells, derivation::COLUMN_FIELDS as u64],
                    derived.columns.iter().flatten().copied(),
                )?;
                writer.f32(
                    "profiles",
                    &[
                        cells,
                        derived.levels.len() as u64,
                        derivation::PROFILE_FIELDS as u64,
                    ],
                    derived.profiles.iter().flatten().copied(),
                )?;
                writer
            } else {
                let keys: Vec<_> = grid.fields.keys().collect();
                let mut writer = BundleWriter::create(
                    Path::new(&params.output_dir),
                    "gfs-decoded-v1",
                    source,
                    json!({"field_keys": keys, "decoder_backend": "rust-grid-v2"}),
                )?;
                writer.f32(
                    "fields",
                    &[grid.fields.len() as u64, cells],
                    grid.fields.values().flatten().copied(),
                )?;
                writer
            };
            writer.f32("latitudes", &[cells], grid.latitudes.iter().copied())?;
            writer.f32("longitudes", &[cells], grid.longitudes.iter().copied())?;
            let manifest = writer.commit(path)?;
            Ok(json!({"manifest_path": manifest}))
        }
        "prepare_gfs_features" => {
            let params: FeatureParams =
                serde_json::from_value(params).map_err(|e| e.to_string())?;
            let grid = EcCodes::load()?.decode_gfs_file(
                Path::new(&params.path),
                gfs_max_messages(),
                default_points(),
            )?;
            let features = grid.native_features(params.observer, params.radius_km)?;
            let optics: Vec<[f32; 2]> = features
                .iter()
                .map(|row| {
                    cloud::optics(
                        row[6] as f32,
                        row[11] as f32,
                        row[12] as f32,
                        row[13] as f32,
                    )
                })
                .collect();
            Ok(json!({"features": features, "cloud_optics": optics, "field_count": 20}))
        }
        "decode_ecmwf_basic_to_files" | "derive_ecmwf_basic_to_files" => {
            let params: EcFileParams = serde_json::from_value(params).map_err(|e| e.to_string())?;
            let path = Path::new(&params.path);
            let source = source_identity(path)?;
            let tensor = ecmwf::decode_basic(
                &EcCodes::load()?,
                path,
                params.bbox,
                params.expected_cycle_utc.as_deref(),
                params.expected_forecast_hour,
                params.max_messages,
                params.max_points,
            )?;
            let writer = if method == "decode_ecmwf_basic_to_files" {
                tensor.write(Path::new(&params.output_dir), source)?
            } else {
                if tensor.latitudes.len() > derivation::MAX_CELLS {
                    return Err("EC canonical derivation limit exceeded (500000 cells)".into());
                }
                let (grid, spacing) = tensor.native_grid()?;
                let raw = derivation::RawGrid::from_gfs(&grid)?;
                let derived = derivation::derive(&raw)?;
                let mut metadata = tensor.metadata();
                metadata["pressure_levels"] = json!(derived.levels);
                metadata["column_fields"] = json!(derivation::COLUMN_FIELDS);
                metadata["profile_fields"] = json!(derivation::PROFILE_FIELDS);
                metadata["spacing_degrees"] = json!(spacing);
                metadata["cloud_proxy"] = json!("ec-basic-tcc-rh-v1");
                metadata["cloud_backend"] = json!("rust-f32-v3");
                let cells = grid.latitudes.len() as u64;
                let mut writer = BundleWriter::create(
                    Path::new(&params.output_dir),
                    "ecmwf-basic-derived-v1",
                    source,
                    metadata,
                )?;
                writer.f32("latitudes", &[cells], grid.latitudes.iter().copied())?;
                writer.f32("longitudes", &[cells], grid.longitudes.iter().copied())?;
                writer.f32(
                    "columns",
                    &[cells, derivation::COLUMN_FIELDS as u64],
                    derived.columns.iter().flatten().copied(),
                )?;
                writer.f32(
                    "profiles",
                    &[
                        cells,
                        derived.levels.len() as u64,
                        derivation::PROFILE_FIELDS as u64,
                    ],
                    derived.profiles.iter().flatten().copied(),
                )?;
                writer
            };
            Ok(json!({"manifest_path": writer.commit(path)?}))
        }
        "decode_cams_to_files" => {
            let params: CamsFileParams =
                serde_json::from_value(params).map_err(|e| e.to_string())?;
            if params.paths.len() > 1 {
                return Err("CAMS decode supports at most two rectangle paths".into());
            }
            let path = Path::new(&params.path);
            let source = source_identity(path)?;
            let mut paths = vec![path.to_owned()];
            for extra in &params.paths {
                let extra = Path::new(extra).to_owned();
                if !paths.contains(&extra) {
                    paths.push(extra);
                }
            }
            let mut identities = vec![source.clone()];
            identities.extend(
                paths
                    .iter()
                    .skip(1)
                    .map(|path| source_identity(path))
                    .collect::<Result<Vec<_>, _>>()?,
            );
            let codes = EcCodes::load()?;
            let optics = if paths.len() == 1 {
                cams::decode(&codes, path)?
            } else {
                cams::decode_many(&codes, &paths)?
            };
            for (path, identity) in paths.iter().zip(&identities) {
                if source_identity(path)? != *identity {
                    return Err("CAMS source changed while decoding".into());
                }
            }
            let mut writer = optics.write(Path::new(&params.output_dir), source)?;
            if paths.len() > 1 {
                writer.metadata_mut()["sources"] = json!(paths
                    .iter()
                    .zip(&identities)
                    .map(|(path, identity)| json!({"path":path,"identity":identity}))
                    .collect::<Vec<_>>());
            }
            Ok(json!({"manifest_path": writer.commit(path)?}))
        }
        "map_cams_to_files" => {
            let params: CamsMapParams =
                serde_json::from_value(params).map_err(|e| e.to_string())?;
            Ok(
                json!({"manifest_path": cams::map_file(Path::new(&params.path),
                Path::new(&params.target_manifest), Path::new(&params.output_dir))?}),
            )
        }
        "interpolate_cams_to_files" => {
            let params: CamsInterpolateParams =
                serde_json::from_value(params).map_err(|e| e.to_string())?;
            Ok(
                json!({"manifest_path": cams::interpolate_files(Path::new(&params.left_path),
                Path::new(&params.right_path), Path::new(&params.target_manifest),
                &params.target_time, Path::new(&params.output_dir))?}),
            )
        }
        "decode_ecmwf_full_to_files" => {
            let p: EcFullFileParams = serde_json::from_value(params).map_err(|e| e.to_string())?;
            let path = Path::new(&p.path);
            let source = source_identity(path)?;
            let tensor = ecmwf::decode_full(
                &EcCodes::load()?,
                path,
                p.model_levels,
                ecmwf::DecodeOptions {
                    bbox: p.bbox,
                    expected_cycle_utc: p.expected_cycle_utc.as_deref(),
                    expected_hour: p.expected_forecast_hour,
                    max_messages: p.max_messages,
                    max_points: p.max_points,
                },
            )?;
            let writer = tensor.write(Path::new(&p.output_dir), source)?;
            Ok(json!({"manifest_path": writer.commit(path)?}))
        }
        "decode_cams_spectral_to_files" => {
            let p: SpectralFileParams =
                serde_json::from_value(params).map_err(|e| e.to_string())?;
            let path = Path::new(&p.path);
            let source = source_identity(path)?;
            let optics = spectral::decode(
                &EcCodes::load()?,
                path,
                p.max_messages,
                p.max_points,
                p.expected_valid_time.as_deref(),
            )?;
            let writer = optics.write(Path::new(&p.output_dir), source)?;
            Ok(json!({"manifest_path": writer.commit(path)?}))
        }
        "diagnose_cams_spectral" => {
            let p: SpectralDiagnosticParams =
                serde_json::from_value(params).map_err(|e| e.to_string())?;
            spectral::diagnose_file(
                &EcCodes::load()?,
                Path::new(&p.path),
                p.observer,
                &p.wavelengths_nm,
                p.solar_elevation_deg,
                p.scattering_angle_deg,
                p.expected_valid_time.as_deref(),
            )
        }
        _ => Err(format!(
            "unsupported method: {method}; full forecasting has not been migrated"
        )),
    }
}

fn response(bytes: &[u8]) -> Value {
    let parsed = serde_json::from_slice::<Value>(bytes);
    let id = parsed
        .as_ref()
        .ok()
        .and_then(|value| value.get("id"))
        .filter(|value| {
            value
                .as_str()
                .is_some_and(|id| !id.is_empty() && id.len() <= 128)
        })
        .cloned()
        .unwrap_or(Value::Null);
    let request = parsed
        .map_err(|error| error.to_string())
        .and_then(|value| serde_json::from_value::<Request>(value).map_err(|e| e.to_string()));
    let request = match request {
        Ok(request)
            if !request.id.is_empty() && request.id.len() <= 128 && request.params.is_object() =>
        {
            request
        }
        _ => {
            return json!({"id": id, "type": "error", "error": {
                "code": "invalid_request", "message": "expected id, method and object params",
            }})
        }
    };
    match dispatch(&request.method, request.params) {
        Ok(result) => json!({"id": request.id, "type": "result", "result": result}),
        Err(message) => json!({"id": request.id, "type": "error", "error": {
            "code": "operation_failed", "message": message,
        }}),
    }
}

/// Shared bounded framing; oversized lines are drained without buffering.
pub(crate) enum Line {
    End,
    Oversized,
    Data,
}
pub(crate) fn read_line(input: &mut impl BufRead, line: &mut Vec<u8>) -> io::Result<Line> {
    line.clear();
    let mut oversized = false;
    let mut seen = false;
    loop {
        let chunk = input.fill_buf()?;
        if chunk.is_empty() {
            break;
        }
        seen = true;
        let newline = chunk.iter().position(|&byte| byte == b'\n');
        let take = newline.map_or(chunk.len(), |pos| pos + 1);
        if !oversized {
            if line.len() + take > MAX_LINE_BYTES {
                oversized = true;
                line.clear();
            } else {
                line.extend_from_slice(&chunk[..take]);
            }
        }
        input.consume(take);
        if newline.is_some() {
            break;
        }
    }
    Ok(if !seen {
        Line::End
    } else if oversized {
        Line::Oversized
    } else {
        Line::Data
    })
}
/// Sequential migration/debug endpoint, not the production desktop service.
pub fn serve(mut input: impl BufRead, mut output: impl Write) -> io::Result<()> {
    let mut line = Vec::new();
    loop {
        let message = match read_line(&mut input, &mut line)? {
            Line::End => break,
            Line::Oversized => json!({"id": null, "type": "error", "error": {
                "code": "invalid_request", "message": "request exceeds 1 MiB",
            }}),
            Line::Data => response(&line),
        };
        serde_json::to_writer(&mut output, &message)?;
        output.write_all(b"\n")?;
        output.flush()?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn hello_does_not_advertise_unported_forecasts() {
        let result = response(br#"{"id":"h","method":"hello"}"#);
        assert_eq!(
            result["result"]["capabilities"],
            json!([
                "score_window",
                "decode_grib",
                "decode_gfs",
                "prepare_gfs_features",
                "derive_gfs",
                "decode_gfs_to_files",
                "derive_gfs_to_files",
                "decode_ecmwf_basic_to_files",
                "derive_ecmwf_basic_to_files",
                "decode_cams_to_files",
                "map_cams_to_files",
                "interpolate_cams_to_files",
                "decode_cams_spectral_to_files",
                "diagnose_cams_spectral",
                "decode_ecmwf_full_to_files",
                "time_contract",
                "solar_events",
                "solar_snapshots",
                "solar_crossing",
                "solar_shadow_speed",
                "solar_glow_window",
                "prepare_regional_solar_to_files",
                "assess_stored_regional_to_files",
                "score_observer_fields",
                "score_nowcast_fields",
                "score_stored_regional_to_files",
                "diagnose_observer_fields",
                "diagnose_stored_regional_to_files",
                "forecast_characteristics",
                "diagnose_profile_columns",
                "diagnose_stored_profiles_to_files",
                "build_stored_frames_to_files",
                "assemble_observer_frames",
                "quantize_frame_fields",
                "validate_point_core",
                "materialize_point_core",
                "forecast_stored_point_to_files",
                "gfs_data",
                "plan_gfs_inputs",
                "forecast_cached_gfs_point_to_files",
                "cams_data",
                "calculate_earth_engine_aod",
                "ecmwf_data",
                "materialize_ecmwf_point",
                "plan_ecmwf_inputs",
                "forecast_cached_ecmwf_point_to_files",
                "build_stored_cross_section",
                "render_html_report",
                "forecast_ecmwf_service_to_files",
                "read_ecmwf_service_cross_section",
                "forecast_ecmwf_service_cached",
                "read_cached_ecmwf_service_cross_section",
                "location",
                "check_desktop_data",
                "desktop_inventory",
                "desktop_location",
                "desktop_publications",
                "desktop_prepare",
                "desktop_session",
                "desktop_nowcast_gfs_plan",
                "desktop_nowcast_prior",
                "desktop_nowcast_prior_batch",
                "desktop_nowcast_prior_shared_batch",
                "desktop_nowcast_prior_decoded_batch",
                "desktop_nowcast_calibrated_retrieval",
                "desktop_nowcast_adjustment",
                "desktop_nowcast_assessment",
                "desktop_nowcast_prepare",
                "desktop_nowcast_hsd_cache",
                "desktop_nowcast_hsd_export",
                "desktop_nowcast_hsd_motion",
                "desktop_nowcast_hsd_advection",
                "desktop_nowcast_hsd_retrieval",
                "decode_raw_hsd_to_files",
                "warm_hsd_cache",
                "assess_radar_images"
            ])
        );
        let result = response(br#"{"id":"f","method":"forecast"}"#);
        assert_eq!(result["type"], "error");
    }

    #[test]
    fn malformed_params_and_shapes_return_errors() {
        for request in [
            r#"{"id":"","method":"hello"}"#,
            r#"{"id":"x","method":"hello","params":[]}"#,
            r#"{"id":"x","method":"score_window","params":{"values":[1],"active":[],"widths":[5]}}"#,
            r#"{"id":"x","method":"score_window","params":{"values":[null],"active":[true],"widths":[5]}}"#,
            "not json",
        ] {
            assert_eq!(response(request.as_bytes())["type"], "error", "{request}");
        }
    }

    #[test]
    fn scores_real_window() {
        let result = response(
            br#"{"id":"s","method":"score_window","params":{
            "values":[0.8,0.8,0.8,0.8,0.1],"active":[true,true,true,true,true],
            "widths":[3,4,5,6,7]}}"#,
        );
        assert_eq!(result["type"], "result");
        let minutes = result["result"]["window_minutes"].as_f64().unwrap();
        assert!((minutes - 20.0).abs() < 1e-12);
        let mean = result["result"]["window_pre_response_index"]
            .as_f64()
            .unwrap();
        assert!((mean - 0.73).abs() < 1e-14);
    }

    #[test]
    fn oversized_line_is_drained_and_next_request_survives() {
        let mut input = vec![b'x'; MAX_LINE_BYTES + 20];
        input.extend_from_slice(b"\n{\"id\":\"next\",\"method\":\"hello\"}\n");
        let mut output = Vec::new();
        serve(Cursor::new(input), &mut output).unwrap();
        let results: Vec<Value> = output
            .split(|&byte| byte == b'\n')
            .filter(|line| !line.is_empty())
            .map(|line| serde_json::from_slice(line).unwrap())
            .collect();
        assert_eq!(results.len(), 2);
        assert_eq!(results[0]["type"], "error");
        assert_eq!(results[1]["id"], "next");
        assert_eq!(results[1]["type"], "result");
    }

    #[test]
    fn final_line_without_newline_is_processed() {
        let mut output = Vec::new();
        serve(
            Cursor::new(b"{\"id\":\"last\",\"method\":\"hello\"}"),
            &mut output,
        )
        .unwrap();
        assert_eq!(
            serde_json::from_slice::<Value>(&output).unwrap()["id"],
            "last"
        );
    }
}
