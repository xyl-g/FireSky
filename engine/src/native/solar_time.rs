//! Shared UTC search/window core. The engine owns IANA timezone formatting.
use crate::native::astronomy::{geometric_elevation, julian_day, SolarTerms};
use chrono::{DateTime, Datelike, Duration, Timelike, Utc};

const RADIUS: f64 = 6371.0;

#[derive(Clone, Copy)]
pub struct Point {
    pub lat: f64,
    pub lon: f64,
    pub elevation_m: f64,
}

impl Point {
    pub fn validate(self) -> Result<Self, String> {
        if !self.lat.is_finite()
            || !self.lon.is_finite()
            || !self.elevation_m.is_finite()
            || !(-90.0..=90.0).contains(&self.lat)
            || !(-180.0..=180.0).contains(&self.lon)
        {
            Err("solar point requires finite valid coordinates and elevation".into())
        } else {
            Ok(self)
        }
    }
}

pub fn checked(time: DateTime<Utc>) -> Result<DateTime<Utc>, String> {
    if !(1..=9999).contains(&time.year()) {
        Err("solar time exceeds calendar range".into())
    } else {
        Ok(time)
    }
}

pub fn add(time: DateTime<Utc>, delta: Duration) -> Result<DateTime<Utc>, String> {
    checked(
        time.checked_add_signed(delta)
            .ok_or("solar time arithmetic overflow")?,
    )
}

pub fn position(point: Point, time: DateTime<Utc>) -> [f64; 2] {
    let seconds = time.num_seconds_from_midnight();
    SolarTerms::at(julian_day(time.year(), time.month(), time.day(), seconds)).position(
        point.lat,
        point.lon,
        f64::from(seconds) / 60.0,
    )
}

pub fn half(duration: Duration) -> Result<Duration, String> {
    let micros = duration
        .num_microseconds()
        .ok_or("solar duration overflow")?;
    let quotient = micros.div_euclid(2);
    Ok(Duration::microseconds(
        quotient
            + if micros.rem_euclid(2) != 0 && quotient % 2 != 0 {
                1
            } else {
                0
            },
    ))
}

pub fn crossing(
    point: Point,
    anchor: DateTime<Utc>,
    target: f64,
    rising: bool,
    search: f64,
) -> Result<Option<DateTime<Utc>>, String> {
    point.validate()?;
    checked(anchor)?;
    if !target.is_finite() || !search.is_finite() || !(0.0..=1440.0).contains(&search) {
        return Err("crossing requires finite elevation and search minutes in 0..1440".into());
    }
    let delta = Duration::microseconds((search * 60.0 * 1e6).round_ties_even() as i64);
    let mut start = add(anchor, -delta)?;
    let mut end = add(anchor, delta)?;
    let lower = position(point, start)[0];
    let upper = position(point, end)[0];
    if target < lower.min(upper) || target > lower.max(upper) {
        return Ok(None);
    }
    for _ in 0..42 {
        let middle = add(start, half(end - start)?)?;
        let value = position(point, middle)[0];
        if (rising && value < target) || (!rising && value > target) {
            start = middle;
        } else {
            end = middle;
        }
    }
    Ok(Some(add(start, half(end - start)?)?))
}

pub fn shadow_speed(point: Point, time: DateTime<Utc>) -> Result<f64, String> {
    point.validate()?;
    let before = position(point, add(time, Duration::seconds(-30))?)[0];
    let after = position(point, add(time, Duration::seconds(30))?)[0];
    Ok(RADIUS * (after - before).abs().to_radians())
}

pub struct GlowWindow {
    pub start: DateTime<Utc>,
    pub midpoint: DateTime<Utc>,
    pub end: DateTime<Utc>,
    pub duration_minutes: f64,
    pub shadow_speed: f64,
    pub maximum_distance: f64,
}

/// `kind=0`: typical deck, `kind=1`: target depth. Parameters are distance km,
/// base m, top m (target only), and observer m (typical only).
pub fn glow_window(
    point: Point,
    anchor: DateTime<Utc>,
    rising: bool,
    kind: u32,
    args: [f64; 4],
) -> Result<Option<GlowWindow>, String> {
    point.validate()?;
    checked(anchor)?;
    if args.iter().any(|v| !v.is_finite()) {
        return Err("window geometry must be finite".into());
    }
    let [distance, base, top, observer] = args;
    let (maximum, a, b) = match kind {
        0 => {
            let relative = base - observer;
            let maximum = (2.0 * RADIUS * relative.max(0.0) / 1000.0).sqrt();
            if relative <= 0.0 || distance <= 0.0 || distance > maximum {
                return Ok(None);
            }
            (
                maximum,
                geometric_elevation(-(distance / (2.0 * RADIUS)).atan().to_degrees()),
                geometric_elevation(-(RADIUS / (RADIUS + relative / 1000.0)).acos().to_degrees()),
            )
        }
        1 => {
            let relative_base = (base - point.elevation_m).max(0.0) / 1000.0;
            // Preserve the reference's max/units order even for top below base.
            let relative_top = relative_base.max(top - point.elevation_m) / 1000.0;
            let maximum = (2.0 * RADIUS * point.elevation_m.max(0.0) / 1000.0).sqrt()
                + (2.0 * RADIUS * relative_top).sqrt();
            if relative_top <= 0.0 || distance <= 0.0 || distance > maximum * 1.08 {
                return Ok(None);
            }
            let shadow = |height: f64| {
                geometric_elevation(-(RADIUS / (RADIUS + height.max(0.001))).acos().to_degrees())
            };
            (
                maximum,
                shadow(relative_base.max(0.001)),
                shadow(relative_top),
            )
        }
        _ => return Err("unknown solar window kind".into()),
    };
    if !maximum.is_finite() {
        return Err("window geometry overflow".into());
    }
    let Some(left) = crossing(point, anchor, a, rising, 90.0)? else {
        return Ok(None);
    };
    let Some(right) = crossing(point, anchor, b, rising, 90.0)? else {
        return Ok(None);
    };
    let (start, end) = (left.min(right), left.max(right));
    if end <= start {
        return Ok(None);
    }
    let midpoint = add(start, half(end - start)?)?;
    Ok(Some(GlowWindow {
        start,
        midpoint,
        end,
        maximum_distance: maximum,
        duration_minutes: (end - start)
            .num_microseconds()
            .ok_or("window duration overflow")? as f64
            / 60e6,
        shadow_speed: shadow_speed(point, midpoint)?,
    }))
}

fn from_micros(time: i64) -> Result<DateTime<Utc>, String> {
    checked(DateTime::from_timestamp_micros(time).ok_or("invalid solar epoch")?)
}

/// # Safety
/// `out` must point to one writable i64. Status 0=time, 1=no crossing, -1=error.
#[no_mangle]
pub unsafe extern "C" fn firesky_solar_crossing_v1(
    lat: f64,
    lon: f64,
    anchor: i64,
    target: f64,
    rising: u32,
    search: f64,
    out: *mut i64,
) -> i32 {
    if out.is_null() || rising > 1 {
        return -1;
    }
    let result = from_micros(anchor).and_then(|time| {
        crossing(
            Point {
                lat,
                lon,
                elevation_m: 0.0,
            },
            time,
            target,
            rising == 1,
            search,
        )
    });
    match result {
        Ok(Some(time)) => {
            *out = time.timestamp_micros();
            0
        }
        Ok(None) => 1,
        Err(_) => -1,
    }
}

/// # Safety
/// `out` must point to one writable f64.
#[no_mangle]
pub unsafe extern "C" fn firesky_solar_shadow_speed_v1(
    lat: f64,
    lon: f64,
    time: i64,
    out: *mut f64,
) -> i32 {
    if out.is_null() {
        return -1;
    }
    match from_micros(time).and_then(|time| {
        shadow_speed(
            Point {
                lat,
                lon,
                elevation_m: 0.0,
            },
            time,
        )
    }) {
        Ok(speed) => {
            *out = speed;
            0
        }
        Err(_) => -1,
    }
}

/// # Safety
/// `point` reads three f64 (lat,lon,elevation), `args` reads four f64 as above.
/// `times` writes three i64 UTC epoch microseconds (start,midpoint,end);
/// `metrics` writes three f64 (duration minutes,shadow speed,maximum distance).
/// All pointers must be valid and non-overlapping. 0=window, 1=none, -1=error.
#[no_mangle]
pub unsafe extern "C" fn firesky_solar_glow_window_v1(
    point: *const f64,
    anchor: i64,
    rising: u32,
    kind: u32,
    args: *const f64,
    times: *mut i64,
    metrics: *mut f64,
) -> i32 {
    if point.is_null() || args.is_null() || times.is_null() || metrics.is_null() || rising > 1 {
        return -1;
    }
    let point = std::slice::from_raw_parts(point, 3);
    let point = Point {
        lat: point[0],
        lon: point[1],
        elevation_m: point[2],
    };
    let values = std::slice::from_raw_parts(args, 4);
    let args = [values[0], values[1], values[2], values[3]];
    match from_micros(anchor).and_then(|time| glow_window(point, time, rising == 1, kind, args)) {
        Ok(Some(window)) => {
            let stamps = [
                window.start.timestamp_micros(),
                window.midpoint.timestamp_micros(),
                window.end.timestamp_micros(),
            ];
            let values = [
                window.duration_minutes,
                window.shadow_speed,
                window.maximum_distance,
            ];
            std::ptr::copy_nonoverlapping(stamps.as_ptr(), times, 3);
            std::ptr::copy_nonoverlapping(values.as_ptr(), metrics, 3);
            0
        }
        Ok(None) => 1,
        Err(_) => -1,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn half_uses_bankers_rounding() {
        for (input, expected) in [(1, 0), (3, 2), (5, 2), (-1, 0), (-3, -2)] {
            assert_eq!(
                half(Duration::microseconds(input))
                    .unwrap()
                    .num_microseconds(),
                Some(expected)
            );
        }
    }
    #[test]
    fn ffi_rejects_calendar_bounds_null_and_invalid_coordinates() {
        let mut output = 0;
        assert_eq!(
            unsafe { firesky_solar_crossing_v1(0.0, 0.0, i64::MAX, 0.0, 1, 90.0, &mut output) },
            -1
        );
        assert_eq!(
            unsafe { firesky_solar_crossing_v1(91.0, 0.0, 0, 0.0, 1, 90.0, &mut output) },
            -1
        );
        assert_eq!(
            unsafe { firesky_solar_crossing_v1(0.0, 0.0, 0, 0.0, 2, 90.0, &mut output) },
            -1
        );
        assert_eq!(
            unsafe { firesky_solar_crossing_v1(0.0, 0.0, 0, 0.0, 1, 90.0, std::ptr::null_mut()) },
            -1
        );
        assert_eq!(
            unsafe {
                firesky_solar_glow_window_v1(
                    std::ptr::null(),
                    0,
                    1,
                    0,
                    std::ptr::null(),
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                )
            },
            -1
        );
    }
}
