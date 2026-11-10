//! Complete event-quality diagnostics, independent of public frame assembly.
use crate::{quality, tensor_bundle::BundleWriter};
use firesky_native::{empirical, observer_diagnostics as diagnostic, quality as core};
use serde::Deserialize;
use serde_json::{json, Value};

pub(crate) const RETURN_MODEL: &str =
    "raw_beer_lambert_diagnostic; no cloud/sky radiance or perception mapping";
#[derive(Clone, Copy, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    Scalar,
    Regional,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Event {
    values: Vec<Vec<f32>>,
    samples: Vec<[f32; 9]>,
    offsets: Vec<f64>,
    valid_indices: Option<Vec<usize>>,
    burnable_gate: Option<Vec<bool>>,
    score_ceiling: Option<Vec<f64>>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    events: Vec<Event>,
    #[serde(default = "regional")]
    mode: Mode,
    #[serde(default = "yes")]
    apply_duration: bool,
    #[serde(default = "radius")]
    radius_km: f64,
}
fn regional() -> Mode {
    Mode::Regional
}
fn yes() -> bool {
    true
}
fn radius() -> f64 {
    1000.0
}

pub fn optics(ec: bool) -> Value {
    json!({
        "cloud_optics_source":if ec {"ec-public-tcc-rh-cloud-proxy"} else {"gfs-clwmr-icmr-material-proxy"},
        "effective_radius_source":if ec {"unavailable-ifs-public-basic-contract"} else {"unavailable-operational-gfs"},
        "cloud_optical_depth_source":"native-path-opacity-proxy",
        "cloud_opacity_proxy_source":"rh-plus-clwmr-icmr-bounded-opacity-proxy",
        "cams_profile_normalization":"aod550-normalized-aerext532-segment-integration",
        "visual_response_mapping":"empirical-material-duration-response-not-radiance",
        "unavailable_spectral_quantities":"effective-radius-spectral-radiance-rgb-brightness-calibration"
    })
}
pub fn metadata(ec: bool) -> Value {
    json!({"contract":"rust-observer-diagnostics-v1","numeric_fields":diagnostic::FIELDS,
        "observer_return_model":RETURN_MODEL,"cloud_optics_components":optics(ec),
        "quality_text_replacements":if ec {json!({"GFS":"EC public TCC/RH proxy"})} else {json!({})},
        "quality_text_replacement_fields":["quality_coverage_source","observer_column_quality_source"],
        "quality_level_source":"empirical-index-thresholds-not-probability",
        "public_forecast_complete":false})
}
pub fn render(score: &core::Quality, extra: &[f64; 20], radius: f64) -> Value {
    let mut result = if score.has_samples {
        quality::render(score, radius)
    } else {
        json!({"index":0.0,"components":{}})
    };
    result["level"] = json!(empirical::quality_level(score.index()));
    if score.has_samples {
        let fields = result["components"].as_object_mut().unwrap();
        fields.extend(optics(false).as_object().unwrap().clone());
        fields.insert("observer_return_model".into(), json!(RETURN_MODEL));
        for (name, value) in diagnostic::FIELDS.iter().zip(extra) {
            fields.insert((*name).into(), json!(value));
        }
    }
    result
}
pub fn execute(params: Params) -> Result<Value, String> {
    if params.events.len() > 4096
        || params.events.iter().map(|e| e.values.len()).sum::<usize>() > 32768
    {
        return Err("debug diagnostic event/sample limit exceeded".into());
    }
    let options = core::Options {
        mode: match params.mode {
            Mode::Scalar => core::Mode::Scalar,
            Mode::Regional => core::Mode::Regional,
        },
        apply_duration: params.apply_duration,
        local_columns: true,
        score_ceiling: false,
        radius_km: params.radius_km,
    };
    core::score(&[], options, None)?;
    let mut events = Vec::with_capacity(params.events.len());
    for event in params.events {
        let n = event.values.len();
        if n > empirical::MAX_SAMPLES
            || event
                .values
                .iter()
                .any(|v| v.len() != firesky_native::NATIONAL_OUTPUT_FIELDS)
            || event.samples.len() != n
            || event.offsets.len() != n
            || event.samples.iter().flatten().any(|v| !v.is_finite())
            || event
                .score_ceiling
                .as_ref()
                .is_some_and(|v| v.len() != n || v.iter().any(|v| !v.is_finite()))
        {
            return Err("diagnostics require matching 82-field ledgers, 9-field samples, offsets and ceilings".into());
        }
        let indices = event.valid_indices.unwrap_or_else(|| (0..n).collect());
        let flat: Vec<_> = event.values.iter().flatten().copied().collect();
        let extra = diagnostic::summarize(
            &flat,
            &event.offsets,
            &indices,
            event.burnable_gate.as_deref(),
        )?;
        let aod: Vec<_> = indices.iter().map(|&i| event.samples[i][5]).collect();
        let scalar_aod = diagnostic::sample_aod_mean(&aod);
        let rows: Vec<_> = indices
            .iter()
            .map(|&i| {
                let mut row =
                    quality::ledger_row(&event.values[i], &event.samples[i], event.offsets[i]);
                row[15] = f64::from(event.burnable_gate.as_ref().is_none_or(|g| g[i]));
                row[16] = event.score_ceiling.as_ref().map_or(2.5, |v| v[i]);
                row[17] = 1.0;
                if matches!(params.mode, Mode::Scalar) {
                    row[9] = scalar_aod;
                }
                row
            })
            .collect();
        let score = core::score(
            &rows,
            core::Options {
                score_ceiling: event.score_ceiling.is_some(),
                ..options
            },
            None,
        )?;
        events.push(render(&score, &extra, params.radius_km));
    }
    Ok(json!({"events":events,"metadata":metadata(false)}))
}

pub fn append_tensors(
    writer: &mut BundleWriter,
    values: &[f32],
    points: usize,
    rising: bool,
    ec: bool,
) -> Result<Value, String> {
    append_with_data(writer, values, points, rising, ec).map(|(metadata, _)| metadata)
}

pub(crate) fn append_with_data(
    writer: &mut BundleWriter,
    values: &[f32],
    points: usize,
    rising: bool,
    ec: bool,
) -> Result<(Value, Vec<[f64; 20]>), String> {
    let stride = 20 * firesky_native::NATIONAL_OUTPUT_FIELDS;
    if values.len() != points * stride {
        return Err("invalid diagnostic tensor shape".into());
    }
    let offsets: Vec<_> = (0..20)
        .map(|s| f64::from(if rising { -75 + s * 5 } else { -20 + s * 5 }))
        .collect();
    // Propagate ordered errors before handing a stream to the durable writer.
    let extra = diagnostic::summarize_many(values, points, &offsets, true)?;
    writer.f64(
        "quality_diagnostics",
        &[points as u64, diagnostic::FIELDS.len() as u64],
        extra.iter().flatten().copied(),
    )?;
    Ok((metadata(ec), extra))
}
