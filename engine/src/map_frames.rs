//! Bounded, content-addressed native map frames. Legacy Python caches are separate.
use flate2::{read::MultiGzDecoder, write::GzEncoder, Compression};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
};

const MAX_EXPANDED: u64 = 128 * 1024 * 1024;
const MAX_COMPRESSED: u64 = 32 * 1024 * 1024;
const MAX_FRAMES: usize = 512;
const MAX_CELLS: usize = 1_000_000;
const MAX_FRAME_CELLS: usize = 250_000;
fn digest(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|v| format!("{v:02x}"))
        .collect()
}
const FLOAT_FIELDS: [&str; 11] = [
    "solar_elevation_deg",
    "solar_azimuth_deg",
    "instant_quality_index",
    "burnable_cloud_coverage",
    "forward_view_cloud_field_amount",
    "forward_burnable_cloud_field_amount",
    "forward_burnable_cloud_load",
    "forward_burnable_cloud_material_load",
    "forward_burnable_cloud_incoming_aerosol_load",
    "solar_incoming_block_risk",
    "forward_burnable_cloud_ratio",
];
const CELL_FIELDS: [&str; 13] = [
    "lat",
    "lon",
    "distance_km",
    "bearing_deg",
    "high_cloud",
    "mid_cloud",
    "low_cloud",
    "cloud_evidence",
    "deep_cloud_risk",
    "burn_score",
    "illumination_score",
    "path_clearance_score",
    "blocking_risk",
];

fn object<'a>(value: &'a Value, name: &str) -> Result<&'a Map<String, Value>, String> {
    value
        .as_object()
        .ok_or_else(|| format!("map {name} must be an object"))
}

fn exact_keys(value: &Value, keys: &[&str], name: &str) -> Result<(), String> {
    let object = object(value, name)?;
    if object.len() != keys.len() || keys.iter().any(|key| !object.contains_key(*key)) {
        return Err(format!("map {name} fields do not match v25 contract"));
    }
    Ok(())
}

fn number(value: &Value, key: &str) -> Result<f64, String> {
    value[key]
        .as_f64()
        .filter(|v| v.is_finite())
        .ok_or_else(|| format!("map {key} must be a finite number"))
}

fn bounded(value: &Value, key: &str, low: f64, high: f64, exclusive: bool) -> Result<(), String> {
    let n = number(value, key)?;
    if n < low || n > high || (exclusive && n == high) {
        return Err(format!("map {key} is outside its physical range"));
    }
    Ok(())
}

fn observer(value: &Value) -> Result<(), String> {
    exact_keys(value, &["lat", "lon"], "observer")?;
    bounded(value, "lat", -90.0, 90.0, false)?;
    bounded(value, "lon", -180.0, 180.0, false)
}

fn cell(value: &Value) -> Result<(), String> {
    exact_keys(value, &CELL_FIELDS, "cell")?;
    bounded(value, "lat", -90.0, 90.0, false)?;
    bounded(value, "lon", -180.0, 180.0, false)?;
    if number(value, "distance_km")? < 0.0 {
        return Err("map distance_km must be nonnegative".into());
    }
    bounded(value, "bearing_deg", 0.0, 360.0, true)?;
    for key in &CELL_FIELDS[4..] {
        bounded(value, key, 0.0, 1.0, false)?;
    }
    Ok(())
}

fn validate(value: &Value) -> Result<(), String> {
    exact_keys(value, &["version", "observer", "frames"], "cache")?;
    if value["version"].as_u64() != Some(25) {
        return Err("map cache version must be 25".into());
    }
    observer(&value["observer"])?;
    let frames = value["frames"]
        .as_array()
        .ok_or("map frames must be an array")?;
    if frames.is_empty() || frames.len() > MAX_FRAMES {
        return Err("map frame count exceeds supported limits".into());
    }
    let mut previous = None;
    let mut count = 0;
    for frame in frames {
        let mut keys = FLOAT_FIELDS.to_vec();
        keys.extend([
            "valid_time_local",
            "valid",
            "physically_burnable",
            "observer",
            "cells",
        ]);
        exact_keys(frame, &keys, "frame")?;
        let time = frame["valid_time_local"]
            .as_str()
            .ok_or("map frame time must be a string")?;
        let time = crate::time_contract::parse_zoned(time)?;
        if previous.is_some_and(|v| time <= v) {
            return Err("map frame times must be unique and strictly increasing".into());
        }
        previous = Some(time);
        observer(&frame["observer"])?;
        for key in ["lat", "lon"] {
            if number(&frame["observer"], key)? != number(&value["observer"], key)? {
                return Err("map frame observer differs from cache observer".into());
            }
        }
        bounded(frame, "solar_elevation_deg", -90.0, 90.0, false)?;
        bounded(frame, "solar_azimuth_deg", 0.0, 360.0, true)?;
        for key in &FLOAT_FIELDS[2..] {
            let upper = if *key == "instant_quality_index" || key.ends_with("_load") {
                2.5
            } else {
                1.0
            };
            bounded(frame, key, 0.0, upper, false)?;
        }
        let valid = frame["valid"]
            .as_bool()
            .ok_or("map valid must be a boolean")?;
        let burnable = frame["physically_burnable"]
            .as_bool()
            .ok_or("map physically_burnable must be a boolean")?;
        let cells = frame["cells"]
            .as_array()
            .ok_or("map cells must be an array")?;
        count += cells.len();
        if cells.len() > MAX_FRAME_CELLS || count > MAX_CELLS {
            return Err("map cell count exceeds supported limits".into());
        }
        if !valid
            && (burnable
                || number(frame, "instant_quality_index")? != 0.0
                || number(frame, "burnable_cloud_coverage")? != 0.0
                || !cells.is_empty())
        {
            return Err("invalid map frame cannot contain active quality or cells".into());
        }
        for value in cells {
            cell(value)?;
        }
    }
    Ok(())
}

fn compact_cell(raw: &Value) -> Result<Value, String> {
    let mut values = Map::new();
    for key in CELL_FIELDS {
        values.insert(key.into(), json!(number(raw, key)?));
    }
    let value = Value::Object(values);
    cell(&value)?;
    let mut compact = Map::new();
    for key in CELL_FIELDS {
        let decimals = match key {
            "lat" | "lon" => 4,
            "distance_km" | "bearing_deg" => 1,
            _ => 3,
        };
        let n = number(&value, key)?;
        let n: f64 = format!("{n:.decimals$}")
            .parse::<f64>()
            .map_err(|e| e.to_string())?;
        compact.insert(
            key.into(),
            json!(if key == "bearing_deg" { n % 360.0 } else { n }),
        );
    }
    Ok(Value::Object(compact))
}

fn cache_path(directory: &Path, token: &str) -> Result<PathBuf, String> {
    if token.len() != 24
        || !token
            .bytes()
            .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
    {
        return Err("invalid map frame token".into());
    }
    Ok(directory
        .join("native-map-v1")
        .join(format!("map-frame-v25-{token}.json.gz")))
}

/// Project native assessment steps onto the public 16-field/13-field map contract.
pub fn publish(directory: &Path, report: &mut Value, lat: f64, lon: f64) -> Result<(), String> {
    object(report, "report")?;
    let steps = report["steps"]
        .as_array()
        .ok_or("map report steps must be an array")?;
    if !steps
        .iter()
        .any(|step| step["map_cells"].as_array().is_some_and(|v| !v.is_empty()))
    {
        report["map_token"] = Value::Null;
        report["map_times"] = json!([]);
        report["map_capability"] =
            json!({"available":false,"reason":"native-report-has-no-map-cells"});
        return Ok(());
    }
    if steps.len() > MAX_FRAMES {
        return Err("map frame count exceeds supported limits".into());
    }
    let location = json!({"lat":lat,"lon":lon});
    observer(&location)?;
    let mut frames = Vec::with_capacity(steps.len());
    let mut total_cells = 0;
    for step in steps {
        let mut frame = Map::new();
        for key in FLOAT_FIELDS {
            frame.insert(key.into(), json!(number(step, key)?));
        }
        for key in ["valid_time_local", "valid", "physically_burnable"] {
            frame.insert(
                key.into(),
                step.get(key)
                    .ok_or_else(|| format!("map step missing {key}"))?
                    .clone(),
            );
        }
        frame.insert("observer".into(), location.clone());
        let cells = step["map_cells"]
            .as_array()
            .ok_or("map step missing map_cells array")?;
        total_cells += cells.len();
        if cells.len() > MAX_FRAME_CELLS || total_cells > MAX_CELLS {
            return Err("map cell count exceeds supported limits".into());
        }
        frame.insert(
            "cells".into(),
            Value::Array(cells.iter().map(compact_cell).collect::<Result<_, _>>()?),
        );
        frames.push(Value::Object(frame));
    }
    let payload = json!({"version":25,"observer":location,"frames":frames});
    validate(&payload)?;
    let bytes = serde_json::to_vec(&payload).map_err(|e| e.to_string())?;
    if bytes.len() as u64 > MAX_EXPANDED {
        return Err("map cache exceeds expanded size limit".into());
    }
    let token = digest(&bytes)[..24].to_owned();
    let path = cache_path(directory, &token)?;
    fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
    let mut temporary =
        tempfile::NamedTempFile::new_in(path.parent().unwrap()).map_err(|e| e.to_string())?;
    {
        let mut gzip = GzEncoder::new(temporary.as_file_mut(), Compression::new(3));
        gzip.write_all(&bytes).map_err(|e| e.to_string())?;
        gzip.finish().map_err(|e| e.to_string())?;
    }
    if temporary
        .as_file()
        .metadata()
        .map_err(|e| e.to_string())?
        .len()
        > MAX_COMPRESSED
    {
        return Err("map cache exceeds compressed size limit".into());
    }
    temporary.as_file().sync_all().map_err(|e| e.to_string())?;
    temporary.persist(&path).map_err(|e| e.to_string())?;
    report["map_token"] = json!(token);
    report["map_times"] = Value::Array(
        payload["frames"]
            .as_array()
            .unwrap()
            .iter()
            .map(|f| f["valid_time_local"].clone())
            .collect(),
    );
    report["map_capability"] = json!({"available":true,"version":25,"contract":"native-map-v1"});
    Ok(())
}

#[cfg(test)]
fn decode(reader: impl Read, limit: u64) -> Result<Value, String> {
    let mut bytes = Vec::new();
    MultiGzDecoder::new(reader)
        .take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| format!("map cache gzip is corrupt: {e}"))?;
    if bytes.len() as u64 > limit {
        return Err("map cache exceeds expanded size limit".into());
    }
    serde_json::from_slice(&bytes).map_err(|e| format!("map cache JSON is corrupt: {e}"))
}

/// Read exactly one time, validating the entire content-addressed bundle first.
pub fn read(directory: &Path, token: &str, time: &str) -> Result<Value, String> {
    let path = cache_path(directory, token)?;
    crate::time_contract::parse_zoned(time)?;
    let metadata = fs::symlink_metadata(&path).map_err(|e| format!("map cache not found: {e}"))?;
    if !metadata.file_type().is_file() || metadata.len() == 0 || metadata.len() > MAX_COMPRESSED {
        return Err("invalid native map cache file".into());
    }
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    let file = options.open(&path).map_err(|e| e.to_string())?;
    if !file.metadata().map_err(|e| e.to_string())?.is_file() {
        return Err("invalid native map cache file".into());
    }
    let mut compressed = Vec::new();
    file.take(MAX_COMPRESSED + 1)
        .read_to_end(&mut compressed)
        .map_err(|e| e.to_string())?;
    if compressed.len() as u64 > MAX_COMPRESSED {
        return Err("map cache exceeds compressed size limit".into());
    }
    let mut bytes = Vec::new();
    MultiGzDecoder::new(compressed.as_slice())
        .take(MAX_EXPANDED + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| format!("map cache gzip is corrupt: {e}"))?;
    if bytes.len() as u64 > MAX_EXPANDED {
        return Err("map cache exceeds expanded size limit".into());
    }
    if &digest(&bytes)[..24] != token {
        return Err("native map cache content identity mismatch".into());
    }
    let payload: Value =
        serde_json::from_slice(&bytes).map_err(|e| format!("map cache JSON is corrupt: {e}"))?;
    validate(&payload)?;
    payload["frames"]
        .as_array()
        .unwrap()
        .iter()
        .find(|frame| frame["valid_time_local"].as_str() == Some(time))
        .cloned()
        .ok_or_else(|| "map frame time not found".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn report() -> Value {
        let mut step = json!({
            "valid_time_local":"2026-11-11T17:00:00+08:00",
            "valid":true,"physically_burnable":true,
            "map_cells":[{
                "lat":30.123456,"lon":120.123456,"distance_km":1.234,"bearing_deg":359.99,
                "high_cloud":0.5,"mid_cloud":0.2,"low_cloud":0.1,"cloud_evidence":0.8,
                "deep_cloud_risk":0.2,"burn_score":0.5,"illumination_score":0.7,
                "path_clearance_score":0.9,"blocking_risk":0.1,
                "cloud_type":"high","aerosol_transmission_score":0.9
            }]
        });
        for key in FLOAT_FIELDS {
            step[key] = json!(0.5);
        }
        json!({"steps":[step]})
    }

    #[test]
    fn native_roundtrip_compacts_and_preserves_contract() {
        let directory = tempfile::tempdir().unwrap();
        let mut report = report();
        publish(directory.path(), &mut report, 30.0, 120.0).unwrap();
        let token = report["map_token"].as_str().unwrap();
        assert_eq!(token.len(), 24);
        let frame = read(directory.path(), token, "2026-11-11T17:00:00+08:00").unwrap();
        assert_eq!(frame.as_object().unwrap().len(), 16);
        assert_eq!(frame["cells"][0].as_object().unwrap().len(), 13);
        assert_eq!(frame["cells"][0]["lat"], 30.1235);
        assert_eq!(frame["cells"][0]["bearing_deg"], 0.0);
        assert_eq!(report["map_capability"]["available"], true);
    }

    #[test]
    fn rejects_invalid_numbers_times_and_missing_fields() {
        let directory = tempfile::tempdir().unwrap();
        for (key, value) in [
            ("solar_azimuth_deg", json!(360)),
            ("instant_quality_index", json!(true)),
            ("valid", json!(0)),
            ("valid_time_local", json!("2026-11-11T17:00:00")),
            ("forward_burnable_cloud_load", json!(-0.1)),
            ("forward_burnable_cloud_ratio", Value::Null),
        ] {
            let mut bad = report();
            bad["steps"][0][key] = value;
            assert!(
                publish(directory.path(), &mut bad, 30.0, 120.0).is_err(),
                "{key}"
            );
        }
        let mut bad = report();
        bad["steps"][0]["map_cells"][0]["blocking_risk"] = json!(1.1);
        assert!(publish(directory.path(), &mut bad, 30.0, 120.0).is_err());
        let mut bad = report();
        let duplicate = bad["steps"][0].clone();
        bad["steps"].as_array_mut().unwrap().push(duplicate);
        assert!(publish(directory.path(), &mut bad, 30.0, 120.0).is_err());
        let mut bad = report();
        bad["steps"][0]["valid"] = json!(false);
        assert!(publish(directory.path(), &mut bad, 30.0, 120.0).is_err());
    }

    #[test]
    fn rejects_modified_token_traversal_and_gzip_expansion() {
        let directory = tempfile::tempdir().unwrap();
        let mut report = report();
        publish(directory.path(), &mut report, 30.0, 120.0).unwrap();
        let token = report["map_token"].as_str().unwrap();
        assert!(read(directory.path(), "../anything", "2026-11-11T17:00:00Z").is_err());
        let path = cache_path(directory.path(), token).unwrap();
        let bytes = fs::read(&path).unwrap();
        let payload = decode(bytes.as_slice(), MAX_EXPANDED).unwrap();
        let mut corrupted = payload;
        corrupted["observer"]["lat"] = json!(31);
        let mut gzip = GzEncoder::new(Vec::new(), Compression::new(3));
        gzip.write_all(&serde_json::to_vec(&corrupted).unwrap())
            .unwrap();
        fs::write(path, gzip.finish().unwrap()).unwrap();
        assert!(read(directory.path(), token, "2026-11-11T17:00:00+08:00")
            .unwrap_err()
            .contains("identity"));
        let mut gzip = GzEncoder::new(Vec::new(), Compression::new(3));
        gzip.write_all(&vec![b' '; 1024]).unwrap();
        assert!(decode(gzip.finish().unwrap().as_slice(), 32)
            .unwrap_err()
            .contains("size limit"));
    }

    #[test]
    fn empty_map_is_explicitly_unavailable_and_writes_nothing() {
        let directory = tempfile::tempdir().unwrap();
        let mut report = json!({"steps":[{}],"map_token":"stale","map_times":["stale"]});
        publish(directory.path(), &mut report, 30.0, 120.0).unwrap();
        assert!(report["map_token"].is_null());
        assert_eq!(report["map_times"], json!([]));
        assert_eq!(report["map_capability"]["available"], false);
        assert!(!directory.path().join("native-map-v1").exists());
    }
}
