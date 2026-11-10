// Modified Rust event-time arithmetic adapted from Astral 3.2 (Apache-2.0).
// Copyright 2009-2021, Simon Kennedy, sffjunkie+code@gmail.com
// Attribution and license: third_party/astral/NOTICE and LICENSE.
use crate::time_contract::{checked, iso, parse_date, parse_zoned};
use chrono::{DateTime, Datelike, Duration, NaiveDate, Offset, TimeZone, Timelike, Utc};
use chrono_tz::Tz;
use firesky_native::astronomy::{julian_day, transit_minutes, SolarTerms};
use firesky_native::solar_time;
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::HashMap;

#[derive(Clone, Copy, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Point {
    pub lat: f64,
    pub lon: f64,
    #[serde(default)]
    pub elevation_m: f64,
}

impl Point {
    fn validate(self) -> Result<Self, String> {
        if !self.lat.is_finite()
            || !self.lon.is_finite()
            || !self.elevation_m.is_finite()
            || !(-90.0..=90.0).contains(&self.lat)
            || !(-180.0..=180.0).contains(&self.lon)
        {
            return Err("solar point requires finite valid coordinates and elevation".into());
        }
        Ok(self)
    }
}

impl From<Point> for solar_time::Point {
    fn from(point: Point) -> Self {
        Self {
            lat: point.lat,
            lon: point.lon,
            elevation_m: point.elevation_m,
        }
    }
}

#[derive(Clone, Copy, Deserialize, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Phenomenon {
    Dawn,
    Dusk,
}

fn zone(name: &str) -> Result<Tz, String> {
    name.parse()
        .map_err(|_| format!("unknown IANA timezone: {name}"))
}

pub(crate) fn local_iso(time: DateTime<Utc>, tz: Tz) -> Result<String, String> {
    let time = time.with_timezone(&tz);
    if !(1..=9999).contains(&time.year()) {
        return Err("local time exceeds calendar range".into());
    }
    // Historical IANA offsets can contain seconds. Preserve them rather than
    // silently rounding to RFC3339's minute-resolution offsets.
    let offset = time.offset().fix().local_minus_utc();
    let magnitude = offset.abs();
    let seconds = magnitude % 60;
    let fraction = if time.timestamp_subsec_micros() == 0 {
        String::new()
    } else {
        format!(".{:06}", time.timestamp_subsec_micros())
    };
    Ok(format!(
        "{}{}{}{:02}:{:02}{}",
        time.format("%Y-%m-%dT%H:%M:%S"),
        fraction,
        if offset < 0 { "-" } else { "+" },
        magnitude / 3600,
        magnitude / 60 % 60,
        if seconds == 0 {
            String::new()
        } else {
            format!(":{seconds:02}")
        }
    ))
}

fn terms(time: DateTime<Utc>) -> SolarTerms {
    SolarTerms::at(julian_day(
        time.year(),
        time.month(),
        time.day(),
        time.num_seconds_from_midnight(),
    ))
}

pub fn position(point: Point, time: DateTime<Utc>) -> [f64; 2] {
    solar_time::position(point.into(), time)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EventsParams {
    pub point: Point,
    pub date: String,
    pub timezone_name: String,
    pub phenomenon: Phenomenon,
}

fn add(time: DateTime<Utc>, delta: Duration) -> Result<DateTime<Utc>, String> {
    checked(
        time.checked_add_signed(delta)
            .ok_or("solar time arithmetic overflow")?,
    )
}

fn transit(
    point: Point,
    date: NaiveDate,
    zenith: f64,
    rising: bool,
) -> Result<DateTime<Utc>, String> {
    let minutes = transit_minutes(
        julian_day(date.year(), date.month(), date.day(), 0),
        point.lat,
        point.lon,
        point.elevation_m,
        zenith,
        rising,
    )?;
    // Astral truncates each component, rather than rounding the total duration.
    let days = (minutes / 1440.0).trunc();
    let seconds = (minutes - days * 1440.0) * 60.0;
    let integer = seconds.trunc();
    let micros = ((seconds - integer) * 1e6).trunc();
    let delta = Duration::days(days as i64)
        + Duration::seconds(integer as i64)
        + Duration::microseconds(micros as i64);
    add(
        Utc.from_utc_datetime(&date.and_hms_opt(0, 0, 0).ok_or("invalid solar date")?),
        delta,
    )
}

fn event(
    point: Point,
    date: NaiveDate,
    tz: Tz,
    zenith: f64,
    rising: bool,
) -> Result<DateTime<Utc>, String> {
    let first = transit(point, date, zenith, rising)?;
    let local_date = first.with_timezone(&tz).date_naive();
    if local_date == date {
        return Ok(first);
    }
    let adjacent = if local_date < date {
        date.succ_opt()
    } else {
        date.pred_opt()
    }
    .ok_or("solar date arithmetic overflow")?;
    let second = transit(point, adjacent, zenith, rising)?;
    if second.with_timezone(&tz).date_naive() != date {
        return Err("unable to find solar event on the specified local date".into());
    }
    Ok(second)
}

pub fn events(params: EventsParams) -> Result<Value, String> {
    let point = params.point.validate()?;
    let date = parse_date(&params.date)?;
    let tz = zone(&params.timezone_name)?;
    // The existing point API calls Astral sun(), not sunrise()/sunset() alone.
    // All four crossings must exist even when the caller only needs one anchor.
    let dawn = event(point, date, tz, 96.0, true)?;
    let sunrise = event(point, date, tz, 90.0 + 32.0 / (60.0 * 2.0), true)?;
    let sunset = event(point, date, tz, 90.0 + 32.0 / (60.0 * 2.0), false)?;
    let dusk = event(point, date, tz, 96.0, false)?;
    let minutes = SolarTerms::at(julian_day(date.year(), date.month(), date.day(), 0))
        .noon_minutes(point.lon);
    let hours = minutes / 60.0;
    let hour = hours.trunc();
    let minute = ((hours - hour) * 60.0).trunc();
    let second = ((((hours - hour) * 60.0) - minute) * 60.0).trunc();
    let noon = add(
        Utc.from_utc_datetime(&date.and_hms_opt(0, 0, 0).ok_or("invalid solar date")?),
        Duration::seconds((hour * 3600.0 + minute * 60.0 + second) as i64),
    )?;
    let anchor = match params.phenomenon {
        Phenomenon::Dawn => sunrise,
        Phenomenon::Dusk => sunset,
    };
    Ok(
        json!({"backend": "rust-astronomy-v1", "anchor_time_utc": iso(anchor),
        "anchor_time_local": local_iso(anchor,tz)?,
        "events": {
            "dawn": local_iso(dawn,tz)?, "sunrise": local_iso(sunrise,tz)?,
            "noon": local_iso(noon,tz)?, "sunset": local_iso(sunset,tz)?, "dusk": local_iso(dusk,tz)?,
        }}),
    )
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SnapshotsParams {
    pub point: Point,
    pub times: Vec<String>,
    pub timezone_name: String,
}

pub fn snapshots(params: SnapshotsParams) -> Result<Value, String> {
    let point = params.point.validate()?;
    let tz = zone(&params.timezone_name)?;
    if params.times.len() > 4096 {
        return Err("solar snapshot time limit exceeded".into());
    }
    let mut cache = HashMap::new();
    let mut output = Vec::with_capacity(params.times.len());
    for value in params.times {
        let time = checked(parse_zoned(&value)?.with_timezone(&Utc))?;
        let terms = *cache.entry(time.timestamp()).or_insert_with(|| terms(time));
        let [elevation, azimuth] = terms.position(
            point.lat,
            point.lon,
            f64::from(time.num_seconds_from_midnight()) / 60.0,
        );
        output.push(
            json!({"valid_time_utc": iso(time), "valid_time_local": local_iso(time,tz)?,
            "elevation_deg": elevation, "azimuth_deg": azimuth}),
        );
    }
    Ok(json!({"backend": "rust-astronomy-v1", "snapshots": output}))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CrossingParams {
    pub point: Point,
    pub anchor: String,
    pub target_elevation_deg: f64,
    pub phenomenon: Phenomenon,
    #[serde(default = "default_search")]
    pub search_minutes: f64,
}
fn default_search() -> f64 {
    90.0
}

pub fn crossing(
    point: Point,
    anchor: DateTime<Utc>,
    target: f64,
    phenomenon: Phenomenon,
    search: f64,
) -> Result<Option<DateTime<Utc>>, String> {
    solar_time::crossing(
        point.into(),
        anchor,
        target,
        matches!(phenomenon, Phenomenon::Dawn),
        search,
    )
}

pub fn crossing_payload(params: CrossingParams) -> Result<Value, String> {
    let point = params.point.validate()?;
    let anchor = checked(parse_zoned(&params.anchor)?.with_timezone(&Utc))?;
    Ok(
        json!({"time_utc": crossing(point,anchor,params.target_elevation_deg,params.phenomenon,params.search_minutes)?.map(iso)}),
    )
}

pub fn shadow_speed(point: Point, time: DateTime<Utc>) -> Result<f64, String> {
    solar_time::shadow_speed(point.into(), time)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ShadowParams {
    pub point: Point,
    pub time: String,
}

pub fn shadow_payload(params: ShadowParams) -> Result<Value, String> {
    Ok(
        json!({"value": shadow_speed(params.point.validate()?, checked(parse_zoned(&params.time)?.with_timezone(&Utc))?)?}),
    )
}

#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum WindowKind {
    Typical {
        edge_distance_km: f64,
        cloud_base_m: f64,
        observer_elevation_m: Option<f64>,
    },
    Target {
        target_distance_km: f64,
        cloud_base_m: f64,
        cloud_top_m: f64,
        regime: String,
    },
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GlowParams {
    pub point: Point,
    pub anchor: String,
    pub timezone_name: String,
    pub phenomenon: Phenomenon,
    pub window: WindowKind,
}

pub fn glow_window(params: GlowParams) -> Result<Value, String> {
    let point = params.point.validate()?;
    let anchor = checked(parse_zoned(&params.anchor)?.with_timezone(&Utc))?;
    let tz = zone(&params.timezone_name)?;
    let (regime, kind, args) = match params.window {
        WindowKind::Typical {
            edge_distance_km: distance,
            cloud_base_m: base,
            observer_elevation_m,
        } => (
            "typical_local_deck".to_string(),
            0,
            [
                distance,
                base,
                0.0,
                observer_elevation_m.unwrap_or(point.elevation_m),
            ],
        ),
        WindowKind::Target {
            target_distance_km: distance,
            cloud_base_m: base,
            cloud_top_m: top,
            regime,
        } => {
            if regime.len() > 128 {
                return Err("window regime exceeds 128 bytes".into());
            }
            (regime, 1, [distance, base, top, 0.0])
        }
    };
    let Some(window) = solar_time::glow_window(
        point.into(),
        anchor,
        matches!(params.phenomenon, Phenomenon::Dawn),
        kind,
        args,
    )?
    else {
        return Ok(json!({"window": null}));
    };
    let (start, midpoint, end) = (window.start, window.midpoint, window.end);
    Ok(json!({"window": {
        "regime": regime, "start_time_utc": iso(start), "start_time_local": local_iso(start,tz)?,
        "midpoint_time_utc": iso(midpoint), "midpoint_time_local": local_iso(midpoint,tz)?,
        "end_time_utc": iso(end), "end_time_local": local_iso(end,tz)?,
        "duration_minutes": window.duration_minutes,
        "shadow_speed_km_per_min": window.shadow_speed, "edge_distance_km": args[0],
        "cloud_base_m": args[1], "geometric_max_distance_km": window.maximum_distance,
    }}))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn polar_and_calendar_failures_are_explicit() {
        assert!(events(EventsParams {
            point: Point {
                lat: 89.0,
                lon: 0.0,
                elevation_m: 0.0
            },
            date: "2026-06-21".into(),
            timezone_name: "UTC".into(),
            phenomenon: Phenomenon::Dawn
        })
        .is_err());
        assert!(zone("not/a/timezone").is_err());
    }
}
