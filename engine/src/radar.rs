//! RainViewer observations and exact Universal Blue PNG echo metrics.
use chrono::{DateTime, TimeZone, Utc};
use image::{ImageFormat, ImageReader, RgbaImage};
use reqwest::blocking::Client;
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    fs,
    io::{Cursor, Read, Write},
    path::{Path, PathBuf},
    time::{Duration, SystemTime},
};

const METADATA_URL: &str = "https://api.rainviewer.com/public/weather-maps.json";
const PALETTE_URL: &str = "https://www.rainviewer.com/files/rainviewer_api_colors_table.csv";
const MAX_BODY: u64 = 4 * 1024 * 1024;
const ZOOM: u32 = 6;
type Palette = BTreeMap<[u8; 4], f32>;

fn clamp(value: f64) -> f64 {
    value.clamp(0.0, 1.0)
}

fn bounded_file(path: &Path) -> Result<Vec<u8>, String> {
    let meta = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    if !meta.file_type().is_file() || meta.len() == 0 || meta.len() > MAX_BODY {
        return Err("radar cache input must be a nonempty bounded regular file".into());
    }
    let mut data = Vec::with_capacity(meta.len() as usize);
    fs::File::open(path)
        .map_err(|e| e.to_string())?
        .take(MAX_BODY + 1)
        .read_to_end(&mut data)
        .map_err(|e| e.to_string())?;
    if data.len() as u64 > MAX_BODY {
        return Err("radar cache input exceeds byte bound".into());
    }
    Ok(data)
}

fn cached_bytes(
    client: &Client,
    url: &str,
    path: &Path,
    ttl: Option<u64>,
) -> Result<Vec<u8>, String> {
    if let Ok(meta) = fs::symlink_metadata(path) {
        let fresh = ttl.is_none_or(|seconds| {
            meta.modified()
                .ok()
                .and_then(|time| SystemTime::now().duration_since(time).ok())
                .is_some_and(|age| age <= Duration::from_secs(seconds))
        });
        if fresh {
            return bounded_file(path);
        }
        if !meta.file_type().is_file() {
            return Err("radar cache entry is not a regular file".into());
        }
    }
    let mut response = client
        .get(url)
        .send()
        .map_err(|_| "RainViewer request failed")?
        .error_for_status()
        .map_err(|_| "RainViewer returned an unsuccessful status")?;
    if response
        .content_length()
        .is_some_and(|size| size > MAX_BODY)
    {
        return Err("RainViewer payload exceeds byte bound".into());
    }
    let mut payload = Vec::new();
    response
        .by_ref()
        .take(MAX_BODY + 1)
        .read_to_end(&mut payload)
        .map_err(|_| "cannot read RainViewer response")?;
    if payload.is_empty() || payload.len() as u64 > MAX_BODY {
        return Err("RainViewer payload is empty or exceeds byte bound".into());
    }
    let parent = path.parent().ok_or("radar cache has no parent directory")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let mut temporary = tempfile::Builder::new()
        .prefix(".radar-")
        .tempfile_in(parent)
        .map_err(|e| e.to_string())?;
    temporary.write_all(&payload).map_err(|e| e.to_string())?;
    temporary.as_file().sync_all().map_err(|e| e.to_string())?;
    temporary.persist(path).map_err(|e| e.to_string())?;
    Ok(payload)
}

fn png(payload: &[u8]) -> Result<RgbaImage, String> {
    let mut reader = ImageReader::with_format(Cursor::new(payload), ImageFormat::Png);
    let mut limits = image::Limits::default();
    limits.max_image_width = Some(1024);
    limits.max_image_height = Some(1024);
    limits.max_alloc = Some(16 * 1024 * 1024);
    reader.limits(limits);
    let image = reader
        .decode()
        .map_err(|_| "invalid or oversized radar PNG")?;
    Ok(image.into_rgba8())
}

pub fn parse_palette(payload: &[u8]) -> Result<Palette, String> {
    let payload = payload.strip_prefix(&[0xef, 0xbb, 0xbf]).unwrap_or(payload);
    let mut reader = csv::ReaderBuilder::new()
        .flexible(true)
        .from_reader(payload);
    let index = reader
        .headers()
        .map_err(|e| e.to_string())?
        .iter()
        .position(|name| name == "Universal Blue")
        .ok_or("RainViewer palette does not contain Universal Blue")?;
    let mut result = BTreeMap::new();
    for row in reader.records().take(128) {
        let row = row.map_err(|e| e.to_string())?;
        let Some(color) = row.get(index) else { break };
        let Ok(dbz) = row.get(0).unwrap_or("").parse::<f32>() else {
            continue;
        };
        if !dbz.is_finite() {
            continue;
        }
        let raw = color.trim().trim_start_matches('#');
        if raw.len() != 8 || !raw.is_ascii() {
            continue;
        }
        let channels: Result<Vec<_>, _> = (0..8)
            .step_by(2)
            .map(|offset| u8::from_str_radix(&raw[offset..offset + 2], 16))
            .collect();
        if let Ok(channels) = channels {
            result.insert(channels.try_into().unwrap(), dbz);
        }
    }
    if result.is_empty() {
        return Err("RainViewer palette contains no valid echo colors".into());
    }
    Ok(result)
}

fn destination(lat: f64, lon: f64, bearing: f64, distance: f64) -> (f64, f64) {
    let angular = distance / 6371.0088;
    let bearing = bearing.to_radians();
    let lat1 = lat.to_radians();
    let lon1 = lon.to_radians();
    let lat2 = (lat1.sin() * angular.cos() + lat1.cos() * angular.sin() * bearing.cos()).asin();
    let lon2 = lon1
        + (bearing.sin() * angular.sin() * lat1.cos())
            .atan2(angular.cos() - lat1.sin() * lat2.sin());
    (
        lat2.to_degrees(),
        (lon2.to_degrees() + 180.0).rem_euclid(360.0) - 180.0,
    )
}

fn world(lat: f64, lon: f64, zoom: u32) -> (f64, f64) {
    let size = 256.0 * 2f64.powi(zoom as i32);
    (
        (lon + 180.0) / 360.0 * size,
        (1.0 - lat
            .clamp(-85.05112878, 85.05112878)
            .to_radians()
            .tan()
            .asinh()
            / std::f64::consts::PI)
            / 2.0
            * size,
    )
}

fn path_points(
    lat: f64,
    lon: f64,
    azimuth: f64,
    width: u32,
    height: u32,
    zoom: u32,
) -> Vec<((i32, i32), f64)> {
    let center = world(lat, lon, zoom);
    let world_width = 256.0 * 2f64.powi(zoom as i32);
    let mut points = Vec::new();
    for distance in [40.0, 80.0, 120.0, 170.0, 220.0] {
        let weight = if distance <= 120.0 {
            1.0
        } else {
            ((220.0 - distance) / 100.0_f64).max(0.25)
        };
        for offset in [-24.0, -12.0, 0.0, 12.0, 24.0] {
            let point = destination(lat, lon, (azimuth + offset).rem_euclid(360.0), distance);
            let point = world(point.0, point.1, zoom);
            // Python round and NumPy use ties-to-even.
            let dx = (point.0 - center.0 + world_width / 2.0).rem_euclid(world_width)
                - world_width / 2.0;
            let x = (width as f64 / 2.0 + dx).round_ties_even() as i32;
            let y = (height as f64 / 2.0 + point.1 - center.1).round_ties_even() as i32;
            if x >= 0 && x < width as i32 && y >= 0 && y < height as i32 {
                points.push(((x, y), weight));
            }
        }
    }
    points
}

pub fn image_metrics(
    radar: &RgbaImage,
    coverage: &RgbaImage,
    palette: &Palette,
    lat: f64,
    lon: f64,
    azimuth: f64,
    zoom: u32,
) -> Result<Value, String> {
    if !lat.is_finite()
        || !(-85.0..=85.0).contains(&lat)
        || !lon.is_finite()
        || !(-180.0..=180.0).contains(&lon)
        || !azimuth.is_finite()
        || zoom > 18
        || radar.dimensions() != coverage.dimensions()
        || radar.width() == 0
        || radar.height() == 0
        || palette.is_empty()
    {
        return Err("invalid radar image metrics request".into());
    }
    let (width, height) = radar.dimensions();
    let values: Vec<_> = radar
        .pixels()
        .map(|pixel| palette.get(&pixel.0).copied())
        .collect();
    let coverage_count = coverage.pixels().filter(|pixel| pixel[3] == 0).count();
    let km_pixel = 156.54303392 * lat.to_radians().cos() / 2f64.powi(zoom as i32);
    let radius_squared = (65.0 / km_pixel.max(0.1)).powi(2);
    let mut local_denominator = 0usize;
    let mut local = Vec::new();
    for y in 0..height {
        for x in 0..width {
            if (x as f64 - width as f64 / 2.0).powi(2) + (y as f64 - height as f64 / 2.0).powi(2)
                <= radius_squared
            {
                local_denominator += 1;
                if coverage.get_pixel(x, y)[3] == 0 {
                    if let Some(value) = values[(y * width + x) as usize] {
                        local.push(value);
                    }
                }
            }
        }
    }
    let mut path = Vec::new();
    let mut weights = Vec::new();
    let mut full_patches = true;
    for ((x, y), weight) in path_points(lat, lon, azimuth, width, height, zoom) {
        let start_x = (x - 2).max(0) as u32;
        let end_x = (x + 3).min(width as i32) as u32;
        let start_y = (y - 2).max(0) as u32;
        let end_y = (y + 3).min(height as i32) as u32;
        full_patches &= (end_x - start_x) * (end_y - start_y) == 25;
        for y in start_y..end_y {
            for x in start_x..end_x {
                path.push(values[(y * width + x) as usize]);
                weights.push(weight);
            }
        }
    }
    if !full_patches {
        weights.fill(1.0);
    }
    let weighted = |threshold: f32, local_values: bool| {
        let mut total = if local_values {
            local.len() as f64
        } else {
            0.0
        };
        let mut hits = if local_values {
            local.iter().filter(|value| **value >= threshold).count() as f64
        } else {
            0.0
        };
        for (value, weight) in path.iter().zip(&weights) {
            if let Some(value) = value {
                total += weight;
                if *value >= threshold {
                    hits += weight;
                }
            }
        }
        if total > 0.0 {
            clamp(hits / total)
        } else {
            0.0
        }
    };
    let local_ratio = if local_denominator > 0 {
        clamp(
            local.iter().filter(|value| **value >= 10.0).count() as f64 / local_denominator as f64,
        )
    } else {
        0.0
    };
    let path_ratio = weighted(10.0, false);
    let convective_ratio = weighted(35.0, true);
    let maximum = local
        .iter()
        .copied()
        .chain(path.iter().flatten().copied())
        .reduce(f32::max);
    let peak_risk = clamp((maximum.unwrap_or(0.0) as f64 - 32.0) / 23.0);
    let risk = clamp(
        0.54 * peak_risk
            + 0.30 * (convective_ratio * 45.0).min(1.0)
            + 0.16 * (path_ratio * 12.0).min(1.0),
    );
    Ok(json!({
        "coverage_ratio":coverage_count as f64/(width as f64*height as f64),
        "local_precip_ratio":local_ratio,"path_precip_ratio":path_ratio,
        "convective_ratio":convective_ratio,"max_dbz":maximum,"convective_risk":risk
    }))
}

pub fn observation(
    cache_dir: &Path,
    reference: DateTime<Utc>,
    lat: f64,
    lon: f64,
    azimuth: f64,
) -> Result<Option<Value>, String> {
    if !lat.is_finite()
        || !(-85.0..=85.0).contains(&lat)
        || !lon.is_finite()
        || !(-180.0..=180.0).contains(&lon)
        || !azimuth.is_finite()
    {
        return Err("invalid radar observation coordinates".into());
    }
    fs::create_dir_all(cache_dir).map_err(|e| e.to_string())?;
    let client = Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(5))
        .timeout(Duration::from_secs(20))
        .user_agent("firesky-rust/0.1")
        .build()
        .map_err(|_| "cannot initialize radar client")?;
    let metadata: Value = serde_json::from_slice(&cached_bytes(
        &client,
        METADATA_URL,
        &cache_dir.join("metadata.json"),
        Some(120),
    )?)
    .map_err(|_| "invalid RainViewer metadata")?;
    let frames = metadata["radar"]["past"]
        .as_array()
        .ok_or("RainViewer metadata lacks past frames")?;
    let selected = frames
        .iter()
        .filter_map(|frame| {
            let time = frame["time"].as_i64()?;
            let valid = Utc.timestamp_opt(time, 0).single()?;
            let age = (reference - valid).num_milliseconds() as f64 / 1000.0;
            (-1200.0..=9000.0)
                .contains(&age)
                .then_some((age.abs(), frame, valid))
        })
        .min_by(|a, b| a.0.total_cmp(&b.0));
    let Some((_, frame, valid)) = selected else {
        return Ok(None);
    };
    let host = metadata["host"]
        .as_str()
        .unwrap_or("")
        .trim_end_matches('/');
    let path = frame["path"].as_str().unwrap_or("");
    if host != "https://tilecache.rainviewer.com"
        || !path.starts_with("/v2/radar/")
        || path.contains("..")
        || !path
            .bytes()
            .all(|v| v.is_ascii_alphanumeric() || v == b'/' || v == b'_' || v == b'-')
    {
        return Err("RainViewer metadata contains an untrusted tile origin/path".into());
    }
    let key = format!("{lat:+.5}-{lon:+.5}");
    let tile = format!("{host}{path}/256/{ZOOM}/{lat:.5}/{lon:.5}/2/0_0.png");
    let coverage_url = format!("{host}/v2/coverage/0/256/{ZOOM}/{lat:.5}/{lon:.5}/0/0_0.png");
    let stamp = valid.format("%Y%m%d%H%M");
    let radar = png(&cached_bytes(
        &client,
        &tile,
        &cache_dir.join(format!("radar-{stamp}-{key}.png")),
        None,
    )?)?;
    let coverage = png(&cached_bytes(
        &client,
        &coverage_url,
        &cache_dir.join(format!("coverage-{key}.png")),
        Some(7 * 86400),
    )?)?;
    if radar.dimensions() != (256, 256) || coverage.dimensions() != (256, 256) {
        return Err("RainViewer returned an unexpected image size".into());
    }
    let palette = parse_palette(&cached_bytes(
        &client,
        PALETTE_URL,
        &cache_dir.join("palette.csv"),
        Some(30 * 86400),
    )?)?;
    let unknown = radar
        .pixels()
        .filter(|pixel| pixel[3] != 0 && !palette.contains_key(&pixel.0))
        .count();
    if unknown as f64 / (256.0 * 256.0) > 0.02 {
        return Err("RainViewer radar colors do not match the calibrated palette".into());
    }
    let mut metrics = image_metrics(&radar, &coverage, &palette, lat, lon, azimuth, ZOOM)?;
    if metrics["coverage_ratio"].as_f64().unwrap_or(0.0) < 0.25 {
        return Ok(None);
    }
    let output = metrics.as_object_mut().unwrap();
    output.insert("source".into(), json!("rainviewer-composite"));
    output.insert(
        "valid_time_utc".into(),
        json!(crate::time_contract::iso(valid)),
    );
    output.insert(
        "age_minutes".into(),
        json!(((reference - valid).num_milliseconds() as f64 / 60000.0).max(0.0)),
    );
    Ok(Some(metrics))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct MetricsRequest {
    radar_path: PathBuf,
    coverage_path: PathBuf,
    palette_path: PathBuf,
    lat: f64,
    lon: f64,
    path_azimuth_deg: f64,
    #[serde(default = "default_zoom")]
    zoom: u32,
}
fn default_zoom() -> u32 {
    ZOOM
}

pub fn metrics_request(params: Value) -> Result<Value, String> {
    let request: MetricsRequest = serde_json::from_value(params).map_err(|e| e.to_string())?;
    let radar = png(&bounded_file(&request.radar_path)?)?;
    let coverage = png(&bounded_file(&request.coverage_path)?)?;
    let palette = parse_palette(&bounded_file(&request.palette_path)?)?;
    image_metrics(
        &radar,
        &coverage,
        &palette,
        request.lat,
        request.lon,
        request.path_azimuth_deg,
        request.zoom,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn palette_ignores_snow_block_and_invalid_colors() {
        let mut text = String::from("dBZ,Universal Blue\n");
        for value in -32..96 {
            text.push_str(&format!("{value},#01020304\n"));
        }
        text.push_str("999,#ffffffff\n");
        let palette = parse_palette(text.as_bytes()).unwrap();
        assert_eq!(palette.len(), 1);
        assert_eq!(palette[&[1, 2, 3, 4]], 95.0);
        assert!(parse_palette(b"wrong,header\n1,#ffffffff\n").is_err());
    }
    #[test]
    fn covered_clear_air_and_uncovered_are_distinct() {
        let clear = RgbaImage::from_pixel(256, 256, image::Rgba([0, 0, 0, 0]));
        let uncovered = RgbaImage::from_pixel(256, 256, image::Rgba([0, 0, 0, 255]));
        let palette = BTreeMap::from([([0, 0, 0, 0], -32.0)]);
        let covered = image_metrics(&clear, &clear, &palette, 30.0, 120.0, 270.0, 6).unwrap();
        let missing = image_metrics(&clear, &uncovered, &palette, 30.0, 120.0, 270.0, 6).unwrap();
        assert_eq!(covered["coverage_ratio"], 1.0);
        assert_eq!(missing["coverage_ratio"], 0.0);
        assert_eq!(covered["convective_risk"], 0.0);
    }
    #[test]
    fn light_path_wraps_at_the_dateline() {
        let ordinary = path_points(0.0, 0.0, 90.0, 256, 256, 6);
        let wrapped = path_points(0.0, 179.8, 90.0, 256, 256, 6);
        assert_eq!(ordinary.len(), 25);
        assert_eq!(wrapped, ordinary);
    }
}
