//! Forecast descriptions are diagnostic proxies, never score multipliers.
use crate::time_contract;
use chrono::{DateTime, Duration, FixedOffset, Timelike};
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::BTreeMap;

#[derive(Clone, Deserialize)]
#[serde(untagged)]
pub enum Phase {
    Label(String),
    Object { label: Option<String> },
}
impl Phase {
    fn label(&self) -> Option<&str> {
        match self {
            Self::Label(s) => Some(s),
            Self::Object { label } => label.as_deref(),
        }
    }
}
#[derive(Deserialize)]
pub struct Step {
    #[serde(default)]
    valid_time_local: String,
    instant_quality_index: Option<f64>,
    burn_score: Option<f64>,
    physically_burnable: Option<bool>,
    burnable_cloud_coverage: Option<f64>,
    remote_sky_score: Option<f64>,
    local_sky_score: Option<f64>,
    scene_scores: Option<BTreeMap<String, Option<f64>>>,
}
impl Step {
    fn hole(&self) -> f64 {
        self.scene_scores
            .as_ref()
            .and_then(|s| s.get("cloud_hole"))
            .copied()
            .flatten()
            .unwrap_or(0.0)
    }
    fn quality(&self) -> f64 {
        self.instant_quality_index
            .or(self.burn_score)
            .unwrap_or(0.0)
    }
    fn target(&self) -> f64 {
        self.remote_sky_score
            .unwrap_or(0.0)
            .max(self.local_sky_score.unwrap_or(0.0))
            .max(self.hole())
    }
}
fn default_threshold() -> f64 {
    0.25
}
fn unknown() -> String {
    "unknown".into()
}
#[derive(Deserialize)]
#[serde(tag = "operation", rename_all = "snake_case", deny_unknown_fields)]
pub enum Operation {
    Level {
        index: f64,
    },
    Scene {
        local_deck: f64,
        distant_target: f64,
        remote_boundary: f64,
        cloud_hole: f64,
        convective_risk: f64,
    },
    Phase {
        cloud_ice_kg_kg: Option<f64>,
        cloud_liquid_kg_kg: Option<f64>,
    },
    Genus {
        dominant_pressure_family: Option<String>,
        cloud_phase: Option<Phase>,
        cloud_base_agl_m: Option<f64>,
        cloud_top_agl_m: Option<f64>,
        convective_risk: f64,
        model_source: String,
    },
    GenusProxy {
        pressure_family: Option<String>,
        cloud_phase: Option<Phase>,
        cloud_base_agl_m: Option<f64>,
        cloud_top_agl_m: Option<f64>,
        convective_risk: f64,
        model_source: String,
    },
    Color {
        burn_score: Option<f64>,
        quality_score: Option<f64>,
        vividness_score: f64,
        solar_elevation_deg: f64,
        aod550: f64,
        solar_incoming_illumination_score: f64,
        deep_cloud_risk: f64,
        cloud_phase: String,
    },
    Evolution {
        steps: Vec<Step>,
        best_index: Option<i64>,
    },
    Window {
        steps: Vec<Step>,
        #[serde(default = "default_threshold")]
        threshold: f64,
        #[serde(default = "unknown")]
        regime: String,
    },
}
fn clamp(v: f64) -> f64 {
    v.clamp(0.0, 1.0)
}

pub fn phase(ice: Option<f64>, liquid: Option<f64>) -> Value {
    let ice = ice.unwrap_or(0.0).max(0.0);
    let liquid = liquid.unwrap_or(0.0).max(0.0);
    if ice + liquid <= 1e-10 {
        return json!({"label":"unknown","ice_fraction":null,"confidence":0.0});
    }
    let fraction = ice / (ice + liquid);
    json!({"label":if fraction>=0.72 {"ice"} else if fraction<=0.28 {"water"} else {"mixed"},
        "ice_fraction":fraction,"confidence":clamp((fraction-0.5).abs()*1.7+0.25)})
}
fn scene(values: [f64; 5]) -> Value {
    let values = values.map(clamp);
    let names = [
        "local_deck",
        "distant_target",
        "remote_boundary",
        "cloud_hole",
        "convective",
    ];
    let mut best = 0;
    for i in 1..4 {
        if values[i] > values[best] {
            best = i;
        }
    }
    let mut selected = names[best];
    let mut scores: BTreeMap<&str, f64> = names.into_iter().zip(values).collect();
    if values[best] < 0.08 {
        if values[4] < 0.35 {
            selected = "clear";
            scores.insert("clear", clamp(1.0 - values[best].max(values[4])));
        } else {
            selected = "convective";
        }
    } else if values[4] >= 0.62 && values[4] >= values[best] {
        selected = "convective";
    }
    json!({"selected_regime":selected,"scene_scores":scores,"local_sky_score":values[0],
        "remote_sky_score":values[1].max(values[2]).max(values[3]),"convective_risk":values[4]})
}
fn genus(
    family: Option<&str>,
    phase: Option<&str>,
    base: Option<f64>,
    top: Option<f64>,
    convective: f64,
    model: &str,
) -> Result<Value, String> {
    let Some(family) = family else {
        return Ok(Value::Null);
    };
    let genera: &[&str] = match family {
        "high" => &["cirrus", "cirrocumulus", "cirrostratus"],
        "middle" => &["altocumulus", "altostratus", "nimbostratus"],
        "low" => &["stratocumulus", "stratus", "cumulus", "cumulonimbus"],
        _ => return Err(format!("unsupported pressure family: {family}")),
    };
    Ok(
        json!({"label":"unknown","status":"compatible-only","confidence":0.0,"scoring_effect":false,
        "compatible_genera":genera,
        "reason":"pressure-family-and-column-proxies-cannot-resolve-actual-target-layer-wmo-cloud-genus;compatible-genera-are-conventional-non-probabilistic-and-non-exhaustive",
        "source":"numerical-model-pressure-family-compatible-genera-v1","model_source":model,
        "evidence":{"dominant_pressure_family":family,"cloud_phase":phase,"cloud_base_agl_m":base,"cloud_top_agl_m":top,"convective_risk":convective}}),
    )
}
fn genus_proxy(
    family: Option<&str>,
    phase: Option<&str>,
    base: Option<f64>,
    top: Option<f64>,
    convective: f64,
    model: &str,
) -> Value {
    let Some(family) = family else {
        return Value::Null;
    };
    let phase = phase.filter(|p| !p.is_empty()).unwrap_or("unknown");
    let convective = clamp(convective);
    let thickness = base.zip(top).filter(|(b, t)| t >= b).map(|(b, t)| t - b);
    let ice_like = matches!(phase, "ice" | "mixed");
    let water_like = matches!(phase, "water" | "mixed");
    let scores: Vec<(&str, f64)> = match family {
        "high" => {
            let thin = thickness.is_some_and(|v| v <= 2500.0);
            let thick = thickness.is_some_and(|v| v >= 3000.0);
            vec![
                (
                    "cirrus",
                    if ice_like {
                        if thin {
                            0.62
                        } else if !thick {
                            0.38
                        } else {
                            0.30
                        }
                    } else {
                        0.26
                    },
                ),
                (
                    "cirrostratus",
                    if ice_like {
                        if thin {
                            0.26
                        } else if !thick {
                            0.42
                        } else {
                            0.56
                        }
                    } else {
                        0.30
                    },
                ),
                (
                    "cirrocumulus",
                    if ice_like {
                        if thin {
                            0.14
                        } else {
                            0.24
                        }
                    } else {
                        0.24
                    },
                ),
            ]
        }
        "middle" => {
            let thick = thickness.is_some_and(|v| v >= 2000.0);
            vec![
                (
                    "altocumulus",
                    if water_like && !thick { 0.56 } else { 0.24 },
                ),
                (
                    "altostratus",
                    if thick {
                        0.52
                    } else if water_like {
                        0.24
                    } else {
                        0.46
                    },
                ),
                (
                    "nimbostratus",
                    clamp(0.10 + 0.22 * convective + if thick { 0.10 } else { 0.0 }),
                ),
            ]
        }
        _ => {
            let shallow = base.is_some_and(|v| v <= 500.0);
            let deep = convective >= 0.62 || thickness.is_some_and(|v| v >= 3500.0);
            vec![
                (
                    "cumulonimbus",
                    if deep {
                        clamp(0.10 + 0.55 * convective)
                    } else {
                        0.06
                    },
                ),
                (
                    "stratus",
                    if shallow && !deep && thickness.is_none_or(|v| v <= 700.0) {
                        0.50
                    } else {
                        0.20
                    },
                ),
                ("stratocumulus", if !deep && !shallow { 0.46 } else { 0.24 }),
                ("cumulus", if !shallow && !deep { 0.34 } else { 0.18 }),
            ]
        }
    };
    let mut best = 0;
    for i in 1..scores.len() {
        if scores[i].1 > scores[best].1 {
            best = i;
        }
    }
    let confidence = clamp(
        0.18 + 0.35 * scores[best].1
            + 0.20 * f64::from(phase != "unknown")
            + 0.15 * f64::from(base.is_some() && top.is_some()),
    );
    let genera: Vec<_> = scores.iter().map(|s| s.0).collect();
    let map: BTreeMap<_, _> = scores.iter().copied().collect();
    json!({"label":scores[best].0,"status":"proxy","confidence":confidence,"scoring_effect":false,
        "candidate_scores":map,"compatible_genera":genera,"source":"pressure-family+phase+geometry-proxy-v2",
        "model_source":model,"evidence":{"pressure_family":family,"cloud_phase":phase,"cloud_base_agl_m":base,
        "cloud_top_agl_m":top,"convective_risk":convective}})
}
fn color(
    strength: f64,
    vividness: f64,
    elevation: f64,
    aod: f64,
    transmission: f64,
    deep: f64,
    phase: &str,
) -> Value {
    let strength = clamp(strength);
    let vividness = clamp(vividness);
    let transmission = clamp(transmission);
    let deep = clamp(deep);
    let aod = aod.max(0.0);
    let kind = if strength < 0.08 || transmission < 0.16 || deep > 0.66 {
        "muted_glow"
    } else if elevation > -1.6 && aod < 0.32 {
        "golden"
    } else if elevation > -3.8 || phase == "water" {
        "orange_red"
    } else {
        "deep_red"
    };
    json!({"type":kind,"confidence":clamp(0.30*strength+0.30*vividness+0.25*transmission+0.15*(1.0-deep)),
        "cloud_phase":phase,"source":"instant-quality+solar-elevation+aod+solar-incoming-illumination+cloud-phase",
        "inputs":{"quality_strength":strength,"brightness_color":vividness,"solar_elevation_deg":elevation,
        "aod550":aod,"solar_incoming_illumination_score":transmission,"deep_cloud_risk":deep,"cloud_phase":phase}})
}

#[derive(Clone)]
struct Stamp {
    time: DateTime<FixedOffset>,
    aware: bool,
}
impl Stamp {
    fn parse(value: &str) -> Result<Self, String> {
        if let Ok(time) = time_contract::parse_zoned(value) {
            return Ok(Self { time, aware: true });
        }
        Ok(Self {
            time: time_contract::parse_zoned(&format!("{value}+00:00"))?,
            aware: false,
        })
    }
    fn iso(&self) -> String {
        let mut value = self.time.format("%Y-%m-%dT%H:%M:%S").to_string();
        let micros = self.time.nanosecond() / 1000;
        if micros != 0 {
            value.push_str(&format!(".{micros:06}"));
        }
        if self.aware {
            value.push_str(&self.time.format("%:z").to_string());
        }
        value
    }
}
fn duration(left: &Stamp, right: &Stamp) -> Result<Duration, String> {
    if left.aware != right.aware {
        return Err("cannot mix naive and timezone-aware timestamps".into());
    }
    Ok(right.time - left.time)
}

fn scaled_microseconds(micros: i64, fraction: f64) -> Result<i64, String> {
    if !fraction.is_finite() || !(0.0..=1.0).contains(&fraction) {
        return Err("invalid time interpolation fraction".into());
    }
    // Python timedelta multiplication rounds the exact float ratio, not a
    // lossy float conversion of a potentially centuries-long microsecond count.
    let bits = fraction.to_bits();
    let exponent = ((bits >> 52) & 0x7ff) as i32;
    let mantissa = if exponent == 0 {
        bits & ((1_u64 << 52) - 1)
    } else {
        (bits & ((1_u64 << 52) - 1)) | (1_u64 << 52)
    };
    let shift = if exponent == 0 { 1074 } else { 1075 - exponent } as u32;
    let numerator = u128::from(micros.unsigned_abs()) * u128::from(mantissa);
    let rounded = if shift >= 128 {
        0
    } else {
        let quotient = numerator >> shift;
        let remainder = numerator - (quotient << shift);
        let half = 1_u128 << (shift - 1);
        quotient + u128::from(remainder > half || (remainder == half && quotient % 2 == 1))
    };
    let result = i64::try_from(rounded).map_err(|_| "time interpolation overflow")?;
    Ok(if micros < 0 { -result } else { result })
}
pub(crate) fn evolution(steps: &[Step], best: Option<i64>) -> Result<Value, String> {
    if steps.is_empty() {
        return Ok(json!({"trend":"unknown","target_score_change":0.0,
        "cloud_hole_closure_risk":0.0,"source":"model-time-interpolation"}));
    }
    let index = if let Some(i) = best {
        i.clamp(0, steps.len() as i64 - 1) as usize
    } else {
        let mut best = 0;
        for i in 1..steps.len() {
            if steps[i].quality() > steps[best].quality() {
                best = i;
            }
        }
        best
    };
    let left = &steps[index.saturating_sub(2)];
    let right = &steps[(index + 2).min(steps.len() - 1)];
    let change = right.target() - left.target();
    let trend = if change >= 0.14 {
        "forming"
    } else if change >= 0.05 {
        "strengthening"
    } else if change <= -0.14 {
        "dissipating"
    } else if change <= -0.05 {
        "weakening"
    } else {
        "steady"
    };
    let closure = if steps[index].hole() > 0.12 {
        clamp((steps[index].hole() - right.hole()) * 2.2)
    } else {
        0.0
    };
    let minutes = duration(
        &Stamp::parse(&left.valid_time_local)?,
        &Stamp::parse(&right.valid_time_local)?,
    )?
    .num_microseconds()
    .ok_or("duration overflow")?
    .unsigned_abs() as f64
        / 60_000_000.0;
    Ok(
        json!({"trend":trend,"target_score_change":change,"cloud_hole_closure_risk":closure,
        "source":"wind-aware-model-time-interpolation","comparison_start_local":left.valid_time_local,
        "comparison_end_local":right.valid_time_local,"comparison_minutes":minutes}),
    )
}
pub(crate) fn window(steps: &[Step], threshold: f64, regime: &str) -> Result<Value, String> {
    if steps.is_empty() {
        return Ok(Value::Null);
    }
    let times: Vec<_> = steps
        .iter()
        .map(|s| Stamp::parse(&s.valid_time_local))
        .collect::<Result<_, _>>()?;
    if times.iter().any(|t| t.aware != times[0].aware) {
        return Err("cannot mix naive and timezone-aware timestamps".into());
    }
    let mut order: Vec<_> = (0..steps.len()).collect();
    order.sort_by_key(|&i| times[i].time);
    let uses_quality = steps.iter().any(|s| s.instant_quality_index.is_some());
    let score = |i: usize| {
        if uses_quality {
            steps[i].instant_quality_index.unwrap_or(0.0)
        } else {
            steps[i].burn_score.unwrap_or(0.0)
        }
    };
    let active = |i: usize| {
        if uses_quality {
            steps[i].physically_burnable.unwrap_or(false)
                && steps[i].burnable_cloud_coverage.unwrap_or(0.0) >= 0.003
        } else {
            score(i) >= threshold
        }
    };
    let mut best: Option<usize> = None;
    for i in 0..order.len() {
        if active(order[i]) && best.is_none_or(|b| score(order[i]) > score(order[b])) {
            best = Some(i);
        }
    }
    let Some(best) = best else {
        return Ok(Value::Null);
    };
    let crossing = |a: usize, b: usize| -> Result<Stamp, String> {
        let (low, high, target) = if uses_quality {
            (
                steps[a].burnable_cloud_coverage.unwrap_or(0.0),
                steps[b].burnable_cloud_coverage.unwrap_or(0.0),
                0.003,
            )
        } else {
            (score(a), score(b), threshold)
        };
        let delta = high - low;
        let fraction = if delta.abs() < 1e-9 {
            0.5
        } else {
            clamp((target - low) / delta)
        };
        let micros = duration(&times[a], &times[b])?
            .num_microseconds()
            .ok_or("duration overflow")?;
        let adjustment = scaled_microseconds(micros, fraction)?;
        Ok(Stamp {
            time: times[a]
                .time
                .checked_add_signed(Duration::microseconds(adjustment))
                .ok_or("window time overflow")?,
            aware: times[a].aware,
        })
    };
    let mut start = times[order[best]].clone();
    let mut end = start.clone();
    for i in (1..=best).rev() {
        if !active(order[i - 1]) {
            start = crossing(order[i - 1], order[i])?;
            break;
        }
        start = times[order[i - 1]].clone();
    }
    for i in best..order.len() - 1 {
        if !active(order[i + 1]) {
            end = crossing(order[i], order[i + 1])?;
            break;
        }
        end = times[order[i + 1]].clone();
    }
    let minutes = duration(&start, &end)?
        .num_microseconds()
        .ok_or("duration overflow")? as f64
        / 60_000_000.0;
    Ok(
        json!({"regime":regime,"source":if uses_quality {"quality-curve-interpolated"} else {"score-curve-interpolated"},
        "threshold":if uses_quality {0.003} else {threshold},"start_time_local":start.iso(),
        "peak_time_local":times[order[best]].iso(),"end_time_local":end.iso(),"duration_minutes":minutes.max(0.0)}),
    )
}

pub fn execute(operation: Operation) -> Result<Value, String> {
    match operation {
        Operation::Level { index } => Ok(json!(crate::native::empirical::quality_level(index))),
        Operation::Scene {
            local_deck,
            distant_target,
            remote_boundary,
            cloud_hole,
            convective_risk,
        } => Ok(scene([
            local_deck,
            distant_target,
            remote_boundary,
            cloud_hole,
            convective_risk,
        ])),
        Operation::Phase {
            cloud_ice_kg_kg,
            cloud_liquid_kg_kg,
        } => Ok(phase(cloud_ice_kg_kg, cloud_liquid_kg_kg)),
        Operation::Genus {
            dominant_pressure_family,
            cloud_phase,
            cloud_base_agl_m,
            cloud_top_agl_m,
            convective_risk,
            model_source,
        } => genus(
            dominant_pressure_family.as_deref(),
            cloud_phase.as_ref().and_then(Phase::label),
            cloud_base_agl_m,
            cloud_top_agl_m,
            convective_risk,
            &model_source,
        ),
        Operation::GenusProxy {
            pressure_family,
            cloud_phase,
            cloud_base_agl_m,
            cloud_top_agl_m,
            convective_risk,
            model_source,
        } => Ok(genus_proxy(
            pressure_family.as_deref(),
            cloud_phase.as_ref().and_then(Phase::label),
            cloud_base_agl_m,
            cloud_top_agl_m,
            convective_risk,
            &model_source,
        )),
        Operation::Color {
            burn_score,
            quality_score,
            vividness_score,
            solar_elevation_deg,
            aod550,
            solar_incoming_illumination_score,
            deep_cloud_risk,
            cloud_phase,
        } => Ok(color(
            quality_score.or(burn_score).unwrap_or(0.0),
            vividness_score,
            solar_elevation_deg,
            aod550,
            solar_incoming_illumination_score,
            deep_cloud_risk,
            &cloud_phase,
        )),
        Operation::Evolution { steps, best_index } => {
            if steps.len() > 4096 {
                return Err("characteristic curve exceeds 4096 steps".into());
            }
            evolution(&steps, best_index)
        }
        Operation::Window {
            steps,
            threshold,
            regime,
        } => {
            if steps.len() > 4096 {
                return Err("characteristic curve exceeds 4096 steps".into());
            }
            window(&steps, threshold, &regime)
        }
    }
}
