//! Native Himawari Standard Data thermal calibration and geostationary geometry.
//! Header layout and nominal IR calibration follow the JMA HSD format used by
//! Satpy's ahi_hsd reader. No Python, resampling, or synthetic cloud mask.
use bzip2::read::MultiBzDecoder;
use chrono::{DateTime, Duration, Timelike, Utc};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, fs, io::Read, path::PathBuf};

const PROJECTION: &str = "ahi-geos9-himawari9-full-disk-5500x5500-v1";
const MAX_RAW_BYTES: u64 = 16 * 1024 * 1024;
const MAX_PIXELS: usize = 5_000_000;
const A: f64 = 6_378_137.0;
const B: f64 = 6_356_752.3;
const H: f64 = 42_164_000.0;
const HEIGHT: f64 = H - A;
const EXTENT: f64 = 5_499_999.9012;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    pub path: PathBuf,
    pub output_dir: PathBuf,
    #[serde(default)]
    pub cache_dir: Option<PathBuf>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    raw_files: Vec<PathBuf>,
    scan_time_utc: String,
    lat: f64,
    lon: f64,
    radius_km: f64,
    #[serde(default)]
    rows: Option<[usize; 2]>,
    #[serde(default)]
    cols: Option<[usize; 2]>,
}

#[derive(Clone, Debug)]
struct Header {
    header_bytes: usize,
    columns: usize,
    lines: usize,
    band: u16,
    segment: u8,
    first_line: usize,
    timeline: u16,
    observation_start_mjd: f64,
    gain: f64,
    offset: f64,
    wavelength_um: f64,
    c0: f64,
    c1: f64,
    c2: f64,
    speed: f64,
    planck: f64,
    boltzmann: f64,
    error_count: u16,
    outside_count: u16,
}

fn bytes<const N: usize>(input: &[u8], offset: usize) -> Result<[u8; N], String> {
    input
        .get(offset..offset + N)
        .ok_or_else(|| "truncated HSD header".to_string())?
        .try_into()
        .map_err(|_| "invalid HSD field size".into())
}
fn u16_at(input: &[u8], offset: usize) -> Result<u16, String> {
    Ok(u16::from_le_bytes(bytes(input, offset)?))
}
fn u32_at(input: &[u8], offset: usize) -> Result<u32, String> {
    Ok(u32::from_le_bytes(bytes(input, offset)?))
}
fn f32_at(input: &[u8], offset: usize) -> Result<f32, String> {
    Ok(f32::from_le_bytes(bytes(input, offset)?))
}
fn f64_at(input: &[u8], offset: usize) -> Result<f64, String> {
    Ok(f64::from_le_bytes(bytes(input, offset)?))
}

fn header(input: &[u8]) -> Result<Header, String> {
    let mut blocks = BTreeMap::new();
    let mut cursor = 0usize;
    for number in 1..=11u8 {
        if input.get(cursor) != Some(&number) {
            return Err("HSD header blocks are missing or out of order".into());
        }
        let size = if number == 10 {
            u32_at(input, cursor + 1)? as usize
        } else {
            u16_at(input, cursor + 1)? as usize
        };
        if size < 3 || size > 256 * 1024 {
            return Err("invalid HSD header block size".into());
        }
        let block = input
            .get(cursor..cursor + size)
            .ok_or("truncated HSD header block")?;
        blocks.insert(number, block);
        cursor += size;
    }
    let basic = blocks[&1];
    let data = blocks[&2];
    let projection = blocks[&3];
    let calibration = blocks[&5];
    let segment = blocks[&7];
    if u16_at(basic, 3)? != 11
        || basic.get(5) != Some(&0)
        || !basic
            .get(6..22)
            .is_some_and(|sat| sat.starts_with(b"Himawari-9"))
        || basic.get(38..42) != Some(b"FLDK".as_slice())
        || u32_at(basic, 70)? as usize != cursor
        || u16_at(data, 3)? != 16
        || u16_at(data, 5)? != 5500
        || u16_at(data, 7)? != 550
        || data.get(9) != Some(&0)
        || segment.get(3) != Some(&10)
        || !segment.get(4).is_some_and(|n| (1..=10).contains(n))
    {
        return Err(
            "unsupported HSD format (requires Himawari-9 FLDK R20 thermal segments)".into(),
        );
    }
    let band = u16_at(calibration, 3)?;
    if ![7, 13, 15].contains(&band) || calibration.len() < 147 {
        return Err("HSD calibration requires B07/B13/B15 thermal bands".into());
    }
    let first_line = u16_at(segment, 5)? as usize;
    if first_line != (segment[4] as usize - 1) * 550 + 1 {
        return Err("HSD segment line origin is inconsistent".into());
    }
    for (actual, expected, tolerance) in [
        (f64_at(projection, 3)?, 140.7, 1e-6),
        (u32_at(projection, 11)? as f64, 20_466_275.0, 0.0),
        (u32_at(projection, 15)? as f64, 20_466_275.0, 0.0),
        (f32_at(projection, 19)? as f64, 2750.5, 0.0),
        (f32_at(projection, 23)? as f64, 2750.5, 0.0),
        (f64_at(projection, 27)? * 1000.0, H, 1e-3),
        (f64_at(projection, 35)? * 1000.0, A, 1e-3),
        (f64_at(projection, 43)? * 1000.0, B, 1e-3),
    ] {
        if !actual.is_finite() || (actual - expected).abs() > tolerance {
            return Err("HSD projection differs from the supported full-disk identity".into());
        }
    }
    let data_bytes = 5500 * 550 * 2;
    if u32_at(basic, 74)? as usize != data_bytes || input.len() != cursor + data_bytes {
        return Err(format!(
            "HSD pixel payload length is inconsistent (decoded={}, header={}, data={data_bytes})",
            input.len(),
            cursor
        ));
    }
    let result = Header {
        header_bytes: cursor,
        columns: 5500,
        lines: 550,
        band,
        segment: segment[4],
        first_line,
        timeline: u16_at(basic, 44)?,
        observation_start_mjd: f64_at(basic, 46)?,
        wavelength_um: f64_at(calibration, 5)?,
        error_count: u16_at(calibration, 15)?,
        outside_count: u16_at(calibration, 17)?,
        gain: f64_at(calibration, 19)?,
        offset: f64_at(calibration, 27)?,
        c0: f64_at(calibration, 35)?,
        c1: f64_at(calibration, 43)?,
        c2: f64_at(calibration, 51)?,
        speed: f64_at(calibration, 83)?,
        planck: f64_at(calibration, 91)?,
        boltzmann: f64_at(calibration, 99)?,
    };
    if [
        result.gain,
        result.offset,
        result.wavelength_um,
        result.c0,
        result.c1,
        result.c2,
        result.speed,
        result.planck,
        result.boltzmann,
        result.observation_start_mjd,
    ]
    .iter()
    .any(|n| !n.is_finite())
        || result.wavelength_um <= 0.0
        || result.speed <= 0.0
        || result.planck <= 0.0
        || result.boltzmann <= 0.0
        || result.timeline / 100 > 23
        || result.timeline % 100 > 59
    {
        return Err("invalid HSD calibration coefficients or observation time".into());
    }
    Ok(result)
}

fn temperature(count: u16, header: &Header) -> f32 {
    if count == header.error_count || count == header.outside_count {
        return f32::NAN;
    }
    // Satpy explicitly rounds count-to-radiance coefficients and radiance to f32.
    let radiance = count as f32 * header.gain as f32 + header.offset as f32;
    if !radiance.is_finite() || radiance <= 0.0 {
        return f32::NAN;
    }
    let wavelength = header.wavelength_um * 1e-6;
    let a = header.planck * header.speed / (header.boltzmann * wavelength);
    let b = 2.0 * header.planck * header.speed.powi(2)
        / (radiance as f64 * 1e6 * wavelength.powi(5))
        + 1.0;
    let effective = a / b.ln();
    (header.c0 + header.c1 * effective + header.c2 * effective.powi(2)).max(0.0) as f32
}

/// Inverse ellipsoidal geostationary projection, sweep=y (AHI full disk).
fn pixel_lonlat(row: usize, col: usize) -> Result<(f32, f32), String> {
    let pixel = 2.0 * EXTENT / 5500.0;
    let x = (-EXTENT + (col as f64 + 0.5) * pixel) / HEIGHT;
    let y = (EXTENT - (row as f64 + 0.5) * pixel) / HEIGHT;
    let vx = x.cos() * y.cos();
    let vy = x.sin() * y.cos();
    let vz = y.sin();
    let ratio = A * A / (B * B);
    let qa = vx * vx + vy * vy + ratio * vz * vz;
    let discriminant = H * H * vx * vx - qa * (H * H - A * A);
    if discriminant < 0.0 {
        return Err("HSD footprint includes space pixels".into());
    }
    let range = (H * vx - discriminant.sqrt()) / qa;
    let ex = H - range * vx;
    let ey = range * vy;
    let ez = range * vz;
    let lon = ey.atan2(ex).to_degrees() + 140.7;
    let lat = (ratio * ez / ex.hypot(ey)).atan().to_degrees();
    Ok((lat as f32, ((lon + 180.0).rem_euclid(360.0) - 180.0) as f32))
}

fn lonlat_pixel(lat: f64, lon: f64) -> Result<(usize, usize), String> {
    let phi = lat.to_radians();
    let lam = (lon - 140.7).to_radians();
    let eccentricity = 1.0 - B * B / (A * A);
    let normal = A / (1.0 - eccentricity * phi.sin().powi(2)).sqrt();
    let ex = normal * phi.cos() * lam.cos();
    let ey = normal * phi.cos() * lam.sin();
    let ez = normal * (1.0 - eccentricity) * phi.sin();
    if H * ex < A * A {
        return Err("HSD point is outside the visible full disk".into());
    }
    let dx = H - ex;
    let x = HEIGHT * ey.atan2(dx);
    let y = HEIGHT * ez.atan2(dx.hypot(ey));
    let pixel = 2.0 * EXTENT / 5500.0;
    let col = ((x + EXTENT) / pixel).floor() as isize;
    let row = ((EXTENT - y) / pixel).floor() as isize;
    if !(0..5500).contains(&row) || !(0..5500).contains(&col) {
        return Err("HSD point is outside the full-disk array".into());
    }
    Ok((row as usize, col as usize))
}

pub fn footprint_indices(
    lat: f64,
    lon: f64,
    radius_km: f64,
) -> Result<([usize; 2], [usize; 2]), String> {
    if !lat.is_finite()
        || !(-90.0..=90.0).contains(&lat)
        || !lon.is_finite()
        || !(-180.0..=180.0).contains(&lon)
        || !radius_km.is_finite()
        || !(0.0..=2500.0).contains(&radius_km)
        || radius_km == 0.0
    {
        return Err("invalid HSD footprint".into());
    }
    let radius_lat = radius_km / 111.2;
    let radius_lon = radius_km / (111.2 * lat.to_radians().cos()).max(20.0);
    let mut rows = Vec::new();
    let mut cols = Vec::new();
    for y in [lat - radius_lat, lat + radius_lat] {
        for x in [lon - radius_lon, lon + radius_lon] {
            let (row, col) = lonlat_pixel(y, x)?;
            rows.push(row);
            cols.push(col);
        }
    }
    Ok((
        [
            rows.iter().min().unwrap().saturating_sub(4),
            (rows.iter().max().unwrap() + 5).min(5500),
        ],
        [
            cols.iter().min().unwrap().saturating_sub(4),
            (cols.iter().max().unwrap() + 5).min(5500),
        ],
    ))
}

pub fn execute(params: Params) -> Result<Value, String> {
    let (input, identity): (Input, _) = crate::request_file::read(&params.path, 256 * 1024)?;
    if !params.path.is_absolute()
        || !params.output_dir.is_absolute()
        || !fs::symlink_metadata(&params.output_dir)
            .map_err(|e| e.to_string())?
            .file_type()
            .is_dir()
        || input.raw_files.is_empty()
        || input.raw_files.len() > 30
    {
        return Err("invalid HSD raw decoding request".into());
    }
    let scan_time = crate::time_contract::parse_utc(&input.scan_time_utc)?;
    if scan_time.minute() % 10 != 0
        || scan_time.second() != 0
        || scan_time.timestamp_subsec_nanos() != 0
    {
        return Err("HSD scan time must be on a ten-minute UTC boundary".into());
    }
    let (planned_rows, planned_cols) = footprint_indices(input.lat, input.lon, input.radius_km)?;
    let rows = input.rows.unwrap_or(planned_rows);
    let cols = input.cols.unwrap_or(planned_cols);
    if rows[0] >= rows[1] || cols[0] >= cols[1] || rows[1] > 5500 || cols[1] > 5500 {
        return Err("invalid HSD pixel slice".into());
    }
    let (target_row, target_col) = lonlat_pixel(input.lat, input.lon)?;
    if !(rows[0]..rows[1]).contains(&target_row) || !(cols[0]..cols[1]).contains(&target_col) {
        return Err("HSD pixel slice does not contain the requested target".into());
    }
    let nrows = rows[1] - rows[0];
    let ncols = cols[1] - cols[0];
    let count = nrows
        .checked_mul(ncols)
        .filter(|n| *n <= MAX_PIXELS)
        .ok_or("HSD footprint exceeds pixel limit")?;
    let mut bands: BTreeMap<u16, Vec<f32>> = [7, 13, 15]
        .into_iter()
        .map(|b| (b, vec![f32::NAN; count]))
        .collect();
    let mut covered: BTreeMap<u16, Vec<bool>> = [7, 13, 15]
        .into_iter()
        .map(|b| (b, vec![false; nrows]))
        .collect();
    let mut raw_sources = Vec::new();
    let mut source_bytes = 0u64;
    let mut digest = Sha256::new();
    digest.update(
        serde_json::to_vec(
            &json!({"rows":rows,"cols":cols,"scan_time_utc":input.scan_time_utc,
            "lat":input.lat,"lon":input.lon,"radius_km":input.radius_km}),
        )
        .map_err(|e| e.to_string())?,
    );
    for path in &input.raw_files {
        if !path.is_absolute() {
            return Err("HSD raw paths must be absolute".into());
        }
        let raw_identity = crate::tensor_bundle::regular_identity(path)?;
        if raw_identity.bytes == 0 || raw_identity.bytes > MAX_RAW_BYTES {
            return Err("HSD raw segment exceeds compressed byte bound".into());
        }
        let file = fs::File::open(path).map_err(|e| e.to_string())?;
        let mut data = Vec::new();
        MultiBzDecoder::new(file)
            .take(MAX_RAW_BYTES + 1)
            .read_to_end(&mut data)
            .map_err(|_| "cannot decompress HSD segment")?;
        if data.len() as u64 > MAX_RAW_BYTES {
            return Err("HSD decompressed segment exceeds bound".into());
        }
        if crate::tensor_bundle::regular_identity(path)? != raw_identity {
            return Err("HSD raw segment changed during decode".into());
        }
        let header = header(&data)?;
        if header.timeline != (scan_time.hour() * 100 + scan_time.minute()) as u16 {
            return Err("HSD segment timeline differs from requested scan".into());
        }
        let observation_ms =
            ((header.observation_start_mjd - 40587.0) * 86_400_000.0).round() as i64;
        let observed = DateTime::<Utc>::from_timestamp_millis(observation_ms)
            .ok_or("invalid HSD observation MJD")?;
        if (observed - scan_time).num_seconds().abs() > 12 * 60 {
            return Err("HSD segment observation date differs from requested scan".into());
        }
        let first = header.first_line - 1;
        let start = rows[0].max(first);
        let end = rows[1].min(first + header.lines);
        if start >= end {
            return Err("HSD raw request contains an irrelevant segment".into());
        }
        let values = bands.get_mut(&header.band).unwrap();
        let coverage = covered.get_mut(&header.band).unwrap();
        for row in start..end {
            if coverage[row - rows[0]] {
                return Err("duplicate HSD band segment".into());
            }
            coverage[row - rows[0]] = true;
            for col in cols[0]..cols[1] {
                let offset = header.header_bytes + ((row - first) * header.columns + col) * 2;
                values[(row - rows[0]) * ncols + col - cols[0]] =
                    temperature(u16_at(&data, offset)?, &header);
            }
        }
        digest.update(header.band.to_le_bytes());
        digest.update([header.segment]);
        digest.update(raw_identity.sha256.as_bytes());
        source_bytes += raw_identity.bytes;
        raw_sources.push(json!({"path":path,"identity":raw_identity,"band":header.band,"segment":header.segment}));
    }
    if covered
        .values()
        .any(|rows| rows.iter().any(|covered| !*covered))
    {
        return Err("HSD raw frame is missing required B07/B13/B15 segments".into());
    }
    let mut min_coverage = 1.0f64;
    for values in bands.values_mut() {
        if values.iter().any(|n| n.is_infinite()) {
            return Err("HSD calibration produced infinite temperatures".into());
        }
        let finite = values.iter().filter(|n| n.is_finite()).count() as f64 / count as f64;
        let outliers = values
            .iter()
            .filter(|n| n.is_finite() && !(100.0..=400.0).contains(*n))
            .count() as f64
            / count as f64;
        if finite < 0.95 || outliers > 0.02 {
            return Err("HSD calibrated band has insufficient valid coverage".into());
        }
        for value in values.iter_mut() {
            if value.is_finite() && !(100.0..=400.0).contains(value) {
                *value = f32::NAN;
            }
        }
        min_coverage =
            min_coverage.min(values.iter().filter(|n| n.is_finite()).count() as f64 / count as f64);
        if min_coverage < 0.95 {
            return Err("HSD calibrated band has insufficient post-mask coverage".into());
        }
    }
    let mut latitudes = Vec::with_capacity(count);
    let mut longitudes = Vec::with_capacity(count);
    for row in rows[0]..rows[1] {
        for col in cols[0]..cols[1] {
            let (lat, lon) = pixel_lonlat(row, col)?;
            latitudes.push(lat);
            longitudes.push(lon);
        }
    }
    let source_hash = digest
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    let source = format!("noaa-himawari9-hsd-calibrated:{}", &source_hash[..24]);
    let valid = scan_time + Duration::minutes(5);
    let quality = if min_coverage >= 0.99 {
        "medium"
    } else {
        "low"
    };
    let metadata = json!({
        "cache_schema_version":2, "cache_algorithm":"ahi-hsd-calibrated-frame-v1",
        "frame_schema_version":1, "valid_time_utc":crate::time_contract::iso(valid),
        "ready_time_utc":crate::time_contract::iso(valid+Duration::minutes(5)),
        "source":source, "quality":quality, "range_bytes":source_bytes, "range_blocks":input.raw_files.len(),
        "target_lat":input.lat, "target_lon":input.lon, "radius_km":input.radius_km,
        "projection_identity":PROJECTION, "projection_hash":Sha256::digest(PROJECTION.as_bytes()).iter().map(|byte|format!("{byte:02x}")).collect::<String>(),
        "bands":["B07","B13","B15"], "units":{"B07":"K","B13":"K","B15":"K"},
        "raw_sources":raw_sources, "rows":rows, "cols":cols
    });
    let root = params.output_dir.join(format!("raw-frame-{source_hash}"));
    let mut writer = crate::tensor_bundle::BundleWriter::create(
        &root,
        "hsd-compact-frame-v1",
        identity,
        metadata,
    )?;
    let shape = [nrows as u64, ncols as u64];
    writer.f32("latitudes", &shape, latitudes)?;
    writer.f32("longitudes", &shape, longitudes)?;
    for band in [7, 13, 15] {
        writer.f32(
            &format!("band_b{band:02}"),
            &shape,
            bands.remove(&band).unwrap(),
        )?;
    }
    let manifest = writer.commit(&params.path)?;
    crate::desktop_hsd_retrieval::frame(&manifest)?;
    let compact_path = params
        .cache_dir
        .as_ref()
        .map(|cache_dir| {
            crate::hsd_provider::write_compact(
                &manifest,
                cache_dir,
                input.lat,
                input.lon,
                input.radius_km,
            )
        })
        .transpose()?;
    Ok(json!({
        "runtime_python_used":false,
        "compact_path":compact_path,
        "manifest_path":manifest,"valid_time_utc":crate::time_contract::iso(valid),
        "source":source,"quality":quality,"range_bytes":source_bytes,"range_blocks":input.raw_files.len(),
        "rows":rows,"cols":cols,"shape":shape
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn projection_round_trips_visible_pixels() {
        for (row, col) in [(1000, 2000), (2000, 2500), (3000, 3500), (4000, 2000)] {
            let (lat, lon) = pixel_lonlat(row, col).unwrap();
            assert_eq!(lonlat_pixel(lat as f64, lon as f64).unwrap(), (row, col));
        }
        assert!(lonlat_pixel(0.0, 0.0).is_err());
        assert!(pixel_lonlat(0, 0).is_err());
    }
    #[test]
    fn sweep_y_matches_official_pyresample_grid() {
        let (lat, lon) = pixel_lonlat(828, 1273).unwrap();
        assert!((lat - 41.102264_f32).abs() < 1e-5);
        assert!((lon - 99.203194_f32).abs() < 1e-5);
        assert_eq!(
            footprint_indices(30.25, 120.17, 1000.0).unwrap(),
            ([828, 1650], [1273, 2338])
        );
        assert_eq!(
            footprint_indices(30.25, 120.17, 100.0).unwrap(),
            ([1166, 1257], [1757, 1874])
        );
    }
    #[test]
    fn rejects_truncated_headers() {
        assert!(header(&[]).is_err());
        assert!(header(&[1, 0, 0]).is_err());
    }
    #[test]
    fn footprint_is_bounded_and_contains_target() {
        let (rows, cols) = footprint_indices(30.0, 120.0, 100.0).unwrap();
        let (row, col) = lonlat_pixel(30.0, 120.0).unwrap();
        assert!(rows[0] < row && row < rows[1]);
        assert!(cols[0] < col && col < cols[1]);
        assert!(footprint_indices(30.0, 120.0, 0.0).is_err());
    }
}
