//! Native publication-cycle probing for the desktop data workflow.
//!
//! The desktop UI needs a small, provider-backed answer to "what is the
//! newest usable cycle?" This module keeps that probe in the persistent Rust
//! engine so the Python desktop service does not own network availability
//! state. Provider failures deliberately fall back to the same conservative
//! heuristics used by the legacy implementation and mark the result
//! `verified=false`.

use crate::{cams_data, time_contract};
use chrono::{DateTime, Duration, TimeZone, Timelike, Utc};
use reqwest::blocking::Client;
use serde::Deserialize;
use serde_json::{json, Map, Value};
use std::{
    collections::{HashMap, HashSet},
    sync::{Mutex, OnceLock},
    time::{Duration as StdDuration, Instant, SystemTime},
};

const CACHE_TTL: StdDuration = StdDuration::from_secs(300);
const GFS_PROBE_HOUR: u32 = 6;
const MAX_TIMEOUT_SECONDS: f64 = 60.0;
const GFS_BASE: &str = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod";
const ECMWF_BASES: [(&str, &str); 3] = [
    (
        "aws",
        "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
    ),
    ("google", "https://storage.googleapis.com/ecmwf-open-data"),
    ("ecmwf", "https://data.ecmwf.int/forecasts"),
];

static CACHE: OnceLock<Mutex<HashMap<String, (Instant, Value)>>> = OnceLock::new();

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    #[serde(default)]
    pub sources: Vec<String>,
    pub now_utc: Option<String>,
    #[serde(default = "default_timeout")]
    pub timeout_seconds: f64,
}

fn default_timeout() -> f64 {
    2.0
}

fn cache() -> &'static Mutex<HashMap<String, (Instant, Value)>> {
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

fn checked_timeout(seconds: f64) -> Result<StdDuration, String> {
    if !seconds.is_finite() || !(0.001..=MAX_TIMEOUT_SECONDS).contains(&seconds) {
        return Err("publication probe timeout must be between 0.001 and 60 seconds".into());
    }
    Ok(StdDuration::from_secs_f64(seconds))
}

fn parse_now(value: Option<&str>) -> Result<DateTime<Utc>, String> {
    value
        .map(time_contract::parse_utc)
        .transpose()
        .map(|value| value.unwrap_or_else(|| DateTime::<Utc>::from(SystemTime::now())))
}

fn choose_cycle(
    reference: DateTime<Utc>,
    cycle_hours: &[u32],
    lag_hours: i64,
) -> Result<DateTime<Utc>, String> {
    let adjusted = reference
        .checked_sub_signed(Duration::hours(lag_hours))
        .ok_or("publication cycle arithmetic overflow")?;
    let day = adjusted.date_naive();
    let (day, hour) = match cycle_hours
        .iter()
        .rev()
        .copied()
        .find(|hour| *hour <= adjusted.hour())
    {
        Some(hour) => (day, hour),
        None => (
            day.pred_opt().ok_or("publication cycle date underflow")?,
            *cycle_hours
                .last()
                .ok_or("publication cycle hours are empty")?,
        ),
    };
    time_contract::checked(
        Utc.from_utc_datetime(
            &day.and_hms_opt(hour, 0, 0)
                .ok_or("invalid publication cycle hour")?,
        ),
    )
}

fn label(cycle: DateTime<Utc>) -> String {
    cycle.format("%Y-%m-%d %HZ").to_string()
}

fn publication(cycle: DateTime<Utc>, verified: bool) -> Value {
    json!({
        "cycle": time_contract::iso(cycle),
        "verified": verified,
        "label": label(cycle),
    })
}

fn heuristic(source: &str, now: DateTime<Utc>) -> Result<DateTime<Utc>, String> {
    match source {
        "gfs" => choose_cycle(now, &[0, 6, 12, 18], 4),
        "ecmwf" => choose_cycle(now, &[0, 12], 7),
        "cams" => {
            // CAMS publication is normally about ten hours after a 00/12Z
            // run becomes available in CDS.
            let adjusted = now
                .checked_sub_signed(Duration::hours(10))
                .ok_or("CAMS publication cycle arithmetic overflow")?;
            let hour = (adjusted.hour() / 12) * 12;
            let day = adjusted.date_naive();
            time_contract::checked(
                Utc.from_utc_datetime(
                    &day.and_hms_opt(hour, 0, 0)
                        .ok_or("invalid CAMS publication cycle hour")?,
                ),
            )
        }
        _ => Err("unknown publication source".into()),
    }
}

fn http_client(timeout: StdDuration) -> Result<Client, String> {
    Client::builder()
        .timeout(timeout)
        .connect_timeout(timeout)
        .redirect(reqwest::redirect::Policy::limited(5))
        .referer(false)
        .retry(reqwest::retry::never())
        .user_agent("firesky/0.1 burning-sunset-engine")
        .build()
        .map_err(|_| "cannot initialize publication probe HTTP client".into())
}

fn head_ok(client: &Client, url: &str) -> bool {
    client
        .head(url)
        .send()
        .is_ok_and(|response| response.status().is_success())
}

fn gfs_probe_url(cycle: DateTime<Utc>) -> String {
    format!(
        "{}/gfs.{}/{:02}/atmos/gfs.t{:02}z.pgrb2.0p25.f{:03}",
        GFS_BASE,
        cycle.format("%Y%m%d"),
        cycle.hour(),
        cycle.hour(),
        GFS_PROBE_HOUR
    )
}

fn ecmwf_probe_url(base: &str, cycle: DateTime<Utc>) -> String {
    format!(
        "{}/{}/{:02}z/ifs/0p25/oper/{}0000-0h-oper-fc.index",
        base.trim_end_matches('/'),
        cycle.format("%Y%m%d"),
        cycle.hour(),
        cycle.format("%Y%m%d%H")
    )
}

fn query_gfs(now: DateTime<Utc>, timeout: StdDuration) -> Result<Value, String> {
    let fallback = heuristic("gfs", now)?;
    let client = http_client(timeout)?;
    let mut candidate = fallback;
    for _ in 0..4 {
        if head_ok(&client, &gfs_probe_url(candidate)) {
            return Ok(publication(candidate, true));
        }
        candidate = candidate
            .checked_sub_signed(Duration::hours(6))
            .ok_or("GFS publication cycle underflow")?;
    }
    Ok(publication(fallback, false))
}

fn query_ecmwf(now: DateTime<Utc>, timeout: StdDuration) -> Result<Value, String> {
    let fallback = heuristic("ecmwf", now)?;
    let client = http_client(timeout)?;
    let mut candidate = fallback;
    for _ in 0..2 {
        if ECMWF_BASES
            .iter()
            .any(|(_, base)| head_ok(&client, &ecmwf_probe_url(base, candidate)))
        {
            return Ok(publication(candidate, true));
        }
        candidate = candidate
            .checked_sub_signed(Duration::hours(12))
            .ok_or("ECMWF publication cycle underflow")?;
    }
    Ok(publication(fallback, false))
}

fn query_cams(now: DateTime<Utc>, timeout_seconds: f64) -> Result<Value, String> {
    let fallback = heuristic("cams", now)?;
    let config = crate::ads::Config {
        timeout_seconds,
        wait_timeout_seconds: timeout_seconds,
        request_attempts: 1,
        poll_initial_seconds: 0.0,
        poll_max_seconds: 0.0,
        ..Default::default()
    };
    match cams_data::latest_publication_cycle(now, config) {
        Ok(cycle) => Ok(publication(cycle, true)),
        Err(_) => Ok(publication(fallback, false)),
    }
}

fn query(
    source: &str,
    now: DateTime<Utc>,
    timeout: StdDuration,
    timeout_seconds: f64,
) -> Result<Value, String> {
    match source {
        "gfs" => query_gfs(now, timeout),
        "ecmwf" => query_ecmwf(now, timeout),
        "cams" => query_cams(now, timeout_seconds),
        _ => Err("unknown publication source".into()),
    }
}

fn cached(source: &str, now: DateTime<Utc>) -> Option<Value> {
    let key = format!("{source}:{}", now.format("%Y%m%d%H"));
    let mut values = cache().lock().unwrap();
    let (created, value) = values.get(&key)?.clone();
    if created.elapsed() < CACHE_TTL {
        return Some(value);
    }
    values.remove(&key);
    None
}

fn store(source: &str, now: DateTime<Utc>, value: Value) -> Value {
    let key = format!("{source}:{}", now.format("%Y%m%d%H"));
    cache()
        .lock()
        .unwrap()
        .insert(key, (Instant::now(), value.clone()));
    value
}

fn validate_sources(sources: &[String]) -> Result<Vec<String>, String> {
    if sources.len() > 3 {
        return Err("publication source limit exceeded".into());
    }
    let mut seen = HashSet::new();
    let mut result = Vec::new();
    for source in sources {
        if !["gfs", "ecmwf", "cams"].contains(&source.as_str()) {
            return Err("unknown publication source".into());
        }
        if seen.insert(source.as_str()) {
            result.push(source.clone());
        }
    }
    Ok(result)
}

pub fn execute(params: Params) -> Result<Value, String> {
    let timeout = checked_timeout(params.timeout_seconds)?;
    let now = parse_now(params.now_utc.as_deref())?;
    let sources = validate_sources(&params.sources)?;
    let values = std::thread::scope(|scope| {
        let handles = sources
            .into_iter()
            .map(|source| {
                let key = source.clone();
                scope.spawn(move || {
                    let value = if let Some(value) = cached(&key, now) {
                        value
                    } else {
                        let value = query(&key, now, timeout, params.timeout_seconds)
                            .unwrap_or_else(|_| {
                                publication(heuristic(&key, now).unwrap_or(now), false)
                            });
                        store(&key, now, value)
                    };
                    (key, value)
                })
            })
            .collect::<Vec<_>>();
        handles
            .into_iter()
            .map(|handle| {
                handle
                    .join()
                    .map_err(|_| "publication probe worker panicked".to_string())
            })
            .collect::<Result<Vec<_>, _>>()
    })?;
    let publications = values.into_iter().collect::<Map<_, _>>();
    Ok(json!({
        "contract": "rust-desktop-publications-v1",
        "runtime_python_used": false,
        "publications": publications,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn now() -> DateTime<Utc> {
        Utc.with_ymd_and_hms(2026, 11, 10, 15, 30, 0).unwrap()
    }

    #[test]
    fn heuristics_match_legacy_cycle_boundaries() {
        assert_eq!(
            heuristic("gfs", now()).unwrap(),
            Utc.with_ymd_and_hms(2026, 11, 10, 6, 0, 0).unwrap()
        );
        assert_eq!(
            heuristic("ecmwf", now()).unwrap(),
            Utc.with_ymd_and_hms(2026, 11, 10, 0, 0, 0).unwrap()
        );
        assert_eq!(
            heuristic("cams", now()).unwrap(),
            Utc.with_ymd_and_hms(2026, 11, 10, 0, 0, 0).unwrap()
        );
    }

    #[test]
    fn probe_urls_match_provider_contracts() {
        let cycle = Utc.with_ymd_and_hms(2026, 11, 10, 6, 0, 0).unwrap();
        assert_eq!(
            gfs_probe_url(cycle),
            "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/gfs.20261110/06/atmos/gfs.t06z.pgrb2.0p25.f006"
        );
        assert_eq!(
            ecmwf_probe_url(ECMWF_BASES[0].1, cycle),
            "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/20261110/06z/ifs/0p25/oper/20261110060000-0h-oper-fc.index"
        );
    }

    #[test]
    fn invalid_timeout_and_empty_sources_are_rejected_or_empty() {
        let invalid = execute(Params {
            sources: vec![],
            now_utc: Some("2026-11-10T15:30:00Z".into()),
            timeout_seconds: 0.0001,
        });
        assert!(invalid.is_err());
        let result = execute(Params {
            sources: vec![],
            now_utc: Some("2026-11-10T15:30:00Z".into()),
            timeout_seconds: 0.2,
        })
        .unwrap();
        assert_eq!(result["publications"], json!({}));
        assert_eq!(result["runtime_python_used"], false);
    }
}
