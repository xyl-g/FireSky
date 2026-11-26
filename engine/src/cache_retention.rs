//! Native cache retention for model, satellite, report and map artifacts.
//!
//! Retention is deliberately opt-in at the request boundary.  A successful
//! download/prepare call may include a `retention` object; cleanup is then
//! performed only after the caller has verified the new inputs.  The module
//! never follows symlinks, skips an artifact while a sibling cache lock is
//! held, and reports deferred entries instead of deleting them optimistically.

use chrono::{DateTime, Duration, NaiveDateTime, Utc};
use regex::Regex;
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    env, fs,
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

const MAX_ENTRIES: usize = 1_000_000;
const MAX_RETENTION: usize = 10_000;

#[derive(Clone, Debug)]
struct Policy {
    gfs_cycles: Option<usize>,
    ecmwf_cycles: Option<usize>,
    cams_cycles: Option<usize>,
    satellite_days: Option<f64>,
    report_days: Option<f64>,
    map_days: Option<f64>,
    now: DateTime<Utc>,
}

#[derive(Default)]
struct ResultSummary {
    scanned: usize,
    removed: Vec<String>,
    deferred: Vec<String>,
    failed: Vec<String>,
    freed_bytes: u64,
}

impl ResultSummary {
    fn add_removed(&mut self, path: &Path, bytes: u64) {
        self.scanned = self.scanned.saturating_add(1);
        self.freed_bytes = self.freed_bytes.saturating_add(bytes);
        self.removed.push(path.display().to_string());
    }

    fn add_deferred(&mut self, path: &Path) {
        self.scanned = self.scanned.saturating_add(1);
        self.deferred.push(path.display().to_string());
    }

    fn add_failed(&mut self, path: &Path, error: impl std::fmt::Display) {
        self.scanned = self.scanned.saturating_add(1);
        self.failed.push(format!("{}: {error}", path.display()));
    }

    fn merge(&mut self, other: ResultSummary) {
        self.scanned = self.scanned.saturating_add(other.scanned);
        self.removed.extend(other.removed);
        self.deferred.extend(other.deferred);
        self.failed.extend(other.failed);
        self.freed_bytes = self.freed_bytes.saturating_add(other.freed_bytes);
    }

    fn json(self) -> Value {
        json!({
            "contract": "rust-cache-retention-v1",
            "runtime_python_used": false,
            "scanned": self.scanned,
            "removed": self.removed,
            "deferred": self.deferred,
            "failed": self.failed,
            "removed_count": self.removed.len(),
            "deferred_count": self.deferred.len(),
            "failed_count": self.failed.len(),
            "freed_bytes": self.freed_bytes,
            "freed_mb": self.freed_bytes as f64 / 1_048_576.0,
            "reclaimed_disk_mb": self.freed_bytes as f64 / 1_048_576.0,
        })
    }
}

fn nonnegative(value: Option<&Value>, key: &str, max: f64) -> Result<Option<f64>, String> {
    value
        .map(|value| {
            value
                .as_f64()
                .filter(|v| v.is_finite() && (0.0..=max).contains(v))
                .ok_or_else(|| format!("retention {key} must be within 0..{max}"))
        })
        .transpose()
}

fn count(value: Option<&Value>, key: &str) -> Result<Option<usize>, String> {
    value
        .map(|value| {
            let count = value
                .as_u64()
                .filter(|v| (1..=MAX_RETENTION as u64).contains(v))
                .ok_or_else(|| {
                    format!("retention {key} must be an integer in 1..{MAX_RETENTION}")
                })?;
            Ok(count as usize)
        })
        .transpose()
}

fn env_count(key: &str) -> Result<Option<usize>, String> {
    env::var(key)
        .ok()
        .map(|value| {
            value
                .parse::<usize>()
                .ok()
                .filter(|value| (1..=MAX_RETENTION).contains(value))
                .ok_or_else(|| format!("{key} must be an integer in 1..{MAX_RETENTION}"))
        })
        .transpose()
}

fn env_days(key: &str) -> Result<Option<f64>, String> {
    env::var(key)
        .ok()
        .map(|value| {
            value
                .parse::<f64>()
                .ok()
                .filter(|value| value.is_finite() && (0.0..=3650.0).contains(value))
                .ok_or_else(|| format!("{key} must be within 0..3650"))
        })
        .transpose()
}

fn policy(payload: &Value) -> Result<Policy, String> {
    let root = payload.get("retention").unwrap_or(payload);
    if !root.is_object() {
        return Err("retention must be an object".into());
    }
    let now = root
        .get("now_utc")
        .or_else(|| payload.get("now_utc"))
        .and_then(Value::as_str)
        .map(crate::time_contract::parse_utc)
        .transpose()?
        .unwrap_or_else(|| SystemTime::now().into());
    Ok(Policy {
        gfs_cycles: count(root.get("gfs_cycles"), "gfs_cycles")?
            .or(env_count("FIRESKY_GFS_RETAIN_CYCLES")?),
        ecmwf_cycles: count(root.get("ecmwf_cycles"), "ecmwf_cycles")?
            .or(env_count("FIRESKY_ECMWF_RETAIN_CYCLES")?),
        cams_cycles: count(root.get("cams_cycles"), "cams_cycles")?
            .or(env_count("FIRESKY_CAMS_RETAIN_CYCLES")?),
        satellite_days: nonnegative(root.get("satellite_days"), "satellite_days", 3650.0)?
            .or(env_days("FIRESKY_SATELLITE_RETAIN_DAYS")?),
        report_days: nonnegative(root.get("report_days"), "report_days", 3650.0)?
            .or(env_days("FIRESKY_REPORT_RETAIN_DAYS")?),
        map_days: nonnegative(root.get("map_days"), "map_days", 3650.0)?
            .or(env_days("FIRESKY_MAP_RETAIN_DAYS")?),
        now,
    })
}

fn regular(path: &Path) -> Result<bool, String> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error.to_string()),
    };
    Ok(metadata.file_type().is_file())
}

fn tree_bytes(path: &Path) -> Result<u64, String> {
    let metadata = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if metadata.file_type().is_symlink() {
        return Ok(0);
    }
    if metadata.is_file() {
        return Ok(metadata.len());
    }
    if !metadata.is_dir() {
        return Ok(0);
    }
    let mut bytes = 0u64;
    for entry in fs::read_dir(path).map_err(|e| e.to_string())? {
        bytes = bytes.saturating_add(tree_bytes(&entry.map_err(|e| e.to_string())?.path())?);
    }
    Ok(bytes)
}

pub(crate) fn walk(root: &Path) -> Result<Vec<PathBuf>, String> {
    if !root.exists() {
        return Ok(Vec::new());
    }
    if !fs::symlink_metadata(root)
        .map_err(|e| e.to_string())?
        .file_type()
        .is_dir()
    {
        return Err(format!(
            "retention root is not a directory: {}",
            root.display()
        ));
    }
    let mut pending = vec![root.to_owned()];
    let mut result = Vec::new();
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(&directory).map_err(|e| e.to_string())? {
            let entry = entry.map_err(|e| e.to_string())?;
            if result.len() >= MAX_ENTRIES {
                return Err("retention directory exceeds one million entries".into());
            }
            let metadata = entry.file_type().map_err(|e| e.to_string())?;
            if metadata.is_symlink() {
                continue;
            }
            if metadata.is_dir() {
                pending.push(entry.path());
            } else if metadata.is_file() {
                result.push(entry.path());
            }
        }
    }
    Ok(result)
}

fn lock_file_held(candidate: &Path) -> bool {
    if !candidate.exists() {
        return false;
    }
    let mut options = fs::OpenOptions::new();
    options.read(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    let Ok(file) = options.open(candidate) else {
        return true;
    };
    if file.metadata().is_ok_and(|meta| !meta.is_file()) {
        return true;
    }
    match fs2::FileExt::try_lock_exclusive(&file) {
        Ok(()) => {
            let _ = fs2::FileExt::unlock(&file);
            false
        }
        Err(error) if error.raw_os_error() == fs2::lock_contended_error().raw_os_error() => true,
        Err(_) => true,
    }
}

/// Flock-verified occupancy check for manual deletion and retention. A `.part`
/// side file is always a live download. A `.lock` file whose flock is free is
/// a normal post-download/crashed leftover: deletion may proceed, and the lock
/// file itself stays on disk — lock/lease files are never unlinked so flock
/// mutual exclusion keeps working for in-flight holders.
pub(crate) fn download_lock_held(path: &Path) -> bool {
    if PathBuf::from(format!("{}.part", path.display())).exists() {
        return true;
    }
    for candidate in [
        PathBuf::from(format!("{}.lock", path.display())),
        PathBuf::from(format!("{}.decode.lock", path.display())),
    ] {
        if lock_file_held(&candidate) {
            return true;
        }
    }
    false
}

fn prune_lock_held(path: &Path) -> bool {
    lock_file_held(&path.with_file_name(".prune.lock"))
}

fn tree_has_active_lock(path: &Path) -> bool {
    let Ok(files) = walk(path) else {
        return false;
    };
    files.iter().any(|file| {
        let name = file
            .file_name()
            .and_then(|v| v.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if name.ends_with(".part") {
            return true;
        }
        if name.ends_with(".lock") {
            return lock_file_held(file);
        }
        false
    })
}

fn remove_file(path: &Path, summary: &mut ResultSummary) {
    if download_lock_held(path) || prune_lock_held(path) {
        summary.add_deferred(path);
        return;
    }
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_file() => {
            let bytes = metadata.len();
            match fs::remove_file(path) {
                Ok(()) => summary.add_removed(path, bytes),
                Err(error) => summary.add_failed(path, error),
            }
        }
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => summary.add_failed(path, error),
    }
}

fn remove_tree(path: &Path, summary: &mut ResultSummary) {
    if download_lock_held(path) || prune_lock_held(path) || tree_has_active_lock(path) {
        summary.add_deferred(path);
        return;
    }
    let bytes = tree_bytes(path).unwrap_or(0);
    match fs::remove_dir_all(path) {
        Ok(()) => summary.add_removed(path, bytes),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => summary.add_failed(path, error),
    }
}

fn parse_cycle(path: &Path, source: &str) -> Result<Option<DateTime<Utc>>, String> {
    if source == "gfs" {
        let sidecar = path.with_extension("grib2.json");
        if regular(&sidecar)? {
            let bytes = fs::read(&sidecar).map_err(|e| e.to_string())?;
            if bytes.len() > 16 * 1024 * 1024 {
                return Err("GFS metadata sidecar exceeds size limit".into());
            }
            let value: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
            if let Some(cycle) = value.get("cycle").and_then(Value::as_str) {
                return Ok(Some(crate::time_contract::parse_utc(cycle)?));
            }
        }
        return Ok(None);
    }
    let name = path.file_name().and_then(|v| v.to_str()).unwrap_or("");
    if source == "ecmwf" {
        static EC: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
        let re = EC.get_or_init(|| Regex::new(r"^ifs-basic-v2-(\d{10})-f\d{3}\.grib2$").unwrap());
        return re
            .captures(name)
            .map(|captures| {
                NaiveDateTime::parse_from_str(&format!("{}0000", &captures[1]), "%Y%m%d%H%M%S")
                    .map(|value| value.and_utc())
                    .map_err(|e| e.to_string())
            })
            .transpose();
    }
    static CAMS: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = CAMS.get_or_init(|| {
        Regex::new(r"^(?:cams-optics-v2|geosfp-optics-v1)-(\d{4}-\d{2}-\d{2})-(\d{4})-\d{3}-")
            .unwrap()
    });
    re.captures(name)
        .map(|captures| {
            NaiveDateTime::parse_from_str(
                &format!("{}{}00", &captures[1], &captures[2]),
                "%Y-%m-%d%H%M%S",
            )
            .map(|value| value.and_utc())
            .map_err(|e| e.to_string())
        })
        .transpose()
}

pub(crate) fn related_files(path: &Path, source: &str, all: &[PathBuf]) -> Vec<PathBuf> {
    let stem = path.file_name().and_then(|v| v.to_str()).unwrap_or("");
    let prefix = stem
        .strip_suffix(".grib2")
        .or_else(|| stem.strip_suffix(".grib"))
        .or_else(|| stem.strip_suffix(".fsopt"))
        .unwrap_or(stem);
    let parent = path.parent().unwrap_or(Path::new(""));
    all.iter()
        .filter(|candidate| candidate.parent() == Some(parent))
        .filter(|candidate| {
            let name = candidate.file_name().and_then(|v| v.to_str()).unwrap_or("");
            if candidate.as_path() == path {
                return true;
            }
            if source == "gfs" && name == format!("{prefix}.grib2.json") {
                return true;
            }
            name.starts_with(prefix)
                && matches!(
                    candidate.extension().and_then(|v| v.to_str()),
                    Some("json" | "idx" | "part")
                )
        })
        .cloned()
        .collect()
}

fn model_cycles(root: &Path, source: &str, keep: usize) -> Result<ResultSummary, String> {
    let files = walk(root)?;
    let mut groups = BTreeMap::<DateTime<Utc>, Vec<PathBuf>>::new();
    let mut summary = ResultSummary::default();
    for path in &files {
        if !matches!(
            path.extension().and_then(|v| v.to_str()),
            Some("grib" | "grib2" | "fsopt")
        ) {
            continue;
        }
        match parse_cycle(path, source) {
            Ok(Some(cycle)) => groups.entry(cycle).or_default().push(path.clone()),
            Ok(None) => {}
            Err(error) => summary.add_failed(path, error),
        }
    }
    let mut cycles: Vec<_> = groups.keys().cloned().collect();
    cycles.sort_by(|a, b| b.cmp(a));
    let keep_cycles: BTreeSet<_> = cycles.into_iter().take(keep).collect();
    let mut seen = BTreeSet::new();
    for (cycle, data_files) in groups {
        if keep_cycles.contains(&cycle) {
            continue;
        }
        for data in data_files {
            for related in related_files(&data, source, &files) {
                if seen.insert(related.clone()) {
                    remove_file(&related, &mut summary);
                }
            }
        }
    }
    Ok(summary)
}

fn scan_cutoff(path: &Path, cutoff: DateTime<Utc>) -> Result<bool, String> {
    let metadata = match fs::metadata(path) {
        Ok(metadata) => metadata,
        // A concurrent retention pass may have removed the file already; a
        // vanished artifact is not a cleanup candidate rather than an error.
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error.to_string()),
    };
    let modified = metadata
        .modified()
        .map_err(|e| e.to_string())?
        .duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?;
    let stamp = DateTime::<Utc>::from_timestamp(
        modified.as_secs().min(i64::MAX as u64) as i64,
        modified.subsec_nanos(),
    )
    .ok_or("invalid filesystem modification time")?;
    Ok(stamp < cutoff)
}

fn satellite_scan(path: &Path) -> Option<DateTime<Utc>> {
    let name = path.file_name()?.to_str()?;
    let value = if name.starts_with("frame-") {
        name.strip_prefix("frame-")?.split('-').next()?
    } else {
        name
    };
    if value.len() >= 16 && value.as_bytes().get(8) == Some(&b'T') {
        if let Ok(time) = NaiveDateTime::parse_from_str(&value[..15], "%Y%m%dT%H%M%S") {
            return Some(time.and_utc());
        }
    }
    for format in ["%Y%m%dT%H%M%S%.fZ", "%Y%m%dT%H%MZ"] {
        if let Ok(time) = NaiveDateTime::parse_from_str(value, format) {
            return Some(time.and_utc());
        }
    }
    None
}

fn satellite(root: &Path, days: f64, now: DateTime<Utc>) -> Result<ResultSummary, String> {
    let cutoff = now - Duration::milliseconds((days * 86_400_000.0).round() as i64);
    let mut summary = ResultSummary::default();
    for subdir in ["raw", "compact"] {
        let root = root.join(subdir);
        if !root.is_dir() {
            continue;
        }
        for path in walk(&root)? {
            if subdir == "raw" {
                continue;
            }
            if satellite_scan(&path).is_some_and(|scan| scan < cutoff) {
                remove_file(&path, &mut summary);
            }
        }
        if subdir == "raw" {
            for entry in fs::read_dir(&root).map_err(|e| e.to_string())? {
                let entry = entry.map_err(|e| e.to_string())?;
                if !entry.file_type().map_err(|e| e.to_string())?.is_dir() {
                    continue;
                }
                if satellite_scan(&entry.path()).is_some_and(|scan| scan < cutoff) {
                    remove_tree(&entry.path(), &mut summary);
                }
            }
        }
    }
    Ok(summary)
}

fn age_cleanup(
    root: &Path,
    days: f64,
    now: DateTime<Utc>,
    preserve: &BTreeSet<PathBuf>,
) -> Result<ResultSummary, String> {
    let cutoff = now - Duration::milliseconds((days * 86_400_000.0).round() as i64);
    let mut summary = ResultSummary::default();
    for path in walk(root)? {
        if preserve.contains(&path) || !scan_cutoff(&path, cutoff)? {
            continue;
        }
        remove_file(&path, &mut summary);
    }
    Ok(summary)
}

fn referenced_maps(report_roots: &[PathBuf], map_root: &Path) -> Result<BTreeSet<PathBuf>, String> {
    let mut refs = BTreeSet::new();
    let re = Regex::new(r"map-frame-v25-([0-9a-f]{24})\.json\.gz").map_err(|e| e.to_string())?;
    for root in report_roots {
        for path in walk(root)? {
            if path.extension().and_then(|v| v.to_str()) != Some("json") {
                continue;
            }
            let bytes = match fs::read(&path) {
                Ok(bytes) if bytes.len() <= 32 * 1024 * 1024 => bytes,
                _ => continue,
            };
            let text = String::from_utf8_lossy(&bytes);
            for captures in re.captures_iter(&text) {
                refs.insert(
                    map_root
                        .join("native-map-v1")
                        .join(format!("map-frame-v25-{}.json.gz", &captures[1])),
                );
            }
        }
    }
    Ok(refs)
}

pub(crate) fn roots(workspace: &Path, key: &str, default: &str) -> PathBuf {
    let visible = match default {
        ".cache/gfs" => "gfs",
        ".cache/ecmwf" => "ecmwf",
        ".cache/cams" => "cams",
        ".cache/himawari-hsd" => "himawari-hsd",
        ".cache/map-frames" => "map-frames",
        ".cache/point-forecasts" => "point-forecasts",
        ".cache/regional-forecasts" => "regional-forecasts",
        "reports/web" => "reports",
        ".firesky/state/tasks" => "state/tasks",
        _ => default,
    };
    crate::paths::resolve(workspace, key, default, visible)
}

fn snapshot_sha(name: &str) -> Option<&str> {
    let sha = name.strip_suffix(".json")?;
    (sha.len() == 64
        && sha
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)))
    .then_some(sha)
}

/// Sweep a crashed-publish leftover that is not a snapshot, lease or live
/// `<sha>.tmp*` file. Try an exclusive flock first so any other process still
/// holding the file defers deletion instead of losing in-flight work.
fn sweep_locked_file(path: &Path, summary: &mut ResultSummary) {
    let mut options = fs::OpenOptions::new();
    options.read(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    let file = match options.open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return,
        #[cfg(unix)]
        Err(error) if error.raw_os_error() == Some(libc::ELOOP) => return,
        Err(error) => {
            summary.add_failed(path, error);
            return;
        }
    };
    match fs2::FileExt::try_lock_exclusive(&file) {
        Ok(()) => remove_file(path, summary),
        Err(error) if error.raw_os_error() == fs2::lock_contended_error().raw_os_error() => {
            summary.add_deferred(path);
        }
        Err(error) => summary.add_failed(path, error),
    }
}

/// Expire immutable result snapshots by the report retention window. Deletion is
/// deferred while a live restore holds the read lease; a crashed reader's lease
/// is reclaimed by the OS. Lease files are never deleted: unlinking one would
/// let a new lease inode be created while a reader/publisher still locks the
/// old one, breaking flock mutual exclusion. They are zero-byte and reused per
/// sha, so their count is bounded by the distinct snapshots ever published.
/// Crashed-publish temp files older than the cutoff are swept unless the
/// corresponding lease shows a live publish.
fn snapshot_cleanup(root: &Path, days: f64, now: DateTime<Utc>) -> Result<ResultSummary, String> {
    let mut summary = ResultSummary::default();
    if !root.is_dir() {
        return Ok(summary);
    }
    let cutoff = now - Duration::milliseconds((days * 86_400_000.0).round() as i64);
    for path in walk(root)? {
        let name = path.file_name().and_then(|v| v.to_str()).unwrap_or("");
        if let Some(sha) = snapshot_sha(name) {
            if !scan_cutoff(&path, cutoff)? {
                continue;
            }
            match crate::result_snapshot::exclusive_lease(root, sha)? {
                None => summary.add_deferred(&path),
                Some(_lease) => remove_file(&path, &mut summary),
            }
        } else if name.ends_with(".lease") {
            continue;
        } else if scan_cutoff(&path, cutoff)? {
            // `<sha>.tmp*` belongs to an in-flight publish while the shared
            // lease is held; only sweep it once the lease is free (crashed).
            let live_publish = name
                .split(".tmp")
                .next()
                .filter(|sha| snapshot_sha(&format!("{sha}.json")).is_some())
                .is_some_and(|sha| {
                    crate::result_snapshot::exclusive_lease(root, sha)
                        .map(|lease| lease.is_none())
                        .unwrap_or(false)
                });
            if live_publish {
                summary.add_deferred(&path);
            } else {
                sweep_locked_file(&path, &mut summary);
            }
        }
    }
    Ok(summary)
}

/// Maps referenced by surviving snapshots must outlive map cleanup. Point and
/// nowcast results carry a `map_token`; regional results embed their frames
/// inline and need no external map. Snapshots larger than the scan bound are
/// regional frame bundles, so skipping them cannot orphan a referenced map.
pub(crate) fn snapshot_map_refs(root: &Path, map_root: &Path) -> Result<BTreeSet<PathBuf>, String> {
    let mut refs = BTreeSet::new();
    if !root.is_dir() {
        return Ok(refs);
    }
    static TOKEN: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let re = TOKEN.get_or_init(|| Regex::new(r#""map_token"\s*:\s*"([0-9a-f]{24})""#).unwrap());
    for path in walk(root)? {
        if snapshot_sha(path.file_name().and_then(|v| v.to_str()).unwrap_or("")).is_none() {
            continue;
        }
        let bytes = match fs::read(&path) {
            Ok(bytes) if bytes.len() <= 64 * 1024 * 1024 => bytes,
            _ => continue,
        };
        let text = String::from_utf8_lossy(&bytes);
        for captures in re.captures_iter(&text) {
            refs.insert(
                map_root
                    .join("native-map-v1")
                    .join(format!("map-frame-v25-{}.json.gz", &captures[1])),
            );
        }
    }
    Ok(refs)
}

/// Apply explicit retention settings and permanently remove eligible artifacts.
pub fn apply(workspace: &Path, payload: &Value) -> Result<Value, String> {
    let policy = policy(payload)?;
    if [policy.gfs_cycles, policy.ecmwf_cycles, policy.cams_cycles]
        .iter()
        .all(Option::is_none)
        && policy.satellite_days.is_none()
        && policy.report_days.is_none()
        && policy.map_days.is_none()
    {
        return Err("retention policy does not configure any retention rule".into());
    }
    let mut summary = ResultSummary::default();
    if let Some(keep) = policy.gfs_cycles {
        summary.merge(model_cycles(
            &roots(workspace, "FIRESKY_GFS_CACHE_DIR", ".cache/gfs"),
            "gfs",
            keep,
        )?);
    }
    if let Some(keep) = policy.ecmwf_cycles {
        summary.merge(model_cycles(
            &roots(workspace, "FIRESKY_ECMWF_CACHE_DIR", ".cache/ecmwf"),
            "ecmwf",
            keep,
        )?);
    }
    if let Some(keep) = policy.cams_cycles {
        summary.merge(model_cycles(
            &roots(workspace, "FIRESKY_CAMS_CACHE_DIR", ".cache/cams"),
            "cams",
            keep,
        )?);
    }
    if let Some(days) = policy.satellite_days {
        summary.merge(satellite(
            &roots(workspace, "FIRESKY_HSD_CACHE_DIR", ".cache/himawari-hsd"),
            days,
            policy.now,
        )?);
    }
    let report_root = roots(workspace, "FIRESKY_REPORT_DIR", "reports/web");
    let map_root = roots(workspace, "FIRESKY_MAP_CACHE_DIR", ".cache/map-frames");
    let snapshot_root = roots(
        workspace,
        "FIRESKY_TASK_HISTORY_DIR",
        ".firesky/state/tasks",
    )
    .join("snapshots");
    let mut report_roots = vec![report_root.clone()];
    report_roots.push(roots(
        workspace,
        "FIRESKY_POINT_CACHE_DIR",
        ".cache/point-forecasts",
    ));
    report_roots.push(roots(
        workspace,
        "FIRESKY_REGIONAL_CACHE_DIR",
        ".cache/regional-forecasts",
    ));
    if let Some(days) = policy.report_days {
        summary.merge(snapshot_cleanup(&snapshot_root, days, policy.now)?);
    }
    let mut preserve = referenced_maps(&report_roots, &map_root)?;
    preserve.extend(snapshot_map_refs(&snapshot_root, &map_root)?);
    if let Some(days) = policy.report_days {
        summary.merge(age_cleanup(
            &report_root,
            days,
            policy.now,
            &BTreeSet::new(),
        )?);
    }
    if let Some(days) = policy.map_days {
        summary.merge(age_cleanup(&map_root, days, policy.now, &preserve)?);
    }
    // Always remove legacy intermediate scratch directories (derived tensors and
    // exported HSD .rust-bundles / .rust-advection) so only final results and raw source caches remain.
    for intermediate in [
        roots(workspace, "FIRESKY_GFS_CACHE_DIR", ".cache/gfs").join("derived"),
        roots(workspace, "FIRESKY_ECMWF_CACHE_DIR", ".cache/ecmwf").join("derived"),
        roots(workspace, "FIRESKY_HSD_CACHE_DIR", ".cache/himawari-hsd").join(".rust-bundles"),
        roots(workspace, "FIRESKY_HSD_CACHE_DIR", ".cache/himawari-hsd").join(".rust-advection"),
    ] {
        if intermediate.is_dir() && !tree_has_active_lock(&intermediate) {
            let bytes = tree_bytes(&intermediate).unwrap_or(0);
            if fs::remove_dir_all(&intermediate).is_ok() {
                summary.add_removed(&intermediate, bytes);
            }
        }
    }
    let mut result = summary.json();
    result["policy"] = json!({
        "gfs_cycles":policy.gfs_cycles,"ecmwf_cycles":policy.ecmwf_cycles,
        "cams_cycles":policy.cams_cycles,"satellite_days":policy.satellite_days,
        "report_days":policy.report_days,"map_days":policy.map_days,
        "now_utc":crate::time_contract::iso(policy.now),
    });
    result["roots"] = json!({
        "gfs":roots(workspace,"FIRESKY_GFS_CACHE_DIR",".cache/gfs"),
        "ecmwf":roots(workspace,"FIRESKY_ECMWF_CACHE_DIR",".cache/ecmwf"),
        "cams":roots(workspace,"FIRESKY_CAMS_CACHE_DIR",".cache/cams"),
        "satellite":roots(workspace,"FIRESKY_HSD_CACHE_DIR",".cache/himawari-hsd"),
        "reports":report_root,"maps":map_root,"snapshots":snapshot_root,
    });
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn model_retention_keeps_newest_distinct_cycles_and_sidecars() {
        let root = tempfile::tempdir().unwrap();
        let cache = root.path().join("gfs");
        let cycles = [
            ("2026-11-10T00:00:00Z", "new"),
            ("2026-11-09T00:00:00Z", "middle"),
            ("2026-11-08T00:00:00Z", "old"),
        ];
        for (cycle, suffix) in cycles {
            let file = cache.join(format!("{suffix}.grib2"));
            fs::create_dir_all(&cache).unwrap();
            fs::write(&file, b"data").unwrap();
            fs::write(
                file.with_extension("grib2.json"),
                json!({"cycle":cycle,"forecast_hour":0}).to_string(),
            )
            .unwrap();
        }
        let result = model_cycles(&cache, "gfs", 2).unwrap();
        assert_eq!(result.removed.len(), 2);
        assert!(!cache.join("old.grib2").exists());
        assert!(!cache.join("old.grib2.json").exists());
        assert!(cache.join("new.grib2").exists());
        assert!(cache.join("middle.grib2.json").exists());
    }

    #[test]
    fn locked_cycle_is_deferred_and_not_deleted() {
        let root = tempfile::tempdir().unwrap();
        let file = root.path().join("old.grib2");
        fs::write(&file, b"data").unwrap();
        fs::write(
            file.with_extension("grib2.json"),
            json!({"cycle":"2026-11-01T00:00:00Z"}).to_string(),
        )
        .unwrap();
        let newer = root.path().join("new.grib2");
        fs::write(&newer, b"data").unwrap();
        fs::write(
            newer.with_extension("grib2.json"),
            json!({"cycle":"2026-11-02T00:00:00Z"}).to_string(),
        )
        .unwrap();
        let lock_path = file.with_extension("grib2.lock");
        let lock_file = fs::OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .open(&lock_path)
            .unwrap();
        fs2::FileExt::lock_exclusive(&lock_file).unwrap();
        let result = model_cycles(root.path(), "gfs", 1).unwrap();
        assert!(result.deferred.iter().any(|v| v.ends_with("old.grib2")));
        assert!(file.exists());
        drop(lock_file);
    }

    #[test]
    fn stale_unlocked_lock_file_does_not_block_retention() {
        let root = tempfile::tempdir().unwrap();
        let file = root.path().join("old.grib2");
        fs::write(&file, b"data").unwrap();
        fs::write(
            file.with_extension("grib2.json"),
            json!({"cycle":"2026-11-01T00:00:00Z"}).to_string(),
        )
        .unwrap();
        // Post-download persistent lock file (not currently flocked) must not block pruning.
        fs::write(file.with_extension("grib2.lock"), b"").unwrap();
        let newer = root.path().join("new.grib2");
        fs::write(&newer, b"data").unwrap();
        fs::write(
            newer.with_extension("grib2.json"),
            json!({"cycle":"2026-11-02T00:00:00Z"}).to_string(),
        )
        .unwrap();
        fs::write(newer.with_extension("grib2.lock"), b"").unwrap();
        let result = model_cycles(root.path(), "gfs", 1).unwrap();
        assert!(result.deferred.is_empty());
        assert!(!file.exists());
        assert!(!file.with_extension("grib2.json").exists());
        // Lock files themselves are preserved for flock mutual exclusion.
        assert!(file.with_extension("grib2.lock").exists());
        assert!(newer.exists());
    }

    #[test]
    fn policy_requires_at_least_one_rule_and_exposes_defaults() {
        assert!(apply(Path::new("/tmp"), &json!({"retention":{}})).is_err());
        let policy = policy(&json!({
            "retention":{"gfs_cycles":5,"satellite_days":3,"now_utc":"2026-11-12T00:00:00Z"}
        }))
        .unwrap();
        assert_eq!(policy.gfs_cycles, Some(5));
        assert_eq!(policy.satellite_days, Some(3.0));
    }

    #[test]
    fn satellite_scan_accepts_raw_and_compact_names() {
        assert_eq!(
            satellite_scan(Path::new("20261111T0540Z")).unwrap(),
            crate::time_contract::parse_utc("2026-11-11T05:40:00Z").unwrap()
        );
        assert_eq!(
            satellite_scan(Path::new("frame-20261111T054000000Z-+31.0+121.0-1.0.npz")).unwrap(),
            crate::time_contract::parse_utc("2026-11-11T05:40:00Z").unwrap()
        );
    }

    #[test]
    fn map_references_are_preserved() {
        let root = tempfile::tempdir().unwrap();
        let reports = root.path().join("reports");
        let maps = root.path().join("maps").join("native-map-v1");
        fs::create_dir_all(&maps).unwrap();
        let token = "a".repeat(24);
        let map = maps.join(format!("map-frame-v25-{token}.json.gz"));
        fs::write(&map, b"map").unwrap();
        fs::create_dir_all(&reports).unwrap();
        let mut file = fs::File::create(reports.join("result.json")).unwrap();
        writeln!(file, "map-frame-v25-{token}.json.gz").unwrap();
        let refs = referenced_maps(&[reports], maps.parent().unwrap()).unwrap();
        assert!(refs.contains(&map));
    }

    fn future() -> DateTime<Utc> {
        crate::time_contract::parse_utc("2030-01-01T00:00:00Z").unwrap()
    }

    #[test]
    fn snapshot_cleanup_expires_old_snapshots_and_sweeps_crashed_temp_files() {
        let dir = tempfile::tempdir().unwrap();
        let first = crate::result_snapshot::publish(dir.path(), &json!({"a":1})).unwrap();
        let second = crate::result_snapshot::publish(dir.path(), &json!({"b":2})).unwrap();
        let orphan_lease = dir.path().join(format!("{}.lease", "c".repeat(64)));
        fs::write(&orphan_lease, b"").unwrap();
        let garbage = dir.path().join(".tmp-crashed-publish");
        fs::write(&garbage, b"partial").unwrap();
        let summary = snapshot_cleanup(dir.path(), 1.0, future()).unwrap();
        assert_eq!(summary.removed.len(), 3);
        for identity in [&first, &second] {
            let sha = identity["sha256"].as_str().unwrap();
            assert!(!dir.path().join(format!("{sha}.json")).exists());
            // Lease files are never unlinked: a stable inode keeps flock
            // mutual exclusion valid for readers and publishers.
            assert!(dir.path().join(format!("{sha}.lease")).exists());
        }
        assert!(orphan_lease.exists());
        assert!(!garbage.exists());
    }

    #[test]
    fn snapshot_cleanup_defers_while_a_lease_is_held() {
        let dir = tempfile::tempdir().unwrap();
        let identity = crate::result_snapshot::publish(dir.path(), &json!({"a":1})).unwrap();
        let sha = identity["sha256"].as_str().unwrap();
        let path = dir.path().join(format!("{sha}.json"));
        let held = crate::result_snapshot::exclusive_lease(dir.path(), sha)
            .unwrap()
            .expect("lease is free");
        let summary = snapshot_cleanup(dir.path(), 1.0, future()).unwrap();
        assert_eq!(summary.deferred.len(), 1);
        assert!(path.exists());
        drop(held);
        let summary = snapshot_cleanup(dir.path(), 1.0, future()).unwrap();
        assert_eq!(summary.removed.len(), 1);
        assert!(!path.exists());
    }

    #[test]
    fn snapshot_cleanup_defers_live_publish_temp_and_keeps_orphan_lease() {
        let dir = tempfile::tempdir().unwrap();
        let sha = "e".repeat(64);
        let temp = dir.path().join(format!("{sha}.tmp-live"));
        fs::write(&temp, b"partial").unwrap();
        let _lease = crate::result_snapshot::shared_lease(dir.path(), &sha).unwrap();
        let orphan = dir.path().join(format!("{}.lease", "d".repeat(64)));
        fs::write(&orphan, b"").unwrap();
        let summary = snapshot_cleanup(dir.path(), 1.0, future()).unwrap();
        assert_eq!(summary.deferred.len(), 1);
        assert!(temp.exists());
        assert!(orphan.exists());
        drop(_lease);
        let summary = snapshot_cleanup(dir.path(), 1.0, future()).unwrap();
        assert_eq!(summary.removed.len(), 1);
        assert!(!temp.exists());
        assert!(orphan.exists());
    }

    #[test]
    fn snapshot_cleanup_keeps_recent_snapshots_and_their_leases() {
        let dir = tempfile::tempdir().unwrap();
        let identity = crate::result_snapshot::publish(dir.path(), &json!({"a":1})).unwrap();
        let sha = identity["sha256"].as_str().unwrap();
        let lease = dir.path().join(format!("{sha}.lease"));
        fs::write(&lease, b"").unwrap();
        let summary = snapshot_cleanup(dir.path(), 3650.0, future()).unwrap();
        assert!(summary.removed.is_empty());
        assert!(dir.path().join(format!("{sha}.json")).exists());
        assert!(lease.exists());
    }

    #[test]
    fn apply_preserves_maps_referenced_by_surviving_snapshots_then_releases() {
        let workspace = tempfile::tempdir().unwrap();
        let snapshot_root = workspace.path().join(".firesky/state/tasks/snapshots");
        let map_root = workspace.path().join(".cache/map-frames");
        let token = "a".repeat(24);
        let map = map_root
            .join("native-map-v1")
            .join(format!("map-frame-v25-{token}.json.gz"));
        fs::create_dir_all(map.parent().unwrap()).unwrap();
        fs::write(&map, b"map").unwrap();
        let identity = crate::result_snapshot::publish(
            &snapshot_root,
            &json!({"map_token":token,"model_results":{}}),
        )
        .unwrap();
        let snapshot = snapshot_root.join(format!("{}.json", identity["sha256"].as_str().unwrap()));
        // Snapshot survives (long report window); the old map is kept by its ref.
        let kept = apply(
            workspace.path(),
            &json!({"retention":{"report_days":3650,"map_days":1,"now_utc":"2030-01-01T00:00:00Z"}}),
        )
        .unwrap();
        assert!(map.exists());
        assert!(snapshot.exists());
        assert_eq!(kept["roots"]["snapshots"], json!(snapshot_root));
        // Once the snapshot expires, the shared map is released and removed.
        let released = apply(
            workspace.path(),
            &json!({"retention":{"report_days":1,"map_days":1,"now_utc":"2030-01-01T00:00:00Z"}}),
        )
        .unwrap();
        assert!(!map.exists());
        assert!(!snapshot.exists());
        assert!(released["removed"]
            .as_array()
            .unwrap()
            .iter()
            .any(|v| v.as_str().unwrap().ends_with(".json")));
    }

    #[test]
    fn ecmwf_retention_deletes_old_cycles_with_sidecars_and_unlocked_locks() {
        let root = tempfile::tempdir().unwrap();
        let cache = root.path().join("ecmwf");
        fs::create_dir_all(&cache).unwrap();
        let old = cache.join("ifs-basic-v2-2026112100-f009.grib2");
        let new = cache.join("ifs-basic-v2-2026112200-f009.grib2");
        for path in [&old, &new] {
            fs::write(path, b"grib2").unwrap();
            fs::write(path.with_extension("grib2.json"), b"{}").unwrap();
            fs::write(path.with_extension("grib2.lock"), b"").unwrap();
        }
        let result = model_cycles(&cache, "ecmwf", 1).unwrap();
        assert!(result.failed.is_empty(), "unexpected failures: {:?}", result.failed);
        assert!(result.deferred.is_empty());
        assert_eq!(result.removed.len(), 2);
        assert!(!old.exists());
        assert!(!old.with_extension("grib2.json").exists());
        assert!(old.with_extension("grib2.lock").exists());
        assert!(new.exists());
        assert!(new.with_extension("grib2.json").exists());
    }

    #[test]
    fn cams_retention_deletes_old_cycles_without_failures() {
        let root = tempfile::tempdir().unwrap();
        let cache = root.path().join("cams");
        fs::create_dir_all(&cache).unwrap();
        let old = cache.join("cams-optics-v2-2026-11-21-1200-084-1c0e9c4c8ed5.grib");
        let new = cache.join("cams-optics-v2-2026-11-22-0000-084-1c0e9c4c8ed5.grib");
        let geos_old = cache.join("geosfp-optics-v1-2026-11-21-0000-003-1c0e9c4c8ed5.fsopt");
        fs::write(&old, b"old-grib").unwrap();
        fs::write(&new, b"new-grib").unwrap();
        fs::write(&geos_old, b"old-fsopt").unwrap();
        let result = model_cycles(&cache, "cams", 1).unwrap();
        assert!(result.failed.is_empty(), "unexpected failures: {:?}", result.failed);
        assert!(result.deferred.is_empty());
        assert_eq!(result.removed.len(), 2);
        assert!(!old.exists());
        assert!(!geos_old.exists());
        assert!(new.exists());
    }

    #[test]
    fn apply_removes_legacy_intermediate_directories() {
        let workspace = tempfile::tempdir().unwrap();
        let gfs_derived = workspace.path().join(".cache/gfs/derived");
        let ecmwf_derived = workspace.path().join(".cache/ecmwf/derived");
        let hsd_bundles = workspace.path().join(".cache/himawari-hsd/.rust-bundles");
        let hsd_advection = workspace.path().join(".cache/himawari-hsd/.rust-advection");
        for (dir, payload) in [
            (&gfs_derived, b"gfs-tensor".as_slice()),
            (&ecmwf_derived, b"ecmwf-tensor".as_slice()),
            (&hsd_bundles, b"hsd-bundle".as_slice()),
            (&hsd_advection, b"hsd-advection".as_slice()),
        ] {
            fs::create_dir_all(dir).unwrap();
            fs::write(dir.join("artifact.bin"), payload).unwrap();
        }
        let result = apply(
            workspace.path(),
            &json!({"retention":{"report_days":30,"now_utc":"2030-01-01T00:00:00Z"}}),
        )
        .unwrap();
        assert!(!gfs_derived.exists());
        assert!(!ecmwf_derived.exists());
        assert!(!hsd_bundles.exists());
        assert!(!hsd_advection.exists());
        assert!(result["freed_bytes"].as_u64().unwrap() > 0);
        let removed = result["removed"].as_array().unwrap();
        assert!(removed.iter().any(|v| v.as_str().unwrap().ends_with("gfs/derived")));
        assert!(removed.iter().any(|v| v.as_str().unwrap().ends_with(".rust-bundles")));
        assert!(removed.iter().any(|v| v.as_str().unwrap().ends_with(".rust-advection")));
    }
}
