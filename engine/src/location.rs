//! Geocoding, point metadata, complete offline timezone data and SRTM.
use crate::{cache_lock::Lock, request_file, timezone_lookup};
use flate2::bufread::GzDecoder;
use reqwest::{blocking::Client, redirect::Policy, Url};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::VecDeque,
    fs::{self, File},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
        Arc, Mutex, OnceLock,
    },
    time::{Duration, Instant},
};

const USER_AGENT: &str = "firesky/0.1 burning-sunset-engine";
const MAX_JSON: u64 = 16 * 1024 * 1024;
const MAX_TILE: u64 = 3601 * 3601 * 2;
const MAX_COMPRESSED: u64 = 32 * 1024 * 1024;
fn yes() -> bool {
    true
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Config {
    geocoding_url: String,
    metadata_url: String,
    nominatim_url: String,
    srtm_base_url: String,
    location_cache_path: PathBuf,
    srtm_cache_dir: PathBuf,
    timezone_manifest: Option<PathBuf>,
    request_attempts: u32,
    retry_seconds: f64,
    lock_timeout_seconds: f64,
    #[serde(skip)]
    pub(crate) cancellation: Option<Arc<AtomicBool>>,
}
impl Default for Config {
    fn default() -> Self {
        Self {
            geocoding_url: "https://geocoding-api.open-meteo.com/v1/search".into(),
            metadata_url: "https://api.open-meteo.com/v1/forecast".into(),
            nominatim_url: "https://nominatim.openstreetmap.org/search".into(),
            srtm_base_url: "https://elevation-tiles-prod.s3.amazonaws.com/skadi".into(),
            location_cache_path: ".cache/locations.json".into(),
            srtm_cache_dir: ".cache/srtm".into(),
            timezone_manifest: None,
            request_attempts: 3,
            retry_seconds: 0.8,
            lock_timeout_seconds: 30.0,
            cancellation: None,
        }
    }
}
#[derive(Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Operation {
    Search,
    Resolve,
    Metadata,
    Timezone,
    Timezones,
    Elevation,
    Tile,
}
/// 海拔来源三档（v1.1.1 契约）：auto=HTTP 优先 SRTM 兜底；http=仅 open-meteo，
/// 失败显式报错不落 SRTM；srtm=跳过 HTTP，本地时区+SRTM 瓦片。
#[derive(Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum ElevationSource {
    Auto,
    Http,
    Srtm,
}
impl Default for ElevationSource {
    fn default() -> Self {
        Self::Auto
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    operation: Operation,
    query: Option<String>,
    lat: Option<f64>,
    lon: Option<f64>,
    path: Option<PathBuf>,
    points: Option<Vec<[f64; 2]>>,
    #[serde(default)]
    config: Config,
    timeout_seconds: Option<f64>,
    #[serde(default = "yes")]
    allow_download: bool,
    #[serde(default)]
    force_refresh: bool,
    #[serde(default)]
    elevation_source: ElevationSource,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Located {
    name: String,
    lat: f64,
    lon: f64,
    timezone_name: String,
    elevation_m: f64,
    source: String,
    display_name: String,
}
fn require(ok: bool, message: &str) -> Result<(), String> {
    if ok {
        Ok(())
    } else {
        Err(message.into())
    }
}
fn coordinates(lat: f64, lon: f64) -> Result<(), String> {
    require(
        lat.is_finite()
            && (-90.0..=90.0).contains(&lat)
            && lon.is_finite()
            && (-180.0..=180.0).contains(&lon),
        "location requires finite bounded coordinates",
    )
}
fn elevation(value: f64) -> Result<f64, String> {
    require(value.is_finite(), "nonfinite elevation")?;
    Ok(value)
}
fn zone(value: &str) -> Result<String, String> {
    require(
        value.len() <= 128 && value.parse::<chrono_tz::Tz>().is_ok(),
        "unknown IANA timezone",
    )?;
    Ok(value.to_owned())
}
fn labels(name: &str, display_name: &str, source: &str) -> Result<(), String> {
    require(
        !name.is_empty()
            && name.len() <= 4096
            && display_name.len() <= 16_384
            && source.len() <= 256,
        "location labels exceed bounds",
    )
}
fn rounded(value: f64) -> f64 {
    format!("{value:.4}").parse().unwrap()
}
fn url(value: &str) -> Result<Url, String> {
    let result = Url::parse(value).map_err(|_| "invalid location provider URL")?;
    let local = result.host_str().is_some_and(|h| {
        h == "localhost"
            || h.trim_matches(['[', ']'])
                .parse::<std::net::IpAddr>()
                .is_ok_and(|ip| ip.is_loopback())
    });
    require(
        (result.scheme() == "https" || result.scheme() == "http" && local)
            && result.host().is_some()
            && result.username().is_empty()
            && result.password().is_none()
            && result.query().is_none()
            && result.fragment().is_none(),
        "location URLs require HTTPS (HTTP only on loopback), no credentials/query/fragment",
    )?;
    Ok(result)
}
fn client() -> Result<&'static Client, String> {
    static CLIENT: OnceLock<Result<Client, String>> = OnceLock::new();
    CLIENT
        .get_or_init(|| {
            Client::builder()
                .no_proxy()
                .redirect(Policy::none())
                .user_agent(USER_AGENT)
                .build()
                .map_err(|_| "cannot initialize location HTTP client".into())
        })
        .as_ref()
        .map_err(Clone::clone)
}

/// Data-plane client for SRTM tile downloads. Unlike the geocoding client it
/// honors the environment proxy (the established data-plane rule, matching
/// ADS): tile CDNs are unreachable from networks that require the proxy, and
/// proxy-less environments behave identically to a direct client.
fn srtm_client() -> Result<&'static Client, String> {
    static CLIENT: OnceLock<Result<Client, String>> = OnceLock::new();
    CLIENT
        .get_or_init(|| {
            Client::builder()
                .user_agent(USER_AGENT)
                .build()
                .map_err(|_| "cannot initialize SRTM HTTP client".into())
        })
        .as_ref()
        .map_err(Clone::clone)
}
fn regular(path: &Path) -> Result<bool, String> {
    match fs::symlink_metadata(path) {
        Ok(m) => {
            require(
                m.is_file(),
                "location cache file must be regular, not a symlink",
            )?;
            Ok(true)
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(e) => Err(e.to_string()),
    }
}
fn directory(path: &Path) -> Result<(), String> {
    fs::create_dir_all(path).map_err(|e| e.to_string())?;
    require(
        fs::symlink_metadata(path)
            .map_err(|e| e.to_string())?
            .is_dir(),
        "location cache requires nonsymlink directory",
    )
}
fn sync_dir(path: &Path) -> Result<(), String> {
    #[cfg(unix)]
    {
        File::open(path)
            .and_then(|f| f.sync_all())
            .map_err(|e| e.to_string())?;
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}
fn open_regular(path: &Path) -> Result<File, String> {
    require(regular(path)?, "location file is missing")?;
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    options.open(path).map_err(|e| e.to_string())
}
impl Config {
    pub(crate) fn validate(&mut self) -> Result<(), String> {
        for endpoint in [
            &self.geocoding_url,
            &self.metadata_url,
            &self.nominatim_url,
            &self.srtm_base_url,
        ] {
            url(endpoint)?;
        }
        require(
            (1..=8).contains(&self.request_attempts)
                && self.retry_seconds.is_finite()
                && (0.0..=10.0).contains(&self.retry_seconds)
                && self.lock_timeout_seconds.is_finite()
                && (0.0..=3600.0).contains(&self.lock_timeout_seconds)
                && !self.location_cache_path.as_os_str().is_empty()
                && !self.srtm_cache_dir.as_os_str().is_empty(),
            "invalid bounded location configuration",
        )?;
        self.location_cache_path =
            std::path::absolute(&self.location_cache_path).map_err(|e| e.to_string())?;
        self.srtm_cache_dir =
            std::path::absolute(&self.srtm_cache_dir).map_err(|e| e.to_string())?;
        if let Some(path) = &mut self.timezone_manifest {
            *path = std::path::absolute(&*path).map_err(|e| e.to_string())?;
        }
        Ok(())
    }
    fn check_cancelled(&self) -> Result<(), String> {
        require(
            !self
                .cancellation
                .as_ref()
                .is_some_and(|v| v.load(Ordering::Acquire)),
            "location operation cancelled",
        )
    }
    fn wait_retry(&self, seconds: f64) -> Result<(), String> {
        let deadline = Instant::now() + Duration::from_secs_f64(seconds);
        loop {
            self.check_cancelled()?;
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Ok(());
            }
            std::thread::sleep(remaining.min(Duration::from_millis(50)));
        }
    }
    fn get_json(
        &self,
        endpoint: &str,
        query: &[(&str, String)],
        timeout: f64,
        attempts: u32,
        allow: bool,
    ) -> Result<Value, String> {
        require(allow, "location downloads disabled")?;
        let endpoint = url(endpoint)?;
        for attempt in 0..attempts {
            self.check_cancelled()?;
            let result = (|| {
                let response = client()?
                    .get(endpoint.clone())
                    .query(query)
                    .timeout(Duration::from_secs_f64(timeout))
                    .send()
                    .map_err(|_| "location provider transport failed")?;
                require(
                    response.status().is_success(),
                    "location provider returned non-success status",
                )?;
                require(
                    response.content_length().is_none_or(|n| n <= MAX_JSON),
                    "location JSON exceeds bound",
                )?;
                let mut body = Vec::new();
                response
                    .take(MAX_JSON + 1)
                    .read_to_end(&mut body)
                    .map_err(|_| "location JSON transfer failed")?;
                require(body.len() as u64 <= MAX_JSON, "location JSON exceeds bound")?;
                serde_json::from_slice(&body).map_err(|_| "invalid location provider JSON".into())
            })();
            self.check_cancelled()?;
            if result.is_ok() || attempt + 1 == attempts {
                return result;
            }
            self.wait_retry(self.retry_seconds * f64::from(attempt + 1))?;
        }
        Err("location provider retries exhausted".into())
    }
    fn local_timezone(&self, lat: f64, lon: f64) -> Result<(String, Value), String> {
        self.check_cancelled()?;
        let result = timezone_lookup::lookup(
            self.timezone_manifest
                .as_deref()
                .ok_or("local timezone dataset is not configured")?,
            rounded(lat),
            rounded(lon),
        )?;
        self.check_cancelled()?;
        Ok(result)
    }
    fn point_metadata(
        &self,
        lat: f64,
        lon: f64,
        timeout: f64,
        allow: bool,
        source: ElevationSource,
    ) -> Result<Value, String> {
        self.check_cancelled()?;
        let (lat, lon) = (rounded(lat), rounded(lon));
        let online = if source == ElevationSource::Srtm {
            None
        } else {
            Some(
                self.get_json(
                    &self.metadata_url,
                    &[
                        ("latitude", lat.to_string()),
                        ("longitude", lon.to_string()),
                        ("timezone", "auto".into()),
                        ("forecast_days", "1".into()),
                    ],
                    timeout,
                    1,
                    allow,
                )
                .and_then(|payload| {
                    Ok((
                        zone(
                            payload["timezone"]
                                .as_str()
                                .ok_or("missing provider timezone")?,
                        )?,
                        elevation(
                            payload["elevation"]
                                .as_f64()
                                .ok_or("missing provider elevation")?,
                        )?,
                    ))
                }),
            )
        };
        match online {
            Some(Ok((timezone_name, elevation_m))) => Ok(
                json!({"timezone_name":timezone_name,"elevation_m":elevation_m,
                "queried_lat":lat,"queried_lon":lon,"source":"open-meteo-point-metadata"}),
            ),
            // http 档失败必须把错误透传给调用方，不得静默落 SRTM。
            Some(Err(error)) if source == ElevationSource::Http => Err(error),
            _ => {
                let (timezone_name, proof) = self.local_timezone(lat, lon)?;
                let (elevation_m, path) = self.local_elevation(lat, lon, timeout, allow)?;
                Ok(
                    json!({"timezone_name":timezone_name,"elevation_m":elevation_m,"queried_lat":lat,
                    "queried_lon":lon,"source":"local-timezone+srtm","timezone_data":proof,"srtm_path":path}),
                )
            }
        }
    }
    fn meteo(&self, query: &str, timeout: f64, allow: bool) -> Result<Option<Located>, String> {
        let payload = self.get_json(
            &self.geocoding_url,
            &[
                ("name", query.into()),
                ("count", "1".into()),
                ("language", "zh".into()),
                ("format", "json".into()),
            ],
            timeout,
            self.request_attempts,
            allow,
        )?;
        let Some(item) = payload
            .get("results")
            .and_then(Value::as_array)
            .and_then(|a| a.first())
        else {
            return Ok(None);
        };
        let lat = number(&item["latitude"])?;
        let lon = number(&item["longitude"])?;
        coordinates(lat, lon)?;
        let timezone_name = match item["timezone"].as_str().filter(|s| !s.is_empty()) {
            Some(value) => zone(value)?,
            None => self.local_timezone(lat, lon)?.0,
        };
        let elevation_m = if item["elevation"].is_null() {
            self.local_elevation(lat, lon, timeout, allow)?.0
        } else {
            elevation(number(&item["elevation"])?)?
        };
        let name = text(item.get("name"), query);
        let display_name = [
            Some(name.as_str()),
            item["admin2"].as_str(),
            item["admin1"].as_str(),
            item["country"].as_str(),
        ]
        .into_iter()
        .flatten()
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(", ");
        labels(&name, &display_name, "open-meteo-geocoding")?;
        Ok(Some(Located {
            name,
            lat,
            lon,
            timezone_name,
            elevation_m,
            source: "open-meteo-geocoding".into(),
            display_name,
        }))
    }
    fn nominatim(
        &self,
        query: &str,
        timeout: f64,
        fast: bool,
        allow: bool,
        source: ElevationSource,
    ) -> Result<Option<Value>, String> {
        let payload = self.get_json(
            &self.nominatim_url,
            &[
                ("q", query.into()),
                ("format", "jsonv2".into()),
                ("limit", "1".into()),
                ("addressdetails", "1".into()),
                ("accept-language", "zh-CN".into()),
            ],
            timeout,
            if fast { 1 } else { self.request_attempts },
            allow,
        )?;
        let Some(item) = payload.as_array().and_then(|a| a.first()) else {
            return Ok(None);
        };
        let lat = number(&item["lat"])?;
        let lon = number(&item["lon"])?;
        coordinates(lat, lon)?;
        let address = &item["address"];
        let name = [item.get("name"), address.get("city"), address.get("region")]
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .find(|s| !s.is_empty())
            .unwrap_or(query)
            .to_owned();
        let display_name = text(item.get("display_name"), &name);
        labels(&name, &display_name, "nominatim-openstreetmap")?;
        let mut result = json!({"name":name,"lat":lat,"lon":lon,"source":"nominatim-openstreetmap",
            "display_name":display_name,"timezone_name":null,"elevation_m":null});
        if !fast {
            let metadata = self.point_metadata(lat, lon, timeout.min(6.0), allow, source)?;
            result["timezone_name"] = metadata["timezone_name"].clone();
            result["elevation_m"] = metadata["elevation_m"].clone();
        }
        Ok(Some(result))
    }
    fn cache_read(&self, query: &str) -> Option<Located> {
        let (payload, _): (Value, _) =
            request_file::read(&self.location_cache_path, MAX_JSON).ok()?;
        if payload["_schema_version"] != 2 {
            return None;
        }
        let item = &payload[query];
        let name = item["name"].as_str()?.to_owned();
        let source = text(item.get("source"), "cache");
        let display_name = text(item.get("display_name"), &name);
        labels(&name, &display_name, &source).ok()?;
        let (lat, lon) = (number(&item["lat"]).ok()?, number(&item["lon"]).ok()?);
        coordinates(lat, lon).ok()?;
        Some(Located {
            name: name.clone(),
            lat,
            lon,
            timezone_name: zone(item["timezone_name"].as_str()?).ok()?,
            elevation_m: elevation(number(&item["elevation_m"]).ok()?).ok()?,
            source,
            display_name,
        })
    }
    fn cache_write(&self, query: &str, result: &Located) -> Result<(), String> {
        self.check_cancelled()?;
        let path = &self.location_cache_path;
        let parent = path.parent().ok_or("missing location cache directory")?;
        directory(parent)?;
        let _lock = Lock::acquire_checked(
            &path.with_extension("json.lock"),
            self.lock_timeout_seconds,
            || self.check_cancelled(),
        )?;
        let mut payload = if regular(path)? {
            let (payload, _): (Value, _) = request_file::read(path, MAX_JSON)?;
            require(
                payload.is_object() && payload["_schema_version"] == 2,
                "preserving incompatible location cache",
            )?;
            payload
        } else {
            json!({"_schema_version":2})
        };
        payload[query] = serde_json::to_value(result).map_err(|e| e.to_string())?;
        require(
            payload.as_object().is_some_and(|v| v.len() <= 100_000),
            "too many cached locations",
        )?;
        let bytes = serde_json::to_vec(&payload).map_err(|e| e.to_string())?;
        require(
            bytes.len() as u64 <= MAX_JSON,
            "location cache exceeds bound",
        )?;
        let mut pending = tempfile::Builder::new()
            .prefix(".firesky-locations-")
            .tempfile_in(parent)
            .map_err(|e| e.to_string())?;
        pending
            .write_all(&bytes)
            .and_then(|_| pending.as_file().sync_all())
            .map_err(|e| e.to_string())?;
        regular(path)?;
        self.check_cancelled()?;
        pending.persist(path).map_err(|e| e.error.to_string())?;
        sync_dir(parent)
    }
    fn search(
        &self,
        query: &str,
        timeout: f64,
        fast: bool,
        allow: bool,
        refresh: bool,
        source: ElevationSource,
    ) -> Result<Value, String> {
        self.check_cancelled()?;
        if !refresh {
            if let Some(cached) = self.cache_read(query) {
                return serde_json::to_value(cached).map_err(|e| e.to_string());
            }
        }
        if fast {
            if let Ok(Some(value)) = self.nominatim(query, timeout, true, allow, source) {
                return Ok(value);
            }
            if let Ok(Some(value)) = self.meteo(query, timeout, allow) {
                return serde_json::to_value(value).map_err(|e| e.to_string());
            }
        } else {
            let found = match self.meteo(query, timeout, allow) {
                Ok(Some(value)) => Some(value),
                _ => self
                    .nominatim(query, timeout, false, allow, source)?
                    .map(serde_json::from_value)
                    .transpose()
                    .map_err(|e| e.to_string())?,
            };
            if let Some(value) = found {
                // The reference cache is best-effort, never a successful lookup's prerequisite.
                let _ = self.cache_write(query, &value);
                return serde_json::to_value(value).map_err(|e| e.to_string());
            }
        }
        Err("location could not be resolved by configured providers/cache".into())
    }
    fn local_elevation(
        &self,
        lat: f64,
        lon: f64,
        timeout: f64,
        allow: bool,
    ) -> Result<(f64, PathBuf), String> {
        self.check_cancelled()?;
        let name = tile(lat, lon)?;
        let target = self.srtm_cache_dir.join(format!("{name}.hgt"));
        if regular(&target)? {
            return Ok((read_srtm(&target, lat, lon)?, target));
        }
        require(allow, "SRTM tile is missing; downloads disabled")?;
        let elevation = self.fetch_tile(&name, lat, lon, timeout, None)?;
        Ok((elevation, target))
    }

    /// Shared tile download core (phase D): lock-guarded, bounds-checked,
    /// publishes atomically via a pending temp file. Emits optional progress
    /// events (`terrain` envelope) with real received/total bytes; percent is
    /// `null` when the provider omits content length. Cancellation is checked
    /// per chunk so the cancel delay is bounded by one 64 KiB read.
    /// GET one tile's compressed bytes with bounds, cancellation and optional
    /// progress; a 404 classifies as `Absent` so bulk runs can mark ocean
    /// tiles missing without retry churn.
    fn fetch_tile_bytes(
        &self,
        name: &str,
        timeout: f64,
        progress: Option<&(dyn Fn(Value) -> Result<(), String> + Sync)>,
    ) -> Result<Vec<u8>, (bool, String)> {
        let endpoint = url(&format!(
            "{}/{}/{}.hgt.gz",
            self.srtm_base_url.trim_end_matches('/'),
            &name[..3],
            name
        ))
        .map_err(|e| (false, e))?;
        let client = srtm_client().map_err(|e| (false, e))?;
        let response = client
            .get(endpoint)
            .timeout(Duration::from_secs_f64(timeout.max(60.0)))
            .send()
            .map_err(|_| (false, "SRTM download transport failed".to_string()))?;
        if response.status().as_u16() == 404 {
            return Err((true, "SRTM tile does not exist".to_string()));
        }
        require(
            response.status().is_success(),
            "SRTM provider returned non-success status",
        )
        .map_err(|e| (false, e))?;
        let total = response
            .content_length()
            .filter(|n| *n <= MAX_COMPRESSED);
        require(
            response.content_length().is_none_or(|n| n <= MAX_COMPRESSED),
            "SRTM compressed tile exceeds bound",
        )
        .map_err(|e| (false, e))?;
        let mut compressed = Vec::new();
        let mut received = 0u64;
        let mut last_percent: Option<i64> = None;
        let mut reader = response.take(MAX_COMPRESSED + 1);
        loop {
            self.check_cancelled().map_err(|e| (false, e))?;
            let mut chunk = [0u8; 65_536];
            let count = reader
                .read(&mut chunk)
                .map_err(|_| (false, "SRTM download failed".to_string()))?;
            if count == 0 {
                break;
            }
            compressed.extend_from_slice(&chunk[..count]);
            received += count as u64;
            require(
                received <= MAX_COMPRESSED,
                "SRTM compressed tile exceeds bound",
            )
            .map_err(|e| (false, e))?;
            if let Some(progress) = progress {
                let percent = total
                    .filter(|t| *t > 0)
                    .map(|t| (received as f64 / t as f64 * 100.0).min(99.0));
                let rounded = percent.map(|v| v as i64);
                if rounded != last_percent {
                    last_percent = rounded;
                    progress(json!({"terrain": {
                        "stage": "downloading",
                        "tile": name,
                        "received_bytes": received,
                        "total_bytes": total,
                        "percent": percent,
                    }}))
                    .map_err(|e| (false, e))?;
                }
            }
        }
        Ok(compressed)
    }

    /// Decompress-verify-publish a fetched tile; returns the decompressed size
    /// and, for point fetches, the sampled elevation validated before the
    /// file is published. Valid tiles are exactly one of the two known SRTM
    /// dimensions, so bulk runs can accept a tile by size alone.
    fn publish_tile(
        &self,
        name: &str,
        compressed: &[u8],
        sample: Option<(f64, f64)>,
    ) -> Result<(u64, Option<f64>), String> {
        let mut decoder = GzDecoder::new(compressed);
        let mut pending = tempfile::Builder::new()
            .prefix(".firesky-srtm-")
            .tempfile_in(&self.srtm_cache_dir)
            .map_err(|e| e.to_string())?;
        let count = std::io::copy(&mut decoder.by_ref().take(MAX_TILE + 1), &mut pending)
            .map_err(|_| "invalid SRTM gzip/CRC".to_string())?;
        require(
            count <= MAX_TILE && decoder.into_inner().is_empty(),
            "SRTM gzip exceeds bounds or has trailing data",
        )?;
        require(
            matches!(count, 2_884_802 | 25_934_402),
            "downloaded tile has unknown SRTM dimensions",
        )?;
        pending.as_file().sync_all().map_err(|e| e.to_string())?;
        let elevation = match sample {
            Some((lat, lon)) => Some(read_srtm(pending.path(), lat, lon)?),
            None => None,
        };
        let target = self.srtm_cache_dir.join(format!("{name}.hgt"));
        require(!regular(&target)?, "SRTM tile appeared during download")?;
        pending
            .persist_noclobber(&target)
            .map_err(|e| e.error.to_string())?;
        sync_dir(&self.srtm_cache_dir)?;
        Ok((count, elevation))
    }

    fn fetch_tile(
        &self,
        name: &str,
        lat: f64,
        lon: f64,
        timeout: f64,
        progress: Option<&(dyn Fn(Value) -> Result<(), String> + Sync)>,
    ) -> Result<f64, String> {
        let target = self.srtm_cache_dir.join(format!("{name}.hgt"));
        directory(&self.srtm_cache_dir)?;
        let _lock = Lock::acquire_checked(
            &target.with_extension("hgt.lock"),
            self.lock_timeout_seconds,
            || self.check_cancelled(),
        )?;
        if regular(&target)? {
            return read_srtm(&target, lat, lon);
        }
        let compressed = self
            .fetch_tile_bytes(name, timeout, progress)
            .map_err(|(_, message)| message)?;
        if let Some(progress) = progress {
            progress(json!({"terrain": {"stage": "verifying", "tile": name}}))?;
        }
        let (_, elevation) = self.publish_tile(name, &compressed, Some((lat, lon)))?;
        match elevation {
            Some(value) => Ok(value),
            None => Err("tile elevation sampling is missing".into()),
        }
    }

    /// Phase D contract: terrain tile inventory for the data page. Lists all
    /// local tiles (capped; `tile_count` always reports the real total) and,
    /// when coordinates are given, the tile that covers them with its local
    /// state. Out-of-coverage latitude is a status, not an error, so the UI
    /// can explain the boundary instead of faking data. With
    /// `estimate_global`, samples provider HEAD sizes to estimate the bulk
    /// (global) download footprint before the user confirms it.
    pub(crate) fn terrain_inventory(&self, params: &Value) -> Result<Value, String> {
        let mut tiles = Vec::new();
        let mut tile_count = 0usize;
        if let Ok(entries) = fs::read_dir(&self.srtm_cache_dir) {
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().to_string();
                let Some(stem) = name.strip_suffix(".hgt") else {
                    continue;
                };
                if !is_tile_name(stem) {
                    continue;
                }
                tile_count += 1;
                if tiles.len() < 200 {
                    tiles.push(json!({
                        "name": stem,
                        "bytes": entry.metadata().map(|meta| meta.len()).unwrap_or(0),
                    }));
                }
            }
        }
        tiles.sort_by(|a, b| a["name"].as_str().cmp(&b["name"].as_str()));
        let mut result = json!({
            "contract": "rust-terrain-inventory-v1",
            "tiles": tiles,
            "tile_count": tile_count,
            "tiles_truncated": tile_count > tiles.len(),
            "srtm_cache_dir": self.srtm_cache_dir.display().to_string(),
        });
        // 焦点瓦片可选：估算请求（仅 bounds）不必带坐标。
        if let (Some(lat), Some(lon)) = (params.get("lat"), params.get("lon")) {
            if let (Ok(lat), Ok(lon)) = (number(lat), number(lon)) {
                coordinates(lat, lon)?;
                let coverage_ok = (-56.0..60.0).contains(&lat);
                result["coverage_ok"] = json!(coverage_ok);
                if coverage_ok {
                    let name = tile(lat, lon)?;
                    let target = self.srtm_cache_dir.join(format!("{name}.hgt"));
                    let exists = regular(&target)?;
                    result["tile"] = json!({
                        "name": name,
                        "exists": exists,
                        "bytes": if exists {
                            fs::metadata(&target).map(|meta| meta.len()).unwrap_or(0)
                        } else { 0 },
                        "lat_range": [lat.floor(), lat.floor() + 1.0],
                        "lon_range": [lon.floor(), lon.floor() + 1.0],
                    });
                }
            }
        }
        if params["estimate_global"] == json!(true) {
            // 可选 bounds：区域预设（中国/亚洲等）复用同一估算路径；缺省为全球。
            let south = params["bounds"]["south"].as_i64().unwrap_or(-56);
            let north = params["bounds"]["north"].as_i64().unwrap_or(59);
            let west = params["bounds"]["west"].as_i64().unwrap_or(-180);
            let east = params["bounds"]["east"].as_i64().unwrap_or(179);
            let candidates = bulk_candidates(south, north, west, east)?;
            let existing = candidates
                .iter()
                .filter(|name| {
                    regular(&self.srtm_cache_dir.join(format!("{name}.hgt"))).unwrap_or(false)
                })
                .count();
            // Sampling is best-effort: transport failures yield no estimate
            // instead of blocking the inventory.
            let estimated_bytes = self.estimate_bulk_bytes(&candidates);
            result["global"] = json!({
                "candidates": candidates.len(),
                "existing": existing,
                "estimated_bytes": estimated_bytes,
                "estimated_gb": estimated_bytes.map(|bytes| bytes as f64 / 1e9),
                "bounds": {"south": south, "north": north, "west": west, "east": east},
            });
        }
        Ok(result)
    }

    /// HEAD-sample up to 12 spread tiles and extrapolate the bulk footprint.
    /// Ocean tiles (404) are excluded from the sample average, so the result
    /// overestimates by the ocean fraction — an honest upper bound, never a
    /// fabricated exact size.
    fn estimate_bulk_bytes(&self, candidates: &[String]) -> Option<u64> {
        if candidates.len() < 12 {
            return None;
        }
        // 12 spread positions proportional to the candidate length — the
        // sample must fit region presets (a few thousand tiles) as well as
        // the global set, so fixed global indices would go out of bounds.
        let len = candidates.len();
        let indices: Vec<usize> = (0..12).map(|k| len * k / 12).collect();
        let mut sampled = 0usize;
        let mut total = 0u64;
        for index in indices {
            match self.head_tile_size(&candidates[index]) {
                Some(size) => {
                    sampled += 1;
                    total += size;
                }
                None => {}
            }
        }
        (sampled > 0).then(|| total / sampled as u64 * candidates.len() as u64)
    }

    fn head_tile_size(&self, name: &str) -> Option<u64> {
        let endpoint = url(&format!(
            "{}/{}/{}.hgt.gz",
            self.srtm_base_url.trim_end_matches('/'),
            &name[..3],
            name
        ))
        .ok()?;
        let client = srtm_client().ok()?;
        let response = client
            .head(endpoint)
            .timeout(Duration::from_secs_f64(30.0))
            .send()
            .ok()?;
        if !response.status().is_success() {
            return None;
        }
        // reqwest's Response::content_length() reports the (empty) HEAD body,
        // not the header; the real tile size lives in the Content-Length
        // header itself.
        response
            .headers()
            .get(reqwest::header::CONTENT_LENGTH)?
            .to_str()
            .ok()?
            .parse()
            .ok()
    }

    /// Bulk (global) terrain download: enumerate candidate tiles over the
    /// requested bounds (default = full SRTM coverage), skip tiles that
    /// already exist (resumable), download the rest with bounded concurrency,
    /// and classify provider 404s as absent (ocean) instead of failing. Runs
    /// do not hold the compute lock; per-tile download locks keep concurrent
    /// readers safe. Cancellation and a free-disk guardrail stop the run
    /// while keeping every finished tile.
    fn terrain_download_global(
        &self,
        params: &Value,
        progress: &(dyn Fn(Value) -> Result<(), String> + Sync),
    ) -> Result<Value, String> {
        let south = params["bounds"]["south"].as_i64().unwrap_or(-56);
        let north = params["bounds"]["north"].as_i64().unwrap_or(59);
        let west = params["bounds"]["west"].as_i64().unwrap_or(-180);
        let east = params["bounds"]["east"].as_i64().unwrap_or(179);
        let candidates = bulk_candidates(south, north, west, east)?;
        let timeout = params
            .get("timeout_seconds")
            .and_then(Value::as_f64)
            .filter(|v| v.is_finite() && (10.0..=900.0).contains(v))
            .unwrap_or(300.0);
        directory(&self.srtm_cache_dir)?;
        // Guardrail: refuse to start when the sampled estimate exceeds the
        // available disk space. `force` lets operators override a stale or
        // unavailable estimate.
        if params["force"] != json!(true) {
            if let Some(estimate) = self.estimate_bulk_bytes(&candidates) {
                let available = fs2::available_space(&self.srtm_cache_dir).ok();
                if let Some(available) = available {
                    require(
                        estimate < available,
                        &format!(
                            "bulk terrain download needs about {:.0} GB but only {:.0} GB is available; free space or pass force",
                            estimate as f64 / 1e9,
                            available as f64 / 1e9
                        ),
                    )?;
                }
            }
        }
        let cursor = AtomicUsize::new(0);
        let existing = AtomicUsize::new(0);
        let downloaded = AtomicUsize::new(0);
        let absent = AtomicUsize::new(0);
        let failed = AtomicUsize::new(0);
        let received = AtomicU64::new(0);
        let cancelled = AtomicBool::new(false);
        let disk_stopped = AtomicBool::new(false);
        let total = candidates.len();
        let workers = total.min(4).max(1);
        std::thread::scope(|scope| {
            for _ in 0..workers {
                scope.spawn(|| loop {
                    if cancelled.load(Ordering::Acquire) || disk_stopped.load(Ordering::Acquire) {
                        return;
                    }
                    if self.check_cancelled().is_err() {
                        cancelled.store(true, Ordering::Release);
                        return;
                    }
                    let index = cursor.fetch_add(1, Ordering::Relaxed);
                    if index >= total {
                        return;
                    }
                    let name = &candidates[index];
                    let target = self.srtm_cache_dir.join(format!("{name}.hgt"));
                    if regular(&target).unwrap_or(true) {
                        existing.fetch_add(1, Ordering::Relaxed);
                    } else {
                        let outcome = match self.fetch_tile_bytes(name, timeout, None) {
                            Ok(compressed) => self
                                .publish_tile(name, &compressed, None)
                                .map_err(|message| (false, message)),
                            Err((tile_absent, message)) => Err((tile_absent, message)),
                        };
                        match outcome {
                            Ok((bytes, _)) => {
                                downloaded.fetch_add(1, Ordering::Relaxed);
                                received.fetch_add(bytes, Ordering::Relaxed);
                                // Free-disk guardrail: stop early instead of
                                // filling the volume; finished tiles persist.
                                if downloaded.load(Ordering::Relaxed) % 32 == 0 {
                                    let available =
                                        fs2::available_space(&self.srtm_cache_dir).ok();
                                    if available.is_some_and(|space| space < 2_000_000_000) {
                                        disk_stopped.store(true, Ordering::Release);
                                    }
                                }
                            }
                            Err((tile_absent, _)) if tile_absent => {
                                absent.fetch_add(1, Ordering::Relaxed);
                            }
                            Err(_) => {
                                failed.fetch_add(1, Ordering::Relaxed);
                            }
                        }
                    }
                    let resolved = existing.load(Ordering::Relaxed)
                        + downloaded.load(Ordering::Relaxed)
                        + absent.load(Ordering::Relaxed)
                        + failed.load(Ordering::Relaxed);
                    let _ = progress(json!({"terrain": {
                        "stage": "global",
                        "candidates": total,
                        "resolved": resolved,
                        "existing": existing.load(Ordering::Relaxed),
                        "downloaded": downloaded.load(Ordering::Relaxed),
                        "absent": absent.load(Ordering::Relaxed),
                        "failed": failed.load(Ordering::Relaxed),
                        "received_bytes": received.load(Ordering::Relaxed),
                        "percent": (resolved as f64 / total as f64 * 100.0).min(100.0),
                        "current_tile": name,
                    }}));
                });
            }
        });
        let was_cancelled = cancelled.load(Ordering::Acquire) || self.check_cancelled().is_err();
        Ok(json!({
            "contract": "rust-terrain-download-v1",
            "status": if was_cancelled { "cancelled" } else { "done" },
            "mode": "global",
            "bounds": {"south": south, "north": north, "west": west, "east": east},
            "candidates": total,
            "existing": existing.load(Ordering::Relaxed),
            "downloaded": downloaded.load(Ordering::Relaxed),
            "absent": absent.load(Ordering::Relaxed),
            "failed": failed.load(Ordering::Relaxed),
            "received_bytes": received.load(Ordering::Relaxed),
            "received_gb": received.load(Ordering::Relaxed) as f64 / 1e9,
            "stopped_reason": if disk_stopped.load(Ordering::Acquire) { json!("disk_space") } else { json!(null) },
        }))
    }

    /// Phase D contract: explicit terrain download for the data page. `point`
    /// mode (default) fetches the tile covering the given coordinates with
    /// real stage progress; `global` mode bulk-downloads the whole coverage.
    /// Both reuse the same bounded download/verify/publish core, never
    /// re-download existing tiles and support cancellation. Out-of-coverage
    /// latitude is a status, not an error.
    pub(crate) fn terrain_download(
        &self,
        params: &Value,
        progress: &(dyn Fn(Value) -> Result<(), String> + Sync),
    ) -> Result<Value, String> {
        let timeout = params
            .get("timeout_seconds")
            .and_then(Value::as_f64)
            .filter(|v| v.is_finite() && (10.0..=900.0).contains(v))
            .unwrap_or(300.0);
        if params["mode"].as_str() == Some("global") {
            // Workers check cancellation on their first loop pass, so a
            // pre-cancelled run reports `status: cancelled` instead of an
            // opaque error.
            return self.terrain_download_global(params, progress);
        }
        self.check_cancelled()?;
        let lat = number(&params["lat"])?;
        let lon = number(&params["lon"])?;
        coordinates(lat, lon)?;
        if !(-56.0..60.0).contains(&lat) {
            return Ok(json!({
                "contract": "rust-terrain-download-v1",
                "status": "out_of_coverage",
                "message": "SRTM coverage is -56 <= latitude < 60",
            }));
        }
        let name = tile(lat, lon)?;
        let target = self.srtm_cache_dir.join(format!("{name}.hgt"));
        if regular(&target)? {
            return Ok(json!({
                "contract": "rust-terrain-download-v1",
                "status": "exists",
                "tile": name,
                "elevation_m": read_srtm(&target, lat, lon)?,
            }));
        }
        let elevation = self.fetch_tile(&name, lat, lon, timeout, Some(progress))?;
        Ok(json!({
            "contract": "rust-terrain-download-v1",
            "status": "downloaded",
            "tile": name,
            "elevation_m": elevation,
        }))
    }
}

/// Candidate tile names over an inclusive 1° grid; the default bounds cover
/// the whole SRTM domain (-56 ≤ lat < 60). Used by bulk download and its
/// inventory estimate.
fn bulk_candidates(south: i64, north: i64, west: i64, east: i64) -> Result<Vec<String>, String> {
    require(south <= north && west <= east, "invalid terrain bounds")?;
    require(
        (-56..=59).contains(&south)
            && (-56..=59).contains(&north)
            && (-180..=179).contains(&west)
            && (-180..=179).contains(&east),
        "terrain bounds exceed SRTM coverage (-56 <= lat < 60)",
    )?;
    let count = ((north - south + 1) as usize).saturating_mul((east - west + 1) as usize);
    require(
        count <= 120_000,
        "terrain bulk request exceeds 120000 candidate tiles",
    )?;
    let mut names = Vec::with_capacity(count);
    for a in south..=north {
        for b in west..=east {
            names.push(format!(
                "{}{:02}{}{:03}",
                if a >= 0 { "N" } else { "S" },
                a.abs(),
                if b >= 0 { "E" } else { "W" },
                b.abs()
            ));
        }
    }
    Ok(names)
}
fn number(value: &Value) -> Result<f64, String> {
    value
        .as_f64()
        .or_else(|| value.as_str().and_then(|s| s.parse().ok()))
        .filter(|v| v.is_finite())
        .ok_or_else(|| "missing or nonfinite location number".into())
}
fn text(value: Option<&Value>, fallback: &str) -> String {
    value
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .unwrap_or(fallback)
        .to_owned()
}
fn tile(lat: f64, lon: f64) -> Result<String, String> {
    coordinates(lat, lon)?;
    require(
        (-56.0..60.0).contains(&lat),
        "SRTM coverage is -56 <= latitude < 60",
    )?;
    let (a, b) = (lat.floor() as i32, lon.floor() as i32);
    Ok(format!(
        "{}{:02}{}{:03}",
        if a >= 0 { "N" } else { "S" },
        a.abs(),
        if b >= 0 { "E" } else { "W" },
        b.abs()
    ))
}
/// Recognizes `[NS]##[EW]###` tile stems so the terrain inventory only lists
/// real SRTM tiles, never lock files or unrelated cache content.
fn is_tile_name(name: &str) -> bool {
    let bytes = name.as_bytes();
    bytes.len() == 7
        && matches!(bytes[0], b'N' | b'S')
        && bytes[1..3].iter().all(u8::is_ascii_digit)
        && matches!(bytes[3], b'E' | b'W')
        && bytes[4..7].iter().all(u8::is_ascii_digit)
}
fn read_srtm(path: &Path, lat: f64, lon: f64) -> Result<f64, String> {
    coordinates(lat, lon)?;
    let mut file = open_regular(path)?;
    let bytes = file.metadata().map_err(|e| e.to_string())?.len();
    let side = match bytes {
        2884802 => 1201u64,
        25934402 => 3601u64,
        _ => return Err("invalid SRTM tile dimensions".into()),
    };
    let row = (lat.floor() + 1.0 - lat) * (side - 1) as f64;
    let col = (lon - lon.floor()) * (side - 1) as f64;
    let (r, c) = (
        row.floor().clamp(0.0, (side - 1) as f64) as u64,
        col.floor().clamp(0.0, (side - 1) as f64) as u64,
    );
    let mut samples = [0i16; 4];
    for (i, (y, x)) in [
        (r, c),
        (r, (c + 1).min(side - 1)),
        ((r + 1).min(side - 1), c),
        ((r + 1).min(side - 1), (c + 1).min(side - 1)),
    ]
    .into_iter()
    .enumerate()
    {
        file.seek(SeekFrom::Start((y * side + x) * 2))
            .map_err(|e| e.to_string())?;
        let mut sample = [0u8; 2];
        file.read_exact(&mut sample).map_err(|e| e.to_string())?;
        samples[i] = i16::from_be_bytes(sample);
    }
    require(
        file.metadata().map_err(|e| e.to_string())?.len() == bytes,
        "SRTM tile changed size during read",
    )?;
    let valid = samples
        .iter()
        .copied()
        .filter(|&v| v != i16::MIN)
        .collect::<Vec<_>>();
    require(
        !valid.is_empty(),
        "SRTM has no valid elevation at this coordinate",
    )?;
    let value = if valid.len() < 4 {
        valid.iter().map(|&v| f64::from(v)).sum::<f64>() / valid.len() as f64
    } else {
        let (dr, dc) = (row - r as f64, col - c as f64);
        let north = f64::from(samples[0]) * (1.0 - dc) + f64::from(samples[1]) * dc;
        let south = f64::from(samples[2]) * (1.0 - dc) + f64::from(samples[3]) * dc;
        north * (1.0 - dr) + south * dr
    };
    elevation(value)
}
type Memo = VecDeque<(String, Value)>;
pub fn execute(mut params: Params) -> Result<Value, String> {
    params.config.check_cancelled()?;
    params.config.validate()?;
    let timeout_seconds = params.timeout_seconds.unwrap_or(
        if matches!(params.operation, Operation::Search | Operation::Metadata) {
            6.0
        } else {
            20.0
        },
    );
    require(
        timeout_seconds.is_finite() && (0.001..=120.0).contains(&timeout_seconds),
        "location timeout must be finite within 0.001..120 seconds",
    )?;
    let operation = match params.operation {
        Operation::Search => "search",
        Operation::Resolve => "resolve",
        Operation::Metadata => "metadata",
        Operation::Timezone => "timezone",
        Operation::Timezones => "timezones",
        Operation::Elevation => "elevation",
        Operation::Tile => "tile",
    };
    if matches!(params.operation, Operation::Timezones) {
        require(
            params.query.is_none()
                && params.lat.is_none()
                && params.lon.is_none()
                && params.path.is_none(),
            "timezones accepts points, not query/lat/lon/path",
        )?;
        let points = params.points.ok_or("missing timezone batch points")?;
        require(
            (1..=4096).contains(&points.len()),
            "timezone batch size must be 1..4096",
        )?;
        for &[lat, lon] in &points {
            coordinates(lat, lon)?;
        }
        let points = points
            .into_iter()
            .map(|[a, b]| [rounded(a), rounded(b)])
            .collect::<Vec<_>>();
        let (zones, proof) = timezone_lookup::lookup_batch(
            params
                .config
                .timezone_manifest
                .as_deref()
                .ok_or("local timezone dataset is not configured")?,
            &points,
        )?;
        return Ok(json!({"contract":"rust-location-v1","operation":operation,
            "result":{"timezone_names":zones,"timezone_data":proof},"memory_cache_hit":false,
            "runtime_python_used":false,"production_location_complete":false}));
    }
    require(
        params.points.is_none(),
        "points is only accepted for timezones",
    )?;
    let query = params.query.as_deref().unwrap_or("").trim();
    let point = if matches!(params.operation, Operation::Search | Operation::Resolve) {
        require(
            !query.is_empty()
                && query.len() <= 2048
                && params.lat.is_none()
                && params.lon.is_none()
                && params.path.is_none(),
            "location search requires a nonempty bounded query, not coordinates/path",
        )?;
        None
    } else {
        require(
            params.query.is_none(),
            "coordinate operation does not accept query",
        )?;
        let (lat, lon) = (
            params.lat.ok_or("missing latitude")?,
            params.lon.ok_or("missing longitude")?,
        );
        coordinates(lat, lon)?;
        require(
            params.path.is_none() || matches!(params.operation, Operation::Elevation),
            "path is only accepted for elevation",
        )?;
        Some((lat, lon))
    };
    let memoized = matches!(params.operation, Operation::Search | Operation::Metadata);
    let key = if memoized {
        serde_json::to_string(&json!({"operation":operation,"query":query,
        "point":point.map(|(a,b)|(rounded(a),rounded(b))),"config":params.config,
        "timeout":timeout_seconds,"allow_download":params.allow_download,
        "elevation_source":params.elevation_source,
        "timezone_manifest_identity":params.config.timezone_manifest.as_deref().and_then(
            |p|request_file::read::<Value>(p,128*1024).ok().map(|(_,identity)|identity))}))
        .map_err(|e| e.to_string())?
    } else {
        String::new()
    };
    static MEMO: OnceLock<Mutex<Memo>> = OnceLock::new();
    let memo = MEMO.get_or_init(|| Mutex::new(VecDeque::new()));
    if memoized && !params.force_refresh {
        let mut entries = memo.lock().map_err(|_| "location memo lock poisoned")?;
        if let Some(i) = entries.iter().position(|(k, _)| k == &key) {
            let entry = entries.remove(i).unwrap();
            let value = entry.1.clone();
            entries.push_back(entry);
            return Ok(
                json!({"contract":"rust-location-v1","operation":operation,"result":value,
                "memory_cache_hit":true,"source_freshness":"memoized-result","runtime_python_used":false,
                "production_location_complete":false}),
            );
        }
    }
    let result = match params.operation {
        Operation::Search | Operation::Resolve => params.config.search(
            query,
            timeout_seconds,
            matches!(params.operation, Operation::Search),
            params.allow_download,
            params.force_refresh,
            params.elevation_source,
        )?,
        Operation::Metadata => {
            let (a, b) = point.unwrap();
            params.config.point_metadata(
                a,
                b,
                timeout_seconds,
                params.allow_download,
                params.elevation_source,
            )?
        }
        Operation::Timezone => {
            let (a, b) = point.unwrap();
            let (zone, proof) = params.config.local_timezone(a, b)?;
            json!({"timezone_name":zone,"timezone_data":proof})
        }
        Operation::Elevation => {
            let (a, b) = point.unwrap();
            let (value, path) = if let Some(path) = params.path {
                (read_srtm(&path, a, b)?, path)
            } else {
                params
                    .config
                    .local_elevation(a, b, timeout_seconds, params.allow_download)?
            };
            json!({"elevation_m":value,"path":path})
        }
        Operation::Tile => {
            let (a, b) = point.unwrap();
            json!({"name":tile(a,b)?})
        }
        Operation::Timezones => unreachable!(),
    };
    if memoized {
        let mut entries = memo.lock().map_err(|_| "location memo lock poisoned")?;
        entries.retain(|(k, _)| k != &key);
        entries.push_back((key, result.clone()));
        while entries.len() > 512 {
            entries.pop_front();
        }
    }
    Ok(
        json!({"contract":"rust-location-v1","operation":operation,"result":result,"memory_cache_hit":false,
        "runtime_python_used":false,"production_location_complete":false}),
    )
}

pub(crate) fn desktop_dispatch(
    method: &str,
    params: &Value,
    config: &Config,
    allow_download: bool,
    cancellation: Arc<AtomicBool>,
) -> Result<Value, String> {
    // search 内部也会触发 point metadata，因此两档方法共用同一来源校验。
    let elevation_source: ElevationSource = match params.get("elevation_source") {
        None | Some(Value::Null) => ElevationSource::Auto,
        Some(value) => serde_json::from_value(value.clone())
            .map_err(|e| format!("invalid elevation_source: {e}"))?,
    };
    let mut config = config.clone();
    config.cancellation = Some(cancellation);
    let call = |operation, query, lat, lon, timeout_seconds| {
        execute(Params {
            operation,
            query,
            lat,
            lon,
            path: None,
            points: None,
            config: config.clone(),
            timeout_seconds: Some(timeout_seconds),
            allow_download,
            force_refresh: false,
            elevation_source,
        })
        .map(|value| value["result"].clone())
    };
    let (lat, lon, name) = if method == "search" {
        let query = params
            .get("query")
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim();
        require(
            !query.is_empty() && query.chars().count() <= 200,
            "search requires a query of 1..200 characters",
        )?;
        let candidate = call(Operation::Search, Some(query.into()), None, None, 6.0)?;
        (
            number(&candidate["lat"])?,
            number(&candidate["lon"])?,
            Some(candidate["name"].clone()),
        )
    } else if method == "metadata" {
        (number(&params["lat"])?, number(&params["lon"])?, None)
    } else {
        return Err("unsupported desktop location method".into());
    };
    config.check_cancelled()?;
    let metadata = call(Operation::Metadata, None, Some(lat), Some(lon), 8.0)?;
    config.check_cancelled()?;
    let mut result = json!({"lat":lat,"lon":lon,
        "timezone_name":metadata["timezone_name"],"elevation_m":metadata["elevation_m"]});
    if let Some(name) = name {
        result["name"] = name;
    }
    Ok(result)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rounding_and_tile_edges() {
        assert_eq!(rounded(1.23445), 1.2345);
        assert_eq!(rounded(1.23455), 1.2346);
        assert_eq!(tile(-0.1, -0.1).unwrap(), "S01W001");
        assert_eq!(tile(-56.0, 180.0).unwrap(), "S56E180");
        assert!(tile(60.0, 0.0).is_err());
        assert!(tile(-56.001, 0.0).is_err());
    }

    // ------------------------------------------------ elevation_source 三档（v1.1.1 §1）
    // 离线口径：HTTP 端点指向不可达 loopback（url() 允许 loopback HTTP），
    // 需要「provider 可用」反证时挂一个只回固定 JSON 的本地 TcpListener。
    const LAT: f64 = 39.9042;
    const LON: f64 = 116.4074;
    const TILE_NAME: &str = "N39E116";
    const TILE_ELEVATION: i16 = 1500;

    fn timezone_manifest() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../resources/app-data/timezones/manifest.json")
    }

    fn offline_config(root: &Path) -> Config {
        Config {
            geocoding_url: "http://127.0.0.1:1/v1/search".into(),
            metadata_url: "http://127.0.0.1:1/v1/forecast".into(),
            nominatim_url: "http://127.0.0.1:1/search".into(),
            srtm_base_url: "http://127.0.0.1:1/skadi".into(),
            location_cache_path: root.join("locations.json"),
            srtm_cache_dir: root.join("srtm"),
            timezone_manifest: Some(timezone_manifest()),
            request_attempts: 1,
            retry_seconds: 0.0,
            lock_timeout_seconds: 5.0,
            cancellation: None,
        }
    }

    /// 预放假 SRTM 瓦片：3601×3601 big-endian int16，全瓦片常量海拔，
    /// read_srtm 双线性插值后必然等于该常量（与坐标无关）。
    fn fake_tile(root: &Path) -> PathBuf {
        let dir = root.join("srtm");
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join(format!("{TILE_NAME}.hgt"));
        let mut file = File::create(&path).unwrap();
        let row = TILE_ELEVATION.to_be_bytes().repeat(3601);
        for _ in 0..3601 {
            file.write_all(&row).unwrap();
        }
        file.sync_all().unwrap();
        assert_eq!(file.metadata().unwrap().len(), 25_934_402);
        path
    }

    /// 单线程顺序应答的本地 JSON provider（Connection: close），
    /// 返回 base URL；线程随测试进程退出回收。
    fn spawn_json_server(payload: Value) -> String {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { break };
                let mut reader = std::io::BufReader::new(stream.try_clone().unwrap());
                let mut line = String::new();
                loop {
                    line.clear();
                    match std::io::BufRead::read_line(&mut reader, &mut line) {
                        Ok(0) => break,
                        Ok(_) if line == "\r\n" || line == "\n" => break,
                        Ok(_) => {}
                        Err(_) => break,
                    }
                }
                let body = serde_json::to_vec(&payload).unwrap();
                let head = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\
                     Content-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                let _ = stream.write_all(head.as_bytes());
                let _ = stream.write_all(&body);
                let _ = stream.flush();
            }
        });
        format!("http://{addr}")
    }

    fn assert_local_srtm(value: &Value) {
        assert_eq!(value["source"], "local-timezone+srtm");
        assert_eq!(value["elevation_m"].as_f64().unwrap(), f64::from(TILE_ELEVATION));
        assert_eq!(value["timezone_name"], "Asia/Shanghai");
        assert!(value["srtm_path"].as_str().unwrap().ends_with(&format!("{TILE_NAME}.hgt")));
    }

    #[test]
    fn elevation_source_params_default_and_invalid() {
        let base = json!({"operation":"metadata","lat":LAT,"lon":LON});
        let params: Params = serde_json::from_value(base.clone()).unwrap();
        assert!(params.elevation_source == ElevationSource::Auto);
        for (text, expected) in [
            ("auto", ElevationSource::Auto),
            ("http", ElevationSource::Http),
            ("srtm", ElevationSource::Srtm),
        ] {
            let mut value = base.clone();
            value["elevation_source"] = json!(text);
            let params: Params = serde_json::from_value(value).unwrap();
            assert!(params.elevation_source == expected);
        }
        for bad in [json!("bogus"), json!(""), json!(3), json!(["srtm"])] {
            let mut value = base.clone();
            value["elevation_source"] = bad;
            assert!(serde_json::from_value::<Params>(value).is_err());
        }
    }

    #[test]
    fn http_mode_failure_propagates_without_srtm_fallback() {
        let dir = tempfile::tempdir().unwrap();
        fake_tile(dir.path());
        let config = offline_config(dir.path());
        // 瓦片就绪、下载允许：http 档失败仍必须透传错误，不得静默落 SRTM。
        let error = config
            .point_metadata(LAT, LON, 0.5, true, ElevationSource::Http)
            .unwrap_err();
        assert!(error.contains("location provider"), "{error}");
    }

    #[test]
    fn auto_mode_falls_back_to_local_timezone_and_srtm() {
        let dir = tempfile::tempdir().unwrap();
        fake_tile(dir.path());
        let config = offline_config(dir.path());
        let value = config
            .point_metadata(LAT, LON, 0.5, true, ElevationSource::Auto)
            .unwrap();
        assert_local_srtm(&value);
    }

    #[test]
    fn auto_and_http_prefer_provider_when_available() {
        let dir = tempfile::tempdir().unwrap();
        fake_tile(dir.path());
        let base = spawn_json_server(json!({"timezone":"UTC","elevation":9999.0}));
        let mut config = offline_config(dir.path());
        config.metadata_url = format!("{base}/v1/forecast");
        for source in [ElevationSource::Auto, ElevationSource::Http] {
            let value = config.point_metadata(LAT, LON, 2.0, true, source).unwrap();
            assert_eq!(value["source"], "open-meteo-point-metadata");
            assert_eq!(value["elevation_m"].as_f64().unwrap(), 9999.0);
            assert_eq!(value["timezone_name"], "UTC");
        }
    }

    #[test]
    fn srtm_mode_skips_http_even_when_provider_available() {
        let dir = tempfile::tempdir().unwrap();
        fake_tile(dir.path());
        // provider 若被错误访问会返回 9999/UTC；srtm 档必须跳过它。
        let base = spawn_json_server(json!({"timezone":"UTC","elevation":9999.0}));
        let mut config = offline_config(dir.path());
        config.metadata_url = format!("{base}/v1/forecast");
        let value = config
            .point_metadata(LAT, LON, 2.0, true, ElevationSource::Srtm)
            .unwrap();
        assert_local_srtm(&value);
    }

    #[test]
    fn srtm_mode_cache_hit_with_downloads_disabled() {
        let dir = tempfile::tempdir().unwrap();
        let tile = fake_tile(dir.path());
        let config = offline_config(dir.path());
        // 缓存命中 + allow_download=false：不触发任何下载/HTTP。
        let value = config
            .point_metadata(LAT, LON, 0.5, false, ElevationSource::Srtm)
            .unwrap();
        assert_local_srtm(&value);
        assert_eq!(value["srtm_path"].as_str().unwrap(), tile.to_str().unwrap());
        // 缺瓦片且禁止下载：报错文案明确。
        let empty = tempfile::tempdir().unwrap();
        let config = offline_config(empty.path());
        let error = config
            .point_metadata(LAT, LON, 0.5, false, ElevationSource::Srtm)
            .unwrap_err();
        assert_eq!(error, "SRTM tile is missing; downloads disabled");
    }

    #[test]
    fn execute_memo_identity_includes_elevation_source() {
        let dir = tempfile::tempdir().unwrap();
        fake_tile(dir.path());
        let config = offline_config(dir.path());
        let params = |source: &str| -> Params {
            serde_json::from_value(json!({
                "operation":"metadata","lat":LAT,"lon":LON,"config":config,
                "timeout_seconds":0.5,"allow_download":true,"elevation_source":source
            }))
            .unwrap()
        };
        let first = execute(params("srtm")).unwrap();
        assert_eq!(first["memory_cache_hit"], false);
        assert_local_srtm(&first["result"]);
        let second = execute(params("srtm")).unwrap();
        assert_eq!(second["memory_cache_hit"], true);
        // 换档不得命中 srtm 的 memo：http 档在不可达 URL 下直接报错。
        let error = execute(params("http")).unwrap_err();
        assert!(error.contains("location provider"), "{error}");
    }

    #[test]
    fn desktop_dispatch_rejects_invalid_elevation_source() {
        let dir = tempfile::tempdir().unwrap();
        let config = offline_config(dir.path());
        let cancellation = Arc::new(AtomicBool::new(false));
        for (method, mut params) in [
            ("metadata", json!({"lat":LAT,"lon":LON})),
            ("search", json!({"query":"北京"})),
        ] {
            params["elevation_source"] = json!("bogus");
            let error =
                desktop_dispatch(method, &params, &config, true, cancellation.clone()).unwrap_err();
            assert!(error.starts_with("invalid elevation_source"), "{error}");
        }
    }

    #[test]
    fn desktop_dispatch_srtm_default_auto_and_http() {
        let dir = tempfile::tempdir().unwrap();
        fake_tile(dir.path());
        let config = offline_config(dir.path());
        let cancellation = Arc::new(AtomicBool::new(false));
        let point = json!({"lat":LAT,"lon":LON});
        // srtm 档透传：禁止下载仍命中缓存瓦片。
        let mut params = point.clone();
        params["elevation_source"] = json!("srtm");
        let result =
            desktop_dispatch("metadata", &params, &config, false, cancellation.clone()).unwrap();
        assert_eq!(result["elevation_m"].as_f64().unwrap(), f64::from(TILE_ELEVATION));
        assert_eq!(result["timezone_name"], "Asia/Shanghai");
        // 缺省与 null 均按 auto：HTTP 不可用/禁下载时落 local-timezone+srtm。
        for params in [point.clone(), {
            let mut value = point.clone();
            value["elevation_source"] = Value::Null;
            value
        }] {
            let result =
                desktop_dispatch("metadata", &params, &config, false, cancellation.clone()).unwrap();
            assert_eq!(result["elevation_m"].as_f64().unwrap(), f64::from(TILE_ELEVATION));
        }
        // http 档：禁下载时直接报错，即使瓦片就绪也不落 SRTM。
        let mut params = point;
        params["elevation_source"] = json!("http");
        let error =
            desktop_dispatch("metadata", &params, &config, false, cancellation.clone()).unwrap_err();
        assert!(error.contains("location downloads disabled"), "{error}");
    }

    #[test]
    fn search_passthrough_srtm_and_http_via_nominatim() {
        let dir = tempfile::tempdir().unwrap();
        fake_tile(dir.path());
        let base = spawn_json_server(json!([{
            "lat":"39.9042","lon":"116.4074","name":"北京",
            "display_name":"北京市, 中国","address":{"city":"北京市"}
        }]));
        let mut config = offline_config(dir.path());
        config.nominatim_url = format!("{base}/search");
        // srtm 档：nominatim 命中后 point metadata 走本地时区+缓存瓦片。
        let value = config
            .search("北京", 2.0, false, true, true, ElevationSource::Srtm)
            .unwrap();
        assert_eq!(value["source"], "nominatim-openstreetmap");
        assert_eq!(value["elevation_m"].as_f64().unwrap(), f64::from(TILE_ELEVATION));
        assert_eq!(value["timezone_name"], "Asia/Shanghai");
        // http 档：metadata URL 不可达 → search 透传错误（refresh 绕过缓存）。
        let error = config
            .search("北京", 2.0, false, true, true, ElevationSource::Http)
            .unwrap_err();
        assert!(error.contains("location provider"), "{error}");
    }
    // ------------------------------------------------ 地形下载契约（阶段 D）

    /// 本地 gzip 服务器：应答假 SRTM 瓦片（.hgt.gz），带 Content-Length。
    fn spawn_tile_server(root: &Path) -> String {
        let dir = root.join("tile-src");
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join(format!("{TILE_NAME}.hgt.gz"));
        let row = TILE_ELEVATION.to_be_bytes().repeat(3601);
        let mut tile = Vec::new();
        for _ in 0..3601 {
            tile.extend_from_slice(&row);
        }
        let mut encoder = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
        std::io::Write::write_all(&mut encoder, &tile).unwrap();
        let body = encoder.finish().unwrap();
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { break };
                let mut reader = std::io::BufReader::new(stream.try_clone().unwrap());
                let mut line = String::new();
                loop {
                    line.clear();
                    match std::io::BufRead::read_line(&mut reader, &mut line) {
                        Ok(0) | Err(_) => break,
                        Ok(_) if line == "\r\n" || line == "\n" => break,
                        Ok(_) => {}
                    }
                }
                let head = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n\
                     Content-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                let _ = stream.write_all(head.as_bytes());
                let _ = stream.write_all(&body);
                let _ = stream.flush();
            }
        });
        format!("http://{addr}")
    }

    /// 路径感知 Skadi 服务器：仅 served 集合内的瓦片返回 200，其余 404，
    /// 用于 bulk 下载的海洋/缺失分类测试。
    fn spawn_skadi_server(root: &Path, served: &[&str]) -> String {
        let dir = root.join("tile-src");
        fs::create_dir_all(&dir).unwrap();
        let row = TILE_ELEVATION.to_be_bytes().repeat(3601);
        let mut tile = Vec::new();
        for _ in 0..3601 {
            tile.extend_from_slice(&row);
        }
        let mut encoder = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
        std::io::Write::write_all(&mut encoder, &tile).unwrap();
        let body = encoder.finish().unwrap();
        let served: Vec<String> = served.iter().map(|name| name.to_string()).collect();
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { break };
                let mut reader = std::io::BufReader::new(stream.try_clone().unwrap());
                let mut request_line = String::new();
                if std::io::BufRead::read_line(&mut reader, &mut request_line).is_err() {
                    continue;
                }
                loop {
                    let mut line = String::new();
                    match std::io::BufRead::read_line(&mut reader, &mut line) {
                        Ok(0) | Err(_) => break,
                        Ok(_) if line == "\r\n" || line == "\n" => break,
                        Ok(_) => {}
                    }
                }
                let name = request_line
                    .split_whitespace()
                    .nth(1)
                    .and_then(|path| path.rsplit('/').next())
                    .and_then(|file| file.strip_suffix(".hgt.gz"))
                    .unwrap_or("");
                let head = if served.iter().any(|item| item == name) {
                    format!(
                        "HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n\
                         Content-Length: {}\r\nConnection: close\r\n\r\n",
                        body.len()
                    )
                } else {
                    "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                        .to_string()
                };
                let _ = stream.write_all(head.as_bytes());
                if served.iter().any(|item| item == name) {
                    let _ = stream.write_all(&body);
                }
                let _ = stream.flush();
            }
        });
        format!("http://{addr}")
    }

    fn progress_collector() -> (Arc<Mutex<Vec<Value>>>, impl Fn(Value) -> Result<(), String> + Send + Sync) {
        let events: Arc<Mutex<Vec<Value>>> = Arc::new(Mutex::new(Vec::new()));
        let sink = events.clone();
        let callback = move |event: Value| {
            sink.lock().unwrap().push(event);
            Ok(())
        };
        (events, callback)
    }

    #[test]
    fn terrain_inventory_lists_tiles_and_focused_state() {
        let dir = tempfile::tempdir().unwrap();
        fake_tile(dir.path());
        let config = offline_config(dir.path());
        let value = config
            .terrain_inventory(&json!({"lat": LAT, "lon": LON}))
            .unwrap();
        assert_eq!(value["contract"], "rust-terrain-inventory-v1");
        assert_eq!(value["coverage_ok"], true);
        assert_eq!(value["tile"]["name"], TILE_NAME);
        assert_eq!(value["tile"]["exists"], true);
        assert!(value["tile"]["bytes"].as_u64().unwrap() > 0);
        let tiles = value["tiles"].as_array().unwrap();
        assert_eq!(tiles.len(), 1);
        assert_eq!(tiles[0]["name"], TILE_NAME);
        // 未给坐标时只列本地瓦片，不带焦点。
        let value = config.terrain_inventory(&json!({})).unwrap();
        assert!(value.get("coverage_ok").is_none());
        assert_eq!(value["tiles"].as_array().unwrap().len(), 1);
        // 超界纬度是状态而非错误。
        let value = config
            .terrain_inventory(&json!({"lat": 65.0, "lon": LON}))
            .unwrap();
        assert_eq!(value["coverage_ok"], false);
        assert!(value.get("tile").is_none());
        // 无效坐标是错误。
        assert!(config.terrain_inventory(&json!({"lat": 200.0, "lon": 0.0})).is_err());
    }

    #[test]
    fn terrain_download_exists_reuses_local_tile_without_network() {
        let dir = tempfile::tempdir().unwrap();
        fake_tile(dir.path());
        let config = offline_config(dir.path());
        let value = config
            .terrain_download(&json!({"lat": LAT, "lon": LON}), &|_| Ok(()))
            .unwrap();
        assert_eq!(value["status"], "exists");
        assert_eq!(value["tile"], TILE_NAME);
        assert_eq!(value["elevation_m"].as_f64().unwrap(), f64::from(TILE_ELEVATION));
    }

    #[test]
    fn terrain_download_out_of_coverage_is_a_status() {
        let dir = tempfile::tempdir().unwrap();
        let config = offline_config(dir.path());
        let value = config
            .terrain_download(&json!({"lat": 65.0, "lon": LON}), &|_| Ok(()))
            .unwrap();
        assert_eq!(value["status"], "out_of_coverage");
    }

    #[test]
    fn terrain_download_fetches_publishes_and_reports_progress() {
        let dir = tempfile::tempdir().unwrap();
        let base = spawn_tile_server(dir.path());
        let mut config = offline_config(dir.path());
        config.srtm_base_url = format!("{base}/skadi");
        let (events, progress) = progress_collector();
        let value = config
            .terrain_download(&json!({"lat": LAT, "lon": LON}), &progress)
            .unwrap();
        assert_eq!(value["status"], "downloaded");
        assert_eq!(value["tile"], TILE_NAME);
        assert_eq!(value["elevation_m"].as_f64().unwrap(), f64::from(TILE_ELEVATION));
        let target = dir.path().join("srtm").join(format!("{TILE_NAME}.hgt"));
        assert!(target.is_file());
        assert!(config
            .terrain_inventory(&json!({"lat": LAT, "lon": LON}))
            .unwrap()["tile"]["exists"]
            .as_bool()
            .unwrap());
        // 进度含 downloading（百分比/字节数）与 verifying 两阶段。
        let events = events.lock().unwrap();
        let stages: Vec<String> = events
            .iter()
            .filter_map(|event| event["terrain"]["stage"].as_str().map(str::to_owned))
            .collect();
        assert!(stages.contains(&"downloading".to_owned()), "{stages:?}");
        assert!(stages.contains(&"verifying".to_owned()), "{stages:?}");
        let downloading = events
            .iter()
            .find(|event| event["terrain"]["stage"] == "downloading")
            .unwrap();
        assert!(downloading["terrain"]["total_bytes"].as_u64().unwrap() > 0);
    }

    #[test]
    fn terrain_download_honours_cancellation_per_chunk() {
        let dir = tempfile::tempdir().unwrap();
        let base = spawn_tile_server(dir.path());
        let mut config = offline_config(dir.path());
        config.srtm_base_url = format!("{base}/skadi");
        let cancel = Arc::new(AtomicBool::new(true));
        config.cancellation = Some(cancel);
        let error = config
            .terrain_download(&json!({"lat": LAT, "lon": LON}), &|_| Ok(()))
            .unwrap_err();
        assert!(error.contains("cancelled"), "{error}");
        assert!(!dir.path().join("srtm").join(format!("{TILE_NAME}.hgt")).exists());
    }

    #[test]
    fn terrain_bulk_download_skips_existing_and_counts_ocean_404() {
        let dir = tempfile::tempdir().unwrap();
        let base = spawn_skadi_server(dir.path(), &["N00E000"]);
        let mut config = offline_config(dir.path());
        config.srtm_base_url = format!("{base}/skadi");
        // 候选 2x2 = S01E000/S01E001/N00E000/N00E001；服务器只服务 N00E000。
        let value = config
            .terrain_download(&json!({
                "mode": "global",
                "bounds": {"south": -1, "north": 0, "west": 0, "east": 1},
            }), &|_| Ok(()))
            .unwrap();
        assert_eq!(value["status"], "done");
        assert_eq!(value["candidates"], 4);
        assert_eq!(value["downloaded"], 1);
        assert_eq!(value["absent"], 3);
        assert_eq!(value["failed"], 0);
        let target = dir.path().join("srtm").join("N00E000.hgt");
        assert!(target.is_file());
        // 断点续传：重跑时已有瓦片直接跳过，不再下载。
        let again = config
            .terrain_download(&json!({
                "mode": "global",
                "bounds": {"south": -1, "north": 0, "west": 0, "east": 1},
            }), &|_| Ok(()))
            .unwrap();
        assert_eq!(again["existing"], 1);
        assert_eq!(again["downloaded"], 0);
        assert_eq!(again["absent"], 3);
    }

    #[test]
    fn terrain_bulk_download_reports_cancelled_and_keeps_tiles() {
        let dir = tempfile::tempdir().unwrap();
        let base = spawn_tile_server(dir.path());
        let mut config = offline_config(dir.path());
        config.srtm_base_url = format!("{base}/skadi");
        config.cancellation = Some(Arc::new(AtomicBool::new(true)));
        let value = config
            .terrain_download(&json!({
                "mode": "global",
                "bounds": {"south": -1, "north": 0, "west": 0, "east": 1},
            }), &|_| Ok(()))
            .unwrap();
        assert_eq!(value["status"], "cancelled");
        assert_eq!(value["downloaded"], 0);
    }

    #[test]
    fn terrain_bulk_bounds_are_validated() {
        let dir = tempfile::tempdir().unwrap();
        let config = offline_config(dir.path());
        for bad in [
            json!({"mode": "global", "bounds": {"south": -60, "north": 0, "west": 0, "east": 1}}),
            json!({"mode": "global", "bounds": {"south": 0, "north": -1, "west": 0, "east": 1}}),
            json!({"mode": "global", "bounds": {"south": 0, "north": 0, "west": 170, "east": 190}}),
        ] {
            assert!(config.terrain_download(&bad, &|_| Ok(())).is_err(), "{bad}");
        }
    }

    #[test]
    fn terrain_inventory_estimate_honours_bounds() {
        let dir = tempfile::tempdir().unwrap();
        let config = offline_config(dir.path());
        // offline_config 的 S3 地址不可达：估算为 None 但候选数/边界照常返回。
        let value = config
            .terrain_inventory(&json!({
                "estimate_global": true,
                "bounds": {"south": 18, "north": 20, "west": 110, "east": 112},
            }))
            .unwrap();
        assert_eq!(value["global"]["candidates"], 9);
        assert_eq!(value["global"]["bounds"]["north"], 20);
        assert_eq!(value["global"]["existing"], 0);
        // 缺省 bounds 仍是全球候选集。
        let value = config.terrain_inventory(&json!({"estimate_global": true})).unwrap();
        assert_eq!(value["global"]["candidates"], 41760);
        // 非法 bounds 报错。
        assert!(config
            .terrain_inventory(&json!({
                "estimate_global": true,
                "bounds": {"south": 90, "north": 95, "west": 0, "east": 1},
            }))
            .is_err());
        // 采样位置按候选集长度取比例，小区域集（这里恰好 12 块）不越界。
        let value = config
            .terrain_inventory(&json!({
                "estimate_global": true,
                "bounds": {"south": 30, "north": 32, "west": 100, "east": 105},
            }))
            .unwrap();
        assert_eq!(value["global"]["candidates"], 18);
    }
}
