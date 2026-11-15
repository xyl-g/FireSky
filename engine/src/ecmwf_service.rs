//! EC provenance, HTML and profile-aware response over the native point workflow.
use crate::{
    assessment::from_epoch,
    cams, cross_section, ecmwf_point,
    frames::Point,
    html_report,
    tensor_bundle::{source_identity, BundleWriter, SourceIdentity},
    time_contract,
};
use chrono::{DateTime, Duration, Utc};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    path::{Path, PathBuf},
    time::Instant,
};

pub(crate) struct Context<'a> {
    pub output: &'a Path,
    pub plan: &'a Value,
    pub ec: &'a Value,
    pub evidence: Value,
    pub point: &'a Point,
    pub cycle: DateTime<Utc>,
    pub source_path: PathBuf,
    pub source_identity: SourceIdentity,
}
fn z(time: DateTime<Utc>) -> String {
    time.to_rfc3339_opts(
        if time.timestamp_subsec_micros() == 0 {
            chrono::SecondsFormat::Secs
        } else {
            chrono::SecondsFormat::Micros
        },
        true,
    )
}
fn round3(value: f64) -> f64 {
    format!("{value:.3}").parse().unwrap()
}
pub(crate) fn append(writer: &mut BundleWriter, context: Context<'_>) -> Result<Value, String> {
    let started = Instant::now();
    let Context {
        output,
        plan,
        ec,
        evidence,
        point,
        cycle,
        source_path,
        source_identity: profile_identity,
    } = context;
    if plan["contract"] != "rust-ecmwf-input-plan-v1"
        || plan["inputs_located"] != true
        || ec["contract"] != "rust-ecmwf-point-report-v1"
    {
        return Err("EC service requires the validated EC event/point workflow".into());
    }
    let provider = &plan["provider"];
    if provider["model"] != "ifs"
        || provider["member"] != "ecmwf-ifs-basic"
        || provider["resolution"] != "0p25"
        || provider["source"].as_str().is_none()
    {
        return Err("EC service provider provenance is missing".into());
    }
    let mut valid_times = BTreeSet::new();
    for step in ec["report"]["steps"]
        .as_array()
        .ok_or("missing EC report steps")?
    {
        for time in step["cloud_source_times_utc"]
            .as_array()
            .ok_or("missing EC step source times")?
        {
            valid_times.insert(z(time_contract::parse_utc(
                time.as_str().ok_or("invalid EC source time")?,
            )?));
        }
    }
    let mut optical_times = BTreeSet::new();
    for hour in plan["forecast_hours"]
        .as_array()
        .ok_or("missing EC source hours")?
    {
        let time = cycle + Duration::hours(hour.as_i64().ok_or("invalid EC forecast hour")?);
        let (a, b, _) = cams::bracket(&z(time))?;
        optical_times.extend([z(a), z(b)]);
    }
    let quality = &ec["native_quality"];
    let dominant = &ec["dominant_burnable"];
    let profile = json!({"file":"cross_section_source.json","identity":profile_identity,
        "contract":"rust-cross-section-source-v1","layout":"verified-per-hour-cell-pressure-24"});
    let mut provenance = json!({
        "model_source":"ec","model":"ifs","member":provider["member"],"contract":"public-open-data/basic",
        "resolution":provider["resolution"],"source":provider["source"],"cycle_utc":z(cycle),
        "valid_times_utc":valid_times,"evaluation_times_utc":evidence["evaluation_times_utc"],
        "forecast_hours":plan["forecast_hours"],"files":evidence["files"],
        "layer_profile_path":null,"pressure_levels":evidence["pressure_levels"],
        "profile_grid":evidence["profile_grid"],"source_tensor_metadata":evidence["source_tensor_metadata"],
        "native_derived_cache":evidence["native_derived_cache"],
        "native_core":"national_assess_batch+build_national_frame_payloads",
        "legacy_assess_timestep_used":false,"native_quality_index":quality["quality_index"],
        "native_point_core_version":8,"dominant_burnable":dominant,"native_frame_version":103,
        "cloud_source_fields":{
            "total_cloud_cover":"EC surface TCC (%)",
            "vertical_cloud_proxy":"EC pressure-level RH conditioned by TCC",
            "cloud_liquid":"unavailable_in_public_basic_contract",
            "cloud_ice":"unavailable_in_public_basic_contract",
            "aggregate_layer_cloud":"unavailable_in_public_basic_contract"},
        "time_interpolation":"linear_native_fields_with_native_wind_advection",
        "advection":"native-family-wind-semilagrangian-from-EC-pl-u-v",
        "domain_geometry":{"candidate_radius_km":plan["candidate_radius_km"],
            "native_solar_trace_radius_km":plan["native_solar_trace_radius_km"],
            "target_domain_radius_km":plan["source_domain_radius_km"],
            "target_grid_halo_semantics":"candidate_plus_native_solar_trace; native cloud-family wind advection uses EC PL u/v"},
        "cams_aerosol_semantics":"cams-aerext532-11level-vertical-profile-native-interpolation",
        "cams_files":evidence["cams_files"],"cams_source_descriptions":evidence["cams_source_descriptions"],
        "cams_valid_times_utc":optical_times,"cams_profile_validated":true,"cams_profile_consumed":true,
        "cams_profile_mode":"native-11level-vertical-extinction",
        "cloud_semantics":"tcc-horizontal-occupancy; pressure-level-rh-conditional-proxy",
        "cloud_phase_semantics":"unknown_without_model_level_clwc_ciwc",
        "unsupported_reasons":["unsupported_missing_model_level_cloud_fields","public_open_data_only_total_cloud_cover"],
        "gfs_cache_used":false,"cams_cache_used":true,"observation_used":false,
        "confidence_score":0.42,"confidence_semantics":ecmwf_point::CONFIDENCE,
        "timing":evidence["timing"],
        "native_provenance_contract":"rust-ecmwf-service-provenance-v1",
        "native_profile_source":profile,"native_runtime":"rust",
        "consumed_source_identities":evidence["consumed_source_identities"],
        "cams_consumed_valid_times_utc":evidence["cams_consumed_valid_times_utc"],
        "cams_valid_times_semantics":"legacy-all-model-source-hour-brackets; consumed times recorded separately",
        "timing_scope":"native input/scoring/frame/point phases including durable tensor output; excludes service export",
    });
    let mut report = ec["report"].clone();
    report["html_report_path"] = json!(output.join("report.html"));
    let generated = html_report::host_generated_label()?;
    let html = html_report::render(&report, &generated)?;
    let html_identity = writer.html_artifact("report", &html)?;
    let html_descriptor =
        json!({"file":"report.html","identity":html_identity,"contract":"rust-html-report-v1"});
    let mut response = ecmwf_point::web_payload(&report, quality, point.elevation_m);
    response["source_roles"] = json!({"primary":"ec_numerical_model","auxiliary":"cams_aerosol"});
    response["confidence_score_semantics"] = json!(ecmwf_point::CONFIDENCE);
    for (suffix, key) in [
        ("pressure_family", "pressure_family"),
        ("cloud_phase", "cloud_phase"),
        ("cloud_base_asl_mean_m", "cloud_base_asl_mean_m"),
        ("cloud_top_asl_mean_m", "cloud_top_asl_mean_m"),
        ("target_terrain_asl_mean_m", "target_terrain_asl_mean_m"),
        ("cloud_base_agl_mean_m", "cloud_base_agl_mean_m"),
        ("cloud_top_agl_mean_m", "cloud_top_agl_mean_m"),
        (
            "cloud_base_above_observer_mean_m",
            "cloud_base_above_observer_mean_m",
        ),
        (
            "cloud_top_above_observer_mean_m",
            "cloud_top_above_observer_mean_m",
        ),
        ("cloud_genus_diagnosis", "genus_diagnosis"),
    ] {
        response[format!("dominant_burnable_{suffix}")] = dominant[key].clone();
    }
    response["cloud_genus_proxy"] = dominant["genus_proxy"].clone();
    response["cloud_phase_proxy"] = dominant["phase_proxy"].clone();
    response["point_refinement"] = provenance["native_core"].clone();
    response["diagnostic_source"] = json!("ec-native-regional-cloud-and-solar-profile");
    response["geometry_window"] = Value::Null;
    response["boundary_uncertainty"] = Value::Null;
    response["data_provenance"] = json!({
        "model_source":"ec","contract":provenance["contract"],"cycle_utc":provenance["cycle_utc"],
        "files":provenance["files"],"gfs_cache_used":false,"cams_cache_used":true,"observation_used":false,
        "confidence_semantics":ecmwf_point::CONFIDENCE,"cams_files":provenance["cams_files"],
        "cams_valid_times_utc":provenance["cams_valid_times_utc"],
    });
    let identity_input = json!({"contract":"rust-ecmwf-service-profile-v1",
        "profile_source":profile,"point":{"lat":point.lat,"lon":point.lon,"elevation_m":point.elevation_m,
            "timezone_name":point.timezone_name},
        "timezone_database":chrono_tz::IANA_TZDB_VERSION,"native_abi":crate::native::NATIVE_ABI_VERSION});
    let digest: String =
        Sha256::digest(serde_json::to_vec(&identity_input).map_err(|e| e.to_string())?)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
    let profile_key = format!("ec-native:{digest}");
    let offset = quality["best_index"].as_u64().map_or(Ok(0.0), |i| {
        let best = time_contract::parse_zoned(
            report["steps"][i as usize]["valid_time_local"]
                .as_str()
                .ok_or("missing EC best time")?,
        )?;
        let anchor = time_contract::parse_zoned(
            report["anchor_local"].as_str().ok_or("missing EC anchor")?,
        )?;
        Ok::<_, String>(
            (best - anchor)
                .num_microseconds()
                .ok_or("EC offset overflow")? as f64
                / 60e6,
        )
    })?;
    let offset = cross_section::snap_offset_to_event_grid(&source_path, point, offset)?;
    let section = cross_section::for_point(source_path, profile_identity, point, offset)?;
    response["cross_section"] = section;
    response["cross_section_request"] = json!({
        "target_date":report["target_date"],"phenomenon":report["phenomenon"],
        "model_source":"ec","forecast_mode":"model","satellite_source":"off",
        "radius_km":1000.0,"spacing_deg":0.25,"gfs_resolution":"0p25","regions":["custom"],
        "elevation_m":point.elevation_m,"profile_key":profile_key,
    });
    response["map_token"] = Value::Null;
    response["map_times"] = json!([]);
    response["html_report_url"] = Value::Null;
    response["html_artifact"] = html_descriptor.clone();
    response["response_cache"] =
        json!({"status":"not-integrated","contract":"rust-ecmwf-service-v1"});
    provenance["target_date"] = report["target_date"].clone();
    provenance["phenomenon"] = report["phenomenon"].clone();
    response["ecmwf_provenance"] = provenance.clone();
    let result = json!({
        "contract":"rust-ecmwf-service-v1","report":report,"native_quality":quality,
        "provenance":provenance,"response":response,"html_artifact":html_descriptor,
        "observer":{"name":point.name,"lat":point.lat,"lon":point.lon,"elevation_m":point.elevation_m,"timezone_name":point.timezone_name},
        "profile_key":profile_key,"service_assembly_and_html_export_ms":started.elapsed().as_secs_f64()*1000.0,
        "production_http_complete":false,"response_cache_complete":false,"public_forecast_complete":false,
    });
    let identity = writer.json_artifact("ecmwf_service", &result)?;
    Ok(
        json!({"file":"ecmwf_service.json","identity":identity,"contract":"rust-ecmwf-service-v1",
        "provenance_complete":true,"profile_layout":"verified-per-hour-cell-pressure-24",
        "html_artifact":html_descriptor,
        "service_assembly_serialization_and_output_ms":started.elapsed().as_secs_f64()*1000.0,
        "public_forecast_complete":false}),
    )
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadParams {
    manifest_path: PathBuf,
    manifest_identity: SourceIdentity,
    profile_key: String,
    lat: f64,
    lon: f64,
    offset_minutes: f64,
}
pub fn read_cross_section(params: ReadParams) -> Result<Value, String> {
    let (manifest, identity): (Value, _) =
        crate::request_file::read(&params.manifest_path, 256 * 1024)?;
    if identity != params.manifest_identity
        || params.manifest_path.file_name().and_then(|v| v.to_str()) != Some("manifest.json")
        || manifest["schema"] != "firesky.tensor-bundle.v1"
        || manifest["kind"] != "ecmwf-service-forecast-v1"
        || !manifest["producer"]
            .as_str()
            .is_some_and(|v| v.starts_with("firesky-engine/"))
        || !params.lat.is_finite()
        || !params.lon.is_finite()
    {
        return Err("invalid EC service manifest identity or request".into());
    }
    let root = params
        .manifest_path
        .parent()
        .ok_or("missing EC service root")?;
    let descriptor = &manifest["metadata"]["ecmwf_service"];
    if descriptor["file"] != "ecmwf_service.json"
        || descriptor["contract"] != "rust-ecmwf-service-v1"
    {
        return Err("invalid EC service descriptor".into());
    }
    let (service, service_identity): (Value, _) =
        crate::request_file::read(&root.join("ecmwf_service.json"), 16 * 1024 * 1024)?;
    let expected: SourceIdentity =
        serde_json::from_value(descriptor["identity"].clone()).map_err(|e| e.to_string())?;
    if expected != service_identity
        || service["contract"] != "rust-ecmwf-service-v1"
        || service["profile_key"].as_str() != Some(params.profile_key.as_str())
        || !params.profile_key.starts_with("ec-native:")
        || params.profile_key.len() != 74
        || !params.profile_key[10..]
            .bytes()
            .all(|v| v.is_ascii_hexdigit() && !v.is_ascii_uppercase())
    {
        return Err("EC service identity/profile key mismatch".into());
    }
    let observer = &service["observer"];
    let value = |key: &str| {
        observer[key]
            .as_f64()
            .ok_or_else(|| format!("missing EC observer {key}"))
    };
    let point = Point {
        name: observer["name"]
            .as_str()
            .ok_or("missing observer name")?
            .into(),
        lat: value("lat")?,
        lon: value("lon")?,
        elevation_m: value("elevation_m")?,
        timezone_name: observer["timezone_name"]
            .as_str()
            .ok_or("missing observer timezone")?
            .into(),
    };
    if (point.lat - params.lat).abs() > 1e-6 || (point.lon - params.lon).abs() > 1e-6 {
        return Err("EC cross-section coordinate does not match its point forecast".into());
    }
    let source = &service["provenance"]["native_profile_source"];
    let source_path = root.join("cross_section_source.json");
    if source["file"] != "cross_section_source.json"
        || source["contract"] != "rust-cross-section-source-v1"
    {
        return Err("EC profile source must belong to its service bundle".into());
    }
    let profile_identity =
        serde_json::from_value(source["identity"].clone()).map_err(|e| e.to_string())?;
    let result =
        cross_section::for_point(source_path, profile_identity, &point, params.offset_minutes)?;
    if source_identity(&params.manifest_path)? != identity
        || source_identity(&root.join("ecmwf_service.json"))? != service_identity
    {
        return Err("EC service changed during cross-section reading".into());
    }
    Ok(result)
}

pub(crate) fn evaluation_times(epochs: &[f64]) -> Result<Vec<String>, String> {
    epochs.iter().map(|&v| from_epoch(v).map(z)).collect()
}
pub(crate) fn timing(values: [f64; 5]) -> Value {
    json!({"input_prepare_seconds":round3(values[0]),"native_compute_seconds":round3(values[1]),
        "frame_assembly_seconds":round3(values[2]),"postprocess_seconds":round3(values[3]),"total_seconds":round3(values[4])})
}
