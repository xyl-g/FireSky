//! Bounded, immutable query sources belonging to one published forecast result.
use crate::{cache_lock, cross_section, request_file, tensor_bundle, time_contract};
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    fs,
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
};

const CONTRACT: &str = "rust-desktop-profile-source-v1";
pub(crate) const MAX_OBSERVERS: usize = 100_000;
const TTL_SECONDS: i64 = 1800;
const MAX_SNAPSHOTS: usize = 8;
const MAX_SNAPSHOT_BYTES: u64 = 8 * 1024 * 1024 * 1024;
const MAX_TOTAL_BYTES: u64 = 16 * 1024 * 1024 * 1024;
const MAX_RECEIPT_BYTES: u64 = 16 * 1024 * 1024;

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Observer {
    pub lat: f64,
    pub lon: f64,
    pub elevation_m: f64,
    pub timezone_name: String,
    pub anchor_utc: String,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Receipt {
    contract: String,
    result_id: String,
    model_source: String,
    cycle_utc: String,
    generated_at_utc: String,
    expires_at_utc: String,
    source_identity: tensor_bundle::SourceIdentity,
    profile_kind: String,
    observers: Vec<Observer>,
    bytes: u64,
    nonce: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Query {
    result_id: String,
    source_identity: tensor_bundle::SourceIdentity,
    model_source: String,
    lat: f64,
    lon: f64,
    valid_time_utc: String,
}

fn check(cancel: &AtomicBool) -> Result<(), String> {
    if cancel.load(Ordering::Acquire) {
        Err("profile query cancelled".into())
    } else {
        Ok(())
    }
}

fn root(workspace: &Path) -> Result<PathBuf, String> {
    let root = workspace.join(".cache/profiles/rust-v1");
    fs::create_dir_all(&root).map_err(|e| e.to_string())?;
    if !fs::symlink_metadata(&root)
        .map_err(|e| e.to_string())?
        .file_type()
        .is_dir()
    {
        return Err("profile root must be a directory, not a symlink".into());
    }
    Ok(root)
}

fn write(path: &Path, value: &impl Serialize) -> Result<(), String> {
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    serde_json::to_writer(&mut file, value).map_err(|e| e.to_string())?;
    file.sync_all().map_err(|e| e.to_string())
}

fn valid_id(id: &str) -> bool {
    id.len() == 64
        && id
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn tree_bytes(path: &Path) -> Result<u64, String> {
    let mut bytes = 0_u64;
    for entry in fs::read_dir(path).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let kind = entry.file_type().map_err(|e| e.to_string())?;
        let next = if kind.is_dir() {
            tree_bytes(&entry.path())?
        } else if kind.is_file() {
            entry.metadata().map_err(|e| e.to_string())?.len()
        } else {
            return Err("profile snapshot contains a nonregular file".into());
        };
        bytes = bytes.checked_add(next).ok_or("profile size overflow")?;
        if bytes > MAX_TOTAL_BYTES {
            return Err("profile storage exceeds its resource bound".into());
        }
    }
    Ok(bytes)
}

fn copy_verified(source: &Path, destination: &Path) -> Result<(), String> {
    let kind = fs::symlink_metadata(source)
        .map_err(|e| e.to_string())?
        .file_type();
    if kind.is_dir() {
        fs::create_dir(destination).map_err(|e| e.to_string())?;
        for entry in fs::read_dir(source).map_err(|e| e.to_string())? {
            let entry = entry.map_err(|e| e.to_string())?;
            copy_verified(&entry.path(), &destination.join(entry.file_name()))?;
        }
        return Ok(());
    }
    if !kind.is_file() {
        return Err("profile relocation rejects symlinks and nonregular files".into());
    }
    let expected = tensor_bundle::regular_identity(source)?;
    let mut input = fs::File::open(source).map_err(|e| e.to_string())?;
    let mut output = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)
        .map_err(|e| e.to_string())?;
    std::io::copy(&mut input, &mut output).map_err(|e| e.to_string())?;
    output.sync_all().map_err(|e| e.to_string())?;
    if tensor_bundle::regular_identity(destination)? != expected
        || tensor_bundle::regular_identity(source)? != expected
    {
        return Err("profile source changed during cross-filesystem copy".into());
    }
    Ok(())
}

fn relocate(source: &Path, destination: &Path) -> Result<(), String> {
    match fs::rename(source, destination) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::CrossesDevices => {
            copy_verified(source, destination)?;
            if fs::symlink_metadata(source)
                .map_err(|e| e.to_string())?
                .file_type()
                .is_dir()
            {
                fs::remove_dir_all(source).map_err(|e| e.to_string())
            } else {
                fs::remove_file(source).map_err(|e| e.to_string())
            }
        }
        Err(error) => Err(error.to_string()),
    }
}

fn receipt(path: &Path) -> Result<Receipt, String> {
    let (receipt, _): (Receipt, _) =
        request_file::read(&path.join("receipt.json"), MAX_RECEIPT_BYTES)?;
    if receipt_id(&receipt)? != receipt.result_id {
        return Err("profile snapshot receipt content changed".into());
    }
    Ok(receipt)
}

fn receipt_id(item: &Receipt) -> Result<String, String> {
    use sha2::{Digest, Sha256};
    let mut value = serde_json::to_value(item).map_err(|e| e.to_string())?;
    value.as_object_mut().unwrap().remove("result_id");
    Ok(
        Sha256::digest(serde_json::to_vec(&value).map_err(|e| e.to_string())?)
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect(),
    )
}

fn maintain(root: &Path, incoming: u64, now: DateTime<Utc>) -> Result<(), String> {
    let mut retained = Vec::new();
    for entry in fs::read_dir(root).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let name = entry.file_name().to_string_lossy().into_owned();
        if !valid_id(&name) {
            continue;
        }
        if !entry.file_type().map_err(|e| e.to_string())?.is_dir() {
            return Err("profile snapshot directory is not regular".into());
        }
        let item = match receipt(&entry.path()) {
            Ok(item) => item,
            Err(_) => {
                // A corrupt private query snapshot cannot be reused. Dropping
                // it makes old queries explicitly missing, never recomputed.
                fs::remove_dir_all(entry.path()).map_err(|e| e.to_string())?;
                continue;
            }
        };
        if item.contract != CONTRACT || item.result_id != name {
            return Err("profile snapshot receipt identity is invalid".into());
        }
        let expires = time_contract::parse_utc(&item.expires_at_utc)?;
        if now >= expires {
            fs::remove_dir_all(entry.path()).map_err(|e| e.to_string())?;
        } else {
            retained.push((expires, tree_bytes(&entry.path())?, entry.path()));
        }
    }
    retained.sort_by_key(|v| v.0);
    let mut total: u64 = retained.iter().map(|v| v.1).sum();
    while retained.len() >= MAX_SNAPSHOTS || total + incoming > MAX_TOTAL_BYTES {
        if retained.is_empty() {
            return Err("profile snapshot exceeds storage bound".into());
        }
        let (_, bytes, path) = retained.remove(0);
        fs::remove_dir_all(path).map_err(|e| e.to_string())?;
        total -= bytes;
    }
    Ok(())
}

fn remove_incomplete(root: &Path) -> Result<(), String> {
    // The store lock excludes every live writer, so pending directories here
    // belong to a process that exited before publishing its receipt.
    for entry in fs::read_dir(root).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        if entry.file_name().to_string_lossy().starts_with(".pending-") {
            if !entry.file_type().map_err(|e| e.to_string())?.is_dir() {
                return Err("incomplete profile source is not a regular directory".into());
            }
            fs::remove_dir_all(entry.path()).map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

fn descriptor(item: &Receipt) -> Value {
    json!({
        "contract":CONTRACT,"available":true,"result_id":item.result_id,
        "source_identity":item.source_identity,"model_source":item.model_source,
        "cycle_utc":item.cycle_utc,"generated_at_utc":item.generated_at_utc,
        "expires_at_utc":item.expires_at_utc,"profile_kind":item.profile_kind,
        "scope":if item.observers.len()==1 {"point"} else {"regional"},
        "query_method":"profile_query","source_semantics":"immutable-result-snapshot",
        "resource_policy":{"ttl_seconds":TTL_SECONDS,"max_snapshots":MAX_SNAPSHOTS,
            "max_snapshot_bytes":MAX_SNAPSHOT_BYTES,"max_total_bytes":MAX_TOTAL_BYTES},
    })
}

fn observers_valid(observers: &[Observer]) -> Result<(), String> {
    if observers.is_empty() || observers.len() > MAX_OBSERVERS {
        return Err(format!(
            "profile source requires 1..{} observers",
            MAX_OBSERVERS
        ));
    }
    for point in observers {
        if !point.lat.is_finite()
            || !(-90.0..=90.0).contains(&point.lat)
            || !point.lon.is_finite()
            || !(-180.0..=180.0).contains(&point.lon)
            || !point.elevation_m.is_finite()
            || !(-500.0..=9000.0).contains(&point.elevation_m)
            || point.timezone_name.len() > 128
            || point.timezone_name.parse::<chrono_tz::Tz>().is_err()
        {
            return Err("invalid frozen profile observer".into());
        }
        time_contract::parse_utc(&point.anchor_utc)?;
    }
    Ok(())
}

pub(crate) fn observer(point: &Value, report: &Value) -> Result<Observer, String> {
    let local = report["anchor_local"]
        .as_str()
        .ok_or("profile result anchor is missing")?;
    let anchor = DateTime::parse_from_rfc3339(local).map_err(|e| e.to_string())?;
    Ok(Observer {
        lat: point["lat"].as_f64().ok_or("profile latitude missing")?,
        lon: point["lon"].as_f64().ok_or("profile longitude missing")?,
        elevation_m: report["observer_elevation_asl_m"]
            .as_f64()
            .or_else(|| point["elevation_m"].as_f64())
            .ok_or("profile observer elevation missing")?,
        timezone_name: point["timezone_name"]
            .as_str()
            .or_else(|| report["timezone_name"].as_str())
            .ok_or("profile observer timezone missing")?
            .into(),
        anchor_utc: time_contract::iso(anchor.with_timezone(&Utc)),
    })
}

pub(crate) fn retain_model(
    workspace: &Path,
    output: &Path,
    source_descriptor: &Value,
    observers: Vec<Observer>,
    cancel: &AtomicBool,
) -> Result<Value, String> {
    let source_path = output.join("cross_section_source.json");
    let (source, identity): (Value, _) = request_file::read(&source_path, MAX_RECEIPT_BYTES)?;
    if serde_json::to_value(&identity).map_err(|e| e.to_string())? != source_descriptor["identity"]
        || source["contract"] != "rust-cross-section-source-v1"
    {
        return Err("profile source descriptor differs from frozen source".into());
    }
    let bundles = source["profile_bundles"]
        .as_array()
        .filter(|v| !v.is_empty() && v.len() <= 64)
        .ok_or("profile source bundle list is invalid")?;
    let mut children = Vec::new();
    let mut bytes = identity.bytes;
    for (index, frame) in bundles.iter().enumerate() {
        let expected = format!("profiles_{index:03}/manifest.json");
        if frame["manifest"].as_str() != Some(expected.as_str()) {
            return Err("profile source must use ordered local child manifests".into());
        }
        let path = output.join(&expected);
        let expected_identity: tensor_bundle::SourceIdentity =
            serde_json::from_value(frame["identity"].clone()).map_err(|e| e.to_string())?;
        if tensor_bundle::regular_identity(&path)? != expected_identity {
            return Err("profile manifest changed before retention".into());
        }
        let child = path.parent().unwrap().to_path_buf();
        if !fs::symlink_metadata(&child)
            .map_err(|e| e.to_string())?
            .file_type()
            .is_dir()
        {
            return Err("profile source child must not be a symlink".into());
        }
        bytes = bytes
            .checked_add(tree_bytes(&child)?)
            .ok_or("profile size overflow")?;
        children.push(child);
    }
    retain(
        workspace,
        source["model_source"]
            .as_str()
            .ok_or("profile model missing")?,
        source["cycle_utc"]
            .as_str()
            .ok_or("profile cycle missing")?,
        "model-pressure-cross-section",
        observers,
        bytes,
        cancel,
        |pending| {
            for child in children {
                check(cancel)?;
                relocate(&child, &pending.join(child.file_name().unwrap()))?;
            }
            let retained_source = pending.join("source.json");
            relocate(&source_path, &retained_source)?;
            if tensor_bundle::regular_identity(&retained_source)? != identity {
                return Err("profile source changed during retention".into());
            }
            Ok(())
        },
    )
}

pub(crate) fn retain_satellite(
    workspace: &Path,
    point: &Value,
    report: &Value,
    cancel: &AtomicBool,
) -> Result<Value, String> {
    let source = json!({
        "contract":"rust-desktop-satellite-profile-steps-v1",
        "model_source":"satellite","cycle_utc":report["cycle_time_utc"],
        "steps":report["steps"].as_array().ok_or("satellite profile steps missing")?
            .iter().map(|step|json!({
                "valid_time_local":step["valid_time_local"],
                "solar_azimuth_deg":step["solar_azimuth_deg"],
                "cloud_source_times_utc":step["cloud_source_times_utc"],
                "cross_section":step["cross_section"],
            })).collect::<Vec<_>>(),
    });
    let bytes = serde_json::to_vec(&source)
        .map_err(|e| e.to_string())?
        .len() as u64;
    if bytes > MAX_RECEIPT_BYTES {
        return Err("satellite profile source exceeds JSON bound".into());
    }
    retain(
        workspace,
        "satellite",
        report["cycle_time_utc"]
            .as_str()
            .ok_or("satellite cycle missing")?,
        "satellite-step-solar-path-diagnostic",
        vec![observer(point, report)?],
        bytes,
        cancel,
        |pending| write(&pending.join("source.json"), &source),
    )
}

#[allow(clippy::too_many_arguments)]
fn retain(
    workspace: &Path,
    model: &str,
    cycle: &str,
    kind: &str,
    observers: Vec<Observer>,
    bytes: u64,
    cancel: &AtomicBool,
    populate: impl FnOnce(&Path) -> Result<(), String>,
) -> Result<Value, String> {
    check(cancel)?;
    observers_valid(&observers)?;
    time_contract::parse_utc(cycle)?;
    if !matches!(model, "gfs" | "ec" | "satellite") || bytes > MAX_SNAPSHOT_BYTES {
        return Err("profile source model or size exceeds its resource bound".into());
    }
    let root = root(workspace)?;
    let _guard =
        cache_lock::Lock::acquire_checked(&root.join("store.lock"), 30.0, || check(cancel))?;
    let now: DateTime<Utc> = std::time::SystemTime::now().into();
    remove_incomplete(&root)?;
    maintain(&root, bytes, now)?;
    let pending = tempfile::Builder::new()
        .prefix(".pending-")
        .tempdir_in(&root)
        .map_err(|e| e.to_string())?;
    populate(pending.path())?;
    check(cancel)?;
    let source_identity = tensor_bundle::regular_identity(&pending.path().join("source.json"))?;
    let mut item = Receipt {
        contract: CONTRACT.into(),
        result_id: String::new(),
        model_source: model.into(),
        cycle_utc: cycle.into(),
        generated_at_utc: time_contract::iso(now),
        expires_at_utc: time_contract::iso(now + Duration::seconds(TTL_SECONDS)),
        source_identity,
        profile_kind: kind.into(),
        observers,
        bytes,
        nonce: pending
            .path()
            .file_name()
            .unwrap()
            .to_string_lossy()
            .into_owned(),
    };
    item.result_id = receipt_id(&item)?;
    if serde_json::to_vec(&item).map_err(|e| e.to_string())?.len() as u64 > MAX_RECEIPT_BYTES {
        return Err("profile receipt exceeds JSON bound".into());
    }
    write(&pending.path().join("receipt.json"), &item)?;
    let actual_bytes = tree_bytes(pending.path())?;
    if actual_bytes > MAX_SNAPSHOT_BYTES {
        return Err("profile snapshot including receipt exceeds storage bound".into());
    }
    maintain(&root, actual_bytes, now)?;
    check(cancel)?;
    fs::rename(pending.path(), root.join(&item.result_id)).map_err(|e| e.to_string())?;
    Ok(descriptor(&item))
}

pub fn query(workspace: &Path, params: Query, cancel: &AtomicBool) -> Result<Value, String> {
    let mut response = json!({
        "contract":"rust-desktop-profile-query-v1","result_id":params.result_id,
        "source_identity":params.source_identity,"model_source":params.model_source,
        "lat":params.lat,"lon":params.lon,"valid_time_utc":params.valid_time_utc,
        "status":"invalid","cross_section":null,"satellite_vertical_profile":null,
    });
    let result = query_inner(workspace, &params, cancel);
    match result {
        Ok(value) => {
            response["status"] = value["status"].clone();
            for key in [
                "cross_section",
                "profile_kind",
                "reason",
                "cycle_utc",
                "source_times_utc",
            ] {
                if let Some(value) = value.get(key) {
                    response[key] = value.clone();
                }
            }
        }
        Err(error) => {
            response["status"] = json!(if cancel.load(Ordering::Acquire) {
                "cancelled"
            } else {
                "invalid"
            });
            response["reason"] = json!(error);
        }
    }
    Ok(response)
}

fn query_inner(workspace: &Path, params: &Query, cancel: &AtomicBool) -> Result<Value, String> {
    check(cancel)?;
    if !valid_id(&params.result_id) || !params.lat.is_finite() || !params.lon.is_finite() {
        return Err("profile query identity or coordinates are invalid".into());
    }
    let selected = time_contract::parse_utc(&params.valid_time_utc)?;
    let root = root(workspace)?;
    let _guard =
        cache_lock::Lock::acquire_checked(&root.join("store.lock"), 30.0, || check(cancel))?;
    let directory = root.join(&params.result_id);
    if !directory.exists() {
        return Ok(
            json!({"status":"missing","reason":"Frozen result source was removed or is unavailable"}),
        );
    }
    if !fs::symlink_metadata(&directory)
        .map_err(|e| e.to_string())?
        .file_type()
        .is_dir()
    {
        return Err("profile source directory is not regular".into());
    }
    let item = receipt(&directory)?;
    if item.contract != CONTRACT
        || item.result_id != params.result_id
        || item.model_source != params.model_source
        || item.source_identity != params.source_identity
    {
        return Err("profile query does not match the frozen result/source/model".into());
    }
    let now: DateTime<Utc> = std::time::SystemTime::now().into();
    if now >= time_contract::parse_utc(&item.expires_at_utc)? {
        return Ok(
            json!({"status":"expired","reason":"Frozen profile source expired; explicitly run a new forecast"}),
        );
    }
    let point = item
        .observers
        .iter()
        .find(|point| point.lat == params.lat && point.lon == params.lon)
        .ok_or("coordinates do not belong to this result")?;
    let source_path = directory.join("source.json");
    if tensor_bundle::regular_identity(&source_path)? != item.source_identity {
        return Err("frozen profile source identity changed".into());
    }
    let section = if item.model_source == "satellite" {
        let (source, read_identity): (Value, _) =
            request_file::read(&source_path, MAX_RECEIPT_BYTES)?;
        if read_identity != item.source_identity
            || source["contract"] != "rust-desktop-satellite-profile-steps-v1"
            || source["cycle_utc"] != item.cycle_utc
        {
            return Err("satellite profile source changed or differs from its receipt".into());
        }
        let step = source["steps"]
            .as_array()
            .ok_or("satellite source steps missing")?
            .iter()
            .find(|step| {
                step["valid_time_local"]
                    .as_str()
                    .and_then(|s| DateTime::parse_from_rfc3339(s).ok())
                    .is_some_and(|time| time.with_timezone(&Utc) == selected)
            });
        let Some(step) = step else {
            return Ok(
                json!({"status":"unavailable","reason":"No satellite diagnostic exists at this exact UTC time"}),
            );
        };
        if !step["cross_section"].is_object() {
            return Ok(
                json!({"status":"unavailable","reason":"This satellite step has no solar-path diagnostic"}),
            );
        }
        let mut result = json!({
            "status":"ready","cross_section":step["cross_section"],"profile_kind":item.profile_kind,
            "cycle_utc":item.cycle_utc,"source_times_utc":step["cloud_source_times_utc"],
            "reason":"Per-step solar-path diagnostic; the independent observed vertical samples are not re-timed or stitched",
        });
        check(cancel)?;
        result["satellite_vertical_profile"] = Value::Null;
        return Ok(result);
    } else {
        let anchor = time_contract::parse_utc(&point.anchor_utc)?;
        let offset = (selected - anchor)
            .num_microseconds()
            .ok_or("profile time difference overflow")? as f64
            / 60_000_000.0;
        cross_section::for_point(
            source_path,
            item.source_identity.clone(),
            &crate::frames::Point {
                name: "Frozen result observer".into(),
                lat: point.lat,
                lon: point.lon,
                elevation_m: point.elevation_m,
                timezone_name: point.timezone_name.clone(),
            },
            offset,
        )?
    };
    check(cancel)?;
    Ok(
        json!({"status":"ready","cross_section":section,"profile_kind":item.profile_kind,
        "cycle_utc":item.cycle_utc}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cancelled() -> AtomicBool {
        AtomicBool::new(false)
    }

    fn model_fixture(workspace: &Path, model: &str) -> (Value, String) {
        let output = workspace.join(format!("fixture-{model}"));
        fs::create_dir(&output).unwrap();
        let seed = output.join("request.json");
        write(&seed, &json!({"fixture":"frozen-profile-v1"})).unwrap();
        let seed_identity = tensor_bundle::regular_identity(&seed).unwrap();
        let mut frames = Vec::new();
        for (index, hour) in [0.0, 24.0, 48.0, 72.0].into_iter().enumerate() {
            let path = output.join(format!("profiles_{index:03}"));
            let mut writer = tensor_bundle::BundleWriter::create(
                &path, "regional-profiles-v1", seed_identity.clone(),
                json!({"frame_index":index,"hour":hour,"pressure_levels":[300.0],"profile_fields":24}),
            ).unwrap();
            let mut cell = [0.0_f32; 24];
            cell[0] = 80.0;
            cell[1] = 9000.0;
            cell[2] = 230.0 + index as f32 * 24.0;
            cell[6] = 50.0;
            cell[12] = 0.0001;
            cell[13] = 1.0;
            cell[14] = 300.0;
            cell[17] = 8000.0;
            cell[18] = 10000.0;
            writer
                .f32("profiles", &[63, 1, 24], (0..63).flat_map(|_| cell))
                .unwrap();
            let manifest = writer.commit(&seed).unwrap();
            frames.push(
                json!({"manifest":format!("profiles_{index:03}/manifest.json"),
                "identity":tensor_bundle::regular_identity(&manifest).unwrap()}),
            );
        }
        let source = json!({
            "contract":"rust-cross-section-source-v1","model_source":model,
            "terrain_source":"frozen-terrain","target_date":"2026-11-09","phenomenon":"dusk",
            "cycle_utc":"2026-11-09T00:00:00Z","hours":[0,24,48,72],
            "pressure_levels":[300.0],"geometry":{"rows":7,"cols":9,"lat0":-10.0,"lon0":90.0,"spacing":3.0},
            "profile_bundles":frames,
        });
        let source_path = output.join("cross_section_source.json");
        write(&source_path, &source).unwrap();
        let source_identity = tensor_bundle::regular_identity(&source_path).unwrap();
        let point = crate::frames::Point {
            name: "Frozen point".into(),
            lat: 0.25,
            lon: 100.25,
            elevation_m: 50.0,
            timezone_name: "Asia/Shanghai".into(),
        };
        let embedded =
            cross_section::for_point(source_path.clone(), source_identity.clone(), &point, 0.0)
                .unwrap();
        let anchor = DateTime::parse_from_rfc3339(embedded["valid_time_local"].as_str().unwrap())
            .unwrap()
            .with_timezone(&Utc);
        let descriptor = retain_model(
            workspace,
            &output,
            &json!({"identity":source_identity}),
            vec![Observer {
                lat: point.lat,
                lon: point.lon,
                elevation_m: point.elevation_m,
                timezone_name: point.timezone_name,
                anchor_utc: time_contract::iso(anchor),
            }],
            &cancelled(),
        )
        .unwrap();
        assert!(!source_path.exists());
        assert!(!output.join("profiles_000").exists());
        (descriptor, time_contract::iso(anchor))
    }

    fn request(descriptor: &Value, time: &str) -> Value {
        json!({"result_id":descriptor["result_id"],"source_identity":descriptor["source_identity"],
            "model_source":descriptor["model_source"],"lat":0.25,"lon":100.25,"valid_time_utc":time})
    }

    fn run(workspace: &Path, value: Value) -> Value {
        query(
            workspace,
            serde_json::from_value(value).unwrap(),
            &cancelled(),
        )
        .unwrap()
    }

    #[test]
    fn frozen_model_queries_recalculate_real_time_and_never_mix_models() {
        let workspace = tempfile::tempdir().unwrap();
        let (gfs, anchor) = model_fixture(workspace.path(), "gfs");
        let (ec, _) = model_fixture(workspace.path(), "ec");
        let first = run(workspace.path(), request(&gfs, &anchor));
        assert_eq!(first["status"], "ready", "{first}");
        assert_eq!(first["cross_section"]["model_source"], "gfs");
        let later =
            time_contract::iso(time_contract::parse_utc(&anchor).unwrap() + Duration::minutes(5));
        let second = run(workspace.path(), request(&gfs, &later));
        assert_eq!(second["status"], "ready", "{second}");
        assert_ne!(
            first["cross_section"]["fields"]["temperature_c"],
            second["cross_section"]["fields"]["temperature_c"]
        );
        assert_ne!(
            first["cross_section"]["valid_time_local"],
            second["cross_section"]["valid_time_local"]
        );
        assert_eq!(
            run(workspace.path(), request(&ec, &anchor))["cross_section"]["model_source"],
            "ec"
        );
        let mut mixed = request(&gfs, &anchor);
        mixed["model_source"] = json!("ec");
        assert_eq!(run(workspace.path(), mixed)["status"], "invalid");
    }

    #[test]
    fn runtime_advertises_and_dispatches_real_frozen_profile_queries() {
        let workspace = tempfile::tempdir().unwrap();
        let (source, anchor) = model_fixture(workspace.path(), "gfs");
        let runtime = crate::desktop_runtime::Runtime::new(workspace.path()).unwrap();
        let hello = runtime.dispatch("hello", json!({})).unwrap();
        assert!(hello["capabilities"]
            .as_array()
            .unwrap()
            .contains(&json!("profile_query")));
        assert_eq!(
            hello["profile_capabilities"]["dynamic_satellite_vertical"],
            false
        );
        let response = runtime
            .dispatch("profile_query", request(&source, &anchor))
            .unwrap();
        assert_eq!(response["status"], "ready", "{response}");
        assert_eq!(response["result_id"], source["result_id"]);
        assert_eq!(response["source_identity"], source["source_identity"]);
    }

    #[test]
    fn profile_query_binds_every_request_dimension_and_explicit_cancel() {
        let workspace = tempfile::tempdir().unwrap();
        let (source, anchor) = model_fixture(workspace.path(), "gfs");
        for (field, value) in [
            ("lat", json!(0.5)),
            ("lon", json!(100.5)),
            ("model_source", json!("ec")),
            (
                "source_identity",
                json!({"bytes":1,"sha256":"0".repeat(64)}),
            ),
            ("valid_time_utc", json!("2026-11-09T00:00:01Z")),
        ] {
            let mut value_request = request(&source, &anchor);
            value_request[field] = value;
            assert_eq!(
                run(workspace.path(), value_request)["status"],
                "invalid",
                "{field}"
            );
        }
        let mut missing = request(&source, &anchor);
        missing["result_id"] = json!("a".repeat(64));
        assert_eq!(run(workspace.path(), missing)["status"], "missing");
        let response = query(
            workspace.path(),
            serde_json::from_value(request(&source, &anchor)).unwrap(),
            &AtomicBool::new(true),
        )
        .unwrap();
        assert_eq!(response["status"], "cancelled");
    }

    #[test]
    fn frozen_tensor_and_receipt_mutations_are_not_silently_rebuilt() {
        let workspace = tempfile::tempdir().unwrap();
        let (source, anchor) = model_fixture(workspace.path(), "gfs");
        let directory = root(workspace.path())
            .unwrap()
            .join(source["result_id"].as_str().unwrap());
        let array = directory.join("profiles_000/profiles.npy");
        let mut bytes = fs::read(&array).unwrap();
        let last = bytes.len() - 1;
        bytes[last] ^= 1;
        fs::write(&array, bytes).unwrap();
        assert_eq!(
            run(workspace.path(), request(&source, &anchor))["status"],
            "invalid"
        );
        let path = directory.join("receipt.json");
        let mut item: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        item["observers"][0]["lat"] = json!(1.0);
        fs::write(&path, serde_json::to_vec(&item).unwrap()).unwrap();
        assert_eq!(
            run(workspace.path(), request(&source, &anchor))["status"],
            "invalid"
        );
    }

    #[test]
    fn expired_source_is_explicit_and_does_not_change_cycle() {
        let workspace = tempfile::tempdir().unwrap();
        let (mut source, anchor) = model_fixture(workspace.path(), "gfs");
        let root = root(workspace.path()).unwrap();
        let directory = root.join(source["result_id"].as_str().unwrap());
        let mut item = receipt(&directory).unwrap();
        item.expires_at_utc = "2020-01-01T00:00:00Z".into();
        item.result_id = receipt_id(&item).unwrap();
        fs::write(
            directory.join("receipt.json"),
            serde_json::to_vec(&item).unwrap(),
        )
        .unwrap();
        fs::rename(directory, root.join(&item.result_id)).unwrap();
        source["result_id"] = json!(item.result_id);
        let result = run(workspace.path(), request(&source, &anchor));
        assert_eq!(result["status"], "expired");
        assert!(result["cross_section"].is_null());
    }

    fn satellite(workspace: &Path) -> Value {
        retain_satellite(workspace,
            &json!({"lat":0.25,"lon":100.25,"timezone_name":"Asia/Shanghai"}),
            &json!({
                "observer_elevation_asl_m":50.0,"anchor_local":"2026-11-09T17:00:00+08:00",
                "cycle_time_utc":"2026-11-09T09:00:00Z","steps":[
                    {"valid_time_local":"2026-11-09T17:00:00+08:00","solar_azimuth_deg":260.0,
                        "cloud_source_times_utc":["2026-11-09T09:00:00Z"],
                        "cross_section":{"samples_count":1,"profile":[{"distance_km":25,"solar_azimuth_deg":260.0}]}},
                    {"valid_time_local":"2026-11-09T17:05:00+08:00","solar_azimuth_deg":261.0,
                        "cloud_source_times_utc":["2026-11-09T09:05:00Z"],
                        "cross_section":{"samples_count":1,"profile":[{"distance_km":50,"solar_azimuth_deg":261.0}]}},
                ],
            }), &cancelled()).unwrap()
    }

    #[test]
    fn satellite_returns_only_exact_real_step_not_retimed_vertical_samples() {
        let workspace = tempfile::tempdir().unwrap();
        let source = satellite(workspace.path());
        let first = run(workspace.path(), request(&source, "2026-11-09T09:00:00Z"));
        let second = run(workspace.path(), request(&source, "2026-11-09T09:05:00Z"));
        assert_eq!(first["status"], "ready");
        assert_eq!(second["status"], "ready");
        assert_ne!(first["cross_section"], second["cross_section"]);
        assert_eq!(first["source_times_utc"], json!(["2026-11-09T09:00:00Z"]));
        assert!(first["satellite_vertical_profile"].is_null());
        assert_eq!(
            run(workspace.path(), request(&source, "2026-11-09T09:02:00Z"))["status"],
            "unavailable"
        );
    }

    #[test]
    fn snapshot_count_is_bounded_and_eviction_is_explicit() {
        let workspace = tempfile::tempdir().unwrap();
        let oldest = satellite(workspace.path());
        for _ in 0..MAX_SNAPSHOTS {
            satellite(workspace.path());
        }
        let root = root(workspace.path()).unwrap();
        let count = fs::read_dir(root)
            .unwrap()
            .filter_map(Result::ok)
            .filter(|entry| valid_id(&entry.file_name().to_string_lossy()))
            .count();
        assert_eq!(count, MAX_SNAPSHOTS);
        assert_eq!(
            run(workspace.path(), request(&oldest, "2026-11-09T09:00:00Z"))["status"],
            "missing"
        );
    }

    #[test]
    fn verified_cross_filesystem_copy_preserves_bytes_and_rejects_overwrite() {
        let workspace = tempfile::tempdir().unwrap();
        let source = workspace.path().join("source");
        fs::create_dir(&source).unwrap();
        write(
            &source.join("manifest.json"),
            &json!({"fixture":"frozen-copy-v1"}),
        )
        .unwrap();
        let destination = workspace.path().join("copy");
        copy_verified(&source, &destination).unwrap();
        assert_eq!(
            tensor_bundle::regular_identity(&source.join("manifest.json")).unwrap(),
            tensor_bundle::regular_identity(&destination.join("manifest.json")).unwrap()
        );
        assert!(copy_verified(&source, &destination).is_err());
    }

    #[test]
    fn incomplete_sources_are_not_registered_and_are_reclaimed() {
        let workspace = tempfile::tempdir().unwrap();
        let root = root(workspace.path()).unwrap();
        let incomplete = root.join(".pending-crashed-fixture");
        fs::create_dir(&incomplete).unwrap();
        write(
            &incomplete.join("uncommitted.json"),
            &json!({"fixture":true}),
        )
        .unwrap();
        let source = satellite(workspace.path());
        assert!(!incomplete.exists());
        assert_eq!(
            run(workspace.path(), request(&source, "2026-11-09T09:00:00Z"))["status"],
            "ready"
        );
    }

    #[cfg(unix)]
    #[test]
    fn cross_filesystem_copy_rejects_symlink_children() {
        let workspace = tempfile::tempdir().unwrap();
        let source = workspace.path().join("source");
        fs::create_dir(&source).unwrap();
        let outside = workspace.path().join("outside.json");
        write(&outside, &json!({"outside":true})).unwrap();
        std::os::unix::fs::symlink(outside, source.join("manifest.json")).unwrap();
        assert!(copy_verified(&source, &workspace.path().join("copy")).is_err());
    }
}
