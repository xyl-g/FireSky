//! Native S3 HSD discovery, bounded downloads, calibration, and atomic NPZ cache.
use chrono::{DateTime, Duration, Timelike, Utc};
use npyz::WriterBuilder;
use quick_xml::{events::Event, Reader};
use reqwest::blocking::Client;
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    time::Duration as Timeout,
};
const BUCKET: &str = "https://noaa-himawari9.s3.amazonaws.com";
const PROJECTION: &str = "ahi-geos9-himawari9-full-disk-5500x5500-v1";
const MAX_OBJECT: u64 = 16 * 1024 * 1024;
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    cache_dir: PathBuf,
    reference_time_utc: String,
    lat: f64,
    lon: f64,
    radius_km: f64,
    #[serde(default = "two")]
    count: usize,
    #[serde(default = "thirty")]
    max_age_minutes: f64,
}
fn two() -> usize {
    2
}
fn thirty() -> f64 {
    30.0
}
#[derive(Clone, Debug)]
struct Object {
    band: String,
    segment: usize,
    key: String,
    etag: String,
    last_modified: String,
    scan: DateTime<Utc>,
    size: u64,
}
fn request(client: &Client, url: &str, limit: u64) -> Result<Vec<u8>, String> {
    let mut response = client
        .get(url)
        .send()
        .map_err(|_| "HSD network request failed (URL redacted)")?;
    if !response.status().is_success() {
        return Err(format!("HSD HTTP status {}", response.status().as_u16()));
    }
    if response.content_length().is_some_and(|bytes| bytes > limit) {
        return Err("HSD response exceeds limit".into());
    }
    let mut bytes = Vec::new();
    (&mut response)
        .take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "HSD response read failed")?;
    if bytes.len() as u64 > limit {
        return Err("HSD response exceeds limit".into());
    }
    Ok(bytes)
}
fn parse_listing(bytes: &[u8]) -> Result<(Vec<Object>, bool, Option<String>), String> {
    let mut reader = Reader::from_reader(bytes);
    let mut result = Vec::new();
    let mut fields = BTreeMap::<String, String>::new();
    let mut tag = String::new();
    let mut inside = false;
    let mut truncated = false;
    let mut token: Option<String> = None;
    let mut depth = 0usize;
    loop {
        match reader.read_event().map_err(|_| "invalid S3 listing XML")? {
            Event::Start(event) => {
                depth += 1;
                if depth > 32 {
                    return Err("S3 XML nesting exceeds bound".into());
                }
                tag = event.local_name().as_ref().to_owned();
                if tag == "Contents" {
                    inside = true;
                    fields.clear();
                }
            }
            Event::Text(text) => {
                let text = text.xml10_content();
                if inside {
                    fields.entry(tag.clone()).or_default().push_str(&text);
                } else if tag == "IsTruncated" {
                    truncated = text.trim() == "true";
                } else if tag == "NextContinuationToken" {
                    token.get_or_insert_with(String::new).push_str(&text);
                }
            }
            Event::GeneralRef(reference) => {
                let text = quick_xml::escape::unescape(&format!("&{};", reference.as_ref()))
                    .map_err(|_| "invalid S3 XML reference")?
                    .into_owned();
                if inside {
                    fields.entry(tag.clone()).or_default().push_str(&text);
                } else if tag == "NextContinuationToken" {
                    token.get_or_insert_with(String::new).push_str(&text);
                }
            }
            Event::End(event) => {
                depth = depth.saturating_sub(1);
                if event.local_name().as_ref() == "Contents" {
                    inside = false;
                    if let Some(item) = object_from_fields(&fields)? {
                        result.push(item);
                    }
                    if result.len() > 10_000 {
                        return Err("S3 page contains too many HSD objects".into());
                    }
                }
                tag.clear();
            }
            Event::DocType(_) => return Err("S3 XML document types are not allowed".into()),
            Event::Eof => break,
            _ => (),
        }
    }
    if depth != 0 {
        return Err("truncated S3 XML document".into());
    }
    if truncated && token.as_ref().is_none_or(|token| token.is_empty()) {
        return Err("truncated S3 listing has no continuation token".into());
    }
    Ok((result, truncated, token))
}
fn object_from_fields(fields: &BTreeMap<String, String>) -> Result<Option<Object>, String> {
    let Some(key) = fields.get("Key") else {
        return Ok(None);
    };
    let Some(name) = key.rsplit('/').next() else {
        return Ok(None);
    };
    let parts: Vec<_> = name.split('_').collect();
    if parts.len() != 8
        || parts[0] != "HS"
        || parts[1] != "H09"
        || parts[5] != "FLDK"
        || parts[6] != "R20"
        || !["B07", "B13", "B15"].contains(&parts[4])
    {
        return Ok(None);
    }
    let Some(segment) = parts[7]
        .strip_prefix('S')
        .and_then(|v| v.strip_suffix("10.DAT.bz2"))
    else {
        return Ok(None);
    };
    let segment = segment
        .parse::<usize>()
        .map_err(|_| "invalid HSD S3 segment")?;
    if !(1..=10).contains(&segment) {
        return Ok(None);
    }
    let scan =
        chrono::NaiveDateTime::parse_from_str(&format!("{}{}", parts[2], parts[3]), "%Y%m%d%H%M")
            .map_err(|_| "invalid HSD object scan timestamp")?
            .and_utc();
    let size = fields
        .get("Size")
        .and_then(|n| n.parse::<u64>().ok())
        .filter(|size| (1..=MAX_OBJECT).contains(size))
        .ok_or("invalid HSD S3 object size")?;
    let etag = fields
        .get("ETag")
        .map(|s| s.trim_matches('"').to_owned())
        .unwrap_or_default();
    if etag.is_empty() || etag.len() > 128 {
        return Err("invalid HSD S3 ETag".into());
    }
    Ok(Some(Object {
        band: parts[4][1..].to_owned(),
        segment,
        key: key.clone(),
        etag,
        last_modified: fields.get("LastModified").cloned().unwrap_or_default(),
        scan,
        size,
    }))
}
fn list(client: &Client, prefix: &str) -> Result<Vec<Object>, String> {
    let mut result = Vec::new();
    let mut token: Option<String> = None;
    let mut seen = BTreeSet::new();
    for _ in 0..32 {
        let mut url = reqwest::Url::parse(BUCKET).unwrap();
        url.query_pairs_mut().extend_pairs([
            ("list-type", "2"),
            ("max-keys", "1000"),
            ("prefix", prefix),
        ]);
        if let Some(token) = &token {
            url.query_pairs_mut()
                .append_pair("continuation-token", token);
        }
        let (items, truncated, next) =
            parse_listing(&request(client, url.as_str(), 4 * 1024 * 1024)?)?;
        result.extend(items);
        if !truncated {
            return Ok(result);
        }
        token = next;
        if !seen.insert(token.clone()) {
            return Err("S3 listing repeated continuation token".into());
        }
    }
    Err("S3 listing exceeds pagination bound".into())
}
fn object_identity(item: &Object) -> Value {
    json!({"key":item.key,"etag":item.etag,"last_modified":item.last_modified,
        "size_bytes":item.size,"band":item.band,"segment":item.segment,
        "scan_time_utc":crate::time_contract::iso(item.scan),"projection_identity":PROJECTION})
}
fn download(client: &Client, item: &Object, root: &Path) -> Result<PathBuf, String> {
    let name = Path::new(&item.key)
        .file_name()
        .ok_or("HSD object has no filename")?;
    let target = root.join(name);
    let metadata_path = target.with_extension("bz2.json");
    let lock_path = target.with_extension("bz2.lock");
    let _lock = crate::cache_lock::Lock::acquire(&lock_path, 120.0)?;
    let identity = object_identity(item);
    if let Ok(metadata) = fs::read(&metadata_path)
        .ok()
        .filter(|bytes| bytes.len() <= 64 * 1024)
        .ok_or(())
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).map_err(|_| ()))
    {
        if metadata["identity"] == identity {
            if let Ok(current) = crate::tensor_bundle::regular_identity(&target) {
                if current.bytes == item.size && metadata["sha256"] == current.sha256 {
                    return Ok(target);
                }
            }
        }
    }
    let mut url = reqwest::Url::parse(BUCKET).unwrap();
    url.set_path(&item.key);
    let mut last = String::new();
    for _ in 0..4 {
        let operation = || -> Result<(), String> {
            let mut response = client
                .get(url.clone())
                .header("Accept-Encoding", "identity")
                .header("Range", format!("bytes=0-{}", item.size - 1))
                .send()
                .map_err(|_| "HSD segment download failed (URL redacted)")?;
            if response.status().as_u16() != 206 {
                return Err("HSD download requires HTTP 206".into());
            }
            let expected = format!("bytes 0-{}/{}", item.size - 1, item.size);
            if response
                .headers()
                .get("Content-Range")
                .and_then(|v| v.to_str().ok())
                != Some(expected.as_str())
                || response
                    .content_length()
                    .is_some_and(|size| size != item.size)
            {
                return Err("HSD Range response identity mismatch".into());
            }
            if response
                .headers()
                .get("ETag")
                .and_then(|v| v.to_str().ok())
                .is_some_and(|etag| etag.trim_matches('"') != item.etag)
            {
                return Err("HSD S3 object changed after listing".into());
            }
            let mut temporary = tempfile::Builder::new()
                .prefix(".hsd-download-")
                .tempfile_in(root)
                .map_err(|e| e.to_string())?;
            let count = std::io::copy(&mut (&mut response).take(item.size + 1), &mut temporary)
                .map_err(|_| "HSD segment response read failed")?;
            if count != item.size {
                return Err("HSD segment transfer length mismatch".into());
            }
            temporary.as_file().sync_all().map_err(|e| e.to_string())?;
            let hash = crate::tensor_bundle::regular_identity(temporary.path())?;
            temporary.persist(&target).map_err(|e| e.to_string())?;
            let mut metadata = tempfile::Builder::new()
                .prefix(".hsd-metadata-")
                .tempfile_in(root)
                .map_err(|e| e.to_string())?;
            serde_json::to_writer(
                &mut metadata,
                &json!({"identity":identity,"sha256":hash.sha256}),
            )
            .map_err(|e| e.to_string())?;
            metadata.as_file().sync_all().map_err(|e| e.to_string())?;
            metadata
                .persist(&metadata_path)
                .map_err(|e| e.to_string())?;
            Ok(())
        };
        match operation() {
            Ok(()) => return Ok(target),
            Err(error) => last = error,
        }
    }
    Err(last)
}
pub fn write_compact(
    manifest: &Path,
    cache_dir: &Path,
    lat: f64,
    lon: f64,
    radius: f64,
) -> Result<PathBuf, String> {
    if !cache_dir.is_absolute() {
        return Err("HSD compact cache directory must be absolute".into());
    }
    let frame = crate::desktop_hsd_retrieval::frame(manifest)?;
    let verified = crate::tensor_bundle::verify_files(manifest, "hsd-compact-frame-v1")?;
    let keys = [
        "cache_schema_version",
        "cache_algorithm",
        "frame_schema_version",
        "valid_time_utc",
        "ready_time_utc",
        "source",
        "quality",
        "range_bytes",
        "range_blocks",
        "target_lat",
        "target_lon",
        "radius_km",
        "projection_identity",
        "projection_hash",
        "bands",
        "units",
    ];
    let metadata: serde_json::Map<String, Value> = keys
        .into_iter()
        .map(|key| (key.to_owned(), verified.metadata[key].clone()))
        .collect();
    let metadata = serde_json::to_string(&metadata).map_err(|e| e.to_string())?;
    let root = cache_dir.join("compact");
    fs::create_dir_all(&root).map_err(|e| e.to_string())?;
    let target = root.join(format!(
        "frame-{}-{lat:+.5}-{lon:+.5}-{radius:.3}.npz",
        frame.valid_time.format("%Y%m%dT%H%M%S%6fZ")
    ));
    let _lock = crate::cache_lock::Lock::acquire(&target.with_extension("npz.lock"), 120.0)?;
    let mut temporary = tempfile::Builder::new()
        .prefix(".hsd-compact-")
        .tempfile_in(&root)
        .map_err(|e| e.to_string())?;
    {
        let mut archive = npyz::npz::NpzWriter::new(temporary.as_file_mut());
        let options = npyz::zip::write::FileOptions::default()
            .compression_method(npyz::zip::CompressionMethod::Deflated)
            .compression_level(Some(1));
        let dtype = npyz::DType::new_scalar(
            format!("<U{}", metadata.chars().count())
                .parse()
                .map_err(|_| "invalid metadata dtype")?,
        );
        let mut writer = archive
            .array::<String>("metadata", options)
            .map_err(|e| e.to_string())?
            .dtype(dtype)
            .shape(&[])
            .begin_nd()
            .map_err(|e| e.to_string())?;
        writer.push(&metadata).map_err(|e| e.to_string())?;
        writer.finish().map_err(|e| e.to_string())?;
        for (source, name) in [
            ("latitudes", "latitudes"),
            ("longitudes", "longitudes"),
            ("band_b07", "band__B07"),
            ("band_b13", "band__B13"),
            ("band_b15", "band__B15"),
        ] {
            archive
                .zip_writer()
                .start_file(format!("{name}.npy"), options)
                .map_err(|e| e.to_string())?;
            let mut file = fs::File::open(manifest.parent().unwrap().join(format!("{source}.npy")))
                .map_err(|e| e.to_string())?;
            std::io::copy(&mut file, archive.zip_writer()).map_err(|e| e.to_string())?;
        }
        archive.zip_writer().finish().map_err(|e| e.to_string())?;
    }
    temporary.as_file().sync_all().map_err(|e| e.to_string())?;
    temporary.persist(&target).map_err(|e| e.to_string())?;
    // Validate the written public cache through the production reader before success.
    let response = crate::desktop_hsd::execute(crate::desktop_hsd::Params {
        cache_dir: cache_dir.to_owned(),
        reference_time_utc: crate::time_contract::iso(frame.valid_time + Duration::minutes(5)),
        lat,
        lon,
        radius_km: radius,
        count: 4,
        max_age_minutes: 1440.0,
        output_dir: None,
    })?;
    if !response["frames"]
        .as_array()
        .is_some_and(|frames| frames.iter().any(|f| f["source"] == frame.source))
    {
        return Err("native HSD compact cache failed post-write verification".into());
    }
    Ok(target)
}
pub fn execute(params: Value) -> Result<Value, String> {
    let input: Input = serde_json::from_value(params).map_err(|e| e.to_string())?;
    let reference = crate::time_contract::parse_utc(&input.reference_time_utc)?;
    if !input.cache_dir.is_absolute()
        || !(1..=8).contains(&input.count)
        || !input.max_age_minutes.is_finite()
        || !(0.1..=1440.0).contains(&input.max_age_minutes)
    {
        return Err("invalid HSD provider request".into());
    }
    let (rows, cols) = crate::hsd_raw::footprint_indices(input.lat, input.lon, input.radius_km)?;
    fs::create_dir_all(&input.cache_dir).map_err(|e| e.to_string())?;
    let cached = || {
        crate::desktop_hsd::execute(crate::desktop_hsd::Params {
            cache_dir: input.cache_dir.clone(),
            reference_time_utc: input.reference_time_utc.clone(),
            lat: input.lat,
            lon: input.lon,
            radius_km: input.radius_km,
            count: input.count,
            max_age_minutes: input.max_age_minutes,
            output_dir: None,
        })
    };
    if let Ok(result) = cached() {
        if result["frames"]
            .as_array()
            .is_some_and(|frames| frames.len() >= input.count)
        {
            return Ok(result);
        }
    }
    let client = crate::http_client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Timeout::from_secs(5))
        .timeout(Timeout::from_secs(20))
        .user_agent("firesky-rust/0.1")
        .build()
        .map_err(|_| "cannot initialize HSD client")?;
    let base = reference
        .with_minute(0)
        .unwrap()
        .with_second(0)
        .unwrap()
        .with_nanosecond(0)
        .unwrap();
    let mut grouped = BTreeMap::<DateTime<Utc>, BTreeMap<(String, usize), Vec<Object>>>::new();
    let hours = ((input.max_age_minutes + 10.0) / 60.0).ceil() as i64;
    for offset in -hours..=0 {
        let time = base + Duration::hours(offset);
        for item in list(
            &client,
            &format!("AHI-L1b-FLDK/{}", time.format("%Y/%m/%d/%H")),
        )? {
            let ready = item.scan + Duration::minutes(10);
            let age = (reference - ready).num_milliseconds() as f64 / 60_000.0;
            if ready <= reference && age <= input.max_age_minutes {
                grouped
                    .entry(item.scan)
                    .or_default()
                    .entry((item.band.clone(), item.segment))
                    .or_default()
                    .push(item);
            }
        }
    }
    let required: Vec<_> = ["07", "13", "15"]
        .into_iter()
        .flat_map(|band| {
            (rows[0] / 550 + 1..=(rows[1] - 1) / 550 + 1)
                .map(move |segment| (band.to_owned(), segment))
        })
        .collect();
    let scans: Vec<_> = grouped
        .into_iter()
        .rev()
        .filter_map(|(scan, items)| {
            required
                .iter()
                .map(|key| {
                    items
                        .get(key)
                        .filter(|v| v.len() == 1)
                        .map(|v| v[0].clone())
                })
                .collect::<Option<Vec<_>>>()
                .map(|items| (scan, items))
        })
        .take(input.count)
        .collect();
    let mut errors = Vec::new();
    for (scan, items) in scans {
        let operation = || -> Result<PathBuf, String> {
            let raw = input
                .cache_dir
                .join("raw")
                .join(scan.format("%Y%m%dT%H%MZ").to_string());
            fs::create_dir_all(&raw).map_err(|e| e.to_string())?;
            let files = std::thread::scope(|scope| {
                let handles: Vec<_> = items
                    .chunks(items.len().div_ceil(4).max(1))
                    .map(|chunk| {
                        let client = &client;
                        let raw = &raw;
                        scope.spawn(move || {
                            chunk
                                .iter()
                                .map(|item| download(client, item, raw))
                                .collect::<Result<Vec<_>, _>>()
                        })
                    })
                    .collect();
                let mut files = Vec::new();
                for handle in handles {
                    files.extend(
                        handle
                            .join()
                            .map_err(|_| "HSD downloader worker panicked".to_string())??,
                    );
                }
                Ok::<_, String>(files)
            })?;
            let work = tempfile::Builder::new()
                .prefix(".hsd-calibration-")
                .tempdir_in(&input.cache_dir)
                .map_err(|e| e.to_string())?;
            let request_path = work.path().join("request.json");
            let mut request = fs::File::create(&request_path).map_err(|e| e.to_string())?;
            serde_json::to_writer(&mut request,&json!({"raw_files":files,"scan_time_utc":crate::time_contract::iso(scan),
                "lat":input.lat,"lon":input.lon,"radius_km":input.radius_km,"rows":rows,"cols":cols})).map_err(|e|e.to_string())?;
            request.flush().map_err(|e| e.to_string())?;
            let result = crate::hsd_raw::execute(crate::hsd_raw::Params {
                path: request_path,
                output_dir: work.path().to_owned(),
                cache_dir: None,
            })?;
            let manifest = PathBuf::from(
                result["manifest_path"]
                    .as_str()
                    .ok_or("raw HSD decode returned no manifest")?,
            );
            write_compact(
                &manifest,
                &input.cache_dir,
                input.lat,
                input.lon,
                input.radius_km,
            )
        };
        if let Err(error) = operation() {
            errors.push(error);
        }
    }
    let mut result = cached()?;
    result["upstream_errors"] = json!(errors);
    if result["frames"]
        .as_array()
        .is_none_or(|frames| frames.is_empty())
    {
        return Err(format!(
            "HSD provider found no complete calibrated frame: {}",
            result["upstream_errors"]
        ));
    }
    Ok(result)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn listing_preserves_xml_entities_and_requires_pagination_token() {
        let xml=br#"<ListBucketResult><Contents><Key>AHI-L1b-FLDK/HS_H09_20261108_0540_B13_FLDK_R20_S0310.DAT.bz2</Key><Size>1234</Size><ETag>&quot;abc&quot;</ETag></Contents><IsTruncated>false</IsTruncated></ListBucketResult>"#;
        let (items, truncated, _) = parse_listing(xml).unwrap();
        assert_eq!(items.len(), 1);
        assert_eq!(items[0].etag, "abc");
        assert!(!truncated);
        assert!(parse_listing(
            b"<ListBucketResult><IsTruncated>true</IsTruncated></ListBucketResult>"
        )
        .is_err());
    }
}
