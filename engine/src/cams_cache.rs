//! Production compact-profile cache selection; no authenticated ADS downloads.
use crate::{cams, grib::EcCodes, time_contract};
use chrono::{DateTime, Duration, TimeZone, Utc};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    fs,
    path::{Path, PathBuf},
    sync::{Mutex, OnceLock},
    time::SystemTime,
};

#[derive(PartialEq, Eq)]
struct ProbeKey {
    path: PathBuf,
    bytes: u64,
    modified: SystemTime,
}
type ProbeCache = VecDeque<(ProbeKey, Option<[f64; 4]>)>;
static PROBES: OnceLock<Mutex<ProbeCache>> = OnceLock::new();

fn probe(
    path: &Path,
    before: &fs::Metadata,
    codes: &mut Option<EcCodes>,
) -> Result<Option<[f64; 4]>, String> {
    let (Ok(path_key), Ok(modified)) = (fs::canonicalize(path), before.modified()) else {
        return Ok(None);
    };
    let key = ProbeKey {
        path: path_key,
        bytes: before.len(),
        modified,
    };
    {
        let mut cache = PROBES.get_or_init(Mutex::default).lock().unwrap();
        if let Some(position) = cache.iter().position(|(old, _)| old == &key) {
            let row = cache.remove(position).unwrap();
            let result = row.1;
            cache.push_back(row);
            return Ok(result);
        }
    }
    let decoder = match codes {
        Some(codes) => codes,
        None => codes.insert(EcCodes::load()?),
    };
    let result = cams::decode(decoder, path).ok().map(|optics| {
        let north = optics
            .latitudes
            .iter()
            .copied()
            .fold(f64::NEG_INFINITY, f64::max);
        let south = optics
            .latitudes
            .iter()
            .copied()
            .fold(f64::INFINITY, f64::min);
        let mut west = f64::INFINITY;
        let mut east = f64::NEG_INFINITY;
        for &lon in &optics.longitudes {
            let lon = (lon + 180.0).rem_euclid(360.0) - 180.0;
            west = west.min(lon);
            east = east.max(lon);
        }
        [north, west, south, east]
    });
    let Ok(after) = fs::symlink_metadata(path) else {
        return Ok(None);
    };
    if !after.is_file() || after.len() != key.bytes || after.modified().ok() != Some(key.modified) {
        return Ok(None);
    }
    // Retain only validation/area metadata, never the decoded optical tensors.
    // A failure is memoized too, under the same file-version key as Python.
    let mut cache = PROBES.get_or_init(Mutex::default).lock().unwrap();
    cache.retain(|(old, _)| old.path != key.path);
    cache.push_back((key, result));
    while cache.len() > 256 {
        cache.pop_front();
    }
    Ok(result)
}

pub(crate) fn optics_valid(path: &Path, codes: &mut Option<EcCodes>) -> bool {
    // Desktop selection follows regular file links, as the owning Python reader does.
    let Ok(path) = path.canonicalize() else {
        return false;
    };
    let Ok(before) = fs::metadata(&path) else {
        return false;
    };
    before.is_file()
        && before.len() > 0
        && before.len() <= 512 * 1024 * 1024
        && probe(&path, &before, codes).ok().flatten().is_some()
}

#[derive(Clone, Serialize)]
pub(crate) struct Selection {
    pub path: PathBuf,
    #[serde(serialize_with = "serialize_time")]
    pub valid_time: DateTime<Utc>,
    #[serde(serialize_with = "serialize_time")]
    pub run_time_utc: DateTime<Utc>,
    pub lead_hours: u32,
}

fn serialize_time<S: serde::Serializer>(
    value: &DateTime<Utc>,
    serializer: S,
) -> Result<S::Ok, S::Error> {
    serializer.serialize_str(&time_contract::iso(*value))
}

pub(crate) fn parse(path: &Path) -> Option<Selection> {
    let name = path.file_name()?.to_str()?;
    let rest = name
        .strip_prefix("cams-optics-v2-")?
        .strip_suffix(".grib")?;
    let parts: Vec<_> = rest.splitn(6, '-').collect();
    if parts.len() != 6 || parts[5].is_empty() || parts[5].contains('.') {
        return None;
    }
    for (value, length) in parts[..5].iter().zip([4, 2, 2, 4, 3]) {
        if value.len() != length || !value.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
    }
    let run = Utc
        .with_ymd_and_hms(
            parts[0].parse().ok()?,
            parts[1].parse().ok()?,
            parts[2].parse().ok()?,
            parts[3][..2].parse().ok()?,
            parts[3][2..].parse().ok()?,
            0,
        )
        .single()?;
    time_contract::checked(run).ok()?;
    let lead: u32 = parts[4].parse().ok()?;
    let valid =
        time_contract::checked(run.checked_add_signed(Duration::hours(lead.into()))?).ok()?;
    Some(Selection {
        path: path.to_owned(),
        valid_time: valid,
        run_time_utc: run,
        lead_hours: lead,
    })
}

pub(crate) fn area_key(area: [f64; 4]) -> Result<String, String> {
    // Planner areas are float values rounded to three decimals. Preserve
    // Python json.dumps(list[float]) spacing and integral ".0" spellings.
    let values = area
        .into_iter()
        .map(|v| serde_json::to_string(&v).map_err(|e| e.to_string()))
        .collect::<Result<Vec<_>, _>>()?;
    let encoded = format!("[{}]", values.join(", "));
    Ok(Sha256::digest(encoded.as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<String>()[..12]
        .into())
}

fn paths(root: &Path) -> Result<Vec<PathBuf>, String> {
    if !root.exists() {
        return Ok(Vec::new());
    }
    let mut pending = vec![(root.to_owned(), 0)];
    let mut paths = Vec::new();
    let mut visited = 0;
    while let Some((dir, depth)) = pending.pop() {
        if depth > 64 {
            return Err("CAMS cache depth exceeds 64".into());
        }
        let mut children = Vec::new();
        for item in fs::read_dir(dir).map_err(|e| e.to_string())? {
            let item = item.map_err(|e| e.to_string())?;
            visited += 1;
            if visited > 1_000_000 {
                return Err("CAMS cache scan exceeds one million entries".into());
            }
            let kind = item.file_type().map_err(|e| e.to_string())?;
            if kind.is_dir() {
                children.push((item.path(), depth + 1));
            } else if kind.is_file() {
                let name = item.file_name();
                if name.to_string_lossy().starts_with("cams-optics-v2-")
                    && name.to_string_lossy().ends_with(".grib")
                {
                    paths.push(item.path());
                    if paths.len() > 100_000 {
                        return Err("CAMS cache exceeds 100000 candidates".into());
                    }
                }
            }
        }
        pending.extend(children.into_iter().rev());
    }
    Ok(paths)
}

pub(crate) fn select(
    root: &Path,
    area: [f64; 4],
    required: &BTreeSet<DateTime<Utc>>,
) -> Result<BTreeMap<DateTime<Utc>, Selection>, String> {
    select_with_key(root, area, required, &area_key(area)?)
}

pub(crate) fn select_with_key(
    root: &Path,
    area: [f64; 4],
    required: &BTreeSet<DateTime<Utc>>,
    key: &str,
) -> Result<BTreeMap<DateTime<Utc>, Selection>, String> {
    let suffix = format!("-{key}.grib");
    let mut candidates: Vec<_> = paths(root)?
        .into_iter()
        .filter_map(|path| {
            let selection = parse(&path)?;
            required.contains(&selection.valid_time).then(|| {
                let exact = path
                    .file_name()
                    .unwrap()
                    .to_string_lossy()
                    .ends_with(&suffix);
                (selection, exact)
            })
        })
        .collect();
    // Stable sort retains traversal order for equal run/exact priorities,
    // matching Python's stable candidate sorting rather than smallest area.
    candidates.sort_by(|(a, ae), (b, be)| b.run_time_utc.cmp(&a.run_time_utc).then(be.cmp(ae)));
    let mut result = BTreeMap::new();
    let mut codes = None;
    for (selection, exact) in candidates {
        if result.contains_key(&selection.valid_time) {
            continue;
        }
        let Ok(before) = fs::symlink_metadata(&selection.path) else {
            continue;
        };
        if !before.is_file() || before.len() == 0 || before.len() > 512 * 1024 * 1024 {
            continue;
        }
        let Some([north, west, south, east]) = probe(&selection.path, &before, &mut codes)? else {
            continue;
        };
        if !exact {
            let tolerance = 0.2 + 1e-9;
            if north + tolerance < area[0]
                || west - tolerance > area[1]
                || south - tolerance > area[2]
                || east + tolerance < area[3]
            {
                continue;
            }
        }
        let Ok(after) = fs::symlink_metadata(&selection.path) else {
            continue;
        };
        if !after.is_file()
            || before.len() != after.len()
            || before.modified().ok() != after.modified().ok()
        {
            continue;
        }
        // Do not turn a filename/GRIB time disagreement into fallback to an
        // older run: production selection admits it and scoring fails closed.
        result.insert(selection.valid_time, selection);
        if result.len() == required.len() {
            break;
        }
    }
    Ok(result)
}

pub(crate) fn nearest_with_key(
    root: &Path,
    area: [f64; 4],
    target: DateTime<Utc>,
    key: &str,
) -> Result<Option<Selection>, String> {
    let suffix = format!("-{key}.grib");
    let mut candidates: Vec<_> = paths(root)?
        .into_iter()
        .filter_map(|path| {
            let value = parse(&path)?;
            let delta = (value.valid_time - target)
                .num_microseconds()?
                .unsigned_abs();
            (delta <= 7_200_000_000).then(|| {
                let exact = path
                    .file_name()
                    .unwrap()
                    .to_string_lossy()
                    .ends_with(&suffix);
                (value, delta, exact)
            })
        })
        .collect();
    candidates.sort_by(|(a, ad, ae), (b, bd, be)| {
        ad.cmp(bd)
            .then(b.run_time_utc.cmp(&a.run_time_utc))
            .then(be.cmp(ae))
    });
    let mut codes = None;
    for (value, _, exact) in candidates {
        if !fs::symlink_metadata(&value.path)
            .is_ok_and(|m| m.is_file() && m.len() > 0 && m.len() <= 512 * 1024 * 1024)
        {
            continue;
        }
        if !exact {
            let decoder = match &mut codes {
                Some(codes) => codes,
                None => codes.insert(EcCodes::load()?),
            };
            let Ok([n, w, s, e]) = decoder.first_area(&value.path) else {
                continue;
            };
            let tolerance = 0.2 + 1e-9;
            if n + tolerance < area[0]
                || w - tolerance > area[1]
                || s - tolerance > area[2]
                || e + tolerance < area[3]
            {
                continue;
            }
        }
        // The legacy nonexact selector checks only headers/filenames, not
        // complete optics. Keep it distinct from strict bracket selection.
        return Ok(Some(value));
    }
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cache_names_preserve_run_minutes_lead_and_hyphenated_area() {
        let value = parse(Path::new(
            "cams-optics-v2-2026-11-09-1230-003-area-name.grib",
        ))
        .unwrap();
        assert_eq!(
            time_contract::iso(value.valid_time),
            "2026-11-09T15:30:00+00:00"
        );
        assert_eq!(value.lead_hours, 3);
        for name in [
            "cams-optics-v2-2026-02-30-0000-003-area.grib",
            "cams-optics-v2-2026-11-09-2400-003-area.grib",
            "cams-optics-v2-2026-11-09-0000-03-area.grib",
            "cams-optics-v2-2026-11-09-0000-003-.grib",
            "cams-optics-v2-2026-11-09-0000-003-a.b.grib",
            "cams-optics-v2-0000-12-31-2300-003-area.grib",
            "cams-optics-v2-9999-12-31-2300-003-area.grib",
        ] {
            assert!(parse(Path::new(name)).is_none(), "{name}");
        }
    }
}
