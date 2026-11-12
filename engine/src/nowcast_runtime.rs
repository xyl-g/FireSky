//! Native cached-calibrated HSD retrieval, assessment and event quality.
//!
//! Ordinary requests resolve HSD/GFS/CAMS inputs natively. Advanced callers may
//! instead supply the bounded calibrated-frame manifests and prior cache
//! contexts consumed by `desktop_nowcast_adjustment`.

use crate::{astronomy, desktop_nowcast_adjustment, desktop_nowcast_assessment, time_contract};
use chrono::{DateTime, Utc};
use npyz::WriterBuilder;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

fn check_cancel(cancel: &AtomicBool) -> Result<(), String> {
    if cancel.load(Ordering::Acquire) {
        Err("nowcast operation cancelled".into())
    } else {
        Ok(())
    }
}

fn config_path(payload: &Value, key: &str, env: &str, default: &str) -> Result<PathBuf, String> {
    let workspace = payload["_runtime_config"]["workspace"]
        .as_str()
        .map(PathBuf::from)
        .unwrap_or(std::env::current_dir().map_err(|e| e.to_string())?);
    let path = payload["_runtime_config"][key]
        .as_str()
        .map(PathBuf::from)
        .or_else(|| std::env::var_os(env).map(PathBuf::from))
        .or_else(|| {
            (key == "satellite_cache_dir")
                .then(|| std::env::var_os("FIRESKY_SATELLITE_CACHE_DIR").map(PathBuf::from))
                .flatten()
        })
        .unwrap_or_else(|| PathBuf::from(default));
    Ok(if path.is_absolute() {
        path
    } else {
        workspace.join(path)
    })
}

fn hash(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|v| format!("{v:02x}"))
        .collect()
}

fn write_f32(path: &Path, shape: &[u64], values: impl Iterator<Item = f32>) -> Result<(), String> {
    let file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    let mut output = std::io::BufWriter::new(file);
    let mut writer = npyz::WriteOptions::new()
        .default_dtype()
        .shape(shape)
        .writer(&mut output)
        .begin_nd()
        .map_err(|e| e.to_string())?;
    for value in values {
        writer.push(&value).map_err(|e| e.to_string())?;
    }
    writer.finish().map_err(|e| e.to_string())?;
    std::io::Write::flush(&mut output).map_err(|e| e.to_string())?;
    output.get_ref().sync_all().map_err(|e| e.to_string())
}

fn longitude_in_bbox(bbox: [f64; 4], longitude: f64) -> bool {
    let longitude = (longitude + 180.0).rem_euclid(360.0) - 180.0;
    let contains = |value| {
        if bbox[0] <= bbox[1] {
            value >= bbox[0] - 1e-5 && value <= bbox[1] + 1e-5
        } else {
            (bbox[0] - 1e-5..=180.0 + 1e-5).contains(&value)
                || (-180.0 - 1e-5..=bbox[1] + 1e-5).contains(&value)
        }
    };
    contains(longitude) || (longitude == -180.0 && contains(180.0))
}

fn decoded_prior_grid(
    path: &Path,
    root: &Path,
    cycle: DateTime<Utc>,
    hour: u32,
    bbox: [f64; 4],
    codes: &crate::grib::EcCodes,
) -> Result<PathBuf, String> {
    let source = crate::tensor_bundle::source_identity(path)?;
    let decoder = codes.cache_identity()?;
    let identity = json!({"source": source, "decoder": decoder,
        "cycle":time_contract::iso(cycle),"hour":hour,"bbox":bbox,"version":3});
    let key = hash(&serde_json::to_vec(&identity).map_err(|e| e.to_string())?);
    fs::create_dir_all(root).map_err(|e| e.to_string())?;
    let target = root.join(&key);
    let _guard = crate::cache_lock::Lock::acquire(&root.join(format!("{key}.lock")), 300.0)?;
    if target.is_dir() {
        let receipt: Value = serde_json::from_slice(
            &fs::read(target.join("identity.json")).map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
        if receipt["input"] != identity {
            return Err("native nowcast decoded cache identity differs".into());
        }
        for name in ["coordinates.npy", "fields.npy", "manifest.json"] {
            if serde_json::to_value(crate::tensor_bundle::source_identity(&target.join(name))?)
                .map_err(|e| e.to_string())?
                != receipt[name]
            {
                return Err("native nowcast decoded cache content differs".into());
            }
        }
        return Ok(target);
    }
    let mut grid = codes.decode_gfs_frame(path, 512, 5_000_000, Some((cycle, hour)))?;
    // Covering regional caches may have different extents at adjacent hours.
    // Canonicalize their shared requested domain before interpolation.
    let mut indexes = (0..grid.latitudes.len())
        .filter(|&index| {
            let lat = f64::from(grid.latitudes[index]);
            let lon = (f64::from(grid.longitudes[index]) + 180.0).rem_euclid(360.0) - 180.0;
            longitude_in_bbox(bbox, lon) && (bbox[3] - 1e-5..=bbox[2] + 1e-5).contains(&lat)
        })
        .collect::<Vec<_>>();
    indexes.sort_by(|&a, &b| {
        grid.latitudes[a]
            .total_cmp(&grid.latitudes[b])
            .then(grid.longitudes[a].total_cmp(&grid.longitudes[b]))
    });
    if indexes.is_empty() {
        return Err("native GFS prior requested domain is empty".into());
    }
    grid.latitudes = indexes.iter().map(|&index| grid.latitudes[index]).collect();
    grid.longitudes = indexes
        .iter()
        .map(|&index| grid.longitudes[index])
        .collect();
    for values in grid.fields.values_mut() {
        *values = indexes.iter().map(|&index| values[index]).collect();
    }
    let pending = tempfile::Builder::new()
        .prefix(".nowcast-grid-")
        .tempdir_in(root)
        .map_err(|e| e.to_string())?;
    let n = grid.latitudes.len() as u64;
    write_f32(
        &pending.path().join("coordinates.npy"),
        &[2, n],
        grid.latitudes.iter().chain(&grid.longitudes).copied(),
    )?;
    write_f32(
        &pending.path().join("fields.npy"),
        &[grid.fields.len() as u64, n],
        grid.fields.values().flatten().copied(),
    )?;
    let fields = grid
        .fields
        .keys()
        .enumerate()
        .map(|(index, key)| {
            json!({
                "level_type":key.0, "level":key.1, "name":key.2, "index":index,
            })
        })
        .collect::<Vec<_>>();
    fs::write(
        pending.path().join("manifest.json"),
        serde_json::to_vec(&json!({"version":7,"fields":fields})).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    let mut receipt = json!({"input":identity});
    for name in ["coordinates.npy", "fields.npy", "manifest.json"] {
        receipt[name] = serde_json::to_value(crate::tensor_bundle::source_identity(
            &pending.path().join(name),
        )?)
        .map_err(|e| e.to_string())?;
    }
    fs::write(
        pending.path().join("identity.json"),
        serde_json::to_vec(&receipt).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    if crate::tensor_bundle::source_identity(path)? != source {
        return Err("GFS source changed during native nowcast decoding".into());
    }
    fs::rename(pending.path(), &target).map_err(|e| e.to_string())?;
    Ok(target)
}

fn aerosol_reading(
    payload: &Value,
    context: &Value,
    azimuth: f64,
    allow_download: bool,
) -> Result<Value, String> {
    if let Some(aerosol) = payload.get("aerosol").filter(|v| v.is_object()) {
        return Ok(aerosol.clone());
    }
    let areas = crate::desktop_nowcast::cams_areas(context)?;
    let cache_dir = config_path(
        payload,
        "cams_cache_dir",
        "FIRESKY_CAMS_CACHE_DIR",
        ".cache/cams",
    )?;
    let mut paths = Vec::new();
    for area in areas {
        let params = serde_json::from_value(json!({
            "operation":"resolve", "cache_dir":cache_dir.clone(),
            "area":area, "target_utc":context["anchor"],
            "allow_download":allow_download,"require_exact_valid_time":false,
        }))
        .map_err(|e| e.to_string())?;
        let result = crate::cams_data::execute(params)?;
        let file = result["files"].as_array().and_then(|v| v.first()).ok_or(
            "native nowcast requires a usable CAMS optical cache; no clear-air fallback is allowed",
        )?;
        paths.push(PathBuf::from(text(file, "path")?));
        if let Some(extra) = file["paths"].as_array() {
            for path in extra {
                paths.push(PathBuf::from(
                    path.as_str().ok_or("invalid wrapped CAMS file path")?,
                ));
            }
        }
    }
    paths.sort();
    paths.dedup();
    let optics = crate::cams::decode_many(&crate::grib::EcCodes::load()?, &paths)?;
    let lat = finite(&context["request"], "lat")?;
    let lon = finite(&context["request"], "lon")?;
    let radius = finite(&context["request"], "radius_km")?;
    let lat1 = lat.to_radians();
    let mut samples = Vec::new();
    let mut nearest = None::<(f64, f64)>;
    for ((&sample_lat, &sample_lon), &aod) in optics
        .latitudes
        .iter()
        .zip(&optics.longitudes)
        .zip(&optics.aod)
    {
        if !aod.is_finite() || !(0.0..=100.0).contains(&aod) {
            continue;
        }
        let sample_lon = (sample_lon + 180.0).rem_euclid(360.0) - 180.0;
        let lat2 = sample_lat.to_radians();
        let dlat = lat2 - lat1;
        let dlon = ((sample_lon - lon + 180.0).rem_euclid(360.0) - 180.0).to_radians();
        let h = (dlat / 2.0).sin().powi(2) + lat1.cos() * lat2.cos() * (dlon / 2.0).sin().powi(2);
        let distance = 2.0 * 6371.0088 * h.sqrt().atan2((1.0 - h).max(0.0).sqrt());
        let bearing = if distance <= 1.0 {
            azimuth
        } else {
            (dlon.sin() * lat2.cos())
                .atan2(lat1.cos() * lat2.sin() - lat1.sin() * lat2.cos() * dlon.cos())
                .to_degrees()
                .rem_euclid(360.0)
        };
        let delta = ((bearing - azimuth + 180.0).rem_euclid(360.0) - 180.0).abs();
        let along = distance * delta.to_radians().cos();
        let cross = (distance * delta.to_radians().sin()).abs();
        if distance > radius + 150.0
            || !((distance <= radius && delta <= 75.0)
                || ((0.0..=radius).contains(&along) && cross <= 150.0))
        {
            continue;
        }
        if nearest.is_none_or(|(old, _)| distance < old) {
            nearest = Some((distance, aod));
        }
        samples.push(
            json!({"lat":sample_lat,"lon":sample_lon,"distance_km":distance,
            "bearing_deg":bearing,"aod550":aod}),
        );
    }
    let aod = nearest
        .ok_or("CAMS corridor did not contain usable AOD samples")?
        .1;
    Ok(
        json!({"aod550":aod,"quality":"cams","source":format!("cams-native corridor {}",paths.iter().map(|path| path.display().to_string()).collect::<Vec<_>>().join(",")),
        "observer_lat":lat,"observer_lon":lon,"path_samples":samples}),
    )
}

fn ordinary_payload(payload: &Value, cancel: &AtomicBool) -> Result<Value, String> {
    check_cancel(cancel)?;
    let mut normalized = payload.clone();
    let object = normalized
        .as_object_mut()
        .ok_or("nowcast payload must be an object")?;
    object.entry("elevation_m").or_insert(json!(0.0));
    let context = crate::desktop_nowcast::execute(crate::desktop_nowcast::Params {
        payload: normalized,
        fast: true,
    })?;
    let allow_download = payload["allow_download"].as_bool().unwrap_or(true);
    let satellite = config_path(
        payload,
        "satellite_cache_dir",
        "FIRESKY_HSD_CACHE_DIR",
        ".cache/himawari-hsd",
    )?;
    let hsd_params = json!({"cache_dir":satellite,"reference_time_utc":context["lookup"],
        "lat":context["request"]["lat"],"lon":context["request"]["lon"],
        "radius_km":context["request"]["radius_km"],"count":2,"max_age_minutes":30.0});
    let cached = crate::desktop_hsd::execute(
        serde_json::from_value(hsd_params.clone()).map_err(|e| e.to_string())?,
    )?;
    if cached["frames"].as_array().is_none_or(|v| v.len() < 2) && allow_download {
        check_cancel(cancel)?;
        crate::hsd_provider::execute(hsd_params.clone())?;
    }
    check_cancel(cancel)?;
    let mut export_params = hsd_params;
    export_params["output_dir"] = json!(satellite.join(".rust-bundles"));
    let exported = crate::desktop_hsd::export(
        serde_json::from_value(export_params).map_err(|e| e.to_string())?,
    )
    .map_err(|e| format!("native nowcast HSD export: {e}"))?;
    let mut frames = exported["frames"]
        .as_array()
        .filter(|v| !v.is_empty())
        .ok_or("native nowcast has no complete calibrated HSD observations")?
        .clone();
    frames.sort_by(|a, b| {
        a["valid_time_utc"]
            .as_str()
            .cmp(&b["valid_time_utc"].as_str())
    });
    let latest = frames.last().cloned().unwrap();
    let target = utc(text(&context, "anchor")?)?;
    let cycle = utc(text(&context, "cycle")?)?;
    let lat = finite(&context["request"], "lat")?;
    let lon = finite(&context["request"], "lon")?;
    let elevation = finite(&context["request"], "elevation_m")?;
    let radius = finite(&context["request"], "radius_km")?;
    let [_, azimuth] = astronomy::position(
        astronomy::Point {
            lat,
            lon,
            elevation_m: elevation,
        },
        target,
    );
    let latest_time = utc(text(&latest, "valid_time_utc")?)?;
    let horizon = ((target - latest_time).num_milliseconds() as f64 / 3_600_000.0).max(0.0);
    let motion = if frames.len() >= 2 {
        crate::desktop_hsd_motion::execute(
            serde_json::from_value(json!({
                "previous_manifest":frames[frames.len()-2]["manifest_path"],
                "latest_manifest":latest["manifest_path"],
            }))
            .map_err(|e| e.to_string())?,
        )?
    } else {
        Value::Null
    };
    let strong =
        motion["quality"] == "strong" && motion["age_minutes"].as_f64().is_some_and(|v| v <= 40.0);
    let (scale, quality, reason) = if horizon <= 1e-9 {
        (0.0, "observation", "at_observation")
    } else if !strong {
        (0.0, "unknown", "paired_motion_unavailable")
    } else if horizon <= 2.0 {
        (1.0, "strong", "strong_0_2h")
    } else if horizon <= 4.0 {
        ((4.0 - horizon) / 2.0, "decayed", "decayed_2_4h")
    } else {
        (0.0, "diagnostic-only", "beyond_4h")
    };
    let applied = scale > 0.0;
    let historical = target <= utc(text(&context, "lookup")?)?;
    if horizon > 0.10 && !applied && quality != "diagnostic-only" && !historical {
        return Err("native nowcast requires fresh paired motion for a future event".into());
    }
    let target = if horizon > 4.0 { latest_time } else { target };
    let mut display = latest.clone();
    if applied {
        check_cancel(cancel)?;
        fs::create_dir_all(satellite.join(".rust-advection")).map_err(|e| e.to_string())?;
        display = crate::desktop_hsd_advection::execute(serde_json::from_value(json!({
            "manifest_path":latest["manifest_path"],"output_dir":satellite.join(".rust-advection"),
            "target_time_utc":time_contract::iso(target),"horizon_hours":horizon,"scale":scale,"applied":true,
            "motion_dx_px_per_hour":motion["dx_px_per_hour"],"motion_dy_px_per_hour":motion["dy_px_per_hour"],
            "motion_quality":quality,"reason":reason,
        })).map_err(|e|e.to_string())?)?;
        display["valid_time_utc"] = json!(time_contract::iso(target));
    }
    check_cancel(cancel)?;
    let bbox = context["bbox"]
        .as_array()
        .filter(|v| v.len() == 4)
        .ok_or("native nowcast bbox is invalid")?;
    let bbox = [
        bbox[0].as_f64().unwrap(),
        bbox[1].as_f64().unwrap(),
        bbox[2].as_f64().unwrap(),
        bbox[3].as_f64().unwrap(),
    ];
    let gfs_root = config_path(
        payload,
        "gfs_cache_dir",
        "FIRESKY_GFS_CACHE_DIR",
        ".cache/gfs",
    )?;
    let client = crate::gfs_data::GfsClient::new(
        serde_json::from_value(json!({
            "cache_dir":gfs_root,"resolution":"0p25","region":context["region"],
            "timeout_seconds":90.0,"lock_timeout_seconds":300.0,"download_attempts":4,
        }))
        .map_err(|e| e.to_string())?,
    )?;
    let codes = crate::grib::EcCodes::load()?;
    let mut grids = BTreeMap::<u32, PathBuf>::new();
    let mut encode_frame = |frame: &Value| -> Result<Value, String> {
        check_cancel(cancel)?;
        let valid_time = utc(text(frame, "valid_time_utc")?)?;
        let elapsed = (valid_time - cycle).num_milliseconds() as f64 / 3_600_000.0;
        if elapsed < -1e-6 {
            return Err("HSD observation precedes GFS prior cycle".into());
        }
        let left = elapsed.floor().max(0.0) as u32;
        let right = if (elapsed - left as f64).abs() < 1e-6 {
            left
        } else {
            left + 1
        };
        for hour in [left, right] {
            if grids.contains_key(&hour) {
                continue;
            }
            check_cancel(cancel)?;
            let found = client
                .resolve(cycle, hour, Some(bbox), false, allow_download)?
                .ok_or_else(|| format!("native nowcast missing GFS f{hour:03} cache"))?;
            let path = Path::new(text(&found, "path")?);
            let grid = decoded_prior_grid(
                path,
                &gfs_root.join(".rust-nowcast-prior-v1"),
                cycle,
                hour,
                bbox,
                &codes,
            )
            .map_err(|e| format!("native nowcast GFS f{hour:03} decoding: {e}"))?;
            grids.insert(hour, grid);
        }
        Ok(
            json!({"manifest_path":frame["manifest_path"],"valid_time_utc":frame["valid_time_utc"],
            "retrieval":{"cycle_time_utc":time_contract::iso(cycle),"left_forecast_hour":left,
                "right_forecast_hour":right,"fraction":elapsed-left as f64,
                "left_cache_path":grids[&left],"right_cache_path":grids[&right],
                "local_radius_km":55.6,"domain_hash":hash(format!("{:?}|{:?}",grids[&left],grids[&right]).as_bytes()),
                "resolution":"0p25"}}),
        )
    };
    let encoded = frames
        .iter()
        .map(&mut encode_frame)
        .collect::<Result<Vec<_>, _>>()?;
    let display = encode_frame(&display)?;
    check_cancel(cancel)?;
    let aerosol = aerosol_reading(payload, &context, azimuth, allow_download)?;
    let mut result = payload.clone();
    result["cached_nowcast"] = json!({
        "target_time_utc":time_contract::iso(target),"timezone_name":context["request"]["timezone_name"],
        "point_lat":lat,"point_lon":lon,"radius_km":radius,"azimuth_deg":azimuth,
        "display_frame":display,"frames":encoded,
        "source_times_utc":frames.iter().rev().map(|v|v["valid_time_utc"].clone()).collect::<Vec<_>>(),
        "motion_dx_px_per_hour":if strong {motion["dx_px_per_hour"].clone()} else {json!(0.0)},
        "motion_dy_px_per_hour":if strong {motion["dy_px_per_hour"].clone()} else {json!(0.0)},
        "motion_correlation":motion.get("correlation").cloned().unwrap_or(json!(0.0)),
        "motion_quality":motion.get("quality").cloned().unwrap_or(json!("unknown")),
        "motion_age_minutes":motion.get("age_minutes").cloned().unwrap_or(Value::Null),
        "advection":{"horizon_hours":horizon,"scale":scale,"applied":applied,"reason":reason,
            "motion_age_minutes":motion.get("age_minutes").cloned().unwrap_or(Value::Null),"motion_quality":quality},
        "single_historical_observation":horizon>0.10 && !applied && historical,
    });
    result["aerosol"] = aerosol;
    result["observer_elevation_m"] = json!(elevation);
    result["event_time_utc"] = context["anchor"].clone();
    if !result["radar"].is_object() {
        check_cancel(cancel)?;
        let radar_root = config_path(
            payload,
            "radar_cache_dir",
            "FIRESKY_RADAR_CACHE_DIR",
            ".cache/radar",
        )?;
        if allow_download {
            match crate::radar::observation(&radar_root, target, lat, lon, azimuth) {
                Ok(Some(radar)) => result["radar"] = radar,
                Ok(None) => {
                    result["_native_warnings"] =
                        json!(["雷达覆盖不足或没有时效内观测；未应用雷达约束，不能解释为无降水。"])
                }
                Err(error) => {
                    result["_native_warnings"] =
                        json!([format!("雷达观测不可用，未应用雷达约束：{error}")])
                }
            }
        } else {
            result["_native_warnings"] =
                json!(["cache-only 请求未获取雷达，雷达状态 unknown，不能解释为无降水。"]);
        }
    }
    Ok(result)
}
const FEATURE_FIELDS: [&str; 27] = [
    "lat",
    "lon",
    "distance_km",
    "bearing_deg",
    "pressure_hpa",
    "geopotential_height_m",
    "rh_percent",
    "temperature_k",
    "omega_pa_s",
    "u_ms",
    "v_ms",
    "cloud_fraction_percent",
    "cloud_liquid_kg_kg",
    "cloud_ice_kg_kg",
    "source_lat",
    "source_lon",
    "source_distance_km",
    "source_bearing_deg",
    "surface_visibility_m",
    "precip_rate_kg_m2_s",
    "surface_temperature_k",
    "surface_dewpoint_k",
    "footprint_azimuth_width_deg",
    "footprint_near_distance_km",
    "footprint_far_distance_km",
    "horizontal_cloud_probability",
    "cloud_probability_given_cloud",
];

fn text<'a>(value: &'a Value, name: &str) -> Result<&'a str, String> {
    value[name]
        .as_str()
        .ok_or_else(|| format!("cached nowcast requires {name}"))
}

fn finite(value: &Value, name: &str) -> Result<f64, String> {
    value[name]
        .as_f64()
        .filter(|v| v.is_finite())
        .ok_or_else(|| format!("cached nowcast requires finite {name}"))
}

fn utc(value: &str) -> Result<DateTime<Utc>, String> {
    DateTime::parse_from_rfc3339(value)
        .map(|v| v.with_timezone(&Utc))
        .map_err(|_| "cached nowcast time must be RFC3339".into())
}

fn material_evidence(adjustment: &Value, source_time: DateTime<Utc>) -> Result<Value, String> {
    let sets = adjustment["cloud_column_sets"]
        .as_array()
        .ok_or("cached nowcast adjustment is missing cloud columns")?;
    let mut matched = false;
    let mut usable = false;
    for set in sets {
        if utc(text(set, "valid_time_utc")?)? != source_time {
            continue;
        }
        matched = true;
        for column in set["columns"]
            .as_array()
            .ok_or("cached nowcast columns are invalid")?
        {
            usable |= column["retrieval_quality"].as_str().unwrap_or("unknown") != "unknown"
                && column["horizontal_cloud_probability"]
                    .as_f64()
                    .is_some_and(|v| v.is_finite() && v >= 0.08);
        }
    }
    if !matched {
        return Err("cached nowcast observation has no source-time cloud columns".into());
    }
    Ok(if usable { json!(0.72) } else { Value::Null })
}

fn assessment_request(
    adjustment: &Value,
    request: &Value,
    options: &Value,
) -> Result<Value, String> {
    let point = astronomy::Point {
        lat: finite(request, "point_lat")?,
        lon: finite(request, "point_lon")?,
        elevation_m: if options.get("observer_elevation_m").is_some() {
            finite(options, "observer_elevation_m")?
        } else {
            0.0
        },
    };
    if !point.elevation_m.is_finite()
        || !(-90.0..=90.0).contains(&point.lat)
        || !(-180.0..=180.0).contains(&point.lon)
    {
        return Err("cached nowcast observer coordinates are invalid".into());
    }
    let features = adjustment["observed_features_by_offset"]
        .as_object()
        .ok_or("cached nowcast adjustment is missing observations")?;
    let times = adjustment["observed_feature_times_by_offset"]
        .as_object()
        .ok_or("cached nowcast adjustment is missing source times")?;
    let mut offsets = features
        .keys()
        .map(|v| v.parse::<i64>())
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| "cached nowcast offset is invalid")?;
    offsets.sort_unstable();
    let mut scans = Vec::with_capacity(offsets.len());
    let mut source_times = std::collections::BTreeSet::new();
    for offset in offsets {
        let key = offset.to_string();
        let source_time = utc(times
            .get(&key)
            .and_then(Value::as_str)
            .ok_or("cached nowcast observation is missing source time")?)?;
        source_times.insert(source_time);
        let rows = features[&key]
            .as_array()
            .ok_or("cached nowcast features are invalid")?
            .iter()
            .map(|feature| {
                Value::Array(
                    FEATURE_FIELDS
                        .iter()
                        .map(|field| feature.get(*field).cloned().unwrap_or(Value::Null))
                        .collect(),
                )
            })
            .collect::<Vec<_>>();
        let [elevation, azimuth] = astronomy::position(point, source_time);
        let radar = options["radar"]
            .as_object()
            .map(|v| {
                let mut filtered = serde_json::Map::new();
                for key in [
                    "source",
                    "coverage_ratio",
                    "age_minutes",
                    "max_dbz",
                    "local_precip_ratio",
                    "path_precip_ratio",
                    "convective_ratio",
                    "convective_risk",
                ] {
                    if let Some(value) = v.get(key) {
                        filtered.insert(key.to_owned(), value.clone());
                    }
                }
                Value::Object(filtered)
            })
            .unwrap_or(Value::Null);
        scans.push(json!({
            "valid_time_utc": time_contract::iso(source_time),
            "forecast_hour": 0, "features": rows,
            "solar_elevation_deg": elevation, "solar_azimuth_deg": azimuth,
            "radar": radar,
            "satellite_material_evidence": material_evidence(adjustment, source_time)?,
            "observer_lat": options["aerosol"].get("observer_lat").cloned().unwrap_or(Value::Null),
        }));
    }
    let radius = finite(request, "radius_km")?;
    let include_map = options["include_map_cells"].as_bool().unwrap_or(false);
    let aerosol = options
        .get("aerosol")
        .filter(|v| v.is_object())
        .ok_or("cached nowcast requires an aerosol reading; missing aerosol is not clear air")?;
    Ok(json!({
        "scans": scans, "timezone_name": text(request, "timezone_name")?,
        "observer_elevation_m": point.elevation_m, "aerosol": aerosol,
        "max_distance_km": 1000.0_f64.max(if include_map { radius } else { 0.0 }),
        "map_radius_km": radius, "include_map_cells": include_map,
        "event_time_utc": options.get("event_time_utc").cloned()
            .unwrap_or_else(|| request["target_time_utc"].clone()),
        "core_window_minutes": options.get("core_window_minutes").cloned().unwrap_or(json!(15.0)),
        "apply_duration": source_times.len() >= 2
            && options["apply_duration"].as_bool().unwrap_or(true),
        "detail": "model",
    }))
}

fn number(value: &Value) -> f64 {
    value.as_f64().unwrap_or(0.0)
}

fn level(index: f64) -> Result<Value, String> {
    crate::characteristics::execute(
        serde_json::from_value(json!({"operation":"level","index":index}))
            .map_err(|e| e.to_string())?,
    )
}

fn report_payload(
    payload: &Value,
    request: &Value,
    mut adjustment: Value,
    assessment: Value,
    apply_duration: bool,
) -> Result<Value, String> {
    let quality = &assessment["quality"];
    let scans = assessment["scans"]
        .as_array()
        .ok_or("native nowcast scans are missing")?;
    let instant = quality["instant"]
        .as_array()
        .ok_or("native nowcast instant ledger is missing")?;
    let projections = quality["instant_event_projection"]
        .as_array()
        .ok_or("native nowcast projection ledger is missing")?;
    let rows = assessment["rows"]
        .as_array()
        .ok_or("native nowcast event rows are missing")?;
    if scans.len() != instant.len() || scans.len() != projections.len() || scans.len() != rows.len()
    {
        return Err("native nowcast report ledger lengths differ".into());
    }
    let mut steps = Vec::with_capacity(scans.len());
    let mut best = None::<usize>;
    let mut diagnostic = None::<usize>;
    for (index, scan) in scans.iter().enumerate() {
        let projection = &projections[index];
        let row = &rows[index];
        let score = number(&projection[0]);
        let physically_burnable = number(&instant[index][5]) != 0.0;
        let core = number(&row[17]) != 0.0;
        if core && diagnostic.is_none_or(|old| score > number(&projections[old][0])) {
            diagnostic = Some(index);
        }
        if core
            && physically_burnable
            && best.is_none_or(|old| score > number(&projections[old][0]))
        {
            best = Some(index);
        }
        let mut step = scan.clone();
        let scenes = scan["scene_scores"]
            .as_array()
            .ok_or("native nowcast scene scores are missing")?;
        let mut scene_scores = serde_json::Map::new();
        for scene in scenes {
            let name = scene[0]
                .as_str()
                .ok_or("native nowcast scene name is invalid")?;
            scene_scores.insert(name.to_owned(), scene[1].clone());
        }
        step["scene_scores"] = Value::Object(scene_scores);
        step["cloud_source_times_utc"] = json!([scan["valid_time_utc"]]);
        step["instant_quality_index"] = json!(score);
        step["instant_quality_level"] = level(score)?;
        step["physically_burnable"] = json!(physically_burnable);
        step["valid"] = json!(true);
        step["burn_score"] = json!((score / 2.5).clamp(0.0, 1.0));
        step["intensity_score"] = json!((number(&projection[3]) / 2.5).clamp(0.0, 1.0));
        step["extent_score"] = json!(number(&row[0]).clamp(0.0, 1.0));
        step["vividness_score"] = json!(number(&projection[7]).clamp(0.0, 1.0));
        step["geometric_cloud_coverage"] = json!(number(&row[0]).clamp(0.0, 1.0));
        for (name, offset) in [
            ("burnable_cloud_coverage", 1),
            ("forward_burnable_cloud_field_amount", 2),
            ("forward_burnable_cloud_load", 3),
            ("forward_burnable_cloud_material_load", 4),
            ("forward_burnable_cloud_incoming_aerosol_load", 5),
            ("forward_burnable_cloud_ratio", 6),
            ("brightness_color", 7),
            ("cloud_material_score", 8),
            ("quality_aod550", 9),
        ] {
            step[name] = projection[offset].clone();
        }
        step["solar_incoming_block_risk"] = row[5].clone();
        step["observer_return_transmission"] = Value::Null;
        steps.push(step);
    }
    let diagnostic = best.or(diagnostic).map(|index| &steps[index]);
    let best_step = best.map(|index| &steps[index]);
    let direct = (1.0 - number(&adjustment["horizon_clearance_score"])).clamp(0.0, 1.0);
    let samples = diagnostic.map_or(0, |v| {
        v["cross_section"]["samples_count"].as_u64().unwrap_or(0)
    });
    adjustment["geometric_path_samples"] = json!(samples);
    adjustment["geometric_path_blocking_risk"] = json!(if samples >= 4 {
        (0.65 * direct + 0.35 * number(&diagnostic.unwrap()["cross_section"]["blocking_risk"]))
            .clamp(0.0, 1.0)
    } else {
        direct
    });
    adjustment["solar_path_profile"] = diagnostic
        .map(|v| v["cross_section"]["profile"].clone())
        .unwrap_or(json!([]));
    let adjustment_object = adjustment
        .as_object_mut()
        .ok_or("native nowcast adjustment is invalid")?;
    adjustment_object.remove("observed_features_by_offset");
    adjustment_object.remove("observed_feature_times_by_offset");
    if let Some(samples) = adjustment_object
        .get_mut("satellite_vertical_profile")
        .and_then(|profile| profile.get_mut("samples"))
        .and_then(Value::as_array_mut)
    {
        for sample in samples {
            if let Some(fields) = sample.as_object_mut() {
                fields.remove("terrain_elevation_asl_m");
            }
        }
    }
    for name in [
        "radar_source",
        "radar_valid_time_utc",
        "radar_age_minutes",
        "radar_coverage_ratio",
        "radar_local_precip_ratio",
        "radar_path_precip_ratio",
        "radar_convective_ratio",
        "radar_max_dbz",
        "radar_convective_risk",
        "radar_reliability_score",
        "effective_convective_risk",
        "effective_path_blocking_risk",
        "convective_score_ceiling",
    ] {
        adjustment_object.entry(name).or_insert(Value::Null);
    }
    if let Some(radar) = payload["radar"].as_object() {
        let mut assessment_radar = serde_json::Map::new();
        for (source, target) in [
            ("source", "radar_source"),
            ("valid_time_utc", "radar_valid_time_utc"),
            ("age_minutes", "radar_age_minutes"),
            ("coverage_ratio", "radar_coverage_ratio"),
            ("local_precip_ratio", "radar_local_precip_ratio"),
            ("path_precip_ratio", "radar_path_precip_ratio"),
            ("convective_ratio", "radar_convective_ratio"),
            ("max_dbz", "radar_max_dbz"),
            ("convective_risk", "radar_convective_risk"),
        ] {
            if let Some(value) = radar.get(source) {
                adjustment_object.insert(target.to_owned(), value.clone());
                if source != "valid_time_utc" {
                    assessment_radar.insert(source.to_owned(), value.clone());
                }
            }
        }
        let diagnostics =
            desktop_nowcast_assessment::radar_diagnostics(Value::Object(assessment_radar))?;
        for (key, value) in diagnostics
            .as_object()
            .ok_or("native radar diagnostics are invalid")?
        {
            adjustment_object.insert(key.clone(), value.clone());
        }
    }
    let event = &quality["event"];
    let event_score = number(&event["index"]);
    let peak_score = best.map_or(0.0, |index| number(&projections[index][0]));
    let duration = if apply_duration {
        event["components"]["duration_minutes"]
            .as_f64()
            .map(|v| json!(v.round()))
            .unwrap_or(Value::Null)
    } else {
        Value::Null
    };
    let zone: chrono_tz::Tz = text(request, "timezone_name")?
        .parse()
        .map_err(|_| "invalid nowcast timezone")?;
    let anchor = utc(payload["event_time_utc"]
        .as_str()
        .unwrap_or(text(request, "target_time_utc")?))?
    .with_timezone(&zone);
    let core_steps = steps
        .iter()
        .enumerate()
        .filter(|(i, _)| number(&rows[*i][17]) != 0.0)
        .map(|(_, v)| v.clone())
        .collect::<Vec<_>>();
    let best_core_index = best.and_then(|index| {
        steps[..=index]
            .iter()
            .enumerate()
            .filter(|(i, _)| number(&rows[*i][17]) != 0.0)
            .count()
            .checked_sub(1)
    });
    let evolution = crate::characteristics::execute(
        serde_json::from_value(json!({
            "operation":"evolution","steps":core_steps,"best_index":best_core_index,
        }))
        .map_err(|e| e.to_string())?,
    )?;
    let timing = if let Some(step) = best_step {
        crate::characteristics::execute(
            serde_json::from_value(json!({
                "operation":"window","steps":core_steps,"regime":step["selected_regime"],
            }))
            .map_err(|e| e.to_string())?,
        )?
    } else {
        Value::Null
    };
    let mut summary = adjustment["summary"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    summary.push(json!(format!(
        "原生 Rust 实况评估：{} 个独立扫描时次；持续时间状态 {}。",
        scans.len(),
        if apply_duration {
            "estimated"
        } else {
            "unknown"
        }
    )));
    if let Some(warnings) = payload["_native_warnings"].as_array() {
        summary.extend(warnings.iter().cloned());
    }
    let mut aerosol = payload["aerosol"].clone();
    if let Some(fields) = aerosol.as_object_mut() {
        fields.entry("source").or_insert(json!("explicit-request"));
        fields.entry("quality").or_insert(json!("unknown"));
        for key in ["pm2_5_ug_m3", "pm10_ug_m3", "particulate_source"] {
            fields.entry(key).or_insert(Value::Null);
        }
    }
    let mut report = json!({
        "contract":"rust-nowcast-runtime-v1","runtime_python_used":false,
        "observer_name":payload["name"].as_str().unwrap_or("观测点"),
        "phenomenon":payload["phenomenon"].as_str().unwrap_or("dusk"),
        "model_source":"satellite","forecast_mode":"nowcast",
        "target_date":payload["target_date"].as_str().map(str::to_owned).unwrap_or_else(||anchor.date_naive().to_string()),
        "analysis_radius_km":request["radius_km"],"timezone_name":request["timezone_name"],
        "observer_elevation_asl_m":payload["observer_elevation_m"].as_f64().unwrap_or(0.0),
        "anchor_local":time_contract::iso(anchor),"cycle_time_utc":adjustment["image_valid_time_utc"],
        "model_cycles":{},"model_weights":{},"best_time_local":best_step.map(|v|v["valid_time_local"].clone()),
        "overall_score":(event_score/2.5).clamp(0.0,1.0),"burn_score":(peak_score/2.5).clamp(0.0,1.0),
        "intensity_score":best_step.map_or(0.0,|v|number(&v["intensity_score"])),
        "extent_score":best_step.map_or(0.0,|v|number(&v["extent_score"])),
        "vividness_score":best_step.map_or(0.0,|v|number(&v["vividness_score"])),
        "confidence_score":adjustment["confidence"],"model_disagreement_score":0.0,
        "quality_index":event_score,"quality_level":level(event_score)?,
        "peak_quality_index":peak_score,"peak_quality_level":level(peak_score)?,
        "quality_components":event["components"],"duration_status":if apply_duration {"estimated"} else {"unknown"},
        "duration_minutes":duration,"potential_duration_minutes":duration,
        "selected_regime":best_step.map(|v|v["selected_regime"].clone()).unwrap_or(json!("unknown")),
        "scene_scores":best_step.map(|v|v["scene_scores"].clone()).unwrap_or(json!({})),
        "local_sky_score":best_step.map_or(0.0,|v|number(&v["local_sky_score"])),
        "remote_sky_score":best_step.map_or(0.0,|v|number(&v["remote_sky_score"])),
        "convective_risk":best_step.map_or(0.0,|v|number(&v["convective_risk"])),
        "color_diagnosis":best_step.map(|v|v["color_diagnosis"].clone()).unwrap_or(json!({})),
        "cloud_evolution":evolution,"timing_window":timing,"aerosol":aerosol,
        "summary":summary,"nowcast":adjustment,"steps":steps,
        "compute_backend_requested":"auto","compute_backend":"cpu",
        "html_report_path":null,"html_report_url":"","map_token":null,"map_times":[],
        "map_capability":{"available":false,"reason":"native-nowcast-map-publication-not-requested"},
        "source_roles":{"primary":"satellite_observation","auxiliary":"aerosol_environment+weather_radar"},
    });
    if payload["include_native_assessment"] == true {
        report["assessment"] = assessment;
    }
    if payload["include_map_cells"] == true {
        let root = config_path(
            payload,
            "map_cache_dir",
            "FIRESKY_MAP_CACHE_DIR",
            ".cache/map-frames",
        )?;
        crate::map_frames::publish(
            &root,
            &mut report,
            finite(request, "point_lat")?,
            finite(request, "point_lon")?,
        )?;
    }
    Ok(report)
}

/// Ordinary desktop request payload, or `cached_nowcast` plus assessment
/// options. Missing optical inputs never silently become clear air.
pub fn compute(payload: &Value) -> Result<Value, String> {
    compute_with_cancel(payload, &AtomicBool::new(false))
}

pub fn compute_with_cancel(payload: &Value, cancel: &AtomicBool) -> Result<Value, String> {
    check_cancel(cancel)?;
    let prepared;
    let payload = if payload.get("cached_nowcast").is_some() {
        payload
    } else {
        prepared = ordinary_payload(payload, cancel)?;
        &prepared
    };
    let request = payload
        .get("cached_nowcast")
        .filter(|v| v.is_object())
        .ok_or(
            "raw HSD acquisition is unavailable in the native runtime; cached_nowcast is required",
        )?;
    if !payload.get("aerosol").is_some_and(Value::is_object) {
        return Err(
            "cached nowcast requires an aerosol reading; missing aerosol is not clear air".into(),
        );
    }
    check_cancel(cancel)?;
    let adjustment = desktop_nowcast_adjustment::compute(request.clone())
        .map_err(|e| format!("native nowcast adjustment: {e}"))?;
    check_cancel(cancel)?;
    let assessment_params = assessment_request(&adjustment, request, payload)?;
    let apply_duration = assessment_params["apply_duration"]
        .as_bool()
        .unwrap_or(false);
    let assessment = desktop_nowcast_assessment::compute(assessment_params)?;
    check_cancel(cancel)?;
    report_payload(payload, request, adjustment, assessment, apply_duration)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prior_longitude_filter_preserves_wrapped_and_global_domains() {
        for lon in [179.0, 180.0, 181.0, -179.0, -180.0] {
            assert!(longitude_in_bbox([178.0, -178.0, 2.0, -1.0], lon));
            assert!(longitude_in_bbox([-180.0, 180.0, 2.0, -1.0], lon));
        }
        assert!(!longitude_in_bbox([178.0, -178.0, 2.0, -1.0], 120.0));
        assert!(longitude_in_bbox([170.0, 180.0, 2.0, -1.0], -180.0));
        assert!(!longitude_in_bbox([170.0, 180.0, 2.0, -1.0], -179.0));
    }

    #[test]
    #[ignore = "requires FIRESKY_NOWCAST_TEST_GRIB: actual encoded 3x2 GFS at 179/180/181 degrees, f009 2026-11-09 00Z"]
    fn encoded_dateline_grib_prior_retains_both_sides() {
        let source = PathBuf::from(std::env::var("FIRESKY_NOWCAST_TEST_GRIB").unwrap());
        let root = tempfile::tempdir().unwrap();
        let codes = crate::grib::EcCodes::load().unwrap();
        let bbox = [178.0, -178.0, 2.0, -1.0];
        let cycle = utc("2026-11-09T00:00:00Z").unwrap();
        let grid = decoded_prior_grid(&source, root.path(), cycle, 9, bbox, &codes).unwrap();
        let bytes = fs::read(grid.join("coordinates.npy")).unwrap();
        let array = npyz::NpyFile::new(&bytes[..]).unwrap();
        assert_eq!(array.shape(), &[2, 6]);
        let values = array
            .data::<f32>()
            .unwrap()
            .collect::<Result<Vec<_>, _>>()
            .unwrap();
        let longitudes = &values[6..];
        assert!(longitudes.iter().any(|v| (*v - 179.0).abs() < 1e-5));
        assert!(longitudes
            .iter()
            .any(|v| (*v - 181.0).abs() < 1e-5 || (*v + 179.0).abs() < 1e-5));
        assert_eq!(
            decoded_prior_grid(&source, root.path(), cycle, 9, bbox, &codes).unwrap(),
            grid,
        );
    }

    fn fixture() -> (Value, Value, Value) {
        let source_time = "2026-11-10T08:00:00Z";
        let adjustment = json!({
            "observed_features_by_offset": {"0": [{
                "lat": 30.0, "lon": 120.0, "distance_km": 100.0, "bearing_deg": 150.0,
                "pressure_hpa": 600, "geopotential_height_m": 5000.0, "rh_percent": 90.0,
                "horizontal_cloud_probability": 0.8, "cloud_probability_given_cloud": 0.7,
            }]},
            "observed_feature_times_by_offset": {"0": source_time},
            "cloud_column_sets": [{"valid_time_utc": source_time, "columns": [{
                "retrieval_quality": "high", "horizontal_cloud_probability": 0.8,
            }]}],
        });
        let request = json!({"point_lat":30.0,"point_lon":120.0,"radius_km":1200.0,
            "timezone_name":"Asia/Shanghai","target_time_utc":source_time});
        let options = json!({"aerosol":{"aod550":0.2,"quality":"measured",
            "observer_lat":30.0,"observer_lon":120.0},"include_map_cells":true});
        (adjustment, request, options)
    }

    #[test]
    fn request_preserves_v2_probability_and_large_map_radius() {
        let (adjustment, request, options) = fixture();
        let encoded = assessment_request(&adjustment, &request, &options).unwrap();
        assert_eq!(
            encoded["scans"][0]["features"][0].as_array().unwrap().len(),
            27
        );
        assert_eq!(encoded["scans"][0]["features"][0][25], 0.8);
        assert_eq!(encoded["scans"][0]["features"][0][26], 0.7);
        assert!(encoded["scans"][0]["features"][0][7].is_null());
        assert_eq!(encoded["scans"][0]["satellite_material_evidence"], 0.72);
        assert_eq!(encoded["max_distance_km"], 1200.0);
    }

    #[test]
    fn missing_source_columns_and_aerosol_fail_closed() {
        let (mut adjustment, request, mut options) = fixture();
        adjustment["cloud_column_sets"] = json!([]);
        assert!(assessment_request(&adjustment, &request, &options)
            .unwrap_err()
            .contains("source-time"));
        let (adjustment, _, _) = fixture();
        options.as_object_mut().unwrap().remove("aerosol");
        assert!(assessment_request(&adjustment, &request, &options)
            .unwrap_err()
            .contains("aerosol"));
        assert!(compute(&json!({"cached_nowcast":null}))
            .unwrap_err()
            .contains("raw HSD acquisition"));
    }

    #[test]
    fn generated_request_runs_native_assessment() {
        let (adjustment, request, options) = fixture();
        let params = assessment_request(&adjustment, &request, &options).unwrap();
        assert_eq!(params["apply_duration"], false);
        let result = desktop_nowcast_assessment::compute(params).unwrap();
        assert_eq!(result["runtime_python_used"], false);
        assert_eq!(result["scan_count"], 1);
        assert_eq!(result["detail"], "model");
        assert_eq!(
            result["scans"][0]["satellite_retrieval_material_evidence"],
            0.72
        );
        assert_eq!(result["rows"].as_array().unwrap().len(), 1);
        assert_eq!(
            result["quality"]["contract"],
            "rust-desktop-nowcast-quality-v2"
        );
        let report = report_payload(&options, &request, adjustment, result, false).unwrap();
        assert_eq!(report["model_source"], "satellite");
        assert_eq!(report["forecast_mode"], "nowcast");
        assert_eq!(report["duration_status"], "unknown");
        assert!(report["duration_minutes"].is_null());
        assert_eq!(
            report["steps"][0]["cloud_source_times_utc"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert!(report["steps"][0]["scene_scores"].is_object());
        assert!(report["nowcast"]
            .get("observed_features_by_offset")
            .is_none());
        assert!(report.get("assessment").is_none());
        for key in [
            "observer_name",
            "phenomenon",
            "target_date",
            "timezone_name",
            "anchor_local",
            "cycle_time_utc",
            "model_cycles",
            "best_time_local",
            "overall_score",
            "intensity_score",
            "extent_score",
            "vividness_score",
            "confidence_score",
            "model_disagreement_score",
            "duration_minutes",
            "potential_duration_minutes",
            "model_weights",
            "aerosol",
            "summary",
            "steps",
            "html_report_path",
            "map_token",
            "map_times",
            "html_report_url",
            "observer_elevation_asl_m",
        ] {
            assert!(report.get(key).is_some(), "missing forecast field {key}");
        }
    }

    #[test]
    fn cancellation_precedes_context_or_provider_work() {
        let cancelled = AtomicBool::new(true);
        assert!(compute_with_cancel(&json!({}), &cancelled)
            .unwrap_err()
            .contains("cancelled"));
    }

    #[test]
    fn ordinary_request_without_cached_observations_fails_closed() {
        let root = tempfile::tempdir().unwrap();
        let result = compute(&json!({
            "lat":30.2741,"lon":120.1551,"target_date":"2026-11-07",
            "timezone_name":"Asia/Shanghai","phenomenon":"dusk",
            "reference_time_utc":"2026-11-07T12:00:00Z",
            "_gfs_cycle_override":"2026-11-07T00:00:00Z","radius_km":900.0,
            "allow_download":false,"_runtime_config":{"workspace":root.path()},
        }));
        assert!(result.unwrap_err().contains("calibrated HSD observations"));
    }

    #[test]
    #[ignore = "requires pinned local calibrated HSD and actual GFS f008/f009/f010 caches"]
    fn real_cached_two_frame_pipeline() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap();
        let result = compute(&json!({
            "lat":30.2741,"lon":120.1551,"elevation_m":0,
            "target_date":"2026-11-07","timezone_name":"Asia/Shanghai","phenomenon":"dusk",
            "reference_time_utc":"2026-11-07T12:00:00Z",
            "_gfs_cycle_override":"2026-11-07T00:00:00Z",
            "radius_km":900,"allow_download":false,"include_native_assessment":true,
            "_runtime_config":{"workspace":root},
            "aerosol": {"aod550": 0.15, "quality": "measured",
                "observer_lat": 30.2741, "observer_lon": 120.1551},
        }))
        .unwrap();
        assert_eq!(result["runtime_python_used"], false);
        assert_eq!(result["assessment"]["scan_count"], 2);
        assert_eq!(result["assessment"]["rows"].as_array().unwrap().len(), 2);
        assert_eq!(result["forecast_mode"], "nowcast");
        assert_eq!(result["steps"].as_array().unwrap().len(), 2);
        assert_eq!(result["duration_status"], "estimated");
        assert!(
            result["nowcast"]["calibrated_sample_coverage_ratio"]
                .as_f64()
                .unwrap()
                > 0.0
        );
    }
}
