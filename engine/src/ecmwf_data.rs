//! Strict public/basic IFS raw transport. Offline full catalogs are never fetched.
use crate::{
    cache_lock::Lock, ecmwf, grib::EcCodes, tensor_bundle::source_identity, time_contract,
};
use chrono::{DateTime, Duration, Timelike, Utc};
use reqwest::{blocking::Client, header, Url};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::VecDeque,
    fs::{self, File},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::Arc,
    time::Instant,
};

const MAX_INDEX: u64 = 32 * 1024 * 1024;
const MAX_FILE: u64 = 512 * 1024 * 1024;
const MAX_RECORDS: usize = 100_000;
const MAX_RETAINED_INDEX: usize = 64 * 1024 * 1024;
const PRESSURE: [&str; 6] = ["gh", "r", "t", "w", "u", "v"];
const SURFACE: [&str; 3] = ["z", "2t", "2d"];

#[derive(Clone, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Config {
    pub cache_dir: PathBuf,
    pub source: String,
    pub resolution: String,
    pub base_url: Option<String>,
    pub timeout_seconds: f64,
    pub lock_timeout_seconds: f64,
    pub request_attempts: u32,
    pub retry_seconds: f64,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            cache_dir: ".cache/ecmwf".into(),
            source: "ecmwf".into(),
            resolution: "0p25".into(),
            base_url: None,
            timeout_seconds: 90.0,
            lock_timeout_seconds: 300.0,
            request_attempts: 3,
            retry_seconds: 1.0,
        }
    }
}
fn safe_url(value: &str) -> Result<Url, String> {
    let url = Url::parse(value).map_err(|_| "invalid EC data URL")?;
    let local = url.host_str().is_some_and(|v| {
        v == "localhost"
            || v.trim_matches(['[', ']'])
                .parse::<std::net::IpAddr>()
                .is_ok_and(|ip| ip.is_loopback())
    });
    if (url.scheme() != "https" && !(url.scheme() == "http" && local))
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
        || url.host().is_none()
    {
        return Err(
            "EC data URLs require HTTPS (HTTP only on loopback), without credentials/fragment"
                .into(),
        );
    }
    Ok(url)
}
impl Config {
    pub(crate) fn validate(&self) -> Result<(), String> {
        if self.cache_dir.as_os_str().is_empty()
            || self.resolution != "0p25"
            || !["aws", "google", "ecmwf"].contains(&self.source.as_str())
            || !self.timeout_seconds.is_finite()
            || !(0.001..=3600.0).contains(&self.timeout_seconds)
            || !self.lock_timeout_seconds.is_finite()
            || !(0.0..=3600.0).contains(&self.lock_timeout_seconds)
            || !self.retry_seconds.is_finite()
            || !(0.0..=120.0).contains(&self.retry_seconds)
            || !(1..=32).contains(&self.request_attempts)
        {
            return Err("invalid bounded EC public/basic data configuration".into());
        }
        if let Some(value) = &self.base_url {
            if safe_url(value)?.query().is_some() {
                return Err("EC data base URL cannot contain a query".into());
            }
        }
        Ok(())
    }
    fn url(&self, cycle: DateTime<Utc>, hour: i64, extension: &str) -> Result<Url, String> {
        let base = self
            .base_url
            .as_deref()
            .unwrap_or(match self.source.as_str() {
                "aws" => "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
                "google" => "https://storage.googleapis.com/ecmwf-open-data",
                _ => "https://data.ecmwf.int/forecasts",
            });
        let stream = if cycle.date_naive() < chrono::NaiveDate::from_ymd_opt(2026, 5, 12).unwrap()
            && [6, 18].contains(&cycle.hour())
        {
            "scda"
        } else {
            "oper"
        };
        safe_url(&format!(
            "{}/{}/{:02}z/ifs/0p25/{stream}/{}0000-{hour}h-{stream}-fc.{extension}",
            base.trim_end_matches('/'),
            cycle.format("%Y%m%d"),
            cycle.hour(),
            cycle.format("%Y%m%d%H")
        ))
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    pub operation: String,
    #[serde(default)]
    pub config: Config,
    pub cycle_utc: Option<String>,
    pub forecast_hour: Option<i64>,
    pub forecast_hours: Option<Vec<i64>>,
    pub reference_utc: Option<String>,
    pub now_utc: Option<String>,
    #[serde(default)]
    pub evaluation_utcs: Vec<String>,
    #[serde(default = "max_cycles")]
    pub max_cycles: usize,
    #[serde(default)]
    pub allow_download: bool,
    #[serde(default = "yes")]
    pub use_cache_for_cycle: bool,
}
fn max_cycles() -> usize {
    8
}
fn yes() -> bool {
    true
}
fn parse_cycle(value: &str) -> Result<DateTime<Utc>, String> {
    let cycle = time_contract::parse_utc(value)?;
    if cycle.minute() != 0 || cycle.second() != 0 || cycle.timestamp_subsec_nanos() != 0 {
        return Err("EC public/basic cycle must be on an exact UTC hour".into());
    }
    Ok(cycle)
}
pub(crate) fn path(root: &Path, cycle: DateTime<Utc>, hour: i64) -> PathBuf {
    root.join(cycle.format("%Y-%m-%d").to_string())
        .join(format!("{:02}Z", cycle.hour()))
        .join(format!(
            "ifs-basic-v2-{}-f{hour:03}.grib2",
            cycle.format("%Y%m%d%H")
        ))
}
fn sidecar(path: &Path) -> PathBuf {
    path.with_file_name(format!(
        "{}.json",
        path.file_name().unwrap().to_string_lossy()
    ))
}
fn metadata(config: &Config, cycle: DateTime<Utc>, hour: i64) -> Value {
    json!({"schema":"firesky-ecmwf-ifs-basic-v2","schema_version":2,
        "contract":"public-open-data/basic","model":"ifs","member":"ecmwf-ifs-basic",
        "source":config.source,"resolution":config.resolution,
        "cycle_utc":cycle.to_rfc3339_opts(chrono::SecondsFormat::Secs,true),"forecast_hour":hour,
        "valid_time_utc":(cycle+Duration::hours(hour)).to_rfc3339_opts(chrono::SecondsFormat::Secs,true),
        "pressure_levels_hpa":ecmwf::LEVELS,"pressure_params":PRESSURE,
        "cloud_params":["tcc"],"surface_params":SURFACE,"terrain_step_hour":0})
}
fn regular(path: &Path) -> Result<bool, String> {
    match fs::symlink_metadata(path) {
        Ok(m) if !m.is_file() => {
            Err("EC cache destination must be a regular nonsymlink file".into())
        }
        Ok(m) if m.len() > MAX_FILE => Err("EC cache file exceeds 512 MiB".into()),
        Ok(m) => Ok(m.len() > 0),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(e) => Err(e.to_string()),
    }
}
fn cached(
    codes: &EcCodes,
    config: &Config,
    cycle: DateTime<Utc>,
    hour: i64,
    target: &Path,
) -> Result<Value, String> {
    let meta_path = sidecar(target);
    if !regular(&meta_path)? {
        return Err("EC cached basic bundle has no valid metadata sidecar".into());
    }
    let mut bytes = Vec::new();
    File::open(meta_path)
        .map_err(|e| e.to_string())?
        .take(64 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > 64 * 1024
        || serde_json::from_slice::<Value>(&bytes).map_err(|_| "invalid EC cache metadata JSON")?
            != metadata(config, cycle, hour)
    {
        return Err("EC cached basic bundle metadata does not match requested contract".into());
    }
    ecmwf::probe_basic(codes, target, cycle, hour)
}

fn index_text<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<String>, D::Error> {
    let value = Value::deserialize(deserializer)?;
    Ok(value.as_str().map(str::to_owned))
}
fn index_integer<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<u64>, D::Error> {
    let value = Value::deserialize(deserializer)?;
    Ok(value.as_u64())
}
#[derive(Deserialize)]
struct IndexRecord {
    #[serde(default, deserialize_with = "index_text")]
    param: Option<String>,
    #[serde(default, rename = "type", deserialize_with = "index_text")]
    request_type: Option<String>,
    #[serde(default, deserialize_with = "index_text")]
    step: Option<String>,
    #[serde(default, deserialize_with = "index_text")]
    levtype: Option<String>,
    #[serde(default, deserialize_with = "index_text")]
    levelist: Option<String>,
    #[serde(default, rename = "_offset", deserialize_with = "index_integer")]
    offset: Option<u64>,
    #[serde(default, rename = "_length", deserialize_with = "index_integer")]
    length: Option<u64>,
}
struct IndexEntry {
    url: String,
    records: Arc<Vec<IndexRecord>>,
    bytes: usize,
}
fn retain_index(
    entries: &mut VecDeque<IndexEntry>,
    retained: &mut usize,
    entry: IndexEntry,
    limit: usize,
) {
    if entry.bytes > limit {
        return;
    }
    while *retained + entry.bytes > limit {
        let old = entries.pop_front().unwrap();
        *retained -= old.bytes;
    }
    *retained += entry.bytes;
    entries.push_back(entry);
}
struct Transfer {
    client: Client,
    config: Config,
    indexes: VecDeque<IndexEntry>,
    retained_index_bytes: usize,
    index_requests: usize,
    range_requests: usize,
    bytes: u64,
    index_ms: f64,
    range_ms: f64,
    header_probe_ms: f64,
}
impl Transfer {
    fn new(config: Config) -> Result<Self, String> {
        let client = Client::builder()
            .retry(reqwest::retry::never())
            .referer(false)
            .user_agent("firesky-rust/0.1")
            .redirect(reqwest::redirect::Policy::custom(|attempt| {
                if attempt.previous().len() >= 10 || safe_url(attempt.url().as_str()).is_err() {
                    attempt.stop()
                } else {
                    attempt.follow()
                }
            }))
            .build()
            .map_err(|_| "cannot initialize EC HTTP client")?;
        Ok(Self {
            client,
            config,
            indexes: VecDeque::new(),
            retained_index_bytes: 0,
            index_requests: 0,
            range_requests: 0,
            bytes: 0,
            index_ms: 0.0,
            range_ms: 0.0,
            header_probe_ms: 0.0,
        })
    }
    fn send(
        &self,
        method: reqwest::Method,
        url: &Url,
        range: Option<(u64, u64)>,
    ) -> Result<reqwest::blocking::Response, String> {
        let attempts = if range.is_some() {
            1
        } else {
            self.config.request_attempts
        };
        for attempt in 1..=attempts {
            let mut request = self
                .client
                .request(method.clone(), url.clone())
                .header(header::ACCEPT_ENCODING, "identity")
                .timeout(std::time::Duration::from_secs_f64(
                    self.config.timeout_seconds,
                ));
            if let Some((start, len)) = range {
                request =
                    request.header(header::RANGE, format!("bytes={start}-{}", start + len - 1));
            }
            let result = request.send();
            let retryable = result.as_ref().map_or(true, |r| {
                r.status().is_server_error() || r.status().as_u16() == 429
            });
            if retryable && attempt < attempts {
                std::thread::sleep(std::time::Duration::from_secs_f64(
                    self.config.retry_seconds,
                ));
                continue;
            }
            return result.map_err(|_| "EC HTTP request failed (URL redacted)".into());
        }
        unreachable!()
    }
    fn probe(&self, cycle: DateTime<Utc>, hour: i64) -> Result<bool, String> {
        let response = self.send(
            reqwest::Method::HEAD,
            &self.config.url(cycle, hour, "index")?,
            None,
        )?;
        match response.status().as_u16() {
            200..=299 => Ok(true),
            404 => Ok(false),
            n => Err(format!("EC index probe returned HTTP {n}")),
        }
    }
    fn index(&mut self, cycle: DateTime<Utc>, hour: i64) -> Result<Arc<Vec<IndexRecord>>, String> {
        let started = Instant::now();
        let result = self.index_inner(cycle, hour);
        self.index_ms += started.elapsed().as_secs_f64() * 1000.0;
        result
    }
    fn index_inner(
        &mut self,
        cycle: DateTime<Utc>,
        hour: i64,
    ) -> Result<Arc<Vec<IndexRecord>>, String> {
        let url = self.config.url(cycle, hour, "index")?;
        if let Some(position) = self.indexes.iter().position(|v| v.url == url.as_str()) {
            let entry = self.indexes.remove(position).unwrap();
            let result = Arc::clone(&entry.records);
            self.indexes.push_back(entry);
            return Ok(result);
        }
        let mut response = self.send(reqwest::Method::GET, &url, None)?;
        self.index_requests += 1;
        if !response.status().is_success() {
            return Err(format!(
                "EC index HTTP status {}",
                response.status().as_u16()
            ));
        }
        if response.content_length().is_some_and(|n| n > MAX_INDEX) {
            return Err("EC index exceeds 32 MiB".into());
        }
        let mut bytes = Vec::new();
        (&mut response)
            .take(MAX_INDEX + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| "EC index read failed")?;
        if bytes.len() as u64 > MAX_INDEX {
            return Err("EC index exceeds 32 MiB".into());
        }
        let mut rows = Vec::new();
        for line in bytes
            .split(|b| *b == b'\n')
            .filter(|v| !v.iter().all(u8::is_ascii_whitespace))
        {
            if rows.len() >= MAX_RECORDS || line.len() > 64 * 1024 {
                return Err("EC index record limit exceeded".into());
            }
            if line.iter().find(|b| !b.is_ascii_whitespace()) != Some(&b'{') {
                return Err("EC index record must be an object".into());
            }
            let row: IndexRecord =
                serde_json::from_slice(line).map_err(|_| "invalid EC index JSON line")?;
            rows.push(row);
        }
        let bytes = std::mem::size_of::<IndexRecord>() * rows.capacity()
            + rows
                .iter()
                .flat_map(|row| {
                    [
                        &row.param,
                        &row.request_type,
                        &row.step,
                        &row.levtype,
                        &row.levelist,
                    ]
                })
                .filter_map(|v| v.as_ref())
                .map(String::capacity)
                .sum::<usize>()
            + url.as_str().len()
            + std::mem::size_of::<IndexEntry>();
        let records = Arc::new(rows);
        retain_index(
            &mut self.indexes,
            &mut self.retained_index_bytes,
            IndexEntry {
                url: url.to_string(),
                records: Arc::clone(&records),
                bytes,
            },
            MAX_RETAINED_INDEX,
        );
        Ok(records)
    }
    fn part(
        &mut self,
        cycle: DateTime<Utc>,
        hour: i64,
        kind: &str,
        out: &mut File,
    ) -> Result<(), String> {
        let rows = self.index(cycle, hour)?;
        let mut ranges = Vec::<(u64, u64)>::new();
        let hour_text = hour.to_string();
        for row in rows.iter() {
            if row.request_type.as_deref() != Some("fc") || row.step.as_deref() != Some(&hour_text)
            {
                continue;
            }
            let name = row.param.as_deref().unwrap_or("");
            let selected = match kind {
                "pl" => {
                    row.levtype.as_deref() == Some("pl")
                        && PRESSURE.contains(&name)
                        && row
                            .levelist
                            .as_deref()
                            .and_then(|v| v.parse::<i32>().ok())
                            .is_some_and(|v| ecmwf::LEVELS.contains(&v))
                }
                "cloud" => row.levtype.as_deref() == Some("sfc") && name == "tcc",
                "surface" => row.levtype.as_deref() == Some("sfc") && SURFACE.contains(&name),
                _ => false,
            };
            if !selected {
                continue;
            }
            let start = row.offset.ok_or("invalid EC index offset")?;
            let length = row
                .length
                .filter(|n| *n >= 16 && *n <= MAX_FILE)
                .ok_or("invalid EC index length")?;
            if start.checked_add(length).is_none() {
                return Err("EC index range overflow".into());
            }
            ranges.push((start, length));
        }
        ranges.sort_unstable();
        if ranges.is_empty() {
            return Err("EC index has no matching required request fields".into());
        }
        let mut merged = Vec::<(u64, u64)>::new();
        for (start, len) in ranges {
            if let Some((a, n)) = merged.last_mut() {
                if start < *a + *n {
                    return Err("EC index contains overlapping selected ranges".into());
                }
                if start == *a + *n {
                    *n = n
                        .checked_add(len)
                        .filter(|n| *n <= MAX_FILE)
                        .ok_or("EC selected part exceeds 512 MiB")?;
                    continue;
                }
            }
            merged.push((start, len));
        }
        let url = self.config.url(cycle, hour, "grib2")?;
        for (start, len) in merged {
            if out
                .stream_position()
                .map_err(|e| e.to_string())?
                .checked_add(len)
                .is_none_or(|n| n > MAX_FILE)
            {
                return Err("EC combined bundle exceeds 512 MiB".into());
            }
            self.copy_range(&url, start, len, out)?;
        }
        Ok(())
    }
    fn copy_range(
        &mut self,
        url: &Url,
        start: u64,
        len: u64,
        out: &mut File,
    ) -> Result<(), String> {
        let started = Instant::now();
        let result = self.copy_range_inner(url, start, len, out);
        self.range_ms += started.elapsed().as_secs_f64() * 1000.0;
        result
    }
    fn copy_range_inner(
        &mut self,
        url: &Url,
        start: u64,
        len: u64,
        out: &mut File,
    ) -> Result<(), String> {
        let offset = out.stream_position().map_err(|e| e.to_string())?;
        // Retry body failures from the same output offset, never append a partial range.
        for attempt in 1..=self.config.request_attempts {
            out.set_len(offset).map_err(|e| e.to_string())?;
            out.seek(SeekFrom::Start(offset))
                .map_err(|e| e.to_string())?;
            self.range_requests += 1;
            let result = (|| {
                let mut response = self.send(reqwest::Method::GET, url, Some((start, len)))?;
                if response.status().as_u16() != 206 {
                    return Err(format!(
                        "EC ranged download requires HTTP 206, got {}",
                        response.status().as_u16()
                    ));
                }
                let range = response
                    .headers()
                    .get(header::CONTENT_RANGE)
                    .and_then(|v| v.to_str().ok())
                    .ok_or("EC ranged response has no Content-Range")?;
                let (interval, total) = range
                    .strip_prefix("bytes ")
                    .and_then(|v| v.split_once('/'))
                    .ok_or("invalid EC Content-Range")?;
                let (left, right) = interval.split_once('-').ok_or("invalid EC Content-Range")?;
                if left.parse::<u64>().ok() != Some(start)
                    || right.parse::<u64>().ok() != Some(start + len - 1)
                    || total.parse::<u64>().ok().is_none_or(|n| n < start + len)
                    || response.content_length().is_some_and(|n| n != len)
                    || response
                        .headers()
                        .get(header::CONTENT_ENCODING)
                        .is_some_and(|v| v != "identity")
                {
                    return Err("EC ranged response boundaries/encoding disagree with index".into());
                }
                let mut buffer = vec![0; 1024 * 1024];
                let mut copied = 0;
                loop {
                    let count = response
                        .read(&mut buffer)
                        .map_err(|_| "EC range response read failed")?;
                    if count == 0 {
                        break;
                    }
                    copied += count as u64;
                    if copied > len {
                        return Err("EC ranged response exceeds index length".into());
                    }
                    out.write_all(&buffer[..count]).map_err(|e| e.to_string())?;
                }
                if copied != len {
                    return Err("EC ranged response is truncated".into());
                }
                Ok(())
            })();
            if result.is_ok() {
                self.bytes += len;
                return Ok(());
            }
            if attempt == self.config.request_attempts {
                return result;
            }
            std::thread::sleep(std::time::Duration::from_secs_f64(
                self.config.retry_seconds,
            ));
        }
        unreachable!()
    }
}

fn download(
    transfer: &mut Transfer,
    codes: &EcCodes,
    cycle: DateTime<Utc>,
    hour: i64,
    allow: bool,
) -> Result<Value, String> {
    let target = path(&transfer.config.cache_dir, cycle, hour);
    fs::create_dir_all(target.parent().unwrap()).map_err(|e| e.to_string())?;
    let lock = target.with_file_name(format!(
        "{}.lock",
        target.file_name().unwrap().to_string_lossy()
    ));
    let _lock = Lock::acquire(&lock, transfer.config.lock_timeout_seconds)
        .map_err(|e| e.replace("GFS", "EC"))?;
    let (hit, probe) = if regular(&target)? {
        let started = Instant::now();
        let probe = cached(codes, &transfer.config, cycle, hour, &target)?;
        transfer.header_probe_ms += started.elapsed().as_secs_f64() * 1000.0;
        (true, probe)
    } else {
        if !allow {
            return Err("EC public/basic raw bundle is missing; downloads disabled".into());
        }
        // Check metadata destination before any network work or publication.
        regular(&sidecar(&target))?;
        let parent = target.parent().unwrap();
        let mut temporary = tempfile::Builder::new()
            .prefix(".firesky-ec-bundle-")
            .tempfile_in(parent)
            .map_err(|e| e.to_string())?;
        transfer.part(cycle, hour, "pl", temporary.as_file_mut())?;
        transfer.part(cycle, hour, "cloud", temporary.as_file_mut())?;
        transfer.part(cycle, 0, "surface", temporary.as_file_mut())?;
        let started = Instant::now();
        let probe = ecmwf::probe_basic(codes, temporary.path(), cycle, hour)?;
        transfer.header_probe_ms += started.elapsed().as_secs_f64() * 1000.0;
        temporary.as_file().sync_all().map_err(|e| e.to_string())?;
        let mut meta = tempfile::Builder::new()
            .prefix(".firesky-ec-meta-")
            .tempfile_in(parent)
            .map_err(|e| e.to_string())?;
        serde_json::to_writer(&mut meta, &metadata(&transfer.config, cycle, hour))
            .map_err(|e| e.to_string())?;
        meta.as_file().sync_all().map_err(|e| e.to_string())?;
        if regular(&target)? {
            return Err("EC cache target appeared during download".into());
        }
        regular(&sidecar(&target))?;
        meta.persist(sidecar(&target))
            .map_err(|e| e.error.to_string())?;
        temporary
            .persist(&target)
            .map_err(|e| e.error.to_string())?;
        #[cfg(unix)]
        File::open(parent)
            .and_then(|f| f.sync_all())
            .map_err(|e| e.to_string())?;
        (false, probe)
    };
    Ok(
        json!({"path":target,"cache_hit":hit,"probe":probe,"identity":source_identity(&target)?,
        "forecast_hour":hour,"valid_time_utc":time_contract::iso(cycle+Duration::hours(hour))}),
    )
}

pub fn source_bracket(
    cycle: DateTime<Utc>,
    target: DateTime<Utc>,
) -> Result<(i64, i64, f64), String> {
    let offset = (target - cycle)
        .num_microseconds()
        .ok_or("EC event span overflow")? as f64
        / 3_600_000_000.0;
    if offset < -1e-6 {
        return Err("EC cycle is later than an evaluation time".into());
    }
    let offset = offset.max(0.0);
    let (base, cadence) = if offset <= 144.0 {
        (0.0, 3.0)
    } else {
        (144.0, 6.0)
    };
    let low = (base + ((offset - base) / cadence).floor() * cadence).min(360.0) as i64;
    let high = (base + ((offset - base) / cadence).ceil() * cadence).min(360.0) as i64;
    Ok((
        low,
        high,
        if low == high {
            0.0
        } else {
            ((offset - low as f64) / (high - low) as f64).clamp(0.0, 1.0)
        },
    ))
}
fn hours(cycle: DateTime<Utc>, targets: &[DateTime<Utc>]) -> Result<Vec<i64>, String> {
    let mut hours = Vec::new();
    for target in targets {
        let (a, b, _) = source_bracket(cycle, *target)?;
        hours.extend([a, b]);
    }
    hours.sort_unstable();
    hours.dedup();
    Ok(hours)
}
pub fn execute(params: Params) -> Result<Value, String> {
    let started = Instant::now();
    params.config.validate()?;
    if !["plan", "probe", "resolve", "download"].contains(&params.operation.as_str())
        || !(1..=64).contains(&params.max_cycles)
        || params.evaluation_utcs.len() > 1024
    {
        return Err("invalid EC data operation/planning limits".into());
    }
    if params
        .forecast_hour
        .is_some_and(|h| !ecmwf::published_step(h))
        || params.forecast_hours.as_ref().is_some_and(|hours| {
            hours.len() > 1024 || hours.iter().any(|h| !ecmwf::published_step(*h))
        })
    {
        return Err("EC requires published forecast hours".into());
    }
    let targets = params
        .evaluation_utcs
        .iter()
        .map(|v| time_contract::parse_utc(v))
        .collect::<Result<Vec<_>, _>>()?;
    let mut transfer = Transfer::new(params.config)?;
    let explicit = params.cycle_utc.as_deref().map(parse_cycle).transpose()?;
    let cycle = if let Some(cycle) = explicit {
        cycle
    } else {
        if targets.is_empty() {
            return Err("automatic EC cycle selection requires event evaluation times".into());
        }
        let reference = time_contract::parse_utc(
            params
                .reference_utc
                .as_deref()
                .ok_or("EC reference_utc is required")?,
        )?;
        let now = params
            .now_utc
            .as_deref()
            .map(time_contract::parse_utc)
            .transpose()?
            .unwrap_or_else(|| DateTime::<Utc>::from(std::time::SystemTime::now()));
        let reference = reference
            .min(now)
            .checked_sub_signed(Duration::hours(7))
            .ok_or("EC cycle selection underflow")?;
        let mut candidate = reference
            .date_naive()
            .and_hms_opt(if reference.hour() < 12 { 0 } else { 12 }, 0, 0)
            .unwrap()
            .and_utc();
        time_contract::checked(candidate)?;
        let mut found = None;
        for _ in 0..params.max_cycles {
            let needed = hours(candidate, &targets)?;
            let mut missing = Vec::new();
            for hour in needed {
                if !params.use_cache_for_cycle
                    || !regular(&path(&transfer.config.cache_dir, candidate, hour))?
                {
                    missing.push(hour);
                }
            }
            if missing.is_empty() || transfer.probe(candidate, *missing.last().unwrap())? {
                found = Some(candidate);
                break;
            }
            candidate = candidate
                .checked_sub_signed(Duration::hours(12))
                .ok_or("EC cycle selection underflow")?;
            time_contract::checked(candidate)?;
        }
        found.ok_or("EC has no complete cycle for requested event brackets")?
    };
    let mut requested = params.forecast_hours.unwrap_or_default();
    if let Some(hour) = params.forecast_hour {
        requested.push(hour);
    }
    requested.extend(hours(cycle, &targets)?);
    if requested.is_empty()
        || requested.len() > 1024
        || requested.iter().any(|h| !ecmwf::published_step(*h))
    {
        return Err("EC requires 1..1024 published forecast hours".into());
    }
    requested.sort_unstable();
    requested.dedup();
    for hour in &requested {
        time_contract::checked(cycle + Duration::hours(*hour))?;
    }
    let mut files = Vec::new();
    if params.operation == "probe" {
        for hour in &requested {
            files.push(json!({"forecast_hour":hour,"available":transfer.probe(cycle,*hour)?}));
        }
    } else if ["resolve", "download"].contains(&params.operation.as_str()) {
        let codes = EcCodes::load()?;
        for hour in &requested {
            files.push(download(
                &mut transfer,
                &codes,
                cycle,
                *hour,
                params.allow_download,
            )?);
        }
    } else {
        for hour in &requested {
            let target = path(&transfer.config.cache_dir, cycle, *hour);
            files.push(json!({"forecast_hour":hour,"path":target,"present_nonempty":regular(&target)?,
                "cache_contract_validated":false,"index_url":transfer.config.url(cycle,*hour,"index")?.to_string(),
                "data_url":transfer.config.url(cycle,*hour,"grib2")?.to_string(),
                "surface_index_url":transfer.config.url(cycle,0,"index")?.to_string()}));
        }
    }
    let brackets = targets
        .iter()
        .map(|target| {
            let (a, b, w) = source_bracket(cycle, *target).unwrap();
            json!({"evaluation_utc":time_contract::iso(*target),"lower":a,"upper":b,"weight":w,
            "within_public_horizon":*target>=cycle&&*target<=cycle+Duration::hours(360)})
        })
        .collect::<Vec<_>>();
    Ok(
        json!({"contract":"rust-ecmwf-data-v1","cycle_utc":time_contract::iso(cycle),
        "forecast_hours":requested,"brackets":brackets,"files":files,
        "index_requests":transfer.index_requests,"range_requests":transfer.range_requests,
        "downloaded_bytes":transfer.bytes,"elapsed_ms":started.elapsed().as_secs_f64()*1000.0,
        "timings_ms":{"index":transfer.index_ms,"range":transfer.range_ms,"header_probe":transfer.header_probe_ms},
        "index_cache":{"entries":transfer.indexes.len(),"retained_bytes_estimate":transfer.retained_index_bytes,
            "limit_bytes":MAX_RETAINED_INDEX},
        "public_forecast_complete":false,"full_catalog_download_supported":false}),
    )
}

/// Explicit snapshot identities do not inspect raw files or load ecCodes.
/// Automatic cycles retain the normal publication/cache selection policy.
pub(crate) fn snapshot_cycle(
    config: &Config,
    explicit: Option<&str>,
    anchor: DateTime<Utc>,
    now: Option<&str>,
    targets: &[DateTime<Utc>],
) -> Result<DateTime<Utc>, String> {
    config.validate()?;
    if let Some(value) = explicit {
        let cycle = parse_cycle(value)?;
        hours(cycle, targets)?;
        return Ok(cycle);
    }
    let plan = execute(Params {
        operation: "plan".into(),
        config: config.clone(),
        cycle_utc: None,
        forecast_hour: None,
        forecast_hours: None,
        reference_utc: Some(time_contract::iso(anchor)),
        now_utc: now.map(str::to_owned),
        evaluation_utcs: targets.iter().copied().map(time_contract::iso).collect(),
        max_cycles: 8,
        allow_download: false,
        use_cache_for_cycle: true,
    })?;
    time_contract::parse_utc(
        plan["cycle_utc"]
            .as_str()
            .ok_or("missing EC snapshot cycle")?,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn retained_index_is_bounded_and_oversized_entries_do_not_evict() {
        let mut entries = VecDeque::new();
        let mut bytes = 0;
        let entry = |url: &str, bytes| IndexEntry {
            url: url.into(),
            records: Arc::new(Vec::new()),
            bytes,
        };
        retain_index(&mut entries, &mut bytes, entry("a", 6), 10);
        retain_index(&mut entries, &mut bytes, entry("too-large", 11), 10);
        assert_eq!(bytes, 6);
        retain_index(&mut entries, &mut bytes, entry("b", 5), 10);
        assert_eq!(bytes, 5);
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].url, "b");
    }
}
