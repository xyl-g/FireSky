use chrono::{DateTime, Datelike, Duration, FixedOffset, NaiveDate, TimeZone, Timelike, Utc};
use serde::Deserialize;
use serde_json::{json, Value};

pub fn parse_date(value: &str) -> Result<NaiveDate, String> {
    let b = value.as_bytes();
    if b.len() != 10
        || b[4] != b'-'
        || b[7] != b'-'
        || !b
            .iter()
            .enumerate()
            .all(|(i, c)| i == 4 || i == 7 || c.is_ascii_digit())
    {
        return Err("date must be an ISO calendar date".into());
    }
    NaiveDate::parse_from_str(value, "%Y-%m-%d")
        .ok()
        .filter(|date| (1..=9999).contains(&date.year()))
        .ok_or_else(|| "date must be an ISO calendar date".into())
}

pub fn parse_zoned(value: &str) -> Result<DateTime<FixedOffset>, String> {
    let invalid = || "time must be a timezone-aware ISO timestamp".to_string();
    let b = value.as_bytes();
    if b.len() < 20
        || b.get(10) != Some(&b'T')
        || b.get(13) != Some(&b':')
        || b.get(16) != Some(&b':')
    {
        return Err(invalid());
    }
    parse_date(value.get(..10).ok_or_else(invalid)?)?;
    let mut end = 19;
    if b.get(end) == Some(&b'.') {
        end += 1;
        let start = end;
        while b.get(end).is_some_and(u8::is_ascii_digit) {
            end += 1;
        }
        if !(1..=6).contains(&(end - start)) {
            return Err(invalid());
        }
    }
    let suffix = value.get(end..).ok_or_else(invalid)?;
    if suffix != "Z" {
        let s = suffix.as_bytes();
        if s.len() != 6
            || !matches!(s[0], b'+' | b'-')
            || s[3] != b':'
            || ![1, 2, 4, 5].iter().all(|&i| s[i].is_ascii_digit())
            || suffix[4..].parse::<u32>().map_err(|_| invalid())? > 59
        {
            return Err(invalid());
        }
    }
    if ![11, 12, 14, 15, 17, 18]
        .iter()
        .all(|&i| b[i].is_ascii_digit())
    {
        return Err(invalid());
    }
    // Python 3.15 normalizes an exact ISO end-of-day 24:00:00 to tomorrow.
    // No nonzero fractional second/minute/second is allowed in that form.
    let normalized;
    let value = if &b[11..13] == b"24" {
        if &b[14..16] != b"00"
            || &b[17..19] != b"00"
            || (end > 19 && b[20..end].iter().any(|&c| c != b'0'))
        {
            return Err(invalid());
        }
        let day = parse_date(&value[..10])?
            .succ_opt()
            .filter(|d| d.year() <= 9999)
            .ok_or_else(invalid)?;
        normalized = format!("{day}T00{}", &value[13..]);
        normalized.as_str()
    } else {
        value
    };
    let parsed = DateTime::parse_from_rfc3339(value).map_err(|_| invalid())?;
    if parsed.second() > 59
        || parsed.nanosecond() >= 1_000_000_000
        || parsed.offset().local_minus_utc().abs() > 14 * 3600
    {
        return Err(invalid());
    }
    Ok(parsed)
}

pub fn parse_utc(value: &str) -> Result<DateTime<Utc>, String> {
    let parsed = parse_zoned(value)?;
    if !value.ends_with('Z') && !value.ends_with("+00:00") {
        return Err("time must use UTC offset Z or +00:00".into());
    }
    Ok(parsed.with_timezone(&Utc))
}

pub fn checked(date: DateTime<Utc>) -> Result<DateTime<Utc>, String> {
    if !(1..=9999).contains(&date.year()) {
        Err("time arithmetic exceeds Python calendar range".into())
    } else {
        Ok(date)
    }
}

pub fn iso<T: TimeZone>(date: DateTime<T>) -> String
where
    T::Offset: std::fmt::Display,
{
    date.to_rfc3339_opts(chrono::SecondsFormat::AutoSi, false)
}

#[derive(Deserialize)]
#[serde(tag = "operation", rename_all = "snake_case", deny_unknown_fields)]
pub enum Operation {
    ParseDate {
        value: String,
    },
    ParseZoned {
        value: String,
    },
    ParseUtc {
        value: String,
    },
    ChinaDate {
        time: String,
    },
    ChooseCycle {
        reference: String,
        cycle_hours: Vec<u32>,
        availability_lag_hours: i32,
    },
    NearestHourOffsets {
        init: String,
        times: Vec<String>,
    },
}

pub fn execute(operation: Operation) -> Result<Value, String> {
    match operation {
        Operation::ParseDate { value } => Ok(json!({"value": parse_date(&value)?.to_string()})),
        Operation::ParseZoned { value } => Ok(json!({"value": iso(parse_zoned(&value)?)})),
        Operation::ParseUtc { value } => Ok(json!({"value": iso(parse_utc(&value)?)})),
        Operation::ChinaDate { time } => {
            let local = parse_zoned(&time)?.with_timezone(&chrono_tz::Asia::Shanghai);
            if !(1..=9999).contains(&local.year()) {
                return Err("local date exceeds calendar range".into());
            }
            Ok(json!({"value": local.date_naive().to_string()}))
        }
        Operation::ChooseCycle {
            reference,
            cycle_hours,
            availability_lag_hours,
        } => {
            if cycle_hours.is_empty()
                || cycle_hours.len() > 24
                || cycle_hours.iter().any(|&h| h > 23)
                || cycle_hours.windows(2).any(|w| w[0] >= w[1])
            {
                return Err(
                    "cycle hours must be a nonempty ascending unique subset of 0..23".into(),
                );
            }
            let adjusted = parse_zoned(&reference)?
                .with_timezone(&Utc)
                .checked_sub_signed(Duration::hours(i64::from(availability_lag_hours)))
                .ok_or("cycle arithmetic overflow")?;
            let (day, hour) =
                if let Some(hour) = cycle_hours.iter().rev().find(|&&h| h <= adjusted.hour()) {
                    (adjusted.date_naive(), *hour)
                } else {
                    (
                        adjusted
                            .date_naive()
                            .pred_opt()
                            .ok_or("cycle date underflow")?,
                        *cycle_hours.last().unwrap(),
                    )
                };
            let result =
                Utc.from_utc_datetime(&day.and_hms_opt(hour, 0, 0).ok_or("invalid cycle hour")?);
            Ok(json!({"value": iso(checked(result)?)}))
        }
        Operation::NearestHourOffsets { init, times } => {
            if times.len() > 4096 {
                return Err("hour offset time limit exceeded".into());
            }
            let init = parse_zoned(&init)?.with_timezone(&Utc);
            let mut hours = std::collections::BTreeSet::new();
            for time in times {
                let delta = parse_zoned(&time)?.with_timezone(&Utc) - init;
                let seconds = delta.num_seconds();
                let fraction = (delta - Duration::seconds(seconds))
                    .num_microseconds()
                    .ok_or("offset overflow")?;
                let value =
                    ((seconds as f64 + fraction as f64 / 1e6) / 3600.0).round_ties_even() as i64;
                if value >= 0 {
                    hours.insert(value);
                }
            }
            Ok(json!({"values": hours}))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn strict_calendar_and_offsets() {
        for value in ["0000-01-01", "2026-02-29", "2026-1-01", "２０２６-01-01"] {
            assert!(parse_date(value).is_err());
        }
        for value in [
            "2026-01-01 00:00:00Z",
            "2026-01-01T00:00:60Z",
            "2026-01-01T00:00:00.1234567Z",
            "2026-01-01T00:00:00+14:01",
        ] {
            assert!(parse_zoned(value).is_err());
        }
        assert!(parse_zoned("2026-01-01T00:00:00-00:00").is_ok());
        assert!(parse_utc("2026-01-01T00:00:00-00:00").is_err());
    }
}
