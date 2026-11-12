//! Offline raw-model -> CAMS -> regional ray-trace assessment.
//! This preserves the complete native assessment tensor, not the public
//! forecast/event-window/diagnostic JSON response.
use crate::tensor_bundle::{
    source_identity, BundleWriter, ProfileTensor, SourceIdentity, MAX_BUNDLE_BYTES,
};
use crate::time_contract::{parse_date, parse_utc};
use crate::{astronomy::Phenomenon, cams, ecmwf, grib::EcCodes, request_file};
use chrono::{DateTime, Duration, Timelike, Utc};
use firesky_native::cams::{Mapped, LEVELS};
use firesky_native::{derivation, gfs, timeline, NATIONAL_OUTPUT_FIELDS};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::time::Instant;

#[derive(Clone, Copy, Deserialize, Serialize)]
#[serde(rename_all = "kebab-case")]
pub(crate) enum Model {
    Gfs,
    EcmwfBasic,
}

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Observer {
    pub name: Option<String>,
    pub lat: f64,
    pub lon: f64,
    pub timezone_name: String,
    pub elevation_source: Elevation,
    pub elevation_m: Option<f64>,
}
#[derive(Clone, Copy, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub(crate) enum Elevation {
    Observer,
    ModelGrid,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Frame {
    pub hour: u32,
    pub path: PathBuf,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct OpticalFile {
    pub valid_time: String,
    pub path: PathBuf,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub paths: Vec<PathBuf>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Input {
    pub model: Model,
    pub cycle_utc: String,
    pub date: String,
    pub phenomenon: Phenomenon,
    pub points: Vec<Observer>,
    pub frames: Vec<Frame>,
    pub cams: Vec<OpticalFile>,
    pub radius_km: f64,
    pub bbox: Option<[f64; 4]>,
    #[serde(default)]
    pub include_point_core: bool,
    pub point_spacing_deg: Option<f64>,
    pub compute_backend_requested: Option<String>,
    #[serde(default)]
    pub derived_cache: Option<crate::derived_cache::Config>,
}

#[derive(Serialize)]
struct Source {
    path: PathBuf,
    role: String,
    identity: SourceIdentity,
}
impl Source {
    fn capture(path: PathBuf, role: String) -> Result<Self, String> {
        let metadata = std::fs::symlink_metadata(&path).map_err(|e| e.to_string())?;
        if !metadata.file_type().is_file() || metadata.len() > 512 * 1024 * 1024 {
            return Err("assessment source must be a regular nonsymlink file <=512 MiB".into());
        }
        let identity = source_identity(&path)?;
        Ok(Self {
            path,
            role,
            identity,
        })
    }
    fn revalidate(&self) -> Result<(), String> {
        let metadata = std::fs::symlink_metadata(&self.path).map_err(|e| e.to_string())?;
        if !metadata.file_type().is_file() || source_identity(&self.path)? != self.identity {
            return Err("assessment source changed during execution".into());
        }
        Ok(())
    }
}

/// Committed children are provisional until the parent manifest is committed.
/// Delete only our known files; preserve unexpected concurrent additions.
#[derive(Default)]
struct Children {
    owned: Vec<PathBuf>,
    root: Option<PathBuf>,
    retained: bool,
}
impl Drop for Children {
    fn drop(&mut self) {
        if !self.retained {
            for root in self.owned.iter().rev() {
                let _ = std::fs::remove_file(root.join("profiles.npy"));
                let _ = std::fs::remove_file(root.join("manifest.json"));
                let _ = std::fs::remove_dir(root);
            }
            if let Some(root) = &self.root {
                let _ = std::fs::remove_dir(root);
            }
        }
    }
}

#[derive(Clone, Serialize)]
pub(crate) struct Geometry {
    rows: usize,
    cols: usize,
    lat0: f32,
    lon0: f32,
    spacing: f32,
    #[serde(skip)]
    lats: Vec<f32>,
    #[serde(skip)]
    lons: Vec<f32>,
}
impl Geometry {
    pub(crate) fn canonicalize(grid: &mut gfs::GfsGrid) -> Result<Self, String> {
        let unique = |values: &[f32]| {
            let mut axis = values.to_vec();
            axis.sort_by(f32::total_cmp);
            axis.dedup_by(|a, b| *a == *b);
            axis
        };
        if grid
            .latitudes
            .iter()
            .chain(&grid.longitudes)
            .any(|v| !v.is_finite())
        {
            return Err("nonfinite regional coordinates".into());
        }
        let lats = unique(&grid.latitudes);
        let mut lons = unique(&grid.longitudes);
        let cells = grid.latitudes.len();
        if cells == 0 || cells > derivation::MAX_CELLS || lats.len() * lons.len() != cells {
            return Err("regional assessment requires a complete rectangular grid".into());
        }
        let spacing = lats
            .windows(2)
            .next()
            .map(|p| p[1] - p[0])
            .or_else(|| {
                lons.windows(2)
                    .map(|p| p[1] - p[0])
                    .chain((lons.len() > 1).then(|| 360.0 + lons[0] - lons[lons.len() - 1]))
                    .filter(|gap| *gap > 0.0)
                    .min_by(f32::total_cmp)
            })
            .ok_or("cannot infer regional grid spacing")?;
        let regular = |axis: &[f32]| {
            axis.windows(2)
                .all(|p| ((p[1] - p[0]) - spacing).abs() <= 1e-5)
        };
        if !regular(&lons) && lons.len() > 1 {
            // A contiguous circular axis has one omitted arc and no other
            // gaps. Rotate only that seam; arbitrary sparse axes stay invalid.
            let breaks: Vec<_> = (0..lons.len())
                .filter(|&i| {
                    let next = if i + 1 == lons.len() {
                        lons[0] + 360.0
                    } else {
                        lons[i + 1]
                    };
                    ((next - lons[i]) - spacing).abs() > 1e-5
                })
                .collect();
            if breaks.len() == 1 && breaks[0] + 1 < lons.len() {
                let seam = breaks[0] + 1;
                lons = lons[seam..]
                    .iter()
                    .copied()
                    .chain(lons[..seam].iter().map(|lon| lon + 360.0))
                    .collect();
            }
        }
        if !spacing.is_finite() || spacing <= 0.0 || !regular(&lats) || !regular(&lons) {
            return Err(
                "regional assessment requires uniform common latitude/longitude spacing".into(),
            );
        }
        let end = *lons.last().unwrap();
        let unwrap = lons[0] < -180.0
            || end > 180.0
            || (end == 180.0 && lons[0] > -180.0);
        let center = (lons[0] + lons[lons.len() - 1]) * 0.5;
        let mapped_lons: Vec<_> = grid
            .longitudes
            .iter()
            .map(|&lon| {
                if unwrap {
                    lon + 360.0 * ((center - lon) / 360.0).round()
                } else {
                    lon
                }
            })
            .collect();
        let mut order = vec![usize::MAX; cells];
        for (i, (&lat, &lon)) in grid.latitudes.iter().zip(&mapped_lons).enumerate() {
            let r = lats.partition_point(|v| *v < lat);
            let c = lons.partition_point(|v| *v < lon);
            let target = r * lons.len() + c;
            if order[target] != usize::MAX {
                return Err("duplicate regional coordinate".into());
            }
            order[target] = i;
        }
        if order.contains(&usize::MAX) {
            return Err("regional grid has coordinate holes".into());
        }
        if order.iter().enumerate().any(|(i, v)| i != *v) {
            grid.latitudes = order.iter().map(|&i| grid.latitudes[i]).collect();
            for values in grid.fields.values_mut() {
                *values = order.iter().map(|&i| values[i]).collect();
            }
        }
        if unwrap || order.iter().enumerate().any(|(i, v)| i != *v) {
            grid.longitudes = order.iter().map(|&i| mapped_lons[i]).collect();
        }
        Ok(Self {
            rows: lats.len(),
            cols: lons.len(),
            lat0: lats[0],
            lon0: lons[0],
            spacing,
            lats,
            lons,
        })
    }
    fn longitude(&self, lon: f64) -> f64 {
        let west = f64::from(self.lons[0]);
        let east = f64::from(*self.lons.last().unwrap());
        if west < -180.0 || east > 180.0 || (east == 180.0 && west > -180.0) {
            lon + 360.0 * (((west + east) * 0.5 - lon) / 360.0).round()
        } else if lon == 180.0 && west == -180.0 && east < 180.0 {
            -180.0
        } else {
            lon
        }
    }
    fn inside(&self, lat: f64, lon: f64) -> bool {
        let lon = self.longitude(lon);
        lat >= f64::from(self.lats[0]) - 1e-6
            && lat <= f64::from(*self.lats.last().unwrap()) + 1e-6
            && lon >= f64::from(self.lons[0]) - 1e-6
            && lon <= f64::from(*self.lons.last().unwrap()) + 1e-6
    }
    fn terrain(&self, values: &[f32], lat: f64, lon: f64) -> f64 {
        let bound = |axis: &[f32], x: f64| {
            let b = axis
                .partition_point(|v| f64::from(*v) < x)
                .min(axis.len() - 1);
            let a = b.saturating_sub(1);
            let d = f64::from(axis[b]) - f64::from(axis[a]);
            (
                a,
                b,
                if d > 0.0 {
                    ((x - f64::from(axis[a])) / d).clamp(0.0, 1.0)
                } else {
                    0.0
                },
            )
        };
        let (a, b, y) = bound(&self.lats, lat);
        let (c, d, x) = bound(
            &self.lons,
            self.longitude((lon + 180.0).rem_euclid(360.0) - 180.0),
        );
        let q = |r, c| f64::from(values[r * self.cols + c]);
        q(a, c) * (1.0 - y) * (1.0 - x)
            + q(a, d) * (1.0 - y) * x
            + q(b, c) * y * (1.0 - x)
            + q(b, d) * y * x
    }
    fn stencil(&self, observer: [f32; 3]) -> ([usize; 4], [f32; 4]) {
        let r = (observer[0] - self.lat0) / self.spacing;
        let c = (self.longitude(f64::from(observer[1])) as f32 - self.lon0) / self.spacing;
        let a = r.floor().clamp(0.0, (self.rows - 1) as f32) as usize;
        let b = c.floor().clamp(0.0, (self.cols - 1) as f32) as usize;
        let d = (a + 1).min(self.rows - 1);
        let e = (b + 1).min(self.cols - 1);
        let y = (r - a as f32).clamp(0.0, 1.0);
        let x = (c - b as f32).clamp(0.0, 1.0);
        (
            [
                a * self.cols + b,
                a * self.cols + e,
                d * self.cols + b,
                d * self.cols + e,
            ],
            [(1.0 - y) * (1.0 - x), (1.0 - y) * x, y * (1.0 - x), y * x],
        )
    }
}

fn validate_gfs(grid: &gfs::GfsGrid, terrain: &[f32]) -> Result<(), String> {
    for level in gfs::PRESSURE_LEVELS {
        let get = |name: &str| {
            grid.fields
                .get(&("isobaricInhPa".into(), level, name.into()))
        };
        let height = get("gh");
        let a = get("tcc");
        let b = get("tcdc");
        let liquid = get("clwmr");
        let ice = get("icmr");
        let nominal = 44330.0 * (1.0 - (f64::from(level) / 1013.25).powf(0.190263));
        for (i, &surface) in terrain.iter().enumerate() {
            if f64::from(surface) >= nominal - 500.0 {
                continue;
            }
            if !height.is_some_and(|v| v[i].is_finite()) {
                return Err(format!(
                    "GFS national input missing geometry at {level} hPa"
                ));
            }
            let amount = a.is_some_and(|v| v[i].is_finite()) || b.is_some_and(|v| v[i].is_finite());
            let phase = match (liquid, ice) {
                (Some(l), Some(r)) => {
                    l[i].is_finite() && r[i].is_finite() && (l[i] > 0.0 || r[i] > 0.0)
                }
                _ => false,
            };
            if !(amount || phase) {
                return Err(format!(
                    "GFS national input missing cloud evidence at {level} hPa"
                ));
            }
        }
    }
    Ok(())
}

fn resolve(base: &Path, path: &Path) -> PathBuf {
    if path.is_absolute() {
        path.to_owned()
    } else {
        base.join(path)
    }
}

fn subset_gfs(grid: &mut gfs::GfsGrid, bbox: Option<[f64; 4]>) -> Result<(), String> {
    let Some([west, east, north, south]) = bbox else {
        return Ok(());
    };
    let indices: Vec<_> = grid
        .latitudes
        .iter()
        .zip(&grid.longitudes)
        .enumerate()
        .filter_map(|(i, (&lat, &lon))| {
            (lat <= north as f32
                && lat >= south as f32
                && if west <= east {
                    lon >= west as f32 && lon <= east as f32
                } else {
                    lon >= west as f32 || lon <= east as f32
                })
            .then_some(i)
        })
        .collect();
    if indices.is_empty() {
        return Err("GFS stored bbox has no cells".into());
    }
    grid.latitudes = indices.iter().map(|&i| grid.latitudes[i]).collect();
    grid.longitudes = indices.iter().map(|&i| grid.longitudes[i]).collect();
    for values in grid.fields.values_mut() {
        *values = indices.iter().map(|&i| values[i]).collect();
    }
    Ok(())
}

pub(crate) fn terrain_at(
    codes: &EcCodes,
    path: &Path,
    cycle: DateTime<Utc>,
    hour: u32,
    bbox: [f64; 4],
    points: &[Observer],
) -> Result<Vec<f64>, String> {
    let before = Source::capture(path.to_owned(), "terrain-prepass".into())?;
    let mut grid =
        codes.decode_gfs_frame(path, 4096, derivation::MAX_CELLS, Some((cycle, hour)))?;
    subset_gfs(&mut grid, Some(bbox))?;
    let geometry = Geometry::canonicalize(&mut grid)?;
    let surface = grid
        .fields
        .get(&("surface".into(), 0, "orog".into()))
        .ok_or("missing model orography")?;
    let values = points
        .iter()
        .map(|point| {
            if !geometry.inside(point.lat, point.lon) {
                return Err("terrain observer is outside the GFS domain".to_string());
            }
            let value = geometry.terrain(surface, point.lat, point.lon);
            if !value.is_finite() || !(-500.0..=9000.0).contains(&value) {
                return Err("invalid sampled GFS terrain".to_string());
            }
            Ok(value)
        })
        .collect::<Result<Vec<_>, _>>()?;
    before.revalidate()?;
    Ok(values)
}
fn epoch(time: DateTime<Utc>) -> f64 {
    time.timestamp() as f64 + f64::from(time.timestamp_subsec_micros()) / 1e6
}
pub(crate) fn from_epoch(value: f64) -> Result<DateTime<Utc>, String> {
    let seconds = value.floor();
    let micros = ((value - seconds) * 1e6).round_ties_even() as i64;
    DateTime::from_timestamp(
        seconds as i64 + micros / 1_000_000,
        (micros % 1_000_000) as u32 * 1000,
    )
    .ok_or("anchor epoch exceeds calendar".into())
}
fn bracket(time: DateTime<Utc>) -> Result<(DateTime<Utc>, DateTime<Utc>, f32), String> {
    cams::bracket(&time.to_rfc3339())
}

fn expand_steps(result: &mut [f32], points: usize, start: usize, stop: usize) {
    let span = stop - start;
    // Expand backwards so no unprocessed compact point is overwritten.
    for point in (0..points).rev() {
        let base = point * timeline::STEPS * NATIONAL_OUTPUT_FIELDS;
        let dest = base + start * NATIONAL_OUTPUT_FIELDS;
        let source = point * span * NATIONAL_OUTPUT_FIELDS;
        result.copy_within(source..source + span * NATIONAL_OUTPUT_FIELDS, dest);
        result[base..dest].fill(0.0);
        result
            [dest + span * NATIONAL_OUTPUT_FIELDS..base + timeline::STEPS * NATIONAL_OUTPUT_FIELDS]
            .fill(0.0);
    }
}

struct OpticalCatalog {
    files: BTreeMap<DateTime<Utc>, Vec<PathBuf>>,
    mapped: BTreeMap<DateTime<Utc>, Mapped>,
}
impl OpticalCatalog {
    fn load(
        &mut self,
        time: DateTime<Utc>,
        codes: &EcCodes,
        lats: &[f64],
        lons: &[f64],
        sources: &mut Vec<Source>,
    ) -> Result<(), String> {
        if self.mapped.contains_key(&time) {
            return Ok(());
        }
        let paths = self
            .files
            .get(&time)
            .ok_or_else(|| format!("missing frozen CAMS bracket at {time}"))?;
        let mut loaded_sources = Vec::with_capacity(paths.len());
        for (index, path) in paths.iter().enumerate() {
            loaded_sources.push(Source::capture(
                path.clone(),
                format!("cams:{time}:{index}"),
            )?);
        }
        let optics = cams::decode_many(codes, paths)?;
        if optics.valid_time != time {
            return Err("CAMS actual valid time differs from stored catalog".into());
        }
        let mapped = firesky_native::cams::map(&optics.grid(), lats, lons)?;
        if mapped.covered.iter().any(|v| !v) {
            return Err("CAMS full profile does not cover every model cell".into());
        }
        for source in &loaded_sources {
            source.revalidate()?;
        }
        // Keep the original order stable so source provenance remains
        // deterministic for ordinary single-area catalogs and wrapped
        // two-area catalogs alike.
        sources.extend(loaded_sources);
        self.mapped.insert(time, mapped);
        Ok(())
    }
}

pub fn run_to_files(path: &Path, output: &Path) -> Result<PathBuf, String> {
    run(path, output, Output::Assessment)
}

pub fn score_to_files(path: &Path, output: &Path) -> Result<PathBuf, String> {
    run(path, output, Output::Quality)
}

pub fn diagnose_to_files(path: &Path, output: &Path) -> Result<PathBuf, String> {
    run(path, output, Output::Diagnostics)
}
pub fn diagnose_profiles_to_files(path: &Path, output: &Path) -> Result<PathBuf, String> {
    run(path, output, Output::ProfileDiagnostics)
}
pub fn frames_to_files(path: &Path, output: &Path) -> Result<PathBuf, String> {
    run(path, output, Output::Frames)
}
pub fn point_to_files(path: &Path, output: &Path) -> Result<PathBuf, String> {
    run(path, output, Output::Point)
}
enum Output {
    Assessment,
    Quality,
    Diagnostics,
    ProfileDiagnostics,
    Frames,
    Point,
    Service,
}

fn run(path: &Path, output: &Path, level: Output) -> Result<PathBuf, String> {
    let started = Instant::now();
    let (input, source): (Input, _) = request_file::read(path, 64 * 1024 * 1024)?;
    run_input(path, output, level, input, source, None, started)
}

pub(crate) fn planned_point_to_files(
    path: &Path,
    output: &Path,
    input: Input,
    source: SourceIdentity,
    plan: serde_json::Value,
    started: Instant,
) -> Result<PathBuf, String> {
    run_input(
        path,
        output,
        Output::Point,
        input,
        source,
        Some(plan),
        started,
    )
}

pub(crate) fn planned_service_to_files(
    path: &Path,
    output: &Path,
    input: Input,
    source: SourceIdentity,
    plan: serde_json::Value,
    started: Instant,
) -> Result<PathBuf, String> {
    run_input(
        path,
        output,
        Output::Service,
        input,
        source,
        Some(plan),
        started,
    )
}

#[allow(clippy::too_many_arguments)]
fn prepare_frame(
    codes: &EcCodes,
    path: &Path,
    input: &Input,
    hour: u32,
    cycle: DateTime<Utc>,
    decode_ms: &mut f64,
    derive_ms: &mut f64,
) -> Result<crate::derived_cache::Data, String> {
    let start = Instant::now();
    let mut ec_proxy_coverage = None;
    let mut ec_native_grid_shape = None;
    let mut decoded = match input.model {
        Model::Gfs => {
            if hour > 384 {
                return Err("GFS forecast hour exceeds 384".into());
            }
            codes.decode_gfs_frame(path, 4096, derivation::MAX_CELLS, Some((cycle, hour)))?
        }
        Model::EcmwfBasic => {
            let raw = ecmwf::decode_basic(
                codes,
                path,
                input.bbox,
                Some(&input.cycle_utc),
                Some(i64::from(hour)),
                4096,
                5_000_000,
            )?;
            ec_native_grid_shape = Some(
                serde_json::from_value(raw.metadata()["native_grid_shape"].clone())
                    .map_err(|e| e.to_string())?,
            );
            let (grid, coverage) = raw.native_grid()?;
            ec_proxy_coverage = Some(coverage);
            grid
        }
    };
    *decode_ms += start.elapsed().as_secs_f64() * 1000.0;
    let start = Instant::now();
    if matches!(input.model, Model::Gfs) {
        subset_gfs(&mut decoded, input.bbox)?;
    }
    Geometry::canonicalize(&mut decoded)?;
    let terrain = decoded
        .fields
        .get(&("surface".into(), 0, "orog".into()))
        .ok_or("missing model orography")?;
    if terrain
        .iter()
        .any(|v| !v.is_finite() || !(-500.0..=9000.0).contains(v))
    {
        return Err("invalid model orography".into());
    }
    if matches!(input.model, Model::Gfs) {
        validate_gfs(&decoded, terrain)?;
    }
    let mut average = false;
    let mut instantaneous = true;
    for family in ["high", "middle", "low"] {
        for boundary in ["Bottom", "Top"] {
            let kind = format!("{family}Cloud{boundary}");
            if !decoded
                .fields
                .contains_key(&(kind.clone(), 0, "pres".into()))
            {
                if decoded.fields.contains_key(&(kind, 0, "avg_pres".into())) {
                    average = true;
                }
                instantaneous = false;
            }
        }
    }
    let boundary_semantics = if average {
        "average-envelope-not-instantaneous"
    } else if instantaneous {
        "instantaneous"
    } else {
        "unknown"
    }
    .to_owned();
    let raw = derivation::RawGrid::from_gfs(&decoded)?;
    let derived = derivation::derive(&raw)?;
    *derive_ms += start.elapsed().as_secs_f64() * 1000.0;
    Ok(crate::derived_cache::Data {
        latitudes: decoded.latitudes,
        longitudes: decoded.longitudes,
        surface: terrain.clone(),
        derived,
        boundary_semantics,
        ec_proxy_coverage,
        ec_native_grid_shape,
    })
}

fn run_input(
    path: &Path,
    output: &Path,
    level: Output,
    mut input: Input,
    request_source: SourceIdentity,
    plan: Option<serde_json::Value>,
    started: Instant,
) -> Result<PathBuf, String> {
    let with_quality = !matches!(level, Output::Assessment);
    let with_diagnostics = matches!(
        level,
        Output::Diagnostics
            | Output::ProfileDiagnostics
            | Output::Frames
            | Output::Point
            | Output::Service
    );
    let with_profiles = matches!(
        level,
        Output::ProfileDiagnostics | Output::Frames | Output::Point | Output::Service
    );
    let with_frames = matches!(level, Output::Frames | Output::Point | Output::Service);
    let with_point = matches!(level, Output::Point | Output::Service);
    let with_service = matches!(level, Output::Service);
    if with_service && (!matches!(input.model, Model::EcmwfBasic) || plan.is_none()) {
        return Err("EC service requires independent EC input planning".into());
    }
    if with_point {
        if input.points.len() != 1 {
            return Err("stored point forecast requires exactly one named observer".into());
        }
        input.include_point_core = true;
    }
    if input.points.is_empty()
        || input.points.len() > timeline::MAX_POINTS
        || input.frames.is_empty()
        || input.frames.len() > 64
        || input.cams.is_empty()
        || input.cams.len() > 256
        || !input.radius_km.is_finite()
        || input.radius_km <= 0.0
        || input.radius_km > 20000.0
        || input.frames.windows(2).any(|p| p[0].hour >= p[1].hour)
    {
        return Err("invalid assessment points, frames, CAMS catalog, radius or ordering".into());
    }
    if with_frames
        && (input.include_point_core && input.points.len() != 1
            || input.points.iter().any(|p| {
                p.name
                    .as_ref()
                    .is_none_or(|n| n.is_empty() || n.len() > 256)
            })
            || input
                .point_spacing_deg
                .is_some_and(|v| !v.is_finite() || v <= 0.0))
    {
        return Err(
            "frame output requires named observers, valid spacing and at most one point core"
                .into(),
        );
    }
    let cycle = ecmwf::expected_cycle(&input.cycle_utc)?;
    if input.bbox.is_some_and(|b| {
        b.iter().any(|v| !v.is_finite())
            || !(-180.0..=180.0).contains(&b[0])
            || !(-180.0..=180.0).contains(&b[1])
            || !(-90.0..=90.0).contains(&b[2])
            || !(-90.0..=90.0).contains(&b[3])
            || b[2] < b[3]
    }) {
        return Err("invalid assessment bbox [west,east,north,south]".into());
    }
    let date = parse_date(&input.date)?;
    let rising = matches!(input.phenomenon, Phenomenon::Dawn);
    let base = path.parent().unwrap_or_else(|| Path::new("."));
    let mut catalog = OpticalCatalog {
        files: BTreeMap::new(),
        mapped: BTreeMap::new(),
    };
    for file in &input.cams {
        let time = parse_utc(&file.valid_time)?;
        if time.minute() != 0
            || time.second() != 0
            || time.timestamp_subsec_micros() != 0
            || time.hour() % 3 != 0
            || catalog
                .files
                .insert(
                    time,
                    if file.paths.is_empty() {
                        vec![resolve(base, &file.path)]
                    } else {
                        let mut paths = Vec::with_capacity(file.paths.len() + 1);
                        paths.push(resolve(base, &file.path));
                        for path in &file.paths {
                            let path = resolve(base, path);
                            if !paths.contains(&path) {
                                paths.push(path);
                            }
                        }
                        paths
                    },
                )
                .is_some()
        {
            return Err("CAMS frozen catalog requires unique exact three-hour valid times".into());
        }
    }
    let hours: Vec<_> = input.frames.iter().map(|f| f64::from(f.hour)).collect();
    let mut sources = Vec::new();
    let codes = EcCodes::load()?;
    let mut geometry: Option<Geometry> = None;
    let mut latitudes = Vec::new();
    let mut longitudes = Vec::new();
    let mut surface = Vec::new();
    let mut levels = Vec::new();
    let mut grid = Vec::new();
    let mut profiles = Vec::new();
    let mut profile_sources = Vec::new();
    let mut boundary_semantics = "unknown".to_owned();
    let mut proxy_coverage = Vec::new();
    let mut decode_ms = 0.0;
    let mut derive_ms = 0.0;
    let mut profile_write_ms = 0.0;
    let mut ec_source_metadata = Vec::new();
    let mut derived_cache =
        crate::derived_cache::Session::new(input.derived_cache.clone(), base, &codes)?;
    let mut writer = BundleWriter::create(
        output,
        if with_service {
            "ecmwf-service-forecast-v1"
        } else if with_point {
            "point-forecast-v1"
        } else if with_frames {
            "regional-frames-v1"
        } else if with_profiles {
            "regional-profile-diagnostics-v1"
        } else if with_diagnostics {
            "regional-diagnostics-v1"
        } else if with_quality {
            "regional-quality-v1"
        } else {
            "regional-assessment-v1"
        },
        request_source.clone(),
        json!({}),
    )?;
    let mut children = Children {
        root: Some(output.to_owned()),
        owned: Vec::new(),
        retained: false,
    };
    let planning_artifact = plan
        .as_ref()
        .map(|plan| {
            writer.json_artifact("input_plan", plan).map(|identity| {
                json!({
                    "file":"input_plan.json","identity":identity,"contract":plan["contract"]
                })
            })
        })
        .transpose()?;
    for (index, frame) in input.frames.iter().enumerate() {
        let phase = Instant::now();
        let source = Source::capture(
            resolve(base, &frame.path),
            format!("model:f{:03}", frame.hour),
        )?;
        decode_ms += phase.elapsed().as_secs_f64() * 1000.0;
        let data = derived_cache.frame(
            &source.path,
            &source.identity,
            input.model,
            &crate::time_contract::iso(cycle),
            frame.hour,
            input.bbox,
            || {
                prepare_frame(
                    &codes,
                    &source.path,
                    &input,
                    frame.hour,
                    cycle,
                    &mut decode_ms,
                    &mut derive_ms,
                )
            },
        )?;
        let mut coordinates = gfs::GfsGrid {
            latitudes: data.latitudes.clone(),
            longitudes: data.longitudes.clone(),
            fields: Default::default(),
        };
        let geom = Geometry::canonicalize(&mut coordinates)?;
        if let Some(coverage) = data.ec_proxy_coverage {
            proxy_coverage.push(coverage);
        }
        if with_service {
            ec_source_metadata.push(json!({
                "file":source.path.canonicalize().map_err(|e|e.to_string())?,
                "valid_time_utc":(cycle+chrono::Duration::hours(i64::from(frame.hour))).to_rfc3339_opts(chrono::SecondsFormat::Secs,true),
                "forecast_hour":frame.hour,"native_grid_shape":data.ec_native_grid_shape,
                "selected_cell_count":data.latitudes.len(),"grid_spacing_deg":f64::from(geom.spacing),
                "surface_geopotential_units":"m2 s-2","terrain_units":"m","tcc_units":"%",
                "model_level_cloud_fields":"unavailable_in_public_basic_contract",
            }));
        }
        let terrain = &data.surface;
        if index == 0 {
            geometry = Some(geom);
            latitudes = data.latitudes.clone();
            longitudes = data.longitudes.clone();
            surface = terrain.clone();
            let cells = latitudes.len() as u128;
            let observers = input.points.len() as u128;
            // Profiles live in independently bounded children; no temporal
            // chunk is dropped merely to fit the main tensor-bundle limit.
            let main_bytes = cells * (input.frames.len() as u128) * (36 + 11) * 4
                + observers
                    * (7884
                        + if with_quality { 3640 } else { 0 }
                        + if with_diagnostics { 160 } else { 0 })
                + if with_profiles {
                    observers * 20 * 7 * 4
                } else {
                    0
                }
                + cells * 8
                + 65536;
            if main_bytes > u128::from(MAX_BUNDLE_BYTES) {
                return Err("assessment main bundle exceeds bounded output size".into());
            }
        } else if latitudes.len() != data.latitudes.len()
            || longitudes.len() != data.longitudes.len()
            || latitudes
                .iter()
                .zip(&data.latitudes)
                .chain(longitudes.iter().zip(&data.longitudes))
                .any(|(a, b)| {
                    (a - b).abs()
                        > if matches!(input.model, Model::EcmwfBasic) {
                            1e-6
                        } else {
                            0.0
                        }
                })
            || surface.iter().zip(terrain).any(|(a, b)| {
                (a - b).abs()
                    > if matches!(input.model, Model::EcmwfBasic) {
                        2.0
                    } else {
                        1e-4
                    }
            })
        {
            return Err("model frames do not share coordinates and surface orography".into());
        }
        if index == 0 && with_profiles {
            boundary_semantics = data.boundary_semantics;
        }
        let derived = data.derived;
        if index == 0 {
            levels = derived.levels.clone();
        } else if levels != derived.levels {
            return Err("model frames do not share pressure levels".into());
        }
        let phase = Instant::now();
        let child = output.join(format!("profiles_{index:03}"));
        let mut profile = BundleWriter::create(
            &child,
            "regional-profiles-v1",
            source.identity.clone(),
            json!({"frame_index":index,"hour":frame.hour,"pressure_levels":levels,"profile_fields":24}),
        )?;
        profile.f32(
            "profiles",
            &[latitudes.len() as u64, levels.len() as u64, 24],
            derived.profiles.iter().flatten().copied(),
        )?;
        source.revalidate()?;
        let manifest = profile.commit(&source.path)?;
        children.owned.push(child);
        let identity = source_identity(&manifest)?;
        if with_profiles {
            profile_sources.push(ProfileTensor::open(
                &manifest,
                identity.clone(),
                latitudes.len(),
                levels.len(),
            )?);
        }
        profiles.push(
            json!({"manifest":format!("profiles_{index:03}/manifest.json"),"identity":identity}),
        );
        profile_write_ms += phase.elapsed().as_secs_f64() * 1000.0;
        grid.extend(derived.columns);
        sources.push(source);
    }
    let geom = geometry.unwrap();
    let cells = latitudes.len();
    let points = input
        .points
        .iter()
        .map(|p| {
            if !p.lat.is_finite() || !p.lon.is_finite() || !geom.inside(p.lat, p.lon) {
                return Err("observer is outside the prepared model profile domain".to_string());
            }
            let elevation_m =
                match (p.elevation_source, p.elevation_m) {
                    (Elevation::Observer, Some(v)) => v,
                    (Elevation::ModelGrid, None) => geom.terrain(&surface, p.lat, p.lon),
                    _ => return Err(
                        "observer elevation must be explicit; model-grid elevation must be derived"
                            .into(),
                    ),
                };
            if p.timezone_name.len() > 128 {
                return Err("timezone name exceeds limit".into());
            }
            Ok(timeline::Point {
                lat: p.lat,
                lon: p.lon,
                elevation_m,
                timezone: p
                    .timezone_name
                    .parse()
                    .map_err(|_| "unknown observer timezone")?,
            })
        })
        .collect::<Result<Vec<_>, String>>()?;
    let mut solar = timeline::prepare(
        &points,
        date,
        rising,
        Some(timeline::ModelTimes {
            cycle_epoch: epoch(cycle),
            hours: &hours,
        }),
    )
    .map_err(|e| e.to_string())?;
    let model_ms = started.elapsed().as_secs_f64() * 1000.0;
    let samples = solar.samples.as_mut().unwrap();
    let mut time_indices = BTreeSet::new();
    let mut step_start = timeline::STEPS;
    let mut step_stop = 0;
    for row in samples.iter() {
        for (step, sample) in row.as_chunks::<9>().0.iter().enumerate() {
            if sample[6] >= 0.5 {
                time_indices.insert(sample[2] as usize);
                time_indices.insert(sample[3] as usize);
                step_start = step_start.min(step);
                step_stop = step_stop.max(step + 1);
            }
        }
    }
    if step_stop == 0 {
        return Err("assessment requires at least one valid solar sample".into());
    }
    let lats: Vec<_> = latitudes.iter().copied().map(f64::from).collect();
    let lons: Vec<_> = longitudes.iter().copied().map(f64::from).collect();
    let mut aerosol = vec![[0.0; 11]; input.frames.len() * cells];
    for row in &mut grid {
        row[25] = 0.0;
        row[26] = f32::NAN;
    }
    for index in time_indices {
        let target = cycle
            .checked_add_signed(Duration::hours(i64::from(input.frames[index].hour)))
            .ok_or("forecast time overflow")?;
        let (left, right, w) = bracket(target)?;
        catalog.load(left, &codes, &lats, &lons, &mut sources)?;
        catalog.load(right, &codes, &lats, &lons, &mut sources)?;
        let a = &catalog.mapped[&left];
        let b = &catalog.mapped[&right];
        for cell in 0..cells {
            grid[index * cells + cell][25] = a.aod[cell] + (b.aod[cell] - a.aod[cell]) * w;
            for (level, value) in aerosol[index * cells + cell].iter_mut().enumerate() {
                *value = a.profiles[cell][level]
                    + (b.profiles[cell][level] - a.profiles[cell][level]) * w;
            }
        }
    }
    let mut anchor_brackets = Vec::with_capacity(points.len());
    for (row, sample_row) in solar.rows.iter().zip(samples.iter_mut()) {
        let target = from_epoch(row.anchor)?;
        let (left, right, _) = bracket(target)?;
        catalog.load(left, &codes, &lats, &lons, &mut sources)?;
        catalog.load(right, &codes, &lats, &lons, &mut sources)?;
        let w = if left == right {
            0.0
        } else {
            target
                .signed_duration_since(left)
                .num_microseconds()
                .unwrap() as f64
                / 10_800_000_000.0
        };
        anchor_brackets.push([epoch(left), epoch(right), w]);
        let (corners, weights) = geom.stencil(row.observer);
        for sample in sample_row
            .as_chunks_mut::<9>()
            .0
            .iter_mut()
            .filter(|s| s[6] >= 0.5)
        {
            let left = sample[2] as usize;
            let right = sample[3] as usize;
            let w = sample[4];
            let values = corners.map(|c| {
                let a = grid[left * cells + c][25];
                a + (grid[right * cells + c][25] - a) * w
            });
            let aod = if let Some(i) = weights.iter().position(|v| *v == 1.0) {
                values[i]
            } else {
                let numerator: f32 = values.iter().zip(weights).map(|(v, w)| v * w).sum();
                let denominator: f32 = weights.iter().sum();
                if denominator > 0.0 {
                    numerator / denominator
                } else {
                    0.0
                }
            };
            sample[5] = aod;
        }
    }
    let aerosol_ms = started.elapsed().as_secs_f64() * 1000.0 - model_ms;
    let observers: Vec<_> = solar
        .rows
        .iter()
        .map(|row| {
            let mut observer = row.observer;
            observer[1] = geom.longitude(f64::from(observer[1])) as f32;
            observer
        })
        .collect();
    let trimmed: Vec<_> = samples
        .iter()
        .flat_map(|row| row[step_start * 9..step_stop * 9].iter().copied())
        .collect();
    let mut result = vec![0.0; points.len() * timeline::STEPS * NATIONAL_OUTPUT_FIELDS];
    let span = step_stop - step_start;
    firesky_native::assess_batch_with_status(
        grid.as_slice().as_flattened(),
        aerosol.as_slice().as_flattened(),
        11,
        input.frames.len(),
        cells,
        observers.as_slice().as_flattened(),
        points.len(),
        &trimmed,
        points.len() * span,
        span,
        geom.rows,
        geom.cols,
        geom.lat0,
        geom.lon0,
        geom.spacing,
        input.radius_km as f32,
        &mut result[..points.len() * span * NATIONAL_OUTPUT_FIELDS],
    )
    .map_err(|e| format!("native regional assessment failed: {e:?}"))?;
    expand_steps(&mut result, points.len(), step_start, step_stop);
    let assess_ms = started.elapsed().as_secs_f64() * 1000.0 - model_ms - aerosol_ms;
    let n = points.len() as u64;
    let t = hours.len() as u64;
    let c = cells as u64;
    writer.f32("latitudes", &[c], latitudes.iter().copied())?;
    writer.f32(
        "longitudes",
        &[c],
        longitudes.iter().map(|&lon| {
            if !(-180.0..180.0).contains(&lon) {
                (lon + 180.0).rem_euclid(360.0) - 180.0
            } else {
                lon
            }
        }),
    )?;
    writer.f64("hours", &[t], hours.iter().copied())?;
    writer.f32("columns", &[t, c, 36], grid.iter().flatten().copied())?;
    writer.f32(
        "aerosol_profiles",
        &[t, c, 11],
        aerosol.iter().flatten().copied(),
    )?;
    writer.f32(
        "observers",
        &[n, 3],
        solar.rows.iter().flat_map(|row| row.observer),
    )?;
    writer.f32("samples", &[n, 20, 9], samples.iter().flatten().copied())?;
    writer.f32(
        "assessment",
        &[n, 20, NATIONAL_OUTPUT_FIELDS as u64],
        result.iter().copied(),
    )?;
    writer.f64(
        "evaluation_epoch",
        &[n, 20],
        solar.rows.iter().flat_map(|r| r.evaluation),
    )?;
    writer.f64("anchor_epoch", &[n], solar.rows.iter().map(|r| r.anchor))?;
    writer.f64(
        "aerosol_anchor_brackets",
        &[n, 3],
        anchor_brackets.iter().flatten().copied(),
    )?;
    let (quality, quality_data) = if with_frames {
        let (metadata, data) = crate::quality::append_with_data(
            &mut writer,
            &result,
            samples.as_slice().as_flattened(),
            points.len(),
            rising,
            input.radius_km,
            true,
        )?;
        (Some(metadata), data)
    } else if with_quality {
        (
            Some(crate::quality::append_tensors(
                &mut writer,
                &result,
                samples.as_slice().as_flattened(),
                points.len(),
                rising,
                input.radius_km,
            )?),
            None,
        )
    } else {
        (None, None)
    };
    let (diagnostics, diagnostic_data) = if with_diagnostics {
        let started = Instant::now();
        let (mut metadata, data) = crate::diagnostics::append_with_data(
            &mut writer,
            &result,
            points.len(),
            rising,
            matches!(input.model, Model::EcmwfBasic),
        )?;
        metadata["composition_and_output_ms"] = json!(started.elapsed().as_secs_f64() * 1000.0);
        (Some(metadata), if with_frames { Some(data) } else { None })
    } else {
        (None, None)
    };
    let (profile_diagnostics, profile_data) = if with_profiles {
        let (metadata, data) = crate::profile_diagnostics::append_with_data(
            &mut writer,
            crate::profile_diagnostics::Stored {
                profiles: &profile_sources,
                columns: &grid,
                observers: &observers,
                samples: samples.as_slice().as_flattened(),
                levels: &levels,
                rows: geom.rows,
                cols: geom.cols,
                origin: [geom.lat0, geom.lon0],
                spacing: geom.spacing,
                boundary_semantics: &boundary_semantics,
            },
        )?;
        (Some(metadata), if with_frames { Some(data) } else { None })
    } else {
        (None, None)
    };
    let mut point_artifact = None;
    let mut ec_artifact = None;
    let mut service_ec = None;
    let mut frame_prepare_ms = 0.0;
    let mut point_postprocess_ms = 0.0;
    let frame_artifact = if with_frames {
        let started = Instant::now();
        let frame_points: Vec<_> = input
            .points
            .iter()
            .zip(&points)
            .map(|(p, normalized)| crate::frames::Point {
                name: p.name.as_ref().unwrap().clone(),
                lat: p.lat,
                lon: p.lon,
                timezone_name: p.timezone_name.clone(),
                elevation_m: normalized.elevation_m,
            })
            .collect();
        let times: Vec<_> = solar
            .rows
            .iter()
            .map(|r| crate::frames::Times {
                evaluation: r.evaluation,
                anchor: r.anchor,
            })
            .collect();
        let sources = anchor_brackets
            .iter()
            .map(|b| {
                Ok(format!(
                    "cams-ads {}..{} w={:.6}",
                    from_epoch(b[0])?.format("%Y-%m-%d %H:%MZ"),
                    from_epoch(b[1])?.format("%Y-%m-%d %H:%MZ"),
                    b[2],
                ))
            })
            .collect::<Result<Vec<_>, String>>()?;
        let qualities = vec!["cams".to_owned(); points.len()];
        let retained = quality_data.as_ref().unwrap();
        let payload = crate::frames::Payload::prepare(crate::frames::Input {
            points: &frame_points,
            times: &times,
            values: &result,
            samples: samples.as_slice().as_flattened(),
            columns: &grid,
            profiles: profile_data.as_deref(),
            scores: &retained.scores,
            marks: &retained.marks,
            extras: diagnostic_data.as_deref().unwrap(),
            aerosol_sources: &sources,
            aerosol_qualities: &qualities,
            rows: geom.rows,
            cols: geom.cols,
            origin: [geom.lat0, geom.lon0],
            spacing: geom.spacing,
            date: &input.date,
            cycle,
            rising,
            ec: matches!(input.model, Model::EcmwfBasic),
            include_point_core: input.include_point_core,
            point_spacing_deg: input.point_spacing_deg,
        })?;
        frame_prepare_ms = started.elapsed().as_secs_f64() * 1000.0;
        if with_point {
            let point_started = Instant::now();
            let request = crate::point_core::Request::stored(
                &frame_points[0],
                &input.date,
                if rising { "dawn" } else { "dusk" },
                if matches!(input.model, Model::EcmwfBasic) {
                    "ec"
                } else {
                    "gfs"
                },
                input.radius_km,
                crate::time_contract::iso(cycle),
            )
            .with_compute_backend(input.compute_backend_requested.as_deref());
            let point = crate::point_core::materialize(
                &request,
                payload.point_core().ok_or("generated core missing")?,
            )?;
            if matches!(input.model, Model::EcmwfBasic) {
                let ec = crate::ecmwf_point::materialize(
                    &request,
                    payload.point_core().ok_or("generated EC core missing")?,
                    &input.frames.iter().map(|f| f.hour).collect::<Vec<_>>(),
                    samples[0].as_chunks::<9>().0,
                )?;
                let identity = writer.json_artifact("ecmwf_point", &ec)?;
                ec_artifact = Some(json!({
                    "file":"ecmwf_point.json","identity":identity,"contract":"rust-ecmwf-point-report-v1",
                    "report_complete":true,"provenance_complete":false,"public_forecast_complete":false,
                }));
                if with_service {
                    service_ec = Some(ec);
                }
            }
            let composition_ms = point_started.elapsed().as_secs_f64() * 1000.0;
            point_postprocess_ms = composition_ms;
            let identity = writer.json_artifact("point_forecast", &point)?;
            let mut artifact = json!({"file":"point_forecast.json","identity":identity,
                "contract":"rust-stored-point-forecast-v1","point_core_validated":true,
                "validation_and_materialization_ms":composition_ms,
                "validation_materialization_and_output_ms":point_started.elapsed().as_secs_f64()*1000.0,
                "stored_point_payload_complete":true,"automatic_input_selection":plan.is_some(),
                "numpy_blas_tie_parity_proven":false,"public_forecast_complete":false});
            if let Some(plan) = &plan {
                artifact["automatic_input_selection_scope"] =
                    json!(if plan["cams_download_enabled"] == true {
                        if matches!(input.model, Model::EcmwfBasic) {
                            "EC-event-hours-and-CAMS-ADS"
                        } else {
                            "GFS-event-hours-and-CAMS-ADS"
                        }
                    } else {
                        if matches!(input.model, Model::EcmwfBasic) {
                            "EC-event-hours-and-local-CAMS"
                        } else {
                            "GFS-event-hours-and-local-CAMS"
                        }
                    });
            }
            point_artifact = Some(artifact);
        }
        let identity = writer.json_artifact("forecast", &payload)?;
        Some(
            json!({"file":"forecast.json","identity":identity,"contract":"rust-regional-frames-v1",
            "frame_version":103,"point_core_version":8,"include_point_core":input.include_point_core,
            "assembly_serialization_and_output_ms":started.elapsed().as_secs_f64()*1000.0,
            "point_core_validator_ported":true,"point_materialization_ported":true,
            "point_core_validated":with_point,
            "instant_scoring_reused":true,
            "performance_scope":"assembly-through-json-serialization-excludes-prior-scoring",
            "numpy_blas_tie_parity_proven":false,"public_forecast_complete":false}),
        )
    } else {
        None
    };
    let cross_section = if with_profiles {
        let source = json!({
            "contract":"rust-cross-section-source-v1",
            "model_source":if matches!(input.model, Model::EcmwfBasic) {"ec"} else {"gfs"},
            "terrain_source":if matches!(input.model, Model::EcmwfBasic) {
                "EC IFS surface geopotential"
            } else {"GFS surface orography"},
            "target_date":input.date,"phenomenon":if rising {"dawn"} else {"dusk"},
            "cycle_utc":input.cycle_utc,"hours":input.frames.iter().map(|f|f.hour).collect::<Vec<_>>(),
            "pressure_levels":levels,"geometry":geom,"profile_bundles":profiles,
        });
        let identity = writer.json_artifact("cross_section_source", &source)?;
        Some(
            json!({"file":"cross_section_source.json","identity":identity,
            "contract":"rust-cross-section-source-v1",
            "layout":"verified-per-hour-cell-pressure-24",
            "combined_python_mmap":false}),
        )
    } else {
        None
    };
    let service_artifact = if with_service {
        let ec = service_ec
            .as_ref()
            .ok_or("EC service point report missing")?;
        let profile_identity = serde_json::from_value(
            cross_section
                .as_ref()
                .ok_or("EC service profile source missing")?["identity"]
                .clone(),
        )
        .map_err(|e| e.to_string())?;
        let point = crate::frames::Point {
            name: input.points[0].name.clone().unwrap(),
            lat: input.points[0].lat,
            lon: input.points[0].lon,
            elevation_m: points[0].elevation_m,
            timezone_name: input.points[0].timezone_name.clone(),
        };
        let source_files = |prefix: &str| -> Result<Vec<PathBuf>, String> {
            sources
                .iter()
                .filter(|s| s.role.starts_with(prefix))
                .map(|s| s.path.canonicalize().map_err(|e| e.to_string()))
                .collect::<Result<BTreeSet<_>, _>>()
                .map(|v| v.into_iter().collect())
        };
        let evidence = json!({
            "files":source_files("model:")?,"cams_files":source_files("cams:")?,
            "source_tensor_metadata":ec_source_metadata,
            "native_derived_cache":derived_cache.metadata(),
            "profile_grid":{"lat0":f64::from(geom.lat0),"lon0":f64::from(geom.lon0),
                "spacing":f64::from(geom.spacing),"rows":geom.rows,"cols":geom.cols},
            "pressure_levels":levels,
            "evaluation_times_utc":crate::ecmwf_service::evaluation_times(&solar.rows[0].evaluation)?,
            "cams_consumed_valid_times_utc":catalog.mapped.keys().map(|v|v.to_rfc3339_opts(chrono::SecondsFormat::Secs,true)).collect::<Vec<_>>(),
            "cams_source_descriptions":[ec["report"]["aerosol"]["source"]],
            "consumed_source_identities":sources,
            "timing":crate::ecmwf_service::timing([
                model_ms/1000.0,(aerosol_ms+assess_ms)/1000.0,frame_prepare_ms/1000.0,
                point_postprocess_ms/1000.0,started.elapsed().as_secs_f64()
            ]),
        });
        Some(crate::ecmwf_service::append(
            &mut writer,
            crate::ecmwf_service::Context {
                output,
                plan: plan.as_ref().unwrap(),
                ec,
                evidence,
                point: &point,
                cycle,
                source_path: output.join("cross_section_source.json"),
                source_identity: profile_identity,
            },
        )?)
    } else {
        None
    };
    *writer.metadata_mut() = json!({
        "backend":if with_point {"rust-stored-point-forecast-v1"} else if with_frames {"rust-stored-frames-v1"} else if with_profiles {"rust-stored-profile-diagnostics-v1"} else if with_diagnostics {"rust-stored-diagnostics-v1"} else if with_quality {"rust-stored-quality-v1"} else {"rust-stored-assessment-v1"},
        "model":input.model,"cycle_utc":input.cycle_utc,
        "date":input.date,"phenomenon":if rising {"dawn"} else {"dusk"},
        "geometry":geom,"radius_km":input.radius_km,"grid_fields":36,"sample_fields":9,
        "assessment_fields":NATIONAL_OUTPUT_FIELDS,"pressure_levels":levels,"aerosol_pressure_levels":LEVELS,
        "profile_bundles":profiles,"sources":sources,
        "point_source_template":"cams-ads {left:%Y-%m-%d %H:%MZ}..{right:%Y-%m-%d %H:%MZ} w={weight:.6f}",
        "aerosol_anchor_bracket_fields":["left_epoch","right_epoch","right_weight"],
        "point_quality":"cams","optical_profile_coverage":1.0,
        "native_step_range":[step_start,step_stop],"native_abi":firesky_native::NATIVE_ABI_VERSION,
        "compute_backend":"cpu","source_scope":"consumed-model-and-CAMS-files",
        "timezone_database":chrono_tz::IANA_TZDB_VERSION,"polar_policy":"legacy-hour-angle-clamp",
        "ec_basic_proxy_coverage":proxy_coverage,
        "derived_cache":derived_cache.metadata(),
        "public_forecast_complete":false,
        "timings_ms":{"model_profiles_and_solar":model_ms,"model_decode_and_source_hash":decode_ms,
            "model_pack_and_derive":derive_ms,"profile_write_and_verify":profile_write_ms,
            "cams_assembly":aerosol_ms,"native_assessment":assess_ms}
    });
    if let Some(planning) = planning_artifact {
        writer.metadata_mut()["input_plan"] = planning;
    }
    if let Some(quality) = quality {
        writer.metadata_mut()["quality"] = quality;
    }
    if let Some(diagnostics) = diagnostics {
        writer.metadata_mut()["diagnostics"] = diagnostics;
    }
    if let Some(profile_diagnostics) = profile_diagnostics {
        writer.metadata_mut()["profile_diagnostics"] = profile_diagnostics;
    }
    if let Some(frame_artifact) = frame_artifact {
        writer.metadata_mut()["frame_payload"] = frame_artifact;
    }
    if let Some(point_artifact) = point_artifact {
        writer.metadata_mut()["point_forecast"] = point_artifact;
    }
    if let Some(ec_artifact) = ec_artifact {
        writer.metadata_mut()["ecmwf_point_report"] = ec_artifact;
    }
    if let Some(cross_section) = cross_section {
        writer.metadata_mut()["cross_section_source"] = cross_section;
    }
    if let Some(service) = service_artifact {
        writer.metadata_mut()["ecmwf_service"] = service;
    }
    for source in &sources {
        source.revalidate()?;
    }
    for profile in &profile_sources {
        profile.revalidate()?;
    }
    if source_identity(path)? != request_source {
        return Err("assessment request changed during execution".into());
    }
    let manifest = writer.commit(path)?;
    children.retained = true;
    Ok(manifest)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn expands_every_possible_trim_span_without_cross_point_corruption() {
        for points in [1, 2, 7] {
            for start in 0..timeline::STEPS {
                for stop in start + 1..=timeline::STEPS {
                    let compact = points * (stop - start) * NATIONAL_OUTPUT_FIELDS;
                    let mut actual =
                        vec![f32::NAN; points * timeline::STEPS * NATIONAL_OUTPUT_FIELDS];
                    let mut expected = vec![0.0; actual.len()];
                    for (i, value) in actual[..compact].iter_mut().enumerate() {
                        *value = (i + 1) as f32;
                        let point = i / ((stop - start) * NATIONAL_OUTPUT_FIELDS);
                        let local = i % ((stop - start) * NATIONAL_OUTPUT_FIELDS);
                        expected
                            [(point * timeline::STEPS + start) * NATIONAL_OUTPUT_FIELDS + local] =
                            *value;
                    }
                    expand_steps(&mut actual, points, start, stop);
                    assert_eq!(actual, expected);
                }
            }
        }
    }

    #[test]
    fn coordinate_reordering_preserves_fields_and_rejects_ambiguous_geometry() {
        let mut grid =
            gfs::GfsGrid::new(vec![1.0, 0.0, 1.0, 0.0], vec![101.0, 100.0, 100.0, 101.0]);
        grid.fields.insert(
            ("surface".into(), 0, "orog".into()),
            vec![40.0, 10.0, 30.0, 20.0],
        );
        let geometry = Geometry::canonicalize(&mut grid).unwrap();
        assert_eq!(grid.latitudes, [0.0, 0.0, 1.0, 1.0]);
        assert_eq!(grid.longitudes, [100.0, 101.0, 100.0, 101.0]);
        assert_eq!(
            grid.fields.values().next().unwrap(),
            &[10.0, 20.0, 30.0, 40.0]
        );
        assert_eq!(
            geometry.terrain(grid.fields.values().next().unwrap(), 0.5, 100.5),
            25.0
        );
        for (latitudes, longitudes) in [
            (vec![0.0, 0.0, 1.0, 1.0], vec![100.0, 100.0, 100.0, 101.0]),
            (vec![0.0, 0.0, 2.0, 2.0], vec![100.0, 101.0, 100.0, 101.0]),
            (vec![0.0, 1.0, 1.0], vec![100.0, 100.0, 101.0]),
        ] {
            assert!(Geometry::canonicalize(&mut gfs::GfsGrid::new(latitudes, longitudes)).is_err());
        }
    }

    #[test]
    fn wrapped_axis_is_contiguous_and_sampling_uses_equivalent_longitudes() {
        let mut grid = gfs::GfsGrid::new(
            vec![1., 0., 1., 0., 1., 0.],
            vec![-179., 179., 179., -180., -180., -179.],
        );
        grid.fields.insert(
            ("surface".into(), 0, "orog".into()),
            vec![60., 10., 40., 20., 50., 30.],
        );
        let geom = Geometry::canonicalize(&mut grid).unwrap();
        assert_eq!(geom.lons, [179., 180., 181.]);
        assert_eq!(grid.longitudes, [179., 180., 181., 179., 180., 181.]);
        let values = grid.fields.values().next().unwrap();
        assert_eq!(values, &[10., 20., 30., 40., 50., 60.]);
        for lon in [-179.5, 180.5] {
            assert!(geom.inside(0.5, lon));
            assert_eq!(geom.terrain(values, 0.5, lon), 40.);
            assert_eq!(geom.stencil([0.5, lon as f32, 0.]).0, [1, 2, 4, 5]);
            assert_eq!(geom.stencil([0.5, lon as f32, 0.]).1, [0.25; 4]);
        }
        assert!(!geom.inside(0.5, 178.));
        assert!(!geom.inside(0.5, -178.));
        let before = grid.longitudes.clone();
        Geometry::canonicalize(&mut grid).unwrap();
        assert_eq!(grid.longitudes, before);

        for lons in [
            vec![-179., -177., 179., -179., -177., 179.],
            vec![-180., -178., 179., -180., -178., 179.],
        ] {
            assert!(Geometry::canonicalize(&mut gfs::GfsGrid::new(
                vec![0., 0., 0., 1., 1., 1.],
                lons,
            ))
            .is_err());
        }
        let mut edge = gfs::GfsGrid::new(vec![0., 0., 1., 1.], vec![179., -180., 179., -180.]);
        let edge = Geometry::canonicalize(&mut edge).unwrap();
        assert_eq!(edge.lons, [179., 180.]);
        assert!(edge.inside(0., -180.));
        let mut western = gfs::GfsGrid::new(vec![0., 0., 1., 1.], vec![-180., -179., -180., -179.]);
        let western = Geometry::canonicalize(&mut western).unwrap();
        assert!(western.inside(0., 180.));
    }
}
