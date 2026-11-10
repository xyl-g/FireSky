//! Regional timeline compatibility core, distinct from the full-sun point API.
//! Astral-derived solar formula attribution: third_party/astral/NOTICE.

use crate::astronomy::{apparent_event_zenith, julian_day, regional_transit_minutes, SolarTerms};
use chrono::{
    DateTime, Datelike, Duration, LocalResult, NaiveDate, NaiveDateTime, Offset, TimeZone, Utc,
};
use chrono_tz::{GapInfo, Tz};
use rayon::prelude::*;
use std::ffi::{c_char, CStr};

pub const STEPS: usize = 20;
pub const SAMPLE_FIELDS: usize = 9;
pub const MAX_POINTS: usize = 500_000;
pub const MAX_HOURS: usize = 4096;
const MAX_EPHEMERIS_SECONDS: usize = 172_800;

#[derive(Clone, Copy)]
pub struct PreparationOptions {
    pub share_ephemeris: bool,
    pub parallel: bool,
}

impl Default for PreparationOptions {
    fn default() -> Self {
        Self {
            share_ephemeris: true,
            parallel: true,
        }
    }
}

#[derive(Debug)]
pub enum Error {
    Invalid(String),
    Coverage {
        minimum: f64,
        maximum: f64,
        first: f64,
        last: f64,
    },
}

impl From<String> for Error {
    fn from(value: String) -> Self {
        Self::Invalid(value)
    }
}
impl From<&str> for Error {
    fn from(value: &str) -> Self {
        Self::Invalid(value.into())
    }
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(message) => f.write_str(message),
            Self::Coverage { minimum,maximum,first,last } => write!(f,
                "model time axis does not cover valid solar samples: required {minimum:.6}..{maximum:.6}h, available {first:.6}..{last:.6}h"),
        }
    }
}

#[derive(Clone, Copy)]
pub struct Point {
    pub lat: f64,
    pub lon: f64,
    pub elevation_m: f64,
    pub timezone: Tz,
}

impl Point {
    fn validate(self) -> Result<(), Error> {
        if !self.lat.is_finite()
            || !(-90.0..=90.0).contains(&self.lat)
            || !self.lon.is_finite()
            || !(-180.0..=180.0).contains(&self.lon)
            || !self.elevation_m.is_finite()
            || !(-500.0..=9000.0).contains(&self.elevation_m)
        {
            return Err(
                "regional solar point requires valid coordinates and elevation in -500..9000 m"
                    .into(),
            );
        }
        Ok(())
    }
}

pub struct Row {
    pub observer: [f32; 3],
    pub elevation: [f64; STEPS],
    pub azimuth: [f64; STEPS],
    pub valid: [u8; STEPS],
    pub evaluation: [f64; STEPS],
    pub anchor: f64,
    pub local_date_fallback: bool,
}

pub struct Timeline {
    pub rows: Vec<Row>,
    pub samples: Option<Vec<[f32; STEPS * SAMPLE_FIELDS]>>,
}

#[derive(Clone, Copy)]
pub struct ModelTimes<'a> {
    pub cycle_epoch: f64,
    pub hours: &'a [f64],
}

fn checked_date(date: NaiveDate) -> Result<NaiveDate, Error> {
    if !(1..=9999).contains(&date.year()) {
        Err("regional calendar arithmetic exceeds 1..9999".into())
    } else {
        Ok(date)
    }
}

fn epoch(date: NaiveDate) -> Result<f64, Error> {
    Ok(checked_date(date)?
        .and_hms_opt(0, 0, 0)
        .ok_or("invalid regional date")?
        .and_utc()
        .timestamp() as f64)
}

fn utc(epoch: f64) -> Result<DateTime<Utc>, Error> {
    if !epoch.is_finite() {
        return Err("solar epoch must be finite".into());
    }
    let seconds = epoch.floor();
    if !(-62135596800.0..=253402300799.0).contains(&seconds) {
        return Err("solar epoch exceeds calendar range".into());
    }
    let micros = ((epoch - seconds) * 1e6).round_ties_even() as i64;
    let seconds = seconds as i64 + micros / 1_000_000;
    let time = DateTime::from_timestamp(seconds, (micros % 1_000_000) as u32 * 1000)
        .ok_or("invalid solar epoch")?;
    checked_date(time.date_naive())?;
    Ok(time)
}

// Match ZoneInfo's fold=0 for ambiguous and nonexistent local times. In a gap
// it uses the pre-transition offset, not the first valid post-gap clock.
fn fold_zero_offset(local: NaiveDateTime, zone: Tz) -> Result<i32, Error> {
    let offset = match zone.from_local_datetime(&local) {
        LocalResult::Single(time) => time.offset().fix().local_minus_utc(),
        LocalResult::Ambiguous(first, second) => first.min(second).offset().fix().local_minus_utc(),
        LocalResult::None => GapInfo::new(&local, &zone)
            .and_then(|gap| gap.begin)
            .ok_or("cannot resolve local timezone gap")?
            .1
            .fix()
            .local_minus_utc(),
    };
    Ok(offset)
}

pub fn local_epoch(local: NaiveDateTime, zone: Tz) -> Result<f64, Error> {
    checked_date(local.date())?;
    let offset = fold_zero_offset(local, zone)?;
    Ok(local.and_utc().timestamp() as f64
        + f64::from(local.and_utc().timestamp_subsec_micros()) / 1e6
        - f64::from(offset))
}

fn position_epoch(point: Point, epoch: f64) -> [f64; 2] {
    let whole = epoch.floor();
    SolarTerms::at(2440587.5 + whole / 86400.0).regional_position(
        point.lat,
        point.lon,
        whole.rem_euclid(86400.0) / 60.0,
    )
}

struct Ephemeris {
    first: i64,
    terms: Vec<SolarTerms>,
}

impl Ephemeris {
    fn new(rows: &[Row], parallel: bool) -> Option<Self> {
        let first = rows
            .iter()
            .flat_map(|row| row.evaluation)
            .map(|value| value.floor() as i64)
            .min()?;
        let last = rows
            .iter()
            .flat_map(|row| row.evaluation)
            .map(|value| value.floor() as i64)
            .max()?;
        let count = usize::try_from(last - first + 1).ok()?;
        // A dense table is only useful with substantial reuse. Bound both
        // memory (<=2.64 MiB) and work for global or unusual timezones.
        if count > MAX_EPHEMERIS_SECONDS || count * 2 > rows.len() * STEPS {
            return None;
        }
        let compute =
            |index: usize| SolarTerms::at(2440587.5 + (first + index as i64) as f64 / 86400.0);
        let terms = if parallel {
            (0..count).into_par_iter().map(compute).collect()
        } else {
            (0..count).map(compute).collect()
        };
        Some(Self { first, terms })
    }

    fn position(&self, point: Point, epoch: f64) -> [f64; 2] {
        let whole = epoch.floor();
        self.terms[(whole as i64 - self.first) as usize].regional_position(
            point.lat,
            point.lon,
            whole.rem_euclid(86400.0) / 60.0,
        )
    }
}

fn transit(date: NaiveDate, point: Point, rising: bool) -> Result<f64, Error> {
    checked_date(date)?;
    Ok(regional_transit_minutes(
        julian_day(date.year(), date.month(), date.day(), 0),
        point.lat,
        point.lon,
        f64::from(point.elevation_m as f32),
        rising,
    )?)
}

fn fallback(date: NaiveDate, point: Point, rising: bool, approximate: f64) -> Result<f64, Error> {
    let local = utc(approximate)?.with_timezone(&point.timezone);
    let seed_local = date.and_time(local.time());
    let seed = local_epoch(seed_local, point.timezone)?;
    let target = 90.0 - apparent_event_zenith(f64::from(point.elevation_m as f32));
    let start = local_epoch(
        date.and_hms_opt(0, 0, 0)
            .ok_or("invalid regional midnight")?,
        point.timezone,
    )?;
    let tomorrow = checked_date(date.succ_opt().ok_or("regional date overflow")?)?;
    let end = local_epoch(
        tomorrow
            .and_hms_opt(0, 0, 0)
            .ok_or("invalid regional midnight")?,
        point.timezone,
    )?;
    let mut lower = start.max(seed - 21600.0);
    let mut upper = end.min(seed + 21600.0);
    let low = position_epoch(point, lower)[0] - target;
    let high = position_epoch(point, upper)[0] - target;
    if !(if rising {
        low <= 0.0 && high >= 0.0
    } else {
        high <= 0.0 && low >= 0.0
    }) {
        return Err("unable to find regional event on requested local date".into());
    }
    for _ in 0..48 {
        let middle = 0.5 * (lower + upper);
        let residual = position_epoch(point, middle)[0] - target;
        if (rising && residual < 0.0) || (!rising && residual > 0.0) {
            lower = middle;
        } else {
            upper = middle;
        }
    }
    Ok(0.5 * (lower + upper))
}

fn row(point: Point, date: NaiveDate, rising: bool) -> Result<Row, Error> {
    let initial = transit(date, point, rising)?;
    let midnight = date
        .and_hms_opt(0, 0, 0)
        .ok_or("invalid regional midnight")?;
    let noon = date.and_hms_opt(12, 0, 0).ok_or("invalid regional noon")?;
    let timezone_hours = f64::from(fold_zero_offset(noon, point.timezone)?) / 3600.0;
    let delta = ((initial + timezone_hours * 60.0) / 1440.0).floor() as i64;
    let transit_date = checked_date(
        date.checked_sub_signed(Duration::days(delta))
            .ok_or("regional date overflow")?,
    )?;
    let adjusted = if delta == 0 {
        initial
    } else {
        transit(transit_date, point, rising)?
    };
    let mut anchor = epoch(transit_date)? + adjusted * 60.0;
    let local_date_fallback = utc(anchor)?.with_timezone(&point.timezone).date_naive() != date;
    if local_date_fallback {
        anchor = fallback(date, point, rising, epoch(date)? + initial * 60.0)?;
    }
    let local_midnight = local_epoch(midnight, point.timezone)?;
    let anchor_minutes = (anchor - local_midnight) / 60.0;
    let mut row = Row {
        observer: [point.lat as f32, point.lon as f32, point.elevation_m as f32],
        elevation: [0.0; STEPS],
        azimuth: [0.0; STEPS],
        valid: [0; STEPS],
        evaluation: [0.0; STEPS],
        anchor,
        local_date_fallback,
    };
    for i in 0..STEPS {
        let offset = if rising {
            -75 + i as i32 * 5
        } else {
            -20 + i as i32 * 5
        };
        row.evaluation[i] = local_midnight + (anchor_minutes + f64::from(offset)) * 60.0;
        utc(row.evaluation[i])?;
    }
    Ok(row)
}

fn sample_row(
    row: &Row,
    model: ModelTimes<'_>,
) -> ([f32; STEPS * SAMPLE_FIELDS], Option<[f64; 2]>) {
    let mut output = [0.0; STEPS * SAMPLE_FIELDS];
    let mut missing: Option<[f64; 2]> = None;
    for step in 0..STEPS {
        let offset = (row.evaluation[step] - model.cycle_epoch) / 3600.0;
        let insertion = model.hours.partition_point(|&value| value < offset);
        let exact =
            insertion < model.hours.len() && (model.hours[insertion] - offset).abs() <= 1e-7;
        let between = insertion > 0 && insertion < model.hours.len() && !exact;
        let (left, right, weight) = if exact {
            (insertion, insertion, 0.0)
        } else if between {
            (
                insertion - 1,
                insertion,
                (offset - model.hours[insertion - 1])
                    / (model.hours[insertion] - model.hours[insertion - 1]),
            )
        } else {
            (0, 0, 0.0)
        };
        let valid = row.valid[step] != 0;
        if valid && !(exact || between) {
            missing = Some(match missing {
                None => [offset, offset],
                Some([low, high]) => [low.min(offset), high.max(offset)],
            });
        }
        let active = valid && (exact || between);
        let values = [
            row.elevation[step] as f32,
            row.azimuth[step] as f32,
            left as f32,
            right as f32,
            weight as f32,
            0.12,
            f32::from(row.valid[step]),
            if active {
                ((offset - model.hours[left]) * 3600.0).max(0.0) as f32
            } else {
                0.0
            },
            if active {
                ((model.hours[right] - offset) * 3600.0).max(0.0) as f32
            } else {
                0.0
            },
        ];
        output[step * SAMPLE_FIELDS..(step + 1) * SAMPLE_FIELDS].copy_from_slice(&values);
    }
    (output, missing)
}

pub fn prepare(
    points: &[Point],
    date: NaiveDate,
    rising: bool,
    model: Option<ModelTimes<'_>>,
) -> Result<Timeline, Error> {
    prepare_with_options(points, date, rising, model, PreparationOptions::default())
}

/// Exposes execution choices for reproducible component profiling. All choices
/// retain the same arithmetic and output ordering.
pub fn prepare_with_options(
    points: &[Point],
    date: NaiveDate,
    rising: bool,
    model: Option<ModelTimes<'_>>,
    options: PreparationOptions,
) -> Result<Timeline, Error> {
    if points.len() > MAX_POINTS {
        return Err("regional point limit exceeded".into());
    }
    checked_date(date)?;
    for point in points {
        point.validate()?;
    }
    if let Some(model) = model {
        utc(model.cycle_epoch)?;
        if model.hours.is_empty()
            || model.hours.len() > MAX_HOURS
            || model.hours.iter().any(|value| !value.is_finite())
            || model.hours.windows(2).any(|pair| pair[0] >= pair[1])
        {
            return Err(
                "model hours must be a nonempty finite strictly ascending vector of at most 4096"
                    .into(),
            );
        }
    }
    let parallel = options.parallel && points.len() >= 1024;
    let mut rows: Vec<Row> = if parallel {
        // Collect in input order, then propagate errors in the same order as
        // serial execution, rather than whichever worker finishes first.
        points
            .par_iter()
            .map(|&point| row(point, date, rising))
            .collect::<Vec<_>>()
            .into_iter()
            .collect::<Result<_, _>>()?
    } else {
        points
            .iter()
            .map(|&point| row(point, date, rising))
            .collect::<Result<_, _>>()?
    };
    let ephemeris = options
        .share_ephemeris
        .then(|| Ephemeris::new(&rows, parallel))
        .flatten();
    let fill = |(point, row): (&Point, &mut Row)| {
        for step in 0..STEPS {
            let [elevation, azimuth] = match &ephemeris {
                Some(cache) => cache.position(*point, row.evaluation[step]),
                None => position_epoch(*point, row.evaluation[step]),
            };
            row.elevation[step] = elevation;
            row.azimuth[step] = azimuth;
            row.valid[step] = u8::from((-7.5..=1.5).contains(&elevation));
        }
        row.valid[if rising { 15 } else { 4 }] = 1;
    };
    if parallel {
        points.par_iter().zip(rows.par_iter_mut()).for_each(fill);
    } else {
        points.iter().zip(&mut rows).for_each(fill);
    }
    let samples = if let Some(model) = model {
        let mut samples = vec![[0.0; STEPS * SAMPLE_FIELDS]; rows.len()];
        let fill = |(row, target): (&Row, &mut [f32; STEPS * SAMPLE_FIELDS])| {
            let (values, gap) = sample_row(row, model);
            *target = values;
            gap
        };
        let merge = |a: Option<[f64; 2]>, b: Option<[f64; 2]>| match (a, b) {
            (Some([a, b]), Some([c, d])) => Some([a.min(c), b.max(d)]),
            (None, value) | (value, None) => value,
        };
        let missing = if parallel {
            rows.par_iter()
                .zip(samples.par_iter_mut())
                .map(fill)
                .reduce(|| None, merge)
        } else {
            rows.iter().zip(&mut samples).map(fill).fold(None, merge)
        };
        if let Some([minimum, maximum]) = missing {
            return Err(Error::Coverage {
                minimum,
                maximum,
                first: model.hours[0],
                last: *model.hours.last().unwrap(),
            });
        }
        Some(samples)
    } else {
        None
    };
    Ok(Timeline { rows, samples })
}

#[repr(C)]
pub struct Outputs {
    pub observers: *mut f32,
    pub elevation: *mut f64,
    pub azimuth: *mut f64,
    pub valid: *mut u8,
    pub evaluation: *mut f64,
    pub anchors: *mut f64,
    pub samples: *mut f32,
}

unsafe fn error_message(message: &str, buffer: *mut c_char, capacity: usize) {
    if !buffer.is_null() && capacity > 0 {
        let count = message.len().min(capacity - 1);
        std::ptr::copy_nonoverlapping(message.as_ptr(), buffer.cast::<u8>(), count);
        *buffer.add(count) = 0;
    }
}

/// # Safety
/// Coordinates read `count*3` f64; zones read `count` NUL-terminated UTF-8
/// pointers (each <=128 bytes). Every nonnull output buffer must hold its
/// declared shape: observers (count,3), angles/valid/evaluation (count,20),
/// anchors (count), optional samples (count,20,9). Buffers must not overlap.
/// With `hour_count=0` samples are not produced; otherwise hours read that
/// many f64 and samples must be writable. Error buffer holds `error_capacity`
/// bytes. Returns 0 success, -2 uncovered model time axis, -1 other rejection.
#[no_mangle]
pub unsafe extern "C" fn firesky_regional_timeline_v1(
    coordinates: *const f64,
    zones: *const *const c_char,
    count: usize,
    year: i32,
    month: u32,
    day: u32,
    rising: u32,
    cycle_micros: i64,
    hours: *const f64,
    hour_count: usize,
    outputs: *const Outputs,
    error: *mut c_char,
    error_capacity: usize,
) -> i32 {
    let result = (|| -> Result<(), Error> {
        if count > MAX_POINTS || rising > 1 || hour_count > MAX_HOURS || outputs.is_null() {
            return Err("invalid regional dimensions or phenomenon".into());
        }
        let date = NaiveDate::from_ymd_opt(year, month, day).ok_or("invalid regional date")?;
        checked_date(date)?;
        if count == 0 {
            prepare(
                &[],
                date,
                rising == 1,
                if hour_count == 0 {
                    None
                } else {
                    if hours.is_null() {
                        return Err("null model hours".into());
                    }
                    Some(ModelTimes {
                        cycle_epoch: cycle_micros as f64 / 1e6,
                        hours: std::slice::from_raw_parts(hours, hour_count),
                    })
                },
            )?;
            return Ok(());
        }
        if coordinates.is_null() || zones.is_null() {
            return Err("null regional input".into());
        }
        let output = &*outputs;
        if output.observers.is_null()
            || output.elevation.is_null()
            || output.azimuth.is_null()
            || output.valid.is_null()
            || output.evaluation.is_null()
            || output.anchors.is_null()
            || (hour_count != 0 && (output.samples.is_null() || hours.is_null()))
        {
            return Err("null regional output or hours".into());
        }
        let coordinates = std::slice::from_raw_parts(coordinates, count * 3);
        let zones = std::slice::from_raw_parts(zones, count);
        let mut zone_cache = std::collections::HashMap::new();
        let mut points = Vec::with_capacity(count);
        for i in 0..count {
            if zones[i].is_null() {
                return Err("null regional timezone".into());
            }
            let name = CStr::from_ptr(zones[i])
                .to_str()
                .map_err(|_| "timezone must be UTF-8")?;
            if name.len() > 128 {
                return Err("timezone name exceeds limit".into());
            }
            let timezone = match zone_cache.get(name) {
                Some(zone) => *zone,
                None => {
                    let zone = name
                        .parse::<Tz>()
                        .map_err(|_| format!("unknown IANA timezone: {name}"))?;
                    zone_cache.insert(name, zone);
                    zone
                }
            };
            points.push(Point {
                lat: coordinates[i * 3],
                lon: coordinates[i * 3 + 1],
                elevation_m: coordinates[i * 3 + 2],
                timezone,
            });
        }
        let model = if hour_count == 0 {
            None
        } else {
            Some(ModelTimes {
                cycle_epoch: cycle_micros as f64 / 1e6,
                hours: std::slice::from_raw_parts(hours, hour_count),
            })
        };
        let timeline = prepare(&points, date, rising == 1, model)?;
        for (i, row) in timeline.rows.iter().enumerate() {
            std::ptr::copy_nonoverlapping(row.observer.as_ptr(), output.observers.add(i * 3), 3);
            std::ptr::copy_nonoverlapping(
                row.elevation.as_ptr(),
                output.elevation.add(i * STEPS),
                STEPS,
            );
            std::ptr::copy_nonoverlapping(
                row.azimuth.as_ptr(),
                output.azimuth.add(i * STEPS),
                STEPS,
            );
            std::ptr::copy_nonoverlapping(row.valid.as_ptr(), output.valid.add(i * STEPS), STEPS);
            std::ptr::copy_nonoverlapping(
                row.evaluation.as_ptr(),
                output.evaluation.add(i * STEPS),
                STEPS,
            );
            *output.anchors.add(i) = row.anchor;
            if let Some(samples) = &timeline.samples {
                std::ptr::copy_nonoverlapping(
                    samples[i].as_ptr(),
                    output.samples.add(i * STEPS * SAMPLE_FIELDS),
                    STEPS * SAMPLE_FIELDS,
                );
            }
        }
        Ok(())
    })();
    match result {
        Ok(()) => 0,
        Err(failure) => {
            error_message(&failure.to_string(), error, error_capacity);
            if matches!(failure, Error::Coverage { .. }) {
                -2
            } else {
                -1
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn timezone_gap_and_fold_match_fold_zero() {
        let zone: Tz = "America/New_York".parse().unwrap();
        let gap = NaiveDate::from_ymd_opt(2026, 3, 8)
            .unwrap()
            .and_hms_opt(2, 30, 0)
            .unwrap();
        let fold = NaiveDate::from_ymd_opt(2026, 11, 1)
            .unwrap()
            .and_hms_opt(1, 30, 0)
            .unwrap();
        assert_eq!(fold_zero_offset(gap, zone).unwrap(), -5 * 3600);
        assert_eq!(fold_zero_offset(fold, zone).unwrap(), -4 * 3600);
    }
    #[test]
    fn left_insertion_and_exact_tolerance_are_not_symmetric() {
        let mut row = Row {
            observer: [0.0; 3],
            elevation: [0.0; STEPS],
            azimuth: [0.0; STEPS],
            valid: [1; STEPS],
            evaluation: [3600.0 - 0.0001; STEPS],
            anchor: 3600.0,
            local_date_fallback: false,
        };
        let model = ModelTimes {
            cycle_epoch: 0.0,
            hours: &[1.0],
        };
        assert!(sample_row(&row, model).1.is_none());
        row.evaluation = [3600.0 + 0.0001; STEPS];
        assert!(sample_row(&row, model).1.is_some());
    }
    #[test]
    fn ffi_rejects_bounds_before_dereferencing() {
        let code = unsafe {
            firesky_regional_timeline_v1(
                std::ptr::null(),
                std::ptr::null(),
                MAX_POINTS + 1,
                2026,
                1,
                1,
                1,
                0,
                std::ptr::null(),
                0,
                std::ptr::null(),
                std::ptr::null_mut(),
                0,
            )
        };
        assert_eq!(code, -1);
    }

    #[test]
    fn cached_parallel_and_serial_outputs_are_bitwise_identical() {
        let date = NaiveDate::from_ymd_opt(2026, 11, 9).unwrap();
        let points: Vec<_> = (0..4096)
            .map(|i| Point {
                lat: 18.0 + f64::from(i % 145) * 0.25,
                lon: 73.0 + f64::from(i % 253) * 0.25,
                elevation_m: f64::from(i % 80) * 100.1234567 - 500.0,
                timezone: "Asia/Shanghai".parse().unwrap(),
            })
            .collect();
        let model = Some(ModelTimes {
            cycle_epoch: epoch(date).unwrap(),
            hours: &[-24.0, 0.0, 24.0, 48.0],
        });
        for rising in [false, true] {
            let baseline = prepare_with_options(
                &points,
                date,
                rising,
                model,
                PreparationOptions {
                    share_ephemeris: false,
                    parallel: false,
                },
            )
            .unwrap();
            assert!(Ephemeris::new(&baseline.rows, false).is_some());
            for (share_ephemeris, parallel) in [(true, false), (false, true), (true, true)] {
                let actual = prepare_with_options(
                    &points,
                    date,
                    rising,
                    model,
                    PreparationOptions {
                        share_ephemeris,
                        parallel,
                    },
                )
                .unwrap();
                for (a, b) in actual.rows.iter().zip(&baseline.rows) {
                    assert_eq!(a.observer.map(f32::to_bits), b.observer.map(f32::to_bits));
                    assert_eq!(a.elevation.map(f64::to_bits), b.elevation.map(f64::to_bits));
                    assert_eq!(a.azimuth.map(f64::to_bits), b.azimuth.map(f64::to_bits));
                    assert_eq!(
                        a.evaluation.map(f64::to_bits),
                        b.evaluation.map(f64::to_bits)
                    );
                    assert_eq!(a.anchor.to_bits(), b.anchor.to_bits());
                    assert_eq!(a.valid, b.valid);
                    assert_eq!(a.local_date_fallback, b.local_date_fallback);
                }
                for (a, b) in actual
                    .samples
                    .unwrap()
                    .iter()
                    .zip(baseline.samples.as_ref().unwrap())
                {
                    assert_eq!(a.map(f32::to_bits), b.map(f32::to_bits));
                }
            }
        }
    }

    #[test]
    fn ephemeris_skips_empty_sparse_and_unbounded_ranges() {
        assert!(Ephemeris::new(&[], false).is_none());
        let point = Point {
            lat: 31.23,
            lon: 121.47,
            elevation_m: 0.0,
            timezone: "Asia/Shanghai".parse().unwrap(),
        };
        let date = NaiveDate::from_ymd_opt(2026, 11, 9).unwrap();
        let mut rows: Vec<_> = (0..4096)
            .map(|_| row(point, date, false).unwrap())
            .collect();
        assert!(Ephemeris::new(&rows[..1], false).is_none());
        rows[0].evaluation[0] -= 86400.0 * 3.0;
        assert!(Ephemeris::new(&rows, true).is_none());
    }
}
