//! Raw desktop model request planning. No downloads or Python callbacks.
use crate::native::{gfs::GfsGrid, timeline};
use crate::{
    assessment, ecmwf, ecmwf_data, ecmwf_point, gfs_data, grib::EcCodes, input_plan, request_file,
    time_contract, timezone_lookup,
};
use chrono::{DateTime, Duration, NaiveDate, TimeZone, Timelike, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeSet, HashSet},
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};

const MAX_POINTS: usize = 500_000;
const MAX_JSON: u64 = 128 * 1024 * 1024;
/// Regional product contract: every generated grid uses 0.25°; the China
/// production domain must contain exactly 36,685 points.
const REGIONAL_SPACING_DEG: f64 = 0.25;
const CHINA_PRODUCTION_POINT_COUNT: usize = 36_685;

fn custom_bounds(payload: &Value) -> Result<[f64; 4], String> {
    let bounds = payload["custom_bounds"]
        .as_object()
        .ok_or("custom_bounds must be an object")?;
    let b = [
        number(bounds.get("west").unwrap_or(&Value::Null), -180., 180.)?,
        number(bounds.get("east").unwrap_or(&Value::Null), -180., 180.)?,
        number(bounds.get("north").unwrap_or(&Value::Null), -90., 90.)?,
        number(bounds.get("south").unwrap_or(&Value::Null), -90., 90.)?,
    ];
    if b[0] == b[1] || b[3] >= b[2] {
        return Err("invalid custom bounds".into());
    }
    Ok(b)
}

/// Resolve the requested grid spacing for plan-only callers (data warmup).
/// The product forecast path additionally enforces the 0.25° contract in
/// `generated()`; warmup plans may use coarser grids because they only need
/// the download envelope, not per-cell scoring.
pub(crate) fn generated_regional_spacing(payload: &Value) -> Result<f64, String> {
    if let Some(value) = payload.get("spacing_deg") {
        if !value.is_null() {
            let requested = value
                .as_f64()
                .filter(|v| v.is_finite())
                .ok_or("spacing_deg must be a finite number")?;
            if !(0.25..=10.0).contains(&requested) {
                return Err("spacing_deg must be within 0.25..=10.0".into());
            }
            return Ok(requested);
        }
    }
    Ok(REGIONAL_SPACING_DEG)
}
pub(crate) const REGIONS: [(&str, &str, [f64; 4]); 10] = [
    ("east-asia", "EA", [63., 155., 60., 12.]),
    ("southeast-asia", "SE", [85., 155., 35., -20.]),
    ("south-asia", "SA", [55., 110., 45., -5.]),
    ("europe", "EU", [-25., 50., 75., 25.]),
    ("north-america", "NA", [-155., -40., 80., 5.]),
    ("south-america", "SA2", [-90., -25., 20., -60.]),
    ("oceania", "OC", [100., 180., 5., -60.]),
    ("africa-middle-east", "AME", [-25., 65., 40., -40.]),
    ("north-asia", "NAS", [20., 180., 82., 35.]),
    ("china", "CN", [73., 136., 54., 18.]),
];

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    path: PathBuf,
    output_path: PathBuf,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Config {
    pub(crate) gfs_cache_dir: PathBuf,
    pub(crate) ecmwf_cache_dir: PathBuf,
    #[serde(default)]
    pub(crate) cams_cache_dir: Option<PathBuf>,
    pub(crate) timezone_manifest: Option<PathBuf>,
    pub(crate) city_catalog_path: PathBuf,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    payload: Value,
    #[serde(default)]
    regional: bool,
    #[serde(default)]
    fast: bool,
    config: Config,
    now_utc: Option<String>,
}
#[derive(Clone, Serialize)]
struct Point {
    name: String,
    lat: f64,
    lon: f64,
    elevation_m: f64,
    timezone_name: String,
    #[serde(rename = "_elevation_source")]
    source: String,
}
impl Point {
    fn solar(&self) -> Result<timeline::Point, String> {
        Ok(timeline::Point {
            lat: self.lat,
            lon: self.lon,
            elevation_m: self.elevation_m,
            timezone: self
                .timezone_name
                .parse()
                .map_err(|_| "unknown IANA timezone")?,
        })
    }
}
fn string(value: &Value) -> String {
    match value {
        Value::String(v) => v.clone(),
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        v => v.to_string(),
    }
}
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(v) => *v,
        Value::Number(v) => v.as_f64() != Some(0.),
        Value::String(v) => !v.is_empty(),
        Value::Array(v) => !v.is_empty(),
        Value::Object(v) => !v.is_empty(),
    }
}
fn text(payload: &Value, key: &str, default: &str) -> String {
    if truthy(&payload[key]) {
        string(&payload[key])
    } else {
        default.into()
    }
}
fn code(payload: &Value, key: &str, default: &str) -> String {
    text(payload, key, default).trim().to_lowercase()
}
fn number(value: &Value, low: f64, high: f64) -> Result<f64, String> {
    let n = match value {
        Value::Number(v) => v.as_f64(),
        Value::String(v) => v.trim().parse().ok(),
        _ => None,
    }
    .ok_or("coordinate/elevation/radius must be numeric")?;
    if n.is_finite() && (low..=high).contains(&n) {
        Ok(n)
    } else {
        Err("coordinate/elevation/radius exceeds supported bounds".into())
    }
}
fn longitude_timezone(lon: f64) -> String {
    let offset = (lon / 15.0).round() as i32;
    if offset >= 0 {
        format!("Etc/GMT-{}", offset)
    } else {
        format!("Etc/GMT+{}", -offset)
    }
}
fn zone(value: &Value) -> Result<String, String> {
    let name = text(&json!({"zone":value}), "zone", "").trim().to_owned();
    if name.len() > 128 || name.parse::<chrono_tz::Tz>().is_err() {
        return Err("timezone_name must be an explicit valid IANA timezone".into());
    }
    Ok(name)
}
fn truncate(value: String, length: usize) -> String {
    value.chars().take(length).collect()
}
fn region(name: &str) -> Option<(&'static str, &'static str, [f64; 4])> {
    REGIONS.iter().copied().find(|v| v.0 == name)
}
pub(crate) fn longitude_contains(west: f64, east: f64, longitude: f64) -> bool {
    if west <= east {
        longitude >= west - 1e-6 && longitude <= east + 1e-6
    } else {
        longitude >= west - 1e-6 || longitude <= east + 1e-6
    }
}

fn contains(b: [f64; 4], p: &Point) -> bool {
    longitude_contains(b[0], b[1], p.lon) && p.lat <= b[2] + 1e-6 && p.lat >= b[3] - 1e-6
}

fn longitude_ranges(west: f64, east: f64) -> Vec<(f64, f64)> {
    if west <= east {
        vec![(west, east)]
    } else {
        vec![(west, 180.0), (-180.0, east)]
    }
}

/// Union circular longitude intervals and return the shortest public interval.
///
/// The result intentionally keeps `west > east` when the shortest interval
/// crosses the antimeridian.  This preserves the request identity all the way
/// through GFS/EC decoding instead of expanding a small dateline region into a
/// near-global rectangle.
fn union_longitudes(intervals: impl IntoIterator<Item = (f64, f64)>) -> [f64; 2] {
    let mut segments = intervals
        .into_iter()
        .flat_map(|(west, east)| longitude_ranges(west, east))
        .collect::<Vec<_>>();
    segments.sort_by(|a, b| a.0.total_cmp(&b.0).then_with(|| a.1.total_cmp(&b.1)));
    let mut merged: Vec<(f64, f64)> = Vec::with_capacity(segments.len());
    for (left, right) in segments {
        if let Some(last) = merged.last_mut() {
            if left <= last.1 + 1e-9 {
                last.1 = last.1.max(right);
                continue;
            }
        }
        merged.push((left, right));
    }
    if merged.is_empty() {
        return [-180., 180.];
    }
    if merged.len() == 1 && merged[0].0 <= -180. + 1e-9 && merged[0].1 >= 180. - 1e-9 {
        return [-180., 180.];
    }
    let mut largest_gap = -1.0;
    let mut gap_index = 0usize;
    for index in 0..merged.len() {
        let next = (index + 1) % merged.len();
        let gap = if next == 0 {
            merged[0].0 + 360.0 - merged[index].1
        } else {
            merged[next].0 - merged[index].1
        };
        if gap > largest_gap {
            largest_gap = gap;
            gap_index = index;
        }
    }
    let start_index = (gap_index + 1) % merged.len();
    let west = merged[start_index].0;
    let east = merged[gap_index].1;
    [west, if east > 180. { east - 360. } else { east }]
}

fn union_bbox(points: &[Point], radius: f64) -> Result<[f64; 4], String> {
    let mut longitude_intervals = Vec::with_capacity(points.len());
    let mut north: f64 = -90.0;
    let mut south: f64 = 90.0;
    for point in points {
        let bbox = bounding(point, radius);
        longitude_intervals.push((bbox[0], bbox[1]));
        north = north.max(bbox[2]);
        south = south.min(bbox[3]);
    }
    if points.is_empty() {
        return Err("cannot union an empty point set".into());
    }
    let [west, east] = union_longitudes(longitude_intervals);
    Ok([west, east, north, south])
}
fn round4(v: f64) -> f64 {
    // These values are bounded coordinates; decimal formatting retains Python's
    // binary-float tie behavior instead of scaling before rounding.
    format!("{v:.4}").parse().unwrap()
}
fn point(payload: &mut Value) -> Result<Point, String> {
    let mut p = Point {
        name: String::new(),
        lat: number(&payload["lat"], -90., 90.)?,
        lon: number(&payload["lon"], -180., 180.)?,
        elevation_m: number(&payload["elevation_m"], -500., 9000.)?,
        timezone_name: zone(&payload["timezone_name"])?,
        source: "observer".into(),
    };
    let mode = code(payload, "forecast_mode", "model");
    let model = code(payload, "model_source", "gfs");
    let resolution = code(payload, "gfs_resolution", "0p25");
    if mode != "model"
        || !matches!(model.as_str(), "gfs" | "ec")
        || !matches!(resolution.as_str(), "0p25" | "0p50")
        || !matches!(
            code(payload, "compute_backend", "auto").as_str(),
            "auto" | "cpu" | "gpu"
        )
    {
        return Err("desktop model context requires valid model/resolution/backend".into());
    }
    // Match request validation even though the common context fixes model mode.
    for key in if model == "ec" {
        ["ec_cycle_override", "_ec_cycle_override"]
    } else {
        ["gfs_cycle_override", "_gfs_cycle_override"]
    } {
        if !payload[key].is_null() {
            time_contract::parse_utc(payload[key].as_str().ok_or("cycle must be UTC ISO text")?)?;
            break;
        }
    }
    let hint = code(payload, "_grid_region", "");
    let custom = payload["regions"].as_array().is_some_and(|rows| {
        rows.iter()
            .any(|v| string(v).trim().eq_ignore_ascii_case("custom"))
    });
    let coordinate_region = REGIONS.iter().filter(|v| contains(v.2, &p)).min_by(|a, b| {
        ((a.2[1] - a.2[0]) * (a.2[2] - a.2[3])).total_cmp(&((b.2[1] - b.2[0]) * (b.2[2] - b.2[3])))
    });
    if model == "gfs"
        && resolution == "0p25"
        && !custom
        && !hint.starts_with("custom")
        && !hint.starts_with("bbox:")
        && region(&hint).is_none_or(|v| contains(v.2, &p))
        && coordinate_region.is_some_and(|v| v.0 == "china")
    {
        let nearest = |v: f64, lo: f64, hi: f64| {
            let at = (v - lo) / 0.25;
            let i = at.floor();
            let fraction = at - i;
            let i = i + if fraction > 0.5 && (fraction - 0.5).abs() > 1e-9 {
                1.
            } else {
                0.
            };
            round4(lo + i.clamp(0., ((hi - lo) / 0.25).round_ties_even()) * 0.25)
        };
        p.lat = nearest(p.lat, 18., 54.);
        p.lon = nearest(p.lon, 73., 136.);
        p.elevation_m = 0.;
        p.timezone_name = "Asia/Shanghai".into();
        payload["_grid_region"] = json!("china");
        payload["_point_grid_mode"] = json!("nearest-regional-grid-v1");
    }
    if truthy(&payload["_point_grid_mode"]) {
        p.source = "model-grid".into();
    }
    p.name = truncate(
        text(payload, "name", &format!("{:.4}, {:.4}", p.lat, p.lon)),
        120,
    );
    Ok(p)
}
fn generated(payload: &Value, config: &Config) -> Result<Vec<Point>, String> {
    let spacing = generated_regional_spacing(payload)?;
    let rows = payload.get("regions").unwrap_or(&Value::Null);
    let default = vec![json!("east-asia")];
    let rows = if payload.get("regions").is_none() {
        &default
    } else {
        rows.as_array()
            .filter(|v| !v.is_empty())
            .ok_or("regions must be a nonempty array")?
    };
    let mut seen_regions = HashSet::new();
    let mut seen_points = HashSet::new();
    let mut points = Vec::new();
    for raw in rows {
        let name = string(raw).trim().to_lowercase();
        if !seen_regions.insert(name.clone()) {
            continue;
        }
        let (prefix, b, timezone) = if name == "custom" {
            let b = custom_bounds(payload)?;
            // A custom area may span multiple IANA zones. Prefer deriving
            // every grid point from the bundled geographic timezone data.
            // The explicit field remains an input-compatibility fallback for
            // older callers; the native macOS UI no longer sends it.
            let timezone = match payload.get("custom_timezone_name") {
                Some(value) if !value.is_null() => Some(zone(value)?),
                _ => None,
            };
            ("CUSTOM", b, timezone)
        } else {
            let (_, prefix, b) = region(&name).ok_or("unknown regional domain")?;
            (
                prefix,
                b,
                if name == "china" {
                    Some("Asia/Shanghai".into())
                } else {
                    None
                },
            )
        };
        let china_production = rows.len() == 1 && name == "china";
        let mut lat = b[3];
        while lat <= b[2] + 1e-9 {
            for (range_index, (west, east)) in longitude_ranges(b[0], b[1]).into_iter().enumerate()
            {
                let mut lon = west;
                while lon <= east + 1e-9 {
                    // 180E and 180W are the same meridian. Keep the first
                    // representation only when a wrapped range is split.
                    if range_index > 0 && lon == -180.0 && b[0] > b[1] {
                        lon += spacing;
                        continue;
                    }
                    let (a, o) = (round4(lat), round4(lon));
                    // Normalize signed zero as Python tuple equality does.
                    let bits = |v: f64| if v == 0. { 0 } else { v.to_bits() };
                    if seen_points.insert((bits(a), bits(o))) {
                        if points.len() == MAX_POINTS {
                            return Err("regional point count exceeds 500000".into());
                        }
                        points.push(Point {
                            name: format!("{prefix}-{lat:.2}-{lon:.2}"),
                            lat: a,
                            lon: o,
                            elevation_m: 0.,
                            timezone_name: timezone.clone().unwrap_or_default(),
                            source: "model-grid".into(),
                        });
                    }
                    lon += spacing;
                }
            }
            lat += spacing;
        }
        if china_production && points.len() != CHINA_PRODUCTION_POINT_COUNT {
            return Err(format!(
                "china production grid must contain exactly {CHINA_PRODUCTION_POINT_COUNT} points"
            ));
        }
    }
    let indices: Vec<_> = points
        .iter()
        .enumerate()
        .filter_map(|(i, p)| p.timezone_name.is_empty().then_some(i))
        .collect();
    if !indices.is_empty() {
        let strict = crate::app_resources::strict_required() || config.timezone_manifest.is_some();
        if let Some(path) = config.timezone_manifest.as_ref() {
            for chunk in indices.chunks(4096) {
                let coords: Vec<_> = chunk
                    .iter()
                    .map(|&i| [points[i].lat, points[i].lon])
                    .collect();
                match timezone_lookup::lookup_batch(path, &coords) {
                    Ok((zones, _)) => {
                        for (&i, zone) in chunk.iter().zip(zones) {
                            points[i].timezone_name = zone;
                        }
                    }
                    Err(error) if strict => return Err(error),
                    Err(_) => {
                        for &i in chunk {
                            points[i].timezone_name = longitude_timezone(points[i].lon);
                        }
                    }
                }
            }
        } else if strict {
            return Err("regional timezone data is not configured".into());
        } else {
            for &i in &indices {
                points[i].timezone_name = longitude_timezone(points[i].lon);
            }
        }
    }
    Ok(points)
}
fn regional_points(payload: &Value, config: &Config) -> Result<Vec<Point>, String> {
    let mode = code(payload, "point_mode", "");
    if !payload["points"].is_null() && !mode.is_empty() {
        return Err("points conflicts with point_mode".into());
    }
    if !mode.is_empty() {
        if mode != "china-city-catalog"
            || payload["regions"]
                .as_array()
                .is_none_or(|v| v.len() != 1 || !string(&v[0]).trim().eq_ignore_ascii_case("china"))
        {
            return Err("city catalog mode requires the China region".into());
        }
        let (catalog, _): (Value, _) =
            request_file::read(&config.city_catalog_path, 4 * 1024 * 1024)?;
        let rows = catalog["cities"]
            .as_array()
            .filter(|v| v.len() == 695)
            .ok_or("city catalog must contain 695 cities")?;
        let mut names = HashSet::new();
        let mut points = rows
            .iter()
            .map(|row| {
                let name = text(row, "short_name", &text(row, "name", ""))
                    .trim()
                    .to_owned();
                if name.is_empty() || !names.insert(name.clone()) {
                    return Err("invalid city catalog name".into());
                }
                Ok(Point {
                    name: truncate(name, 120),
                    lat: number(&row["lat"], -90., 90.)?,
                    lon: number(&row["lon"], -180., 180.)?,
                    elevation_m: 0.,
                    timezone_name: "Asia/Shanghai".into(),
                    source: "model-grid".into(),
                })
            })
            .collect::<Result<Vec<Point>, String>>()?;
        if let Some(path) = config.timezone_manifest.as_ref() {
            for chunk in points.chunks_mut(4096) {
                let coords: Vec<_> = chunk.iter().map(|p| [p.lat, p.lon]).collect();
                let (zones, _) = timezone_lookup::lookup_batch(path, &coords)?;
                for (point, zone) in chunk.iter_mut().zip(zones) {
                    point.timezone_name = zone;
                }
            }
        }
        return Ok(points);
    }
    if payload["points"].is_null() {
        return generated(payload, config);
    }
    let rows = payload["points"]
        .as_array()
        .filter(|v| !v.is_empty() && v.len() <= MAX_POINTS)
        .ok_or("points requires 1..500000 observers")?;
    rows.iter()
        .enumerate()
        .map(|(i, row)| {
            if !row.is_object() {
                return Err("point must be an object".into());
            }
            let source = code(row, "elevation_source", "");
            let elevation_m = match source.as_str() {
                "observer" => number(&row["elevation_m"], -500., 9000.)?,
                "model-grid" if row.get("elevation_m").is_none() => 0.,
                _ => return Err(
                    "points require explicit observer elevation or model-grid without elevation_m"
                        .into(),
                ),
            };
            Ok(Point {
                name: truncate(text(row, "name", &format!("point-{}", i + 1)), 120),
                lat: number(&row["lat"], -90., 90.)?,
                lon: number(&row["lon"], -180., 180.)?,
                elevation_m,
                timezone_name: zone(&row["timezone_name"])?,
                source,
            })
        })
        .collect()
}

/// Return the immutable city-catalog observers that belong to the requested
/// (non-wrapping) regional bounds.  The assessment input intentionally
/// contains only its strict observer fields; catalog metadata is kept out of
/// the calculation contract and can be attached by the caller after scoring.
pub(crate) fn city_catalog_points(
    path: &Path,
    timezone_manifest: Option<&Path>,
    bounds: &[[f64; 4]],
) -> Result<Vec<Value>, String> {
    let (catalog, _) = request_file::read::<Value>(path, 4 * 1024 * 1024)?;
    let rows = catalog["cities"]
        .as_array()
        .filter(|v| v.len() == 695)
        .ok_or("city catalog must contain 695 cities")?;
    let mut names = HashSet::new();
    let mut points = Vec::new();
    for row in rows {
        let name = text(row, "short_name", &text(row, "name", ""))
            .trim()
            .to_owned();
        if name.is_empty() || !names.insert(name.clone()) {
            return Err("invalid city catalog name".into());
        }
        let lat = number(&row["lat"], -90., 90.)?;
        let lon = number(&row["lon"], -180., 180.)?;
        if !bounds.is_empty()
            && !bounds.iter().any(|bound| {
                longitude_contains(bound[0], bound[1], lon)
                    && lat >= bound[3] - 1e-6
                    && lat <= bound[2] + 1e-6
            })
        {
            continue;
        }
        points.push(json!({
            "name":truncate(name, 120),
            "lat":lat,
            "lon":lon,
            "timezone_name":"Asia/Shanghai",
            "elevation_source":"model-grid",
        }));
    }
    if let Some(path) = timezone_manifest {
        for chunk in points.chunks_mut(4096) {
            let coords: Vec<_> = chunk
                .iter()
                .map(|point| {
                    [
                        point["lat"].as_f64().unwrap(),
                        point["lon"].as_f64().unwrap(),
                    ]
                })
                .collect();
            let (zones, _) = timezone_lookup::lookup_batch(path, &coords)?;
            for (point, zone) in chunk.iter_mut().zip(zones) {
                point["timezone_name"] = json!(zone);
            }
        }
    }
    Ok(points)
}

fn bounding(p: &Point, radius: f64) -> [f64; 4] {
    let lat = radius / 111.;
    let lon = radius / (111. * p.lat.to_radians().cos()).max(25.);
    let normalize = |v: f64| {
        let n = (v + 180.).rem_euclid(360.) - 180.;
        if n == -180. && v > 0. {
            180.
        } else {
            n
        }
    };
    [
        normalize(p.lon - lon),
        normalize(p.lon + lon),
        (p.lat + lat).min(90.),
        (p.lat - lat).max(-90.),
    ]
}
fn union_boxes(points: &[Point], radius: f64) -> Result<[f64; 4], String> {
    let mut result = [180_f64, -180_f64, -90_f64, 90_f64];
    for p in points {
        let b = bounding(p, radius);
        result[2] = result[2].max(b[2]);
        result[3] = result[3].min(b[3]);
    }
    let intervals = points.iter().map(|p| {
        let b = bounding(p, radius);
        (b[0], b[1])
    });
    let [west, east] = union_longitudes(intervals);
    result[0] = west;
    result[1] = east;
    Ok(result)
}
fn corners(b: [f64; 4]) -> Vec<Point> {
    [b[3], b[2]]
        .into_iter()
        .flat_map(|lat| {
            [b[0], b[1]].into_iter().map(move |lon| Point {
                name: String::new(),
                lat,
                lon,
                elevation_m: 0.,
                timezone_name: "UTC".into(),
                source: "observer".into(),
            })
        })
        .collect()
}
fn domain(
    payload: &Value,
    points: &[Point],
    model: &str,
    radius: f64,
) -> Result<(Option<String>, [f64; 4], String), String> {
    let forced = code(payload, "_compute_region_override", "");
    let regions = payload["regions"].as_array();
    let mut selected = if !forced.is_empty() {
        Some(
            region(&forced)
                .ok_or("unknown forced compute region")?
                .0
                .to_owned(),
        )
    } else {
        let name = regions.filter(|v| !v.is_empty()).map_or_else(
            || Some("east-asia".into()),
            |v| {
                if v.len() == 1 {
                    Some(string(&v[0]).trim().to_lowercase())
                } else {
                    None
                }
            },
        );
        name.filter(|v| region(v).is_some())
    };
    if selected
        .as_ref()
        .is_some_and(|name| points.iter().any(|p| !contains(region(name).unwrap().2, p)))
    {
        selected = None;
    }
    let explicit =
        !payload["points"].is_null() || code(payload, "point_mode", "") == "china-city-catalog";
    // Multiple selected regions (e.g. east-asia + custom) have no single named
    // domain: the point union already spans them, so compute on the union bbox
    // with source padding instead of silently falling back to one named region
    // and failing the terrain prepass for points outside it.
    let multi_region = regions.is_some_and(|v| v.len() > 1);
    let bbox = if model == "ec" && explicit && forced.is_empty() {
        selected = None;
        let bbox = union_bbox(points, 0.)?;
        // 显式点集若退化（单点，或共线使某一轴零跨度），紧贴包络会塌成
        // 单行/单列：EC 代理网格无法从单一格点推断间距（"EC proxy requires
        // at least one non-singleton axis"），CAMS 覆盖合同也要求至少两条
        // 纬线/经线。此时回退到与 GFS 一致的缓冲区域域（source padding），
        // 保证区域 EC 始终是二维网格；真正的多点二维点集仍保留紧贴包络。
        if bbox[0] == bbox[1] || bbox[2] == bbox[3] {
            gfs_data::canonical_bbox(union_boxes(points, 1000.)?, "0p25")?
        } else {
            bbox
        }
    } else if selected.as_deref() == Some("china") && model == "gfs" {
        gfs_data::canonical_bbox(
            union_boxes(&corners(region("china").unwrap().2), source_radius(radius))?,
            "0p25",
        )?
    } else if selected.is_none() || (multi_region && forced.is_empty()) {
        gfs_data::canonical_bbox(
            union_boxes(
                points,
                if model == "gfs" {
                    source_radius(radius)
                } else {
                    1000.
                },
            )?,
            "0p25",
        )?
    } else {
        region(selected.as_deref().ok_or("missing regional domain")?)
            .ok_or("missing regional domain")?
            .2
    };
    if multi_region && forced.is_empty() {
        selected = None;
    }
    let grid = if let Some(name) = &selected {
        name.clone()
    } else {
        // Geographic bboxes exceed the scientific-notation threshold except
        // custom explicit coordinates; serde's finite f64 spelling is retained.
        let digest = Sha256::digest(serde_json::to_vec(&bbox).map_err(|e| e.to_string())?);
        format!(
            "bbox:{}",
            digest
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect::<String>()[..16]
                .to_owned()
        )
    };
    Ok((selected, bbox, grid))
}
pub(crate) fn source_radius(radius: f64) -> f64 {
    (radius + (radius + 400.).min(1000.)).max(1000.)
}

/// Default point compute radius; also the worst case used to size the background
/// sync envelope so any point forecast domain inside the anchor is covered.
pub(crate) const DEFAULT_RADIUS_KM: f64 = 1000.;

/// Expand an anchor bbox so it covers the GFS point-forecast domain of ANY point
/// inside it. Point domains use `source_radius` (see `bounding`): lat margin is
/// radius/111, lon margin is radius/(111*cos(point_lat)) with the denominator
/// floored at 25. The lon margin is largest at the anchor's highest |latitude|
/// (points live inside the anchor, not inside the expanded lat band), so use the
/// anchor's own extreme latitude for the worst case. Caller canonicalizes/clamps.
pub(crate) fn gfs_point_domain_envelope(bounds: [f64; 4], radius_km: f64) -> [f64; 4] {
    let r = source_radius(radius_km);
    let lat = r / 111.;
    let north = (bounds[2] + lat).min(90.);
    let south = (bounds[3] - lat).max(-90.);
    let worst_lat = bounds[2].abs().max(bounds[3].abs());
    let lon = r / (111. * worst_lat.to_radians().cos()).max(25.);
    let west = (bounds[0] - lon).max(-180.);
    let east = (bounds[1] + lon).min(180.);
    [west, east, north, south]
}
fn iso(t: DateTime<Utc>) -> String {
    if t.timestamp_subsec_micros() == 0 {
        format!("{}+00:00", t.format("%Y-%m-%dT%H:%M:%S"))
    } else {
        format!(
            "{}.{:06}+00:00",
            t.format("%Y-%m-%dT%H:%M:%S"),
            t.timestamp_subsec_micros()
        )
    }
}
fn timeline(points: &[Point], date: NaiveDate, rising: bool) -> Result<Vec<timeline::Row>, String> {
    let solar = points
        .iter()
        .map(Point::solar)
        .collect::<Result<Vec<_>, _>>()?;
    let mut rows = Vec::with_capacity(points.len());
    for chunk in solar.chunks(1024) {
        rows.extend(
            timeline::prepare(chunk, date, rising, None)
                .map_err(|e| e.to_string())?
                .rows,
        );
    }
    Ok(rows)
}
fn times(
    points: &[Point],
    date: NaiveDate,
    rising: bool,
    batch: bool,
    ec_point: bool,
) -> Result<Vec<DateTime<Utc>>, String> {
    if batch || ec_point {
        let mut result = Vec::new();
        let mut any = false;
        for chunk in points.chunks(1024) {
            for row in timeline(chunk, date, rising)? {
                for (epoch, valid) in row.evaluation.into_iter().zip(row.valid) {
                    any |= valid != 0;
                    if ec_point || valid != 0 {
                        result.push(assessment::from_epoch(epoch)?);
                    }
                }
            }
        }
        if !any {
            return Err("no computable dawn/dusk events for this region/date".into());
        }
        // Regional grids evaluate tens of thousands of points whose valid
        // dawn/dusk epochs differ by seconds to minutes. Every downstream
        // consumer resolves 3-hour source brackets (EC source_bracket, CAMS
        // bracket, GFS required hours), so collapse the list to one
        // pair of representatives (earliest + latest) per 3-hour bracket.
        // Exact-second dedup cannot do this: sub-second epochs stay distinct
        // and the list trips the EC plan's 1024-target limit. Keeping both
        // extremes preserves every bracket pair AND the full valid-sample
        // range, so the required forecast hours still cover the latest solar
        // sample; keeping only the earliest epoch would drop the upper
        // bracket and truncate the frame time axis below the latest sample.
        result.sort();
        let mut collapsed: Vec<DateTime<Utc>> = Vec::new();
        for t in result {
            let bracket = t.timestamp().div_euclid(10_800);
            let n = collapsed.len();
            if n >= 1 && collapsed[n - 1].timestamp().div_euclid(10_800) == bracket {
                if n >= 2 && collapsed[n - 2].timestamp().div_euclid(10_800) == bracket {
                    collapsed[n - 1] = t;
                } else if collapsed[n - 1] != t {
                    collapsed.push(t);
                }
            } else {
                collapsed.push(t);
            }
        }
        Ok(collapsed)
    } else {
        let mut result = Vec::new();
        for p in points {
            result.extend(input_plan::scalar_times(p.solar()?, date, rising)?);
        }
        Ok(result)
    }
}
fn required(
    model: &str,
    cycle: DateTime<Utc>,
    times: &[DateTime<Utc>],
) -> Result<BTreeSet<u32>, String> {
    let mut hours = BTreeSet::new();
    for &t in times {
        if model == "ec" {
            let (a, b, _) = ecmwf_data::source_bracket(cycle, t)?;
            hours.extend([a as u32, b as u32]);
        } else {
            let offset = ((t - cycle)
                .num_microseconds()
                .ok_or("event span overflow")? as f64
                / 3_600_000_000.)
                .max(0.);
            let step = if offset > 120. { 3. } else { 1. };
            let upper = (offset / step).ceil() * step;
            if upper > 10_000. {
                return Err("desktop GFS forecast hour exceeds 10000".into());
            }
            hours.extend([((offset / step).floor() * step) as u32, upper as u32]);
        }
    }
    Ok(hours)
}
#[allow(clippy::too_many_arguments)]
fn cycle(
    input: &Input,
    points: &[Point],
    date: NaiveDate,
    rising: bool,
    model: &str,
    ec_point: bool,
    now: DateTime<Utc>,
    evaluation: &[DateTime<Utc>],
) -> Result<DateTime<Utc>, String> {
    let public = format!("{model}_cycle_override");
    let private = format!("_{model}_cycle_override");
    let keys = if ec_point && !input.fast {
        [public, private]
    } else {
        [private, public]
    };
    for key in keys {
        // The real (non-fast) EC path re-verifies publication below, so the
        // heuristic private pin written by the fast check must not be honored
        // literally: it can name a cycle the remote has not published yet,
        // which prepare would then fail to download (EC index HTTP 404).
        if model == "ec" && !input.fast && key.starts_with('_') {
            continue;
        }
        if !input.payload[&key].is_null() {
            let time = time_contract::parse_utc(
                input.payload[&key]
                    .as_str()
                    .ok_or("cycle must be UTC ISO text")?,
            )?;
            return Ok(if ec_point && !input.fast {
                time.with_nanosecond(0).unwrap()
            } else {
                time
            });
        }
    }
    let mut reference = now;
    let mut anchor = None;
    for chunk in points.chunks(1024) {
        for row in timeline(chunk, date, rising)? {
            if row.anchor.is_finite() {
                let t = assessment::from_epoch(row.anchor)?;
                reference = reference.min(t);
                anchor = Some(anchor.map_or(t, |a: DateTime<Utc>| a.min(t)));
            }
        }
    }
    if model == "ec" && !input.fast {
        let ec_source = input.payload["ecmwf_source"].as_str().unwrap_or("ecmwf");
        if !["ecmwf", "aws", "google"].contains(&ec_source) {
            return Err("ecmwf_source must be ecmwf, aws or google".into());
        }
        // Publication-aware selection for both point and regional EC: probe the
        // remote and fall back 12h to the latest published cycle covering the
        // event window, so prepare never targets an unpublished cycle. A fully
        // cached cycle is accepted without network (use_cache_for_cycle).
        return ecmwf_data::snapshot_cycle(
            &ecmwf_data::Config {
                cache_dir: input.config.ecmwf_cache_dir.clone(),
                source: ec_source.into(),
                timeout_seconds: 8.,
                ..Default::default()
            },
            None,
            anchor.unwrap_or(reference),
            Some(&iso(now)),
            evaluation,
        );
    }
    let (lag, step) = if model == "gfs" { (4, 6) } else { (7, 12) };
    let adjusted = time_contract::checked(
        reference
            .checked_sub_signed(Duration::hours(lag))
            .ok_or("cycle underflow")?,
    )?;
    Ok(Utc.from_utc_datetime(
        &adjusted
            .date_naive()
            .and_hms_opt(adjusted.hour() / step * step, 0, 0)
            .unwrap(),
    ))
}

// The owning terrain sampler permits points outside a cached grid and clamps
// them to its edge. Keep that policy separate from assessment's inside check.
fn apply_terrain(grid: &GfsGrid, points: &mut [Point]) -> Result<(), String> {
    let values = grid
        .fields
        .get(&("surface".into(), 0, "orog".into()))
        .ok_or("missing orography")?;
    let axes = |v: &[f32]| {
        let mut a = v.iter().map(|&v| f64::from(v)).collect::<Vec<_>>();
        a.sort_by(f64::total_cmp);
        a.dedup();
        a
    };
    let lat = axes(&grid.latitudes);
    let normalized = grid
        .longitudes
        .iter()
        .map(|&v| ((f64::from(v) + 180.).rem_euclid(360.) - 180.) as f32)
        .collect::<Vec<_>>();
    let lon = axes(&normalized);
    if lat.is_empty()
        || lon.is_empty()
        || lat.len() * lon.len() != values.len()
        || values.len() != grid.latitudes.len()
    {
        return Err("terrain requires a rectangular grid".into());
    }
    let mut matrix = vec![f64::NAN; values.len()];
    for (i, &v) in values.iter().enumerate() {
        let a = lat.partition_point(|&x| x < f64::from(grid.latitudes[i]));
        let b = lon.partition_point(|&x| x < f64::from(normalized[i]));
        matrix[a * lon.len() + b] = f64::from(v);
    }
    let bound = |axis: &[f64], x: f64| {
        let b = axis.partition_point(|&v| v <= x).clamp(1, axis.len() - 1);
        let a = b.saturating_sub(1);
        let d = axis[b] - axis[a];
        (
            a,
            b,
            if d > 0. {
                ((x - axis[a]) / d).clamp(0., 1.)
            } else {
                0.
            },
        )
    };
    let mut updates = Vec::new();
    for (i, p) in points
        .iter()
        .enumerate()
        .filter(|(_, p)| p.source == "model-grid")
    {
        let bracket = |axis: &[f64], v: f64| {
            if axis.len() == 1 {
                (0, 0, 0.)
            } else {
                bound(axis, v)
            }
        };
        let (a, b, y) = bracket(&lat, p.lat);
        let (c, d, x) = bracket(&lon, (p.lon + 180.).rem_euclid(360.) - 180.);
        let q = |r, c| matrix[r * lon.len() + c];
        let v = q(a, c) * (1. - y) * (1. - x)
            + q(a, d) * (1. - y) * x
            + q(b, c) * y * (1. - x)
            + q(b, d) * y * x;
        if !v.is_finite() || !(-500. ..=9000.).contains(&v) {
            return Err("invalid sampled model terrain".into());
        }
        updates.push((i, v));
    }
    for (i, v) in updates {
        points[i].elevation_m = v;
    }
    Ok(())
}
/// Combine per-segment orography grids (wrapped envelopes download one file
/// per longitude segment) into the rectangular grid `apply_terrain` expects.
fn merge_terrain_grids(grids: Vec<GfsGrid>) -> Result<GfsGrid, String> {
    if grids.len() == 1 {
        return Ok(grids.into_iter().next().unwrap());
    }
    let key = ("surface".to_owned(), 0_i32, "orog".to_owned());
    let mut lats: Vec<f32> = Vec::new();
    let mut lons: Vec<f32> = Vec::new();
    for grid in &grids {
        if !grid.fields.contains_key(&key) {
            return Err("terrain segment lacks surface orography".into());
        }
        for &v in &grid.latitudes {
            if !lats.contains(&v) {
                lats.push(v);
            }
        }
        for &v in &grid.longitudes {
            if !lons.contains(&v) {
                lons.push(v);
            }
        }
    }
    lats.sort_by(|a, b| a.total_cmp(b));
    lons.sort_by(|a, b| a.total_cmp(b));
    let mut values = vec![f32::NAN; lats.len() * lons.len()];
    for grid in &grids {
        let field = &grid.fields[&key];
        for (i, &v) in field.iter().enumerate() {
            let a = lats.partition_point(|&x| x < grid.latitudes[i]);
            let b = lons.partition_point(|&x| x < grid.longitudes[i]);
            values[a * lons.len() + b] = v;
        }
    }
    if values.iter().any(|v| !v.is_finite()) {
        return Err("terrain segments do not form a rectangular grid".into());
    }
    // Cell-major coordinate lists (one entry per cell), matching the layout
    // apply_terrain and the decoders use; the unique axes only index values.
    let mut cell_lats = Vec::with_capacity(values.len());
    let mut cell_lons = Vec::with_capacity(values.len());
    for &lat in &lats {
        for &lon in &lons {
            cell_lats.push(lat);
            cell_lons.push(lon);
        }
    }
    let mut merged = GfsGrid::new(cell_lats, cell_lons);
    merged.insert(key, values)?;
    Ok(merged)
}
#[allow(clippy::too_many_arguments)]
fn terrain(
    input: &Input,
    model: &str,
    cycle: DateTime<Utc>,
    hours: &BTreeSet<u32>,
    bbox: [f64; 4],
    area_bbox: [f64; 4],
    compute_region: Option<String>,
    points: &mut [Point],
) -> Result<bool, String> {
    let codes = EcCodes::load()?;
    if model == "gfs" {
        let client = gfs_data::GfsClient::new(gfs_data::Config {
            cache_dir: input.config.gfs_cache_dir.clone(),
            resolution: "0p25".into(),
            region: compute_region,
            timeout_seconds: 90.,
            lock_timeout_seconds: 30.,
            download_attempts: 3,
        })?;
        for &hour in hours {
            let attempt = || -> Result<Option<GfsGrid>, String> {
                // Wrapped envelopes live in two per-segment files; every
                // segment must be cached before terrain can be sampled.
                let mut paths = Vec::new();
                for part in gfs_data::request_bboxes(bbox, "0p25")? {
                    let url = gfs_data::build_url(cycle, hour, Some(part), "0p25", false)?;
                    match client.find(cycle, hour, Some(part), Some(&url))? {
                        Some(path) => paths.push(path),
                        None => return Ok(None),
                    }
                }
                // GFS's owning cache-only preflight does not validate the GRIB
                // cycle here; readiness/execution performs that validation.
                let mut grids = Vec::new();
                for path in &paths {
                    grids.push(codes.decode_gfs_terrain(path, 4096, 5_000_000)?);
                }
                Ok(Some(merge_terrain_grids(grids)?))
            };
            if let Ok(Some(grid)) = attempt() {
                apply_terrain(&grid, points)?;
                return Ok(true);
            }
        }
    } else {
        for &hour in hours {
            let mut path = ecmwf_data::path(&input.config.ecmwf_cache_dir, cycle, i64::from(hour));
            if !path.exists() {
                path = input.config.ecmwf_cache_dir.join(format!(
                    "ifs-basic-v2-{}-f{hour:03}.grib2",
                    cycle.format("%Y%m%d%H")
                ));
            }
            if path.is_file() && path.metadata().is_ok_and(|m| m.len() > 0) {
                // Mirror the GFS branch: an unusable hour (decode/contract
                // failure) skips to the next candidate instead of failing the
                // whole prepare with a raw decode error.
                let mut attempt = || -> Result<(), String> {
                    // Terrain-only decode: the full basic catalog retains ~38
                    // fields per cell and exceeds the retained-value limit for
                    // large regional envelopes; the prepass only needs z.
                    let grid = ecmwf::decode_basic_terrain(
                        &codes,
                        &path,
                        Some(area_bbox),
                        Some(&iso(cycle)),
                        Some(i64::from(hour)),
                        4096,
                        5_000_000,
                    )?;
                    apply_terrain(&grid, points)
                };
                if attempt().is_ok() {
                    return Ok(true);
                }
            }
        }
    }
    Ok(false)
}
pub(crate) fn resolve_path(path: &Path) -> Result<Value, String> {
    let (input, _) = request_file::read(path, MAX_JSON)?;
    resolve(input)
}

fn resolve(mut input: Input) -> Result<Value, String> {
    if !input.payload.is_object() {
        return Err("desktop payload must be an object".into());
    }
    for root in [
        &input.config.gfs_cache_dir,
        &input.config.ecmwf_cache_dir,
        &input.config.city_catalog_path,
    ] {
        if root.as_os_str().is_empty() || root.to_str().is_none_or(|s| s.len() > 4096) {
            return Err("invalid desktop context resource path".into());
        }
    }
    if let Some(root) = &input.config.cams_cache_dir {
        if root.as_os_str().is_empty() || root.to_str().is_none_or(|s| s.len() > 4096) {
            return Err("invalid desktop context CAMS cache path".into());
        }
    }
    let model = text(&input.payload, "model_source", "gfs");
    if !matches!(model.as_str(), "gfs" | "ec") {
        return Err("model_source must be gfs or ec".into());
    }
    let date = time_contract::parse_date(
        input.payload["target_date"]
            .as_str()
            .ok_or("target_date is required")?,
    )?;
    let rising = match input.payload["phenomenon"].as_str() {
        Some("dawn") => true,
        Some("dusk") => false,
        _ => return Err("phenomenon must be dawn or dusk".into()),
    };
    let radius = number(
        input
            .payload
            .get("radius_km")
            .unwrap_or(&json!(DEFAULT_RADIUS_KM)),
        100.,
        1000.,
    )?;
    let now = input
        .now_utc
        .as_deref()
        .map(time_contract::parse_utc)
        .transpose()?
        .unwrap_or_else(|| DateTime::<Utc>::from(std::time::SystemTime::now()));
    let ec_point = model == "ec" && !input.regional;
    let mut points = if input.regional {
        // Product contract: forecast contexts always use the 0.25° grid.
        // Plan-only warmup callers (fast=true) may keep coarser planning
        // grids because they only derive the download envelope.
        if !input.fast
            && input.payload["points"].is_null()
            && code(&input.payload, "point_mode", "").is_empty()
            && generated_regional_spacing(&input.payload)? != REGIONAL_SPACING_DEG
        {
            return Err("regional grids require spacing_deg=0.25".into());
        }
        regional_points(&input.payload, &input.config)?
    } else {
        vec![point(&mut input.payload)?]
    };
    let (compute_region, bbox, grid_region) = if input.regional {
        domain(&input.payload, &points, &model, radius)?
    } else {
        (
            None,
            if ec_point {
                bounding(&points[0], ecmwf_point::domain(radius)?[2])
            } else {
                gfs_data::canonical_bbox(union_boxes(&points, source_radius(radius))?, "0p25")?
            },
            text(&input.payload, "_grid_region", "custom"),
        )
    };
    let area_bbox = if model == "ec" && !ec_point {
        ec_regional_model_bbox(bbox, radius)?
    } else {
        bbox
    };
    let mut evaluation = times(
        &points,
        date,
        rising,
        model == "ec" || points.len() >= 64,
        ec_point,
    )?;
    let cycle = cycle(
        &input,
        &points,
        date,
        rising,
        &model,
        ec_point,
        now,
        &evaluation,
    )?;
    let mut terrain_pending = points.iter().any(|p| p.source == "model-grid");
    if terrain_pending && !input.fast {
        // The preparation prepass downloads the first hour of the reported
        // envelope, so the sampler must search that same ±2 h envelope rather
        // than only the core evaluation hours, or the prepass file never hits.
        let mut envelope = evaluation.clone();
        for t in &evaluation {
            for offset in [-2, 2] {
                if let Some(shifted) = t.checked_add_signed(Duration::hours(offset)) {
                    if shifted >= cycle {
                        envelope.push(shifted);
                    }
                }
            }
        }
        let hours = required(&model, cycle, &envelope)?;
        if terrain(
            &input,
            &model,
            cycle,
            &hours,
            bbox,
            area_bbox,
            compute_region.clone(),
            &mut points,
        )
        .unwrap_or(false)
        {
            evaluation = times(
                &points,
                date,
                rising,
                model == "ec" || points.len() >= 64,
                false,
            )?;
            terrain_pending = false;
        }
    }
    let mut check_times = BTreeSet::new();
    for t in evaluation {
        for offset in if terrain_pending {
            &[-2, 0, 2][..]
        } else {
            &[0][..]
        } {
            let t = time_contract::checked(
                t.checked_add_signed(Duration::hours(*offset))
                    .ok_or("event envelope overflow")?,
            )?;
            if t >= cycle {
                check_times.insert(t);
            }
        }
    }
    if check_times.is_empty() {
        return Err("model cycle is later than the event window".into());
    }
    let check_times = check_times.into_iter().collect::<Vec<_>>();
    let hours = required(&model, cycle, &check_times)?;
    let round = |v: f64| -> f64 { format!("{v:.3}").parse().unwrap() };
    let area = [
        round((area_bbox[2] + 0.6).min(90.)),
        round(area_bbox[0] - 0.6),
        round((area_bbox[3] - 0.6).max(-90.)),
        round(area_bbox[1] + 0.6),
    ];
    let ec_source = input.payload["ecmwf_source"].as_str().unwrap_or("ecmwf");
    if !["ecmwf", "aws", "google"].contains(&ec_source) {
        return Err("ecmwf_source must be ecmwf, aws or google".into());
    }
    let common = json!({"target_date":input.payload["target_date"],"phenomenon":input.payload["phenomenon"],
        "model_source":model,"forecast_mode":"model","satellite_source":"off","radius_km":radius,
        "compute_backend":"auto","gfs_resolution":"0p25","ecmwf_source":ec_source,
        "workers":input.payload.get("workers").unwrap_or(&json!(4)),
        "_compute_region":compute_region,"_grid_region":grid_region,"_model_fixed_bbox":bbox,"_gfs_fixed_bbox":bbox,
        "_model_cycle_override":iso(cycle),format!("_{model}_cycle_override"):iso(cycle)});
    Ok(
        json!({"points":points,"common":common,"cycle":iso(cycle),"bbox":bbox,"area":area,"hours":hours,
        "times":check_times.into_iter().map(iso).collect::<Vec<_>>(),"ec_point":ec_point,"terrain_pending":terrain_pending}),
    )
}
pub(crate) fn ec_regional_model_bbox(bbox: [f64; 4], radius: f64) -> Result<[f64; 4], String> {
    union_boxes(&corners(bbox), ecmwf_point::domain(radius)?[2])
}
pub fn execute(params: Params) -> Result<Value, String> {
    let (input, source): (Input, _) = request_file::read(&params.path, MAX_JSON)?;
    let context = resolve(input)?;
    let bytes = serde_json::to_vec(
        &json!({"contract":"rust-desktop-context-v1","source":source,
        "runtime_python_used":false,"context":context}),
    )
    .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > MAX_JSON {
        return Err("desktop context output exceeds 128 MiB".into());
    }
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&params.output_path)
        .map_err(|e| e.to_string())?;
    if let Err(e) = output.write_all(&bytes).and_then(|_| output.sync_all()) {
        drop(output);
        let _ = fs::remove_file(&params.output_path);
        return Err(e.to_string());
    }
    Ok(
        json!({"contract":"rust-desktop-context-file-v1","output_path":params.output_path,
        "bytes":bytes.len(),"sha256":Sha256::digest(&bytes).iter().map(|b| format!("{b:02x}")).collect::<String>(),
        "source":source,"runtime_python_used":false}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wrapped_terrain_segments_merge_into_rectangular_grid() {
        let key = ("surface".to_owned(), 0_i32, "orog".to_owned());
        let mut left = GfsGrid::new(
            vec![10.0, 20.0, 10.0, 20.0],
            vec![170.0, 170.0, 180.0, 180.0],
        );
        left.insert(key.clone(), vec![1.0, 2.0, 3.0, 4.0]).unwrap();
        let mut right = GfsGrid::new(
            vec![10.0, 20.0, 10.0, 20.0],
            vec![-180.0, -180.0, -170.0, -170.0],
        );
        right.insert(key.clone(), vec![5.0, 6.0, 7.0, 8.0]).unwrap();
        let merged = merge_terrain_grids(vec![left, right]).unwrap();
        assert_eq!(
            merged.longitudes,
            vec![-180.0, -170.0, 170.0, 180.0, -180.0, -170.0, 170.0, 180.0]
        );
        assert_eq!(
            merged.latitudes,
            vec![10.0, 10.0, 10.0, 10.0, 20.0, 20.0, 20.0, 20.0]
        );
        assert_eq!(
            merged.fields[&key],
            vec![5.0, 7.0, 1.0, 3.0, 6.0, 8.0, 2.0, 4.0]
        );
        // A single grid passes through untouched.
        let mut single = GfsGrid::new(vec![0.0], vec![100.0]);
        single.insert(key, vec![42.0]).unwrap();
        assert_eq!(merge_terrain_grids(vec![single]).unwrap().fields.len(), 1);
    }

    fn city_catalog() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../resources/app-data/cities/china-city-catalog.json")
    }

    /// Product contract: every generated regional grid uses 0.25°, and the
    /// China production domain must contain exactly 36,685 points.
    #[test]
    fn generated_regional_spacing_is_pinned_to_product_grid() {
        let config = Config {
            gfs_cache_dir: PathBuf::from("/tmp/gfs"),
            ecmwf_cache_dir: PathBuf::from("/tmp/ecmwf"),
            cams_cache_dir: None,
            timezone_manifest: None,
            city_catalog_path: city_catalog(),
        };
        let east_asia = json!({"regions": ["east-asia"], "model_source": "gfs"});
        assert_eq!(generated_regional_spacing(&east_asia).unwrap(), 0.25);
        let points = generated(&east_asia, &config).unwrap();
        assert_eq!(points.len(), 193 * 369);

        let china = json!({"regions": ["china"], "model_source": "gfs"});
        assert_eq!(generated_regional_spacing(&china).unwrap(), 0.25);
        let points = generated(&china, &config).unwrap();
        assert_eq!(points.len(), CHINA_PRODUCTION_POINT_COUNT);

        let custom = json!({
            "regions": ["custom"],
            "custom_bounds": {"west": 119.5, "east": 120.5, "north": 30.75, "south": 29.75},
        });
        assert_eq!(generated_regional_spacing(&custom).unwrap(), 0.25);
        assert_eq!(generated(&custom, &config).unwrap().len(), 5 * 5);
        // Plan-only callers may request coarse grids; the product generator
        // refuses anything but the 0.25° contract.
        assert_eq!(
            generated_regional_spacing(&json!({"spacing_deg": 2.0})).unwrap(),
            2.0
        );
        assert!(generated_regional_spacing(&json!({"spacing_deg": 0.1})).is_err());
        assert!(generated_regional_spacing(&json!({"spacing_deg": 12.0})).is_err());
    }

    /// Forecast contexts (fast=false) must refuse coarse generated grids;
    /// warmup planning contexts (fast=true) keep the legacy coarse option.
    #[test]
    fn forecast_context_pins_generated_grid_to_quarter_degree() {
        let dir = tempfile::tempdir().unwrap();
        let config = Config {
            gfs_cache_dir: dir.path().join("gfs"),
            ecmwf_cache_dir: dir.path().join("ecmwf"),
            cams_cache_dir: Some(dir.path().join("cams")),
            timezone_manifest: None,
            city_catalog_path: city_catalog(),
        };
        let payload = json!({
            "target_date": "2026-11-12", "phenomenon": "dusk", "model_source": "gfs",
            "regions": ["east-asia"], "spacing_deg": 2.0,
        });
        let error = resolve(Input {
            payload: payload.clone(),
            regional: true,
            fast: false,
            config: Config {
                gfs_cache_dir: config.gfs_cache_dir.clone(),
                ecmwf_cache_dir: config.ecmwf_cache_dir.clone(),
                cams_cache_dir: config.cams_cache_dir.clone(),
                timezone_manifest: config.timezone_manifest.clone(),
                city_catalog_path: config.city_catalog_path.clone(),
            },
            now_utc: Some("2026-11-12T00:00:00Z".into()),
        })
        .unwrap_err();
        assert!(error.contains("spacing_deg=0.25"), "{error}");
        let plan = resolve(Input {
            payload,
            regional: true,
            fast: true,
            config,
            now_utc: Some("2026-11-12T00:00:00Z".into()),
        })
        .expect("warmup plan keeps coarse planning grid");
        let rows = plan["points"].as_array().unwrap();
        // lat 12..=60 step 2 -> 25 rows; lon 63..=155 step 2 -> 47 columns.
        assert_eq!(rows.len(), 25 * 47);
    }

    /// Regression for the multi-region EC terrain prepass: the east-asia +
    /// custom envelope spans ~115k native cells, and the full basic catalog
    /// decode is unnecessary for terrain sampling. The terrain-only decode
    /// must sample exactly the same elevations and let resolve() finalize the
    /// event plan (terrain_pending=false) from cached bundles.
    #[test]
    fn ec_regional_terrain_only_decode_matches_full_catalog() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
        let cache = root.join(".cache/ecmwf");
        let cycle = Utc.with_ymd_and_hms(2026, 11, 12, 12, 0, 0).unwrap();
        let bundle = ecmwf_data::path(&cache, cycle, 21);
        if !bundle.is_file() {
            // Real-data regression only runs where the workspace cache exists.
            return;
        }
        let codes = EcCodes::load().expect("eccodes");
        let bbox = [98.5, 141.5, 49.0, 11.5];
        let area_bbox = ec_regional_model_bbox(bbox, DEFAULT_RADIUS_KM).expect("area bbox");
        let terrain = ecmwf::decode_basic_terrain(
            &codes,
            &bundle,
            Some(area_bbox),
            Some(&iso(cycle)),
            Some(21),
            4096,
            5_000_000,
        )
        .expect("terrain-only decode");
        assert_eq!(terrain.fields.len(), 1);
        let full = ecmwf::decode_basic(
            &codes,
            &bundle,
            Some(area_bbox),
            Some(&iso(cycle)),
            Some(21),
            4096,
            5_000_000,
        )
        .expect("full decode");
        let (full_grid, _) = full.native_grid().expect("native grid");
        assert_eq!(terrain.latitudes.len(), full_grid.latitudes.len());
        let sample = |grid: &crate::native::gfs::GfsGrid| -> Vec<f64> {
            let mut points = vec![
                Point {
                    name: "corner".into(),
                    lat: 12.0,
                    lon: 63.0,
                    elevation_m: 0.,
                    timezone_name: "Asia/Shanghai".into(),
                    source: "model-grid".into(),
                },
                Point {
                    name: "杭州".into(),
                    lat: 30.2741,
                    lon: 120.1551,
                    elevation_m: 0.,
                    timezone_name: "Asia/Shanghai".into(),
                    source: "model-grid".into(),
                },
            ];
            apply_terrain(grid, &mut points).expect("sample terrain");
            points.iter().map(|p| p.elevation_m).collect()
        };
        assert_eq!(sample(&terrain), sample(&full_grid));
    }

    /// End-to-end context regression for the click-test regional payload:
    /// east-asia + custom must resolve a final EC plan (terrain applied,
    /// bracket-deduplicated evaluation times inside the EC plan limit).
    #[test]
    fn ec_multi_region_context_finalizes_from_cached_bundles() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
        let cache = root.join(".cache/ecmwf");
        let cycle = Utc.with_ymd_and_hms(2026, 11, 12, 12, 0, 0).unwrap();
        if !ecmwf_data::path(&cache, cycle, 21).is_file() {
            return;
        }
        let input = Input {
            payload: json!({
                "custom_bounds": {"east": 120.5, "north": 30.75, "south": 29.75, "west": 119.5},
                "ecmwf_source": "ecmwf", "forecast_mode": "model", "model_source": "ec",
                "phenomenon": "dusk", "regions": ["east-asia", "custom"],
                "satellite_source": "off", "spacing_deg": 0.25,
                "target_date": "2026-11-13", "workers": 1
            }),
            regional: true,
            fast: false,
            config: Config {
                gfs_cache_dir: root.join(".cache/gfs"),
                ecmwf_cache_dir: cache,
                cams_cache_dir: Some(root.join(".cache/cams")),
                timezone_manifest: None,
                city_catalog_path: city_catalog(),
            },
            now_utc: None,
        };
        let ctx = resolve(input).expect("multi-region EC context");
        assert_eq!(ctx["terrain_pending"], json!(false));
        let times = ctx["times"].as_array().expect("times");
        assert!(!times.is_empty() && times.len() <= 1024);
        let hours = ctx["hours"].as_array().expect("hours");
        assert!(!hours.is_empty());
        let points = ctx["points"].as_array().expect("points");
        // Explicit 0.25° stays available for callers that opt into the dense
        // grid; the desktop default is now the adaptive (legacy-parity) one.
        assert!(
            points.len() > 50_000,
            "explicit 0.25 east-asia grid must be dense"
        );
        assert!(
            points
                .iter()
                .all(|p| p["elevation_m"].as_f64().unwrap().is_finite()),
            "terrain prepass must sample every grid point"
        );
        assert!(
            points
                .iter()
                .any(|p| p["elevation_m"].as_f64().unwrap().abs() > 1.0),
            "terrain prepass must apply real orography"
        );
    }

    #[test]
    fn ec_terrain_prepass_skips_missing_hours_and_decodes_forecast_step_surface() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
        let cache = root.join(".cache/ecmwf");
        let cycle = Utc.with_ymd_and_hms(2026, 11, 12, 12, 0, 0).unwrap();
        let bundle = ecmwf_data::path(&cache, cycle, 21);
        if !bundle.is_file() {
            // Real-data regression only runs where the workspace cache exists.
            return;
        }
        let input = Input {
            payload: json!({"ecmwf_source": "ecmwf"}),
            regional: true,
            fast: false,
            config: Config {
                gfs_cache_dir: root.join(".cache/gfs"),
                ecmwf_cache_dir: cache,
                cams_cache_dir: None,
                timezone_manifest: None,
                city_catalog_path: city_catalog(),
            },
            now_utc: None,
        };
        let mut points = vec![Point {
            name: "杭州".into(),
            lat: 30.2741,
            lon: 120.1551,
            elevation_m: 8.,
            timezone_name: "Asia/Shanghai".into(),
            source: "model-grid".into(),
        }];
        // f018/f019 are absent from this cache; f021 carries surface z at the
        // forecast step, which the strict decoder used to reject.
        let hours: BTreeSet<u32> = [18, 19, 20, 21, 22, 23].into_iter().collect();
        let bbox = [119.5, 120.5, 30.75, 29.75];
        let ok = terrain(&input, "ec", cycle, &hours, bbox, bbox, None, &mut points)
            .expect("terrain prepass");
        assert!(ok, "prepass must resolve terrain from a later cached hour");
        assert!(points[0].elevation_m.is_finite());
        assert!((-500.0..=9000.0).contains(&points[0].elevation_m));
    }

    /// Regression for the GFS terrain prepass point budget: a regional East
    /// Asia file carries ~150 fields x ~25k points, so the full decode exceeds
    /// the 10M cumulative budget before reaching orography. The terrain-only
    /// decode must succeed on the same file.
    /// Regional batch evaluation must collapse to one representative per
    /// 3-hour bracket: a dense grid otherwise produces tens of thousands of
    /// distinct epochs and trips the EC plan's 1024-target limit.
    #[test]
    fn regional_batch_times_are_deduplicated() {
        // 0.25-degree grid rows across 6 degrees of latitude at one longitude:
        // dusk anchors differ by minutes, so exact-second dedup would leave
        // every epoch distinct while 3-hour brackets must collapse them.
        let mut points = Vec::new();
        for i in 0..25 {
            for j in 0..25 {
                points.push(Point {
                    name: format!("p{i}-{j}"),
                    lat: 28.0 + f64::from(i) * 0.25,
                    lon: 118.0 + f64::from(j) * 0.25,
                    elevation_m: 8.,
                    timezone_name: "Asia/Shanghai".into(),
                    source: "model-grid".into(),
                });
            }
        }
        let date = NaiveDate::from_ymd_opt(2026, 11, 13).unwrap();
        let rows = timeline(&points, date, false).expect("timeline");
        let raw: usize = rows
            .iter()
            .map(|r| {
                r.evaluation
                    .iter()
                    .zip(&r.valid)
                    .filter(|(_, v)| **v != 0)
                    .count()
            })
            .sum();
        let deduped = times(&points, date, false, true, false).expect("batch times");
        assert!(raw > 1024, "fixture must exceed the EC plan target limit");
        assert!(
            deduped.len() < raw / 10,
            "bracket dedup must collapse the grid epochs: raw={raw} deduped={}",
            deduped.len()
        );
        assert!(
            deduped.len() <= 1024,
            "batch times must fit the EC plan limit"
        );
        let brackets: std::collections::BTreeSet<i64> = deduped
            .iter()
            .map(|t| t.timestamp().div_euclid(10_800))
            .collect();
        assert!(
            deduped.len() <= 2 * brackets.len(),
            "at most two representatives (earliest+latest) per bracket"
        );
        assert!(deduped.windows(2).all(|w| w[0] <= w[1]), "sorted output");
        // The collapsed list must still span the full valid-sample range so
        // the required forecast hours cover the latest solar sample.
        let raw_min = rows
            .iter()
            .flat_map(|r| {
                r.evaluation
                    .iter()
                    .zip(&r.valid)
                    .filter(|(_, v)| **v != 0)
                    .map(|(e, _)| *e)
            })
            .fold(f64::INFINITY, f64::min);
        let raw_max = rows
            .iter()
            .flat_map(|r| {
                r.evaluation
                    .iter()
                    .zip(&r.valid)
                    .filter(|(_, v)| **v != 0)
                    .map(|(e, _)| *e)
            })
            .fold(f64::NEG_INFINITY, f64::max);
        let dedup_min = deduped.first().unwrap().timestamp() as f64;
        let dedup_max = deduped.last().unwrap().timestamp() as f64;
        assert!(
            dedup_min <= raw_min.floor() + 1.0,
            "earliest sample preserved"
        );
        assert!(
            dedup_max >= raw_max.floor() - 1.0,
            "latest sample preserved"
        );
    }

    /// Bracket dedup must not change any downstream requirement: EC source
    /// hours and CAMS bracket targets computed from the collapsed list equal
    /// those computed from every raw valid epoch.
    #[test]
    fn regional_bracket_dedup_preserves_model_requirements() {
        let points: Vec<Point> = (0..40)
            .map(|i| Point {
                name: format!("p{i}"),
                lat: 30.0 + f64::from(i) * 0.25,
                lon: 120.0,
                elevation_m: 8.,
                timezone_name: "Asia/Shanghai".into(),
                source: "model-grid".into(),
            })
            .collect();
        let date = NaiveDate::from_ymd_opt(2026, 11, 13).unwrap();
        let cycle = time_contract::parse_utc("2026-11-12T12:00:00Z").expect("cycle");
        let rows = timeline(&points, date, false).expect("timeline");
        let mut raw = Vec::new();
        for row in rows {
            for (epoch, valid) in row.evaluation.into_iter().zip(row.valid) {
                if valid != 0 {
                    raw.push(assessment::from_epoch(epoch).expect("epoch"));
                }
            }
        }
        let deduped = times(&points, date, false, true, false).expect("batch times");
        assert!(deduped.len() < raw.len());
        assert_eq!(
            required("ec", cycle, &raw).expect("raw EC hours"),
            required("ec", cycle, &deduped).expect("deduped EC hours")
        );
        assert_eq!(
            required("gfs", cycle, &raw).expect("raw GFS hours"),
            required("gfs", cycle, &deduped).expect("deduped GFS hours")
        );
        let cams_set = |times: &[DateTime<Utc>]| -> std::collections::BTreeSet<DateTime<Utc>> {
            let mut out = std::collections::BTreeSet::new();
            for t in times {
                let (left, right, _) =
                    crate::cams::bracket(&time_contract::iso(*t)).expect("bracket");
                out.extend([left, right]);
            }
            out
        };
        assert_eq!(cams_set(&raw), cams_set(&deduped));
    }

    #[test]
    fn gfs_terrain_decode_handles_regional_point_budget() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
        let bundle = root.join(".cache/gfs/2026-11-13/00Z/0p25/17bc6ec77472cfdc62b4.grib2");
        if !bundle.is_file() {
            // Real-data regression only runs where the workspace cache exists.
            return;
        }
        let codes = EcCodes::load().expect("eccodes");
        let grid = codes
            .decode_gfs_terrain(&bundle, 4096, 5_000_000)
            .expect("terrain-only decode");
        assert!(grid
            .fields
            .contains_key(&("surface".into(), 0, "orog".into())));
        assert_eq!(grid.fields.len(), 1);
        // The frame subset decode must sample the same terrain as the full
        // decode while staying inside the cumulative point budget.
        let bbox = [98.5, 141.5, 49.0, 11.5];
        let subset = codes
            .decode_gfs_frame_subset(&bundle, 4096, 5_000_000, None, bbox)
            .expect("subset frame decode");
        let full = codes
            .decode_gfs_file(&bundle, 4096, 5_000_000)
            .expect("full frame decode");
        let sample = |grid: &GfsGrid| -> f64 {
            let mut points = vec![Point {
                name: "杭州".into(),
                lat: 30.2741,
                lon: 120.1551,
                elevation_m: 0.,
                timezone_name: "Asia/Shanghai".into(),
                source: "model-grid".into(),
            }];
            apply_terrain(grid, &mut points).expect("sample terrain");
            points[0].elevation_m
        };
        let terrain_only = sample(&grid);
        assert!((-500.0..=9000.0).contains(&terrain_only));
        assert_eq!(terrain_only, sample(&subset));
        assert_eq!(terrain_only, sample(&full));
    }

    #[test]
    fn gfs_sync_envelope_covers_every_point_domain_inside_anchor() {
        let bounds = region("china").expect("china region").2;
        let env =
            gfs_data::canonical_bbox(gfs_point_domain_envelope(bounds, DEFAULT_RADIUS_KM), "0p25")
                .expect("canonical sync envelope");
        for (lat, lon) in [
            (30.2741, 120.1551),
            (54.0, 136.0),
            (18.0, 73.0),
            (45.0, 90.0),
        ] {
            let p = Point {
                name: "p".into(),
                lat,
                lon,
                elevation_m: 0.,
                timezone_name: "Asia/Shanghai".into(),
                source: "test".into(),
            };
            let dom = gfs_data::canonical_bbox(
                union_boxes(&[p], source_radius(DEFAULT_RADIUS_KM)).expect("point domain"),
                "0p25",
            )
            .expect("canonical point domain");
            assert!(
                env[0] <= dom[0] && env[1] >= dom[1] && env[2] >= dom[2] && env[3] <= dom[3],
                "envelope {env:?} must cover point domain {dom:?} at ({lat},{lon})"
            );
        }
    }

    #[test]
    fn city_catalog_points_are_deterministic_and_region_scoped() {
        let all = city_catalog_points(&city_catalog(), None, &[]).expect("bundled city catalog");
        assert_eq!(all.len(), 695);
        let names = all
            .iter()
            .map(|row| row["name"].as_str().unwrap())
            .collect::<BTreeSet<_>>();
        assert_eq!(names.len(), 695);
        assert!(all
            .iter()
            .all(|row| row["elevation_source"] == "model-grid"));

        let shanghai = city_catalog_points(&city_catalog(), None, &[[120., 122., 32., 30.]])
            .expect("scoped city catalog");
        assert!(!shanghai.is_empty());
        assert!(shanghai.iter().all(|row| {
            let lat = row["lat"].as_f64().unwrap();
            let lon = row["lon"].as_f64().unwrap();
            (30. - 1e-6..=32. + 1e-6).contains(&lat) && (120. - 1e-6..=122. + 1e-6).contains(&lon)
        }));
    }

    #[test]
    fn regional_custom_context_uses_a_valid_cams_area() {
        let dir = tempfile::tempdir().unwrap();
        let payload = json!({
            "name":"Hangzhou test area","lat":30.2741,"lon":120.1551,"elevation_m":8,
            "timezone_name":"Asia/Shanghai","target_date":"2026-11-12",
            "phenomenon":"dusk","model_source":"gfs","forecast_mode":"model",
            "satellite_source":"off","regions":["custom"],
            "custom_bounds":{
                "west":120.0,"east":122.0,"north":32.0,"south":30.0
            },"spacing_deg":0.25,
            "ecmwf_source":"ecmwf",
        });
        for model in ["gfs", "ec"] {
            let mut request = payload.clone();
            request["model_source"] = json!(model);
            let context = resolve(Input {
                payload: request,
                regional: true,
                fast: true,
                config: Config {
                    gfs_cache_dir: dir.path().join("gfs"),
                    ecmwf_cache_dir: dir.path().join("ecmwf"),
                    cams_cache_dir: Some(dir.path().join("cams")),
                    timezone_manifest: Some(
                        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                            .join("../resources/app-data/timezones/manifest.json"),
                    ),
                    city_catalog_path: city_catalog(),
                },
                now_utc: Some("2026-11-12T00:00:00Z".into()),
            })
            .expect(model);
            eprintln!("{model} area={}", context["area"]);
            crate::cams_data::area_identity(context["area"].clone()).expect(model);
            let points = context["points"].as_array().expect("regional points");
            assert!(!points.is_empty());
            assert!(points.iter().all(|point| {
                point["timezone_name"]
                    .as_str()
                    .is_some_and(|value| !value.is_empty())
            }));
            assert!(points
                .iter()
                .any(|point| point["timezone_name"] == "Asia/Shanghai"));
        }
    }

    /// A single explicit observer makes the EC regional point union degenerate
    /// (north == south and west == east). The plan bbox must still be a real
    /// 2D grid: the EC proxy cannot infer spacing from a single cell, and the
    /// CAMS model-grid coverage gate needs >=2 rows/columns. The degenerate
    /// tight envelope therefore falls back to the buffered regional domain.
    /// Regression for the v1.0.0 EC regional replay failure.
    #[test]
    fn ec_regional_single_explicit_point_bbox_is_two_dimensional() {
        let dir = tempfile::tempdir().unwrap();
        let payload = json!({
            "name":"Beijing-region","lat":39.9,"lon":116.4,"elevation_m":50.0,
            "timezone_name":"Asia/Shanghai","target_date":"2026-11-14",
            "phenomenon":"dusk","model_source":"ec","forecast_mode":"model",
            "satellite_source":"off","regions":["custom"],
            "custom_bounds":{"west":116.0,"east":117.0,"north":40.5,"south":39.5},
            "custom_timezone_name":"Asia/Shanghai","spacing_deg":0.25,
            "ecmwf_source":"ecmwf",
            "points":[{"name":"Beijing","lat":39.9,"lon":116.4,"elevation_m":50.0,
                "timezone_name":"Asia/Shanghai","elevation_source":"observer"}],
        });
        let context = resolve(Input {
            payload,
            regional: true,
            fast: true,
            config: Config {
                gfs_cache_dir: dir.path().join("gfs"),
                ecmwf_cache_dir: dir.path().join("ecmwf"),
                cams_cache_dir: Some(dir.path().join("cams")),
                timezone_manifest: Some(
                    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                        .join("../resources/app-data/timezones/manifest.json"),
                ),
                city_catalog_path: city_catalog(),
            },
            now_utc: Some("2026-11-14T00:00:00Z".into()),
        })
        .expect("ec regional context");
        let bbox = context["bbox"].as_array().expect("bbox");
        let north = bbox[2].as_f64().unwrap();
        let south = bbox[3].as_f64().unwrap();
        let west = bbox[0].as_f64().unwrap();
        let east = bbox[1].as_f64().unwrap();
        assert!(north > south, "EC regional bbox must span >=2 latitude rows: {bbox:?}");
        assert!(east != west, "EC regional bbox must span >=2 longitude columns: {bbox:?}");
        // The buffered domain still contains the observer.
        assert!(west <= 116.4 && 116.4 <= east || west > east, "observer longitude inside bbox");
        assert!(south <= 39.9 && 39.9 <= north, "observer latitude inside bbox");
        // The CAMS coverage axis derived from this bbox must have >=2 rows.
        let prepared = crate::gfs_data::canonical_bbox(
            [west, east, north, south],
            "0p25",
        )
        .unwrap();
        let (lats, lons) = crate::cams_cache::model_axes(prepared);
        assert!(lats.len() >= 2, "coverage needs >=2 latitude rows: {lats:?}");
        assert!(lons.len() >= 2, "coverage needs >=2 longitude columns: {lons:?}");
    }
}
