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

type ArcKey = (PathBuf, u64, SystemTime, [u64; 4]);
static ARC_CHECKS: OnceLock<Mutex<VecDeque<(ArcKey, bool)>>> = OnceLock::new();

/// Contiguous longitude runs of a GRIB file's first message, normalized to
/// [-180, 180). A wrapped (antimeridian) grid yields two runs; an ordinary
/// grid yields one. Spacing is detected from the smallest positive gap so
/// the single wrap seam never merges the two sides.
pub(crate) fn longitude_runs(path: &Path) -> Option<Vec<(f64, f64)>> {
    let codes = EcCodes::load().ok()?;
    let mut longitudes: Option<Vec<f64>> = None;
    let visit = codes.visit_while(path, 1, |message| {
        let count = message.size(b"values\0")?;
        if count == 0 || count > 5_000_000 {
            return Ok(false);
        }
        longitudes = Some(message.array(b"longitudes\0", count)?);
        Ok(false)
    });
    let longitudes = longitudes.filter(|_| visit.is_ok())?;
    let mut unique: Vec<f64> = longitudes
        .iter()
        .map(|&v| (v + 180.0).rem_euclid(360.0) - 180.0)
        .collect();
    unique.sort_by(f64::total_cmp);
    unique.dedup_by(|a, b| (*a - *b).abs() < 1e-6);
    if unique.len() < 2 {
        return None;
    }
    let spacing = unique
        .windows(2)
        .map(|w| w[1] - w[0])
        .filter(|&g| g > 1e-6)
        .min_by(f64::total_cmp)?;
    if spacing <= 0.0 {
        return None;
    }
    let mut runs = Vec::new();
    let mut start = unique[0];
    let mut prev = unique[0];
    for &lon in &unique[1..] {
        if lon - prev > spacing * 1.5 {
            runs.push((start, prev));
            start = lon;
        }
        prev = lon;
    }
    runs.push((start, prev));
    Some(runs)
}

/// Whether the candidate's actual longitude arc covers `bounds`. ADS can
/// answer a wrapped part request with a broader (even global) grid stored
/// under that part's area hash; normalized min/max bounds then span both
/// parts and cannot prove coverage of either. Require a contiguous run
/// spanning the requested part instead.
pub(crate) fn arc_covers(path: &Path, bounds: [f64; 4]) -> bool {
    let Ok(before) = fs::metadata(path) else {
        return false;
    };
    let (Ok(path_key), Ok(modified)) = (fs::canonicalize(path), before.modified()) else {
        return false;
    };
    let key: ArcKey = (
        path_key,
        before.len(),
        modified,
        bounds.map(|v| v.to_bits()),
    );
    {
        let mut cache = ARC_CHECKS.get_or_init(|| Mutex::default()).lock().unwrap();
        if let Some(position) = cache.iter().position(|(old, _)| old == &key) {
            let row = cache.remove(position).unwrap();
            let result = row.1;
            cache.push_back(row);
            return result;
        }
    }
    let tolerance = 0.2 + 1e-9;
    let [north, west, south, east] = bounds;
    let result = longitude_runs(path).is_some_and(|runs| {
        runs.iter()
            .any(|&(a, b)| a - tolerance <= west && b + tolerance >= east && north >= south)
    });
    let Ok(after) = fs::symlink_metadata(path) else {
        return false;
    };
    if !after.is_file() || after.len() != before.len() || after.modified().ok() != Some(modified) {
        return false;
    }
    let mut cache = ARC_CHECKS.get_or_init(|| Mutex::default()).lock().unwrap();
    cache.retain(|(old, _)| old.0 != key.0);
    cache.push_back((key, result));
    while cache.len() > 256 {
        cache.pop_front();
    }
    result
}

/// A cached file may only be reused across the antimeridian when its own
/// longitude arc wraps the same way as the request. Ordinary west/east
/// comparisons would let a [26, 180] file "cover" a [-180, -168] request.
fn wrapped_request(area: [f64; 4]) -> bool {
    area[1] > area[3]
}

fn wrap_reuse_allowed(path: &Path, area: [f64; 4]) -> bool {
    // Ordinary requests read west < east; a wrapped request crosses the
    // antimeridian and reads west > east (e.g. 170 -> -170).
    if !wrapped_request(area) {
        // ADS can answer a wrapped part request with a broader (even global)
        // grid stored under a sibling area hash. Its normalized min/max then
        // spans the whole globe while its true runs cover only one side, so
        // an ordinary strip request (e.g. [-180, -168]) must still prove a
        // contiguous run over its arc. Single-run files were already proven
        // by the min/max bounds check.
        return longitude_runs(path).is_none_or(|runs| runs.len() <= 1 || arc_covers(path, area));
    }
    // Probed bounds are normalized min/max, so a wrapped file also reads as
    // west < east. Verify the true longitude runs against both request arcs
    // instead: an ordinary sibling segment lacks one of them and is rejected.
    let w = arc_covers(path, [area[0], area[1], area[2], 180.0]);
    let e = arc_covers(path, [area[0], -180.0, area[2], area[3]]);
    w && e
}

/// The regular model grid the assessment will map CAMS onto. Selection uses it
/// to enforce the same fail-closed coverage contract as the scorer, including
/// the nearest-source fallback across a sub-grid longitude gap.
pub(crate) struct ModelCoverage<'a> {
    pub lats: &'a [f64],
    pub lons: &'a [f64],
}

/// Regular 0.25-degree model axes of a prepared bbox, matching the grid the
/// assessment maps CAMS onto. Wrapped bboxes keep the assessment's unwrapped
/// longitude representation (e.g. 26.75..353.75); `map` normalizes targets.
pub(crate) fn model_axes(bbox: [f64; 4]) -> (Vec<f64>, Vec<f64>) {
    let step = 0.25_f64;
    let axis = |lo: f64, hi: f64| -> Vec<f64> {
        let count = ((hi - lo) / step).round() as i64;
        (0..=count)
            .map(|i| {
                let v = lo + i as f64 * step;
                if v == 0.0 {
                    0.0
                } else {
                    v
                }
            })
            .collect()
    };
    let lats = axis(bbox[3], bbox[2]);
    let mut lons = if bbox[0] <= bbox[1] {
        axis(bbox[0], bbox[1])
    } else {
        // Wrapped request: the assessment grid is the union of the two
        // served segments, each canonicalized to the 0.25-degree grid.
        let west_part = crate::gfs_data::canonical_bbox([bbox[0], 180.0, bbox[2], bbox[3]], "0p25")
            .unwrap_or([bbox[0], 180.0, bbox[2], bbox[3]]);
        let east_part =
            crate::gfs_data::canonical_bbox([-180.0, bbox[1], bbox[2], bbox[3]], "0p25")
                .unwrap_or([-180.0, bbox[1], bbox[2], bbox[3]]);
        let mut lons = axis(west_part[0], west_part[1]);
        lons.extend(axis(east_part[0], east_part[1]));
        lons
    };
    lons.sort_unstable_by(f64::total_cmp);
    lons.dedup();
    (lats, lons)
}

struct CoverageAxes {
    lats: Vec<f64>,
    lons: Vec<f64>,
    indices: Vec<usize>,
}

fn norm_lon(value: f64) -> f64 {
    (value + 180.0).rem_euclid(360.0) - 180.0
}

/// Boundary-column bridge for wrapped groups: the -180 and +180 columns are
/// the same physical meridian, so a sibling part that serves one signed edge
/// also supplies the other. Only the boundary column is borrowed; interior
/// gaps still fail closed.
struct BoundaryBridge {
    lats: Vec<f64>,
    /// (grid slot, cell index) pairs aligned with `lats`.
    cells: Vec<(usize, usize)>,
}

fn boundary_bridge(
    grids: &[crate::native::cams::Grid<'_>],
    positive: bool,
) -> Option<BoundaryBridge> {
    let target = if positive { 180.0 } else { -180.0 };
    for (slot, grid) in grids.iter().enumerate() {
        let cells = grid.latitudes.len();
        if cells == 0
            || cells > crate::native::cams::MAX_SOURCE_CELLS
            || grid.longitudes.len() != cells
            || grid.aod.len() != cells
            || grid.extinction.len() != cells * crate::native::cams::LEVELS.len()
        {
            continue;
        }
        let mut column: Vec<(f64, (usize, usize))> = grid
            .latitudes
            .iter()
            .zip(grid.longitudes)
            .enumerate()
            .filter(|(_, (_, &lon))| (lon - target).abs() < 1e-9)
            .map(|(index, (&lat, _))| (lat, (slot, index)))
            .collect();
        if column.is_empty() {
            continue;
        }
        column.sort_unstable_by(|a, b| a.0.total_cmp(&b.0));
        column.dedup_by(|a, b| a.0 == b.0);
        // The first sibling grid that serves the column wins, matching the
        // merge part order for the shared boundary cells.
        let lats = column.iter().map(|&(lat, _)| lat).collect();
        let cells = column.into_iter().map(|(_, pair)| pair).collect();
        return Some(BoundaryBridge { lats, cells });
    }
    None
}

fn coverage_axes(
    grid: &crate::native::cams::Grid<'_>,
    bridge_pos: Option<&BoundaryBridge>,
    bridge_neg: Option<&BoundaryBridge>,
) -> Option<CoverageAxes> {
    let cells = grid.latitudes.len();
    if cells == 0
        || cells > crate::native::cams::MAX_SOURCE_CELLS
        || grid.longitudes.len() != cells
        || grid.aod.len() != cells
        || grid.extinction.len() != cells * crate::native::cams::LEVELS.len()
    {
        return None;
    }
    let mut lats = grid.latitudes.to_vec();
    lats.sort_unstable_by(f64::total_cmp);
    lats.dedup();
    let mut lons: Vec<f64> = grid.longitudes.iter().copied().map(norm_lon).collect();
    lons.sort_unstable_by(f64::total_cmp);
    lons.dedup();
    // Mirror native::cams::map: keep the raw +/-180 boundary column adjacent
    // to its seam neighbors so wrapped model targets stay covered.
    crate::native::cams::restore_antimeridian(&mut lons, grid.longitudes);
    let has_pos = lons.last() == Some(&180.0);
    let has_neg = lons.first() == Some(&-180.0);
    let raw_pos = grid.longitudes.iter().any(|&v| (v - 180.0).abs() < 1e-9);
    let raw_neg = grid.longitudes.iter().any(|&v| (v + 180.0).abs() < 1e-9);
    // A grid serving only the -180 sign but reaching positive longitudes
    // gets the +180 end from restore_antimeridian; fill it from its own -180
    // column below. Otherwise the +180 end comes from a sibling bridge.
    let self_pos = raw_neg && !raw_pos && has_pos;
    if !has_pos && bridge_pos.is_some() {
        lons.push(180.0);
    }
    if !has_neg {
        if bridge_neg.is_some() {
            lons.insert(0, -180.0);
        }
    }
    let dense = lats.len().checked_mul(lons.len())?;
    if dense > crate::native::cams::MAX_SOURCE_CELLS {
        return None;
    }
    let mut indices = vec![usize::MAX; dense];
    for (index, (&lat, &raw_lon)) in grid.latitudes.iter().zip(grid.longitudes).enumerate() {
        let lon = norm_lon(raw_lon);
        let row = lats.partition_point(|&v| v < lat);
        let mut col = lons.partition_point(|&v| v < lon);
        // The appended +180 column shares the physical -180 column; targets
        // on the seam itself read the raw-sign copy, exactly like the scorer.
        if lon == -180.0 && lons.last() == Some(&180.0) {
            col = lons.len() - 1;
        }
        indices[row * lons.len() + col] = index;
    }
    // Fill borrowed boundary columns from the sibling bridge. Bridge cells
    // are encoded as (slot + 1) * MAX_SOURCE_CELLS + cell so they never
    // collide with own-grid indices or the usize::MAX hole sentinel.
    let fill_bridge = |indices: &mut [usize], bridge: Option<&BoundaryBridge>, edge: f64| {
        let Some(bridge) = bridge else { return };
        let col = lons.partition_point(|&v| v < edge);
        if col >= lons.len() || lons[col] != edge {
            return;
        }
        for (row, &lat) in lats.iter().enumerate() {
            let b = bridge.lats.partition_point(|&v| v < lat);
            if b >= bridge.lats.len() || bridge.lats[b] != lat {
                continue;
            }
            let (slot, cell) = bridge.cells[b];
            indices[row * lons.len() + col] =
                (slot + 1) * crate::native::cams::MAX_SOURCE_CELLS + cell;
        }
    };
    if !has_pos && !self_pos {
        fill_bridge(&mut indices, bridge_pos, 180.0);
    }
    if !has_neg {
        fill_bridge(&mut indices, bridge_neg, -180.0);
    }
    Some(CoverageAxes {
        lats,
        lons,
        indices,
    })
}

fn axis_bounds(axis: &[f64], target: f64) -> (usize, usize, f64) {
    let upper = axis.partition_point(|&v| v < target).min(axis.len() - 1);
    let lower = upper.saturating_sub(1);
    let denominator = axis[upper] - axis[lower];
    let weight = if denominator > 0.0 {
        ((target - axis[lower]) / denominator).clamp(0.0, 1.0)
    } else {
        0.0
    };
    (lower, upper, weight)
}

/// Mirrors `native::cams::map` coverage semantics (bilinear with nearest
/// fallback, AOD/extinction sign bounds) on the model grid corners. A group
/// that leaves any model cell uncovered must not be selected. Wrapped groups
/// are judged together: the +/-180 boundary columns are the same physical
/// meridian, so a sibling part's boundary column bridges the seam gap exactly
/// like the scorer's merged grid does.
pub(crate) fn model_grid_covers_group(
    paths: &[PathBuf],
    codes: &EcCodes,
    coverage: &ModelCoverage<'_>,
) -> bool {
    if paths.is_empty() || coverage.lats.len() < 2 || coverage.lons.len() < 2 {
        return false;
    }
    let mut decoded = Vec::with_capacity(paths.len());
    for path in paths {
        let Ok(optics) = cams::decode(codes, path) else {
            return false;
        };
        decoded.push(optics);
    }
    let grids: Vec<crate::native::cams::Grid<'_>> = decoded.iter().map(|o| o.grid()).collect();
    let bridge_pos = boundary_bridge(&grids, true);
    let bridge_neg = boundary_bridge(&grids, false);
    let mut axes = Vec::with_capacity(grids.len());
    for grid in &grids {
        let Some(axis) = coverage_axes(grid, bridge_pos.as_ref(), bridge_neg.as_ref()) else {
            return false;
        };
        if axis.lats.len() < 2 || axis.lons.len() < 2 {
            return false;
        }
        axes.push(axis);
    }
    let stride = crate::native::cams::MAX_SOURCE_CELLS;
    let value = |channel: usize, index: usize| -> f64 {
        if index == usize::MAX {
            return f64::NAN;
        }
        let (slot, cell) = if index < stride {
            (0, index)
        } else {
            (index / stride - 1, index % stride)
        };
        let Some(grid) = grids.get(slot) else {
            return f64::NAN;
        };
        let cells = grid.latitudes.len();
        if cell >= cells {
            return f64::NAN;
        }
        if channel == 0 {
            grid.aod[cell]
        } else {
            grid.extinction[(channel - 1) * cells + cell]
        }
    };
    // Longitude targets outside a source axis are clamped to its boundary by
    // `axis_bounds`, exactly as the scorer does; the per-cell bilinear /
    // nearest fallback decides coverage, so there is no envelope early-exit.
    for &lat in coverage.lats {
        for &raw_lon in coverage.lons {
            let lon = norm_lon(raw_lon);
            // The +/-180 model columns normalize to the same meridian; a
            // part may serve either signed edge, so try both spellings.
            let candidates: [f64; 2] = if lon == -180.0 {
                [-180.0, 180.0]
            } else {
                [lon, lon]
            };
            let mut covered = false;
            'axes: for axes in &axes {
                for &lon in &candidates {
                    // `native::cams::map` marks targets outside the source
                    // envelope uncovered even when the clamped sample is finite.
                    if lat < axes.lats[0]
                        || lat > axes.lats[axes.lats.len() - 1]
                        || lon < axes.lons[0]
                        || lon > axes.lons[axes.lons.len() - 1]
                    {
                        continue;
                    }
                    let (a, b, y) = axis_bounds(&axes.lats, lat);
                    let (c, d, x) = axis_bounds(&axes.lons, lon);
                    let corners = [
                        axes.indices[a * axes.lons.len() + c],
                        axes.indices[a * axes.lons.len() + d],
                        axes.indices[b * axes.lons.len() + c],
                        axes.indices[b * axes.lons.len() + d],
                    ];
                    let nearest = axes.indices[if y <= 0.5 { a } else { b } * axes.lons.len()
                        + if x <= 0.5 { c } else { d }];
                    let mut cell_ok = true;
                    for channel in 0..12 {
                        let q = corners.map(|i| value(channel, i));
                        let bilinear = q[0] * (1.0 - y) * (1.0 - x)
                            + q[1] * (1.0 - y) * x
                            + q[2] * y * (1.0 - x)
                            + q[3] * y * x;
                        let sampled = if bilinear.is_finite() {
                            bilinear
                        } else {
                            value(channel, nearest)
                        };
                        if !sampled.is_finite() || sampled < 0.0 || (channel == 0 && sampled > 10.0)
                        {
                            cell_ok = false;
                            break;
                        }
                    }
                    if cell_ok {
                        covered = true;
                        break 'axes;
                    }
                }
            }
            if !covered {
                return false;
            }
        }
    }
    true
}

pub(crate) fn model_grid_covers(
    path: &Path,
    codes: &EcCodes,
    coverage: &ModelCoverage<'_>,
) -> bool {
    model_grid_covers_group(std::slice::from_ref(&path.to_path_buf()), codes, coverage)
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

pub(crate) fn paths(root: &Path) -> Result<Vec<PathBuf>, String> {
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
    select_with_key(root, area, required, &area_key(area)?, None, &[])
}

pub(crate) fn select_with_key(
    root: &Path,
    area: [f64; 4],
    required: &BTreeSet<DateTime<Utc>>,
    key: &str,
    coverage: Option<&ModelCoverage<'_>>,
    siblings: &[PathBuf],
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
            if !wrap_reuse_allowed(&selection.path, area) {
                continue;
            }
            // A broad dateline-spanning tile's normalized min/max covers any
            // ordinary part, but its true runs may miss the part's arc (e.g.
            // a [-180, 179.6] grid for the [26.15, 180] part). Require the
            // same true-arc contract the desktop preflight applies.
            if !arc_covers(&selection.path, area) {
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
        // The assessment maps the full model grid and fails closed on any
        // uncovered cell. Enforce the same contract here so a newer run whose
        // tile misses the padded model boundary falls through to the next
        // candidate instead of poisoning the whole forecast.
        if let Some(coverage) = coverage {
            let decoder = match &mut codes {
                Some(decoder) => decoder,
                None => codes.insert(EcCodes::load()?),
            };
            let mut group = vec![selection.path.clone()];
            group.extend(siblings.iter().cloned());
            if !model_grid_covers_group(&group, decoder, coverage) {
                continue;
            }
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
    coverage: Option<&ModelCoverage<'_>>,
    siblings: &[PathBuf],
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
            if !wrap_reuse_allowed(&value.path, area) {
                continue;
            }
            // Same true-arc contract as `select_with_key`: a dateline-spanning
            // tile must not silently satisfy an ordinary part it misses.
            if !arc_covers(&value.path, area) {
                continue;
            }
        }
        if let Some(coverage) = coverage {
            let decoder = match &mut codes {
                Some(decoder) => decoder,
                None => codes.insert(EcCodes::load()?),
            };
            let mut group = vec![value.path.clone()];
            group.extend(siblings.iter().cloned());
            if !model_grid_covers_group(&group, decoder, coverage) {
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

    #[test]
    fn wrapped_part_reuse_requires_true_arc_coverage() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join(".cache/cams");
        let west = [78.85, 26.15, -6.85, 180.0];
        let east = [78.85, -180.0, -6.85, -168.15];
        let Ok(key) = area_key(west) else {
            return; // offline checkout without the shared cache
        };
        let target = Utc.with_ymd_and_hms(2026, 11, 14, 3, 0, 0).unwrap();
        let required: BTreeSet<_> = [target].into_iter().collect();
        let Some(hit) = select_with_key(&root, west, &required, &key, None, &[])
            .unwrap()
            .get(&target)
            .cloned()
        else {
            return; // cache not populated on this machine
        };
        // ADS may answer the west part with a broader (even global) grid. The
        // east part may reuse that file only when its true longitude runs
        // cover the east arc; normalized min/max alone must not decide this.
        let runs = longitude_runs(&hit.path).expect("cached file longitudes");
        let covers_east = runs
            .iter()
            .any(|&(a, b)| a - 0.2 <= -180.0 && b + 0.2 >= east[3]);
        assert_eq!(
            arc_covers(&hit.path, [east[0], -180.0, east[2], east[3]]),
            covers_east,
            "arc_covers must follow the true longitude runs"
        );
        // A file whose runs lack the east arc must never be reused for the
        // east part, even though its normalized min/max spans [-180, 180].
        if !covers_east {
            let east_key = area_key(east).unwrap();
            assert!(
                !select_with_key(&root, east, &required, &east_key, None, &[])
                    .unwrap()
                    .get(&target)
                    .is_some_and(|s| s.path == hit.path),
                "east part reused a file without east-arc coverage"
            );
            assert!(
                !nearest_with_key(&root, east, target, &east_key, None, &[])
                    .unwrap()
                    .is_some_and(|s| s.path == hit.path),
                "nearest selector reused a file without east-arc coverage"
            );
        }
        // The west request still resolves from its own cache.
        assert!(select_with_key(&root, west, &required, &key, None, &[])
            .unwrap()
            .contains_key(&target));
    }

    #[test]
    fn wrapped_12z_tiles_cover_their_model_arcs() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join(".cache/cams");
        let west_path =
            root.join("2026-11-13/12Z/cams-optics-v2-2026-11-13-1200-015-689bc94c330f.grib");
        let east_path =
            root.join("2026-11-13/12Z/cams-optics-v2-2026-11-13-1200-015-fdbe4d8cdded.grib");
        if !west_path.exists() || !east_path.exists() {
            return; // cache not populated on this machine
        }
        let codes = EcCodes::load().unwrap();
        // Real East Asia request: wrapped [west, east, north, south].
        let bbox = [26.75_f64, -168.75, 78.25, 26.75];
        let prepared = crate::gfs_data::canonical_bbox(bbox, "0p25").unwrap();
        let (lats, lons) = model_axes(prepared);
        let west_part: Vec<f64> = lons.iter().copied().filter(|&l| l >= 26.0).collect();
        let east_part: Vec<f64> = lons.iter().copied().filter(|&l| l <= -168.0).collect();
        assert!(west_part.len() > 2 && east_part.len() > 2);
        // The west tile stops at 179.6 but carries the physical -180 column,
        // which is the same meridian as +180; its own seam copy closes the
        // (179.6, 180] gap for the western arc.
        assert!(
            model_grid_covers(
                &west_path,
                &codes,
                &ModelCoverage {
                    lats: &lats,
                    lons: &west_part
                }
            ),
            "west tile covers its arc through its own boundary column"
        );
        // With the sibling east tile the shared -180/+180 boundary column
        // bridges the gap, exactly like the scorer's merged grid.
        let group = vec![west_path.clone(), east_path.clone()];
        assert!(
            model_grid_covers_group(
                &group,
                &codes,
                &ModelCoverage {
                    lats: &lats,
                    lons: &west_part
                }
            ),
            "wrapped group must cover the western arc through the seam bridge"
        );
        assert!(
            model_grid_covers_group(
                &group,
                &codes,
                &ModelCoverage {
                    lats: &lats,
                    lons: &east_part
                }
            ),
            "wrapped group must cover the eastern arc from the -180 seam column"
        );
    }

    #[test]
    fn model_grid_coverage_rejects_wrapped_group_missing_west_part() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join(".cache/cams");
        if !root.exists() {
            return; // offline checkout without the shared cache
        }
        // East Asia model grid: the eastern edge (-6.25) lies beyond the
        // newer run's CAMS tile edge (-6.8) but inside the older tile (-6.4).
        // Same wrapped East Asia grid the desktop plan uses: the canonical
        // union keeps the +180 seam column on the western arc.
        let prepared =
            crate::gfs_data::canonical_bbox([26.75, -168.75, 78.25, 26.75], "0p25").unwrap();
        let (lats, lons) = model_axes(prepared);
        let west_part: Vec<f64> = lons.iter().copied().filter(|&l| l >= 26.0).collect();
        let full = ModelCoverage {
            lats: &lats,
            lons: &lons,
        };
        let west_coverage = ModelCoverage {
            lats: &lats,
            lons: &west_part,
        };
        let codes = EcCodes::load().unwrap();
        let west = root.join("2026-11-13/00Z/cams-optics-v2-2026-11-13-0000-027-689bc94c330f.grib");
        let east = root.join("2026-11-13/00Z/cams-optics-v2-2026-11-13-0000-027-fdbe4d8cdded.grib");
        if !west.exists() || !east.exists() {
            return; // cache not populated on this machine
        }
        // A wrapped group that dropped its west part (the historical
        // desktop_prepare grouping bug) leaves the whole western arc
        // uncovered and must be rejected by the coverage contract.
        assert!(
            !model_grid_covers(&east, &codes, &full),
            "east-only group leaves the western arc uncovered"
        );
        assert!(
            !model_grid_covers(&west, &codes, &full),
            "west-only group leaves the eastern arc uncovered"
        );
        // The west tile covers its own arc once the sibling supplies the
        // shared boundary column; the full group covers both arcs.
        let group = vec![west.clone(), east.clone()];
        assert!(
            model_grid_covers_group(&group, &codes, &west_coverage),
            "west arc covered through the seam bridge"
        );
        assert!(
            model_grid_covers_group(&group, &codes, &full),
            "complete wrapped group covers the whole model grid"
        );
    }

    #[test]
    fn wrapped_request_detection_uses_arc_direction() {
        // Ordinary intervals (including the east strip east of the dateline)
        // read west < east; only a true crossing reads west > east.
        assert!(!wrapped_request([78.85, 26.15, -6.85, 180.0]));
        assert!(!wrapped_request([78.85, -180.0, -6.85, -168.15]));
        assert!(!wrapped_request([10.0, -10.0, 0.0, 10.0]));
        assert!(wrapped_request([78.85, 26.15, -6.85, -168.15]));
        assert!(wrapped_request([10.0, 170.0, 0.0, -170.0]));
    }
}
