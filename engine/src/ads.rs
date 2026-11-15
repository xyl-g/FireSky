//! Bounded ADS v1 processing client. Never attach API credentials to assets.
use reqwest::{
    blocking::{Client, Response},
    header::{HeaderMap, HeaderValue},
    Method, Url,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::{Duration, Instant},
};

pub(crate) const DATASET: &str = "cams-global-atmospheric-composition-forecasts";
const MAX_JSON: u64 = 4 * 1024 * 1024;
pub(crate) const MAX_DOWNLOAD: u64 = 512 * 1024 * 1024;

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
}
impl Default for Config {
    fn default() -> Self {
        Self {
            api_url: None,
            credentials_file: None,
            timeout_seconds: 120.0,
            wait_timeout_seconds: 7200.0,
            poll_initial_seconds: 1.0,
            poll_max_seconds: 5.0,
            request_attempts: 3,
            lock_timeout_seconds: 300.0,
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

pub(crate) struct Ads {
    api: Client,
    assets: Client,
    base: Url,
    config: Config,
    deadline: Instant,
    cancel: Option<Arc<AtomicBool>>,
    pub constraints_requests: usize,
    pub submissions: usize,
    pub downloads: usize,
    pub constraints_ms: f64,
    pub retrieval_ms: f64,
}
impl Ads {
    pub fn new(config: Config) -> Result<Self, String> {
        Self::with_cancel(config, None)
    }
    /// Interactive variant: the flag is polled inside queue waits and asset
    /// transfers so a cancelled prepare/sync releases the operation lock in
    /// seconds instead of riding out the whole ADS job.
    pub fn with_cancel(config: Config, cancel: Option<Arc<AtomicBool>>) -> Result<Self, String> {
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
    fn json(
        &mut self,
        method: Method,
        url: &Url,
        body: Option<&Value>,
        retry: bool,
    ) -> Result<Value, String> {
        let attempts = if retry {
            self.config.request_attempts
        } else {
            1
        };
        for attempt in 1..=attempts {
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
                Ok(r) => r.status().is_server_error() || r.status().as_u16() == 429,
                Err(_) => true,
            };
            if retryable && attempt < attempts {
                self.pause(self.config.poll_max_seconds)?;
                continue;
            }
            let mut response = response.map_err(|error| {
                // A budget-limited request can time out just before our Instant
                // deadline because the transport timer uses its own clock.
                http_failure(
                    self.deadline,
                    error.is_timeout() && remaining <= request_timeout,
                )
            })?;
            if !response.status().is_success() {
                return Err(format!(
                    "ADS API HTTP status {}",
                    response.status().as_u16()
                ));
            }
            return read_json(&mut response);
        }
        unreachable!()
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
    fn retrieve_inner(
        &mut self,
        inputs: &Value,
        target: &mut tempfile::NamedTempFile,
    ) -> Result<(), String> {
        let execution = self.process("/execution");
        self.submissions += 1;
        // Retrying an ambiguous submission can create/bill duplicate jobs.
        let job = self.json(
            Method::POST,
            &execution,
            Some(&json!({"inputs":inputs})),
            false,
        )?;
        let monitor = self.api_link(
            &execution,
            link(&job, "monitor").ok_or("ADS submission has no monitor link")?,
        )?;
        let mut delay = self.config.poll_initial_seconds;
        let state = loop {
            self.check_cancelled()?;
            let state = self.json(Method::GET, &monitor, None, true)?;
            match state["status"].as_str() {
                Some("successful") => break state,
                Some("accepted" | "running") => {
                    self.pause(delay)?;
                    delay = (delay * 1.5).min(self.config.poll_max_seconds);
                }
                Some("failed" | "rejected" | "dismissed" | "deleted") => {
                    return Err(format!(
                        "ADS job ended with status {}",
                        state["status"].as_str().unwrap()
                    ))
                }
                _ => return Err("ADS job has an unknown or missing status".into()),
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
        self.downloads += 1;
        Ok(())
    }
    fn download(
        &self,
        url: &Url,
        expected: u64,
        target: &mut tempfile::NamedTempFile,
    ) -> Result<(), String> {
        for attempt in 1..=self.config.request_attempts {
            target
                .as_file_mut()
                .set_len(0)
                .map_err(|_| "cannot reset ADS temporary file")?;
            use std::io::{Seek, SeekFrom};
            target
                .as_file_mut()
                .seek(SeekFrom::Start(0))
                .map_err(|_| "cannot seek ADS temporary file")?;
            let result = (|| {
                let timeout = self
                    .remaining()?
                    .min(Duration::from_secs_f64(self.config.timeout_seconds));
                let mut response = self
                    .assets
                    .get(url.clone())
                    .header("Accept-Encoding", "identity")
                    .timeout(timeout)
                    .send()
                    .map_err(|_| "ADS asset request failed (URL redacted)")?;
                if !response.status().is_success() {
                    return Err(format!(
                        "ADS asset HTTP status {}",
                        response.status().as_u16()
                    ));
                }
                if response.content_length().is_some_and(|n| n != expected) {
                    return Err("ADS asset Content-Length disagrees with result size".into());
                }
                let mut buffer = vec![0; 1024 * 1024];
                let mut bytes = 0_u64;
                loop {
                    self.check_cancelled()?;
                    let count = response
                        .read(&mut buffer)
                        .map_err(|_| "ADS asset response read failed")?;
                    if count == 0 {
                        break;
                    }
                    bytes += count as u64;
                    if bytes > expected {
                        return Err("ADS asset exceeds declared size".into());
                    }
                    target
                        .write_all(&buffer[..count])
                        .map_err(|_| "cannot write ADS temporary file")?;
                }
                if bytes != expected {
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
            self.pause(self.config.poll_max_seconds)?;
        }
        unreachable!()
    }
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
}
