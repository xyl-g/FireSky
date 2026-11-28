//! Strict EC public/basic and separate offline hybrid-level input contracts.

use crate::grib::{EcCodes, Message};
use crate::native::gfs::{FieldKey, GfsGrid};
use crate::tensor_bundle::{BundleWriter, SourceIdentity};
use chrono::{DateTime, Duration, SecondsFormat, TimeZone, Utc};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

pub const LEVELS: [i32; 9] = [200, 250, 300, 400, 500, 600, 700, 850, 925];
const PARAMETERS: [&str; 6] = ["gh", "r", "t", "w", "u", "v"];
const GRAVITY: f32 = 9.80665;
const MAX_RETAINED_VALUES: usize = 64_000_000;
pub const DEFAULT_MODEL_LEVELS: usize = 137;
// The complete catalog needs 54 pressure fields, four surface fields, and
// three messages per model level within the common 4096-message budget.
pub const MAX_MODEL_LEVELS: usize = (4096 - 58) / 3;

enum Catalog {
    Basic,
    Full(Vec<i32>),
}

impl Catalog {
    fn full(&self) -> bool {
        matches!(self, Self::Full(_))
    }

    fn levels(&self) -> &[i32] {
        match self {
            Self::Basic => &[],
            Self::Full(levels) => levels,
        }
    }

    fn retains(&self, field: &FieldKey) -> bool {
        field.0 != "hybrid" || self.levels().contains(&field.1)
    }
}

pub struct DecodeOptions<'a> {
    pub bbox: Option<[f64; 4]>,
    pub expected_cycle_utc: Option<&'a str>,
    pub expected_hour: Option<i64>,
    pub max_messages: usize,
    pub max_points: usize,
}

fn key(level_type: &str, level: i32, name: &str) -> FieldKey {
    (level_type.into(), level, name.into())
}

fn normalise_longitude(value: f32) -> f32 {
    (value + 180.0).rem_euclid(360.0) - 180.0
}

fn basic_proxy_pressure_factor(pressure_hpa: i32) -> f32 {
    match pressure_hpa {
        ..=350 => 0.55,
        351..=400 => 0.55 + (pressure_hpa - 350) as f32 / 50.0 * (0.78 - 0.55),
        401..=500 => 0.78 + (pressure_hpa - 400) as f32 / 100.0 * (0.95 - 0.78),
        501..=700 => 0.95,
        _ => 1.0,
    }
}

fn basic_proxy_cloud_fraction(tcc_percent: f32, rh_percent: f32, pressure_hpa: i32) -> f32 {
    let humidity_support = ((rh_percent - 72.0) / 26.0).clamp(0.0, 1.0);
    let cover_support = (tcc_percent / 100.0).clamp(0.0, 1.0).powf(0.75);
    (0.55 * humidity_support + 0.45 * cover_support)
        * basic_proxy_pressure_factor(pressure_hpa).clamp(0.0, 1.0)
        * 100.0
}

fn coordinate_bits(value: f32) -> u32 {
    if value == 0.0 {
        0
    } else {
        value.to_bits()
    }
}

fn validate_coordinates(
    latitudes: &[f32],
    longitudes: &[f32],
    shape: [usize; 2],
) -> Result<(), String> {
    if shape.contains(&0)
        || shape[0].checked_mul(shape[1]) != Some(latitudes.len())
        || longitudes.len() != latitudes.len()
        || latitudes.iter().chain(longitudes).any(|v| !v.is_finite())
    {
        return Err("EC native coordinates contain nonfinite values or invalid dimensions".into());
    }
    let unique = |mut bits: Vec<u32>| {
        bits.sort_unstable();
        !bits.windows(2).any(|pair| pair[0] == pair[1])
    };
    let ni = shape[1];
    let first_longitudes: Vec<_> = longitudes[..ni]
        .iter()
        .copied()
        .map(normalise_longitude)
        .collect();
    // Prove the Cartesian product directly for normal row-major scans.
    // Unusual scan orders still use the exact set-based contract below.
    if unique(
        first_longitudes
            .iter()
            .copied()
            .map(coordinate_bits)
            .collect(),
    ) && unique(
        latitudes
            .iter()
            .step_by(ni)
            .copied()
            .map(coordinate_bits)
            .collect(),
    ) && latitudes
        .chunks_exact(ni)
        .zip(longitudes.chunks_exact(ni))
        .all(|(lats, lons)| {
            lats.iter()
                .zip(lons)
                .zip(&first_longitudes)
                .all(|((&lat, &lon), &expected_lon)| {
                    lat == lats[0] && normalise_longitude(lon) == expected_lon
                })
        })
    {
        return Ok(());
    }
    let lats: BTreeSet<_> = latitudes.iter().map(|&v| coordinate_bits(v)).collect();
    let lons: BTreeSet<_> = longitudes
        .iter()
        .map(|&v| coordinate_bits(normalise_longitude(v)))
        .collect();
    let pairs: BTreeSet<_> = latitudes
        .iter()
        .zip(longitudes)
        .map(|(&a, &b)| (coordinate_bits(a), coordinate_bits(normalise_longitude(b))))
        .collect();
    if lats.len().checked_mul(lons.len()) != Some(latitudes.len()) || pairs.len() != latitudes.len()
    {
        return Err("EC native grid has missing or duplicated cells".into());
    }
    Ok(())
}

fn date(message: &Message<'_>, date_key: &[u8], time_key: &[u8]) -> Result<DateTime<Utc>, String> {
    let date = message.long(date_key)?;
    let time = message.long(time_key)?;
    let (hour, minute) = if time < 24 {
        (time, 0)
    } else {
        (time / 100, time % 100)
    };
    if !(10_000_000..=99_999_999).contains(&date)
        || !(0..=23).contains(&hour)
        || !(0..=59).contains(&minute)
    {
        return Err("EC invalid UTC date/time".into());
    }
    Utc.with_ymd_and_hms(
        (date / 10_000) as i32,
        ((date / 100) % 100) as u32,
        (date % 100) as u32,
        hour as u32,
        minute as u32,
        0,
    )
    .single()
    .ok_or_else(|| "EC invalid UTC date/time".into())
}

pub fn expected_cycle(value: &str) -> Result<DateTime<Utc>, String> {
    let date = DateTime::parse_from_rfc3339(value).map_err(|e| e.to_string())?;
    if date.offset().local_minus_utc() != 0 {
        return Err("EC expected cycle must be aware UTC".into());
    }
    Ok(date.with_timezone(&Utc))
}

pub fn published_step(hour: i64) -> bool {
    (0..=144).contains(&hour) && hour % 3 == 0 || (150..=360).contains(&hour) && hour % 6 == 0
}

struct Record {
    raw_key: FieldKey,
    units: String,
    raw: Vec<f32>,
    converted: Option<Vec<f32>>,
}

impl Record {
    fn values(&self) -> &[f32] {
        self.converted.as_deref().unwrap_or(&self.raw)
    }

    fn values_mut(&mut self) -> &mut [f32] {
        self.converted.get_or_insert_with(|| self.raw.clone())
    }
}

fn conversion(name: &str, level_type: &str, units: &str) -> Option<fn(f32) -> f32> {
    if name == "z" && level_type == "isobaricInhPa" {
        Some(|value| value / GRAVITY)
    } else if name == "tcc" && units != "%" {
        Some(|value| value * 100.0)
    } else if name == "cc" && units == "%" {
        Some(|value| value / 100.0)
    } else {
        None
    }
}

pub struct InputTensor {
    pub latitudes: Vec<f32>,
    pub longitudes: Vec<f32>,
    native_latitudes: Vec<f32>,
    native_longitudes: Vec<f32>,
    shape: [usize; 2],
    cycle: DateTime<Utc>,
    valid_time: DateTime<Utc>,
    hour: i64,
    records: BTreeMap<FieldKey, Record>,
    terrain: Vec<f32>,
    humidity_low: usize,
    humidity_high: usize,
    model_levels: Vec<i32>,
    hybrid_pv: Option<Vec<f32>>,
}

/// Existing basic-decoder callers may retain the original type name.
pub type BasicTensor = InputTensor;

struct Metadata {
    canonical: FieldKey,
    raw: FieldKey,
    units: String,
    cycle: DateTime<Utc>,
    valid_time: DateTime<Utc>,
    hour: i64,
    shape: [usize; 2],
}

fn metadata(message: &Message<'_>, catalog: &Catalog) -> Result<Option<Metadata>, String> {
    let level_type = message.string(b"typeOfLevel\0")?;
    let level = i32::try_from(message.long(b"level\0")?).map_err(|_| "EC invalid level")?;
    let name = message.string(b"shortName\0")?;
    let canonical = if level_type == "isobaricInhPa"
        && LEVELS.contains(&level)
        && (PARAMETERS.contains(&name.as_str()) || name == "z")
    {
        key(
            "isobaricInhPa",
            level,
            if name == "z" { "gh" } else { &name },
        )
    } else if level_type == "surface" && level == 0 && name == "z" {
        key("surface", 0, "z")
    } else if catalog.full()
        && ((level_type == "surface"
            && level == 0
            && ["lnsp", "2t", "2d"].contains(&name.as_str()))
            || (["hybrid", "hybridLevel"].contains(&level_type.as_str())
                && level == 0
                && name == "lnsp")
            || (level_type == "heightAboveGround"
                && level == 2
                && ["2t", "2d"].contains(&name.as_str())))
    {
        key("surface", 0, &name)
    } else if catalog.full()
        && ["hybrid", "hybridLevel"].contains(&level_type.as_str())
        && level > 0
        && ["cc", "clwc", "ciwc"].contains(&name.as_str())
    {
        key("hybrid", level, &name)
    } else if !catalog.full()
        && level == 0
        && name == "tcc"
        && [
            "entireAtmosphere",
            "atmosphere",
            "entireAtmosphereSingleLayer",
        ]
        .contains(&level_type.as_str())
    {
        key("entireAtmosphere", 0, "tcc")
    } else {
        return Ok(None);
    };
    let units = message.string(b"units\0")?;
    let accepted: &[&str] = match name.as_str() {
        "gh" => &["gpm", "m"],
        "z" => &["m**2 s**-2", "m2 s-2", "m^2 s^-2", "m**2 s^-2"],
        "r" => &["%"],
        "t" | "2t" | "2d" => &["K"],
        "w" => &["Pa s**-1", "Pa s-1", "Pa s^-1", "Pa/s"],
        "u" | "v" => &["m s**-1", "m s-1", "m s^-1", "m/s"],
        "tcc" => &["%", "(0 - 1)", "0 - 1", "1"],
        "cc" => &["%", "(0 - 1)", "0 - 1", "1"],
        "clwc" | "ciwc" => &["kg kg**-1", "kg kg-1", "kg/kg"],
        "lnsp" => &["Numeric", "ln(Pa)", "1"],
        _ => unreachable!(),
    };
    if !accepted.contains(&units.as_str()) {
        return Err(format!("EC {:?} units={units:?}", canonical));
    }
    let cycle = date(message, b"dataDate\0", b"dataTime\0")
        .or_else(|_| date(message, b"date\0", b"time\0"))?;
    let hour: f64 = message
        .string(b"step\0")?
        .parse()
        .map_err(|_| "EC invalid step")?;
    if message.long(b"stepUnits\0")? != 1
        || !hour.is_finite()
        || !(0.0..=1_000_000.0).contains(&hour)
        || hour.fract() != 0.0
    {
        return Err("EC step must be nonnegative integer hours".into());
    }
    if message
        .string(b"marsType\0")
        .or_else(|_| message.string(b"type\0"))?
        != "fc"
        || message
            .string(b"marsStream\0")
            .or_else(|_| message.string(b"stream\0"))?
            != "oper"
    {
        return Err("EC messages must have type=fc and stream=oper".into());
    }
    let ni = message.long(b"Ni\0")?;
    let nj = message.long(b"Nj\0")?;
    if message.string(b"gridType\0")? != "regular_ll" || ni <= 0 || nj <= 0 {
        return Err("EC requires a regular_ll grid with positive Ni/Nj".into());
    }
    Ok(Some(Metadata {
        canonical,
        raw: (level_type, level, name),
        units,
        cycle,
        valid_time: date(message, b"validityDate\0", b"validityTime\0")?,
        hour: hour as i64,
        shape: [nj as usize, ni as usize],
    }))
}

fn validate_bbox(bbox: Option<[f64; 4]>) -> Result<Option<[f32; 4]>, String> {
    if let Some([west, east, north, south]) = bbox {
        if ![west, east, north, south].iter().all(|v| v.is_finite())
            || !(-180.0..=180.0).contains(&west)
            || !(-180.0..=180.0).contains(&east)
            || !(-90.0..=90.0).contains(&north)
            || !(-90.0..=90.0).contains(&south)
            || south > north
        {
            return Err("EC invalid bbox [west,east,north,south]".into());
        }
        Ok(Some([west as f32, east as f32, north as f32, south as f32]))
    } else {
        Ok(None)
    }
}

/// Cache admission checks only headers, as the existing basic adapter does.
/// Full numerical/coordinate validation remains the decoder's responsibility.
pub fn probe_basic(
    codes: &EcCodes,
    path: &Path,
    cycle: DateTime<Utc>,
    hour: i64,
) -> Result<Value, String> {
    if !published_step(hour) {
        return Err("EC forecast hour is not on the published open-data step grid".into());
    }
    let mut seen = BTreeSet::new();
    let mut shape = None;
    codes.visit_file(path, 4096, |message| {
        let Some(meta) = metadata(message, &Catalog::Basic)? else {
            return Ok(());
        };
        if !seen.insert(meta.canonical.clone()) {
            return Err("EC duplicate canonical field".into());
        }
        if meta.cycle != cycle {
            return Err("EC cache messages do not share the expected cycle".into());
        }
        if meta.canonical == key("surface", 0, "z") {
            if meta.hour != 0 && meta.hour != hour {
                return Err("EC surface geopotential step is not static or forecast step".into());
            }
        } else if meta.hour != hour || meta.valid_time != cycle + Duration::hours(hour) {
            return Err("EC cache forecast steps or valid times disagree".into());
        }
        if shape.is_some_and(|shape| shape != meta.shape) {
            return Err("EC cache messages do not share one native grid shape".into());
        }
        shape = Some(meta.shape);
        Ok(())
    })?;
    for level in LEVELS {
        for parameter in PARAMETERS {
            if !seen.contains(&key("isobaricInhPa", level, parameter)) {
                return Err(format!("EC missing required field {level}/{parameter}"));
            }
        }
    }
    for field in [key("surface", 0, "z"), key("entireAtmosphere", 0, "tcc")] {
        if !seen.contains(&field) {
            return Err(format!("EC missing required field {field:?}"));
        }
    }
    Ok(json!({"supported":true,"contract":"public-open-data/basic",
        "native_grid_shape":shape,"required_field_count":seen.len(),
        "numeric_values_validated":false}))
}

pub fn decode_basic(
    codes: &EcCodes,
    path: &Path,
    bbox: Option<[f64; 4]>,
    expected_cycle_utc: Option<&str>,
    expected_hour: Option<i64>,
    max_messages: usize,
    max_points: usize,
) -> Result<BasicTensor, String> {
    decode(
        codes,
        path,
        Catalog::Basic,
        DecodeOptions {
            bbox,
            expected_cycle_utc,
            expected_hour,
            max_messages,
            max_points,
        },
    )
}

pub fn decode_full(
    codes: &EcCodes,
    path: &Path,
    model_levels: Vec<i32>,
    options: DecodeOptions<'_>,
) -> Result<InputTensor, String> {
    if model_levels.is_empty()
        || model_levels.len() > MAX_MODEL_LEVELS
        || model_levels
            .iter()
            .enumerate()
            .any(|(i, &v)| v != i as i32 + 1)
    {
        return Err(format!(
            "EC full model levels must be the complete sequence 1..N, N <= {MAX_MODEL_LEVELS}"
        ));
    }
    decode(codes, path, Catalog::Full(model_levels), options)
}

/// Decode only the surface geopotential (z) field for the terrain prepass.
///
/// Regional bundles cover whole continents; the full basic catalog retains
/// ~38 fields per cell and exceeds `MAX_RETAINED_VALUES` for large regional
/// envelopes. The prepass only samples orography, so this reads the single z
/// message (static hour 0 or the forecast step, matching `probe_basic`) and
/// returns elevation in metres on the bbox-selected cells.
pub fn decode_basic_terrain(
    codes: &EcCodes,
    path: &Path,
    bbox: Option<[f64; 4]>,
    expected_cycle_utc: Option<&str>,
    expected_hour: Option<i64>,
    max_messages: usize,
    max_points: usize,
) -> Result<GfsGrid, String> {
    let bbox = validate_bbox(bbox)?;
    let expected_cycle = expected_cycle_utc.map(expected_cycle).transpose()?;
    if expected_hour.is_some_and(|hour| !published_step(hour)) {
        return Err("EC expected forecast hour is not on the published open-data step grid".into());
    }
    if !(1..=5_000_000).contains(&max_points) {
        return Err("EC max_points must be 1..5000000".into());
    }
    let mut grid: Option<GfsGrid> = None;
    codes.visit_while(path, max_messages, |message| {
        let level_type = message.string(b"typeOfLevel\0")?;
        let level = message.long(b"level\0")?;
        let name = message.string(b"shortName\0")?;
        if level_type != "surface" || level != 0 || name != "z" {
            return Ok(true);
        }
        let units = message.string(b"units\0")?;
        if !["m**2 s**-2", "m2 s-2", "m^2 s^-2"].contains(&units.as_str()) {
            return Err(format!("EC surface z units={units:?}"));
        }
        let cycle = date(message, b"dataDate\0", b"dataTime\0")
            .or_else(|_| date(message, b"date\0", b"time\0"))?;
        if expected_cycle.is_some_and(|expected| expected != cycle) {
            return Err("EC messages do not share the expected cycle".into());
        }
        let hour: f64 = message
            .string(b"step\0")?
            .parse()
            .map_err(|_| "EC invalid step")?;
        if message.long(b"stepUnits\0")? != 1
            || !hour.is_finite()
            || !(0.0..=1_000_000.0).contains(&hour)
            || hour.fract() != 0.0
        {
            return Err("EC step must be nonnegative integer hours".into());
        }
        let hour = hour as i64;
        if hour != 0 && expected_hour.is_some_and(|expected| hour != expected) {
            return Err("EC surface geopotential step is not static or forecast step".into());
        }
        let count = message.size(b"values\0")?;
        if count == 0 || count > max_points {
            return Err("EC invalid grid shape or decoded point limit exceeded".into());
        }
        let latitudes: Vec<f32> = message
            .array(b"latitudes\0", count)?
            .into_iter()
            .map(|v| v as f32)
            .collect();
        let longitudes: Vec<f32> = message
            .array(b"longitudes\0", count)?
            .into_iter()
            .map(|v| v as f32)
            .collect();
        let ni = message.long(b"Ni\0")?;
        let nj = message.long(b"Nj\0")?;
        if message.string(b"gridType\0")? != "regular_ll" || ni <= 0 || nj <= 0 {
            return Err("EC requires a regular_ll grid with positive Ni/Nj".into());
        }
        validate_coordinates(&latitudes, &longitudes, [nj as usize, ni as usize])?;
        let indices: Vec<usize> = (0..count)
            .filter(|&i| {
                bbox.is_none_or(|[west, east, north, south]| {
                    let lon = normalise_longitude(longitudes[i]);
                    latitudes[i] >= south
                        && latitudes[i] <= north
                        && if west <= east {
                            lon >= west && lon <= east
                        } else {
                            lon >= west || lon <= east
                        }
                })
            })
            .collect();
        if indices.is_empty() {
            return Err("EC native grid has no cells inside bbox".into());
        }
        let values = message.values(count)?;
        let elevation: Vec<f32> = indices.iter().map(|&i| values[i] / GRAVITY).collect();
        if elevation
            .iter()
            .any(|v| !v.is_finite() || !(-500.0..=9000.0).contains(v))
        {
            return Err("EC surface geopotential produces invalid terrain elevation".into());
        }
        let mut terrain = GfsGrid::new(
            indices.iter().map(|&i| latitudes[i]).collect(),
            indices
                .iter()
                .map(|&i| normalise_longitude(longitudes[i]))
                .collect(),
        );
        terrain.insert(key("surface", 0, "orog"), elevation)?;
        grid = Some(terrain);
        Ok(false)
    })?;
    grid.ok_or_else(|| "EC GRIB has no surface geopotential field".into())
}

fn hybrid_coordinates(message: &Message<'_>, levels: usize) -> Result<Option<Vec<f64>>, String> {
    let limit = 2 * (levels + 1);
    if let Some(pv) = message.optional_array(b"pv\0", limit)? {
        return Ok(Some(pv));
    }
    let a = message.optional_array(b"hybridA\0", levels + 1)?;
    let b = message.optional_array(b"hybridB\0", levels + 1)?;
    Ok(match (a, b) {
        (Some(mut a), Some(b)) if a.len() == b.len() => {
            a.extend(b);
            Some(a)
        }
        _ => None,
    })
}

fn decode(
    codes: &EcCodes,
    path: &Path,
    catalog: Catalog,
    options: DecodeOptions<'_>,
) -> Result<InputTensor, String> {
    let bbox = validate_bbox(options.bbox)?;
    let expected_cycle = options.expected_cycle_utc.map(expected_cycle).transpose()?;
    if options
        .expected_hour
        .is_some_and(|hour| !published_step(hour))
    {
        return Err("EC expected forecast hour is not on the published open-data step grid".into());
    }
    if !(1..=5_000_000).contains(&options.max_points) {
        return Err("EC max_points must be 1..5000000".into());
    }
    let mut records = BTreeMap::new();
    let mut native_latitudes: Vec<f32> = Vec::new();
    let mut native_longitudes: Vec<f32> = Vec::new();
    let mut indices = Vec::new();
    let mut shape = [0, 0];
    let mut cycle = None;
    let mut forecast = None;
    let mut terrain_step = None;
    let mut retained = 0;
    let mut seen = BTreeSet::new();
    let mut declared_shape = None;
    let mut hybrid_pv: Option<Vec<f64>> = None;
    codes.visit_file(path, options.max_messages, |message| {
        let Some(meta) = metadata(message, &catalog)? else {
            return Ok(());
        };
        if !seen.insert(meta.canonical.clone()) {
            return Err(format!("EC duplicate canonical field {:?}", meta.canonical));
        }
        if cycle.is_some_and(|cycle| cycle != meta.cycle)
            || expected_cycle.is_some_and(|cycle| cycle != meta.cycle)
        {
            return Err("EC messages do not share the expected cycle".into());
        }
        cycle = Some(meta.cycle);
        let is_terrain = meta.canonical == key("surface", 0, "z");
        if is_terrain {
            // Bundles carry surface z either as a static analysis (hour 0)
            // or at the forecast step; probe_basic accepts both, and the
            // terrain prepass decodes non-zero hours from the same bundles.
            if meta.hour != 0 && options.expected_hour.is_some_and(|hour| meta.hour != hour) {
                return Err("EC surface geopotential step is not static or forecast step".into());
            }
            terrain_step = Some(meta.hour);
        } else {
            let time = meta
                .cycle
                .checked_add_signed(Duration::hours(meta.hour))
                .ok_or("EC forecast time overflow")?;
            if time != meta.valid_time
                || forecast.is_some_and(|(h, t)| h != meta.hour || t != meta.valid_time)
                || options.expected_hour.is_some_and(|hour| hour != meta.hour)
            {
                return Err("EC forecast steps or valid times disagree".into());
            }
            forecast = Some((meta.hour, meta.valid_time));
        }
        if declared_shape.is_some_and(|shape| shape != meta.shape) {
            return Err("EC messages do not share one native grid shape".into());
        }
        declared_shape = Some(meta.shape);
        if meta.canonical.0 == "hybrid" {
            let pv = hybrid_coordinates(message, catalog.levels().len())?;
            if meta.canonical.2 == "cc" && catalog.retains(&meta.canonical) && pv.is_none() {
                return Err("EC model cloud fraction is missing hybrid A/B coordinates".into());
            }
            if let Some(pv) = pv {
                if pv.len() != 2 * (catalog.levels().len() + 1)
                    || hybrid_pv.as_ref().is_some_and(|previous| previous != &pv)
                {
                    return Err("EC hybrid A/B coordinate lengths or values disagree".into());
                }
                hybrid_pv = Some(pv);
            }
        }
        // The reference probes extra model-level metadata, but only retains
        // the explicitly requested complete 1..N catalog's values.
        if !catalog.retains(&meta.canonical) {
            return Ok(());
        }
        let count = message.size(b"values\0")?;
        if count == 0
            || count > options.max_points
            || meta.shape[0].checked_mul(meta.shape[1]) != Some(count)
        {
            return Err("EC invalid grid shape or decoded point limit exceeded".into());
        }
        let latitudes: Vec<f32> = message
            .array(b"latitudes\0", count)?
            .into_iter()
            .map(|v| v as f32)
            .collect();
        let longitudes: Vec<f32> = message
            .array(b"longitudes\0", count)?
            .into_iter()
            .map(|v| v as f32)
            .collect();
        if native_latitudes.is_empty() {
            validate_coordinates(&latitudes, &longitudes, meta.shape)?;
            indices = (0..count)
                .filter(|&i| {
                    bbox.is_none_or(|[west, east, north, south]| {
                        let lon = normalise_longitude(longitudes[i]);
                        latitudes[i] >= south
                            && latitudes[i] <= north
                            && if west <= east {
                                lon >= west && lon <= east
                            } else {
                                lon >= west || lon <= east
                            }
                    })
                })
                .collect();
            if indices.is_empty() {
                return Err("EC native grid has no cells inside bbox".into());
            }
            native_latitudes = latitudes;
            native_longitudes = longitudes;
            shape = meta.shape;
        } else if shape != meta.shape
            || latitudes != native_latitudes
            || longitudes != native_longitudes
        {
            return Err("EC messages do not share one native coordinate grid".into());
        }
        retained += indices.len();
        if retained > MAX_RETAINED_VALUES {
            return Err("EC retained value limit exceeded (64000000)".into());
        }
        let full_values = message.values(count)?;
        // Missing values outside the selected bbox must also fail, as in the reference.
        if full_values.iter().any(|v| !v.is_finite()) {
            return Err(format!("EC {:?} contains missing values", meta.canonical));
        }
        let raw: Vec<f32> = indices.iter().map(|&i| full_values[i]).collect();
        let converted = conversion(&meta.raw.2, &meta.canonical.0, &meta.units)
            .map(|convert| raw.iter().copied().map(convert).collect());
        records.insert(
            meta.canonical,
            Record {
                raw_key: meta.raw,
                units: meta.units,
                raw,
                converted,
            },
        );
        Ok(())
    })?;
    for level in LEVELS {
        for parameter in PARAMETERS {
            if !records.contains_key(&key("isobaricInhPa", level, parameter)) {
                return Err(format!("EC missing required field {level}/{parameter}"));
            }
        }
    }
    let mut required = vec![key("surface", 0, "z")];
    if catalog.full() {
        required.extend(["lnsp", "2t", "2d"].map(|name| key("surface", 0, name)));
        for &level in catalog.levels() {
            required.extend(["cc", "clwc", "ciwc"].map(|name| key("hybrid", level, name)));
        }
    } else {
        required.push(key("entireAtmosphere", 0, "tcc"));
    }
    for required in required {
        if !records.contains_key(&required) {
            return Err(format!("EC missing required field {required:?}"));
        }
    }
    let (hour, valid_time) = forecast.ok_or("EC no forecast fields")?;
    if ![Some(0), Some(hour)].contains(&terrain_step) {
        return Err("EC surface geopotential step is not static or forecast step".into());
    }
    let mut humidity_low = 0;
    let mut humidity_high = 0;
    for level in LEVELS {
        let humidity = records.get_mut(&key("isobaricInhPa", level, "r")).unwrap();
        let low = humidity.values().iter().filter(|&&v| v < 0.0).count();
        let high = humidity.values().iter().filter(|&&v| v > 100.0).count();
        humidity_low += low;
        humidity_high += high;
        if low + high > 0 {
            for value in humidity.values_mut() {
                *value = value.clamp(0.0, 100.0);
            }
        }
        if records[&key("isobaricInhPa", level, "t")]
            .values()
            .iter()
            .any(|v| !(120.0..=380.0).contains(v))
        {
            return Err("EC temperature is invalid".into());
        }
    }
    let terrain: Vec<f32> = records[&key("surface", 0, "z")]
        .values()
        .iter()
        .map(|v| v / GRAVITY)
        .collect();
    if terrain
        .iter()
        .any(|v| !v.is_finite() || !(-500.0..=9000.0).contains(v))
    {
        return Err("EC surface geopotential produces invalid terrain elevation".into());
    }
    if !catalog.full()
        && records[&key("entireAtmosphere", 0, "tcc")]
            .values()
            .iter()
            .any(|v| !(0.0..=100.0).contains(v))
    {
        return Err("EC public tcc cloud cover is invalid".into());
    }
    for &level in catalog.levels() {
        if records[&key("hybrid", level, "cc")]
            .values()
            .iter()
            .any(|v| !(0.0..=1.0).contains(v))
            || ["clwc", "ciwc"].iter().any(|name| {
                records[&key("hybrid", level, name)]
                    .values()
                    .iter()
                    .any(|v| *v < 0.0)
            })
        {
            return Err("EC model-level cloud fraction or condensate is invalid".into());
        }
    }
    let hybrid_pv = hybrid_pv.map(|pv| pv.into_iter().map(|v| v as f32).collect::<Vec<_>>());
    if catalog.full()
        && hybrid_pv
            .as_ref()
            .is_none_or(|pv| pv.iter().any(|v| !v.is_finite()))
    {
        return Err("EC full catalog requires finite float32 hybrid A/B coordinates".into());
    }
    Ok(InputTensor {
        latitudes: indices.iter().map(|&i| native_latitudes[i]).collect(),
        longitudes: indices
            .iter()
            .map(|&i| normalise_longitude(native_longitudes[i]))
            .collect(),
        native_latitudes,
        native_longitudes,
        shape,
        cycle: cycle.ok_or("EC no cycle")?,
        valid_time,
        hour,
        records,
        terrain,
        humidity_low,
        humidity_high,
        model_levels: catalog.levels().to_vec(),
        hybrid_pv,
    })
}

impl InputTensor {
    pub fn metadata(&self) -> Value {
        let mut metadata = json!({
            "contract": "public-open-data/basic", "resolution": "0p25", "grid_type": "regular_ll",
            "cycle_utc": self.cycle.to_rfc3339_opts(SecondsFormat::Secs, false),
            "valid_time_utc": self.valid_time.to_rfc3339_opts(SecondsFormat::Secs, false),
            "forecast_hour": self.hour, "native_grid_shape": self.shape, "pressure_levels": LEVELS,
            "raw_keys": self.records.values().map(|r| &r.raw_key).collect::<Vec<_>>(),
            "raw_units": self.records.values().map(|r| &r.units).collect::<Vec<_>>(),
            "value_diagnostics": {
                "relative_humidity_below_zero_count": self.humidity_low,
                "relative_humidity_above_100_count": self.humidity_high,
                "relative_humidity_clipped_count": self.humidity_low + self.humidity_high,
            },
            "unsupported_reasons": [
                "unsupported_missing_model_level_cloud_fields", "public_open_data_only_total_cloud_cover",
            ],
        });
        if !self.model_levels.is_empty() {
            metadata["contract"] = json!("full-catalog/offline-bundle");
            metadata["decoder_backend"] = json!("rust-ec-full-v1");
            metadata["model_level_numbers"] = json!(self.model_levels);
            metadata["unsupported_reasons"] = json!([]);
        }
        metadata
    }

    pub fn write(&self, root: &Path, source: SourceIdentity) -> Result<BundleWriter, String> {
        let cells = self.latitudes.len() as u64;
        let full = !self.model_levels.is_empty();
        let mut writer = BundleWriter::create(
            root,
            if full {
                "ecmwf-full-v1"
            } else {
                "ecmwf-basic-v1"
            },
            source,
            self.metadata(),
        )?;
        for (name, values) in [
            ("latitudes", self.latitudes.as_slice()),
            ("longitudes", self.longitudes.as_slice()),
            ("native_latitudes", self.native_latitudes.as_slice()),
            ("native_longitudes", self.native_longitudes.as_slice()),
            ("terrain", self.terrain.as_slice()),
            (
                "surface_geopotential",
                self.records[&key("surface", 0, "z")].values(),
            ),
        ] {
            writer.f32(name, &[values.len() as u64], values.iter().copied())?;
        }
        if full {
            for (name, field) in [
                ("lnsp", "lnsp"),
                ("surface_temperature", "2t"),
                ("surface_dewpoint", "2d"),
            ] {
                writer.f32(
                    name,
                    &[cells],
                    self.records[&key("surface", 0, field)]
                        .values()
                        .iter()
                        .copied(),
                )?;
            }
            let pv = self.hybrid_pv.as_ref().ok_or("missing hybrid A/B")?;
            let half = self.model_levels.len() + 1;
            writer.f32("hybrid_a", &[half as u64], pv[..half].iter().copied())?;
            writer.f32("hybrid_b", &[half as u64], pv[half..].iter().copied())?;
            for name in ["cc", "clwc", "ciwc"] {
                writer.f32(
                    &format!("model_{name}"),
                    &[self.model_levels.len() as u64, cells],
                    self.model_levels.iter().flat_map(|&level| {
                        self.records[&key("hybrid", level, name)]
                            .values()
                            .iter()
                            .copied()
                    }),
                )?;
            }
        } else {
            writer.f32(
                "total_cloud",
                &[cells],
                self.records[&key("entireAtmosphere", 0, "tcc")]
                    .values()
                    .iter()
                    .copied(),
            )?;
        }
        for parameter in PARAMETERS {
            writer.f32(
                parameter,
                &[LEVELS.len() as u64, cells],
                LEVELS.iter().flat_map(|&level| {
                    self.records[&key("isobaricInhPa", level, parameter)]
                        .values()
                        .iter()
                        .copied()
                }),
            )?;
        }
        writer.f32(
            "raw_fields",
            &[self.records.len() as u64, cells],
            self.records.values().flat_map(|r| r.raw.iter().copied()),
        )?;
        Ok(writer)
    }

    /// Explicit EC-basic TCC/RH proxy; missing condensates stay missing.
    pub fn native_grid(&self) -> Result<(GfsGrid, f64), String> {
        if !self.model_levels.is_empty() {
            return Err("EC full hybrid fields must not use the basic TCC/RH proxy".into());
        }
        let mut order: Vec<_> = (0..self.latitudes.len()).collect();
        order.sort_by(|&a, &b| {
            self.latitudes[a]
                .partial_cmp(&self.latitudes[b])
                .unwrap()
                .then_with(|| self.longitudes[a].partial_cmp(&self.longitudes[b]).unwrap())
        });
        let latitudes: Vec<_> = order.iter().map(|&i| self.latitudes[i]).collect();
        let longitudes: Vec<_> = order.iter().map(|&i| self.longitudes[i]).collect();
        let axis = |values: &[f32]| {
            let mut unique = values.to_vec();
            unique.sort_by(|a, b| a.partial_cmp(b).unwrap());
            unique.dedup();
            unique
        };
        let lat_axis = axis(&latitudes);
        let lon_axis = axis(&longitudes);
        if lat_axis.len().checked_mul(lon_axis.len()) != Some(order.len()) {
            return Err("EC proxy requires a complete rectangular grid".into());
        }
        let mut spacing: Option<f64> = None;
        for axis in [&lat_axis, &lon_axis] {
            if axis.len() > 1 {
                let delta = axis[1] as f64 - axis[0] as f64;
                if delta <= 0.0 || spacing.is_some_and(|spacing| (spacing - delta).abs() > 1e-4) {
                    return Err("EC proxy requires equally spaced latitude/longitude axes".into());
                }
                if spacing.is_none() {
                    spacing = Some(delta);
                }
            }
        }
        // A dateline-crossing regional envelope normalizes longitudes into
        // [-180, 180], which introduces a single circular seam (e.g. 179.75 ->
        // -180). The axis is still uniformly spaced around the circle; detect
        // that one seam and accept it. Geometry::canonicalize rotates the seam
        // downstream, so the proxy grid stays rectangular and equally spaced.
        let regular_axis = |axis: &[f32], delta: f64| -> bool {
            axis.windows(2)
                .all(|v| (v[1] as f64 - v[0] as f64 - delta).abs() <= 1e-4)
        };
        if lon_axis.len() > 1 {
            let delta = spacing.unwrap();
            if !regular_axis(&lon_axis, delta) {
                let breaks: Vec<usize> = (0..lon_axis.len())
                    .filter(|&i| {
                        let next = if i + 1 == lon_axis.len() {
                            lon_axis[0] as f64 + 360.0
                        } else {
                            lon_axis[i + 1] as f64
                        };
                        (next - lon_axis[i] as f64 - delta).abs() > 1e-4
                    })
                    .collect();
                if breaks.len() != 1 {
                    return Err("EC proxy requires equally spaced latitude/longitude axes".into());
                }
            }
        }
        if lat_axis.len() > 1 && !regular_axis(&lat_axis, spacing.unwrap()) {
            return Err("EC proxy requires equally spaced latitude/longitude axes".into());
        }
        let spacing = spacing.ok_or("EC proxy requires at least one non-singleton axis")?;
        let ordered = |values: &[f32]| order.iter().map(|&i| values[i]).collect::<Vec<_>>();
        let mut fields = BTreeMap::new();
        fields.insert(key("surface", 0, "orog"), ordered(&self.terrain));
        fields.insert(
            key("surface", 0, "z"),
            ordered(self.records[&key("surface", 0, "z")].values()),
        );
        let tcc = self.records[&key("entireAtmosphere", 0, "tcc")].values();
        fields.insert(key("surface", 0, "tcc"), ordered(tcc));
        for level in LEVELS {
            for parameter in PARAMETERS {
                fields.insert(
                    key("isobaricInhPa", level, parameter),
                    ordered(self.records[&key("isobaricInhPa", level, parameter)].values()),
                );
            }
            let rh = self.records[&key("isobaricInhPa", level, "r")].values();
            fields.insert(
                key("isobaricInhPa", level, "tcc"),
                order
                    .iter()
                    .map(|&i| basic_proxy_cloud_fraction(tcc[i], rh[i], level))
                    .collect(),
            );
            for name in ["clwmr", "icmr"] {
                fields.insert(
                    key("isobaricInhPa", level, name),
                    vec![f32::NAN; order.len()],
                );
            }
        }
        Ok((
            GfsGrid {
                latitudes,
                longitudes,
                fields,
            },
            spacing,
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    /// Regression for the dateline-crossing regional EC proxy grid: the
    /// multi-region envelope wraps across +/-180, so the normalized longitude
    /// axis carries one circular seam. native_grid must accept that seam and
    /// produce a rectangular proxy grid instead of rejecting the spacing.
    #[test]
    fn ec_proxy_native_grid_accepts_dateline_seam() {
        let root = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
        let cache = root.join(".cache/ecmwf");
        let cycle = chrono::Utc
            .with_ymd_and_hms(2026, 11, 12, 12, 0, 0)
            .unwrap();
        let bundle = crate::ecmwf_data::path(&cache, cycle, 21);
        if !bundle.is_file() {
            return;
        }
        let codes = crate::grib::EcCodes::load().unwrap();
        let bbox = [44.75, 173.25, 69.25, 2.75];
        let area_bbox = crate::desktop_context::ec_regional_model_bbox(bbox, 1000.0).unwrap();
        // The envelope must actually wrap across the dateline for this test.
        assert!(area_bbox[0] > area_bbox[1], "expected a wrapped envelope");
        let tensor = decode_basic(
            &codes,
            &bundle,
            Some(area_bbox),
            Some("2026-11-12T12:00:00+00:00"),
            Some(21),
            4096,
            5_000_000,
        )
        .unwrap();
        let (grid, spacing) = tensor
            .native_grid()
            .expect("dateline seam must be accepted");
        assert!((spacing - 0.25).abs() < 1e-6);
        assert!(grid
            .fields
            .contains_key(&("surface".into(), 0, "orog".into())));
        assert!(grid.latitudes.len() > 100_000);
    }

    #[test]
    fn converts_units_once_and_copies_only_when_values_change() {
        for (name, level_type, units) in [
            ("gh", "isobaricInhPa", "gpm"),
            ("z", "surface", "m**2 s**-2"),
            ("r", "isobaricInhPa", "%"),
            ("cc", "hybrid", "(0 - 1)"),
            ("clwc", "hybrid", "kg kg**-1"),
            ("tcc", "entireAtmosphere", "%"),
        ] {
            assert!(conversion(name, level_type, units).is_none());
        }
        assert_eq!(conversion("cc", "hybrid", "%").unwrap()(25.0), 0.25);
        assert_eq!(
            conversion("tcc", "entireAtmosphere", "1").unwrap()(0.25),
            25.0
        );
        assert_eq!(
            conversion("z", "isobaricInhPa", "m**2 s**-2").unwrap()(GRAVITY),
            1.0
        );
        let mut record = Record {
            raw_key: key("isobaricInhPa", 500, "r"),
            units: "%".into(),
            raw: vec![-1.0, 101.0, -0.0],
            converted: None,
        };
        assert_eq!(record.values().as_ptr(), record.raw.as_ptr());
        for value in record.values_mut() {
            *value = value.clamp(0.0, 100.0);
        }
        assert_eq!(record.values(), &[0.0, 100.0, -0.0]);
        assert_eq!(record.raw[..2], [-1.0, 101.0]);
        assert_eq!(record.raw[2].to_bits(), (-0.0_f32).to_bits());
        assert_eq!(record.values()[2].to_bits(), (-0.0_f32).to_bits());
    }

    #[test]
    fn ec_basic_cloud_proxy_uses_humidity_cover_and_pressure_support() {
        assert_eq!(basic_proxy_cloud_fraction(0.0, 72.0, 300), 0.0);
        assert!((basic_proxy_cloud_fraction(100.0, 98.0, 300) - 55.0).abs() < 1e-5);
        assert!((basic_proxy_cloud_fraction(100.0, 98.0, 400) - 78.0).abs() < 1e-5);
        assert!((basic_proxy_cloud_fraction(100.0, 98.0, 500) - 95.0).abs() < 1e-5);
        assert!((basic_proxy_cloud_fraction(100.0, 98.0, 850) - 100.0).abs() < 1e-5);

        let humidity_only = basic_proxy_cloud_fraction(0.0, 85.0, 500);
        let cover_only = basic_proxy_cloud_fraction(50.0, 50.0, 500);
        assert!(humidity_only > 0.0 && humidity_only < 30.0);
        assert!(cover_only > 0.0 && cover_only < 30.0);
    }
    #[test]
    fn cartesian_proof_and_fallback_accept_all_scan_permutations() {
        fn permutations(order: &mut [usize], start: usize) {
            if start == order.len() {
                let latitudes: Vec<_> = order
                    .iter()
                    .map(|i| if *i < 3 { -0.0 } else { 1.0 })
                    .collect();
                let longitudes: Vec<_> =
                    order.iter().map(|i| [179.0, 180.0, 181.0][i % 3]).collect();
                assert!(validate_coordinates(&latitudes, &longitudes, [2, 3]).is_ok());
                assert!(validate_coordinates(&latitudes, &longitudes, [3, 2]).is_ok());
                let mut duplicate_lats = latitudes.clone();
                let mut duplicate_lons = longitudes.clone();
                duplicate_lats[0] = duplicate_lats[1];
                duplicate_lons[0] = duplicate_lons[1];
                assert!(validate_coordinates(&duplicate_lats, &duplicate_lons, [2, 3]).is_err());
                return;
            }
            for i in start..order.len() {
                order.swap(start, i);
                permutations(order, start + 1);
                order.swap(start, i);
            }
        }
        permutations(&mut [0, 1, 2, 3, 4, 5], 0);
        assert!(validate_coordinates(&[f32::NAN], &[100.0], [1, 1]).is_err());
        assert!(validate_coordinates(&[0.0], &[100.0], [0, 1]).is_err());
    }

    #[test]
    fn validates_published_cadence_and_utc() {
        for hour in [0, 3, 144, 150, 360] {
            assert!(published_step(hour));
        }
        for hour in [-1, 1, 145, 147, 361] {
            assert!(!published_step(hour));
        }
        assert!(expected_cycle("2026-11-09T12:00:00+00:00").is_ok());
        assert!(expected_cycle("2026-11-09T12:00:00+08:00").is_err());
        assert!(expected_cycle("2026-11-09T12:00:00").is_err());
    }
}
