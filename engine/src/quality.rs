//! Debug JSON adapter and tensor schema for the shared empirical scorer.
use firesky_native::quality::{self, Mode, Options, Quality, Row};
use serde::Deserialize;
use serde_json::{json, Map, Value};

#[derive(Deserialize, Default, Clone, Copy)]
#[serde(rename_all = "kebab-case")]
enum ScoringMode {
    Scalar,
    #[default]
    Regional,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    rows: Vec<Vec<Row>>,
    #[serde(default)]
    mode: ScoringMode,
    #[serde(default = "yes")]
    apply_duration: bool,
    #[serde(default = "yes")]
    local_columns: bool,
    #[serde(default)]
    score_ceiling: bool,
    #[serde(default = "radius")]
    radius_km: f64,
    scalar_aod: Option<Vec<Vec<f64>>>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NowcastParams {
    rows: Vec<Row>,
    #[serde(default = "yes")]
    apply_duration: bool,
    #[serde(default = "yes")]
    local_columns: bool,
    #[serde(default)]
    score_ceiling: bool,
    #[serde(default = "radius")]
    radius_km: f64,
    #[serde(default)]
    parallel_instants: bool,
}
fn yes() -> bool {
    true
}
fn radius() -> f64 {
    200.0
}

pub fn render(score: &Quality, radius: f64) -> Value {
    if !score.has_samples {
        return json!({"index":0.0,"components":{"scoring_scope":quality::SCORING_SCOPE}});
    }
    let mut components = Map::new();
    for (&name, &value) in quality::FIELDS.iter().zip(&score.values).skip(1) {
        if name.starts_with("observation_")
            && name != "observation_burnability_veto_steps"
            && !score.score_ceiling
        {
            continue;
        }
        components.insert(name.into(), json!(value));
    }
    components.insert("scoring_scope".into(), json!(quality::SCORING_SCOPE));
    components.insert("score_formula".into(), json!(quality::FORMULA));
    components.insert(
        "quality_limiting_factor".into(),
        json!(quality::LIMITING_FACTORS[score.limiting]),
    );
    components.insert(
        "observer_column_quality_source".into(),
        json!(if score.local_columns {
            quality::LOCAL_SOURCE
        } else {
            quality::NO_LOCAL_SOURCE
        }),
    );
    components.insert(
        "quality_coverage_source".into(),
        json!(quality::coverage_source(radius)),
    );
    json!({"index":score.index(),"components":components})
}

pub fn metadata(radius: f64) -> Value {
    json!({
        "contract":"rust-observer-quality-v1",
        "input_fields":quality::INPUT_NAMES,"numeric_fields":quality::FIELDS,
        "instant_fields":quality::INSTANT_NAMES,
        "flag_fields":["has_samples","local_columns","score_ceiling","limiting_factor_code"],
        "limiting_factors":quality::LIMITING_FACTORS,
        "scoring_scope":quality::SCORING_SCOPE,"score_formula":quality::FORMULA,
        "observer_column_quality_source":quality::LOCAL_SOURCE,
        "missing_observer_column_quality_source":quality::NO_LOCAL_SOURCE,
        "quality_coverage_source":quality::coverage_source(radius),
        "window_policy":"serial-f64-earliest-computed-maximum-v1",
        "numpy_blas_tie_parity_proven":false
    })
}

fn instant_values(rows: &[Row], mode: Mode, force_valid: bool) -> Vec<Vec<f64>> {
    rows.iter()
        .map(|row| {
            let allowed = (force_valid || row[17] >= 0.5)
                && row[6] >= 0.05
                && (mode == Mode::Regional || row[15] >= 0.5);
            quality::score_instant(row, allowed, true).to_vec()
        })
        .collect()
}

fn instant_ledger(rows: &[Row], mode: Mode, force_valid: bool) -> Vec<Value> {
    instant_values(rows, mode, force_valid)
        .into_iter()
        .map(|values| {
            let mut object = Map::new();
            for (field, value) in quality::INSTANT_NAMES.iter().zip(values) {
                object.insert(
                    (*field).into(),
                    if *field == "physically_burnable" {
                        json!(value >= 0.5)
                    } else {
                        json!(value)
                    },
                );
            }
            Value::Object(object)
        })
        .collect()
}

const NOWCAST_EVENT_FIELDS: [&str; 10] = [
    "index",
    "burnable_cloud_coverage_peak",
    "forward_burnable_cloud_field_amount_peak",
    "forward_burnable_cloud_load_peak",
    "forward_burnable_cloud_material_load_peak",
    "forward_burnable_cloud_incoming_aerosol_load_mean",
    "forward_burnable_cloud_ratio_peak",
    "brightness_color",
    "cloud_material",
    "quality_aod550",
];

fn nowcast_event_projection(score: &Quality) -> Vec<f64> {
    NOWCAST_EVENT_FIELDS
        .iter()
        .map(|field| {
            if *field == "index" {
                score.index()
            } else {
                let index = quality::FIELDS
                    .iter()
                    .position(|name| name == field)
                    .expect("nowcast event projection field exists");
                score.values[index]
            }
        })
        .collect()
}

/// Event composition and its per-step instantaneous ledger share one input
/// tensor. This avoids a second process round-trip for the nowcast caller
/// while retaining the general multi-event scorer contract unchanged.
pub fn execute_nowcast(params: NowcastParams) -> Result<Value, String> {
    if params.rows.len() > 32_768 {
        return Err("nowcast scoring requires <=32768 samples".into());
    }
    let options = Options {
        mode: Mode::Scalar,
        apply_duration: params.apply_duration,
        local_columns: params.local_columns,
        score_ceiling: params.score_ceiling,
        radius_km: params.radius_km,
    };
    let instant_options = Options {
        apply_duration: false,
        ..options
    };
    let event = quality::score(&params.rows, options, None)?;
    let mut instant_event_projection = Vec::with_capacity(params.rows.len());
    if params.parallel_instants && params.rows.len() >= 64 {
        let scores = quality::score_batch_with(
            params.rows.len(),
            instant_options,
            true,
            |index, instant_options| {
                let mut row = params.rows[index];
                // Preserve the established nowcast adapter semantics: each
                // step is an independent no-duration event ledger, with only
                // visibility and observation burnability gates still applied.
                row[17] = 1.0;
                quality::score(&[row], instant_options, None)
            },
        )?;
        instant_event_projection.extend(scores.iter().map(nowcast_event_projection));
    } else {
        for mut row in params.rows.iter().copied() {
            row[17] = 1.0;
            instant_event_projection.push(nowcast_event_projection(&quality::score(
                &[row],
                instant_options,
                None,
            )?));
        }
    }
    let instant = instant_values(&params.rows, options.mode, true);
    let mut nowcast_metadata = metadata(params.radius_km);
    nowcast_metadata["nowcast_event_fields"] = json!(NOWCAST_EVENT_FIELDS);
    Ok(json!({
        "contract":"rust-desktop-nowcast-quality-v2",
        "event":render(&event, params.radius_km),
        "instant":instant,
        "instant_event_projection":instant_event_projection,
        "metadata":nowcast_metadata,
    }))
}

pub fn execute(params: Params) -> Result<Value, String> {
    if params.rows.len() > 4096
        || params.rows.iter().map(Vec::len).sum::<usize>() > 32_768
        || params
            .scalar_aod
            .as_ref()
            .is_some_and(|v| v.len() != params.rows.len())
    {
        return Err("debug scoring requires <=4096 events and <=32768 total samples".into());
    }
    let options = Options {
        mode: if matches!(params.mode, ScoringMode::Scalar) {
            Mode::Scalar
        } else {
            Mode::Regional
        },
        apply_duration: params.apply_duration,
        local_columns: params.local_columns,
        score_ceiling: params.score_ceiling,
        radius_km: params.radius_km,
    };
    quality::score(&[], options, None)?;
    if options.mode == Mode::Regional && params.scalar_aod.is_some() {
        return Err("scalar_aod is only defined for scalar composition".into());
    }
    let mut events = Vec::with_capacity(params.rows.len());
    let mut instants = Vec::with_capacity(params.rows.len());
    for (i, rows) in params.rows.iter().enumerate() {
        let aod = params.scalar_aod.as_ref().map(|v| v[i].as_slice());
        let score = quality::score(rows, options, aod)?;
        events.push(render(&score, params.radius_km));
        let instant = instant_ledger(rows, options.mode, false);
        instants.push(instant);
    }
    Ok(json!({"events":events,"instant":instants,"metadata":metadata(params.radius_km)}))
}

pub fn assessment_rows(
    assessment: &[f32],
    samples: &[f32],
    rising: bool,
) -> Result<[Row; 20], String> {
    if assessment.len() != 20 * firesky_native::NATIONAL_OUTPUT_FIELDS || samples.len() != 20 * 9 {
        return Err("quality preparation requires one complete canonical observer".into());
    }
    Ok(std::array::from_fn(|step| {
        let v = &assessment[step * firesky_native::NATIONAL_OUTPUT_FIELDS..];
        let s = &samples[step * 9..];
        // Native ledger: view/burn/local, then total/material/incoming loads.
        assessment_row(v, s, step, rising)
    }))
}

fn assessment_row(v: &[f32], s: &[f32], step: usize, rising: bool) -> Row {
    ledger_row(
        v,
        s,
        f64::from(if rising {
            -75 + step as i32 * 5
        } else {
            -20 + step as i32 * 5
        }),
    )
}

pub(crate) fn ledger_row(v: &[f32], s: &[f32], offset: f64) -> Row {
    [
        f64::from(v[43]),
        f64::from(v[44]),
        f64::from(v[45]),
        f64::from(v[46]),
        f64::from(v[62]),
        f64::from(v[60]),
        f64::from(v[5]),
        f64::from(v[4]),
        offset,
        f64::from(s[5]),
        f64::from(v[54]),
        f64::from(v[55]),
        f64::from(v[56]),
        f64::from(v[61]),
        f64::from(v[63]),
        1.0,
        2.5,
        f64::from(v[0] >= 0.5),
    ]
}

pub fn append_tensors(
    writer: &mut crate::tensor_bundle::BundleWriter,
    assessment: &[f32],
    samples: &[f32],
    points: usize,
    rising: bool,
    radius: f64,
) -> Result<Value, String> {
    append_with_data(writer, assessment, samples, points, rising, radius, false)
        .map(|(metadata, _)| metadata)
}

pub(crate) struct Retained {
    pub scores: Vec<Quality>,
    pub marks: Vec<InstantMark>,
}

#[derive(Clone, Copy)]
pub(crate) struct InstantMark {
    pub index: f64,
    pub coverage: f32,
    pub physically_burnable: bool,
}

pub(crate) fn append_with_data(
    writer: &mut crate::tensor_bundle::BundleWriter,
    assessment: &[f32],
    samples: &[f32],
    points: usize,
    rising: bool,
    radius: f64,
    retain: bool,
) -> Result<(Value, Option<Retained>), String> {
    let assessment_stride = 20 * firesky_native::NATIONAL_OUTPUT_FIELDS;
    if assessment.len() != points * assessment_stride || samples.len() != points * 20 * 9 {
        return Err("quality source tensors have invalid dimensions".into());
    }
    let options = Options {
        mode: Mode::Regional,
        apply_duration: true,
        local_columns: true,
        score_ceiling: false,
        radius_km: radius,
    };
    let started = std::time::Instant::now();
    let scores = quality::score_batch_with(points, options, true, |point, options| {
        let v = &assessment[point * assessment_stride..(point + 1) * assessment_stride];
        let s = &samples[point * 180..(point + 1) * 180];
        quality::score(&assessment_rows(v, s, rising)?, options, None)
    })?;
    let score_ms = started.elapsed().as_secs_f64() * 1000.0;
    writer.f64(
        "quality",
        &[points as u64, quality::FIELDS.len() as u64],
        scores.iter().flat_map(|q| q.values),
    )?;
    writer.f32(
        "quality_flags",
        &[points as u64, 4],
        scores.iter().flat_map(Quality::flags),
    )?;
    // Public frames need only a small projection, not N*20*19 doubles.
    let mut marks = if retain {
        Vec::with_capacity(points * 20)
    } else {
        Vec::new()
    };
    writer.f64(
        "instant_quality",
        &[points as u64, 20, quality::INSTANT_NAMES.len() as u64],
        (0..points * 20).flat_map(|i| {
            let v = &assessment[i * firesky_native::NATIONAL_OUTPUT_FIELDS..];
            let s = &samples[i * 9..];
            let row = assessment_row(v, s, i % 20, rising);
            let instant = quality::score_instant(&row, row[17] >= 0.5 && row[6] >= 0.05, true);
            if retain {
                marks.push(InstantMark {
                    index: if row[17] >= 0.5 { instant[0] } else { 0.0 },
                    coverage: if row[17] >= 0.5 {
                        instant[6] as f32
                    } else {
                        0.0
                    },
                    physically_burnable: row[17] >= 0.5 && instant[5] >= 0.5,
                });
            }
            instant
        }),
    )?;
    let mut metadata = metadata(radius);
    metadata["event_composition_ms"] = json!(score_ms);
    metadata["instant_and_output_ms"] = json!(started.elapsed().as_secs_f64() * 1000.0 - score_ms);
    Ok((metadata, retain.then_some(Retained { scores, marks })))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn json_preserves_the_neighbors_of_physical_thresholds() {
        for threshold in [0.005_f64, 0.05, 1e-9] {
            for bits in [
                threshold.to_bits() - 1,
                threshold.to_bits(),
                threshold.to_bits() + 1,
            ] {
                let value = f64::from_bits(bits);
                let parsed: f64 = serde_json::from_str(&value.to_string()).unwrap();
                assert_eq!(parsed.to_bits(), bits);
            }
        }
    }
    #[test]
    fn projects_the_current_native_ledger_without_reusing_legacy_slots() {
        let mut values = vec![0.0; 20 * firesky_native::NATIONAL_OUTPUT_FIELDS];
        for row in values
            .as_chunks_mut::<{ firesky_native::NATIONAL_OUTPUT_FIELDS }>()
            .0
        {
            for (i, v) in row.iter_mut().enumerate() {
                *v = i as f32;
            }
            row[0] = 1.0;
        }
        let samples = vec![0.25; 180];
        let dawn = assessment_rows(&values, &samples, true).unwrap();
        assert_eq!(
            &dawn[0][..8],
            &[43.0, 44.0, 45.0, 46.0, 62.0, 60.0, 5.0, 4.0]
        );
        assert_eq!(
            &dawn[0][8..],
            &[-75.0, 0.25, 54.0, 55.0, 56.0, 61.0, 63.0, 1.0, 2.5, 1.0]
        );
        let dusk = assessment_rows(&values, &samples, false).unwrap();
        assert_eq!(dusk[19][8], 75.0);
        assert!(assessment_rows(&values[..82], &samples, false).is_err());
    }
}
