//! Strict compact CAMS optical GRIB input and standalone model-grid sidecars.

use crate::grib::{EcCodes, Message};
use crate::tensor_bundle::{source_identity, BundleWriter, CoordinateGrid, SourceIdentity};
use chrono::{DateTime, Datelike, SecondsFormat, TimeZone, Timelike, Utc};
use firesky_native::cams::{self, Grid, Mapped, LEVELS};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

pub struct Optics {
    pub latitudes: Vec<f64>,
    pub longitudes: Vec<f64>,
    pub aod: Vec<f64>,
    pub extinction: Vec<f64>,
    pub valid_time: DateTime<Utc>,
}

const MERGE_COORD_SCALE: f64 = 10_000_000.0;

fn normalized_longitude(value: f64) -> f64 {
    let value = (value + 180.0).rem_euclid(360.0) - 180.0;
    if value == -180.0 && value.is_sign_negative() {
        -180.0
    } else {
        value
    }
}

fn merge_coordinate_key(lat: f64, lon: f64) -> Result<(i64, i64), String> {
    if !lat.is_finite() || !lon.is_finite() {
        return Err("CAMS merge contains nonfinite coordinates".into());
    }
    let lat = (lat * MERGE_COORD_SCALE).round();
    let lon = (normalized_longitude(lon) * MERGE_COORD_SCALE).round();
    if !lat.is_finite() || !lon.is_finite() {
        return Err("CAMS merge coordinate is outside the supported range".into());
    }
    Ok((lat as i64, lon as i64))
}

/// Merge ordinary CAMS rectangles that together cover a wrapped longitude
/// interval.  The source files are decoded independently because ecCodes
/// represents each area as a separate native grid.  Cells on the shared
/// dateline edge are canonicalized and retained once.
pub fn merge(parts: &[Optics]) -> Result<Optics, String> {
    if parts.is_empty() {
        return Err("CAMS merge requires at least one optical grid".into());
    }
    let valid_time = parts[0].valid_time;
    let mut cells = BTreeMap::<(i64, i64), usize>::new();
    let mut latitudes = Vec::new();
    let mut longitudes = Vec::new();
    let mut aod = Vec::new();
    let mut profiles: Vec<[f64; LEVELS.len()]> = Vec::new();
    for part in parts {
        if part.valid_time != valid_time {
            return Err("CAMS merge parts use different valid times".into());
        }
        let count = part.latitudes.len();
        if count == 0
            || part.longitudes.len() != count
            || part.aod.len() != count
            || part.extinction.len() != count * LEVELS.len()
            || count + latitudes.len() > cams::MAX_SOURCE_CELLS
        {
            return Err("invalid CAMS merge grid dimensions or cell limit".into());
        }
        for index in 0..count {
            let key = merge_coordinate_key(part.latitudes[index], part.longitudes[index])?;
            if cells.contains_key(&key) {
                // Area requests include their boundary cells.  Keep the first
                // value so the merged source identity stays deterministic.
                continue;
            }
            let output_index = latitudes.len();
            cells.insert(key, output_index);
            latitudes.push(part.latitudes[index]);
            longitudes.push(normalized_longitude(part.longitudes[index]));
            aod.push(part.aod[index]);
            let mut profile = [0.0; LEVELS.len()];
            for (level, value) in profile.iter_mut().enumerate() {
                *value = part.extinction[level * count + index];
            }
            profiles.push(profile);
        }
    }
    if latitudes.is_empty() {
        return Err("CAMS merge produced no cells".into());
    }
    let mut extinction = Vec::with_capacity(latitudes.len() * LEVELS.len());
    for level in 0..LEVELS.len() {
        extinction.extend(profiles.iter().map(|profile| profile[level]));
    }
    Ok(Optics {
        latitudes,
        longitudes,
        aod,
        extinction,
        valid_time,
    })
}

pub fn decode_many(codes: &EcCodes, paths: &[PathBuf]) -> Result<Optics, String> {
    if paths.is_empty() {
        return Err("CAMS decode requires at least one path".into());
    }
    let parts = paths
        .iter()
        .map(|path| decode(codes, path))
        .collect::<Result<Vec<_>, _>>()?;
    merge(&parts)
}

fn valid_time(message: &Message<'_>) -> Result<DateTime<Utc>, String> {
    let date = message.long(b"validityDate\0")?;
    let time = message.long(b"validityTime\0")?;
    validity(date, time)
}

pub(crate) fn validity(date: i64, time: i64) -> Result<DateTime<Utc>, String> {
    // GRIB validityTime is HHMM here: 6 means 00:06, not 06:00.
    if !(10_000_000..=99_999_999).contains(&date) || !(0..=2359).contains(&time) || time % 100 > 59
    {
        return Err("invalid CAMS validity date/time".into());
    }
    Utc.with_ymd_and_hms(
        (date / 10000) as i32,
        ((date / 100) % 100) as u32,
        (date % 100) as u32,
        (time / 100) as u32,
        (time % 100) as u32,
        0,
    )
    .single()
    .ok_or_else(|| "invalid CAMS validity date/time".into())
}

pub fn decode(codes: &EcCodes, path: &Path) -> Result<Optics, String> {
    let mut lats = None;
    let mut lons = None;
    let mut aod = None;
    let mut profiles: [Option<Vec<f64>>; 11] = std::array::from_fn(|_| None);
    let mut time = None;
    codes.visit_file(path, 12, |message| {
        let name = message.string(b"shortName\0")?;
        let level = message.long(b"level\0")?;
        let level_type = message.string(b"typeOfLevel\0")?;
        let profile = if name == "aod550" {
            if level_type != "surface" || level != 0 || aod.is_some() {
                return Err("CAMS aod550 must be unique at surface level 0".into());
            }
            None
        } else if name == "aerext532" {
            let index = LEVELS.iter().position(|&p| i64::from(p) == level);
            if level_type != "isobaricInhPa"
                || index.is_none()
                || message.string(b"units\0")? != "m**-1"
            {
                return Err("invalid CAMS aerext532 pressure level or units".into());
            }
            let index = index.unwrap();
            if profiles[index].is_some() {
                return Err("duplicate CAMS aerext532 level".into());
            }
            Some(index)
        } else {
            return Err(format!("unexpected CAMS optical shortName: {name}"));
        };
        let message_time = valid_time(message)?;
        if time.is_some_and(|v| v != message_time) {
            return Err("CAMS messages use different valid times".into());
        }
        time = Some(message_time);
        let count = usize::try_from(message.long(b"numberOfPoints\0")?)
            .map_err(|_| "invalid CAMS cell count")?;
        if count == 0 || count > cams::MAX_SOURCE_CELLS {
            return Err("CAMS cell limit exceeded".into());
        }
        let lat = message.array(b"latitudes\0", count)?;
        let lon = message.array(b"longitudes\0", count)?;
        let values = message.values_f64(count)?;
        if lat
            .iter()
            .chain(&lon)
            .chain(&values)
            .any(|v| !v.is_finite())
        {
            return Err("CAMS input contains missing or nonfinite coordinates/values".into());
        }
        if lats.as_ref().is_some_and(|v| v != &lat) || lons.as_ref().is_some_and(|v| v != &lon) {
            return Err("CAMS messages use different native grids".into());
        }
        if lats.is_none() {
            lats = Some(lat);
            lons = Some(lon);
        }
        if values
            .iter()
            .any(|&v| v < 0.0 || (profile.is_none() && v > 100.0))
        {
            return Err("CAMS optical values outside valid range".into());
        }
        if let Some(index) = profile {
            profiles[index] = Some(values);
        } else {
            aod = Some(values);
        }
        Ok(())
    })?;
    let aod = aod.ok_or("CAMS optical input is incomplete: missing aod550")?;
    let mut extinction = Vec::with_capacity(aod.len() * 11);
    for profile in profiles {
        extinction
            .extend(profile.ok_or("CAMS optical input is incomplete: missing pressure level")?);
    }
    if aod.iter().enumerate().any(|(cell, &v)| {
        v > 0.0 && (0..11).all(|level| extinction[level * aod.len() + cell] <= 0.0)
    }) {
        return Err("CAMS positive aod550 has zero extinction profile".into());
    }
    Ok(Optics {
        latitudes: lats.ok_or("missing CAMS coordinates")?,
        longitudes: lons.ok_or("missing CAMS coordinates")?,
        aod,
        extinction,
        valid_time: time.ok_or("missing CAMS valid time")?,
    })
}

impl Optics {
    pub fn grid(&self) -> Grid<'_> {
        Grid {
            latitudes: &self.latitudes,
            longitudes: &self.longitudes,
            aod: &self.aod,
            extinction: &self.extinction,
        }
    }
    fn metadata(&self) -> Value {
        json!({"decoder_backend": "rust-cams-v1", "pressure_levels": LEVELS,
            "valid_time_utc": self.valid_time.to_rfc3339_opts(SecondsFormat::AutoSi, true)})
    }
    pub fn write(&self, output: &Path, source: SourceIdentity) -> Result<BundleWriter, String> {
        let mut writer = BundleWriter::create(output, "cams-optics-v1", source, self.metadata())?;
        let cells = self.aod.len() as u64;
        writer.f64("latitudes", &[cells], self.latitudes.iter().copied())?;
        writer.f64("longitudes", &[cells], self.longitudes.iter().copied())?;
        writer.f64("aod550", &[cells], self.aod.iter().copied())?;
        writer.f64("aerext532", &[11, cells], self.extinction.iter().copied())?;
        Ok(writer)
    }
}

pub fn bracket(value: &str) -> Result<(DateTime<Utc>, DateTime<Utc>, f32), String> {
    let target = DateTime::parse_from_rfc3339(value)
        .map_err(|e| e.to_string())?
        .with_timezone(&Utc);
    if !(1..=9999).contains(&target.year())
        || target.nanosecond() >= 1_000_000_000
        || target.nanosecond() % 1000 != 0
    {
        return Err("CAMS target requires datetime-compatible microsecond precision".into());
    }
    let left = Utc
        .timestamp_opt(target.timestamp().div_euclid(10800) * 10800, 0)
        .single()
        .ok_or("CAMS bracket overflow")?;
    let right = if target == left {
        left
    } else {
        left.checked_add_signed(chrono::Duration::hours(3))
            .ok_or("CAMS bracket overflow")?
    };
    if !(1..=9999).contains(&left.year()) || !(1..=9999).contains(&right.year()) {
        return Err("CAMS bracket outside datetime range".into());
    }
    let elapsed = target
        .signed_duration_since(left)
        .num_microseconds()
        .ok_or("CAMS duration overflow")?;
    Ok((left, right, (elapsed as f64 / 10_800_000_000.0) as f32))
}

fn write_mapped(
    output: &Path,
    source: SourceIdentity,
    mut metadata: Value,
    targets: &CoordinateGrid,
    mapped: &Mapped,
) -> Result<BundleWriter, String> {
    metadata["target_manifest"] = json!(targets.source_manifest);
    metadata["target_source"] = json!(targets.source);
    metadata["pressure_levels"] = json!(LEVELS);
    metadata["sampling_backend"] = json!("rust-cams-v1");
    let mut writer = BundleWriter::create(output, "cams-mapped-v1", source, metadata)?;
    let cells = mapped.aod.len() as u64;
    writer.f64("latitudes", &[cells], targets.latitudes.iter().copied())?;
    writer.f64("longitudes", &[cells], targets.longitudes.iter().copied())?;
    writer.f32("aod550", &[cells], mapped.aod.iter().copied())?;
    writer.f32(
        "aerext532",
        &[cells, 11],
        mapped.profiles.iter().flatten().copied(),
    )?;
    writer.f32(
        "covered",
        &[cells],
        mapped.covered.iter().map(|&v| if v { 1.0 } else { 0.0 }),
    )?;
    Ok(writer)
}

pub fn map_file(path: &Path, target: &Path, output: &Path) -> Result<PathBuf, String> {
    let source = source_identity(path)?;
    let targets = CoordinateGrid::read(target, cams::MAX_TARGET_CELLS)?;
    let optics = decode(&EcCodes::load()?, path)?;
    let mapped = cams::map(&optics.grid(), &targets.latitudes, &targets.longitudes)?;
    let writer = write_mapped(output, source, optics.metadata(), &targets, &mapped)?;
    targets.revalidate()?;
    writer.commit(path)
}

pub fn interpolate_files(
    left_path: &Path,
    right_path: &Path,
    target: &Path,
    target_time: &str,
    output: &Path,
) -> Result<PathBuf, String> {
    let (left_time, right_time, weight) = bracket(target_time)?;
    let source = source_identity(left_path)?;
    let right_source = source_identity(right_path)?;
    let targets = CoordinateGrid::read(target, cams::MAX_TARGET_CELLS)?;
    let codes = EcCodes::load()?;
    let left = decode(&codes, left_path)?;
    if left.valid_time != left_time {
        return Err("CAMS left valid time does not match target bracket".into());
    }
    let mut mapped = cams::map(&left.grid(), &targets.latitudes, &targets.longitudes)?;
    drop(left);
    // Exact brackets may share one file; retain the same f32 arithmetic even at weight zero.
    let right = if left_time == right_time && left_path == right_path {
        Mapped {
            aod: mapped.aod.clone(),
            profiles: mapped.profiles.clone(),
            covered: mapped.covered.clone(),
        }
    } else {
        let right = decode(&codes, right_path)?;
        if right.valid_time != right_time {
            return Err("CAMS right valid time does not match target bracket".into());
        }
        cams::map(&right.grid(), &targets.latitudes, &targets.longitudes)?
    };
    if mapped
        .covered
        .iter()
        .zip(&right.covered)
        .any(|(&a, &b)| !a || !b)
    {
        return Err("CAMS full profile does not cover every target model cell".into());
    }
    mapped.aod = cams::blend(&mapped.aod, &right.aod, weight)?;
    // Fixed-size rows avoid another large flattened profile allocation.
    for (row, rhs) in mapped.profiles.iter_mut().zip(right.profiles) {
        for (v, r) in row.iter_mut().zip(rhs) {
            *v = *v + (r - *v) * weight;
        }
    }
    let writer = write_mapped(
        output,
        source,
        json!({
            "target_time": target_time, "left_valid_time": left_time.to_rfc3339(),
            "right_valid_time": right_time.to_rfc3339(), "right_weight": weight,
            "right_source": right_source, "decoder_backend": "rust-cams-v1",
        }),
        &targets,
        &mapped,
    )?;
    targets.revalidate()?;
    if source_identity(right_path)? != right_source {
        return Err("CAMS right source changed during interpolation".into());
    }
    writer.commit(left_path)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn brackets_microseconds_offsets_and_pre_epoch() {
        let (left, right, w) = bracket("2026-11-09T08:00:00+08:00").unwrap();
        assert_eq!(left, right);
        assert_eq!(w, 0.);
        let (left, right, w) = bracket("1969-12-31T23:00:00Z").unwrap();
        assert_eq!(left.hour(), 21);
        assert_eq!(right.hour(), 0);
        assert_eq!(w, (2.0 / 3.0) as f32);
        assert!(bracket("2026-11-09T00:00:00.000000001Z").is_err());
        assert!(bracket("2016-12-31T23:59:60Z").is_err());
        assert!(bracket("9999-12-31T23:59:00Z").is_err());
    }

    #[test]
    fn merge_deduplicates_dateline_boundary_cells() {
        let left = Optics {
            latitudes: vec![0.0, 1.0],
            longitudes: vec![179.6, 180.0],
            aod: vec![1.0, 2.0],
            extinction: (0..11)
                .flat_map(|level| [level as f64 + 1.0, level as f64 + 2.0])
                .collect(),
            valid_time: Utc.with_ymd_and_hms(2026, 11, 12, 0, 0, 0).unwrap(),
        };
        let right = Optics {
            latitudes: vec![1.0, 1.0],
            longitudes: vec![-180.0, -179.6],
            aod: vec![20.0, 3.0],
            extinction: (0..11)
                .flat_map(|level| [level as f64 + 20.0, level as f64 + 3.0])
                .collect(),
            valid_time: left.valid_time,
        };
        let merged = merge(&[left, right]).unwrap();
        assert_eq!(merged.latitudes.len(), 3);
        assert!(merged
            .longitudes
            .iter()
            .zip([179.6, -180.0, -179.6])
            .all(|(left, right)| (left - right).abs() < 1e-9));
        assert_eq!(merged.aod, [1.0, 2.0, 3.0]);
        assert_eq!(merged.extinction.len(), 33);
        assert_eq!(merged.extinction[3], 2.0);
    }

    #[test]
    fn merge_rejects_mixed_valid_times() {
        let valid_time = Utc.with_ymd_and_hms(2026, 11, 12, 0, 0, 0).unwrap();
        let base = Optics {
            latitudes: vec![0.0],
            longitudes: vec![0.0],
            aod: vec![1.0],
            extinction: vec![1.0; LEVELS.len()],
            valid_time,
        };
        let other = Optics {
            latitudes: vec![0.0],
            longitudes: vec![0.0],
            aod: vec![1.0],
            extinction: vec![1.0; LEVELS.len()],
            valid_time: valid_time + chrono::Duration::hours(3),
        };
        assert!(merge(&[base, other]).is_err());
    }
}
