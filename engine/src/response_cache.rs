//! Immutable complete EC service generations with atomic latest pointers.
//! A hit is a historical snapshot, not proof of current raw-input freshness.
use crate::{
    cache_lock::Lock,
    cross_section, ecmwf_plan, ecmwf_service, frames, request_file,
    tensor_bundle::{regular_identity, verify_files, SourceIdentity},
    time_contract,
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    time::Instant,
};

const CONTRACT: &str = "rust-ecmwf-response-cache-v1";
const MAX_JSON: u64 = 16 * 1024 * 1024;
const MAX_RECEIPT: u64 = 256 * 1024;
const MAX_FILES: usize = 256;
const MAX_BYTES: u64 = 64 * 1024 * 1024 * 1024;
fn timeout() -> f64 {
    30.0
}
fn now() -> DateTime<Utc> {
    std::time::SystemTime::now().into()
}
fn digest(value: &impl Serialize) -> Result<String, String> {
    Ok(
        Sha256::digest(serde_json::to_vec(value).map_err(|e| e.to_string())?)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect(),
    )
}
fn hex(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn generation(value: &str) -> bool {
    value.len() == 32 && value.bytes().all(|b| b.is_ascii_alphanumeric())
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Params {
    path: PathBuf,
    cache_dir: PathBuf,
    #[serde(default)]
    force_refresh: bool,
    max_age_seconds: Option<f64>,
    #[serde(default = "timeout")]
    lock_timeout_seconds: f64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ReadParams {
    cache_dir: PathBuf,
    response_key: String,
    lat: f64,
    lon: f64,
    offset_minutes: f64,
    elevation_m: Option<f64>,
    timezone_name: Option<String>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Index {
    contract: String,
    key: String,
    generation: String,
    receipt: SourceIdentity,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Receipt {
    contract: String,
    key: String,
    generation: String,
    identity: Value,
    generated_at_utc: String,
    generation_request_source: SourceIdentity,
    manifest: SourceIdentity,
    #[serde(deserialize_with = "unique_files")]
    files: BTreeMap<String, SourceIdentity>,
}
fn unique_files<'de, D: serde::Deserializer<'de>>(
    input: D,
) -> Result<BTreeMap<String, SourceIdentity>, D::Error> {
    struct Unique;
    impl<'de> serde::de::Visitor<'de> for Unique {
        type Value = BTreeMap<String, SourceIdentity>;
        fn expecting(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            f.write_str("unique snapshot files")
        }
        fn visit_map<M: serde::de::MapAccess<'de>>(
            self,
            mut map: M,
        ) -> Result<Self::Value, M::Error> {
            let mut result = BTreeMap::new();
            while let Some((key, value)) = map.next_entry::<String, SourceIdentity>()? {
                if result.len() >= MAX_FILES || result.insert(key, value).is_some() {
                    return Err(serde::de::Error::custom(
                        "too many or duplicate snapshot files",
                    ));
                }
            }
            Ok(result)
        }
    }
    input.deserialize_map(Unique)
}
struct Snapshot {
    root: PathBuf,
    receipt: Receipt,
    receipt_identity: SourceIdentity,
    service: Value,
}
fn directory(path: &Path) -> Result<(), String> {
    if !fs::symlink_metadata(path)
        .map_err(|e| e.to_string())?
        .file_type()
        .is_dir()
    {
        return Err("response cache requires nonsymlink directories".into());
    }
    Ok(())
}
fn root(path: &Path) -> Result<PathBuf, String> {
    if !path.is_absolute() {
        return Err("response cache_dir must be absolute".into());
    }
    fs::create_dir_all(path).map_err(|e| e.to_string())?;
    directory(path)?;
    path.canonicalize().map_err(|e| e.to_string())
}
fn sync_dir(path: &Path) -> Result<(), String> {
    #[cfg(unix)]
    File::open(path)
        .and_then(|f| f.sync_all())
        .map_err(|e| e.to_string())?;
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}
fn write_new(path: &Path, value: &impl Serialize, limit: u64) -> Result<SourceIdentity, String> {
    let bytes = serde_json::to_vec(value).map_err(|e| e.to_string())?;
    if bytes.len() as u64 > limit {
        return Err("response cache JSON exceeds bound".into());
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    file.write_all(&bytes)
        .and_then(|_| file.sync_all())
        .map_err(|e| e.to_string())?;
    regular_identity(path)
}
fn json_file(path: &Path) -> Result<(Value, SourceIdentity), String> {
    request_file::read(path, MAX_JSON)
}
fn identity(value: &Value) -> Result<SourceIdentity, String> {
    serde_json::from_value(value.clone()).map_err(|e| e.to_string())
}
fn require(condition: bool, message: &str) -> Result<(), String> {
    if condition {
        Ok(())
    } else {
        Err(message.into())
    }
}

/// All files are discovered from validated manifests or fixed artifact names.
/// No arbitrary receipt-provided path is opened.
fn verify_bundle(
    root: &Path,
) -> Result<
    (
        SourceIdentity,
        SourceIdentity,
        BTreeMap<String, SourceIdentity>,
        Value,
    ),
    String,
> {
    directory(root)?;
    let parent = verify_files(&root.join("manifest.json"), "ecmwf-service-forecast-v1")?;
    let metadata = &parent.metadata;
    require(
        metadata["model"] == "ecmwf-basic",
        "snapshot is not an EC basic service",
    )?;
    let profiles = metadata["profile_bundles"]
        .as_array()
        .ok_or("missing snapshot profiles")?;
    let levels = metadata["pressure_levels"]
        .as_array()
        .ok_or("missing snapshot pressure levels")?;
    require(
        !profiles.is_empty() && profiles.len() <= 64 && !levels.is_empty() && levels.len() <= 11,
        "invalid snapshot time/pressure dimensions",
    )?;
    let cells = parent
        .shapes
        .get("latitudes")
        .filter(|v| v.len() == 1)
        .ok_or("missing snapshot coordinates")?[0];
    require(cells > 0 && cells <= 500_000, "invalid snapshot cell count")?;
    let required = [
        "aerosol_anchor_brackets",
        "aerosol_profiles",
        "anchor_epoch",
        "assessment",
        "columns",
        "evaluation_epoch",
        "hours",
        "instant_quality",
        "latitudes",
        "longitudes",
        "observers",
        "profile_diagnostics",
        "quality",
        "quality_diagnostics",
        "quality_flags",
        "samples",
    ];
    require(
        parent.shapes.keys().map(String::as_str).collect::<Vec<_>>() == required,
        "snapshot must retain all sixteen service arrays",
    )?;
    require(
        parent.shapes["columns"] == [profiles.len() as u64, cells, 36]
            && parent.shapes["longitudes"] == [cells]
            && parent.shapes["hours"] == [profiles.len() as u64]
            && parent.shapes["observers"] == [1, 3],
        "snapshot main tensor axes mismatch",
    )?;
    let mut files = parent.files;
    for (key, name) in [
        ("input_plan", "input_plan.json"),
        ("frame_payload", "forecast.json"),
        ("point_forecast", "point_forecast.json"),
        ("ecmwf_point_report", "ecmwf_point.json"),
        ("cross_section_source", "cross_section_source.json"),
        ("ecmwf_service", "ecmwf_service.json"),
    ] {
        let (_, actual) = json_file(&root.join(name))?;
        require(
            metadata[key]["file"] == name && identity(&metadata[key]["identity"])? == actual,
            "snapshot artifact descriptor mismatch",
        )?;
        files.insert(name.into(), actual);
    }
    let (source, _) = json_file(&root.join("cross_section_source.json"))?;
    require(
        source["contract"] == "rust-cross-section-source-v1"
            && source["model_source"] == "ec"
            && source["profile_bundles"] == metadata["profile_bundles"]
            && source["pressure_levels"] == metadata["pressure_levels"]
            && source["geometry"] == metadata["geometry"],
        "snapshot cross-section axes mismatch",
    )?;
    let hours = source["hours"].as_array().ok_or("missing snapshot hours")?;
    require(
        hours.len() == profiles.len(),
        "snapshot profile/hour count mismatch",
    )?;
    for (i, descriptor) in profiles.iter().enumerate() {
        let folder = format!("profiles_{i:03}");
        require(
            descriptor["manifest"] == format!("{folder}/manifest.json"),
            "nonlocal snapshot profile reference",
        )?;
        let child = verify_files(
            &root.join(&folder).join("manifest.json"),
            "regional-profiles-v1",
        )?;
        require(
            identity(&descriptor["identity"])? == child.manifest
                && child.shapes.len() == 1
                && child.shapes.get("profiles") == Some(&vec![cells, levels.len() as u64, 24])
                && child.metadata["frame_index"] == i
                && child.metadata["hour"] == hours[i]
                && child.metadata["pressure_levels"] == metadata["pressure_levels"]
                && child.metadata["profile_fields"] == 24,
            "snapshot profile axes/identity mismatch",
        )?;
        for (file, actual) in child.files {
            files.insert(format!("{folder}/{file}"), actual);
        }
    }
    let html = regular_identity(&root.join("report.html"))?;
    require(html.bytes <= MAX_JSON, "snapshot HTML exceeds bound")?;
    files.insert("report.html".into(), html.clone());
    let (service, _) = json_file(&root.join("ecmwf_service.json"))?;
    require(
        service["contract"] == "rust-ecmwf-service-v1"
            && service["html_artifact"]["file"] == "report.html"
            && identity(&service["html_artifact"]["identity"])? == html
            && service["provenance"]["native_profile_source"]["file"]
                == "cross_section_source.json"
            && identity(&service["provenance"]["native_profile_source"]["identity"])?
                == files["cross_section_source.json"],
        "snapshot service/artifact identity mismatch",
    )?;
    let total = files
        .values()
        .try_fold(0u64, |sum, v| sum.checked_add(v.bytes))
        .ok_or("snapshot size overflow")?;
    require(
        files.len() <= MAX_FILES && total <= MAX_BYTES,
        "snapshot exceeds total size bound",
    )?;
    let mut found = BTreeSet::new();
    for entry in fs::read_dir(root).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| "snapshot filename is not UTF-8")?;
        let file_type = entry.file_type().map_err(|e| e.to_string())?;
        if file_type.is_dir() {
            require(
                (0..profiles.len()).any(|i| name == format!("profiles_{i:03}")),
                "unknown snapshot directory",
            )?;
            for child in fs::read_dir(entry.path()).map_err(|e| e.to_string())? {
                let child = child.map_err(|e| e.to_string())?;
                require(
                    child.file_type().map_err(|e| e.to_string())?.is_file(),
                    "snapshot profile file is not regular",
                )?;
                let file = child
                    .file_name()
                    .into_string()
                    .map_err(|_| "invalid snapshot profile filename")?;
                require(
                    found.insert(format!("{name}/{file}")),
                    "duplicate snapshot filename",
                )?;
            }
        } else {
            require(file_type.is_file(), "snapshot files must not be symlinks")?;
            found.insert(name);
        }
        require(
            found.len() <= MAX_FILES,
            "snapshot file count exceeds bound",
        )?;
    }
    require(
        found == files.keys().cloned().collect(),
        "snapshot has missing/unknown files",
    )?;
    require(
        regular_identity(&root.join("manifest.json"))? == parent.manifest,
        "snapshot changed during verification",
    )?;
    Ok((parent.manifest, parent.source, files, service))
}
fn load(
    key_root: &Path,
    key: &str,
    version: &str,
    expected: Option<&SourceIdentity>,
    expected_sha: Option<&str>,
) -> Result<Snapshot, String> {
    require(
        hex(key) && generation(version),
        "invalid response cache key/generation",
    )?;
    directory(key_root)?;
    let root = key_root.join(version);
    directory(&root)?;
    let (receipt, actual): (Receipt, _) =
        request_file::read(&root.join("receipt.json"), MAX_RECEIPT)?;
    require(
        expected.is_none_or(|v| v == &actual) && expected_sha.is_none_or(|v| v == actual.sha256),
        "response receipt checksum mismatch",
    )?;
    require(
        receipt.contract == CONTRACT
            && receipt.key == key
            && receipt.generation == version
            && receipt.identity["engine_source"] == env!("FIRESKY_ENGINE_SOURCE_ID")
            && digest(&receipt.identity)? == key,
        "response receipt identity mismatch",
    )?;
    time_contract::parse_utc(&receipt.generated_at_utc)?;
    let bundle = root.join("bundle");
    let (manifest, source, files, service) = verify_bundle(&bundle)?;
    require(
        receipt.manifest == manifest
            && receipt.files == files
            && receipt.generation_request_source == source,
        "response receipt file inventory mismatch",
    )?;
    require(
        regular_identity(&root.join("receipt.json"))? == actual,
        "response receipt changed",
    )?;
    Ok(Snapshot {
        root: bundle,
        receipt,
        receipt_identity: actual,
        service,
    })
}
fn token(snapshot: &Snapshot) -> String {
    format!(
        "ec-response:{}:{}:{}",
        snapshot.receipt.key, snapshot.receipt.generation, snapshot.receipt_identity.sha256
    )
}
fn parse_token(value: &str) -> Result<(&str, &str, &str), String> {
    let parts: Vec<_> = value
        .strip_prefix("ec-response:")
        .ok_or("invalid response key namespace")?
        .split(':')
        .collect();
    if parts.len() != 3 || !hex(parts[0]) || !generation(parts[1]) || !hex(parts[2]) {
        return Err("invalid registered response key".into());
    }
    Ok((parts[0], parts[1], parts[2]))
}
fn view(
    snapshot: Snapshot,
    status: &str,
    reason: &str,
    request_source: SourceIdentity,
    elapsed: f64,
) -> Value {
    let response_key = token(&snapshot);
    let metadata = json!({
        "status":status,"contract":CONTRACT,"namespace":"ecmwf-ifs-basic-native","identity_key":snapshot.receipt.key,
        "generated_at_utc":snapshot.receipt.generated_at_utc,"identity_payload":snapshot.receipt.identity,
        "response_key":response_key,"reason":reason,"source_freshness":"immutable-snapshot-not-current-raw-validation",
        "generation_request_source":snapshot.receipt.generation_request_source,"request_source":request_source,
        "elapsed_ms":elapsed,"native_response_cache_available":true,"production_http_complete":false,
    });
    let mut service = snapshot.service;
    service["report"]["html_report_path"] = json!(snapshot.root.join("report.html"));
    service["response"]["response_cache"] = metadata.clone();
    service["response"]["cross_section_request"]["response_key"] = json!(response_key);
    service["native_response_cache_available"] = json!(true);
    json!({"contract":"rust-ecmwf-cached-service-v1","service":service,
        "manifest_path":snapshot.root.join("manifest.json"),"manifest_identity":snapshot.receipt.manifest,
        "response_key":response_key,"response_cache":metadata,"service_view_is_contextual":true,
        "native_response_cache_available":true,"production_http_complete":false,"public_forecast_complete":false})
}
fn expired(generated: &str, max_age: Option<f64>, now: DateTime<Utc>) -> Result<bool, String> {
    let created = time_contract::parse_utc(generated)?;
    let age = (now - created)
        .num_microseconds()
        .ok_or("response age overflow")? as f64
        / 1e6;
    Ok(age < -300.0 || max_age.is_some_and(|limit| limit == 0.0 || age >= limit))
}
pub(crate) fn execute(params: Params) -> Result<Value, String> {
    let started = Instant::now();
    require(
        params.lock_timeout_seconds.is_finite()
            && (0.0..=3600.0).contains(&params.lock_timeout_seconds)
            && params
                .max_age_seconds
                .is_none_or(|v| v.is_finite() && (0.0..=365.0 * 86400.0).contains(&v)),
        "invalid response cache age/lock limits",
    )?;
    require(
        params.cache_dir.is_absolute(),
        "response cache_dir must be absolute",
    )?;
    let request = ecmwf_plan::service_request(&params.path)?;
    let root = root(&params.cache_dir)?;
    let key = digest(&request.identity)?;
    let _lock = Lock::acquire(
        &root.join(format!("{key}.lock")),
        params.lock_timeout_seconds,
    )
    .map_err(|e| e.replace("GFS cache", "response cache"))?;
    let key_root = root.join(&key);
    fs::create_dir_all(&key_root).map_err(|e| e.to_string())?;
    directory(&key_root)?;
    let index_path = key_root.join("index.json");
    let index_exists = match fs::symlink_metadata(&index_path) {
        Ok(m) => {
            require(
                m.file_type().is_file(),
                "response index must be a regular nonsymlink file",
            )?;
            true
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => false,
        Err(e) => return Err(e.to_string()),
    };
    let mut reason = if params.force_refresh {
        "forced".to_owned()
    } else {
        "miss".to_owned()
    };
    let mut invalid_index = false;
    if index_exists {
        let index: Result<(Index, SourceIdentity), _> =
            request_file::read(&index_path, MAX_RECEIPT);
        let cached = index.and_then(|(index, _)| {
            require(
                index.contract == CONTRACT && index.key == key,
                "invalid response index identity",
            )?;
            load(
                &key_root,
                &key,
                &index.generation,
                Some(&index.receipt),
                None,
            )
        });
        match cached {
            Ok(snapshot) => {
                require(
                    snapshot.receipt.identity == request.identity,
                    "response semantic identity mismatch",
                )?;
                if !params.force_refresh {
                    if !expired(
                        &snapshot.receipt.generated_at_utc,
                        params.max_age_seconds,
                        now(),
                    )? {
                        require(
                            regular_identity(&params.path)? == request.source,
                            "response request changed",
                        )?;
                        return Ok(view(
                            snapshot,
                            "hit",
                            "snapshot",
                            request.source,
                            started.elapsed().as_secs_f64() * 1000.0,
                        ));
                    }
                    reason = "expired".into();
                }
            }
            Err(_) => {
                invalid_index = true;
                if !params.force_refresh {
                    reason = "invalid".into();
                }
            }
        }
    }
    let pending = tempfile::Builder::new()
        .prefix("")
        .rand_bytes(32)
        .tempdir_in(&key_root)
        .map_err(|e| e.to_string())?;
    let version = pending
        .path()
        .file_name()
        .and_then(|v| v.to_str())
        .ok_or("invalid generated directory")?
        .to_owned();
    require(generation(&version), "invalid generated response version")?;
    let bundle = pending.path().join("bundle");
    let request_source = request.source.clone();
    let identity_payload = request.identity.clone();
    ecmwf_plan::service_from_request(&params.path, &bundle, request, started)?;
    let (manifest, source, files, service) = verify_bundle(&bundle)?;
    require(
        source == request_source && regular_identity(&params.path)? == request_source,
        "response request changed during computation",
    )?;
    let receipt = Receipt {
        contract: CONTRACT.into(),
        key: key.clone(),
        generation: version.clone(),
        identity: identity_payload,
        generated_at_utc: now().to_rfc3339_opts(chrono::SecondsFormat::Micros, true),
        generation_request_source: source,
        manifest,
        files,
    };
    let actual = write_new(&pending.path().join("receipt.json"), &receipt, MAX_RECEIPT)?;
    for entry in fs::read_dir(&bundle).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        if entry.file_type().map_err(|e| e.to_string())?.is_dir() {
            sync_dir(&entry.path())?;
        }
    }
    sync_dir(&bundle)?;
    sync_dir(pending.path())?;
    let next = Index {
        contract: CONTRACT.into(),
        key: key.clone(),
        generation: version,
        receipt: actual.clone(),
    };
    let pointer = pending.path().join("index.pending");
    write_new(&pointer, &next, MAX_RECEIPT)?;
    require(
        regular_identity(&params.path)? == request_source,
        "response request changed before publication",
    )?;
    if invalid_index {
        fs::rename(
            &index_path,
            key_root.join(format!(".invalid-index-{}.json", next.generation)),
        )
        .map_err(|e| e.to_string())?;
    }
    fs::rename(&pointer, &index_path).map_err(|e| e.to_string())?;
    let retained = pending.keep();
    sync_dir(&key_root)?;
    sync_dir(&root)?;
    let snapshot = Snapshot {
        root: retained.join("bundle"),
        receipt,
        receipt_identity: actual,
        service,
    };
    Ok(view(
        snapshot,
        "refresh",
        &reason,
        request_source,
        started.elapsed().as_secs_f64() * 1000.0,
    ))
}
pub(crate) fn read(params: ReadParams) -> Result<Value, String> {
    require(
        params.cache_dir.is_absolute(),
        "response cache_dir must be absolute",
    )?;
    require(
        params.lat.is_finite()
            && (-90.0..=90.0).contains(&params.lat)
            && params.lon.is_finite()
            && (-180.0..=180.0).contains(&params.lon)
            && params.offset_minutes.is_finite(),
        "cached profile requires finite bounded coordinates and offset",
    )?;
    let selected = match (params.elevation_m, params.timezone_name) {
        (None, None) => None,
        (Some(elevation_m), Some(timezone_name)) => {
            require(
                elevation_m.is_finite()
                    && (-500.0..=9000.0).contains(&elevation_m)
                    && timezone_name.len() <= 128
                    && timezone_name.parse::<chrono_tz::Tz>().is_ok(),
                "selected profile requires bounded elevation and IANA timezone",
            )?;
            Some(frames::Point {
                name: String::new(),
                lat: params.lat,
                lon: params.lon,
                elevation_m,
                timezone_name,
            })
        }
        _ => return Err("selected profile requires both elevation_m and timezone_name".into()),
    };
    directory(&params.cache_dir)?;
    let (key, version, sha) = parse_token(&params.response_key)?;
    let snapshot = load(&params.cache_dir.join(key), key, version, None, Some(sha))?;
    let result = if let Some(point) = selected {
        // A new map point must supply its own elevation/timezone, never inherit
        // those of the forecast's original observer.
        cross_section::for_point(
            snapshot.root.join("cross_section_source.json"),
            identity(&snapshot.service["provenance"]["native_profile_source"]["identity"])?,
            &point,
            params.offset_minutes,
        )?
    } else {
        ecmwf_service::read_cross_section(
            serde_json::from_value(json!({
                "manifest_path":snapshot.root.join("manifest.json"),"manifest_identity":snapshot.receipt.manifest,
                "profile_key":snapshot.service["profile_key"],"lat":params.lat,"lon":params.lon,
                "offset_minutes":params.offset_minutes,
            })).map_err(|e|e.to_string())?,
        )?
    };
    require(
        regular_identity(&snapshot.root.join("manifest.json"))? == snapshot.receipt.manifest
            && regular_identity(&snapshot.root.join("ecmwf_service.json"))?
                == snapshot.receipt.files["ecmwf_service.json"]
            && regular_identity(
                &snapshot
                    .root
                    .parent()
                    .ok_or("missing generation")?
                    .join("receipt.json"),
            )? == snapshot.receipt_identity,
        "response snapshot changed during profile reading",
    )?;
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn tokens_bind_request_generation_and_receipt_without_path_components() {
        let valid = format!(
            "ec-response:{}:{}:{}",
            "a".repeat(64),
            "A9".repeat(16),
            "b".repeat(64)
        );
        assert!(parse_token(&valid).is_ok());
        for value in [
            "ec:abc".to_owned(),
            valid.replace("A9", "../"),
            valid.to_uppercase(),
            format!("{valid}:extra"),
            valid.replace(&"b".repeat(64), &"中".repeat(64)),
        ] {
            assert!(parse_token(&value).is_err());
        }
    }
    #[test]
    fn snapshot_expiry_is_optional_and_bounded_by_generation_time() {
        let now = time_contract::parse_utc("2026-11-09T00:00:10Z").unwrap();
        assert!(!expired("2026-11-09T00:00:00Z", None, now).unwrap());
        assert!(!expired("2026-11-09T00:00:00Z", Some(11.), now).unwrap());
        assert!(expired("2026-11-09T00:00:00Z", Some(10.), now).unwrap());
        assert!(expired("2026-11-09T00:00:11Z", Some(0.), now).unwrap());
        assert!(!expired("2026-11-09T00:00:11Z", None, now).unwrap());
        assert!(expired("2026-11-09T00:10:00Z", None, now).unwrap());
    }
}
