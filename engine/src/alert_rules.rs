//! Cache-only high-score sunset/sunrise alert rules and webhook delivery
//! (Bark, Server酱, or generic JSON webhook).

use crate::{paths, time_contract};
use chrono::{DateTime, Duration, Utc};
use reqwest::Url;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    fs,
    io::Write,
    path::{Path, PathBuf},
    time::{Duration as StdDuration, SystemTime},
};

const SETTINGS_CONTRACT: &str = "firesky-alert-rules-v1";
const LEDGER_CONTRACT: &str = "firesky-alert-sent-v1";
const MAX_SETTINGS_BYTES: u64 = 64 * 1024;
const MAX_LEDGER_BYTES: u64 = 256 * 1024;
const MAX_SPOTS: usize = 32;
const MAX_RECENT_ALERTS: usize = 20;
const SENT_TTL_DAYS: i64 = 7;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct WatchedSpot {
    pub name: String,
    pub lat: f64,
    pub lon: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub elevation_m: Option<f64>,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub note: String,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct AlertRules {
    pub contract: String,
    pub enabled: bool,
    pub threshold: f64,
    pub spots: Vec<WatchedSpot>,
    pub webhook_url: String,
    pub webhook_type: String,
    pub notify_desktop: bool,
}

impl Default for AlertRules {
    fn default() -> Self {
        Self {
            contract: SETTINGS_CONTRACT.to_owned(),
            enabled: false,
            threshold: 1.5,
            spots: Vec::new(),
            webhook_url: String::new(),
            webhook_type: "auto".to_owned(),
            notify_desktop: true,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct RecentAlert {
    pub id: String,
    pub title: String,
    pub body: String,
    pub spot_name: String,
    pub target_date: String,
    pub phenomenon: String,
    pub model_source: String,
    pub peak_quality_index: f64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub best_time_local: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub peak_hex: Option<String>,
    pub triggered_at_utc: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub webhook_status: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
#[serde(default)]
struct AlertLedger {
    contract: String,
    sent: BTreeMap<String, String>,
    recent_alerts: Vec<RecentAlert>,
}

impl Default for AlertLedger {
    fn default() -> Self {
        Self {
            contract: LEDGER_CONTRACT.to_owned(),
            sent: BTreeMap::new(),
            recent_alerts: Vec::new(),
        }
    }
}

fn settings_path(workspace: &Path) -> PathBuf {
    paths::resolve(
        workspace,
        "",
        ".firesky/settings/alert_rules.json",
        "settings/alert_rules.json",
    )
}

fn ledger_path(workspace: &Path) -> PathBuf {
    paths::resolve(
        workspace,
        "",
        ".firesky/state/alert-sent.json",
        "state/alert-sent.json",
    )
}

fn atomic_write_json(path: &Path, value: &impl Serialize) -> Result<(), String> {
    if fs::symlink_metadata(path).is_ok_and(|m| m.file_type().is_symlink()) {
        return Err("alert settings/state path cannot be a symlink".into());
    }
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .ok_or("alert path has no parent directory")?;
    fs::create_dir_all(parent).map_err(|e| format!("cannot create alert directory: {e}"))?;
    let bytes =
        serde_json::to_vec_pretty(value).map_err(|e| format!("cannot serialize alert json: {e}"))?;
    let mut temp = tempfile::Builder::new()
        .prefix(".firesky-alert-")
        .tempfile_in(parent)
        .map_err(|e| format!("cannot create alert temp file: {e}"))?;
    temp.write_all(&bytes)
        .map_err(|e| format!("cannot write alert temp file: {e}"))?;
    let _ = temp.as_file().sync_all();
    temp.persist(path)
        .map_err(|e| format!("cannot persist alert file: {}", e.error))?;
    Ok(())
}

fn validate_webhook_url(raw: &str) -> Result<String, String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Ok(String::new());
    }
    if trimmed.len() > 1024 || trimmed.chars().any(char::is_whitespace) {
        return Err("invalid webhook URL length or whitespace".into());
    }
    let url = Url::parse(trimmed).map_err(|_| "invalid webhook URL")?;
    let is_loopback = url.host_str().is_some_and(|host| {
        host == "localhost"
            || host
                .parse::<std::net::IpAddr>()
                .is_ok_and(|ip| ip.is_loopback())
    });
    if !(url.scheme() == "https" || (url.scheme() == "http" && is_loopback))
        || url.host_str().is_none()
    {
        return Err("webhook URL must use HTTPS (HTTP allowed only on localhost)".into());
    }
    Ok(trimmed.to_owned())
}

fn read_rules(workspace: &Path) -> AlertRules {
    let path = settings_path(workspace);
    let Ok(meta) = fs::symlink_metadata(&path) else {
        return AlertRules::default();
    };
    if meta.file_type().is_symlink() || !meta.is_file() || meta.len() > MAX_SETTINGS_BYTES {
        return AlertRules::default();
    }
    let Ok(bytes) = fs::read(&path) else {
        return AlertRules::default();
    };
    let Ok(mut parsed) = serde_json::from_slice::<AlertRules>(&bytes) else {
        return AlertRules::default();
    };
    if parsed.contract != SETTINGS_CONTRACT {
        return AlertRules::default();
    }
    if !parsed.threshold.is_finite() {
        parsed.threshold = 1.5;
    }
    parsed
}

fn read_ledger(workspace: &Path) -> AlertLedger {
    let path = ledger_path(workspace);
    let Ok(meta) = fs::symlink_metadata(&path) else {
        return AlertLedger::default();
    };
    if meta.file_type().is_symlink() || !meta.is_file() || meta.len() > MAX_LEDGER_BYTES {
        return AlertLedger::default();
    }
    let Ok(bytes) = fs::read(&path) else {
        return AlertLedger::default();
    };
    let Ok(parsed) = serde_json::from_slice::<AlertLedger>(&bytes) else {
        return AlertLedger::default();
    };
    if parsed.contract != LEDGER_CONTRACT {
        return AlertLedger::default();
    }
    parsed
}

fn validate_and_normalize_rules(params: &Value) -> Result<AlertRules, String> {
    let enabled = params["enabled"].as_bool().unwrap_or(false);
    let threshold = params["threshold"].as_f64().unwrap_or(1.5);
    if !threshold.is_finite() || !(0.3..=2.5).contains(&threshold) {
        return Err("alert threshold must be between 0.3 and 2.5".into());
    }
    let webhook_type = match params["webhook_type"].as_str().unwrap_or("auto") {
        t @ ("auto" | "bark" | "serverchan" | "generic") => t.to_owned(),
        _ => return Err("webhook_type must be auto, bark, serverchan, or generic".into()),
    };
    let webhook_url = validate_webhook_url(params["webhook_url"].as_str().unwrap_or(""))?;
    let notify_desktop = params["notify_desktop"].as_bool().unwrap_or(true);

    let mut spots = Vec::new();
    if let Some(items) = params["spots"].as_array() {
        if items.len() > MAX_SPOTS {
            return Err("too many watched spots (max 32)".into());
        }
        for item in items {
            let name = item["name"].as_str().unwrap_or("").trim();
            let lat = item["lat"].as_f64().unwrap_or(f64::NAN);
            let lon = item["lon"].as_f64().unwrap_or(f64::NAN);
            if name.is_empty()
                || name.len() > 128
                || !lat.is_finite()
                || !(-90.0..=90.0).contains(&lat)
                || !lon.is_finite()
                || !(-180.0..=180.0).contains(&lon)
            {
                return Err("watched spot requires valid name and finite lat/lon".into());
            }
            let elevation_m = item["elevation_m"]
                .as_f64()
                .filter(|v| v.is_finite() && (-500.0..=9000.0).contains(v));
            let note = item["note"].as_str().unwrap_or("").trim().chars().take(128).collect();
            spots.push(WatchedSpot {
                name: name.to_owned(),
                lat,
                lon,
                elevation_m,
                note,
            });
        }
    }

    Ok(AlertRules {
        contract: SETTINGS_CONTRACT.to_owned(),
        enabled,
        threshold: (threshold * 100.0).round() / 100.0,
        spots,
        webhook_url,
        webhook_type,
        notify_desktop,
    })
}

pub fn load_status(workspace: &Path) -> Result<Value, String> {
    let rules = read_rules(workspace);
    let ledger = read_ledger(workspace);
    Ok(json!({
        "contract": SETTINGS_CONTRACT,
        "enabled": rules.enabled,
        "threshold": rules.threshold,
        "spots": rules.spots,
        "webhook_url": rules.webhook_url,
        "webhook_configured": !rules.webhook_url.is_empty(),
        "webhook_type": rules.webhook_type,
        "notify_desktop": rules.notify_desktop,
        "recent_alerts": ledger.recent_alerts,
    }))
}

pub fn save(workspace: &Path, params: Value) -> Result<Value, String> {
    let rules = validate_and_normalize_rules(&params)?;
    atomic_write_json(&settings_path(workspace), &rules)?;
    load_status(workspace)
}

fn resolve_webhook_kind<'a>(webhook_type: &'a str, url: &str) -> &'a str {
    if webhook_type != "auto" {
        return webhook_type;
    }
    let lower = url.to_ascii_lowercase();
    if lower.contains("day.app") {
        "bark"
    } else if lower.contains("sctapi.ftqq.com") || lower.ends_with(".send") {
        "serverchan"
    } else {
        "generic"
    }
}

fn post_webhook(
    webhook_url: &str,
    webhook_type: &str,
    title: &str,
    body: &str,
    meta: &Value,
) -> Result<(), String> {
    let validated = validate_webhook_url(webhook_url)?;
    if validated.is_empty() {
        return Err("webhook URL is empty".into());
    }
    let kind = resolve_webhook_kind(webhook_type, &validated);
    let payload = match kind {
        "bark" => json!({
            "title": title,
            "body": body,
            "group": "FireSky",
        }),
        "serverchan" => json!({
            "title": title,
            "desp": body,
        }),
        _ => json!({
            "event": "firesky_alert",
            "title": title,
            "body": body,
            "meta": meta,
        }),
    };
    let client = crate::http_client::builder()
        .timeout(StdDuration::from_secs(12))
        .build()
        .map_err(|_| "cannot build alert HTTP client")?;
    let body_bytes = serde_json::to_vec(&payload).map_err(|_| "cannot encode alert payload")?;
    let resp = client
        .post(&validated)
        .header("Content-Type", "application/json")
        .body(body_bytes)
        .send()
        .map_err(|_| "alert webhook request failed (URL redacted)".to_owned())?;
    if !resp.status().is_success() {
        return Err(format!("alert webhook HTTP status {}", resp.status().as_u16()));
    }
    Ok(())
}

pub fn send_test_webhook(workspace: &Path, params: Value) -> Result<Value, String> {
    let saved = read_rules(workspace);
    let url_raw = params["webhook_url"]
        .as_str()
        .map(str::to_owned)
        .unwrap_or(saved.webhook_url);
    let wtype = params["webhook_type"]
        .as_str()
        .unwrap_or(&saved.webhook_type);
    let url = validate_webhook_url(&url_raw)?;
    if url.is_empty() {
        return Err("请先填写 Bark / Server酱 / Webhook URL。".into());
    }
    let title = "FireSky 火烧云测试预警";
    let body = "这是一条来自 FireSky 的测试推送：当关注地点火烧云指数达到设定阈值时将自动提醒。";
    post_webhook(
        &url,
        wtype,
        title,
        body,
        &json!({"test": true, "threshold": saved.threshold}),
    )?;
    Ok(json!({
        "ok": true,
        "webhook_type": resolve_webhook_kind(wtype, &url),
    }))
}

fn spot_dedup_key(date: &str, phenomenon: &str, spot: &WatchedSpot) -> String {
    format!(
        "{date}|{phenomenon}|{:.3}|{:.3}",
        spot.lat, spot.lon
    )
}

fn best_model_summary(models: &serde_json::Map<String, Value>) -> Option<(&str, &Value, f64)> {
    let mut best: Option<(&str, &Value, f64)> = None;
    for (name, report) in models {
        let peak = report["peak_quality_index"]
            .as_f64()
            .or_else(|| report["quality_index"].as_f64())
            .filter(|v| v.is_finite())?;
        if best.is_none_or(|(_, _, cur)| peak > cur) {
            best = Some((name.as_str(), report, peak));
        }
    }
    best
}

fn short_local_time(iso: Option<&str>) -> String {
    iso.and_then(|s| s.split('T').nth(1))
        .and_then(|t| t.get(..5))
        .unwrap_or("—")
        .to_owned()
}

pub fn evaluate_after_sync(workspace: &Path) -> Result<Value, String> {
    let rules = read_rules(workspace);
    if !rules.enabled || rules.spots.is_empty() {
        return Ok(json!({"evaluated": 0, "triggered": 0, "skipped": "disabled_or_no_spots"}));
    }
    let now_utc: DateTime<Utc> = SystemTime::now().into();
    let cutoff = now_utc - Duration::days(SENT_TTL_DAYS);
    let mut ledger = read_ledger(workspace);
    ledger.sent.retain(|_, ts| {
        time_contract::parse_utc(ts).is_ok_and(|parsed| parsed >= cutoff)
    });

    let runtime = crate::desktop_runtime::Runtime::new(workspace)?;

    let mut evaluated = 0usize;
    let mut triggered = 0usize;
    for spot in &rules.spots {
        let local_approx = now_utc + Duration::minutes((spot.lon * 4.0).round() as i64);
        let today = local_approx.date_naive();
        let dates = [
            today.format("%Y-%m-%d").to_string(),
            (today + Duration::days(1)).format("%Y-%m-%d").to_string(),
        ];
        for date in &dates {
            for phenomenon in ["sunset", "dawn"] {
                let key = spot_dedup_key(date, phenomenon, spot);
                if ledger.sent.contains_key(&key) {
                    continue;
                }
                evaluated += 1;
                let payload = json!({
                    "name": spot.name,
                    "lat": spot.lat,
                    "lon": spot.lon,
                    "elevation_m": spot.elevation_m.unwrap_or(0.0),
                    "target_date": date,
                    "phenomenon": phenomenon,
                    "forecast_mode": "model",
                    "model_source": "all",
                    "radius_km": 2000.0,
                });
                let Ok(forecast) = runtime.compute_cached_point_for_alert(payload) else {
                    continue;
                };
                let Some(models) = forecast["model_results"].as_object() else {
                    continue;
                };
                let Some((model_name, report, peak)) = best_model_summary(models) else {
                    continue;
                };
                if peak < rules.threshold {
                    continue;
                }

                triggered += 1;
                let phen_zh = if phenomenon == "dawn" { "朝霞" } else { "晚霞" };
                let level = report["peak_quality_level"]
                    .as_str()
                    .or_else(|| report["quality_level"].as_str())
                    .unwrap_or("高概率火烧云");
                let best_iso = report["best_time_local"].as_str();
                let best_hhmm = short_local_time(best_iso);
                let peak_hex = report["spectral_color"]["peak_hex"]
                    .as_str()
                    .map(str::to_owned);
                let color_zh = report["spectral_color"]["peak_label_zh"]
                    .as_str()
                    .unwrap_or("");
                let color_note = if color_zh.is_empty() {
                    String::new()
                } else {
                    format!(" · {color_zh}")
                };

                let title = format!("🔥 {} {} 预警（{:.2} 分）", spot.name, phen_zh, peak);
                let body = format!(
                    "{date} {phen_zh}（{}）预计达到「{level}」{color_note}，峰值时刻 {best_hhmm}。",
                    model_name.to_uppercase()
                );

                let webhook_status = if !rules.webhook_url.is_empty() {
                    match post_webhook(
                        &rules.webhook_url,
                        &rules.webhook_type,
                        &title,
                        &body,
                        &json!({
                            "spot_name": spot.name,
                            "target_date": date,
                            "phenomenon": phenomenon,
                            "model_source": model_name,
                            "peak_quality_index": peak,
                            "best_time_local": best_iso,
                            "peak_hex": peak_hex,
                        }),
                    ) {
                        Ok(()) => Some("sent".to_owned()),
                        Err(err) => Some(err),
                    }
                } else {
                    None
                };

                let now_iso = time_contract::iso(now_utc);
                ledger.sent.insert(key.clone(), now_iso.clone());
                ledger.recent_alerts.insert(
                    0,
                    RecentAlert {
                        id: format!("{key}|{}", now_utc.timestamp()),
                        title,
                        body,
                        spot_name: spot.name.clone(),
                        target_date: date.clone(),
                        phenomenon: phenomenon.to_owned(),
                        model_source: model_name.to_owned(),
                        peak_quality_index: (peak * 100.0).round() / 100.0,
                        best_time_local: best_iso.map(str::to_owned),
                        peak_hex,
                        triggered_at_utc: now_iso,
                        webhook_status,
                    },
                );
                ledger.recent_alerts.truncate(MAX_RECENT_ALERTS);
            }
        }
    }

    if triggered > 0 || !ledger_path(workspace).exists() {
        atomic_write_json(&ledger_path(workspace), &ledger)?;
    }

    Ok(json!({
        "evaluated": evaluated,
        "triggered": triggered,
        "recent_alerts": ledger.recent_alerts,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    use std::net::TcpListener;
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    };

    #[test]
    fn save_and_load_alert_rules_round_trip() {
        let dir = tempfile::tempdir().unwrap();
        let saved = save(
            dir.path(),
            json!({
                "enabled": true,
                "threshold": 1.65,
                "spots": [
                    { "name": "外滩", "lat": 31.23, "lon": 121.47, "note": "浦西视角" }
                ],
                "webhook_url": "https://api.day.app/demo-key",
                "webhook_type": "auto",
                "notify_desktop": true
            }),
        )
        .unwrap();
        assert_eq!(saved["enabled"], true);
        assert_eq!(saved["threshold"], 1.65);
        assert_eq!(saved["webhook_configured"], true);
        assert_eq!(saved["spots"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn bark_webhook_test_posts_expected_json_payload() {
        let dir = tempfile::tempdir().unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let addr = listener.local_addr().unwrap();
        let stop = Arc::new(AtomicBool::new(false));
        let stop_srv = stop.clone();
        let captured = Arc::new(Mutex::new(Vec::<String>::new()));
        let cap_srv = captured.clone();

        let handle = std::thread::spawn(move || {
            while !stop_srv.load(Ordering::Acquire) {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        let _ = stream.set_nonblocking(false);
                        let _ = stream.set_read_timeout(Some(StdDuration::from_secs(2)));
                        let mut raw = Vec::new();
                        let mut buf = [0u8; 4096];
                        let mut expected_total: Option<usize> = None;
                        while let Ok(n) = stream.read(&mut buf) {
                            if n == 0 {
                                break;
                            }
                            raw.extend_from_slice(&buf[..n]);
                            if expected_total.is_none() {
                                if let Some(hdr_end) =
                                    raw.windows(4).position(|w| w == b"\r\n\r\n")
                                {
                                    let hdr = String::from_utf8_lossy(&raw[..hdr_end]);
                                    let cl = hdr
                                        .lines()
                                        .find_map(|line| {
                                            let (k, v) = line.split_once(':')?;
                                            if k.eq_ignore_ascii_case("content-length") {
                                                v.trim().parse::<usize>().ok()
                                            } else {
                                                None
                                            }
                                        })
                                        .unwrap_or(0);
                                    expected_total = Some(hdr_end + 4 + cl);
                                }
                            }
                            if let Some(total) = expected_total {
                                if raw.len() >= total {
                                    break;
                                }
                            }
                        }
                        cap_srv
                            .lock()
                            .unwrap()
                            .push(String::from_utf8_lossy(&raw).into_owned());
                        let _ = stream.write_all(
                            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok",
                        );
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        std::thread::sleep(StdDuration::from_millis(10));
                    }
                    Err(_) => break,
                }
            }
        });

        let url = format!("http://127.0.0.1:{}/push", addr.port());
        let res = send_test_webhook(
            dir.path(),
            json!({
                "webhook_url": url,
                "webhook_type": "bark"
            }),
        )
        .unwrap();
        assert_eq!(res["ok"], true);
        assert_eq!(res["webhook_type"], "bark");

        stop.store(true, Ordering::Release);
        let _ = handle.join();
        let reqs = captured.lock().unwrap();
        assert_eq!(reqs.len(), 1);
        assert!(reqs[0].contains("FireSky"));
        assert!(reqs[0].contains("\"group\":\"FireSky\""));
    }
}
