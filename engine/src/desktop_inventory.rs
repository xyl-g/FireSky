//! Read-only desktop cache inventory. Counts do not prove forecast readiness.
use crate::{grib::EcCodes, request_file};
use chrono::{DateTime, NaiveDate, NaiveDateTime};
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    fs,
    path::{Component, Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex, OnceLock,
    },
    time::UNIX_EPOCH,
};
const MIB: f64 = 1024.0 * 1024.0;
const MAX_ENTRIES: usize = 1_000_000;
const MAX_DEPTH: usize = 128;
const MAX_SIDECAR: u64 = 16 * 1024 * 1024;

#[derive(Clone, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Config {
    pub(crate) gfs_cache_dir: PathBuf,
    pub(crate) ecmwf_cache_dir: PathBuf,
    pub(crate) cams_cache_dir: PathBuf,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            gfs_cache_dir: ".cache/gfs".into(),
            ecmwf_cache_dir: ".cache/ecmwf".into(),
            cams_cache_dir: ".cache/cams".into(),
        }
    }
}
impl Config {
    pub(crate) fn validate(&self) -> Result<(), String> {
        if [
            &self.gfs_cache_dir,
            &self.ecmwf_cache_dir,
            &self.cams_cache_dir,
        ]
        .iter()
        .any(|p| p.as_os_str().is_empty() || p.to_str().is_none_or(|s| s.len() > 4096))
        {
            return Err("inventory cache roots require nonempty UTF-8 paths".into());
        }
        Ok(())
    }
}
#[derive(Clone, Copy)]
enum Source {
    Gfs,
    Ecmwf,
    Cams,
}
#[derive(Debug)]
struct Entry {
    path: PathBuf,
    bytes: u64,
    modified_ns: Option<u128>,
    grib2: bool,
}
#[derive(Default, Serialize)]
struct Cycle {
    hours: BTreeSet<i64>,
    files: usize,
    mb: f64,
    label: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    coverage: Option<BTreeMap<i64, Vec<Option<Vec<f64>>>>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    paths: Option<BTreeMap<i64, String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    area_keys: Option<BTreeMap<i64, BTreeSet<String>>>,
}
fn check(cancel: &AtomicBool) -> Result<(), String> {
    if cancel.load(Ordering::Acquire) {
        Err("inventory operation cancelled".into())
    } else {
        Ok(())
    }
}
fn directory(
    root: &Path,
    depth: usize,
    visited: &mut usize,
    output: &mut Vec<Entry>,
    cancel: &AtomicBool,
) -> Result<Vec<PathBuf>, String> {
    check(cancel)?;
    if depth > MAX_DEPTH {
        return Err("inventory directory depth exceeds 128".into());
    }
    let entries = match fs::read_dir(root) {
        Ok(entries) => entries,
        Err(error)
            if matches!(
                error.kind(),
                std::io::ErrorKind::NotFound
                    | std::io::ErrorKind::PermissionDenied
                    | std::io::ErrorKind::NotADirectory
            ) =>
        {
            return Ok(Vec::new())
        }
        Err(error) => return Err(error.to_string()),
    };
    let mut directories = Vec::new();
    for entry in entries {
        check(cancel)?;
        let entry = entry.map_err(|e| e.to_string())?;
        *visited += 1;
        if *visited > MAX_ENTRIES {
            return Err("inventory exceeds one million directory entries".into());
        }
        let kind = entry.file_type().map_err(|e| e.to_string())?;
        let path = entry.path();
        if kind.is_dir() {
            if matches!(
                path.extension().and_then(|s| s.to_str()),
                Some("grib" | "grib2")
            ) {
                return Err("inventory GRIB entries must resolve to regular files".into());
            }
            directories.push(path);
            continue;
        }
        let suffix = path.extension().and_then(|s| s.to_str());
        if !matches!(suffix, Some("grib" | "grib2")) {
            continue;
        }
        let grib2 = suffix == Some("grib2");
        let metadata = fs::metadata(&path).map_err(|e| e.to_string())?;
        if !metadata.is_file() {
            return Err("inventory GRIB entries must resolve to regular files".into());
        }
        output.push(Entry {
            path,
            bytes: metadata.len(),
            modified_ns: metadata
                .modified()
                .ok()
                .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                .map(|v| v.as_nanos()),
            grib2,
        });
    }
    Ok(directories)
}
// Python 3.15 rglob yields each sibling directory's direct matches before
// descending via a LIFO directory stack. Retain duplicate-lead last-path order.
fn walk(root: &Path, output: &mut Vec<Entry>, cancel: &AtomicBool) -> Result<(), String> {
    let mut pending = vec![(vec![root.to_owned()], 0)];
    let mut visited = 0;
    while let Some((siblings, depth)) = pending.pop() {
        for sibling in siblings {
            let children = directory(&sibling, depth, &mut visited, output, cancel)?;
            if !children.is_empty() {
                pending.push((children, depth + 1));
            }
        }
    }
    Ok(())
}
fn resolved(path: &Path) -> Result<PathBuf, String> {
    let path = std::path::absolute(path).map_err(|e| e.to_string())?;
    let mut resolved = PathBuf::new();
    for part in path.components() {
        match part {
            Component::ParentDir => {
                resolved.pop();
            }
            Component::CurDir => {}
            _ => {
                resolved.push(part);
                match resolved.canonicalize() {
                    Ok(canonical) => resolved = canonical,
                    Err(error)
                        if matches!(
                            error.kind(),
                            std::io::ErrorKind::NotFound | std::io::ErrorKind::NotADirectory
                        ) => {}
                    Err(error) => return Err(error.to_string()),
                }
            }
        }
    }
    Ok(resolved)
}
fn label(cycle: &str) -> String {
    if let Ok(date) = crate::time_contract::parse_zoned(cycle) {
        return date.format("%Y-%m-%d %HZ").to_string();
    }
    let value = cycle.replace('Z', "+00:00");
    if let Ok(date) = DateTime::parse_from_rfc3339(&value) {
        return date.format("%Y-%m-%d %HZ").to_string();
    }
    for format in [
        "%Y-%m-%dT%H:%M:%S%.f",
        "%Y-%m-%d %H:%M:%S%.f",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y%m%dT%H%M%S",
    ] {
        if let Ok(date) = NaiveDateTime::parse_from_str(cycle, format) {
            return date.format("%Y-%m-%d %HZ").to_string();
        }
    }
    for format in ["%Y-%m-%d", "%Y%m%d"] {
        if let Ok(date) = NaiveDate::parse_from_str(cycle, format) {
            return date.format("%Y-%m-%d").to_string() + " 00Z";
        }
    }
    cycle.into()
}
fn integer(value: &Value) -> Option<i64> {
    value
        .as_i64()
        .or_else(|| value.as_bool().map(i64::from))
        .or_else(|| value.as_str().and_then(|s| s.trim().parse().ok()))
        .or_else(|| {
            value
                .as_f64()
                .filter(|v| v.is_finite() && *v >= i64::MIN as f64 && *v < i64::MAX as f64)
                .map(|v| v as i64)
        })
}
fn number(value: &Value) -> Option<f64> {
    value
        .as_f64()
        .or_else(|| value.as_bool().map(|b| if b { 1.0 } else { 0.0 }))
        .or_else(|| value.as_str().and_then(|s| s.parse().ok()))
        .filter(|v| v.is_finite())
}
fn truth(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(v) => *v,
        Value::String(v) => !v.is_empty(),
        Value::Array(v) => !v.is_empty(),
        Value::Object(v) => !v.is_empty(),
        Value::Number(v) => v.as_f64() != Some(0.0),
    }
}
fn gfs_metadata(path: &Path) -> Option<(String, i64, Option<Vec<f64>>)> {
    let (meta, _): (Value, _) =
        request_file::read(&path.with_extension("grib2.json"), MAX_SIDECAR).ok()?;
    let key = meta.get("cycle")?.as_str()?.to_owned();
    if key.is_empty() || key.len() > 256 {
        return None;
    }
    let hour = match meta.get("forecast_hour") {
        Some(v) => integer(v)?,
        None => -1,
    };
    let bbox = match meta.get("bbox") {
        Some(value) => Some(
            value
                .as_array()?
                .iter()
                .map(number)
                .collect::<Option<Vec<_>>>()?,
        ),
        None => None,
    };
    Some((key, hour, if truth(&meta["global"]) { None } else { bbox }))
}
#[derive(Clone, PartialEq)]
struct AreaKey {
    path: PathBuf,
    bytes: u64,
    modified_ns: u128,
}
type AreaMemo = VecDeque<(AreaKey, [f64; 4])>;
#[derive(Default)]
pub(crate) struct AreaReader {
    sidecars: Option<(PathBuf, Value)>,
    decoder: Option<Result<EcCodes, String>>,
}
impl AreaReader {
    pub(crate) fn read(&mut self, path: &Path, cancel: &AtomicBool) -> Option<[f64; 4]> {
        let metadata = fs::metadata(path).ok()?;
        if !metadata.is_file() {
            return None;
        }
        let entry = Entry {
            path: path.into(),
            bytes: metadata.len(),
            modified_ns: metadata
                .modified()
                .ok()?
                .duration_since(UNIX_EPOCH)
                .ok()
                .map(|v| v.as_nanos()),
            grib2: false,
        };
        area(&entry, &mut self.sidecars, &mut self.decoder, cancel)
    }
}
fn area(
    entry: &Entry,
    sidecars: &mut Option<(PathBuf, Value)>,
    decoder: &mut Option<Result<EcCodes, String>>,
    cancel: &AtomicBool,
) -> Option<[f64; 4]> {
    check(cancel).ok()?;
    let key = AreaKey {
        path: entry.path.canonicalize().ok()?,
        bytes: entry.bytes,
        modified_ns: entry.modified_ns?,
    };
    static MEMO: OnceLock<Mutex<AreaMemo>> = OnceLock::new();
    let memo = MEMO.get_or_init(|| Mutex::new(VecDeque::new()));
    {
        let mut entries = memo.lock().ok()?;
        if let Some(i) = entries.iter().position(|(k, _)| k == &key) {
            let cached = entries.remove(i)?;
            let value = cached.1;
            entries.push_back(cached);
            return Some(value);
        }
    }
    let parent = entry.path.parent()?;
    if sidecars
        .as_ref()
        .is_none_or(|(directory, _)| directory != parent)
    {
        *sidecars = Some((
            parent.into(),
            request_file::read::<Value>(&parent.join(".cams-area-index-v1.json"), MAX_SIDECAR)
                .map(|(v, _)| v)
                .unwrap_or(Value::Null),
        ));
    }
    let sidecar = &sidecars.as_ref()?.1;
    let cached = (|| {
        let item = sidecar.get(entry.path.file_name()?.to_str()?)?;
        if item["m"].as_u64().map(u128::from) != Some(key.modified_ns)
            || item["s"].as_u64() != Some(entry.bytes)
        {
            return None;
        }
        let values = item["a"].as_array()?;
        if values.len() != 4 {
            return None;
        }
        Some([
            number(&values[0])?,
            number(&values[1])?,
            number(&values[2])?,
            number(&values[3])?,
        ])
    })();
    let value = cached.or_else(|| {
        decoder
            .get_or_insert_with(EcCodes::load)
            .as_ref()
            .ok()?
            .first_area(&entry.path)
            .ok()
    })?;
    check(cancel).ok()?;
    let after = fs::metadata(&entry.path).ok()?;
    if after.len() != key.bytes
        || after
            .modified()
            .ok()?
            .duration_since(UNIX_EPOCH)
            .ok()?
            .as_nanos()
            != key.modified_ns
    {
        return None;
    }
    let mut entries = memo.lock().ok()?;
    entries.retain(|(k, _)| k.path != key.path);
    entries.push_back((key, value));
    while entries.len() > 4096 {
        entries.pop_front();
    }
    Some(value)
}
fn scan(source: Source, root: &Path, cancel: &AtomicBool) -> Result<Value, String> {
    let mut files = Vec::new();
    walk(root, &mut files, cancel)?;
    aggregate(source, root, &files, true, cancel)
}
pub(crate) fn local_snapshot(
    source: &str,
    root: &Path,
    cancel: &AtomicBool,
) -> Result<(Value, Vec<PathBuf>), String> {
    let kind = match source {
        "gfs" => Source::Gfs,
        "ecmwf" => Source::Ecmwf,
        "cams" => Source::Cams,
        _ => return Err("unknown desktop inventory source".into()),
    };
    let mut files = Vec::new();
    walk(root, &mut files, cancel)?;
    let value = aggregate(kind, root, &files, false, cancel)?;
    Ok((value, files.into_iter().map(|entry| entry.path).collect()))
}

/// Cycle→GRIB-file groups keyed exactly like the inventory `cycles` object so
/// UI delete scopes can reference displayed cycle keys. Files the inventory
/// cannot index (missing GFS sidecar) group under "" and are only addressable
/// through whole-source deletion.
pub(crate) fn cycle_files(
    source: &str,
    root: &Path,
    cancel: &AtomicBool,
) -> Result<BTreeMap<String, Vec<PathBuf>>, String> {
    let kind = match source {
        "gfs" => Source::Gfs,
        "ecmwf" => Source::Ecmwf,
        "cams" => Source::Cams,
        _ => return Err("unknown desktop inventory source".into()),
    };
    let mut files = Vec::new();
    walk(root, &mut files, cancel)?;
    static EC_KEY: OnceLock<Regex> = OnceLock::new();
    static CAMS_KEY: OnceLock<Regex> = OnceLock::new();
    let ec = EC_KEY
        .get_or_init(|| Regex::new(r"^ifs-basic-v2-([0-9]{10})-f([0-9]{3})\.grib2$").unwrap());
    let cams = CAMS_KEY.get_or_init(||Regex::new(
        r"^cams-optics-v2-([0-9]{4})-([0-9]{2})-([0-9]{2})-([0-9]{4})-([0-9]{3})-([a-z0-9]+)\.grib$").unwrap());
    let mut groups = BTreeMap::<String, Vec<PathBuf>>::new();
    for entry in files {
        let name = entry
            .path
            .file_name()
            .and_then(|v| v.to_str())
            .ok_or("inventory file path is not UTF-8")?;
        let key = match kind {
            Source::Gfs => gfs_metadata(&entry.path).map(|(key, _, _)| key),
            Source::Ecmwf => ec.captures(name).map(|m| m[1].to_owned()),
            Source::Cams => cams
                .captures(name)
                .map(|m| format!("{}-{}-{} {}", &m[1], &m[2], &m[3], &m[4])),
        };
        groups
            .entry(key.unwrap_or_default())
            .or_default()
            .push(entry.path);
    }
    Ok(groups)
}
fn aggregate(
    source: Source,
    root: &Path,
    files: &[Entry],
    include_areas: bool,
    cancel: &AtomicBool,
) -> Result<Value, String> {
    let total_bytes = files
        .iter()
        .try_fold(0u64, |sum, v| sum.checked_add(v.bytes))
        .ok_or("inventory size overflow")?;
    let mut cycles: BTreeMap<String, Cycle> = BTreeMap::new();
    let (mut indexed_files, mut indexed_mb) = (0usize, 0.0);
    let mut area_bounds = BTreeMap::<String, Option<[f64; 4]>>::new();
    let mut sidecars = None;
    let mut decoder = None;
    static EC: OnceLock<Regex> = OnceLock::new();
    static CAMS: OnceLock<Regex> = OnceLock::new();
    let ec =
        EC.get_or_init(|| Regex::new(r"^ifs-basic-v2-([0-9]{10})-f([0-9]{3})\.grib2$").unwrap());
    let cams = CAMS.get_or_init(||Regex::new(
        r"^cams-optics-v2-([0-9]{4})-([0-9]{2})-([0-9]{2})-([0-9]{4})-([0-9]{3})-([a-z0-9]+)\.grib$").unwrap());
    for entry in files {
        check(cancel)?;
        let name = entry
            .path
            .file_name()
            .and_then(|v| v.to_str())
            .ok_or("inventory file path is not UTF-8")?;
        let mb = entry.bytes as f64 / MIB;
        let mut bbox = None;
        let mut area_key = None;
        let parsed = match source {
            Source::Gfs if entry.grib2 && entry.bytes > 0 => {
                indexed_files += 1;
                indexed_mb += mb;
                gfs_metadata(&entry.path).map(|(k, h, b)| {
                    bbox = b;
                    let label = label(&k);
                    (k, h, label)
                })
            }
            Source::Ecmwf if entry.grib2 && entry.bytes > 0 => ec.captures(name).map(|m| {
                indexed_files += 1;
                indexed_mb += mb;
                let k = m[1].to_owned();
                let label = format!("{}-{}-{} {}Z", &k[..4], &k[4..6], &k[6..8], &k[8..10]);
                (k, m[2].parse::<i64>().unwrap(), label)
            }),
            Source::Cams if !entry.grib2 => {
                cams.captures(name).and_then(|m| {
                    let key = m[6].to_owned();
                    // Inventory includes an area result even for an empty matching file.
                    if include_areas && !area_bounds.contains_key(&key) {
                        area_bounds.insert(
                            key.clone(),
                            area(entry, &mut sidecars, &mut decoder, cancel),
                        );
                    }
                    if entry.bytes == 0 {
                        return None;
                    }
                    indexed_files += 1;
                    indexed_mb += mb;
                    area_key = Some(key);
                    Some((
                        format!("{}-{}-{} {}", &m[1], &m[2], &m[3], &m[4]),
                        m[5].parse::<i64>().unwrap(),
                        format!("{}-{}-{} {}Z", &m[1], &m[2], &m[3], &m[4][..2]),
                    ))
                })
            }
            _ => None,
        };
        if let Some((key, hour, label)) = parsed {
            let cycle = cycles.entry(key).or_insert_with(|| Cycle {
                label,
                ..Default::default()
            });
            cycle.hours.insert(hour);
            cycle.files += 1;
            cycle.mb += mb;
            match source {
                Source::Gfs => {
                    cycle
                        .coverage
                        .get_or_insert_with(BTreeMap::new)
                        .entry(hour)
                        .or_default()
                        .push(bbox);
                }
                Source::Ecmwf => {
                    cycle.paths.get_or_insert_with(BTreeMap::new).insert(
                        hour,
                        entry
                            .path
                            .components()
                            .filter(|v| !matches!(v, Component::CurDir))
                            .collect::<PathBuf>()
                            .to_str()
                            .ok_or("inventory path is not UTF-8")?
                            .into(),
                    );
                }
                Source::Cams => {
                    cycle
                        .area_keys
                        .get_or_insert_with(BTreeMap::new)
                        .entry(hour)
                        .or_default()
                        .insert(area_key.unwrap());
                }
            }
        }
    }
    check(cancel)?;
    let grouped_count: usize = cycles.values().map(|c| c.files).sum();
    let latest = cycles
        .last_key_value()
        .map(|(_, c)| c.label.as_str())
        .unwrap_or("");
    let source = match source {
        Source::Gfs => "gfs",
        Source::Ecmwf => "ecmwf",
        Source::Cams => "cams",
    };
    // Decimal formatting preserves Python's round(x, 2) tie behavior.
    let rounded = format!("{indexed_mb:.2}")
        .parse::<f64>()
        .map_err(|e| e.to_string())?;
    Ok(
        json!({"source":source,"path":resolved(root)?,"total_file_count":files.len(),
        "total_storage_mb":total_bytes as f64/MIB,
        "unindexed_file_count":files.len().saturating_sub(grouped_count),
        "area_bounds":area_bounds,"file_count":indexed_files,"total_size_mb":rounded,
        "latest_cycle":latest,"cycles":cycles}),
    )
}
pub(crate) fn execute(config: &Config, cancel: Arc<AtomicBool>) -> Result<Value, String> {
    config.validate()?;
    let datasets = [
        (Source::Gfs, &config.gfs_cache_dir),
        (Source::Ecmwf, &config.ecmwf_cache_dir),
        (Source::Cams, &config.cams_cache_dir),
    ]
    .into_iter()
    .map(|(source, root)| scan(source, root, &cancel))
    .collect::<Result<Vec<_>, _>>()?;
    Ok(json!({"datasets":datasets}))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn entry_bound_fails_before_growing_inventory() {
        let root = tempfile::tempdir().unwrap();
        fs::write(root.path().join("one.grib2"), b"unowned").unwrap();
        let mut visited = MAX_ENTRIES;
        let mut entries = Vec::new();
        let error = directory(
            root.path(),
            0,
            &mut visited,
            &mut entries,
            &AtomicBool::new(false),
        )
        .unwrap_err();
        assert!(error.contains("one million"));
        assert!(entries.is_empty());
        assert_eq!(fs::read(root.path().join("one.grib2")).unwrap(), b"unowned");
    }
    #[test]
    fn cancelled_inventory_does_not_visit_roots() {
        let root = tempfile::tempdir().unwrap();
        let config = Config {
            gfs_cache_dir: root.path().join("missing"),
            ..Default::default()
        };
        assert!(execute(&config, Arc::new(AtomicBool::new(true)))
            .unwrap_err()
            .contains("cancelled"));
        assert!(!config.gfs_cache_dir.exists());
    }
}
