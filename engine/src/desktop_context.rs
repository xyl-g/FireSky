//! Raw desktop model request planning. No downloads or Python callbacks.
use crate::{
    assessment, ecmwf, ecmwf_data, ecmwf_point, gfs_data, grib::EcCodes, input_plan, request_file,
    time_contract, timezone_lookup,
};
use chrono::{DateTime, Duration, NaiveDate, TimeZone, Timelike, Utc};
use firesky_native::{gfs::GfsGrid, timeline};
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
const REGIONS: [(&str, &str, [f64; 4]); 10] = [
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
fn longitude_timezone(lon: f64) -> String {
    let offset = (lon / 15.0).round() as i32;
    if offset >= 0 {
        format!("Etc/GMT-{}", offset)
    } else {
        format!("Etc/GMT+{}", -offset)
    }
}
fn contains(b: [f64; 4], p: &Point) -> bool {
    // Owning bbox_contains allows a microdegree at the boundary.
    p.lon >= b[0] - 1e-6 && p.lon <= b[1] + 1e-6 && p.lat <= b[2] + 1e-6 && p.lat >= b[3] - 1e-6
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
            let bounds = payload["custom_bounds"]
                .as_object()
                .ok_or("custom_bounds must be an object")?;
            let b = [
                number(bounds.get("west").unwrap_or(&Value::Null), -180., 180.)?,
                number(bounds.get("east").unwrap_or(&Value::Null), -180., 180.)?,
                number(bounds.get("north").unwrap_or(&Value::Null), -90., 90.)?,
                number(bounds.get("south").unwrap_or(&Value::Null), -90., 90.)?,
            ];
            if b[0] >= b[1] || b[3] >= b[2] {
                return Err("invalid custom bounds".into());
            }
            ("CUSTOM", b, Some(zone(&payload["custom_timezone_name"])?))
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
        if rows.len() == 1 && name == "china" && code(payload, "gfs_resolution", "0p25") != "0p25" {
            return Err("China production grid requires 0p25 resolution".into());
        }
        let mut lat = b[3];
        while lat <= b[2] + 1e-9 {
            let mut lon = b[0];
            while lon <= b[1] + 1e-9 {
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
                lon += 0.25;
            }
            lat += 0.25;
        }
    }
    let indices: Vec<_> = points
        .iter()
        .enumerate()
        .filter_map(|(i, p)| p.timezone_name.is_empty().then_some(i))
        .collect();
    if !indices.is_empty() {
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
                    Err(_) => {
                        for &i in chunk {
                            points[i].timezone_name = longitude_timezone(points[i].lon);
                        }
                    }
                }
            }
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
        return rows
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
            .collect();
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
        if b[0] > b[1] {
            return Err("source domain crosses the dateline".into());
        }
        result = [
            result[0].min(b[0]),
            result[1].max(b[1]),
            result[2].max(b[2]),
            result[3].min(b[3]),
        ];
    }
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
    let bbox = if model == "ec" && explicit && forced.is_empty() {
        selected = None;
        let lon_min = points.iter().map(|p| p.lon).fold(f64::INFINITY, f64::min);
        let lon_max = points
            .iter()
            .map(|p| p.lon)
            .fold(f64::NEG_INFINITY, f64::max);
        if lon_max - lon_min >= 180. {
            return Err("explicit EC envelope crosses the dateline".into());
        }
        [
            lon_min,
            lon_max,
            points
                .iter()
                .map(|p| p.lat)
                .fold(f64::NEG_INFINITY, f64::max),
            points.iter().map(|p| p.lat).fold(f64::INFINITY, f64::min),
        ]
    } else if selected.as_deref() == Some("china") && model == "gfs" {
        gfs_data::canonical_bbox(
            union_boxes(&corners(region("china").unwrap().2), source_radius(radius))?,
            "0p25",
        )?
    } else if selected.is_none() {
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
fn source_radius(radius: f64) -> f64 {
    (radius + (radius + 400.).min(1000.)).max(1000.)
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
        Ok(result)
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
    if ec_point && !input.fast {
        let ec_source = input.payload["ecmwf_source"].as_str().unwrap_or("ecmwf");
        if !["ecmwf", "aws", "google"].contains(&ec_source) {
            return Err("ecmwf_source must be ecmwf, aws or google".into());
        }
        return ecmwf_data::snapshot_cycle(
            &ecmwf_data::Config {
                cache_dir: input.config.ecmwf_cache_dir.clone(),
                source: ec_source.into(),
                timeout_seconds: 8.,
                ..Default::default()
            },
            None,
            anchor.ok_or("missing EC point anchor")?,
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
                let url = gfs_data::build_url(cycle, hour, Some(bbox), "0p25", false)?;
                let Some(path) = client.find(cycle, hour, Some(bbox), Some(&url))? else {
                    return Ok(None);
                };
                // GFS's owning cache-only preflight does not validate the GRIB
                // cycle here; readiness/execution performs that validation.
                Ok(Some(codes.decode_gfs_file(&path, 4096, 5_000_000)?))
            };
            if let Ok(Some(grid)) = attempt() {
                apply_terrain(&grid, points)?;
                return Ok(true);
            }
        }
    } else {
        for &hour in hours {
            let path = input.config.ecmwf_cache_dir.join(format!(
                "ifs-basic-v2-{}-f{hour:03}.grib2",
                cycle.format("%Y%m%d%H")
            ));
            if path.is_file() && path.metadata().is_ok_and(|m| m.len() > 0) {
                let tensor = ecmwf::decode_basic(
                    &codes,
                    &path,
                    Some(area_bbox),
                    Some(&iso(cycle)),
                    Some(i64::from(hour)),
                    4096,
                    5_000_000,
                )?;
                let (grid, _) = tensor.native_grid()?;
                apply_terrain(&grid, points)?;
                return Ok(true);
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
        input.payload.get("radius_km").unwrap_or(&json!(1000.)),
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
        if bbox[0] > bbox[1] {
            return Err("EC prepared bbox crosses the dateline".into());
        }
        union_boxes(&corners(bbox), ecmwf_point::domain(radius)?[2])?
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
        let hours = required(&model, cycle, &evaluation)?;
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
