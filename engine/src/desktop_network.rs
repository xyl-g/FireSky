//! Explicit bounded download probes. Samples never enter forecast caches.
use chrono::{DateTime, Duration as ChronoDuration, Timelike, Utc};
use reqwest::{blocking::Client, Method, Url};
use serde_json::{json, Value};
use std::{
    io::{Read, Write},
    time::{Duration, Instant, SystemTime},
};

const MAX_SAMPLE: usize = 4 * 1024 * 1024;
const MAX_JSON: usize = 1024 * 1024;
const DATASET: &str = "cams-global-atmospheric-composition-forecasts";

#[derive(Debug)]
struct Failure {
    state: &'static str,
    message: String,
}
type ProbeResult<T> = Result<T, Failure>;
fn fail(state: &'static str, message: impl Into<String>) -> Failure {
    Failure {
        state,
        message: message.into(),
    }
}
fn invalid(message: impl Into<String>) -> Failure {
    fail("invalid_response", message)
}
fn http_error(status: u16) -> Failure {
    let (state, message) = match status {
        401 => ("unauthorized", "身份验证失败，请检查 ADS Token。"),
        403 => (
            "forbidden",
            "服务拒绝访问；请检查网络限制及 CAMS 数据集条款授权。",
        ),
        404 => ("unavailable", "测试时次或数据资源暂不可用，请稍后重试。"),
        429 => ("rate_limited", "上游限流，请稍后重试。"),
        _ => ("http_error", "上游服务返回错误，请稍后重试。"),
    };
    fail(state, format!("HTTP {status} · {message}"))
}
fn request_error(error: reqwest::Error) -> Failure {
    if error.is_timeout() {
        fail("timeout", "连接或读取超时，尚未证明数据可下载。")
    } else if error.is_connect() {
        fail(
            "connection_error",
            "连接失败，请检查证书、防火墙、代理或网络限制。",
        )
    } else {
        fail("error", "探测未完成，请检查本地配置或稍后重试。")
    }
}
fn safe_https(raw: &str) -> ProbeResult<Url> {
    let url = Url::parse(raw).map_err(|_| invalid("下载地址无效。"))?;
    if url.scheme() != "https"
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
    {
        return Err(invalid("服务返回了不安全的下载地址。"));
    }
    Ok(url)
}
fn api_link(base: &str, href: &str) -> ProbeResult<String> {
    let url = safe_https(base)?
        .join(href)
        .map_err(|_| invalid("ADS 链接无效。"))?;
    let url = safe_https(url.as_str())?;
    if url.host_str() != Some("ads.atmosphere.copernicus.eu")
        || url.port_or_known_default() != Some(443)
        || !url.path().starts_with("/api/retrieve/v1/")
        || url.query().is_some()
    {
        return Err(invalid("ADS API 链接超出安全范围。"));
    }
    Ok(url.to_string())
}
fn link<'a>(document: &'a Value, relation: &str) -> ProbeResult<&'a str> {
    document["links"]
        .as_array()
        .and_then(|links| links.iter().find(|link| link["rel"] == relation))
        .and_then(|link| link["href"].as_str())
        .ok_or_else(|| invalid("ADS 响应缺少任务链接。"))
}

struct Transfer<'a, F> {
    source: &'a str,
    title: &'a str,
    host: &'a str,
    progress: &'a F,
    started: Instant,
    deadline: Instant,
    bytes: usize,
    client: Client,
    cams_waiting: bool,
}
impl<'a, F: Fn(Value) -> Result<(), String>> Transfer<'a, F> {
    fn new(source: &'a str, ec_source: &'a str, progress: &'a F) -> ProbeResult<Self> {
        let (title, host) = match source {
            "gfs" => ("GFS · NOAA NOMADS", "nomads.ncep.noaa.gov"),
            "ec" => (
                "EC · ECMWF Open Data",
                match ec_source {
                    "aws" => "ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
                    "google" => "storage.googleapis.com",
                    _ => "data.ecmwf.int",
                },
            ),
            "cams" => ("CAMS · Copernicus ADS", "ads.atmosphere.copernicus.eu"),
            _ => return Err(fail("invalid_source", "下载源设置无效。")),
        };
        // Mirror the production ADS client posture: honor the ambient proxy
        // environment instead of forcing a direct connection.
        let builder = Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .referer(false)
            .retry(reqwest::retry::never())
            .connect_timeout(Duration::from_secs(8))
            .user_agent("firesky-rust/0.1");
        let client = builder.build().map_err(request_error)?;
        let started = Instant::now();
        Ok(Self {
            source,
            title,
            host,
            progress,
            started,
            deadline: started + Duration::from_secs(if source == "cams" { 90 } else { 40 }),
            bytes: 0,
            client,
            cams_waiting: false,
        })
    }
    fn row(&self, state: &str, detail: &str) -> Value {
        json!({
            "source":self.source, "title":self.title, "host":self.host,
            "state":state, "detail":detail, "bytes_received":self.bytes,
            "elapsed_ms":self.started.elapsed().as_millis() as u64
        })
    }
    fn tick(&self, detail: &str) -> ProbeResult<Duration> {
        (self.progress)(json!({
            "source":self.source, "stage":"network_probe",
            "probe":self.row("testing", detail), "percent":0
        }))
        .map_err(|message| fail("cancelled", message))?;
        self.deadline
            .checked_duration_since(Instant::now())
            .filter(|duration| !duration.is_zero())
            .ok_or_else(|| fail("timeout", "探测超时，尚未证明数据可下载。"))
    }
    fn read(
        &mut self,
        method: Method,
        raw: &str,
        token: Option<&str>,
        body: Option<&Value>,
        limit: usize,
        range: Option<(u64, usize)>,
    ) -> ProbeResult<Vec<u8>> {
        let mut url = safe_https(raw)?;
        for _ in 0..6 {
            let remaining = self.tick("正在下载并校验数据样本")?;
            let mut request = self
                .client
                .request(method.clone(), url.clone())
                .header("Accept-Encoding", "identity")
                .timeout(remaining.min(Duration::from_secs(8)));
            if let Some(token) = token {
                // API tokens are never attached to assets or redirects.
                api_link(url.as_str(), url.as_str())?;
                let mut header = reqwest::header::HeaderValue::from_str(token)
                    .map_err(|_| fail("not_configured", "CAMS 凭据无效。"))?;
                header.set_sensitive(true);
                request = request.header("PRIVATE-TOKEN", header);
            }
            if let Some(body) = body {
                request = request
                    .header("Content-Type", "application/json")
                    .body(serde_json::to_vec(body).map_err(|_| invalid("请求 JSON 无效。"))?);
            }
            if let Some((offset, length)) = range {
                request = request.header(
                    "Range",
                    format!("bytes={offset}-{}", offset + length as u64 - 1),
                );
            }
            let mut response = request.send().map_err(request_error)?;
            let status = response.status().as_u16();
            if (300..400).contains(&status) {
                if token.is_some() {
                    return Err(invalid("ADS API 发生重定向，已停止以保护 Token。"));
                }
                let location = response
                    .headers()
                    .get("Location")
                    .and_then(|value| value.to_str().ok())
                    .ok_or_else(|| invalid("下载重定向缺少目标地址。"))?;
                url = safe_https(
                    url.join(location)
                        .map_err(|_| invalid("下载重定向地址无效。"))?
                        .as_str(),
                )?;
                continue;
            }
            if ![200, 201, 202, 206].contains(&status) {
                return Err(http_error(status));
            }
            if let Some((offset, length)) = range {
                let expected = format!("bytes {offset}-{}/", offset + length as u64 - 1);
                if status != 206
                    || !response
                        .headers()
                        .get("Content-Range")
                        .and_then(|value| value.to_str().ok())
                        .is_some_and(|value| value.starts_with(&expected))
                {
                    return Err(invalid(
                        "服务器未按 Range 返回数据，已停止以避免整文件下载。",
                    ));
                }
            }
            if response
                .content_length()
                .is_some_and(|bytes| bytes > limit as u64)
            {
                return Err(fail("too_large", "响应超出小样本上限，已停止下载。"));
            }
            let mut payload = Vec::new();
            let mut chunk = [0; 16384];
            loop {
                let count = response
                    .read(&mut chunk)
                    .map_err(|_| fail("connection_error", "数据样本读取失败，尚未验证下载。"))?;
                if count == 0 {
                    break;
                }
                self.bytes += count;
                if payload.len() + count > limit {
                    return Err(fail("too_large", "响应超出小样本上限，已停止下载。"));
                }
                payload.extend_from_slice(&chunk[..count]);
                self.tick("正在下载并校验数据样本")?;
            }
            if range.is_some_and(|(_, length)| length != payload.len()) {
                return Err(invalid("数据样本传输不完整。"));
            }
            return Ok(payload);
        }
        Err(invalid("下载重定向次数过多。"))
    }
    fn json(
        &mut self,
        method: Method,
        url: &str,
        token: &str,
        body: Option<&Value>,
    ) -> ProbeResult<Value> {
        serde_json::from_slice(&self.read(method, url, Some(token), body, MAX_JSON, None)?)
            .map_err(|_| invalid("服务返回的不是有效 JSON 数据。"))
    }
}

fn validate_grib(payload: &[u8]) -> ProbeResult<()> {
    if payload.len() < 20 || &payload[..4] != b"GRIB" || &payload[payload.len() - 4..] != b"7777" {
        return Err(fail(
            "invalid_data",
            "下载内容不是完整 GRIB 数据，可能被代理或登录页面替换。",
        ));
    }
    let size = match payload[7] {
        1 => u32::from_be_bytes([0, payload[4], payload[5], payload[6]]) as u64,
        2 => u64::from_be_bytes(payload[8..16].try_into().unwrap()),
        _ => return Err(fail("invalid_data", "GRIB 数据版本无效。")),
    };
    if size != payload.len() as u64 {
        return Err(fail("invalid_data", "GRIB 数据长度不匹配。"));
    }
    let decode = || -> Result<(), String> {
        let mut sample =
            tempfile::NamedTempFile::new().map_err(|_| "cannot create probe sample")?;
        sample
            .write_all(payload)
            .map_err(|_| "cannot write probe sample")?;
        let codes = crate::grib::EcCodes::load()?;
        let mut fields = 0;
        codes.visit_file(sample.path(), 1, |message| {
            let count = message.size(b"values\0")?;
            if count == 0 || count > 5_000_000 {
                return Err("invalid probe field point count".into());
            }
            message.values(count)?;
            fields += 1;
            Ok(())
        })?;
        if fields != 1 {
            return Err("probe must contain one field".into());
        }
        Ok(())
    };
    decode().map_err(|_| fail("invalid_data", "GRIB 样本无法解码，请检查 ecCodes 安装。"))
}

fn gfs<F: Fn(Value) -> Result<(), String>>(
    transfer: &mut Transfer<'_, F>,
    now: DateTime<Utc>,
) -> ProbeResult<String> {
    let adjusted = now - ChronoDuration::hours(6);
    let mut cycle = adjusted
        .with_hour(adjusted.hour() / 6 * 6)
        .unwrap()
        .with_minute(0)
        .unwrap()
        .with_second(0)
        .unwrap()
        .with_nanosecond(0)
        .unwrap();
    for _ in 0..3 {
        let mut url = safe_https("https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl")?;
        url.query_pairs_mut().extend_pairs([
            (
                "file",
                format!("gfs.t{}z.pgrb2.0p25.f000", cycle.format("%H")),
            ),
            (
                "dir",
                format!(
                    "/gfs.{}/{}/atmos",
                    cycle.format("%Y%m%d"),
                    cycle.format("%H")
                ),
            ),
            ("var_TMP", "on".into()),
            ("lev_850_mb", "on".into()),
            ("subregion", "".into()),
            ("leftlon", "120".into()),
            ("rightlon", "121".into()),
            ("toplat", "31".into()),
            ("bottomlat", "30".into()),
        ]);
        match transfer.read(Method::GET, url.as_str(), None, None, MAX_SAMPLE, None) {
            Ok(payload) => {
                validate_grib(&payload)?;
                return Ok(format!(
                    "小区域 GRIB 已下载并解码 · {}",
                    cycle.format("%m-%d %HZ")
                ));
            }
            Err(error) if error.state == "unavailable" => cycle -= ChronoDuration::hours(6),
            Err(error) => return Err(error),
        }
    }
    Err(fail(
        "unavailable",
        "最近三个 GFS 测试时次均不可用，请稍后重试。",
    ))
}

fn ec<F: Fn(Value) -> Result<(), String>>(
    transfer: &mut Transfer<'_, F>,
    now: DateTime<Utc>,
    source: &str,
) -> ProbeResult<String> {
    let root = match source {
        "ecmwf" => "https://data.ecmwf.int/forecasts",
        "aws" => "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
        "google" => "https://storage.googleapis.com/ecmwf-open-data",
        _ => return Err(fail("invalid_source", "EC 下载源设置无效。")),
    };
    let adjusted = now - ChronoDuration::hours(12);
    let mut cycle = adjusted
        .with_hour(adjusted.hour() / 12 * 12)
        .unwrap()
        .with_minute(0)
        .unwrap()
        .with_second(0)
        .unwrap()
        .with_nanosecond(0)
        .unwrap();
    for _ in 0..2 {
        let base = format!(
            "{root}/{}/{:02}z/ifs/0p25/oper/{}0000-0h-oper-fc",
            cycle.format("%Y%m%d"),
            cycle.hour(),
            cycle.format("%Y%m%d%H")
        );
        let index = match transfer.read(
            Method::GET,
            &(base.clone() + ".index"),
            None,
            None,
            MAX_JSON,
            None,
        ) {
            Ok(index) => index,
            Err(error) if error.state == "unavailable" => {
                cycle -= ChronoDuration::hours(12);
                continue;
            }
            Err(error) => return Err(error),
        };
        let first = index
            .split(|byte| *byte == b'\n')
            .next()
            .unwrap_or_default();
        let record: Value =
            serde_json::from_slice(first).map_err(|_| invalid("EC 索引格式无效。"))?;
        let number = |key: &str| {
            record[key]
                .as_u64()
                .or_else(|| record[key].as_str()?.parse().ok())
        };
        let offset = number("_offset").ok_or_else(|| invalid("EC 索引 Range 无效。"))?;
        let length = number("_length")
            .filter(|n| (20..=MAX_SAMPLE as u64).contains(n))
            .ok_or_else(|| invalid("EC 索引样本大小超出上限。"))? as usize;
        offset
            .checked_add(length as u64)
            .ok_or_else(|| invalid("EC 索引 Range 溢出。"))?;
        let payload = transfer.read(
            Method::GET,
            &(base + ".grib2"),
            None,
            None,
            length,
            Some((offset, length)),
        )?;
        validate_grib(&payload)?;
        return Ok(format!(
            "索引及 Range GRIB 下载、解码通过 · {}",
            cycle.format("%m-%d %HZ")
        ));
    }
    Err(fail(
        "unavailable",
        "最近两个 EC 测试时次均不可用，请稍后重试。",
    ))
}

fn cams<F: Fn(Value) -> Result<(), String>>(
    transfer: &mut Transfer<'_, F>,
    now: DateTime<Utc>,
) -> ProbeResult<String> {
    let token = crate::desktop_credentials::key().map_err(|_| {
        fail(
            "not_configured",
            "CAMS 凭据未配置或配置无效，请先在设置中保存 ADS Token。",
        )
    })?;
    let process = format!(
        "{}/retrieve/v1/processes/{DATASET}",
        crate::desktop_credentials::ADS_URL
    );
    let inputs = json!({
        "variable":["total_aerosol_optical_depth_550nm"], "type":["forecast"],
        "date":[(now-ChronoDuration::days(1)).format("%Y-%m-%d").to_string()],
        "time":["00:00"], "leadtime_hour":["0"], "data_format":"grib", "area":[31,120,30,121]
    });
    transfer.json(Method::GET, &process, &token, None)?;
    let execution = process + "/execution";
    let job = transfer.json(
        Method::POST,
        &execution,
        &token,
        Some(&json!({"inputs":inputs})),
    )?;
    let monitor = api_link(&execution, link(&job, "monitor")?)?;
    let mut completed = false;
    let result = (|| {
        let state = loop {
            let state = transfer.json(Method::GET, &monitor, &token, None)?;
            match state["status"].as_str() {
                Some("successful") => {
                    completed = true;
                    transfer.cams_waiting = false;
                    break state;
                }
                Some("accepted" | "running") => {
                    transfer.cams_waiting = true;
                    for _ in 0..2 {
                        let remaining =
                            transfer.tick("CAMS 测试任务排队 / 处理中，尚未验证下载")?;
                        std::thread::sleep(remaining.min(Duration::from_secs(1)));
                    }
                }
                _ => {
                    return Err(fail(
                        "job_failed",
                        "CAMS 测试任务被拒绝或失败，请检查 Token、数据集条款及服务状态。",
                    ))
                }
            }
        };
        let result_url = api_link(
            &monitor,
            link(&state, "results").unwrap_or(&(monitor.clone() + "/results")),
        )?;
        let result = transfer.json(Method::GET, &result_url, &token, None)?;
        let asset = &result["asset"]["value"];
        let href = asset["href"]
            .as_str()
            .ok_or_else(|| invalid("CAMS 样本下载信息缺少地址。"))?;
        let url = safe_https(
            safe_https(&result_url)?
                .join(href)
                .map_err(|_| invalid("CAMS 样本地址无效。"))?
                .as_str(),
        )?;
        let size = asset["file:size"]
            .as_u64()
            .or_else(|| asset["file:size"].as_str()?.parse().ok())
            .filter(|size| (20..=MAX_SAMPLE as u64).contains(size))
            .ok_or_else(|| invalid("CAMS 样本大小超出上限。"))? as usize;
        let payload = transfer.read(Method::GET, url.as_str(), None, None, size, None)?;
        if payload.len() != size {
            return Err(fail("invalid_data", "CAMS 样本下载不完整。"));
        }
        validate_grib(&payload)?;
        Ok("最小 CAMS 任务完成，GRIB 已下载并解码".into())
    })();
    if !completed {
        // Best effort only, without redirects or further polling after cancellation.
        let _ = transfer
            .client
            .delete(&monitor)
            .header("PRIVATE-TOKEN", token)
            .timeout(Duration::from_secs(2))
            .send();
    }
    result
}

pub fn execute(params: Value) -> Result<Value, String> {
    execute_with_progress(params, |_| Ok(()))
}

pub fn execute_with_progress<F>(params: Value, progress: F) -> Result<Value, String>
where
    F: Fn(Value) -> Result<(), String> + Sync,
{
    if !params.is_object() {
        return Err("network_probe params must be an object".into());
    }
    let ec_source = params["ecmwf_source"].as_str().unwrap_or("ecmwf");
    if !["ecmwf", "aws", "google"].contains(&ec_source) {
        return Err("EC 下载源设置无效。".into());
    }
    let now: DateTime<Utc> = if let Some(now) = params["now_utc"].as_str() {
        DateTime::parse_from_rfc3339(now)
            .map_err(|_| "invalid now_utc")?
            .with_timezone(&Utc)
    } else {
        SystemTime::now().into()
    };
    let sources = ["gfs", "ec", "cams"];
    let rows = std::thread::scope(|scope| {
        let handles: Vec<_> = sources.iter().map(|source| {
            let progress = &progress;
            scope.spawn(move || -> Result<Value, String> {
                let mut transfer = Transfer::new(source, ec_source, progress)
                    .map_err(|error| error.message)?;
                let result = match *source {
                    "gfs" => gfs(&mut transfer,now),
                    "ec" => ec(&mut transfer,now,ec_source),
                    _ => cams(&mut transfer,now),
                };
                let row = match result {
                    Ok(detail) => transfer.row("passed",&detail),
                    Err(error) if error.state == "cancelled" => return Err(error.message),
                    Err(error) => {
                        let detail = if transfer.cams_waiting && error.state == "timeout" {
                            "CAMS 任务仍在排队 / 处理中，等待超时；尚未验证实际下载，可稍后重试。"
                        } else { &error.message };
                        transfer.row(error.state,detail)
                    }
                };
                progress(json!({"source":source,"stage":"network_probe","probe":row,"percent":100}))?;
                Ok(row)
            })
        }).collect();
        handles
            .into_iter()
            .map(|handle| {
                handle
                    .join()
                    .map_err(|_| "download probe worker panicked".to_string())?
            })
            .collect::<Result<Vec<Value>, String>>()
    })?;
    Ok(json!({
        "checked_at":now.to_rfc3339(), "all_passed":rows.iter().all(|row|row["state"]=="passed"),
        "datasets":rows
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_insecure_addresses_and_token_redirects() {
        for url in [
            "http://example.com",
            "https://user:secret@example.com/",
            "https://example.com/#fragment",
        ] {
            assert!(safe_https(url).is_err());
        }
        assert!(api_link(
            "https://ads.atmosphere.copernicus.eu/api/retrieve/v1/processes/x",
            "https://evil.example/api/retrieve/v1/jobs/x"
        )
        .is_err());
        assert!(api_link(
            "https://ads.atmosphere.copernicus.eu/api/retrieve/v1/processes/x",
            "/api/retrieve/v1/jobs/x"
        )
        .is_ok());
        assert!(api_link(
            "https://ads.atmosphere.copernicus.eu/api/retrieve/v1/processes/x",
            "/api/retrieve/v1/jobs/x?key=secret"
        )
        .is_err());
    }
    #[test]
    fn rejects_non_grib_without_loading_decoder() {
        assert_eq!(
            validate_grib(b"<html>proxy login</html>")
                .unwrap_err()
                .state,
            "invalid_data"
        );
    }
    #[test]
    fn cancellation_is_not_reported_as_failed_row() {
        let transfer = Transfer::new("gfs", "ecmwf", &|_| Err("cancelled".into())).unwrap();
        assert_eq!(transfer.tick("testing").unwrap_err().state, "cancelled");
    }
    #[test]
    fn invalid_source_is_rejected_before_network() {
        assert!(execute(json!({"ecmwf_source":"not-a-source"})).is_err());
    }
}
