//! GFS NOMADS requests, shared Python cache metadata, and checked downloads.
use crate::{cache_lock::Lock, request_file, time_contract};
use chrono::{DateTime, Utc};
use reqwest::{blocking::Client, Url};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::VecDeque;
use std::fs::{self, File, Metadata};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Component, Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
#[cfg(not(unix))]
use std::time::UNIX_EPOCH;
use std::time::{Duration, Instant};

const INDEX_FORMAT: &str = "firesky-gfs-metadata-index-v1";
const INDEX_DIR: &str = ".gfs-metadata-index-v1";
const MAX_METADATA_BYTES: u64 = 256 * 1024;
const MAX_INDEX_BYTES: u64 = 64 * 1024 * 1024;
const MAX_ENTRIES: usize = 100_000;
const MAX_DOWNLOAD: u64 = 512 * 1024 * 1024;
const VARIABLES: &[&str] = &[
    "RH", "HGT", "TMP", "VVEL", "UGRD", "VGRD", "TCDC", "HCDC", "MCDC", "LCDC", "CLWMR", "ICMR",
    "DPT", "PRES", "VIS", "PRATE",
];

fn resolution() -> String {
    "0p25".into()
}
fn timeout() -> f64 {
    90.0
}
fn lock_timeout() -> f64 {
    300.0
}
fn attempts() -> u32 {
    4
}
fn yes() -> bool {
    true
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum Operation {
    Url,
    Find,
    Resolve,
    Download,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    operation: Operation,
    cache_dir: Option<PathBuf>,
    cycle_utc: Option<String>,
    forecast_hour: Option<u32>,
    forecast_hours: Option<Vec<u32>>,
    bbox: Option<[f64; 4]>,
    #[serde(default = "resolution")]
    resolution: String,
    region: Option<String>,
    #[serde(default)]
    include_diagnostic_fields: bool,
    url: Option<String>,
    #[serde(default = "yes")]
    allow_download: bool,
    #[serde(default = "timeout")]
    timeout_seconds: f64,
    #[serde(default = "lock_timeout")]
    lock_timeout_seconds: f64,
    #[serde(default = "attempts")]
    download_attempts: u32,
}

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub cache_dir: PathBuf,
    #[serde(default = "resolution")]
    pub resolution: String,
    pub region: Option<String>,
    #[serde(default = "timeout")]
    pub timeout_seconds: f64,
    #[serde(default = "lock_timeout")]
    pub lock_timeout_seconds: f64,
    #[serde(default = "attempts")]
    pub download_attempts: u32,
}

impl Config {
    fn validate(&self) -> Result<(), String> {
        grid_spacing(&self.resolution)?;
        if self.cache_dir.as_os_str().is_empty()
            || !self.timeout_seconds.is_finite()
            || !(0.001..=3600.0).contains(&self.timeout_seconds)
            || !self.lock_timeout_seconds.is_finite()
            || !(0.0..=3600.0).contains(&self.lock_timeout_seconds)
            || !(1..=32).contains(&self.download_attempts)
        {
            return Err("invalid bounded GFS client configuration".into());
        }
        if let Some(region) = &self.region {
            if region.is_empty()
                || region.len() > 256
                || region == "."
                || region == ".."
                || region.contains(['/', '\\', '\0'])
            {
                return Err("GFS region must be one nonempty path component".into());
            }
        }
        Ok(())
    }
}

fn grid_spacing(resolution: &str) -> Result<f64, String> {
    match resolution {
        "0p25" => Ok(0.25),
        "0p50" => Ok(0.5),
        _ => Err("GFS resolution must be 0p25 or 0p50".into()),
    }
}

pub fn canonical_bbox(bbox: [f64; 4], resolution: &str) -> Result<[f64; 4], String> {
    let [west, east, north, south] = bbox;
    if bbox.iter().any(|v| !v.is_finite())
        || !(-180.0..=180.0).contains(&west)
        || !(-180.0..=180.0).contains(&east)
        || !(-90.0..=90.0).contains(&south)
        || !(-90.0..=90.0).contains(&north)
        || south > north
    {
        return Err("invalid GFS bbox".into());
    }
    let step = grid_spacing(resolution)?;
    Ok([
        (-180.0_f64).max((west / step).floor() * step),
        180.0_f64.min((east / step).ceil() * step),
        90.0_f64.min((north / step).ceil() * step),
        (-90.0_f64).max((south / step).floor() * step),
    ]
    .map(|v| if v == 0.0 { 0.0 } else { v }))
}

pub fn build_url(
    cycle: DateTime<Utc>,
    hour: u32,
    bbox: Option<[f64; 4]>,
    resolution: &str,
    diagnostic: bool,
) -> Result<String, String> {
    grid_spacing(resolution)?;
    let mut url = Url::parse(&format!(
        "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_{resolution}.pl"
    ))
    .map_err(|e| e.to_string())?;
    let mut query = url.query_pairs_mut();
    query.append_pair(
        "file",
        &format!("gfs.t{}z.pgrb2.{resolution}.f{hour:03}", cycle.format("%H")),
    );
    query.append_pair(
        "dir",
        &format!(
            "/gfs.{}/{}/atmos",
            cycle.format("%Y%m%d"),
            cycle.format("%H")
        ),
    );
    if let Some(bbox) = bbox {
        let [west, east, north, south] = canonical_bbox(bbox, resolution)?;
        let longitude = |v: f64| format!("{:.3}", if v < 0.0 { 360.0 + v } else { v });
        for (key, value) in [
            ("subregion", String::new()),
            ("leftlon", longitude(west)),
            ("rightlon", longitude(east)),
            ("toplat", format!("{north:.3}")),
            ("bottomlat", format!("{south:.3}")),
        ] {
            query.append_pair(key, &value);
        }
    }
    for level in firesky_native::gfs::PRESSURE_LEVELS
        .into_iter()
        .chain([1000, 975, 950, 900].into_iter().filter(|_| diagnostic))
    {
        query.append_pair(&format!("lev_{level}_mb"), "on");
    }
    for layer in [
        "low_cloud_layer",
        "middle_cloud_layer",
        "high_cloud_layer",
        "low_cloud_bottom_level",
        "low_cloud_top_level",
        "middle_cloud_bottom_level",
        "middle_cloud_top_level",
        "high_cloud_bottom_level",
        "high_cloud_top_level",
        "surface",
    ] {
        query.append_pair(&format!("lev_{layer}"), "on");
    }
    for variable in VARIABLES
        .iter()
        .copied()
        .chain(["RWMR", "SNMR", "GRLE"].into_iter().filter(|_| diagnostic))
    {
        query.append_pair(&format!("var_{variable}"), "on");
    }
    drop(query);
    Ok(url.into())
}

fn cycle_key(cycle: DateTime<Utc>) -> String {
    cycle.format("%Y-%m-%dT%H:%M:%SZ").to_string()
}
fn parse_cycle(value: &str) -> Result<DateTime<Utc>, String> {
    Ok(time_contract::parse_zoned(value)?.with_timezone(&Utc))
}
fn relative(path: &Path) -> bool {
    !path.as_os_str().is_empty()
        && path
            .components()
            .all(|part| matches!(part, Component::Normal(_) | Component::CurDir))
}
fn signature(metadata: &Metadata) -> (u64, i128) {
    (metadata.len(), modified_ns(metadata))
}
fn modified_ns(metadata: &Metadata) -> i128 {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        i128::from(metadata.mtime()) * 1_000_000_000 + i128::from(metadata.mtime_nsec())
    }
    #[cfg(not(unix))]
    {
        match metadata
            .modified()
            .unwrap_or(UNIX_EPOCH)
            .duration_since(UNIX_EPOCH)
        {
            Ok(value) => value.as_nanos() as i128,
            Err(value) => -(value.duration().as_nanos() as i128),
        }
    }
}
fn regular(path: &Path) -> Option<Metadata> {
    fs::symlink_metadata(path)
        .ok()
        .filter(|m| m.file_type().is_file())
}
fn nonempty(path: &Path) -> bool {
    regular(path).is_some_and(|m| m.len() > 0)
}

fn query_pairs(url: &str) -> Vec<(String, String)> {
    let parsed = Url::parse(url).or_else(|_| Url::parse("http://localhost/").unwrap().join(url));
    parsed
        .map(|url| {
            url.query_pairs()
                .map(|(k, v)| (k.into_owned(), v.into_owned()))
                .collect()
        })
        .unwrap_or_default()
}
fn spatial_key(key: &str) -> bool {
    matches!(
        key,
        "leftlon" | "rightlon" | "toplat" | "bottomlat" | "subregion"
    )
}
fn schema(url: &str) -> Vec<(String, String)> {
    let mut pairs: Vec<_> = query_pairs(url)
        .into_iter()
        .filter(|(k, _)| !spatial_key(k))
        .collect();
    pairs.sort();
    pairs
}

#[derive(Clone, Serialize, Deserialize)]
struct Entry {
    metadata_path: PathBuf,
    metadata_size: u64,
    metadata_mtime_ns: i128,
    cycle: String,
    forecast_hour: i64,
    file: PathBuf,
    bbox: Option<[f64; 4]>,
    #[serde(rename = "global")]
    global_cache: bool,
    spatially_unbounded: bool,
    request_schema: Vec<(String, String)>,
}
#[derive(Clone, Serialize, Deserialize)]
struct Stamp {
    path: PathBuf,
    device: u64,
    inode: u64,
    mtime_ns: i128,
}
impl Stamp {
    fn new(root: &Path, path: &Path) -> Result<Self, String> {
        let metadata = fs::metadata(path).map_err(|e| e.to_string())?;
        #[cfg(unix)]
        let (device, inode) = {
            use std::os::unix::fs::MetadataExt;
            (metadata.dev(), metadata.ino())
        };
        #[cfg(not(unix))]
        let (device, inode) = (0, 0);
        let relative = path.strip_prefix(root).map_err(|e| e.to_string())?;
        Ok(Self {
            path: if relative.as_os_str().is_empty() {
                ".".into()
            } else {
                relative.into()
            },
            device,
            inode,
            mtime_ns: modified_ns(&metadata),
        })
    }
    fn matches(&self, root: &Path) -> bool {
        Self::new(root, &root.join(&self.path)).is_ok_and(|m| {
            m.device == self.device && m.inode == self.inode && m.mtime_ns == self.mtime_ns
        })
    }
}
#[derive(Clone, Serialize, Deserialize)]
struct Index {
    format: String,
    cache_root: PathBuf,
    entries: Vec<Entry>,
    directories: Vec<Stamp>,
}

struct CachedIndex {
    signature: (u64, i128),
    index: Arc<Index>,
}
static INDEX_CACHE: OnceLock<Mutex<VecDeque<CachedIndex>>> = OnceLock::new();

fn remember_index(index: Arc<Index>, signature: (u64, i128)) {
    if signature.0 > MAX_INDEX_BYTES {
        return;
    }
    let mut cache = INDEX_CACHE.get_or_init(Mutex::default).lock().unwrap();
    cache.retain(|old| old.index.cache_root != index.cache_root);
    cache.push_back(CachedIndex { signature, index });
    // Bound retained roots and serialized index bytes, not an RSS guarantee.
    while cache.len() > 4 || cache.iter().map(|v| v.signature.0).sum::<u64>() > MAX_INDEX_BYTES {
        cache.pop_front();
    }
}

fn metadata_number(value: &Value) -> Option<f64> {
    let number = match value {
        Value::Number(v) => v.as_f64()?,
        Value::Bool(v) => f64::from(u8::from(*v)),
        Value::String(v) => v.trim().parse().ok()?,
        _ => return None,
    };
    number.is_finite().then_some(number)
}

fn entry(root: &Path, path: &Path) -> Option<Entry> {
    let before = regular(path)?;
    let (data, _): (Value, _) = request_file::read(path, MAX_METADATA_BYTES).ok()?;
    let after = regular(path)?;
    if signature(&before) != signature(&after) {
        return None;
    }
    let metadata_path = path.strip_prefix(root).ok()?.to_path_buf();
    let file = PathBuf::from(data["file"].as_str()?);
    if !relative(&file) {
        return None;
    }
    let bbox = if data["bbox"].is_null() {
        None
    } else {
        let values = data["bbox"].as_array()?;
        if values.len() != 4 {
            return None;
        }
        Some([
            metadata_number(&values[0])?,
            metadata_number(&values[1])?,
            metadata_number(&values[2])?,
            metadata_number(&values[3])?,
        ])
    };
    let url = data["url"].as_str().unwrap_or("");
    let forecast_hour = data["forecast_hour"].as_i64().or_else(|| {
        if let Some(value) = data["forecast_hour"].as_str() {
            value.trim().parse().ok()
        } else {
            metadata_number(&data["forecast_hour"])
                .filter(|v| *v >= i64::MIN as f64 && *v < -(i64::MIN as f64))
                .map(|v| v.trunc() as i64)
        }
    })?;
    Some(Entry {
        metadata_path,
        metadata_size: after.len(),
        metadata_mtime_ns: modified_ns(&after),
        cycle: data["cycle"].as_str().unwrap_or("").into(),
        forecast_hour,
        file,
        bbox,
        global_cache: match &data["global"] {
            Value::Null => false,
            Value::Bool(v) => *v,
            Value::Number(v) => v.as_f64().is_none_or(|v| v != 0.0),
            Value::String(v) => !v.is_empty(),
            Value::Array(v) => !v.is_empty(),
            Value::Object(v) => !v.is_empty(),
        },
        spatially_unbounded: !query_pairs(url).iter().any(|(k, _)| spatial_key(k)),
        request_schema: schema(url),
    })
}

// Do not follow directory symlinks. The index directory is excluded so its
// own atomic replacements cannot invalidate the surrounding cache stamps.
fn walk(root: &Path, collect_files: bool) -> Result<(Vec<PathBuf>, Vec<Stamp>), String> {
    let mut pending = vec![(root.to_path_buf(), 0)];
    let mut files = Vec::new();
    let mut stamps = Vec::new();
    let mut visited = 0;
    while let Some((directory, depth)) = pending.pop() {
        if depth > 64 {
            return Err("GFS cache directory depth exceeds 64".into());
        }
        stamps.push(Stamp::new(root, &directory)?);
        for item in fs::read_dir(&directory).map_err(|e| e.to_string())? {
            let item = item.map_err(|e| e.to_string())?;
            visited += 1;
            if visited > 1_000_000 {
                return Err("GFS cache scan exceeds one million entries".into());
            }
            let kind = item.file_type().map_err(|e| e.to_string())?;
            if kind.is_dir() && item.file_name() != INDEX_DIR {
                pending.push((item.path(), depth + 1));
            } else if collect_files
                && kind.is_file()
                && item.file_name().to_string_lossy().ends_with(".grib2.json")
            {
                files.push(item.path());
                if files.len() > MAX_ENTRIES {
                    return Err("GFS metadata index exceeds 100000 entries".into());
                }
            }
        }
    }
    files.sort();
    stamps.sort_by(|a, b| a.path.cmp(&b.path));
    Ok((files, stamps))
}

fn index_paths(root: &Path) -> Result<(PathBuf, PathBuf), String> {
    let directory = root.join(INDEX_DIR);
    fs::create_dir_all(&directory).map_err(|e| e.to_string())?;
    Ok((directory.join("index.json"), directory.join("index.lock")))
}
fn read_index(root: &Path, path: &Path, validate_dirs: bool) -> Option<Arc<Index>> {
    let root_key = fs::canonicalize(root).ok()?;
    let before = signature(&regular(path)?);
    let cached = {
        let mut cache = INDEX_CACHE.get_or_init(Mutex::default).lock().unwrap();
        cache
            .iter()
            .position(|old| old.index.cache_root == root_key && old.signature == before)
            .and_then(|position| cache.remove(position))
            .map(|old| {
                let index = Arc::clone(&old.index);
                cache.push_back(old);
                index
            })
    };
    if let Some(index) = cached {
        return (!validate_dirs || index.directories.iter().all(|s| s.matches(root)))
            .then_some(index);
    }
    let (mut index, _): (Index, _) = request_file::read(path, MAX_INDEX_BYTES).ok()?;
    if index.format != INDEX_FORMAT
        || index.cache_root != root_key
        || signature(&regular(path)?) != before
        || index.entries.len() > MAX_ENTRIES
        || index.directories.len() > 1_000_000
        || !index.directories.iter().any(|s| s.path == Path::new("."))
        || index.directories.iter().any(|s| !relative(&s.path))
        || index.entries.iter().any(|e| {
            !relative(&e.metadata_path)
                || !relative(&e.file)
                || e.bbox.is_some_and(|v| v.iter().any(|n| !n.is_finite()))
        })
        || (validate_dirs && index.directories.iter().any(|s| !s.matches(root)))
    {
        return None;
    }
    index
        .entries
        .sort_by(|a, b| a.metadata_path.cmp(&b.metadata_path));
    let index = Arc::new(index);
    remember_index(Arc::clone(&index), before);
    Some(index)
}
fn atomic_json(path: &Path, data: &impl Serialize, limit: u64) -> Result<(), String> {
    if fs::symlink_metadata(path).is_ok_and(|m| !m.is_file()) {
        return Err("GFS metadata destination is not a regular file".into());
    }
    let parent = path.parent().ok_or("GFS metadata has no parent")?;
    let bytes = serde_json::to_vec(data).map_err(|e| e.to_string())?;
    if bytes.len() as u64 > limit {
        return Err("GFS metadata exceeds byte limit".into());
    }
    let mut temporary = tempfile::Builder::new()
        .prefix(".firesky-tmp-")
        .tempfile_in(parent)
        .map_err(|e| e.to_string())?;
    temporary.write_all(&bytes).map_err(|e| e.to_string())?;
    temporary.as_file().sync_all().map_err(|e| e.to_string())?;
    temporary.persist(path).map_err(|e| e.error.to_string())?;
    sync_directory(parent)
}
fn sync_directory(path: &Path) -> Result<(), String> {
    #[cfg(unix)]
    {
        File::open(path)
            .and_then(|f| f.sync_all())
            .map_err(|e| e.to_string())?;
    }
    #[cfg(not(unix))]
    {
        let _ = path;
    }
    Ok(())
}
fn rebuild_locked(root: &Path, path: &Path) -> Result<Arc<Index>, String> {
    let (files, directories) = walk(root, true)?;
    let entries = files.iter().filter_map(|p| entry(root, p)).collect();
    let index = Index {
        format: INDEX_FORMAT.into(),
        cache_root: fs::canonicalize(root).map_err(|e| e.to_string())?,
        entries,
        directories,
    };
    atomic_json(path, &index, MAX_INDEX_BYTES)?;
    let index = Arc::new(index);
    if let Some(metadata) = regular(path) {
        remember_index(Arc::clone(&index), signature(&metadata));
    }
    Ok(index)
}
fn load_index(root: &Path, seconds: f64, force: bool) -> Result<Arc<Index>, String> {
    let (path, lock) = index_paths(root)?;
    if !force {
        if let Some(index) = read_index(root, &path, true) {
            return Ok(index);
        }
    }
    let _guard = Lock::acquire(&lock, seconds)?;
    if !force {
        if let Some(index) = read_index(root, &path, true) {
            return Ok(index);
        }
    }
    rebuild_locked(root, &path)
}

pub struct GfsClient {
    config: Config,
}
impl GfsClient {
    pub fn new(config: Config) -> Result<Self, String> {
        config.validate()?;
        Ok(Self { config })
    }
    pub fn find(
        &self,
        cycle: DateTime<Utc>,
        hour: u32,
        bbox: Option<[f64; 4]>,
        url: Option<&str>,
    ) -> Result<Option<PathBuf>, String> {
        let bbox = bbox
            .map(|b| canonical_bbox(b, &self.config.resolution))
            .transpose()?;
        let key = cycle_key(cycle);
        let requested_schema = url.map(schema);
        let mut index = load_index(
            &self.config.cache_dir,
            self.config.lock_timeout_seconds,
            false,
        )?;
        for attempt in 0..2 {
            let mut stale = false;
            for entry in &index.entries {
                if entry.cycle != key || entry.forecast_hour != i64::from(hour) {
                    continue;
                }
                let path = self.config.cache_dir.join(&entry.metadata_path);
                if regular(&path)
                    .is_none_or(|m| signature(&m) != (entry.metadata_size, entry.metadata_mtime_ns))
                {
                    stale = true;
                    break;
                }
                let mut file = path.parent().unwrap().join(&entry.file);
                if !file.exists() {
                    file = self.config.cache_dir.join(&entry.file);
                }
                if !nonempty(&file)
                    || requested_schema
                        .as_ref()
                        .is_some_and(|s| *s != entry.request_schema)
                {
                    continue;
                }
                if entry.global_cache {
                    if entry.bbox == Some([-180.0, 180.0, 90.0, -90.0]) && entry.spatially_unbounded
                    {
                        return Ok(Some(file));
                    }
                } else if let (Some(outer), Some(inner)) = (entry.bbox, bbox) {
                    if outer[0] <= inner[0]
                        && outer[1] >= inner[1]
                        && outer[2] >= inner[2]
                        && outer[3] <= inner[3]
                    {
                        return Ok(Some(file));
                    }
                }
            }
            if stale && attempt == 0 {
                index = load_index(
                    &self.config.cache_dir,
                    self.config.lock_timeout_seconds,
                    true,
                )?;
            } else {
                break;
            }
        }
        Ok(None)
    }
    pub fn resolve(
        &self,
        cycle: DateTime<Utc>,
        hour: u32,
        bbox: Option<[f64; 4]>,
        diagnostic: bool,
        allow_download: bool,
    ) -> Result<Option<Value>, String> {
        let url = build_url(cycle, hour, bbox, &self.config.resolution, diagnostic)?;
        if let Some(path) = self.find(cycle, hour, bbox, Some(&url))? {
            return Ok(Some(
                json!({"path":path,"status":"covering_cache","url":url,
                "content_verified":false}),
            ));
        }
        if !allow_download {
            return Ok(None);
        }
        Ok(Some(self.download(&url, Some(cycle), Some(hour), bbox)?))
    }
    fn target(&self, url: &str, cycle: Option<DateTime<Utc>>) -> PathBuf {
        let digest: String = Sha256::digest(url.as_bytes())
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        let mut directory = self.config.cache_dir.clone();
        if let Some(cycle) = cycle {
            directory.push(cycle.format("%Y-%m-%d").to_string());
            directory.push(cycle.format("%HZ").to_string());
            if let Some(region) = &self.config.region {
                directory.push(region);
            }
            directory.push(&self.config.resolution);
        } else {
            directory.push("undated");
        }
        directory.join(format!("{}.grib2", &digest[..20]))
    }
    fn metadata(
        &self,
        path: &Path,
        url: &str,
        cycle: Option<DateTime<Utc>>,
        hour: Option<u32>,
        bbox: Option<[f64; 4]>,
    ) -> Result<(), String> {
        let (Some(cycle), Some(hour)) = (cycle, hour) else {
            return Ok(());
        };
        let mut data = json!({"model":"gfs","cycle":cycle_key(cycle),"forecast_hour":hour,
            "file":path.file_name().unwrap().to_string_lossy(),"url":url});
        data["bbox"] = json!(bbox.unwrap_or([-180.0, 180.0, 90.0, -90.0]));
        if bbox.is_none() {
            data["global"] = json!(true);
        }
        let (index_path, lock_path) = index_paths(&self.config.cache_dir)?;
        let _guard = Lock::acquire(&lock_path, self.config.lock_timeout_seconds)?;
        let metadata_path = path.with_extension("grib2.json");
        atomic_json(&metadata_path, &data, MAX_METADATA_BYTES)?;
        if index_path.is_file() {
            let root = &self.config.cache_dir;
            if let (Some(previous), Some(new)) = (
                read_index(root, &index_path, false),
                entry(root, &metadata_path),
            ) {
                let mut entries = previous.entries.clone();
                match entries.binary_search_by(|e| e.metadata_path.cmp(&new.metadata_path)) {
                    Ok(position) => entries[position] = new,
                    Err(position) => entries.insert(position, new),
                }
                if entries.len() > MAX_ENTRIES {
                    return Err("GFS metadata index exceeds 100000 entries".into());
                }
                let (_, directories) = walk(root, false)?;
                let index = Arc::new(Index {
                    format: INDEX_FORMAT.into(),
                    cache_root: previous.cache_root.clone(),
                    entries,
                    directories,
                });
                atomic_json(&index_path, index.as_ref(), MAX_INDEX_BYTES)?;
                if let Some(metadata) = regular(&index_path) {
                    remember_index(index, signature(&metadata));
                }
            } else {
                rebuild_locked(root, &index_path)?;
            }
        }
        Ok(())
    }
    pub fn download(
        &self,
        url: &str,
        cycle: Option<DateTime<Utc>>,
        hour: Option<u32>,
        bbox: Option<[f64; 4]>,
    ) -> Result<Value, String> {
        let parsed = Url::parse(url).map_err(|_| "invalid GFS HTTP URL")?;
        if !matches!(parsed.scheme(), "http" | "https")
            || !parsed.username().is_empty()
            || parsed.password().is_some()
            || parsed.fragment().is_some()
        {
            return Err("GFS downloads require HTTP(S), without credentials or fragments".into());
        }
        let bbox = bbox
            .map(|b| canonical_bbox(b, &self.config.resolution))
            .transpose()?;
        let target = self.target(url, cycle);
        let parent = target.parent().unwrap();
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        if nonempty(&target) {
            self.metadata(&target, url, cycle, hour, bbox)?;
            return Ok(json!({"path":target,"status":"exact_cache","content_verified":false}));
        }
        let _guard = Lock::acquire(
            &target.with_extension("grib2.lock"),
            self.config.lock_timeout_seconds,
        )?;
        if nonempty(&target) {
            self.metadata(&target, url, cycle, hour, bbox)?;
            return Ok(json!({"path":target,"status":"exact_cache","content_verified":false}));
        }
        if fs::symlink_metadata(&target).is_ok_and(|m| !m.file_type().is_file()) {
            return Err("GFS destination is not a regular file".into());
        }
        let client = Client::builder()
            .timeout(Duration::from_secs_f64(self.config.timeout_seconds))
            .connect_timeout(Duration::from_secs_f64(self.config.timeout_seconds))
            .redirect(reqwest::redirect::Policy::limited(10))
            .referer(false)
            .retry(reqwest::retry::never())
            .user_agent("firesky/0.1 burning-sunset-engine")
            .build()
            .map_err(|e| e.without_url().to_string())?;
        let mut last_error = String::new();
        for attempt in 1..=self.config.download_attempts {
            let mut temporary = tempfile::Builder::new()
                .prefix(".firesky-tmp-")
                .tempfile_in(parent)
                .map_err(|e| e.to_string())?;
            match download_once(&client, &parsed, &mut temporary) {
                Ok(identity) => {
                    if nonempty(&target) {
                        self.metadata(&target, url, cycle, hour, bbox)?;
                        return Ok(
                            json!({"path":target,"status":"exact_cache","content_verified":false}),
                        );
                    }
                    // Empty cache placeholders are replaceable; nonempty files
                    // are never overwritten. The per-target lock spans publish.
                    if target.exists() {
                        fs::remove_file(&target).map_err(|e| e.to_string())?;
                    }
                    temporary
                        .persist_noclobber(&target)
                        .map_err(|e| e.error.to_string())?;
                    sync_directory(parent)?;
                    self.metadata(&target, url, cycle, hour, bbox)?;
                    return Ok(
                        json!({"path":target,"status":"downloaded","identity":identity,
                        "content_verified":true,"verification":"length-and-GRIB-boundary-markers",
                        "model_fields_validated":false,"attempts":attempt}),
                    );
                }
                Err(error) => last_error = error,
            }
            drop(temporary);
            if attempt < self.config.download_attempts {
                std::thread::sleep(Duration::from_secs((1_u64 << (attempt - 1).min(4)).min(12)));
            }
        }
        Err(format!(
            "GFS download failed after {} attempts: {last_error}",
            self.config.download_attempts
        ))
    }
}

fn download_once(
    client: &Client,
    url: &Url,
    temporary: &mut tempfile::NamedTempFile,
) -> Result<Value, String> {
    let mut response = client
        .get(url.clone())
        .header("Accept-Encoding", "identity")
        .send()
        .and_then(|r| r.error_for_status())
        .map_err(|e| e.without_url().to_string())?;
    let expected = response
        .headers()
        .get(reqwest::header::CONTENT_LENGTH)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.parse::<u64>().ok());
    if expected.is_some_and(|v| v > MAX_DOWNLOAD) {
        return Err("GFS response exceeds 512 MiB".into());
    }
    let mut buffer = vec![0; 1024 * 1024];
    let mut bytes = 0_u64;
    let mut hash = Sha256::new();
    loop {
        let count = response
            .read(&mut buffer)
            .map_err(|e| format!("GFS response read: {e}"))?;
        if count == 0 {
            break;
        }
        bytes += count as u64;
        if bytes > MAX_DOWNLOAD {
            return Err("GFS response exceeds 512 MiB".into());
        }
        temporary
            .write_all(&buffer[..count])
            .map_err(|e| e.to_string())?;
        hash.update(&buffer[..count]);
    }
    if expected.is_some_and(|v| v != bytes) {
        return Err("incomplete GFS response length".into());
    }
    if bytes < 16 {
        return Err("GFS response is too small".into());
    }
    temporary
        .seek(SeekFrom::Start(0))
        .map_err(|e| e.to_string())?;
    let mut marker = [0; 4];
    temporary
        .read_exact(&mut marker)
        .map_err(|e| e.to_string())?;
    if marker != *b"GRIB" {
        return Err("GFS response is not a GRIB file".into());
    }
    temporary
        .seek(SeekFrom::End(-4))
        .map_err(|e| e.to_string())?;
    temporary
        .read_exact(&mut marker)
        .map_err(|e| e.to_string())?;
    if marker != *b"7777" {
        return Err("GFS response has no GRIB end marker".into());
    }
    temporary.as_file().sync_all().map_err(|e| e.to_string())?;
    Ok(
        json!({"bytes":bytes, "sha256":hash.finalize().iter().map(|b| format!("{b:02x}")).collect::<String>()}),
    )
}

pub fn execute(params: Params) -> Result<Value, String> {
    let started = Instant::now();
    let cycle = params.cycle_utc.as_deref().map(parse_cycle).transpose()?;
    let required_cycle = || cycle.ok_or_else(|| "GFS operation requires cycle_utc".to_string());
    let hour = || {
        params
            .forecast_hour
            .ok_or_else(|| "GFS operation requires forecast_hour".to_string())
    };
    if matches!(params.operation, Operation::Url) {
        let bbox = params
            .bbox
            .map(|b| canonical_bbox(b, &params.resolution))
            .transpose()?;
        return Ok(
            json!({"url":build_url(required_cycle()?,hour()?,bbox,&params.resolution,params.include_diagnostic_fields)?,
            "bbox":bbox}),
        );
    }
    let client = GfsClient::new(Config {
        cache_dir: params.cache_dir.ok_or("GFS operation requires cache_dir")?,
        resolution: params.resolution,
        region: params.region,
        timeout_seconds: params.timeout_seconds,
        lock_timeout_seconds: params.lock_timeout_seconds,
        download_attempts: params.download_attempts,
    })?;
    let result = match params.operation {
        Operation::Url => unreachable!(),
        Operation::Find => {
            json!({"path":client.find(required_cycle()?,hour()?,params.bbox,params.url.as_deref())?})
        }
        Operation::Download => client.download(
            params.url.as_deref().ok_or("download requires url")?,
            cycle,
            params.forecast_hour,
            params.bbox,
        )?,
        Operation::Resolve => {
            let hours = params
                .forecast_hours
                .ok_or("resolve requires forecast_hours")?;
            if hours.is_empty() || hours.len() > 64 || hours.windows(2).any(|v| v[0] >= v[1]) {
                return Err("GFS hours must be 1..64 unique ascending integers".into());
            }
            let mut files = Vec::new();
            let mut missing = Vec::new();
            for hour in hours {
                if let Some(mut file) = client.resolve(
                    required_cycle()?,
                    hour,
                    params.bbox,
                    params.include_diagnostic_fields,
                    params.allow_download,
                )? {
                    file["hour"] = json!(hour);
                    files.push(file);
                } else {
                    missing.push(hour);
                }
            }
            json!({"files":files,"missing_hours":missing,"complete":missing.is_empty()})
        }
    };
    let mut result = result;
    result["elapsed_ms"] = json!(started.elapsed().as_secs_f64() * 1000.0);
    result["contract"] = json!("rust-gfs-data-v1");
    Ok(result)
}
