//! Automatic GFS event-hour/terrain planning and local CAMS file selection.
use crate::native::timeline::{self, Point};
use crate::{
    assessment::{self, Elevation, Frame, Observer, OpticalFile},
    astronomy, cams, cams_cache, cams_data,
    gfs_data::{self, Config, GfsClient},
    grib::EcCodes,
    request_file,
    tensor_bundle::{source_identity, SourceIdentity},
    time_contract,
};
use chrono::{DateTime, Duration, NaiveDate, TimeZone, Timelike, Utc};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
    time::{Instant, SystemTime},
};

fn threshold() -> usize {
    64
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    date: String,
    phenomenon: astronomy::Phenomenon,
    points: Vec<Observer>,
    radius_km: f64,
    cycle_utc: Option<String>,
    now_utc: Option<String>,
    gfs: Config,
    cams_cache_dir: PathBuf,
    cams_download: Option<crate::ads::Config>,
    bbox: Option<[f64; 4]>,
    #[serde(default)]
    allow_gfs_download: bool,
    #[serde(default = "threshold")]
    batch_min_points: usize,
    #[serde(default)]
    derived_cache: Option<crate::derived_cache::Config>,
    #[serde(default)]
    aerosol_source: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    pub path: PathBuf,
}

pub fn execute(params: Params) -> Result<Value, String> {
    Ok(prepare(&params.path, false)?.summary)
}

pub fn forecast(path: &Path, output: &Path) -> Result<PathBuf, String> {
    forecast_with_progress(path, output, &|_: u128| {})
}

pub fn forecast_with_progress(
    path: &Path,
    output: &Path,
    progress: &(dyn Fn(u128) + Sync),
) -> Result<PathBuf, String> {
    let started = Instant::now();
    let prepared = prepare(path, true)?;
    if prepared.summary["inputs_located"] != true {
        return Err(format!(
            "GFS/CAMS event inputs are incomplete: {}",
            prepared.summary
        ));
    }
    assessment::planned_point_to_files(
        path,
        output,
        prepared.input,
        prepared.source,
        prepared.summary,
        started,
        progress,
    )
}

struct Prepared {
    input: assessment::Input,
    source: SourceIdentity,
    summary: Value,
}

struct CamsPlan {
    areas: Vec<[f64; 4]>,
    selected: BTreeMap<DateTime<Utc>, Vec<cams_cache::Selection>>,
    download_reports: Vec<Value>,
    cache_hits: usize,
}

fn cams_plan(
    root: &Path,
    area: [f64; 4],
    targets: &BTreeSet<DateTime<Utc>>,
    download: Option<&crate::ads::Config>,
    coverage: Option<&cams_cache::ModelCoverage<'_>>,
    source: cams_cache::AerosolSource,
) -> Result<CamsPlan, String> {
    let areas = cams_data::split_areas(area)?;
    let mut selected_by_time = BTreeMap::<DateTime<Utc>, Vec<cams_cache::Selection>>::new();
    let mut download_reports = Vec::new();
    let mut cache_hits = 0;
    for sub_area in &areas {
        let (part_west, part_east) = if sub_area[1] <= sub_area[3] {
            (sub_area[1], sub_area[3])
        } else {
            (sub_area[1], sub_area[3] + 360.0)
        };
        let part_lons: Vec<f64> = coverage
            .map(|c| {
                c.lons
                    .iter()
                    .copied()
                    .filter(|&lon| {
                        let unwrapped = if lon < part_west { lon + 360.0 } else { lon };
                        unwrapped >= part_west && unwrapped <= part_east
                    })
                    .collect()
            })
            .unwrap_or_default();
        let part_coverage = coverage.map(|c| cams_cache::ModelCoverage {
            lats: c.lats,
            lons: &part_lons,
        });
        let key = cams_cache::area_key(*sub_area)?;
        let mut selected = cams_cache::select_with_key_for_source(
            root,
            *sub_area,
            targets,
            &key,
            part_coverage.as_ref(),
            &[],
            source,
        )?;
        // Wrapped groups share the physical +/-180 boundary column: retry
        // missing targets with the sibling part's newest tile so the seam
        // bridge can close the (179.6, 180] gap before any ADS download.
        if part_coverage.is_some() && selected.len() < targets.len() && areas.len() > 1 {
            let (bounds, _, _) = cams_data::area_identity(serde_json::json!(sub_area))?;
            let part = cams_data::Area::from_bounds(bounds);
            let siblings = cams_data::sibling_tiles_for_source(
                root,
                &part,
                &areas,
                &targets.iter().copied().collect::<Vec<_>>(),
                true,
                source,
            )?;
            for target in targets {
                if selected.contains_key(target) {
                    continue;
                }
                let empty = Vec::new();
                let group_siblings = siblings.get(target).unwrap_or(&empty);
                if let Some(hit) = cams_cache::select_with_key_for_source(
                    root,
                    *sub_area,
                    &[*target].into_iter().collect(),
                    &key,
                    part_coverage.as_ref(),
                    group_siblings,
                    source,
                )?
                .remove(target)
                {
                    selected.insert(*target, hit);
                }
            }
        }
        cache_hits += selected.len();
        if selected.len() < targets.len() {
            if let Some(config) = download {
                download_reports.push(cams_data::resolve_with_source(
                    root,
                    *sub_area,
                    targets.iter().copied().collect(),
                    config.clone(),
                    Some(source.as_str().to_owned()),
                )?);
                selected = cams_cache::select_with_key_for_source(
                    root,
                    *sub_area,
                    targets,
                    &key,
                    part_coverage.as_ref(),
                    &[],
                    source,
                )?;
            }
        }
        for target in targets {
            if let Some(value) = selected.get(target) {
                selected_by_time
                    .entry(*target)
                    .or_default()
                    .push(value.clone());
            }
        }
    }
    Ok(CamsPlan {
        areas,
        selected: selected_by_time,
        download_reports,
        cache_hits,
    })
}

fn points(input: &Input, elevations: &[f64]) -> Result<Vec<Point>, String> {
    input
        .points
        .iter()
        .zip(elevations)
        .map(|(p, &elevation_m)| {
            Ok(Point {
                lat: p.lat,
                lon: p.lon,
                elevation_m,
                timezone: p
                    .timezone_name
                    .parse()
                    .map_err(|_| format!("unknown IANA timezone: {}", p.timezone_name))?,
            })
        })
        .collect()
}

fn bbox(input: &Input) -> Result<[f64; 4], String> {
    if let Some(value) = input.bbox {
        let value = gfs_data::canonical_bbox(value, "0p25")?;
        if value[0] == value[1] || (value[0] == 180.0 && value[1] == -180.0) || value[3] >= value[2]
        {
            return Err("GFS planning requires a nonempty bbox".into());
        }
        return Ok(value);
    }
    let radius = (input.radius_km + (input.radius_km + 400.0).min(1000.0)).max(1000.0);
    let mut longitude_intervals = Vec::with_capacity(input.points.len());
    let mut north = -90.0_f64;
    let mut south = 90.0_f64;
    let normalize = |lon: f64| {
        let value = (lon + 180.0).rem_euclid(360.0) - 180.0;
        if value == -180.0 && lon > 0.0 {
            180.0
        } else {
            value
        }
    };
    for p in &input.points {
        let lat_pad = radius / 111.0;
        let lon_pad = radius / (111.0 * p.lat.to_radians().cos()).max(25.0);
        let west = normalize(p.lon - lon_pad);
        let east = normalize(p.lon + lon_pad);
        longitude_intervals.push((west, east));
        north = north.max((p.lat + lat_pad).min(90.0));
        south = south.min((p.lat - lat_pad).max(-90.0));
    }
    let [west, east] = union_longitudes(longitude_intervals);
    gfs_data::canonical_bbox([west, east, north, south], "0p25")
}

fn longitude_ranges(west: f64, east: f64) -> Vec<(f64, f64)> {
    if west <= east {
        vec![(west, east)]
    } else {
        vec![(west, 180.0), (-180.0, east)]
    }
}

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
        return [-180.0, 180.0];
    }
    if merged.len() == 1 && merged[0].0 <= -180.0 + 1e-9 && merged[0].1 >= 180.0 - 1e-9 {
        return [-180.0, 180.0];
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
    [west, if east > 180.0 { east - 360.0 } else { east }]
}

pub(crate) fn scalar_times(
    point: Point,
    date: NaiveDate,
    rising: bool,
) -> Result<Vec<DateTime<Utc>>, String> {
    let events = astronomy::events(astronomy::EventsParams {
        point: astronomy::Point {
            lat: point.lat,
            lon: point.lon,
            elevation_m: point.elevation_m,
        },
        date: date.to_string(),
        timezone_name: point.timezone.to_string(),
        phenomenon: if rising {
            astronomy::Phenomenon::Dawn
        } else {
            astronomy::Phenomenon::Dusk
        },
    })?;
    let anchor = time_contract::parse_utc(
        events["anchor_time_utc"]
            .as_str()
            .ok_or("missing event anchor")?,
    )?;
    let local = anchor.with_timezone(&point.timezone).naive_local();
    let mut times = BTreeSet::new();
    let (first, last, dense_start, dense_end) = if rising {
        (-75, 20, -45, 20)
    } else {
        (-20, 75, -20, 45)
    };
    for offset in first..=last {
        if !(dense_start..=dense_end).contains(&offset) && offset % 5 != 0 {
            continue;
        }
        let local = local
            .checked_add_signed(Duration::minutes(offset))
            .ok_or("event local clock overflow")?;
        let time = assessment::from_epoch(
            timeline::local_epoch(local, point.timezone).map_err(|e| e.to_string())?,
        )?;
        let elevation = astronomy::position(
            astronomy::Point {
                lat: point.lat,
                lon: point.lon,
                elevation_m: point.elevation_m,
            },
            time,
        )[0];
        if (-7.5..=1.5).contains(&elevation) {
            times.insert(time);
        }
    }
    times.insert(anchor);
    Ok(times.into_iter().collect())
}

struct Requirements {
    hours: BTreeSet<u32>,
    cams: BTreeSet<DateTime<Utc>>,
    first: Option<DateTime<Utc>>,
    last: Option<DateTime<Utc>>,
}
impl Requirements {
    fn new() -> Self {
        Self {
            hours: BTreeSet::new(),
            cams: BTreeSet::new(),
            first: None,
            last: None,
        }
    }
    fn insert(&mut self, time: DateTime<Utc>, offset: f64, batch: bool) -> Result<(), String> {
        self.first = Some(self.first.map_or(time, |v| v.min(time)));
        self.last = Some(self.last.map_or(time, |v| v.max(time)));
        let offset = if batch { offset } else { offset.max(0.0) };
        let cadence = if offset > 120.0 { 3.0 } else { 1.0 };
        let index = offset / cadence;
        let nearest = index.round_ties_even();
        let (lower, upper) = if batch && (index - nearest).abs() <= 1e-7 {
            (nearest * cadence, nearest * cadence)
        } else {
            (index.floor() * cadence, index.ceil() * cadence)
        };
        if lower < 0.0 {
            return Err("model cycle is later than valid regional solar samples".into());
        }
        if upper > 384.0 {
            return Err("planned GFS hour exceeds 384".into());
        }
        self.hours.extend([lower as u32, upper as u32]);
        let (left, right, _) = cams::bracket(&time_contract::iso(time))?;
        self.cams.extend([left, right]);
        if self.hours.len() > 64 || self.cams.len() > 256 {
            return Err("planned catalog exceeds 64 GFS or 256 CAMS files".into());
        }
        Ok(())
    }
}

fn requirements(
    input: &Input,
    elevations: &[f64],
    date: NaiveDate,
    cycle: DateTime<Utc>,
) -> Result<Requirements, String> {
    let rising = matches!(input.phenomenon, astronomy::Phenomenon::Dawn);
    let points = points(input, elevations)?;
    let batch = points.len() >= input.batch_min_points;
    let mut result = Requirements::new();
    if batch {
        // Keep the production batch threshold, but bound temporary timeline
        // rows rather than retaining every observer's twenty solar samples.
        for chunk in points.chunks(1024) {
            let prepared =
                timeline::prepare(chunk, date, rising, None).map_err(|e| e.to_string())?;
            for row in prepared.rows {
                for (epoch, valid) in row.evaluation.into_iter().zip(row.valid) {
                    if valid == 0 {
                        continue;
                    }
                    result.insert(
                        assessment::from_epoch(epoch)?,
                        (epoch - epoch_seconds(cycle)) / 3600.0,
                        true,
                    )?;
                }
            }
        }
    } else {
        for point in points {
            for time in scalar_times(point, date, rising)? {
                let offset = (time - cycle)
                    .num_microseconds()
                    .ok_or("event time span overflow")? as f64
                    / 3_600_000_000.0;
                result.insert(time, offset, false)?;
            }
        }
    }
    if result.hours.is_empty() {
        return Err("event window has no model hours".into());
    }
    Ok(result)
}

fn epoch_seconds(time: DateTime<Utc>) -> f64 {
    time.timestamp() as f64 + f64::from(time.timestamp_subsec_micros()) / 1e6
}

fn cycle(input: &Input, elevations: &[f64], date: NaiveDate) -> Result<DateTime<Utc>, String> {
    if let Some(value) = &input.cycle_utc {
        return time_contract::parse_utc(value);
    }
    let now = input
        .now_utc
        .as_deref()
        .map(time_contract::parse_utc)
        .transpose()?
        .unwrap_or_else(|| DateTime::<Utc>::from(SystemTime::now()));
    let mut first = now;
    let rising = matches!(input.phenomenon, astronomy::Phenomenon::Dawn);
    for chunk in points(input, elevations)?.chunks(1024) {
        let timeline = timeline::prepare(chunk, date, rising, None).map_err(|e| e.to_string())?;
        for row in timeline.rows {
            if row.anchor.is_finite() {
                first = first.min(assessment::from_epoch(row.anchor)?);
            }
        }
    }
    let adjusted = first
        .checked_sub_signed(Duration::hours(4))
        .ok_or("cycle date underflow")?;
    time_contract::checked(
        Utc.from_utc_datetime(
            &adjusted
                .date_naive()
                .and_hms_opt(adjusted.hour() / 6 * 6, 0, 0)
                .ok_or("invalid cycle hour")?,
        ),
    )
}

fn located(
    client: &GfsClient,
    input: &Input,
    cycle: DateTime<Utc>,
    hour: u32,
    bbox: [f64; 4],
) -> Result<Option<Vec<PathBuf>>, String> {
    let rows = client.resolve_parts(cycle, hour, Some(bbox), false, input.allow_gfs_download)?;
    if rows.is_empty() {
        return Ok(None);
    }
    let mut paths = Vec::new();
    for row in rows {
        paths.push(
            row["path"]
                .as_str()
                .map(PathBuf::from)
                .ok_or("invalid resolved GFS path")?,
        );
    }
    Ok(Some(paths))
}

fn prepare(path: &Path, point_only: bool) -> Result<Prepared, String> {
    let started = Instant::now();
    let (mut input, source): (Input, _) = request_file::read(path, 64 * 1024 * 1024)?;
    if point_only && input.points.len() != 1 {
        return Err("planned point forecast requires exactly one observer".into());
    }
    if point_only
        && input.points[0]
            .name
            .as_ref()
            .is_none_or(|name| name.is_empty() || name.len() > 256)
    {
        return Err("planned point forecast requires a named observer".into());
    }
    if input.points.is_empty()
        || input.points.len() > timeline::MAX_POINTS
        || !input.radius_km.is_finite()
        || !(0.0..=1000.0).contains(&input.radius_km)
        || input.radius_km == 0.0
        || !(1..=timeline::MAX_POINTS).contains(&input.batch_min_points)
        || input.cams_cache_dir.as_os_str().is_empty()
    {
        return Err("invalid GFS event planning configuration".into());
    }
    let base = path.parent().unwrap_or(Path::new("."));
    if let Some(config) = &mut input.derived_cache {
        config.resolve(base)?;
    }
    if input.gfs.cache_dir.is_relative() {
        input.gfs.cache_dir = base.join(&input.gfs.cache_dir);
    }
    if input.cams_cache_dir.is_relative() {
        input.cams_cache_dir = base.join(&input.cams_cache_dir);
    }
    if let Some(config) = &mut input.cams_download {
        crate::ads::resolve_credentials_path(config, base);
        config.validate()?;
    }
    let client = GfsClient::new(input.gfs.clone())?;
    let date = time_contract::parse_date(&input.date)?;
    let mut elevations = Vec::with_capacity(input.points.len());
    let mut model_grid = Vec::new();
    for (index, p) in input.points.iter().enumerate() {
        if p.timezone_name.len() > 128 || p.name.as_ref().is_some_and(|n| n.len() > 256) {
            return Err("observer name/timezone exceeds planning limits".into());
        }
        match (p.elevation_source, p.elevation_m) {
            (Elevation::Observer, Some(v)) if v.is_finite() && (-500.0..=9000.0).contains(&v) => {
                elevations.push(v)
            }
            (Elevation::ModelGrid, None) => {
                model_grid.push(index);
                elevations.push(0.0);
            }
            _ => return Err("observer elevation must be explicit or model-grid derived".into()),
        }
    }
    // Validate coordinates/zones even when an explicit cycle bypasses its
    // regional anchor computation.
    for point in points(&input, &elevations)? {
        if !point.lat.is_finite()
            || !(-90.0..=90.0).contains(&point.lat)
            || !point.lon.is_finite()
            || !(-180.0..=180.0).contains(&point.lon)
        {
            return Err("invalid planning observer coordinates".into());
        }
    }
    let domain = bbox(&input)?;
    let prepared_bbox = gfs_data::canonical_bbox(domain, &input.gfs.resolution)?;
    let phase = Instant::now();
    let cycle = cycle(&input, &elevations, date)?;
    let cycle_ms = phase.elapsed().as_secs_f64() * 1000.0;
    let phase = Instant::now();
    let mut required = requirements(&input, &elevations, date, cycle)?;
    let requirements_ms = phase.elapsed().as_secs_f64() * 1000.0;
    let initial_hours: Vec<_> = required.hours.iter().copied().collect();
    let mut terrain = None;
    let mut terrain_pending = !model_grid.is_empty();
    let phase = Instant::now();
    if terrain_pending {
        for attempt in 0..2 {
            let hour = *required.hours.first().unwrap();
            let Some(paths) = located(&client, &input, cycle, hour, domain)? else {
                break;
            };
            let targets: Vec<_> = model_grid
                .iter()
                .map(|&i| input.points[i].clone())
                .collect();
            let codes = EcCodes::load()?;
            let mut values =
                assessment::terrain_at(&codes, &paths[0], cycle, hour, prepared_bbox, &targets)?;
            for extra in &paths[1..] {
                let part =
                    assessment::terrain_at(&codes, extra, cycle, hour, prepared_bbox, &targets)?;
                for (v, p) in values.iter_mut().zip(part) {
                    if p.is_finite() {
                        *v = p;
                    }
                }
            }
            for (&index, value) in model_grid.iter().zip(values) {
                elevations[index] = value;
            }
            required = requirements(&input, &elevations, date, cycle)?;
            terrain = Some(json!({"path":paths[0],"forecast_hour":hour}));
            if required.hours.contains(&hour) {
                terrain_pending = false;
                break;
            }
            if attempt == 1 {
                return Err("terrain prepass is outside the final event-hour plan".into());
            }
        }
    }
    let terrain_ms = phase.elapsed().as_secs_f64() * 1000.0;
    let phase = Instant::now();
    let mut frames = Vec::new();
    let mut missing = Vec::new();
    for &hour in &required.hours {
        if let Some(paths) = located(&client, &input, cycle, hour, domain)? {
            frames.push(Frame {
                hour,
                path: paths[0].clone(),
                paths: paths.into_iter().skip(1).collect(),
            });
        } else {
            missing.push(hour);
        }
    }
    let gfs_ms = phase.elapsed().as_secs_f64() * 1000.0;
    let round = |v: f64| (v * 1000.0).round_ties_even() / 1000.0;
    let area = [
        round((domain[2] + 0.6).min(90.0)),
        round(domain[0] - 0.6),
        round((domain[3] - 0.6).max(-90.0)),
        round(domain[1] + 0.6),
    ];
    let phase = Instant::now();
    // The assessment maps CAMS onto the regular 0.25-degree model grid of the
    // prepared bbox; reuse the shared axes so selection enforces the scorer's
    // fail-closed coverage contract, including the +/-180 seam column.
    let (coverage_lats, coverage_lons) = cams_cache::model_axes(prepared_bbox);
    let model_coverage = cams_cache::ModelCoverage {
        lats: &coverage_lats,
        lons: &coverage_lons,
    };
    let aerosol_source = cams_cache::AerosolSource::parse(input.aerosol_source.as_deref())?;
    let cams_plan = cams_plan(
        &input.cams_cache_dir,
        area,
        &required.cams,
        input.cams_download.as_ref(),
        Some(&model_coverage),
        aerosol_source,
    )?;
    let cams_ms = phase.elapsed().as_secs_f64() * 1000.0;
    let missing_cams: Vec<_> = required
        .cams
        .iter()
        .filter(|t| {
            cams_plan
                .selected
                .get(t)
                .is_none_or(|values| values.len() != cams_plan.areas.len())
        })
        .map(|t| time_contract::iso(*t))
        .collect();
    let cams: Vec<_> = cams_plan
        .selected
        .values()
        .filter(|values| values.len() == cams_plan.areas.len())
        .map(|values| {
            let paths = values.iter().map(|s| s.path.clone()).collect::<Vec<_>>();
            OpticalFile {
                valid_time: time_contract::iso(values[0].valid_time),
                path: paths[0].clone(),
                paths: paths.into_iter().skip(1).collect(),
            }
        })
        .collect();
    let cams_area_keys = cams_plan
        .areas
        .iter()
        .map(|area| cams_cache::area_key(*area))
        .collect::<Result<Vec<_>, _>>()?;
    let download_report = match cams_plan.download_reports.as_slice() {
        [] => Value::Null,
        [single] => single.clone(),
        many => Value::Array(many.to_vec()),
    };
    let summary = json!({
        "contract":"rust-gfs-input-plan-v1","cycle_utc":time_contract::iso(cycle),
        "cycle_selection":if input.cycle_utc.is_some() {"explicit"} else {"target-event-or-now-minus-4h-6h-cycle"},
        "publication_verified":false,"date":input.date,"phenomenon":input.phenomenon,
        "point_count":input.points.len(),"radius_km":input.radius_km,
        "time_policy":if input.points.len() >= input.batch_min_points {"regional-twenty-sample"} else {"scalar-dense-minute"},
        "batch_min_points":input.batch_min_points,"bbox":domain,"prepared_bbox":prepared_bbox,
        "cams_area":area,
        "cams_areas":cams_plan.areas,
        "cams_area_key":if cams_area_keys.len() == 1 {json!(cams_area_keys[0])} else {Value::Null},
        "cams_area_keys":cams_area_keys,
        "initial_forecast_hours":initial_hours,"forecast_hours":required.hours,
        "first_evaluation_utc":required.first.map(time_contract::iso),
        "last_evaluation_utc":required.last.map(time_contract::iso),
        "cams_valid_times":required.cams.iter().copied().map(time_contract::iso).collect::<Vec<_>>(),
        "model_files":frames,"cams_files":cams,
        "cams_selections":cams_plan.selected.values().flatten().collect::<Vec<_>>(),
        "missing_hours":missing,"missing_cams_valid_times":missing_cams,
        "terrain_pending":terrain_pending,"terrain_source":terrain,
        "model_grid_observer_count":model_grid.len(),
        "model_grid_elevation_range_m":if model_grid.is_empty() {None} else {Some([
            model_grid.iter().map(|&i| elevations[i]).fold(f64::INFINITY,f64::min),
            model_grid.iter().map(|&i| elevations[i]).fold(f64::NEG_INFINITY,f64::max)])},
        "inputs_located":missing.is_empty()&&missing_cams.is_empty()&&!terrain_pending,
        "gfs_download_enabled":input.allow_gfs_download,"cams_download_supported":true,
        "cams_download_enabled":input.cams_download.is_some(),
        "cams_download":if download_report.is_null() {Value::Null} else {download_report},
        "cams_cache_hits_validated":cams_plan.cache_hits,
        "model_cache_hits_validated":false,"cams_profile_contents_validated":true,
        "derived_cache":input.derived_cache,
        "cams_filename_grib_time_verified":false,
        "production_input_lifecycle_complete":false,"public_forecast_complete":false,
        "elapsed_ms":started.elapsed().as_secs_f64()*1000.0,
        "timings_ms":{"cycle":cycle_ms,"solar_requirements":requirements_ms,
            "terrain_and_replanning":terrain_ms,"gfs_resolution":gfs_ms,"cams_selection":cams_ms},
    });
    if source_identity(path)? != source {
        return Err("planning request changed during execution".into());
    }
    Ok(Prepared {
        source,
        summary,
        input: assessment::Input {
            model: assessment::Model::Gfs,
            cycle_utc: time_contract::iso(cycle),
            date: input.date,
            phenomenon: input.phenomenon,
            points: input.points,
            frames,
            cams,
            radius_km: input.radius_km,
            bbox: Some(prepared_bbox),
            include_point_core: true,
            point_spacing_deg: None,
            compute_backend_requested: None,
            derived_cache: input.derived_cache,
            compact_results: false,
        },
    })
}

#[cfg(test)]
mod tests {
    use super::union_longitudes;

    #[test]
    fn wrapped_longitude_union_keeps_the_narrow_arc() {
        assert_eq!(
            union_longitudes([(179.0, 180.0), (-180.0, -179.0)]),
            [179.0, -179.0]
        );
        assert_eq!(union_longitudes([(170.0, -170.0)]), [170.0, -170.0]);
    }

    #[test]
    fn ordinary_longitude_union_remains_ordinary() {
        assert_eq!(
            union_longitudes([(100.0, 110.0), (105.0, 115.0)]),
            [100.0, 115.0]
        );
    }
}
