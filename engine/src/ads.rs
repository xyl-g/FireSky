//! Bounded ADS v1 processing client. Never attach API credentials to assets.
use crate::{cache_lock::Lock, paths, time_contract};
use chrono::{DateTime, Duration as ChronoDuration, Utc};
use reqwest::{
    blocking::{Client, Response},
    header::{HeaderMap, HeaderValue},
    Method, StatusCode, Url,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::{Duration, Instant, SystemTime},
};

pub(crate) const DATASET: &str = "cams-global-atmospheric-composition-forecasts";
const MAX_JSON: u64 = 4 * 1024 * 1024;
pub(crate) const MAX_DOWNLOAD: u64 = 512 * 1024 * 1024;
const JOB_LEDGER_CONTRACT: &str = "firesky-ads-jobs-v1";
const MAX_LEDGER_BYTES: u64 = 1024 * 1024;

fn http_failure(deadline: Instant, deadline_limited_timeout: bool) -> String {
    if deadline_limited_timeout || Instant::now() >= deadline {
        "ADS operation deadline exceeded".into()
    } else {
        "ADS HTTP request failed (URL/body redacted)".into()
    }
}

#[derive(Clone, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Config {
    pub api_url: Option<String>,
    pub credentials_file: Option<PathBuf>,
    pub timeout_seconds: f64,
    pub wait_timeout_seconds: f64,
    pub poll_initial_seconds: f64,
    pub poll_max_seconds: f64,
    pub request_attempts: u32,
    pub lock_timeout_seconds: f64,
    pub job_ledger_path: Option<PathBuf>,
    pub job_max_age_seconds: f64,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            api_url: None,
            credentials_file: None,
            timeout_seconds: 120.0,
            wait_timeout_seconds: 7200.0,
            poll_initial_seconds: 2.0,
            poll_max_seconds: 20.0,
            request_attempts: 3,
            lock_timeout_seconds: 300.0,
            job_ledger_path: None,
            job_max_age_seconds: 14400.0,
        }
    }
}
impl Config {
    pub(crate) fn validate(&self) -> Result<(), String> {
        for (value, low, high) in [
            (self.timeout_seconds, 0.001, 3600.0),
            (self.wait_timeout_seconds, 0.001, 86400.0),
            (self.poll_initial_seconds, 0.0, 120.0),
            (self.poll_max_seconds, 0.0, 120.0),
            (self.lock_timeout_seconds, 0.0, 3600.0),
            (self.job_max_age_seconds, 1.0, 7.0 * 86400.0),
        ] {
            if !value.is_finite() || value < low || value > high {
                return Err("invalid ADS timeout/poll configuration".into());
            }
        }
        if self.poll_initial_seconds > self.poll_max_seconds
            || !(1..=32).contains(&self.request_attempts)
        {
            return Err("invalid ADS retry/poll configuration".into());
        }
        if let Some(url) = &self.api_url {
            safe_url(url)?;
        }
        Ok(())
    }
}

pub(crate) fn ledger_path_for_cams_cache(cams_cache_dir: &Path) -> PathBuf {
    if cams_cache_dir.file_name() == Some(std::ffi::OsStr::new("cams")) {
        if let Some(parent) = cams_cache_dir.parent() {
            let workspace = if parent.file_name() == Some(std::ffi::OsStr::new(".cache")) {
                parent.parent().unwrap_or(parent)
            } else {
                parent
            };
            return paths::resolve(
                workspace,
                "",
                ".firesky/state/ads-jobs.json",
                "state/ads-jobs.json",
            );
        }
    }
    cams_cache_dir.join(".ads-jobs.json")
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
struct JobLedgerEntry {
    monitor_url: String,
    process_id: String,
    submitted_at_utc: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
struct JobLedger {
    contract: String,
    jobs: BTreeMap<String, JobLedgerEntry>,
}

impl Default for JobLedger {
    fn default() -> Self {
        Self {
            contract: JOB_LEDGER_CONTRACT.to_owned(),
            jobs: BTreeMap::new(),
        }
    }
}

fn canonicalize_json(value: &Value) -> Value {
    match value {
        Value::Array(items) => Value::Array(items.iter().map(canonicalize_json).collect()),
        Value::Object(map) => {
            let mut entries: Vec<_> = map.iter().collect();
            entries.sort_by(|(a, _), (b, _)| a.cmp(b));
            let mut sorted = Map::with_capacity(entries.len());
            for (k, v) in entries {
                sorted.insert(k.clone(), canonicalize_json(v));
            }
            Value::Object(sorted)
        }
        other => other.clone(),
    }
}

fn job_fingerprint(process_id: &str, inputs: &Value) -> String {
    let canonical = serde_json::to_vec(&canonicalize_json(inputs)).unwrap_or_default();
    let mut hasher = Sha256::new();
    hasher.update(process_id.as_bytes());
    hasher.update(b":");
    hasher.update(&canonical);
    hasher
        .finalize()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn read_ledger_unlocked(path: &Path) -> JobLedger {
    let Ok(meta) = fs::symlink_metadata(path) else {
        return JobLedger::default();
    };
    if meta.file_type().is_symlink() || !meta.is_file() || meta.len() > MAX_LEDGER_BYTES {
        return JobLedger::default();
    }
    let Ok(bytes) = fs::read(path) else {
        return JobLedger::default();
    };
    let Ok(ledger) = serde_json::from_slice::<JobLedger>(&bytes) else {
        return JobLedger::default();
    };
    if ledger.contract != JOB_LEDGER_CONTRACT {
        return JobLedger::default();
    }
    ledger
}

fn update_ledger<T>(
    path: &Path,
    lock_timeout: f64,
    operation: impl FnOnce(&mut JobLedger) -> T,
) -> Result<T, String> {
    if fs::symlink_metadata(path).is_ok_and(|m| m.file_type().is_symlink()) {
        return Err("ADS job ledger path cannot be a symlink".into());
    }
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .ok_or("ADS job ledger path has no parent directory")?;
    fs::create_dir_all(parent).map_err(|e| format!("cannot create ADS job ledger dir: {e}"))?;
    let lock_path = path.with_file_name(format!(
        "{}.lock",
        path.file_stem()
            .and_then(|s| s.to_str())
            .filter(|s| !s.is_empty())
            .unwrap_or("ads-jobs")
    ));
    let _lock = Lock::acquire(&lock_path, lock_timeout)?;
    let mut ledger = read_ledger_unlocked(path);
    let before = ledger.clone();
    let outcome = operation(&mut ledger);
    if ledger != before || !path.exists() {
        let bytes = serde_json::to_vec_pretty(&ledger)
            .map_err(|e| format!("cannot serialize ADS job ledger: {e}"))?;
        let mut temporary = tempfile::Builder::new()
            .prefix(".ads-jobs-")
            .tempfile_in(parent)
            .map_err(|e| format!("cannot create ADS job ledger temp file: {e}"))?;
        temporary
            .write_all(&bytes)
            .map_err(|e| format!("cannot write ADS job ledger: {e}"))?;
        let _ = temporary.as_file().sync_all();
        temporary
            .persist(path)
            .map_err(|e| format!("cannot persist ADS job ledger: {}", e.error))?;
    }
    Ok(outcome)
}

fn remove_ledger_entry(path: &Path, lock_timeout: f64, fingerprint: &str) {
    let _ = update_ledger(path, lock_timeout, |ledger| {
        ledger.jobs.remove(fingerprint);
    });
}

fn safe_url(value: &str) -> Result<Url, String> {
    let url = Url::parse(value).map_err(|_| "invalid ADS HTTP URL")?;
    let local = url.host_str().is_some_and(|host| {
        host == "localhost"
            || host
                .parse::<std::net::IpAddr>()
                .is_ok_and(|ip| ip.is_loopback())
    });
    if !(url.scheme() == "https" || url.scheme() == "http" && local)
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
        || url.host_str().is_none()
    {
        return Err(
            "ADS URLs require HTTPS (HTTP only on loopback), without credentials or fragments"
                .into(),
        );
    }
    Ok(url)
}

fn credentials(config: &Config) -> Result<(Url, HeaderValue), String> {
    let env_value = |name| std::env::var(name).ok().filter(|v| !v.trim().is_empty());
    let mut url = config.api_url.clone().or_else(|| env_value("CDSAPI_URL"));
    let mut key = env_value("CDSAPI_KEY");
    if url.is_none() || key.is_none() {
        let path = config
            .credentials_file
            .clone()
            .or_else(|| std::env::var_os("CDSAPI_RC").map(PathBuf::from))
            .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".cdsapirc")));
        if let Some(path) = path {
            if path.exists() {
                let metadata =
                    fs::metadata(&path).map_err(|_| "cannot read ADS credentials file")?;
                if !metadata.is_file() || metadata.len() > 64 * 1024 {
                    return Err("ADS credentials file must be a regular file <=64 KiB".into());
                }
                let text =
                    fs::read_to_string(path).map_err(|_| "cannot read ADS credentials file")?;
                for line in text.lines() {
                    if let Some((name, value)) = line.trim().split_once(':') {
                        match name {
                            "url" if url.is_none() => url = Some(value.trim().into()),
                            "key" if key.is_none() => key = Some(value.trim().into()),
                            "verify" if value.trim() != "1" => {
                                return Err("ADS TLS verification cannot be disabled".into())
                            }
                            _ => (),
                        }
                    }
                }
            } else if config.credentials_file.is_some() {
                return Err("ADS credentials file does not exist".into());
            }
        }
    }
    let url = safe_url(&url.ok_or("missing ADS API URL (CDSAPI_URL or credentials file)")?)?;
    if url.query().is_some() {
        return Err("ADS API base URL cannot contain a query".into());
    }
    let key = key
        .filter(|v| !v.trim().is_empty() && v.len() <= 16384)
        .ok_or("missing or invalid ADS API key (CDSAPI_KEY or credentials file)")?;
    let mut header = HeaderValue::from_str(&key).map_err(|_| "invalid ADS API key header")?;
    header.set_sensitive(true);
    Ok((url, header))
}

/// 资产下载的总时限（v1.9.6）：max(配置超时, 期望字节 / 保守吞吐)，受
/// remaining 预算约束。旧实现固定 min(remaining, 120s) 覆盖整个响应体
/// 读取，慢链路下几百 MB 的整周期资产必然读不完，三次重试全部撞同一
/// 上限后熔断（"ADS asset response read failed"）。保守吞吐取 256 KiB/s
/// ——512 MiB 资产 ≈ 34 min，慢链路可持续下载。
const ASSET_MIN_BYTES_PER_SECOND: u64 = 256 * 1024;

pub(crate) fn asset_transfer_budget(
    remaining: Duration,
    timeout_seconds: f64,
    expected: u64,
) -> Duration {
    let transfer_budget = Duration::from_secs(expected / ASSET_MIN_BYTES_PER_SECOND);
    remaining.min(Duration::from_secs_f64(timeout_seconds).max(transfer_budget))
}

pub(crate) struct Ads<'a> {
    api: Client,
    assets: Client,
    base: Url,
    config: Config,
    deadline: Instant,
    cancel: Option<Arc<AtomicBool>>,
    /// v1.9.8 进度事件 sink：队列等待（ads_queue）与资产分片读取
    /// （ads_read）事件外发，供 runtime 看门狗识别“慢而活跃”。
    progress: Option<&'a (dyn Fn(Value) -> Result<(), String> + Sync)>,
    pub constraints_requests: usize,
    pub submissions: usize,
    pub downloads: usize,
    pub constraints_ms: f64,
    pub retrieval_ms: f64,
}
impl<'a> Ads<'a> {
    pub fn new(config: Config) -> Result<Self, String> {
        Self::with_cancel(config, None)
    }
    /// Interactive variant: the flag is polled inside queue waits and asset
    /// transfers so a cancelled prepare/sync releases the operation lock in
    /// seconds instead of riding out the whole ADS job.
    pub fn with_cancel(
        config: Config,
        cancel: Option<Arc<AtomicBool>>,
    ) -> Result<Ads<'a>, String> {
        Self::with_cancel_progress(config, cancel, None)
    }

    /// 带进度 sink 的交互构造（v1.9.8）。
    pub fn with_cancel_progress(
        config: Config,
        cancel: Option<Arc<AtomicBool>>,
        progress: Option<&'a (dyn Fn(Value) -> Result<(), String> + Sync)>,
    ) -> Result<Ads<'a>, String> {
        config.validate()?;
        let (url, key) = credentials(&config)?;
        let mut headers = HeaderMap::new();
        headers.insert("PRIVATE-TOKEN", key);
        // Honor the ambient proxy environment (HTTP(S)_PROXY/ALL_PROXY/NO_PROXY)
        // like the GFS/EC clients; ADS often needs the user's proxy for reachability.
        let make = || {
            crate::http_client::builder()
                .referer(false)
                .retry(reqwest::retry::never())
                .user_agent("firesky-rust/0.1")
        };
        let api = make()
            .default_headers(headers)
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|_| "cannot initialize ADS API HTTP client")?;
        let assets = make()
            .redirect(reqwest::redirect::Policy::custom(|attempt| {
                if attempt.previous().len() >= 10 || safe_url(attempt.url().as_str()).is_err() {
                    attempt.stop()
                } else {
                    attempt.follow()
                }
            }))
            .build()
            .map_err(|_| "cannot initialize ADS asset HTTP client")?;
        let base = Url::parse(&format!(
            "{}/retrieve/v1/",
            url.as_str().trim_end_matches('/')
        ))
        .map_err(|_| "invalid ADS API base")?;
        let deadline = Instant::now() + Duration::from_secs_f64(config.wait_timeout_seconds);
        let mut result = Self {
            api,
            assets,
            base,
            config,
            deadline,
            cancel,
            progress,
            constraints_requests: 0,
            submissions: 0,
            downloads: 0,
            constraints_ms: 0.0,
            retrieval_ms: 0.0,
        };
        let process = result.process("");
        result.json(Method::GET, &process, None, true)?;
        Ok(result)
    }
    /// v1.9.8：向外发进度事件；未设置 sink 时静默。
    fn emit(&self, event: Value) -> Result<(), String> {
        match self.progress {
            Some(progress) => progress(event),
            None => Ok(()),
        }
    }

    fn check_cancelled(&self) -> Result<(), String> {
        if self
            .cancel
            .as_ref()
            .is_some_and(|flag| flag.load(Ordering::Acquire))
        {
            Err("desktop operation cancelled".into())
        } else {
            Ok(())
        }
    }
    fn remaining(&self) -> Result<Duration, String> {
        let value = self.deadline.saturating_duration_since(Instant::now());
        if value.is_zero() {
            Err("ADS operation deadline exceeded".into())
        } else {
            Ok(value)
        }
    }
    fn process(&self, suffix: &str) -> Url {
        self.base
            .join(&format!("processes/{DATASET}{suffix}"))
            .unwrap()
    }
    fn api_link(&self, from: &Url, href: &str) -> Result<Url, String> {
        let url = safe_url(
            from.join(href)
                .map_err(|_| "invalid ADS API link")?
                .as_str(),
        )?;
        if url.origin() != self.base.origin()
            || !url.path().starts_with(self.base.path())
            || url.query().is_some()
        {
            return Err("ADS API link escapes configured processing origin/path".into());
        }
        Ok(url)
    }
    fn pause(&self, seconds: f64) -> Result<(), String> {
        let budget = Duration::from_secs_f64(seconds).min(self.remaining()?);
        let started = Instant::now();
        // Sleep in short slices so a cancel request interrupts the ADS queue
        // wait instead of blocking until the job finishes.
        while started.elapsed() < budget {
            self.check_cancelled()?;
            let slice = (budget - started.elapsed()).min(Duration::from_millis(200));
            std::thread::sleep(slice);
        }
        self.check_cancelled()?;
        self.remaining().map(|_| ())
    }
    fn send_with_retry(
        &mut self,
        method: Method,
        url: &Url,
        body: Option<&Value>,
        retry: bool,
    ) -> Result<Response, String> {
        let attempts = if retry {
            self.config.request_attempts
        } else {
            1
        };
        for attempt in 1..=attempts {
            self.check_cancelled()?;
            let remaining = self.remaining()?;
            let request_timeout = Duration::from_secs_f64(self.config.timeout_seconds);
            let timeout = remaining.min(request_timeout);
            let mut request = self
                .api
                .request(method.clone(), url.clone())
                .timeout(timeout);
            if let Some(body) = body {
                request = request
                    .header("Content-Type", "application/json")
                    .body(serde_json::to_vec(body).map_err(|_| "cannot encode ADS request")?);
            }
            let response = request.send();
            let retryable = match &response {
                Ok(r) => r.status().is_server_error() || r.status() == StatusCode::TOO_MANY_REQUESTS,
                Err(_) => true,
            };
            if retryable && attempt < attempts {
                self.pause(self.config.poll_max_seconds.min(5.0))?;
                continue;
            }
            return response.map_err(|error| {
                // A budget-limited request can time out just before our Instant
                // deadline because the transport timer uses its own clock.
                http_failure(
                    self.deadline,
                    error.is_timeout() && remaining <= request_timeout,
                )
            });
        }
        unreachable!()
    }
    fn json(
        &mut self,
        method: Method,
        url: &Url,
        body: Option<&Value>,
        retry: bool,
    ) -> Result<Value, String> {
        let mut response = self.send_with_retry(method, url, body, retry)?;
        if !response.status().is_success() {
            return Err(format!(
                "ADS API HTTP status {}",
                response.status().as_u16()
            ));
        }
        read_json(&mut response)
    }
    fn poll_job_status(&mut self, url: &Url) -> PollOutcome {
        if let Err(err) = self.check_cancelled() {
            return PollOutcome::Fatal(err);
        }
        let remaining = match self.remaining() {
            Ok(value) => value,
            Err(err) => return PollOutcome::Fatal(err),
        };
        let request_timeout = Duration::from_secs_f64(self.config.timeout_seconds);
        let timeout = remaining.min(request_timeout);
        let response = self.api.get(url.clone()).timeout(timeout).send();
        let mut response = match response {
            Ok(resp) => resp,
            Err(error) => {
                if (error.is_timeout() && remaining <= request_timeout)
                    || Instant::now() >= self.deadline
                {
                    return PollOutcome::Fatal("ADS operation deadline exceeded".into());
                }
                return PollOutcome::Transient(
                    "ADS HTTP request failed (URL/body redacted)".into(),
                );
            }
        };
        let status = response.status();
        if matches!(status, StatusCode::NOT_FOUND | StatusCode::GONE) {
            return PollOutcome::NotFound;
        }
        if status.is_server_error()
            || status == StatusCode::TOO_MANY_REQUESTS
            || status == StatusCode::REQUEST_TIMEOUT
        {
            return PollOutcome::Transient(format!("ADS API HTTP status {}", status.as_u16()));
        }
        if !status.is_success() {
            return PollOutcome::Fatal(format!("ADS API HTTP status {}", status.as_u16()));
        }
        match read_json(&mut response) {
            Ok(value) => PollOutcome::State(value),
            Err(err) => PollOutcome::Transient(err),
        }
    }
    fn delete_best_effort(&self, url: &Url) {
        let Ok(remaining) = self.remaining() else {
            return;
        };
        let timeout = remaining.min(Duration::from_secs_f64(self.config.timeout_seconds));
        let _ = self.api.delete(url.clone()).timeout(timeout).send();
    }
    pub fn constraints(&mut self, inputs: &Value) -> Result<Value, String> {
        self.constraints_requests += 1;
        let started = Instant::now();
        let result = self.json(
            Method::POST,
            &self.process("/constraints"),
            Some(&json!({"inputs":inputs})),
            true,
        );
        self.constraints_ms += started.elapsed().as_secs_f64() * 1000.0;
        result
    }
    pub fn retrieve(
        &mut self,
        inputs: &Value,
        target: &mut tempfile::NamedTempFile,
    ) -> Result<(), String> {
        let started = Instant::now();
        let result = self.retrieve_inner(inputs, target);
        self.retrieval_ms += started.elapsed().as_secs_f64() * 1000.0;
        result
    }
    fn cleanup_stale_ledger(&self, ledger_path: &Path) {
        let max_age = ChronoDuration::milliseconds((self.config.job_max_age_seconds * 1000.0) as i64);
        let now = DateTime::<Utc>::from(SystemTime::now());
        let Ok(stale_urls) = update_ledger(ledger_path, self.config.lock_timeout_seconds, |ledger| {
            let stale_keys: Vec<String> = ledger
                .jobs
                .iter()
                .filter_map(|(key, entry)| {
                    let stale = time_contract::parse_utc(&entry.submitted_at_utc)
                        .map(|submitted| now.signed_duration_since(submitted) > max_age)
                        .unwrap_or(true);
                    stale.then(|| key.clone())
                })
                .collect();
            let mut urls = Vec::with_capacity(stale_keys.len());
            for key in stale_keys {
                if let Some(entry) = ledger.jobs.remove(&key) {
                    urls.push(entry.monitor_url);
                }
            }
            urls
        }) else {
            return;
        };
        for raw_url in stale_urls {
            if self.check_cancelled().is_err() {
                break;
            }
            if let Ok(url) = self.api_link(&self.base, &raw_url) {
                self.delete_best_effort(&url);
            }
        }
    }
    fn retrieve_inner(
        &mut self,
        inputs: &Value,
        target: &mut tempfile::NamedTempFile,
    ) -> Result<(), String> {
        let ledger_path = self.config.job_ledger_path.clone();
        let lock_timeout = self.config.lock_timeout_seconds;
        let fingerprint = job_fingerprint(DATASET, inputs);
        if let Some(path) = ledger_path.as_deref() {
            self.cleanup_stale_ledger(path);
        }

        let mut monitor: Option<Url> = None;
        let mut initial_state: Option<Value> = None;
        let mut reattached = false;

        if let Some(path) = ledger_path.as_deref() {
            let recorded = update_ledger(path, lock_timeout, |ledger| {
                ledger.jobs.get(&fingerprint).cloned()
            })
            .ok()
            .flatten();
            if let Some(entry) = recorded {
                self.check_cancelled()?;
                match self.api_link(&self.base, &entry.monitor_url) {
                    Ok(candidate) => match self.poll_job_status(&candidate) {
                        PollOutcome::State(state) => match state["status"].as_str() {
                            Some("successful") => {
                                monitor = Some(candidate);
                                initial_state = Some(state);
                                reattached = true;
                            }
                            Some("accepted" | "running") => {
                                monitor = Some(candidate);
                                reattached = true;
                            }
                            _ => remove_ledger_entry(path, lock_timeout, &fingerprint),
                        },
                        PollOutcome::NotFound => {
                            remove_ledger_entry(path, lock_timeout, &fingerprint);
                        }
                        PollOutcome::Transient(_) => {
                            // Keep the ledger entry and enter the resilient polling loop instead of
                            // failing or submitting a duplicate job.
                            monitor = Some(candidate);
                            reattached = true;
                        }
                        PollOutcome::Fatal(err) => return Err(err),
                    },
                    Err(_) => remove_ledger_entry(path, lock_timeout, &fingerprint),
                }
            }
        }

        let monitor = match monitor {
            Some(url) => url,
            None => {
                let execution = self.process("/execution");
                self.submissions += 1;
                // Retrying an ambiguous submission can create/bill duplicate jobs.
                let job = self.json(
                    Method::POST,
                    &execution,
                    Some(&json!({"inputs":inputs})),
                    false,
                )?;
                let url = self.api_link(
                    &execution,
                    link(&job, "monitor").ok_or("ADS submission has no monitor link")?,
                )?;
                if let Some(path) = ledger_path.as_deref() {
                    let entry = JobLedgerEntry {
                        monitor_url: url.as_str().to_owned(),
                        process_id: DATASET.to_owned(),
                        submitted_at_utc: time_contract::iso(DateTime::<Utc>::from(
                            SystemTime::now(),
                        )),
                    };
                    let _ = update_ledger(path, lock_timeout, |ledger| {
                        ledger.jobs.insert(fingerprint.clone(), entry);
                    });
                }
                url
            }
        };

        let mut delay = self.config.poll_initial_seconds;
        let base_retry_delay = self
            .config
            .poll_max_seconds
            .max(self.config.poll_initial_seconds);
        let mut retry_delay = base_retry_delay;
        let retry_max_delay = (base_retry_delay * 3.0).min(60.0);
        let mut consecutive_transient_errors: u32 = 0;
        let queued_at = Instant::now();
        let state = if let Some(state) = initial_state {
            self.emit(json!({
                "stage": "ads_queue",
                "status": state["status"],
                "waited_s": 0.0,
                "reattached": true,
            }))?;
            state
        } else {
            loop {
                self.check_cancelled()?;
                match self.poll_job_status(&monitor) {
                    PollOutcome::State(state) => {
                        consecutive_transient_errors = 0;
                        retry_delay = base_retry_delay;
                        // v1.9.8：排队等待事件化——ADS 排队占 CAMS 准备大头，让
                        // 「慢」可见并维持 runtime 看门狗的活跃心跳。
                        self.emit(json!({
                            "stage": "ads_queue",
                            "status": state["status"],
                            "waited_s": queued_at.elapsed().as_secs_f64(),
                            "reattached": reattached,
                        }))?;
                        match state["status"].as_str() {
                            Some("successful") => break state,
                            Some("accepted" | "running") => {
                                self.pause(delay)?;
                                delay = (delay * 1.5).min(self.config.poll_max_seconds);
                            }
                            Some("failed" | "rejected" | "dismissed" | "deleted") => {
                                if let Some(path) = ledger_path.as_deref() {
                                    remove_ledger_entry(path, lock_timeout, &fingerprint);
                                }
                                return Err(format!(
                                    "ADS job ended with status {}",
                                    state["status"].as_str().unwrap()
                                ));
                            }
                            _ => {
                                if let Some(path) = ledger_path.as_deref() {
                                    remove_ledger_entry(path, lock_timeout, &fingerprint);
                                }
                                return Err("ADS job has an unknown or missing status".into());
                            }
                        }
                    }
                    PollOutcome::Transient(err) => {
                        consecutive_transient_errors += 1;
                        self.emit(json!({
                            "stage": "ads_queue",
                            "status": "retrying",
                            "waited_s": queued_at.elapsed().as_secs_f64(),
                            "reattached": reattached,
                            "retry_error": err,
                            "consecutive_errors": consecutive_transient_errors,
                        }))?;
                        if let Err(pause_err) = self.pause(retry_delay) {
                            if pause_err.contains("ADS operation deadline exceeded") {
                                return Err(format!(
                                    "ADS operation deadline exceeded (last poll error: {err})"
                                ));
                            }
                            return Err(pause_err);
                        }
                        retry_delay = (retry_delay * 1.5).min(retry_max_delay);
                    }
                    PollOutcome::NotFound => {
                        if let Some(path) = ledger_path.as_deref() {
                            remove_ledger_entry(path, lock_timeout, &fingerprint);
                        }
                        return Err(
                            "ADS job was removed or expired on server (HTTP 404/410)".into(),
                        );
                    }
                    PollOutcome::Fatal(err) => return Err(err),
                }
            }
        };
        let results = match link(&state, "results") {
            Some(href) => self.api_link(&monitor, href)?,
            None => self.api_link(&monitor, &format!("{}/results", monitor.path()))?,
        };
        let response = self.json(Method::GET, &results, None, true)?;
        let asset = &response["asset"]["value"];
        let href = asset["href"]
            .as_str()
            .ok_or("ADS result has no asset URL")?;
        let url = safe_url(
            results
                .join(href)
                .map_err(|_| "invalid ADS asset URL")?
                .as_str(),
        )?;
        let bytes = asset["file:size"]
            .as_u64()
            .or_else(|| asset["file:size"].as_str()?.parse().ok())
            .filter(|b| (16..=MAX_DOWNLOAD).contains(b))
            .ok_or("invalid ADS asset size (16 bytes..512 MiB)")?;
        self.download(&url, bytes, target)?;
        if let Some(path) = ledger_path.as_deref() {
            remove_ledger_entry(path, lock_timeout, &fingerprint);
        }
        self.downloads += 1;
        Ok(())
    }
    fn download(
        &self,
        url: &Url,
        expected: u64,
        target: &mut tempfile::NamedTempFile,
    ) -> Result<(), String> {
        use std::io::{Seek, SeekFrom};
        for attempt in 1..=self.config.request_attempts {
            // v1.9.7 断点续传：重试不再清空临时文件——从上一轮断点
            // (Range: bytes=N-) 续传剩余部分；服务端忽略 Range 返回 200
            // 时退回全量重下。几百 MB 资产失败重试不再从头下载。
            let resume_from = target
                .as_file()
                .metadata()
                .map(|meta| meta.len())
                .unwrap_or(0)
                .min(expected);
            let result = (|| {
                // v1.9.6：总时限按资产大小自适应（asset_transfer_budget）——旧
                // 实现固定 min(remaining, 120s) 覆盖整个响应体读取，慢链路下
                // 几百 MB 的整周期资产必然读不完，三次重试全部撞同一上限后熔断。
                let timeout = asset_transfer_budget(
                    self.remaining()?,
                    self.config.timeout_seconds,
                    expected - resume_from,
                );
                let mut request = self
                    .assets
                    .get(url.clone())
                    .header("Accept-Encoding", "identity")
                    .timeout(timeout);
                if resume_from > 0 {
                    request = request.header("Range", format!("bytes={resume_from}-"));
                }
                let mut response = request
                    .send()
                    .map_err(|_| "ADS asset request failed (URL redacted)")?;
                let mut offset = resume_from;
                if resume_from > 0 {
                    if response.status() == reqwest::StatusCode::PARTIAL_CONTENT {
                        // Content-Range 起点必须与断点一致，否则视为服务端
                        // 内容已变化，退回全量（fail-closed）。
                        let content_range = response
                            .headers()
                            .get(reqwest::header::CONTENT_RANGE)
                            .and_then(|value| value.to_str().ok())
                            .unwrap_or("")
                            .to_owned();
                        if !content_range.starts_with(&format!("bytes {resume_from}-")) {
                            return Err(format!(
                                "ADS asset Content-Range mismatch ({content_range})"
                            ));
                        }
                    } else if response.status() == reqwest::StatusCode::OK {
                        // 服务端忽略 Range：清空断点，退回全量重下。
                        offset = 0;
                        target
                            .as_file_mut()
                            .set_len(0)
                            .map_err(|_| "cannot reset ADS temporary file")?;
                    } else {
                        return Err(format!(
                            "ADS asset HTTP status {}",
                            response.status().as_u16()
                        ));
                    }
                } else if !response.status().is_success() {
                    return Err(format!(
                        "ADS asset HTTP status {}",
                        response.status().as_u16()
                    ));
                }
                let remaining = expected - offset;
                if response.content_length().is_some_and(|n| n != remaining) {
                    return Err("ADS asset Content-Length disagrees with result size".into());
                }
                target
                    .as_file_mut()
                    .seek(SeekFrom::Start(offset))
                    .map_err(|_| "cannot seek ADS temporary file")?;
                let mut buffer = vec![0; 1024 * 1024];
                let mut bytes = 0_u64;
                // v1.9.8：每 32 MiB 发一次读取事件（慢链路大资产的
                // “慢而活跃”心跳，防止被空闲看门狗误杀）。
                const READ_EVENT_CHUNK: u64 = 32 * 1024 * 1024;
                let mut next_event_at = READ_EVENT_CHUNK;
                loop {
                    self.check_cancelled()?;
                    let count = response.read(&mut buffer).map_err(|error| {
                        format!("ADS asset response read failed ({error})")
                    })?;
                    if count == 0 {
                        break;
                    }
                    bytes += count as u64;
                    if bytes > remaining {
                        return Err("ADS asset exceeds declared size".into());
                    }
                    if bytes >= next_event_at {
                        next_event_at = bytes + READ_EVENT_CHUNK;
                        self.emit(json!({
                            "stage": "ads_read",
                            "bytes": offset + bytes,
                            "expected": expected,
                        }))?;
                    }
                    target
                        .write_all(&buffer[..count])
                        .map_err(|_| "cannot write ADS temporary file")?;
                }
                if bytes != remaining {
                    return Err("ADS asset is truncated".into());
                }
                target
                    .as_file_mut()
                    .sync_all()
                    .map_err(|_| "cannot sync ADS temporary file")?;
                Ok(())
            })();
            if result.is_ok() || attempt == self.config.request_attempts {
                return result;
            }
            self.pause(self.config.poll_max_seconds.min(5.0))?;
        }
        unreachable!()
    }
}

enum PollOutcome {
    State(Value),
    NotFound,
    Transient(String),
    Fatal(String),
}

fn read_json(response: &mut Response) -> Result<Value, String> {
    if response.content_length().is_some_and(|n| n > MAX_JSON) {
        return Err("ADS JSON response exceeds 4 MiB".into());
    }
    let mut bytes = Vec::new();
    response
        .take(MAX_JSON + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "ADS JSON response read failed")?;
    if bytes.len() as u64 > MAX_JSON {
        return Err("ADS JSON response exceeds 4 MiB".into());
    }
    serde_json::from_slice(&bytes).map_err(|_| "invalid ADS JSON response (body redacted)".into())
}
fn link<'a>(value: &'a Value, rel: &str) -> Option<&'a str> {
    value["links"]
        .as_array()?
        .iter()
        .find(|v| v["rel"] == rel)?["href"]
        .as_str()
}

pub(crate) fn resolve_credentials_path(config: &mut Config, base: &Path) {
    if let Some(path) = &mut config.credentials_file {
        if path.is_relative() {
            *path = base.join(&path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    #[test]
    fn cancelled_pause_returns_promptly() {
        let cancel = Arc::new(AtomicBool::new(false));
        let ads = Ads {
            api: Client::new(),
            assets: Client::new(),
            base: Url::parse("https://ads.example.invalid/retrieve/v1/").unwrap(),
            config: Config::default(),
            deadline: Instant::now() + Duration::from_secs(60),
            cancel: Some(cancel.clone()),
            constraints_requests: 0,
            submissions: 0,
            downloads: 0,
            constraints_ms: 0.0,
            retrieval_ms: 0.0,
            progress: None,
        };
        cancel.store(true, Ordering::Release);
        let started = Instant::now();
        let error = ads.pause(30.0).unwrap_err();
        assert!(error.contains("cancelled"), "{error}");
        assert!(started.elapsed() < Duration::from_secs(2));
    }

    #[test]
    fn operation_budget_timeout_is_not_a_generic_network_failure() {
        let future = Instant::now() + Duration::from_secs(60);
        assert_eq!(
            http_failure(future, true),
            "ADS operation deadline exceeded"
        );
        assert_eq!(
            http_failure(future, false),
            "ADS HTTP request failed (URL/body redacted)"
        );
        assert_eq!(
            http_failure(Instant::now() - Duration::from_secs(1), false),
            "ADS operation deadline exceeded"
        );
    }

    struct MockAds {
        base_url: String,
        stop: Arc<AtomicBool>,
        requests: Arc<std::sync::Mutex<Vec<(String, String)>>>,
        handle: Option<std::thread::JoinHandle<()>>,
    }

    impl MockAds {
        fn start<F>(handler: F) -> Self
        where
            F: Fn(&str, &str, usize) -> (u16, &'static str, Vec<u8>) + Send + Sync + 'static,
        {
            use std::io::{BufRead, BufReader};
            use std::net::TcpListener;
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            listener.set_nonblocking(true).unwrap();
            let addr = listener.local_addr().unwrap();
            let stop = Arc::new(AtomicBool::new(false));
            let requests = Arc::new(std::sync::Mutex::new(Vec::new()));
            let stop_flag = stop.clone();
            let req_log = requests.clone();
            let handle = std::thread::spawn(move || {
                let mut seq = 0usize;
                while !stop_flag.load(Ordering::Acquire) {
                    match listener.accept() {
                        Ok((mut stream, _)) => {
                            let _ = stream.set_nonblocking(false);
                            let mut reader = BufReader::new(stream.try_clone().unwrap());
                            let mut line = String::new();
                            if reader.read_line(&mut line).unwrap_or(0) == 0 {
                                continue;
                            }
                            let mut parts = line.split_whitespace();
                            let method = parts.next().unwrap_or("").to_owned();
                            let path = parts.next().unwrap_or("").to_owned();
                            let mut content_len = 0usize;
                            loop {
                                let mut hdr = String::new();
                                if reader.read_line(&mut hdr).unwrap_or(0) == 0 {
                                    break;
                                }
                                let trimmed = hdr.trim();
                                if trimmed.is_empty() {
                                    break;
                                }
                                if let Some((k, v)) = trimmed.split_once(':') {
                                    if k.eq_ignore_ascii_case("content-length") {
                                        content_len = v.trim().parse().unwrap_or(0);
                                    }
                                }
                            }
                            if content_len > 0 {
                                let mut discard = vec![0u8; content_len];
                                let _ = reader.read_exact(&mut discard);
                            }
                            seq += 1;
                            req_log.lock().unwrap().push((method.clone(), path.clone()));
                            let (code, ctype, body) = handler(&method, &path, seq);
                            let reason = match code {
                                200 => "OK",
                                202 => "Accepted",
                                404 => "Not Found",
                                _ => "Error",
                            };
                            let resp_head = format!(
                                "HTTP/1.1 {code} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                                body.len()
                            );
                            let _ = stream.write_all(resp_head.as_bytes());
                            let _ = stream.write_all(&body);
                            let _ = stream.flush();
                        }
                        Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                            std::thread::sleep(Duration::from_millis(10));
                        }
                        Err(_) => break,
                    }
                }
            });
            Self {
                base_url: format!("http://127.0.0.1:{}", addr.port()),
                stop,
                requests,
                handle: Some(handle),
            }
        }
    }

    impl Drop for MockAds {
        fn drop(&mut self) {
            self.stop.store(true, Ordering::Release);
            if let Some(h) = self.handle.take() {
                let _ = h.join();
            }
        }
    }

    fn test_config(base_url: &str, ledger_path: PathBuf) -> (tempfile::NamedTempFile, Config) {
        let mut rc = tempfile::NamedTempFile::new().unwrap();
        writeln!(rc, "url: {base_url}\nkey: test-ads-key").unwrap();
        let config = Config {
            api_url: Some(base_url.to_owned()),
            credentials_file: Some(rc.path().to_path_buf()),
            timeout_seconds: 5.0,
            wait_timeout_seconds: 15.0,
            poll_initial_seconds: 0.02,
            poll_max_seconds: 0.05,
            request_attempts: 1,
            lock_timeout_seconds: 2.0,
            job_ledger_path: Some(ledger_path),
            job_max_age_seconds: 3600.0,
        };
        (rc, config)
    }

    #[test]
    fn job_fingerprint_is_canonical_across_key_order() {
        let a = json!({
            "date": ["2026-11-24"],
            "area": [81.0, 26.0, -11.0, 180.0],
            "nested": {"b": 2, "a": 1}
        });
        let b = json!({
            "nested": {"a": 1, "b": 2},
            "area": [81.0, 26.0, -11.0, 180.0],
            "date": ["2026-11-24"]
        });
        let c = json!({
            "nested": {"a": 1, "b": 3},
            "area": [81.0, 26.0, -11.0, 180.0],
            "date": ["2026-11-24"]
        });
        assert_eq!(job_fingerprint(DATASET, &a), job_fingerprint(DATASET, &b));
        assert_ne!(job_fingerprint(DATASET, &a), job_fingerprint(DATASET, &c));
    }

    #[test]
    fn cancel_preserves_ledger_and_next_retrieve_reattaches_without_duplicate_post() {
        let dir = tempfile::tempdir().unwrap();
        let ledger = dir.path().join("state/ads-jobs.json");
        let job_polls = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let polls_srv = job_polls.clone();
        let server = MockAds::start(move |method, path, _| {
            if method == "GET" && path == format!("/retrieve/v1/processes/{DATASET}") {
                return (200, "application/json", br#"{"id":"cams"}"#.to_vec());
            }
            if method == "POST" && path == format!("/retrieve/v1/processes/{DATASET}/execution") {
                return (
                    202,
                    "application/json",
                    br#"{"links":[{"rel":"monitor","href":"/retrieve/v1/jobs/job-1"}]}"#.to_vec(),
                );
            }
            if method == "GET" && path == "/retrieve/v1/jobs/job-1" {
                let n = polls_srv.fetch_add(1, Ordering::SeqCst);
                if n == 0 {
                    return (
                        200,
                        "application/json",
                        br#"{"status":"accepted"}"#.to_vec(),
                    );
                }
                return (
                    200,
                    "application/json",
                    br#"{"status":"successful","links":[{"rel":"results","href":"/retrieve/v1/jobs/job-1/results"}]}"#.to_vec(),
                );
            }
            if method == "GET" && path == "/retrieve/v1/jobs/job-1/results" {
                return (
                    200,
                    "application/json",
                    br#"{"asset":{"value":{"href":"/retrieve/v1/assets/out.grib","file:size":16}}}"#.to_vec(),
                );
            }
            if method == "GET" && path == "/retrieve/v1/assets/out.grib" {
                return (200, "application/x-grib", b"0123456789abcdef".to_vec());
            }
            (404, "application/json", br#"{}"#.to_vec())
        });

        let (_rc, cfg) = test_config(&server.base_url, ledger.clone());
        let inputs = json!({"date":["2026-11-24"],"time":["12:00"]});
        let cancel = Arc::new(AtomicBool::new(false));
        let cancel_sink = cancel.clone();
        let mut out1 = tempfile::NamedTempFile::new().unwrap();
        let err = {
            let sink = |ev: Value| -> Result<(), String> {
                if ev["stage"] == "ads_queue" {
                    cancel_sink.store(true, Ordering::Release);
                }
                Ok(())
            };
            let mut ads = Ads::with_cancel_progress(cfg.clone(), Some(cancel.clone()), Some(&sink)).unwrap();
            ads.retrieve(&inputs, &mut out1).unwrap_err()
        };
        assert!(err.contains("cancelled"), "{err}");
        let saved = read_ledger_unlocked(&ledger);
        assert_eq!(saved.jobs.len(), 1);

        let events = std::sync::Mutex::new(Vec::<Value>::new());
        let mut out2 = tempfile::NamedTempFile::new().unwrap();
        {
            let sink = |ev: Value| -> Result<(), String> {
                events.lock().unwrap().push(ev);
                Ok(())
            };
            let mut ads = Ads::with_cancel_progress(cfg, None, Some(&sink)).unwrap();
            ads.retrieve(&inputs, &mut out2).unwrap();
            assert_eq!(ads.submissions, 0);
            assert_eq!(ads.downloads, 1);
        }
        assert_eq!(std::fs::read(out2.path()).unwrap(), b"0123456789abcdef");
        assert!(read_ledger_unlocked(&ledger).jobs.is_empty());
        let evs = events.into_inner().unwrap();
        assert!(evs
            .iter()
            .any(|e| e["stage"] == "ads_queue" && e["reattached"] == json!(true)));
        let reqs = server.requests.lock().unwrap();
        let post_count = reqs
            .iter()
            .filter(|(m, p)| m == "POST" && p.ends_with("/execution"))
            .count();
        assert_eq!(post_count, 1);
    }

    #[test]
    fn reattach_to_already_successful_job_skips_post_and_downloads() {
        let dir = tempfile::tempdir().unwrap();
        let ledger = dir.path().join("ads-jobs.json");
        let server = MockAds::start(move |method, path, _| {
            if method == "GET" && path == format!("/retrieve/v1/processes/{DATASET}") {
                return (200, "application/json", br#"{"id":"cams"}"#.to_vec());
            }
            if method == "GET" && path == "/retrieve/v1/jobs/job-done" {
                return (
                    200,
                    "application/json",
                    br#"{"status":"successful","links":[{"rel":"results","href":"/retrieve/v1/jobs/job-done/results"}]}"#.to_vec(),
                );
            }
            if method == "GET" && path == "/retrieve/v1/jobs/job-done/results" {
                return (
                    200,
                    "application/json",
                    br#"{"asset":{"value":{"href":"/retrieve/v1/assets/done.grib","file:size":16}}}"#.to_vec(),
                );
            }
            if method == "GET" && path == "/retrieve/v1/assets/done.grib" {
                return (200, "application/x-grib", b"abcdef0123456789".to_vec());
            }
            (404, "application/json", br#"{}"#.to_vec())
        });
        let (_rc, cfg) = test_config(&server.base_url, ledger.clone());
        let inputs = json!({"date":["2026-11-24"]});
        let fp = job_fingerprint(DATASET, &inputs);
        update_ledger(&ledger, 2.0, |l| {
            l.jobs.insert(
                fp,
                JobLedgerEntry {
                    monitor_url: format!("{}/retrieve/v1/jobs/job-done", server.base_url),
                    process_id: DATASET.into(),
                    submitted_at_utc: time_contract::iso(DateTime::<Utc>::from(SystemTime::now())),
                },
            );
        })
        .unwrap();

        let mut ads = Ads::new(cfg).unwrap();
        let mut out = tempfile::NamedTempFile::new().unwrap();
        ads.retrieve(&inputs, &mut out).unwrap();
        assert_eq!(ads.submissions, 0);
        assert_eq!(ads.downloads, 1);
        assert_eq!(std::fs::read(out.path()).unwrap(), b"abcdef0123456789");
        assert!(read_ledger_unlocked(&ledger).jobs.is_empty());
    }

    #[test]
    fn terminal_failed_ledger_entry_is_dropped_and_resubmitted() {
        let dir = tempfile::tempdir().unwrap();
        let ledger = dir.path().join("ads-jobs.json");
        let server = MockAds::start(move |method, path, _| {
            if method == "GET" && path == format!("/retrieve/v1/processes/{DATASET}") {
                return (200, "application/json", br#"{"id":"cams"}"#.to_vec());
            }
            if method == "GET" && path == "/retrieve/v1/jobs/job-bad" {
                return (200, "application/json", br#"{"status":"failed"}"#.to_vec());
            }
            if method == "POST" && path == format!("/retrieve/v1/processes/{DATASET}/execution") {
                return (
                    202,
                    "application/json",
                    br#"{"links":[{"rel":"monitor","href":"/retrieve/v1/jobs/job-new"}]}"#.to_vec(),
                );
            }
            if method == "GET" && path == "/retrieve/v1/jobs/job-new" {
                return (
                    200,
                    "application/json",
                    br#"{"status":"successful","links":[{"rel":"results","href":"/retrieve/v1/jobs/job-new/results"}]}"#.to_vec(),
                );
            }
            if method == "GET" && path == "/retrieve/v1/jobs/job-new/results" {
                return (
                    200,
                    "application/json",
                    br#"{"asset":{"value":{"href":"/retrieve/v1/assets/new.grib","file:size":16}}}"#.to_vec(),
                );
            }
            if method == "GET" && path == "/retrieve/v1/assets/new.grib" {
                return (200, "application/x-grib", b"newjob0123456789".to_vec());
            }
            (404, "application/json", br#"{}"#.to_vec())
        });
        let (_rc, cfg) = test_config(&server.base_url, ledger.clone());
        let inputs = json!({"date":["2026-11-24"]});
        let fp = job_fingerprint(DATASET, &inputs);
        update_ledger(&ledger, 2.0, |l| {
            l.jobs.insert(
                fp,
                JobLedgerEntry {
                    monitor_url: "/retrieve/v1/jobs/job-bad".into(),
                    process_id: DATASET.into(),
                    submitted_at_utc: time_contract::iso(DateTime::<Utc>::from(SystemTime::now())),
                },
            );
        })
        .unwrap();

        let mut ads = Ads::new(cfg).unwrap();
        let mut out = tempfile::NamedTempFile::new().unwrap();
        ads.retrieve(&inputs, &mut out).unwrap();
        assert_eq!(ads.submissions, 1);
        assert_eq!(std::fs::read(out.path()).unwrap(), b"newjob0123456789");
        assert!(read_ledger_unlocked(&ledger).jobs.is_empty());
    }

    #[test]
    fn stale_ledger_entry_triggers_best_effort_delete_and_removal() {
        let dir = tempfile::tempdir().unwrap();
        let ledger = dir.path().join("ads-jobs.json");
        let server = MockAds::start(move |method, path, _| {
            if method == "GET" && path == format!("/retrieve/v1/processes/{DATASET}") {
                return (200, "application/json", br#"{"id":"cams"}"#.to_vec());
            }
            if method == "DELETE" && path == "/retrieve/v1/jobs/job-stale" {
                return (200, "application/json", br#"{}"#.to_vec());
            }
            if method == "POST" && path == format!("/retrieve/v1/processes/{DATASET}/execution") {
                return (
                    202,
                    "application/json",
                    br#"{"links":[{"rel":"monitor","href":"/retrieve/v1/jobs/job-fresh"}]}"#.to_vec(),
                );
            }
            if method == "GET" && path == "/retrieve/v1/jobs/job-fresh" {
                return (
                    200,
                    "application/json",
                    br#"{"status":"successful","links":[{"rel":"results","href":"/retrieve/v1/jobs/job-fresh/results"}]}"#.to_vec(),
                );
            }
            if method == "GET" && path == "/retrieve/v1/jobs/job-fresh/results" {
                return (
                    200,
                    "application/json",
                    br#"{"asset":{"value":{"href":"/retrieve/v1/assets/fresh.grib","file:size":16}}}"#.to_vec(),
                );
            }
            if method == "GET" && path == "/retrieve/v1/assets/fresh.grib" {
                return (200, "application/x-grib", b"fresh00123456789".to_vec());
            }
            (404, "application/json", br#"{}"#.to_vec())
        });
        let (_rc, cfg) = test_config(&server.base_url, ledger.clone());
        let old_time = DateTime::<Utc>::from(SystemTime::now()) - ChronoDuration::hours(48);
        update_ledger(&ledger, 2.0, |l| {
            l.jobs.insert(
                "stale-fingerprint".into(),
                JobLedgerEntry {
                    monitor_url: "/retrieve/v1/jobs/job-stale".into(),
                    process_id: DATASET.into(),
                    submitted_at_utc: time_contract::iso(old_time),
                },
            );
        })
        .unwrap();

        let inputs = json!({"date":["2026-11-25"]});
        let mut ads = Ads::new(cfg).unwrap();
        let mut out = tempfile::NamedTempFile::new().unwrap();
        ads.retrieve(&inputs, &mut out).unwrap();
        assert_eq!(ads.submissions, 1);
        assert!(read_ledger_unlocked(&ledger).jobs.is_empty());
        let reqs = server.requests.lock().unwrap();
        assert!(reqs
            .iter()
            .any(|(m, p)| m == "DELETE" && p == "/retrieve/v1/jobs/job-stale"));
    }

    #[test]
    fn poll_survives_transient_500_and_429_burst_until_job_succeeds() {
        let dir = tempfile::tempdir().unwrap();
        let ledger = dir.path().join("ads-jobs.json");
        let job_polls = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let polls_srv = job_polls.clone();
        let server = MockAds::start(move |method, path, _| {
            if method == "GET" && path == format!("/retrieve/v1/processes/{DATASET}") {
                return (200, "application/json", br#"{"id":"cams"}"#.to_vec());
            }
            if method == "POST" && path == format!("/retrieve/v1/processes/{DATASET}/execution") {
                return (
                    202,
                    "application/json",
                    br#"{"links":[{"rel":"monitor","href":"/retrieve/v1/jobs/job-flaky"}]}"#.to_vec(),
                );
            }
            if method == "GET" && path == "/retrieve/v1/jobs/job-flaky" {
                let n = polls_srv.fetch_add(1, Ordering::SeqCst);
                return match n {
                    0 | 1 => (500, "application/json", br#"{"detail":"err"}"#.to_vec()),
                    2 => (502, "application/json", br#"{"detail":"bad gw"}"#.to_vec()),
                    3 => (429, "application/json", br#"{"detail":"slow"}"#.to_vec()),
                    4 => (500, "application/json", br#"{"detail":"err"}"#.to_vec()),
                    5 => (503, "application/json", br#"{"detail":"unavail"}"#.to_vec()),
                    6 => (200, "application/json", br#"{"status":"running"}"#.to_vec()),
                    _ => (
                        200,
                        "application/json",
                        br#"{"status":"successful","links":[{"rel":"results","href":"/retrieve/v1/jobs/job-flaky/results"}]}"#.to_vec(),
                    ),
                };
            }
            if method == "GET" && path == "/retrieve/v1/jobs/job-flaky/results" {
                return (
                    200,
                    "application/json",
                    br#"{"asset":{"value":{"href":"/retrieve/v1/assets/flaky.grib","file:size":16}}}"#.to_vec(),
                );
            }
            if method == "GET" && path == "/retrieve/v1/assets/flaky.grib" {
                return (200, "application/x-grib", b"flaky0123456789a".to_vec());
            }
            (404, "application/json", br#"{}"#.to_vec())
        });

        let (_rc, cfg) = test_config(&server.base_url, ledger.clone());
        let events = std::sync::Mutex::new(Vec::<Value>::new());
        let mut out = tempfile::NamedTempFile::new().unwrap();
        {
            let sink = |ev: Value| -> Result<(), String> {
                events.lock().unwrap().push(ev);
                Ok(())
            };
            let mut ads = Ads::with_cancel_progress(cfg, None, Some(&sink)).unwrap();
            let inputs = json!({"date":["2026-11-25"]});
            ads.retrieve(&inputs, &mut out).unwrap();
            assert_eq!(ads.submissions, 1);
            assert_eq!(ads.downloads, 1);
        }
        assert_eq!(std::fs::read(out.path()).unwrap(), b"flaky0123456789a");
        assert!(read_ledger_unlocked(&ledger).jobs.is_empty());
        let evs = events.into_inner().unwrap();
        assert!(evs
            .iter()
            .any(|e| e["stage"] == "ads_queue" && e["status"] == json!("retrying")));
    }

    #[test]
    fn poll_fails_immediately_on_non_retryable_403() {
        let dir = tempfile::tempdir().unwrap();
        let ledger = dir.path().join("ads-jobs.json");
        let server = MockAds::start(move |method, path, _| {
            if method == "GET" && path == format!("/retrieve/v1/processes/{DATASET}") {
                return (200, "application/json", br#"{"id":"cams"}"#.to_vec());
            }
            if method == "POST" && path == format!("/retrieve/v1/processes/{DATASET}/execution") {
                return (
                    202,
                    "application/json",
                    br#"{"links":[{"rel":"monitor","href":"/retrieve/v1/jobs/job-403"}]}"#.to_vec(),
                );
            }
            if method == "GET" && path == "/retrieve/v1/jobs/job-403" {
                return (403, "application/json", br#"{"detail":"forbidden"}"#.to_vec());
            }
            (404, "application/json", br#"{}"#.to_vec())
        });

        let (_rc, cfg) = test_config(&server.base_url, ledger);
        let mut ads = Ads::new(cfg).unwrap();
        let mut out = tempfile::NamedTempFile::new().unwrap();
        let inputs = json!({"date":["2026-11-25"]});
        let err = ads.retrieve(&inputs, &mut out).unwrap_err();
        assert!(err.contains("ADS API HTTP status 403"), "{err}");
        let reqs = server.requests.lock().unwrap();
        let poll_count = reqs
            .iter()
            .filter(|(m, p)| m == "GET" && p == "/retrieve/v1/jobs/job-403")
            .count();
        assert_eq!(poll_count, 1);
    }

    #[test]
    fn reattach_survives_transient_500_without_duplicate_submission() {
        let dir = tempfile::tempdir().unwrap();
        let ledger = dir.path().join("ads-jobs.json");
        let job_polls = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let polls_srv = job_polls.clone();
        let server = MockAds::start(move |method, path, _| {
            if method == "GET" && path == format!("/retrieve/v1/processes/{DATASET}") {
                return (200, "application/json", br#"{"id":"cams"}"#.to_vec());
            }
            if method == "GET" && path == "/retrieve/v1/jobs/job-reattach" {
                let n = polls_srv.fetch_add(1, Ordering::SeqCst);
                if n < 2 {
                    return (500, "application/json", br#"{"detail":"transient"}"#.to_vec());
                }
                return (
                    200,
                    "application/json",
                    br#"{"status":"successful","links":[{"rel":"results","href":"/retrieve/v1/jobs/job-reattach/results"}]}"#.to_vec(),
                );
            }
            if method == "GET" && path == "/retrieve/v1/jobs/job-reattach/results" {
                return (
                    200,
                    "application/json",
                    br#"{"asset":{"value":{"href":"/retrieve/v1/assets/reattach.grib","file:size":16}}}"#.to_vec(),
                );
            }
            if method == "GET" && path == "/retrieve/v1/assets/reattach.grib" {
                return (200, "application/x-grib", b"reattach01234567".to_vec());
            }
            (404, "application/json", br#"{}"#.to_vec())
        });

        let (_rc, cfg) = test_config(&server.base_url, ledger.clone());
        let inputs = json!({"date":["2026-11-25"]});
        let fp = job_fingerprint(DATASET, &inputs);
        update_ledger(&ledger, 2.0, |l| {
            l.jobs.insert(
                fp,
                JobLedgerEntry {
                    monitor_url: "/retrieve/v1/jobs/job-reattach".into(),
                    process_id: DATASET.into(),
                    submitted_at_utc: time_contract::iso(DateTime::<Utc>::from(SystemTime::now())),
                },
            );
        })
        .unwrap();

        let mut ads = Ads::new(cfg).unwrap();
        let mut out = tempfile::NamedTempFile::new().unwrap();
        ads.retrieve(&inputs, &mut out).unwrap();
        assert_eq!(ads.submissions, 0);
        assert_eq!(ads.downloads, 1);
        assert_eq!(std::fs::read(out.path()).unwrap(), b"reattach01234567");
        assert!(read_ledger_unlocked(&ledger).jobs.is_empty());
        let reqs = server.requests.lock().unwrap();
        let post_count = reqs
            .iter()
            .filter(|(m, p)| m == "POST" && p.ends_with("/execution"))
            .count();
        assert_eq!(post_count, 0);
    }

    #[test]
    fn default_job_max_age_cleans_up_five_hour_old_orphan() {
        assert_eq!(Config::default().job_max_age_seconds, 14400.0);
        let dir = tempfile::tempdir().unwrap();
        let ledger = dir.path().join("ads-jobs.json");
        let server = MockAds::start(move |method, path, _| {
            if method == "GET" && path == format!("/retrieve/v1/processes/{DATASET}") {
                return (200, "application/json", br#"{"id":"cams"}"#.to_vec());
            }
            if method == "DELETE" && path == "/retrieve/v1/jobs/job-5h-old" {
                return (200, "application/json", br#"{}"#.to_vec());
            }
            if method == "POST" && path == format!("/retrieve/v1/processes/{DATASET}/execution") {
                return (
                    202,
                    "application/json",
                    br#"{"links":[{"rel":"monitor","href":"/retrieve/v1/jobs/job-fresh-5h"}]}"#.to_vec(),
                );
            }
            if method == "GET" && path == "/retrieve/v1/jobs/job-fresh-5h" {
                return (
                    200,
                    "application/json",
                    br#"{"status":"successful","links":[{"rel":"results","href":"/retrieve/v1/jobs/job-fresh-5h/results"}]}"#.to_vec(),
                );
            }
            if method == "GET" && path == "/retrieve/v1/jobs/job-fresh-5h/results" {
                return (
                    200,
                    "application/json",
                    br#"{"asset":{"value":{"href":"/retrieve/v1/assets/fresh5h.grib","file:size":16}}}"#.to_vec(),
                );
            }
            if method == "GET" && path == "/retrieve/v1/assets/fresh5h.grib" {
                return (200, "application/x-grib", b"fresh5h123456789".to_vec());
            }
            (404, "application/json", br#"{}"#.to_vec())
        });

        let mut rc = tempfile::NamedTempFile::new().unwrap();
        writeln!(rc, "url: {}\nkey: test-ads-key", server.base_url).unwrap();
        let cfg = Config {
            api_url: Some(server.base_url.clone()),
            credentials_file: Some(rc.path().to_path_buf()),
            timeout_seconds: 5.0,
            wait_timeout_seconds: 15.0,
            poll_initial_seconds: 0.02,
            poll_max_seconds: 0.05,
            request_attempts: 1,
            lock_timeout_seconds: 2.0,
            job_ledger_path: Some(ledger.clone()),
            ..Config::default()
        };
        let old_time = DateTime::<Utc>::from(SystemTime::now()) - ChronoDuration::hours(5);
        update_ledger(&ledger, 2.0, |l| {
            l.jobs.insert(
                "five-hour-orphan".into(),
                JobLedgerEntry {
                    monitor_url: "/retrieve/v1/jobs/job-5h-old".into(),
                    process_id: DATASET.into(),
                    submitted_at_utc: time_contract::iso(old_time),
                },
            );
        })
        .unwrap();

        let mut ads = Ads::new(cfg).unwrap();
        let mut out = tempfile::NamedTempFile::new().unwrap();
        ads.retrieve(&json!({"date":["2026-11-26"]}), &mut out).unwrap();
        let reqs = server.requests.lock().unwrap();
        assert!(reqs
            .iter()
            .any(|(m, p)| m == "DELETE" && p == "/retrieve/v1/jobs/job-5h-old"));
    }
}
