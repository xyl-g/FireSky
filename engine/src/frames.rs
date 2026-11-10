//! Complete compact regional frames and generated point cores, not cache APIs.
use crate::{
    astronomy,
    characteristics::{self, Operation, Phase},
    diagnostics,
    profile_diagnostics::frame_index,
    quality, time_contract,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use chrono::{DateTime, Utc};
use firesky_native::{empirical, profile_diagnostics::Stencil, quality as scoring};
use serde::{
    ser::{SerializeMap, SerializeSeq},
    Deserialize, Serialize,
};
use serde_json::{json, Map, Value};
use std::time::Instant;

const REFINEMENT: &str = "raytraced-sky-v4-cpu";
const AEROSOL_MODE: &str = "cams-aerext532-11level-aod550-normalized-segment-v1";
pub(crate) const CORE_ALGORITHM: &str = "national-point-core-2026.11.04.87-pre-veto-loss-ledger-v1-cams-aerext532-11level-aod550-normalized-segment-v1-near-low-return-diagnostic-v1";
const CONFIDENCE: &str = "input-and-geometry-support-not-event-hit-probability";
const QUALITY_SEMANTICS: &str = "empirical-potential-index-0-to-2.5-not-probability";
const LEAD: &str = "valid-time-minus-model-cycle-hours";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DebugInput {
    points: Vec<Point>,
    times: Vec<Times>,
    values: Vec<Vec<Vec<f32>>>,
    samples: Vec<Vec<[f32; 9]>>,
    columns: Vec<Vec<Vec<f32>>>,
    profiles: Option<Vec<Vec<[f32; 7]>>>,
    aerosol_sources: Vec<String>,
    aerosol_qualities: Vec<String>,
    rows: usize,
    cols: usize,
    origin: [f32; 2],
    spacing: f32,
    date: String,
    cycle_utc: String,
    phenomenon: crate::astronomy::Phenomenon,
    model_source: String,
    analysis_radius_km: f64,
    #[serde(default)]
    include_point_core: bool,
    point_spacing_deg: Option<f64>,
}
pub fn execute(params: DebugInput) -> Result<Value, String> {
    let n = params.points.len();
    let cells = params
        .rows
        .checked_mul(params.cols)
        .ok_or("frame grid overflow")?;
    if n == 0
        || n > 64
        || params.values.len() != n
        || params.samples.len() != n
        || params.values.iter().any(|p| p.len() != 20)
        || params.values.iter().flatten().any(|v| v.len() != 82)
        || params.samples.iter().any(|p| p.len() != 20)
        || cells == 0
        || cells > 4096
        || params.columns.is_empty()
        || params.columns.len() > 64
        || params.columns.len() * cells * 36 > 65536
        || params.columns.iter().any(|f| f.len() != cells)
        || params.columns.iter().flatten().any(|v| v.len() != 36)
        || params
            .profiles
            .as_ref()
            .is_some_and(|p| p.len() != n || p.iter().any(|r| r.len() != 20))
        || !matches!(params.model_source.as_str(), "gfs" | "ec")
    {
        return Err("invalid bounded inline frame source tensors".into());
    }
    let rising = matches!(params.phenomenon, crate::astronomy::Phenomenon::Dawn);
    let values: Vec<_> = params.values.into_iter().flatten().flatten().collect();
    let samples: Vec<_> = params.samples.into_iter().flatten().flatten().collect();
    let columns: Vec<[f32; 36]> = params
        .columns
        .into_iter()
        .flatten()
        .map(|v| v.try_into().unwrap())
        .collect();
    let profiles: Option<Vec<_>> = params.profiles.map(|v| v.into_iter().flatten().collect());
    let offsets: Vec<_> = (0..20)
        .map(|s| f64::from(if rising { -75 + s * 5 } else { -20 + s * 5 }))
        .collect();
    let extras = firesky_native::observer_diagnostics::summarize_many(&values, n, &offsets, true)?;
    let mut scores = Vec::with_capacity(n);
    let mut marks = Vec::with_capacity(n * 20);
    for i in 0..n {
        let rows = quality::assessment_rows(
            &values[i * 20 * 82..(i + 1) * 20 * 82],
            &samples[i * 180..(i + 1) * 180],
            rising,
        )?;
        scores.push(scoring::score(
            &rows,
            scoring::Options {
                mode: scoring::Mode::Regional,
                apply_duration: true,
                local_columns: true,
                score_ceiling: false,
                radius_km: params.analysis_radius_km,
            },
            None,
        )?);
        for row in rows {
            let instant = scoring::score_instant(&row, row[17] >= 0.5 && row[6] >= 0.05, true);
            marks.push(quality::InstantMark {
                index: if row[17] >= 0.5 { instant[0] } else { 0.0 },
                coverage: if row[17] >= 0.5 {
                    instant[6] as f32
                } else {
                    0.0
                },
                physically_burnable: row[17] >= 0.5 && instant[5] >= 0.5,
            });
        }
    }
    let payload = Payload::prepare(Input {
        points: &params.points,
        times: &params.times,
        values: &values,
        samples: &samples,
        columns: &columns,
        profiles: profiles.as_deref(),
        scores: &scores,
        marks: &marks,
        extras: &extras,
        aerosol_sources: &params.aerosol_sources,
        aerosol_qualities: &params.aerosol_qualities,
        rows: params.rows,
        cols: params.cols,
        origin: params.origin,
        spacing: params.spacing,
        date: &params.date,
        cycle: time_contract::parse_utc(&params.cycle_utc)?,
        rising,
        ec: params.model_source == "ec",
        include_point_core: params.include_point_core,
        point_spacing_deg: params.point_spacing_deg,
    })?;
    serde_json::to_value(payload).map_err(|e| e.to_string())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QuantizeInput {
    values: Vec<Option<f32>>,
    encoding: String,
    scale: Option<f32>,
}
pub fn quantize(params: QuantizeInput) -> Result<Value, String> {
    let mode = match params.encoding.as_str() {
        "u8" => 0,
        "u16" => 1,
        "i16" => 2,
        "raw-u8" => 3,
        _ => return Err("unknown frame encoding".into()),
    };
    let scale = params.scale.unwrap_or(1.0);
    if params.values.len() > 32768
        || !scale.is_finite()
        || scale <= 0.0
        || mode == 3
            && params.values.iter().any(|v| {
                v.is_none_or(|v| !v.is_finite() || !(0.0..=255.0).contains(&v) || v.fract() != 0.0)
            })
    {
        return Err("invalid bounded frame quantization input".into());
    }
    Ok(
        json!({"encoding":params.encoding,"value":encode(params.values.into_iter().map(|v|v.unwrap_or(f32::NAN)),mode,scale)}),
    )
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Point {
    pub name: String,
    pub lat: f64,
    pub lon: f64,
    pub timezone_name: String,
    pub elevation_m: f64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Times {
    pub evaluation: [f64; 20],
    pub anchor: f64,
}
pub(crate) struct Input<'a> {
    pub points: &'a [Point],
    pub times: &'a [Times],
    pub values: &'a [f32],
    pub samples: &'a [f32],
    pub columns: &'a [[f32; 36]],
    pub profiles: Option<&'a [[f32; 7]]>,
    pub scores: &'a [scoring::Quality],
    pub marks: &'a [quality::InstantMark],
    pub extras: &'a [[f64; 20]],
    pub aerosol_sources: &'a [String],
    pub aerosol_qualities: &'a [String],
    pub rows: usize,
    pub cols: usize,
    pub origin: [f32; 2],
    pub spacing: f32,
    pub date: &'a str,
    pub cycle: DateTime<Utc>,
    pub rising: bool,
    pub ec: bool,
    pub include_point_core: bool,
    pub point_spacing_deg: Option<f64>,
}

/// Stream results one observer at a time; do not retain N JSON component maps.
pub(crate) struct Payload<'a> {
    input: Input<'a>,
    stencils: Vec<Stencil>,
    frames: Value,
    single: Option<(Value, Option<Value>)>,
    started: Instant,
}

pub(crate) fn optics(ec: bool) -> Value {
    let mut v = diagnostics::optics(ec);
    if ec {
        v.as_object_mut().unwrap().retain(|k, _| {
            matches!(
                k.as_str(),
                "cloud_optics_source" | "effective_radius_source" | "cloud_optical_depth_source"
            )
        });
    }
    v
}
fn clean(v: f32) -> f32 {
    if v.is_nan() {
        0.0
    } else {
        v.clamp(f32::MIN, f32::MAX)
    }
}
fn finite_zero(v: f32) -> f32 {
    if v.is_finite() {
        v
    } else {
        0.0
    }
}
fn unit(v: f32) -> f32 {
    clean(v).clamp(0.0, 1.0)
}
fn encode(values: impl Iterator<Item = f32>, mode: u8, scale: f32) -> String {
    let mut bytes = Vec::new();
    for value in values {
        match mode {
            0 => bytes.push((unit(value) * 255.0).round_ties_even() as u8),
            1 => bytes.extend(
                ((clean(value).max(0.0) / scale)
                    .round_ties_even()
                    .clamp(0.0, 65535.0) as u16)
                    .to_le_bytes(),
            ),
            2 => bytes.extend(
                ((clean(value) / scale)
                    .round_ties_even()
                    .clamp(-32768.0, 32767.0) as i16)
                    .to_le_bytes(),
            ),
            _ => bytes.push(value as u8),
        }
    }
    STANDARD.encode(bytes)
}
fn family(v: f32) -> u8 {
    let code = finite_zero(v).round_ties_even().rem_euclid(256.0) as u8;
    if (1..=3).contains(&code) {
        code
    } else {
        0
    }
}
fn family_name(code: u8) -> Option<&'static str> {
    match code {
        1 => Some("high"),
        2 => Some("middle"),
        3 => Some("low"),
        _ => None,
    }
}
fn unknown_phase(source: &str) -> Value {
    json!({"label":"unknown","ice_fraction":null,"confidence":0.0,"source":source})
}
fn phase_label(value: &Value) -> Option<String> {
    value
        .get("label")
        .and_then(Value::as_str)
        .map(str::to_owned)
}
fn local(epoch: f64, timezone: &str) -> Result<String, String> {
    astronomy::local_iso(
        crate::assessment::from_epoch(epoch)?,
        timezone.parse().map_err(|_| "unknown frame timezone")?,
    )
}
fn mean(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    // NumPy's contiguous float64 reduction uses eight accumulators for n>=8.
    if values.len() < 8 {
        return values.iter().fold(-0.0, |s, v| s + v) / values.len() as f64;
    }
    let mut lanes: [f64; 8] = values[..8].try_into().unwrap();
    let stop = values.len() - values.len() % 8;
    for chunk in values[8..stop].as_chunks::<8>().0 {
        for (a, b) in lanes.iter_mut().zip(chunk) {
            *a += b;
        }
    }
    let mut sum = ((lanes[0] + lanes[1]) + (lanes[2] + lanes[3]))
        + ((lanes[4] + lanes[5]) + (lanes[6] + lanes[7]));
    for v in &values[stop..] {
        sum += v;
    }
    sum / values.len() as f64
}

impl<'a> Payload<'a> {
    pub(crate) fn point_core(&self) -> Option<&Value> {
        self.single.as_ref().and_then(|(_, core)| core.as_ref())
    }
    pub(crate) fn prepare(input: Input<'a>) -> Result<Self, String> {
        let n = input.points.len();
        let cells = input
            .rows
            .checked_mul(input.cols)
            .ok_or("frame grid overflow")?;
        if n == 0
            || n > 500_000
            || cells == 0
            || !input.columns.len().is_multiple_of(cells)
            || input.columns.is_empty()
            || input.columns.len() / cells > 64
            || input.values.len() != n * 20 * 82
            || input.samples.len() != n * 20 * 9
            || input.times.len() != n
            || input.scores.len() != n
            || input.marks.len() != n * 20
            || input.extras.len() != n
            || input.aerosol_sources.len() != n
            || input.aerosol_qualities.len() != n
            || input
                .profiles
                .is_some_and(|p| p.len() != n * 20 || p.iter().flatten().any(|v| !v.is_finite()))
            || input.include_point_core && n != 1
            || input
                .values
                .iter()
                .chain(input.samples)
                .any(|v| !v.is_finite())
            || input
                .point_spacing_deg
                .is_some_and(|v| !v.is_finite() || v <= 0.0)
        {
            return Err("invalid complete frame tensor dimensions or options".into());
        }
        time_contract::parse_date(input.date)?;
        let mut stencils = Vec::with_capacity(n);
        for (i, p) in input.points.iter().enumerate() {
            if p.name.is_empty()
                || p.name.len() > 256
                || p.timezone_name.len() > 128
                || !p.lat.is_finite()
                || !p.lon.is_finite()
                || !(-90.0..=90.0).contains(&p.lat)
                || !(-180.0..=180.0).contains(&p.lon)
                || !p.elevation_m.is_finite()
                || !(-500.0..=9000.0).contains(&p.elevation_m)
            {
                return Err("invalid named frame observer".into());
            }
            let t = &input.times[i];
            if !t.anchor.is_finite()
                || t.evaluation.iter().any(|v| !v.is_finite())
                || t.evaluation.iter().enumerate().any(|(s, v)| {
                    ((v - t.anchor) / 60.0
                        - f64::from(if input.rising {
                            -75 + s as i32 * 5
                        } else {
                            -20 + s as i32 * 5
                        }))
                    .abs()
                        > 1e-6
                })
            {
                return Err(
                    "frame evaluation offsets do not match canonical event timeline".into(),
                );
            }
            local(t.anchor, &p.timezone_name)?;
            if !input.scores[i].has_samples {
                return Err(format!("observer {i} has no valid frame samples"));
            }
            stencils.push(Stencil::new(
                [p.lat as f32, p.lon as f32],
                input.origin,
                input.spacing,
                input.rows,
                input.cols,
            )?);
        }
        let started = Instant::now();
        let mut payload = Self {
            input,
            stencils,
            frames: Value::Null,
            single: None,
            started,
        };
        payload.frames = payload.compact_frames()?;
        if payload.input.include_point_core {
            payload.single = Some(payload.point(0)?);
        }
        Ok(payload)
    }
    fn processed(&self) -> usize {
        self.input
            .values
            .as_chunks::<82>()
            .0
            .iter()
            .filter(|v| v[0] >= 0.5)
            .count()
    }
    fn v(&self, i: usize, s: usize) -> &[f32] {
        &self.input.values[(i * 20 + s) * 82..(i * 20 + s + 1) * 82]
    }
    fn sample(&self, i: usize, s: usize) -> &[f32] {
        &self.input.samples[(i * 20 + s) * 9..(i * 20 + s + 1) * 9]
    }
    fn model(&self) -> &'static str {
        if self.input.ec {
            "ec"
        } else {
            "gfs"
        }
    }
    fn source(&self) -> &'static str {
        if self.input.ec {
            "ec-public-tcc-rh-proxy-selected-column-bilinear-diagnostic"
        } else {
            "gfs-selected-column-bilinear-diagnostic"
        }
    }
    fn proxy_source(&self) -> &'static str {
        if self.input.profiles.is_none() {
            "gfs-layer-aggregate"
        } else if self.input.ec {
            "pressure-level-profile"
        } else {
            "gfs-pressure-profile-plus-average-boundary-envelope"
        }
    }
    fn boundary(&self) -> &'static str {
        if !self.input.ec && self.input.profiles.is_some() {
            "average-envelope-not-instantaneous"
        } else {
            "pressure-profile-proxy"
        }
    }
    fn selected(&self, i: usize, s: usize) -> Result<[f32; 7], String> {
        let cells = self.input.rows * self.input.cols;
        let frames = self.input.columns.len() / cells;
        let sample = self.sample(i, s);
        let indices = [
            frame_index(sample[2], frames),
            frame_index(sample[3], frames),
        ];
        let mut out = [0.0; 7];
        self.stencils[i].sample_with(sample[4], &mut out, |right, cell, field| {
            self.input.columns[indices[usize::from(right)] * cells + cell]
                [[0, 1, 2, 3, 7, 15, 16][field]]
        })?;
        Ok(out.map(unit))
    }
    fn profile(&self, i: usize, s: usize, selected: [f32; 7]) -> [f32; 7] {
        self.input.profiles.map_or_else(
            || {
                let code = if selected[3] >= 0.62 {
                    8.0
                } else if selected[0] >= selected[1] {
                    1.0
                } else {
                    2.0
                };
                [
                    if selected[..3]
                        .iter()
                        .chain(&selected[4..5])
                        .copied()
                        .fold(0.0_f32, f32::max)
                        < 0.06
                    {
                        0.0
                    } else {
                        code
                    },
                    0.0,
                    0.0,
                    selected[3],
                    -1.0,
                    0.0,
                    0.55,
                ]
            },
            |p| p[i * 20 + s],
        )
    }
    fn geometry(&self, i: usize, s: usize) -> (Option<&'static str>, [f64; 3]) {
        let v = self.v(i, s);
        let name = family_name(family(v[47]));
        (
            name,
            if name.is_none() {
                [0.0; 3]
            } else {
                [
                    finite_zero(v[70]) as f64,
                    finite_zero(v[71]) as f64,
                    finite_zero(v[72]) as f64,
                ]
            },
        )
    }
    fn instant(&self, i: usize, s: usize) -> scoring::Instant {
        let row = quality::ledger_row(self.v(i, s), self.sample(i, s), 0.0);
        scoring::score_instant(&row, row[17] >= 0.5 && row[6] >= 0.05, true)
    }
    fn selected_phase(&self, p: [f32; 7]) -> Value {
        if self.input.profiles.is_none() {
            return unknown_phase("pressure-profile-unavailable");
        }
        let mut phase = characteristics::phase(Some(p[2] as f64), Some(p[1] as f64));
        phase["source"] = json!("gfs-selected-observer-column-condensate");
        phase
    }
    fn genus(
        &self,
        f: Option<&str>,
        phase: &Value,
        g: [f64; 3],
        deep: f64,
        proxy: bool,
    ) -> Result<Value, String> {
        let (base, top) = if f.is_some() {
            (Some((g[0] - g[2]).max(0.0)), Some((g[1] - g[2]).max(0.0)))
        } else {
            (None, None)
        };
        let phase = phase_label(phase).map(Phase::Label);
        characteristics::execute(if proxy {
            Operation::GenusProxy {
                pressure_family: f.map(str::to_owned),
                cloud_phase: phase,
                cloud_base_agl_m: base,
                cloud_top_agl_m: top,
                convective_risk: deep,
                model_source: self.model().into(),
            }
        } else {
            Operation::Genus {
                dominant_pressure_family: f.map(str::to_owned),
                cloud_phase: phase,
                cloud_base_agl_m: base,
                cloud_top_agl_m: top,
                convective_risk: deep,
                model_source: self.model().into(),
            }
        })
    }
    fn color(
        &self,
        index: f64,
        instant: scoring::Instant,
        solar: f32,
        deep: f64,
        phase: &Value,
    ) -> Result<Value, String> {
        characteristics::execute(Operation::Color {
            burn_score: None,
            quality_score: Some(index / 2.5),
            vividness_score: instant[7],
            solar_elevation_deg: solar as f64,
            aod550: instant[17],
            solar_incoming_illumination_score: instant[11],
            deep_cloud_risk: deep,
            cloud_phase: phase_label(phase).unwrap_or_else(|| "unknown".into()),
        })
    }
    fn compact_frames(&self) -> Result<Value, String> {
        let n = self.input.points.len();
        let mut frames = Vec::with_capacity(20);
        for s in 0..20 {
            let selected = (0..n)
                .map(|i| self.selected(i, s))
                .collect::<Result<Vec<_>, _>>()?;
            let profiles: Vec<_> = (0..n).map(|i| self.profile(i, s, selected[i])).collect();
            let mut frame = Map::new();
            frame.insert(
                "selected_column_diagnostic_source".into(),
                json!(self.source()),
            );
            frame.insert(
                "offset_minutes".into(),
                json!(if self.input.rising {
                    -75 + s as i32 * 5
                } else {
                    -20 + s as i32 * 5
                }),
            );
            for (name, field, mode, scale) in [
                ("forward_view_cloud_field_amount", 43, 0, 1.0),
                ("forward_burnable_cloud_field_amount", 44, 0, 1.0),
                ("forward_burnable_cloud_load", 45, 1, 0.01),
                ("forward_burnable_cloud_material_load", 46, 1, 0.01),
                ("forward_burnable_cloud_incoming_aerosol_load", 62, 1, 0.01),
                ("solar_incoming_block_risk", 60, 0, 1.0),
                ("observer_local_cloud_field_amount", 54, 0, 1.0),
                ("observer_local_burnable_cloud_field_amount", 55, 0, 1.0),
                ("observer_local_burnable_cloud_load", 56, 1, 0.01),
                ("observer_local_burnable_cloud_material_load", 61, 1, 0.01),
                (
                    "observer_local_burnable_cloud_incoming_aerosol_load",
                    63,
                    1,
                    0.01,
                ),
                ("burn_score", 1, 0, 1.0),
                ("intensity_score", 2, 0, 1.0),
                ("extent_score", 3, 0, 1.0),
                ("vividness_score", 4, 0, 1.0),
                ("high_burn", 51, 0, 1.0),
                ("mid_burn", 52, 0, 1.0),
                ("low_burn", 53, 0, 1.0),
                ("illumination_score", 40, 0, 1.0),
                ("path_clearance_score", 37, 0, 1.0),
                ("blocking_risk", 39, 0, 1.0),
                ("target_score", 11, 0, 1.0),
                ("target_deck_connected", 64, 0, 1.0),
                ("target_deck_attenuated_fraction", 65, 0, 1.0),
                ("target_deep_risk", 66, 0, 1.0),
                ("cloud_optical_depth", 67, 1, 0.001),
                ("peak_blocking", 68, 0, 1.0),
                ("clear_gap_count", 69, 1, 0.01),
                ("observer_return_optical_depth", 73, 1, 0.001),
                ("observer_return_transmission", 74, 0, 1.0),
                ("observer_local_return_optical_depth", 75, 1, 0.001),
                ("observer_local_return_transmission", 76, 0, 1.0),
            ] {
                frame.insert(
                    name.into(),
                    json!(encode((0..n).map(|i| self.v(i, s)[field]), mode, scale)),
                );
            }
            for (name, field) in [
                ("forward_candidate_cloud_field_amount", 77),
                ("forward_target_eligible_cloud_field_amount", 78),
                ("forward_solar_geometry_reachable_cloud_field_amount", 79),
                ("forward_external_cloud_path_transmitted_field_amount", 80),
                ("forward_unoccluded_burn_potential_upper_bound", 81),
            ] {
                frame.insert(
                    name.into(),
                    json!(encode(
                        (0..n).map(|i| if self.v(i, s)[0] >= 0.5 {
                            self.v(i, s)[field]
                        } else {
                            0.0
                        }),
                        0,
                        1.0
                    )),
                );
            }
            for (name, field) in [
                ("high_cloud", 0),
                ("mid_cloud", 1),
                ("low_cloud", 2),
                ("cloud_evidence", 4),
            ] {
                frame.insert(
                    name.into(),
                    json!(encode(selected.iter().map(|p| p[field]), 0, 1.0)),
                );
            }
            for (name, field) in [
                ("dominant_burnable_cloud_base_asl_mean_m", 0),
                ("dominant_burnable_cloud_top_asl_mean_m", 1),
                ("dominant_burnable_target_terrain_asl_mean_m", 2),
            ] {
                frame.insert(
                    name.into(),
                    json!(encode(
                        (0..n).map(|i| self.geometry(i, s).1[field] as f32),
                        2,
                        10.0
                    )),
                );
            }
            for (name, mode, scale) in [
                ("valid", 3, 1.0),
                ("instant_quality_index", 1, 0.001),
                ("physically_burnable", 3, 1.0),
                ("burnable_cloud_coverage", 0, 1.0),
                ("forward_burnable_cloud_ratio", 0, 1.0),
                ("selected_column_cloud_material_class_code", 3, 1.0),
                ("convective_risk", 0, 1.0),
                ("dominant_burnable_pressure_family_code", 3, 1.0),
                ("local_aod550", 1, 0.001),
            ] {
                frame.insert(
                    name.into(),
                    json!(encode(
                        (0..n).map(|i| {
                            let v = self.v(i, s);
                            let m = self.input.marks[i * 20 + s];
                            match name {
                                "valid" => f32::from(v[0] >= 0.5),
                                "instant_quality_index" => m.index as f32,
                                "physically_burnable" => f32::from(m.physically_burnable),
                                "burnable_cloud_coverage" => m.coverage,
                                "forward_burnable_cloud_ratio" => {
                                    if v[43] > 1e-6 {
                                        v[44] / v[43].max(1e-6)
                                    } else {
                                        0.0
                                    }
                                }
                                "selected_column_cloud_material_class_code" => profiles[i][0],
                                "convective_risk" => unit(profiles[i][3]).max(unit(v[66])),
                                "dominant_burnable_pressure_family_code" => family(v[47]) as f32,
                                _ => self.sample(i, s)[5],
                            }
                        }),
                        mode,
                        scale
                    )),
                );
            }
            frames.push(Value::Object(frame));
        }
        Ok(
            json!({"version":103,"encoding":"base64-quantized-row-major-v1","selected_column_diagnostic_source":self.source(),
            "point_count":n,"height_scale_m":10.0,"cloud_optics":optics(self.input.ec),
            "precision":{"mode":REFINEMENT,"point_spacing_deg":self.input.point_spacing_deg,
                "model_grid_spacing_deg":self.input.spacing as f64,"point_count":n,"processed_sample_count":self.processed(),
                "candidate_limit_per_sample":0,"vertical_profiles":self.input.profiles.is_some(),"full_point_coverage":true,
                "aerosol_profile_mode":AEROSOL_MODE},"frames":frames}),
        )
    }
    fn step(&self, i: usize, s: usize) -> Result<Value, String> {
        let raw = self.v(i, s);
        let valid = raw[0] >= 0.5;
        let zero = [0.0; 82];
        let v = if valid { raw } else { &zero };
        let mark = self.input.marks[i * 20 + s];
        let instant = self.instant(i, s);
        let sample = self.sample(i, s);
        let selected = self.selected(i, s)?;
        let p = self.profile(i, s, selected);
        let deep = unit(p[3]).max(unit(raw[66])) as f64;
        let (mut f, g) = self.geometry(i, s);
        if !valid {
            f = None;
        }
        let phase = if f.is_none() {
            Value::Null
        } else {
            self.selected_phase(p)
        };
        let genus = self.genus(f, &phase, g, deep, true)?;
        let mut color_instant = instant;
        if !valid {
            color_instant[7] = 0.0;
            color_instant[11] = 0.0;
        }
        let color = self.color(
            mark.index,
            color_instant,
            sample[0],
            if valid { deep } else { 0.0 },
            &phase,
        )?;
        let view = unit(v[43]) as f64;
        let burn = unit(v[44]) as f64;
        let block = unit(v[60]) as f64;
        let regime = if !valid {
            "invalid"
        } else if view < 0.01 {
            "clear"
        } else if block >= 0.55 && burn < view * 0.55 {
            "solar_incoming_blocked_field"
        } else {
            "forward_cloud_field"
        };
        let mut row = Map::new();
        for (name, field) in [
            ("forward_view_cloud_field_amount", 43),
            ("forward_burnable_cloud_field_amount", 44),
            ("forward_candidate_cloud_field_amount", 77),
            ("forward_target_eligible_cloud_field_amount", 78),
            ("forward_solar_geometry_reachable_cloud_field_amount", 79),
            ("forward_external_cloud_path_transmitted_field_amount", 80),
            ("forward_unoccluded_burn_potential_upper_bound", 81),
            ("forward_burnable_cloud_load", 45),
            ("forward_burnable_cloud_material_load", 46),
            ("forward_burnable_cloud_incoming_aerosol_load", 62),
            ("solar_incoming_block_risk", 60),
            ("forward_high_cloud_amount", 48),
            ("forward_mid_cloud_amount", 49),
            ("forward_low_cloud_amount", 50),
            ("forward_high_burnable_amount", 51),
            ("forward_mid_burnable_amount", 52),
            ("forward_low_burnable_amount", 53),
            ("observer_local_cloud_field_amount", 54),
            ("observer_local_burnable_cloud_field_amount", 55),
            ("observer_local_burnable_cloud_load", 56),
            ("observer_local_burnable_cloud_material_load", 61),
            ("observer_local_burnable_cloud_incoming_aerosol_load", 63),
            ("ground_area_cloud_field_amount", 57),
            ("ground_area_burnable_cloud_field_amount", 58),
            ("ground_area_burnable_cloud_load", 59),
            ("target_deck_connected", 64),
            ("target_deck_attenuated_fraction", 65),
            ("target_deep_risk", 66),
            ("cloud_optical_depth", 67),
            ("peak_blocking", 68),
            ("clear_gap_count", 69),
            ("observer_return_optical_depth", 73),
            ("observer_return_transmission", 74),
            ("observer_local_return_optical_depth", 75),
            ("observer_local_return_transmission", 76),
            ("visibility_score", 5),
        ] {
            row.insert(name.into(), json!(v[field] as f64));
        }
        row.extend(json!({
            "selected_column_diagnostic_source":self.source(),"offset_minutes":if self.input.rising {-75+s as i32*5}else{-20+s as i32*5},
            "valid":valid,"instant_quality_index":mark.index,"instant_quality_level":empirical::quality_level(mark.index),
            "physically_burnable":mark.physically_burnable,"burnable_cloud_coverage":mark.coverage as f64,
            "solar_elevation_deg":sample[0] as f64,"solar_azimuth_deg":sample[1] as f64,
            "cloud_geometry_proxy_source":self.proxy_source(),"cloud_geometry_support_count":if valid {p[4] as i32}else{-1},
            "cloud_geometry_uncertainty_m":if valid {p[5] as f64}else{0.0},"cloud_geometry_confidence":if valid {p[6] as f64}else{0.0},
            "forward_burnable_cloud_ratio":(v[44]/v[43].max(1e-6)).clamp(0.0,1.0) as f64,
            "brightness_color":if valid {instant[7]}else{0.0},"cloud_material_score":if valid {instant[8]}else{0.0},
            "selected_column_cloud_material_class_code":if valid {p[0] as i32}else{0},"quality_aod550":instant[17],
            "dominant_burnable_pressure_family":f,
            "dominant_burnable_cloud_base_asl_mean_m":f.map(|_|g[0]),"dominant_burnable_cloud_top_asl_mean_m":f.map(|_|g[1]),
            "dominant_burnable_target_terrain_asl_mean_m":f.map(|_|g[2]),
            "burn_score":mark.index/2.5,"intensity_score":(v[45]/2.5).clamp(0.0,1.0) as f64,
            "extent_score":view,"vividness_score":if valid {instant[7]}else{0.0},
            "observer_mid_blocker_opacity":if valid {selected[5] as f64}else{0.0},
            "observer_low_blocker_opacity":if valid {selected[6] as f64}else{0.0},
            "selected_regime":regime,
            "scene_scores":if valid {json!({"clear":1.0-view,"forward_cloud_field":view,
                "burnable_cloud_field":burn,"solar_incoming_blocked_field":block})}else{
                json!({"clear":0.0,"forward_cloud_field":0.0,"burnable_cloud_field":0.0,"solar_incoming_blocked_field":0.0})},
            "local_sky_score":if valid {1.0-block}else{0.0},"remote_sky_score":if valid {burn}else{0.0},
            "convective_risk":if valid {deep}else{0.0},"color_diagnosis":color,"cloud_phase_proxy":phase,"cloud_genus_proxy":genus,
        }).as_object().unwrap().clone());
        Ok(Value::Object(row))
    }
    fn point(&self, i: usize) -> Result<(Value, Option<Value>), String> {
        let observer = &self.input.points[i];
        let time = &self.input.times[i];
        let mut best: Option<usize> = None;
        let mut scores = Vec::new();
        let mut first = None;
        for s in 0..20 {
            if self.v(i, s)[0] < 0.5 {
                continue;
            }
            first.get_or_insert(s);
            let mark = self.input.marks[i * 20 + s];
            scores.push(mark.index);
            if mark.physically_burnable
                && best.is_none_or(|b| {
                    let old = self.input.marks[i * 20 + b];
                    (mark.index as f32, mark.coverage, self.v(i, s)[45])
                        > (old.index as f32, old.coverage, self.v(i, b)[45])
                })
            {
                best = Some(s);
            }
        }
        let mut quality = diagnostics::render(
            &self.input.scores[i],
            &self.input.extras[i],
            self.input.scores[i].values[scoring::FIELDS
                .iter()
                .position(|&s| s == "forward_field_radius_km")
                .unwrap()],
        );
        let components = quality["components"]
            .as_object_mut()
            .ok_or("missing frame quality components")?;
        components.extend(optics(self.input.ec).as_object().unwrap().clone());
        let threshold = components
            .get("event_active_cloud_threshold")
            .and_then(Value::as_f64)
            .ok_or("missing event threshold")? as f32;
        components.insert(
            "physically_burnable_steps".into(),
            json!((0..20)
                .filter(|&s| self.v(i, s)[0] >= 0.5
                    && self.input.marks[i * 20 + s].coverage >= threshold)
                .count() as f64),
        );
        if self.input.ec {
            for key in ["quality_coverage_source", "observer_column_quality_source"] {
                if let Some(text) = components[key].as_str() {
                    components.insert(
                        key.into(),
                        json!(text.replace("GFS", "EC public TCC/RH proxy")),
                    );
                }
            }
        }
        let selected = if let Some(s) = best {
            self.selected(i, s)?
        } else {
            [0.0; 7]
        };
        let profile = if let Some(s) = best {
            self.profile(i, s, selected)
        } else {
            [0.0, 0.0, 0.0, 0.0, -1.0, 0.0, 0.55]
        };
        let (f, g) = best.map_or((None, [0.0; 3]), |s| self.geometry(i, s));
        let deep = best.map_or(0.0, |s| unit(profile[3]).max(unit(self.v(i, s)[66])) as f64);
        let peak = best.map_or(0.0, |s| self.input.marks[i * 20 + s].index as f32 as f64);
        let instant = best.map_or([0.0; 19], |s| self.instant(i, s));
        let mut steps = Vec::new();
        if self.input.include_point_core {
            for s in 0..20 {
                steps.push(self.step(i, s)?);
            }
        }
        let selected_phase = if let Some(s) = best.filter(|_| self.input.include_point_core) {
            steps[s]["cloud_phase_proxy"].clone()
        } else if best.is_some() && self.input.profiles.is_some() {
            self.selected_phase(profile)
        } else {
            unknown_phase("pressure-profile-unavailable")
        };
        let actual_phase = if f.is_some() {
            unknown_phase("not-resolved-for-native-burnable-target")
        } else {
            Value::Null
        };
        let genus = self.genus(f, &actual_phase, g, deep, false)?;
        let genus_proxy = if let Some(s) = best.filter(|_| self.input.include_point_core) {
            steps[s]["cloud_genus_proxy"].clone()
        } else {
            self.genus(f, &selected_phase, g, deep, true)?
        };
        let color = self.color(
            peak,
            instant,
            best.map_or(0.0, |s| self.sample(i, s)[0]),
            deep,
            &selected_phase,
        )?;
        let confidence_step = best.or(first);
        let geometry_confidence = if let Some(s) = confidence_step {
            self.profile(i, s, self.selected(i, s)?)[6] as f64
        } else {
            0.55
        };
        let confidence = (if self.input.aerosol_qualities[i].to_lowercase() == "cams" {
            0.82
        } else {
            0.60
        } * (0.70 + 0.30 * geometry_confidence.clamp(0.0, 1.0)))
        .clamp(0.25, 0.96);
        let cycle_epoch = self.input.cycle.timestamp() as f64
            + self.input.cycle.timestamp_subsec_micros() as f64 / 1e6;
        let lead = best.map(|s| (time.evaluation[s] - cycle_epoch) / 3600.0);
        let mut result = json!({
            "name":observer.name,"lat":observer.lat,"lon":observer.lon,"observer_elevation_asl_m":observer.elevation_m,
            "frame_index":i,"burn_score":peak/2.5,"peak_quality_index":peak,"peak_quality_level":empirical::quality_level(peak),
            "overall_score":mean(&scores)/2.5,"intensity_score":best.map_or(0.0,|s|(self.v(i,s)[45]/2.5).clamp(0.0,1.0) as f64),
            "extent_score":best.map_or(0.0,|s|unit(self.v(i,s)[43]) as f64),"vividness_score":instant[7],
            "quality_index":quality["index"],"quality_level":quality["level"],"quality_components":null,
            "cloud_optics":optics(self.input.ec),"dominant_burnable_pressure_family":f,
            "dominant_burnable_cloud_base_asl_mean_m":f.map(|_|g[0]),"dominant_burnable_cloud_top_asl_mean_m":f.map(|_|g[1]),
            "dominant_burnable_target_terrain_asl_mean_m":f.map(|_|g[2]),
            "dominant_burnable_cloud_base_agl_mean_m":f.map(|_|(g[0]-g[2]).max(0.0)),
            "dominant_burnable_cloud_top_agl_mean_m":f.map(|_|(g[1]-g[2]).max(0.0)),
            "dominant_burnable_cloud_base_above_observer_mean_m":f.map(|_|g[0]-observer.elevation_m),
            "dominant_burnable_cloud_top_above_observer_mean_m":f.map(|_|g[1]-observer.elevation_m),
            "dominant_burnable_cloud_phase":actual_phase,"cloud_phase_proxy":selected_phase,"color_diagnosis":color,
            "dominant_burnable_cloud_genus_diagnosis":genus,"cloud_genus_proxy":genus_proxy,
            "confidence_score":confidence,"confidence_kind":CONFIDENCE,"forecast_lead_hours":lead,
            "forecast_lead_semantics":LEAD,"quality_index_semantics":QUALITY_SEMANTICS,
            "cloud_geometry_proxy_source":self.proxy_source(),"cloud_geometry_boundary_semantics":self.boundary(),
            "cloud_geometry_support_count":profile[4] as i32,"cloud_geometry_uncertainty_m":profile[5] as f64,
            "cloud_geometry_confidence":best.map_or(0.55,|_|profile[6] as f64),
            "best_time_local":best.map(|s|local(time.evaluation[s],&observer.timezone_name)).transpose()?,
            "anchor_local":local(time.anchor,&observer.timezone_name)?,"near_cloud_ratio":null,"far_cloud_ratio":null,
            "compute_backend":"cpu","point_refinement":REFINEMENT,"high_cloud":selected[0] as f64,"mid_cloud":selected[1] as f64,
            "low_cloud":selected[2] as f64,"cloud_evidence":selected[4] as f64,"selected_column_cloud_material_class_code":profile[0] as i32,
            "convective_risk":deep,"illumination_score":best.map_or(0.0,|s|self.v(i,s)[40] as f64),
            "path_clearance_score":best.map_or(0.0,|s|self.v(i,s)[37] as f64),"blocking_risk":best.map_or(0.0,|s|self.v(i,s)[39] as f64),
        });
        let core = if self.input.include_point_core {
            let mut core = json!({
                "version":8,"algorithm":CORE_ALGORITHM,"target_date":self.input.date,"phenomenon":if self.input.rising {"dawn"}else{"dusk"},
                "model_source":self.model(),"forecast_mode":"model","point_refinement":REFINEMENT,
                "analysis_radius_km":quality["components"]["forward_field_radius_km"],"precision_mode":REFINEMENT,
                "lat":observer.lat,"lon":observer.lon,"timezone_name":observer.timezone_name,
                "observer_elevation_asl_m":observer.elevation_m,"anchor_local":result["anchor_local"],
                "cycle_time_utc":time_contract::iso(self.input.cycle),"model_cycles":{(self.model()):time_contract::iso(self.input.cycle)},
                "confidence_score":confidence,"confidence_kind":CONFIDENCE,"forecast_lead_hours":lead,"forecast_lead_semantics":LEAD,
                "quality_index_semantics":QUALITY_SEMANTICS,"cloud_optics":optics(self.input.ec),
                "cloud_geometry_proxy_source":self.proxy_source(),"cloud_geometry_boundary_semantics":self.boundary(),
                "cloud_geometry_support_count":profile[4] as i32,"cloud_geometry_uncertainty_m":profile[5] as f64,
                "cloud_geometry_confidence":best.map_or(0.55,|_|profile[6] as f64),"model_disagreement_score":0.0,"model_weights":{(self.model()):1.0},
                "quality_index":quality["index"],"quality_level":quality["level"],"peak_quality_index":peak,
                "peak_quality_level":empirical::quality_level(peak),"overall_score":mean(&scores)/2.5,
                "best_offset_minutes":best.map(|s|if self.input.rising {-75+s as i32*5}else{-20+s as i32*5}),
                "quality_components":quality["components"],"dominant_burnable_pressure_family":f,
                "dominant_burnable_cloud_base_asl_mean_m":f.map(|_|g[0]),"dominant_burnable_cloud_top_asl_mean_m":f.map(|_|g[1]),
                "dominant_burnable_target_terrain_asl_mean_m":f.map(|_|g[2]),"convective_risk":deep,
                "aerosol":{"aod550":quality["components"]["quality_aod550"],"source":self.input.aerosol_sources[i],
                    "quality":self.input.aerosol_qualities[i]},"steps":steps,
            });
            for key in [
                "burnable_cloud_coverage_effective",
                "forward_view_cloud_field_amount_peak",
                "forward_burnable_cloud_field_amount_peak",
                "forward_burnable_cloud_load_peak",
                "forward_burnable_cloud_load_effective",
                "forward_burnable_cloud_material_load_peak",
                "forward_burnable_cloud_material_load_effective",
                "solar_incoming_block_risk_peak",
                "forward_burnable_cloud_ratio_peak",
            ] {
                core[key] = quality["components"][key].clone();
            }
            core["burnable_duration_minutes"] = quality["components"]["duration_minutes"].clone();
            Some(core)
        } else {
            None
        };
        // Move the large component map into the result, rather than cloning it again.
        result["quality_components"] = quality["components"].take();
        Ok((result, core))
    }
}

struct Results<'a, 'b>(&'a Payload<'b>);
impl Serialize for Results<'_, '_> {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut seq = serializer.serialize_seq(Some(self.0.input.points.len()))?;
        if let Some((result, _)) = &self.0.single {
            seq.serialize_element(result)?;
        } else {
            for i in 0..self.0.input.points.len() {
                let (result, _) = self.0.point(i).map_err(serde::ser::Error::custom)?;
                seq.serialize_element(&result)?;
            }
        }
        seq.end()
    }
}
impl Serialize for Payload<'_> {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut map = serializer.serialize_map(Some(3))?;
        map.serialize_entry("results", &Results(self))?;
        map.serialize_entry(
            "point_core",
            &self.single.as_ref().and_then(|(_, core)| core.as_ref()),
        )?;
        map.serialize_entry("frames", &FrameEnvelope(self))?;
        map.end()
    }
}

struct FrameEnvelope<'a, 'b>(&'a Payload<'b>);
impl Serialize for FrameEnvelope<'_, '_> {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let fields = self.0.frames.as_object().unwrap();
        let mut map = serializer.serialize_map(Some(fields.len() + 1))?;
        for (key, value) in fields {
            map.serialize_entry(key, value)?;
        }
        let elapsed = self.0.started.elapsed().as_secs_f64();
        map.serialize_entry("performance",&json!({
            "instant_scoring_seconds":0.0,
            "assembly_seconds":(elapsed*1000.0).round_ties_even()/1000.0,
            "total_seconds":(elapsed*1000.0).round_ties_even()/1000.0,
            "points_per_second":(self.0.input.points.len() as f64/elapsed*10.0).round_ties_even()/10.0,
            "samples_per_second":(self.0.processed() as f64/elapsed*10.0).round_ties_even()/10.0,
        }))?;
        map.end()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use sha2::{Digest, Sha256};
    use std::io::Write;

    #[test]
    fn codecs_preserve_nonfinite_saturation_and_little_endian() {
        let values = [f32::NAN, f32::NEG_INFINITY, f32::INFINITY, -0.0, 0.5, 1.0];
        assert_eq!(
            STANDARD.decode(encode(values.into_iter(), 0, 1.0)).unwrap(),
            [0, 0, 255, 0, 128, 255]
        );
        let expected: Vec<_> = [0_u16, 0, 65535, 0, 0, 1]
            .into_iter()
            .flat_map(u16::to_le_bytes)
            .collect();
        assert_eq!(
            STANDARD.decode(encode(values.into_iter(), 1, 1.0)).unwrap(),
            expected
        );
        let expected: Vec<_> = [0_i16, -32768, 32767, 0, 0, 1]
            .into_iter()
            .flat_map(i16::to_le_bytes)
            .collect();
        assert_eq!(
            STANDARD.decode(encode(values.into_iter(), 2, 1.0)).unwrap(),
            expected
        );
        assert_eq!(
            [1.5, 2.5, 257.0, 258.5, -255.0, f32::NAN].map(family),
            [2, 2, 1, 2, 1, 0]
        );
    }

    struct HashWriter {
        bytes: usize,
        hash: Sha256,
    }
    impl Write for HashWriter {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            self.bytes += bytes.len();
            self.hash.update(bytes);
            Ok(bytes.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    /// Run each mode in a separate process for comparable process high-water RSS.
    #[test]
    #[ignore = "component benchmark: set FIRESKY_BENCH_MODE=streaming|materialized"]
    fn bench_frame_assembly() {
        let mode = std::env::var("FIRESKY_BENCH_MODE").unwrap_or_else(|_| "streaming".into());
        assert!(matches!(mode.as_str(), "streaming" | "materialized"));
        let n: usize = std::env::var("FIRESKY_BENCH_POINTS")
            .map(|v| v.parse().unwrap())
            .unwrap_or(36685);
        let trials: usize = std::env::var("FIRESKY_BENCH_TRIALS")
            .map(|v| v.parse().unwrap())
            .unwrap_or(3);
        assert!((1..=100000).contains(&n) && (1..=15).contains(&trials));
        let anchor = 1794217501.123456;
        let points: Vec<_> = (0..n)
            .map(|i| Point {
                name: format!("Observer {i}"),
                lat: 0.25,
                lon: 100.75,
                timezone_name: "Asia/Shanghai".into(),
                elevation_m: 50.123456789,
            })
            .collect();
        let times: Vec<_> = (0..n)
            .map(|_| Times {
                anchor,
                evaluation: std::array::from_fn(|s| anchor + (-20.0 + s as f64 * 5.0) * 60.0),
            })
            .collect();
        let mut values = vec![0.0; n * 20 * 82];
        for (index, row) in values.as_chunks_mut::<82>().0.iter_mut().enumerate() {
            row.fill(0.2);
            row[0] = 1.0;
            row[5] = 1.0;
            row[43] = 0.8;
            row[44] = 0.4 + (index % 20) as f32 * 0.01;
            row[45] = 1.7;
            row[46] = 1.5;
            row[62] = 1.6;
            row[47] = 1.0;
            row[70] = 4500.0;
            row[71] = 8000.0;
            row[72] = 125.0;
        }
        let mut samples = vec![0.0; n * 180];
        for row in samples.as_chunks_mut::<9>().0 {
            row[0] = -3.0;
            row[1] = 280.0;
            row[3] = 1.0;
            row[4] = 0.25;
            row[5] = 0.12;
        }
        let rows = quality::assessment_rows(&values[..1640], &samples[..180], false).unwrap();
        let score = scoring::score(
            &rows,
            scoring::Options {
                mode: scoring::Mode::Regional,
                apply_duration: true,
                local_columns: true,
                score_ceiling: false,
                radius_km: 200.0,
            },
            None,
        )
        .unwrap();
        let scores = vec![score; n];
        let step_marks: Vec<_> = rows
            .iter()
            .map(|r| {
                let instant = scoring::score_instant(r, true, true);
                quality::InstantMark {
                    index: instant[0],
                    coverage: instant[6] as f32,
                    physically_burnable: instant[5] >= 0.5,
                }
            })
            .collect();
        let marks: Vec<_> = (0..n).flat_map(|_| step_marks.iter().copied()).collect();
        let offsets: Vec<_> = (0..20).map(|s| -20.0 + s as f64 * 5.0).collect();
        let extra = firesky_native::observer_diagnostics::summarize_many(
            &values[..1640],
            1,
            &offsets,
            true,
        )
        .unwrap()[0];
        let extras = vec![extra; n];
        let profiles = vec![[1.0, 1e-6, 4e-6, 0.2, 3.0, 1000.0, 0.6875]; n * 20];
        let columns = vec![[0.2; 36]; 4];
        let sources = vec!["frozen-source".to_owned(); n];
        let qualities = vec!["cams".to_owned(); n];
        let mut elapsed = Vec::new();
        let mut reference = None;
        for trial in 0..=trials {
            let started = Instant::now();
            let payload = Payload::prepare(Input {
                points: &points,
                times: &times,
                values: &values,
                samples: &samples,
                columns: &columns,
                profiles: Some(&profiles),
                scores: &scores,
                marks: &marks,
                extras: &extras,
                aerosol_sources: &sources,
                aerosol_qualities: &qualities,
                rows: 1,
                cols: 2,
                origin: [0.0, 100.0],
                spacing: 1.0,
                date: "2026-11-09",
                cycle: time_contract::parse_utc("2026-11-09T00:00:00Z").unwrap(),
                rising: false,
                ec: false,
                include_point_core: false,
                point_spacing_deg: None,
            })
            .unwrap();
            let mut writer = std::io::BufWriter::with_capacity(
                65536,
                HashWriter {
                    bytes: 0,
                    hash: Sha256::new(),
                },
            );
            if mode == "streaming" {
                serde_json::to_writer(&mut writer, &Results(&payload)).unwrap();
            } else {
                let results: Vec<_> = (0..n).map(|i| payload.point(i).unwrap().0).collect();
                serde_json::to_writer(&mut writer, &results).unwrap();
            }
            writer.flush().unwrap();
            let ms = started.elapsed().as_secs_f64() * 1000.0;
            let output = writer.into_inner().map_err(|e| e.to_string()).unwrap();
            let hash: String = output
                .hash
                .finalize()
                .iter()
                .map(|v| format!("{v:02x}"))
                .collect();
            let identity = (output.bytes, hash);
            if let Some(expected) = &reference {
                assert_eq!(&identity, expected);
            } else {
                reference = Some(identity);
            }
            if trial > 0 {
                elapsed.push(ms);
            }
        }
        println!("scope=frame-preparation-and-regional-result-json-only mode={mode} points={n} trials_ms={elapsed:?} identity={reference:?}");
        elapsed.sort_by(f64::total_cmp);
        println!(
            "mode={mode} median_ms={:.6} retained_marks_bytes={} full_instant_f64_bytes={}",
            elapsed[elapsed.len() / 2],
            marks.len() * std::mem::size_of::<quality::InstantMark>(),
            n * 20 * 19 * 8
        );
    }
}
