//! Raw desktop nowcast context planning.
//!
//! Satellite bytes and compact-frame validation remain owned by the existing
//! cache provider. This module owns the deterministic request geometry and
//! time contract so the desktop checker does not need Python numerical code.
use crate::{
    astronomy::{self, EventsParams, Phenomenon},
    time_contract,
};
use chrono::{DateTime, Duration, Timelike, Utc};
use reqwest::blocking::Client;
use serde::Deserialize;
use serde_json::{json, Value};
use std::time::Duration as StdDuration;

const MAX_NOWCAST_HOURS: i64 = 4;
const HSD_LOOKAHEAD_MINUTES: i64 = 10;
const LIGHTPATH_RADIUS_KM: f64 = 1000.0;
const INTERPOLATION_PADDING_KM: f64 = 2.0 * 0.25 * 111.2;
const GRID_DEGREES: f64 = 0.25;
const EAST_ASIA: [f64; 4] = [63.0, 145.0, 60.0, 12.0];
const REGIONS: [(&str, [f64; 4]); 10] = [
    ("east-asia", [63.0, 155.0, 60.0, 12.0]),
    ("southeast-asia", [85.0, 155.0, 35.0, -20.0]),
    ("south-asia", [55.0, 110.0, 45.0, -5.0]),
    ("europe", [-25.0, 50.0, 75.0, 25.0]),
    ("north-america", [-155.0, -40.0, 80.0, 5.0]),
    ("south-america", [-90.0, -25.0, 20.0, -60.0]),
    ("oceania", [100.0, 180.0, 5.0, -60.0]),
    ("africa-middle-east", [-25.0, 65.0, 40.0, -40.0]),
    ("north-asia", [20.0, 180.0, 82.0, 35.0]),
    ("china", [73.0, 136.0, 54.0, 18.0]),
];

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    pub(crate) payload: Value,
    #[serde(default)]
    pub(crate) fast: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GfsPlanParams {
    lat: f64,
    lon: f64,
    radius_km: f64,
    cycle_utc: String,
    valid_times: Vec<String>,
}

#[derive(Clone, Copy)]
struct Point {
    lat: f64,
    lon: f64,
    elevation_m: f64,
}

fn number(value: &Value, low: f64, high: f64, label: &str) -> Result<f64, String> {
    let value = value
        .as_f64()
        .filter(|v| v.is_finite() && (low..=high).contains(v))
        .ok_or_else(|| format!("{label} is invalid"))?;
    Ok(value)
}

fn string(value: &Value, label: &str) -> Result<String, String> {
    value
        .as_str()
        .map(str::to_owned)
        .filter(|v| !v.is_empty())
        .ok_or_else(|| format!("{label} is required"))
}

fn iso(time: DateTime<Utc>) -> String {
    if time.timestamp_subsec_micros() == 0 {
        format!("{}+00:00", time.format("%Y-%m-%dT%H:%M:%S"))
    } else {
        format!(
            "{}.{:06}+00:00",
            time.format("%Y-%m-%dT%H:%M:%S"),
            time.timestamp_subsec_micros()
        )
    }
}

fn choose_cycle(reference: DateTime<Utc>) -> DateTime<Utc> {
    let adjusted = reference - Duration::hours(4);
    let hour = adjusted.hour();
    let cycle_hour = [0, 6, 12, 18]
        .into_iter()
        .rev()
        .find(|candidate| *candidate <= hour)
        .unwrap_or(0);
    adjusted
        .date_naive()
        .and_hms_opt(cycle_hour, 0, 0)
        .map(|value| DateTime::<Utc>::from_naive_utc_and_offset(value, Utc))
        .unwrap()
}

fn published_cycle(reference: DateTime<Utc>) -> DateTime<Utc> {
    let heuristic = choose_cycle(reference);
    let client = match Client::builder().timeout(StdDuration::from_secs(6)).build() {
        Ok(client) => client,
        Err(_) => return heuristic,
    };
    let mut candidate = heuristic;
    for _ in 0..4 {
        let url = format!(
            "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/gfs.{}/{:02}/atmos/gfs.t{:02}z.pgrb2.0p25.f006",
            candidate.format("%Y%m%d"),
            candidate.hour(),
            candidate.hour()
        );
        if client
            .head(&url)
            .send()
            .is_ok_and(|response| response.status().is_success())
        {
            return candidate;
        }
        candidate -= Duration::hours(6);
    }
    heuristic
}

fn normalize_lon(value: f64) -> f64 {
    let value = (value + 180.0).rem_euclid(360.0) - 180.0;
    if value == -180.0 {
        180.0
    } else {
        value
    }
}

fn bounding(point: Point, radius_km: f64) -> [f64; 4] {
    let lat_pad = radius_km / 111.0;
    let lon_pad = radius_km / (111.0 * point.lat.to_radians().cos()).max(25.0);
    [
        normalize_lon(point.lon - lon_pad),
        normalize_lon(point.lon + lon_pad),
        (point.lat + lat_pad).min(90.0),
        (point.lat - lat_pad).max(-90.0),
    ]
}

fn canonical_bbox(bbox: [f64; 4]) -> [f64; 4] {
    [
        (-180.0_f64).max((bbox[0] / GRID_DEGREES).floor() * GRID_DEGREES),
        180.0_f64.min((bbox[1] / GRID_DEGREES).ceil() * GRID_DEGREES),
        90.0_f64.min((bbox[2] / GRID_DEGREES).ceil() * GRID_DEGREES),
        (-90.0_f64).max((bbox[3] / GRID_DEGREES).floor() * GRID_DEGREES),
    ]
}

fn contains(bbox: [f64; 4], point: Point) -> bool {
    point.lon >= bbox[0] && point.lon <= bbox[1] && point.lat <= bbox[2] && point.lat >= bbox[3]
}

fn region(point: Point) -> Option<&'static str> {
    REGIONS
        .iter()
        .filter(|(_, bbox)| contains(*bbox, point))
        .min_by(|(_, left), (_, right)| {
            ((left[1] - left[0]) * (left[2] - left[3]))
                .total_cmp(&((right[1] - right[0]) * (right[2] - right[3])))
        })
        .map(|(name, _)| *name)
}

fn cams_area(bbox: [f64; 4]) -> [f64; 4] {
    [
        (bbox[2] + 0.6).min(90.0),
        bbox[0] - 0.6,
        (bbox[3] - 0.6).max(-90.0),
        bbox[1] + 0.6,
    ]
    .map(|value| (value * 1000.0).round() / 1000.0)
}

fn parse_cycle(payload: &Value) -> Result<Option<DateTime<Utc>>, String> {
    for key in ["gfs_cycle_override", "_gfs_cycle_override"] {
        if let Some(value) = payload.get(key).filter(|v| !v.is_null()) {
            return time_contract::parse_utc(
                value
                    .as_str()
                    .ok_or("GFS cycle override must be UTC text")?,
            )
            .map(Some);
        }
    }
    Ok(None)
}

pub fn gfs_plan(params: GfsPlanParams) -> Result<Value, String> {
    let point = Point {
        lat: number(&json!(params.lat), -90.0, 90.0, "latitude")?,
        lon: number(&json!(params.lon), -180.0, 180.0, "longitude")?,
        elevation_m: 0.0,
    };
    let radius = number(&json!(params.radius_km), 100.0, 1000.0, "radius_km")?;
    let cycle = time_contract::parse_utc(&params.cycle_utc)?;
    let source_radius = radius.max(LIGHTPATH_RADIUS_KM) + INTERPOLATION_PADDING_KM;
    let bbox = canonical_bbox(bounding(point, source_radius));
    if bbox[0] > bbox[1] {
        return Err("nowcast GFS bbox crosses the dateline".into());
    }

    let mut hours = std::collections::BTreeSet::new();
    let mut valid_times = Vec::with_capacity(params.valid_times.len());
    for value in params.valid_times {
        let valid_time = time_contract::parse_utc(&value)?;
        let elapsed_hours = (valid_time - cycle).num_milliseconds() as f64 / 3_600_000.0;
        if elapsed_hours < -1e-6 {
            continue;
        }
        let left = elapsed_hours.floor().max(0.0) as i64;
        hours.insert(left);
        if (elapsed_hours - left as f64).abs() >= 1e-6 {
            hours.insert(left + 1);
        }
        valid_times.push(iso(valid_time));
    }
    Ok(json!({
        "contract": "rust-desktop-nowcast-gfs-plan-v1",
        "runtime_python_used": false,
        "cycle": iso(cycle),
        "bbox": bbox,
        "hours": hours.into_iter().collect::<Vec<_>>(),
        "valid_times": valid_times,
    }))
}

pub fn execute(params: Params) -> Result<Value, String> {
    let payload = params.payload;
    if !payload.is_object() {
        return Err("nowcast payload must be an object".into());
    }
    let point = Point {
        lat: number(&payload["lat"], -90.0, 90.0, "latitude")?,
        lon: number(&payload["lon"], -180.0, 180.0, "longitude")?,
        elevation_m: number(&payload["elevation_m"], -500.0, 9000.0, "elevation_m")?,
    };
    let timezone_name = string(&payload["timezone_name"], "timezone_name")?;
    timezone_name
        .parse::<chrono_tz::Tz>()
        .map_err(|_| "timezone_name must be a valid IANA timezone")?;
    let date = string(&payload["target_date"], "target_date")?;
    let phenomenon = match string(&payload["phenomenon"], "phenomenon")?.as_str() {
        "dawn" => Phenomenon::Dawn,
        "dusk" => Phenomenon::Dusk,
        _ => return Err("phenomenon must be dawn or dusk".into()),
    };
    let radius = number(
        payload.get("radius_km").unwrap_or(&json!(900.0)),
        100.0,
        1000.0,
        "radius_km",
    )?;
    let solar = astronomy::events(EventsParams {
        point: astronomy::Point {
            lat: point.lat,
            lon: point.lon,
            elevation_m: point.elevation_m,
        },
        date: date.clone(),
        timezone_name: timezone_name.clone(),
        phenomenon,
    })?;
    let anchor = time_contract::parse_utc(
        solar["anchor_time_utc"]
            .as_str()
            .ok_or("missing solar event anchor")?,
    )?;
    let reference = payload
        .get("reference_time_utc")
        .filter(|v| !v.is_null() && v.as_str().is_some_and(|v| !v.is_empty()))
        .map(|value| {
            time_contract::parse_utc(value.as_str().ok_or("reference time must be UTC text")?)
        })
        .transpose()?
        .unwrap_or_else(|| DateTime::<Utc>::from(std::time::SystemTime::now()));
    if reference <= anchor && anchor - reference > Duration::hours(MAX_NOWCAST_HOURS) {
        return Err("nowcast targets more than four hours in the future".into());
    }
    let lookup = if reference <= anchor {
        reference
    } else {
        anchor + Duration::minutes(HSD_LOOKAHEAD_MINUTES)
    };
    let cycle = if let Some(cycle) = parse_cycle(&payload)? {
        cycle
    } else {
        let cycle_reference = anchor.min(reference);
        if params.fast {
            choose_cycle(cycle_reference)
        } else {
            published_cycle(cycle_reference)
        }
    };
    let source_radius = radius.max(LIGHTPATH_RADIUS_KM) + INTERPOLATION_PADDING_KM;
    let bbox = canonical_bbox(bounding(point, source_radius));
    if bbox[0] > bbox[1] {
        return Err("nowcast GFS bbox crosses the dateline".into());
    }
    let sample_times: Vec<_> = (-9..=9)
        .map(|step| anchor + Duration::minutes(i64::from(step * 5)))
        .collect();
    let times: Vec<_> = sample_times.iter().copied().map(iso).collect();
    let mut cams_valid_times = std::collections::BTreeSet::new();
    for target in &sample_times {
        let left_hour = target.hour() - target.hour() % 3;
        let left = target
            .date_naive()
            .and_hms_opt(left_hour, 0, 0)
            .map(|value| DateTime::<Utc>::from_naive_utc_and_offset(value, Utc))
            .ok_or("invalid CAMS bracket time")?;
        cams_valid_times.insert(iso(left));
        if *target != left {
            cams_valid_times.insert(iso(left + Duration::hours(3)));
        }
    }
    let area = cams_area(
        region(point)
            .and_then(|name| {
                REGIONS
                    .iter()
                    .find(|(code, _)| *code == name)
                    .map(|(_, bbox)| *bbox)
            })
            .unwrap_or(EAST_ASIA),
    );
    let request = json!({
        "target_date": date,
        "timezone_name": timezone_name,
        "lat": point.lat,
        "lon": point.lon,
        "elevation_m": point.elevation_m,
        "phenomenon": match phenomenon { Phenomenon::Dawn => "dawn", Phenomenon::Dusk => "dusk" },
        "forecast_mode": "nowcast",
        "model_source": "satellite",
        "satellite_source": "auto",
        "radius_km": radius,
        "reference_time_utc": iso(reference),
        "_gfs_cycle_override": iso(cycle),
    });
    Ok(json!({
        "contract": "rust-desktop-nowcast-context-v1",
        "runtime_python_used": false,
        "request": request,
        "anchor": iso(anchor),
        "reference": iso(reference),
        "lookup": iso(lookup),
        "cycle": iso(cycle),
        "bbox": bbox,
        "times": times,
        "region": region(point),
        "area": area,
        "cams_valid_times": cams_valid_times.into_iter().collect::<Vec<_>>(),
    }))
}
