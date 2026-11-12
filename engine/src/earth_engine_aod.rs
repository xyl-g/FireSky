//! Imported Earth Engine AOD and explicit spectral/profile estimates, not CAMS GRIB.
use crate::{request_file, tensor_bundle::source_identity, time_contract};
use chrono::Duration;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{io::Write, path::PathBuf};

const MAX_CELLS: usize = 100_000;
const MAX_INPUT: u64 = 32 * 1024 * 1024;
const BANDS: [&str; 3] = [
    "total_aerosol_optical_depth_at_469nm_surface",
    "total_aerosol_optical_depth_at_550nm_surface",
    "total_aerosol_optical_depth_at_670nm_surface",
];

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Sample {
    lat: f64,
    lon: f64,
    aod469: Option<f64>,
    aod550: Option<f64>,
    aod670: Option<f64>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    schema: String,
    collection: String,
    image_id: String,
    valid_time_utc: String,
    run_time_utc: String,
    forecast_hour: i64,
    area: [f64; 4],
    crs: String,
    crs_transform: [f64; 6],
    shape: [usize; 2],
    bands: Vec<String>,
    raw_npy_identity: crate::tensor_bundle::SourceIdentity,
    cells: Vec<Sample>,
    point: Option<Sample>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    pub input_path: PathBuf,
    pub output_dir: PathBuf,
    #[serde(default)]
    pub scale_heights_m: Vec<f64>,
    #[serde(default)]
    pub heights_agl_m: Vec<f64>,
}

#[derive(Debug, Serialize)]
struct Spectrum {
    aod550: Option<f64>,
    aod532_estimate: Option<f64>,
    angstrom_469_550: Option<f64>,
    angstrom_550_670: Option<f64>,
    angstrom_pair_difference: Option<f64>,
    status: &'static str,
}

fn spectral(sample: &Sample) -> Spectrum {
    let mut result = Spectrum {
        aod550: sample
            .aod550
            .filter(|v| v.is_finite() && (0.0..=100.0).contains(v)),
        aod532_estimate: None,
        angstrom_469_550: None,
        angstrom_550_670: None,
        angstrom_pair_difference: None,
        status: "missing_or_invalid_spectral_bands",
    };
    let valid = |value: Option<f64>| value.filter(|v| v.is_finite() && (0.0..=100.0).contains(v));
    let (Some(a), Some(b), Some(c)) = (
        valid(sample.aod469),
        valid(sample.aod550),
        valid(sample.aod670),
    ) else {
        return result;
    };
    if a == 0.0 && b == 0.0 && c == 0.0 {
        result.aod532_estimate = Some(0.0);
        result.status = "zero_aerosol";
        return result;
    }
    if a <= 0.0 || b <= 0.0 || c <= 0.0 {
        result.status = "inconsistent_zero_spectral_band";
        return result;
    }
    let alpha = -(a / b).ln() / (469.0_f64 / 550.0).ln();
    let other = -(b / c).ln() / (550.0_f64 / 670.0).ln();
    let aod532 = b * (532.0_f64 / 550.0).powf(-alpha);
    if !alpha.is_finite()
        || !other.is_finite()
        || !aod532.is_finite()
        || !(0.0..=100.0).contains(&aod532)
    {
        result.status = "invalid_spectral_estimate";
        return result;
    }
    result.aod532_estimate = Some(aod532);
    result.angstrom_469_550 = Some(alpha);
    result.angstrom_550_670 = Some(other);
    result.angstrom_pair_difference = Some(alpha - other);
    result.status = "spectral_estimate";
    result
}

fn calculated(sample: &Sample, scales: &[f64], heights: &[f64]) -> Value {
    let spectrum = spectral(sample);
    let scenarios: Vec<_> = scales
        .iter()
        .map(|&scale| {
            let values = spectrum.aod532_estimate.map(|tau| {
                heights
                    .iter()
                    .map(|&z| tau / scale * (-z / scale).exp())
                    .collect::<Vec<_>>()
            });
            json!({"scale_height_m":scale,"extinction532_m_inv":values})
        })
        .collect();
    json!({"lat":sample.lat,"lon":sample.lon,"spectrum":spectrum,"assumed_profile_scenarios":scenarios})
}

fn validate(input: &Input, params: &Params) -> Result<(), String> {
    if input.schema != "gee-cams-aod-v1"
        || input.collection != "ECMWF/CAMS/NRT"
        || input.image_id.is_empty()
        || input.image_id.len() > 512
        || input.bands != BANDS
        || input.crs.is_empty()
        || input.crs_transform.iter().any(|v| !v.is_finite())
    {
        return Err("invalid Earth Engine AOD source contract".into());
    }
    let transform = input.crs_transform;
    if transform[0] * transform[4] - transform[1] * transform[3] == 0.0 {
        return Err("Earth Engine affine transform is singular".into());
    }
    let valid = time_contract::parse_utc(&input.valid_time_utc)?;
    let run = time_contract::parse_utc(&input.run_time_utc)?;
    if !(0..=120).contains(&input.forecast_hour)
        || valid - run != Duration::hours(input.forecast_hour)
    {
        return Err("Earth Engine run time, forecast hour and valid time disagree".into());
    }
    let [n, w, s, e] = input.area;
    if input.area.iter().any(|v| !v.is_finite())
        || !(-90.0 <= s && s < n && n <= 90.0 && -180.0 <= w && w < e && e <= 180.0)
        || input.shape.contains(&0)
        || input.shape[0].checked_mul(input.shape[1]) != Some(input.cells.len())
        || input.cells.is_empty()
        || input.cells.len() > MAX_CELLS
    {
        return Err("invalid Earth Engine region or grid dimensions".into());
    }
    for sample in input.cells.iter().chain(input.point.iter()) {
        if !sample.lat.is_finite()
            || !sample.lon.is_finite()
            || !(-90.0..=90.0).contains(&sample.lat)
            || !(-180.0..=180.0).contains(&sample.lon)
        {
            return Err("invalid Earth Engine grid coordinates".into());
        }
    }
    if input
        .point
        .as_ref()
        .is_some_and(|point| point.lat < s || point.lat > n || point.lon < w || point.lon > e)
    {
        return Err("Earth Engine point lies outside the requested region".into());
    }
    let profiles = !params.scale_heights_m.is_empty();
    if profiles == params.heights_agl_m.is_empty()
        || params.scale_heights_m.len() > 4
        || params.heights_agl_m.len() > 64
        || params
            .scale_heights_m
            .iter()
            .any(|v| !v.is_finite() || !(1.0..=20_000.0).contains(v))
        || params
            .heights_agl_m
            .iter()
            .any(|v| !v.is_finite() || *v < 0.0)
        || params.heights_agl_m.windows(2).any(|v| v[0] >= v[1])
        || input
            .cells
            .len()
            .saturating_mul(params.scale_heights_m.len())
            .saturating_mul(params.heights_agl_m.len())
            > 2_000_000
    {
        return Err(
            "profile estimates require explicit bounded scale heights and increasing AGL heights"
                .into(),
        );
    }
    Ok(())
}

pub fn execute(params: Params) -> Result<Value, String> {
    let (input, input_identity): (Input, _) = request_file::read(&params.input_path, MAX_INPUT)?;
    validate(&input, &params)?;
    let raw = params
        .input_path
        .parent()
        .ok_or("invalid Earth Engine input path")?
        .join("aod.npy");
    let metadata = std::fs::symlink_metadata(&raw).map_err(|_| "missing Earth Engine raw NPY")?;
    if !metadata.is_file() || metadata.len() > MAX_INPUT {
        return Err("Earth Engine raw NPY must be a bounded regular file".into());
    }
    let identity = source_identity(&raw)?;
    if identity.bytes != input.raw_npy_identity.bytes
        || identity.sha256 != input.raw_npy_identity.sha256
    {
        return Err("Earth Engine raw NPY identity mismatch".into());
    }
    let usable = input
        .cells
        .iter()
        .filter(|s| spectral(s).aod532_estimate.is_some())
        .count();
    if usable == 0 {
        return Err("Earth Engine grid contains no usable spectral samples".into());
    }
    let cells: Vec<_> = input
        .cells
        .iter()
        .map(|s| calculated(s, &params.scale_heights_m, &params.heights_agl_m))
        .collect();
    let point = input
        .point
        .as_ref()
        .map(|s| calculated(s, &params.scale_heights_m, &params.heights_agl_m));
    let document = json!({"schema":"rust-gee-aod-calculated-v1","source":{
        "collection":input.collection,"image_id":input.image_id,"valid_time_utc":input.valid_time_utc,
        "run_time_utc":input.run_time_utc,"forecast_hour":input.forecast_hour,"input_identity":input_identity,
        "raw_npy_identity":identity,"area":input.area,"crs":input.crs,"crs_transform":input.crs_transform,
        "bands":input.bands,"data_kind":"model_forecast"},"shape":input.shape,"usable_spectral_cells":usable,
        "units":{"aod":"dimensionless","angstrom":"dimensionless","extinction":"m^-1","height":"m AGL"},
        "quality":{"aod550":"CAMS forecast distributed by Earth Engine","aod532":"Angstrom spectral estimate",
        "vertical_profile":"explicit assumed exponential scenarios only","usable_as_cams_profile":false,
        "strict_forecast_inputs_complete":false},"heights_agl_m":params.heights_agl_m,"cells":cells,"point":point});
    if params.output_dir.as_os_str().is_empty() {
        return Err("Earth Engine calculation requires an output directory".into());
    }
    std::fs::create_dir_all(&params.output_dir).map_err(|e| e.to_string())?;
    let path = params.output_dir.join("aod-calculated.json");
    let mut temporary = tempfile::Builder::new()
        .prefix(".gee-aod-")
        .tempfile_in(&params.output_dir)
        .map_err(|e| e.to_string())?;
    serde_json::to_writer_pretty(&mut temporary, &document).map_err(|e| e.to_string())?;
    temporary
        .flush()
        .and_then(|_| temporary.as_file().sync_all())
        .map_err(|e| e.to_string())?;
    temporary.persist(&path).map_err(|e| e.to_string())?;
    Ok(
        json!({"contract":"rust-gee-aod-calculated-v1","path":path,"identity":source_identity(&path)?,
        "usable_spectral_cells":usable,"point":point,"usable_as_cams_profile":false}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample(tau: f64, alpha: f64) -> Sample {
        Sample {
            lat: 30.27,
            lon: 120.15,
            aod469: Some(tau * (469.0_f64 / 550.0).powf(-alpha)),
            aod550: Some(tau),
            aod670: Some(tau * (670.0_f64 / 550.0).powf(-alpha)),
        }
    }

    fn fixture(identity: &crate::tensor_bundle::SourceIdentity) -> Value {
        json!({"schema":"gee-cams-aod-v1","collection":"ECMWF/CAMS/NRT","image_id":"20261111T12F021",
            "valid_time_utc":"2026-11-12T09:00:00Z","run_time_utc":"2026-11-11T12:00:00Z","forecast_hour":21,
            "area":[31,120,30,121],"crs":"EPSG:4326","crs_transform":[0.4,0,120,0,-0.4,31],
            "shape":[1,1],"bands":BANDS,"raw_npy_identity":identity,"cells":[sample(0.2,1.5)],"point":null})
    }

    #[test]
    fn import_preserves_quality_and_rejects_changed_raw_source() {
        let dir = tempfile::tempdir().unwrap();
        let raw = dir.path().join("aod.npy");
        std::fs::write(&raw, b"source bytes used only for identity verification").unwrap();
        let input_path = dir.path().join("input.json");
        std::fs::write(
            &input_path,
            serde_json::to_vec(&fixture(&source_identity(&raw).unwrap())).unwrap(),
        )
        .unwrap();
        let params = || Params {
            input_path: input_path.clone(),
            output_dir: dir.path().join("result"),
            scale_heights_m: vec![],
            heights_agl_m: vec![],
        };
        let result = execute(params()).unwrap();
        assert_eq!(result["usable_as_cams_profile"], false);
        let saved: Value =
            serde_json::from_slice(&std::fs::read(result["path"].as_str().unwrap()).unwrap())
                .unwrap();
        assert_eq!(saved["quality"]["strict_forecast_inputs_complete"], false);
        assert!(saved["cells"][0]["assumed_profile_scenarios"]
            .as_array()
            .unwrap()
            .is_empty());
        std::fs::write(&raw, b"changed").unwrap();
        assert!(execute(params()).unwrap_err().contains("identity mismatch"));
    }

    #[test]
    fn mismatched_time_and_incomplete_profile_assumptions_are_rejected() {
        let identity = crate::tensor_bundle::SourceIdentity {
            bytes: 0,
            sha256: String::new(),
        };
        let mut input: Input = serde_json::from_value(fixture(&identity)).unwrap();
        let mut params = Params {
            input_path: "input.json".into(),
            output_dir: "result".into(),
            scale_heights_m: vec![],
            heights_agl_m: vec![],
        };
        assert!(validate(&input, &params).is_ok());
        input.forecast_hour = 117;
        assert!(validate(&input, &params).unwrap_err().contains("disagree"));
        input.forecast_hour = 21;
        params.scale_heights_m.push(1000.0);
        assert!(validate(&input, &params).is_err());
        params.heights_agl_m = vec![1000.0, 0.0];
        assert!(validate(&input, &params).is_err());
    }

    #[test]
    fn power_law_recovers_spectral_slope_and_532_depth() {
        for alpha in [-0.5, 0.0, 1.5, 3.0] {
            let result = spectral(&sample(0.2, alpha));
            assert!((result.angstrom_469_550.unwrap() - alpha).abs() < 1e-12);
            assert!(
                (result.aod532_estimate.unwrap() - 0.2 * (532.0_f64 / 550.0).powf(-alpha)).abs()
                    < 1e-12
            );
            assert!(result.angstrom_pair_difference.unwrap().abs() < 1e-12);
        }
    }

    #[test]
    fn missing_and_zero_bands_do_not_fabricate_spectral_information() {
        let mut value = sample(0.2, 1.0);
        value.aod469 = None;
        assert_eq!(spectral(&value).aod550, Some(0.2));
        assert!(spectral(&value).aod532_estimate.is_none());
        value.aod469 = Some(0.0);
        assert!(spectral(&value).aod532_estimate.is_none());
        let zero = spectral(&sample(0.0, 1.0));
        assert_eq!(zero.aod532_estimate, Some(0.0));
        assert!(zero.angstrom_469_550.is_none());
    }

    #[test]
    fn explicit_profile_scenarios_have_correct_units_and_column_integral() {
        let value = sample(0.2, 1.5);
        let result = calculated(&value, &[1000.0, 4000.0], &[0.0, 1000.0]);
        let tau = spectral(&value).aod532_estimate.unwrap();
        let beta = result["assumed_profile_scenarios"][0]["extinction532_m_inv"][0]
            .as_f64()
            .unwrap();
        assert!((beta * 1000.0 - tau).abs() < 1e-12);
        let upper = result["assumed_profile_scenarios"][0]["extinction532_m_inv"][1]
            .as_f64()
            .unwrap();
        assert!((upper / beta - (-1.0_f64).exp()).abs() < 1e-12);
        assert!(calculated(&value, &[], &[])["assumed_profile_scenarios"]
            .as_array()
            .unwrap()
            .is_empty());
    }
}
