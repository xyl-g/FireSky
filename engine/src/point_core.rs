//! Exact generated-core validation and public point payload materialization.
use crate::{
    astronomy,
    characteristics::{self, Operation, Phase},
    frames, time_contract,
};
use chrono::{DateTime, Datelike, Duration, FixedOffset, Offset, Utc};
use firesky_native::{empirical, quality};
use serde::Deserialize;
use serde_json::{json, Map, Value};

const ROOT: &str = "
version algorithm target_date phenomenon model_source forecast_mode point_refinement analysis_radius_km precision_mode
lat lon timezone_name observer_elevation_asl_m anchor_local cycle_time_utc model_cycles confidence_score confidence_kind
forecast_lead_hours forecast_lead_semantics quality_index_semantics cloud_optics cloud_geometry_proxy_source
cloud_geometry_boundary_semantics cloud_geometry_support_count cloud_geometry_uncertainty_m cloud_geometry_confidence
model_disagreement_score model_weights quality_index quality_level peak_quality_index peak_quality_level overall_score
best_offset_minutes quality_components burnable_cloud_coverage_effective forward_view_cloud_field_amount_peak
forward_burnable_cloud_field_amount_peak forward_burnable_cloud_load_peak forward_burnable_cloud_load_effective
forward_burnable_cloud_material_load_peak forward_burnable_cloud_material_load_effective solar_incoming_block_risk_peak
forward_burnable_cloud_ratio_peak burnable_duration_minutes dominant_burnable_pressure_family
dominant_burnable_cloud_base_asl_mean_m dominant_burnable_cloud_top_asl_mean_m dominant_burnable_target_terrain_asl_mean_m
convective_risk aerosol steps";
const STEP: &str = "
selected_column_diagnostic_source offset_minutes valid instant_quality_index instant_quality_level physically_burnable
burnable_cloud_coverage forward_view_cloud_field_amount forward_burnable_cloud_field_amount
forward_candidate_cloud_field_amount forward_target_eligible_cloud_field_amount
forward_solar_geometry_reachable_cloud_field_amount forward_external_cloud_path_transmitted_field_amount
forward_unoccluded_burn_potential_upper_bound forward_burnable_cloud_load forward_burnable_cloud_material_load
forward_burnable_cloud_incoming_aerosol_load solar_incoming_block_risk forward_high_cloud_amount forward_mid_cloud_amount
forward_low_cloud_amount forward_high_burnable_amount forward_mid_burnable_amount forward_low_burnable_amount
observer_local_cloud_field_amount observer_local_burnable_cloud_field_amount observer_local_burnable_cloud_load
observer_local_burnable_cloud_material_load observer_local_burnable_cloud_incoming_aerosol_load
ground_area_cloud_field_amount ground_area_burnable_cloud_field_amount ground_area_burnable_cloud_load solar_elevation_deg
solar_azimuth_deg target_deck_connected target_deck_attenuated_fraction target_deep_risk cloud_optical_depth peak_blocking
clear_gap_count observer_return_optical_depth observer_return_transmission observer_local_return_optical_depth
observer_local_return_transmission cloud_geometry_proxy_source cloud_geometry_support_count cloud_geometry_uncertainty_m
cloud_geometry_confidence forward_burnable_cloud_ratio brightness_color cloud_material_score
selected_column_cloud_material_class_code quality_aod550 dominant_burnable_pressure_family
dominant_burnable_cloud_base_asl_mean_m dominant_burnable_cloud_top_asl_mean_m dominant_burnable_target_terrain_asl_mean_m
burn_score intensity_score extent_score vividness_score visibility_score observer_mid_blocker_opacity
observer_low_blocker_opacity selected_regime scene_scores local_sky_score remote_sky_score convective_risk color_diagnosis";
const OPTIONAL_STEP: &str = "cloud_phase_proxy cloud_genus_proxy";
const SCENE: &str = "clear forward_cloud_field burnable_cloud_field solar_incoming_blocked_field";
const COMPONENT_TEXT: &str =
    "observer_column_quality_source quality_limiting_factor quality_coverage_source";
const COMPONENT_UNIT: &str = "
forward_view_cloud_field_amount_peak forward_view_cloud_field_amount_mean forward_view_cloud_field_amount_effective
forward_view_cloud_field_amount_full_window_mean forward_burnable_cloud_field_amount_peak
forward_burnable_cloud_field_amount_mean forward_burnable_cloud_field_amount_effective
forward_burnable_cloud_field_amount_full_window_mean forward_burnable_cloud_ratio_peak forward_burnable_cloud_ratio_effective
burnable_cloud_coverage_peak burnable_cloud_coverage_mean burnable_cloud_coverage_effective burnable_to_view_ratio
cloud_material material_factor incoming_aod_illumination_factor observer_aod_contrast_factor solar_incoming_block_risk_peak
solar_incoming_block_risk_mean solar_incoming_block_risk_effective solar_incoming_block_risk_at_best_cloud
observer_local_cloud_field_amount_peak observer_local_cloud_field_amount_mean observer_local_burnable_cloud_field_amount_peak
observer_local_burnable_cloud_field_amount_mean surface_visibility_gate_peak brightness_color event_active_cloud_threshold
observer_return_transmission_mean observer_local_return_transmission_mean ground_area_view_amount_mean
ground_area_burnable_amount_mean forward_high_view_peak forward_high_view_mean forward_mid_view_peak forward_mid_view_mean
forward_low_view_peak forward_low_view_mean forward_high_burn_peak forward_high_burn_mean forward_mid_burn_peak
forward_mid_burn_mean forward_low_burn_peak forward_low_burn_mean";
const COMPONENT_SCORE: &str = "
forward_burnable_cloud_load_peak forward_burnable_cloud_load_mean forward_burnable_cloud_load_effective
forward_burnable_cloud_load_full_window_mean forward_burnable_cloud_material_load_peak forward_burnable_cloud_material_load_mean
forward_burnable_cloud_material_load_effective forward_burnable_cloud_material_load_full_window_mean
forward_burnable_cloud_incoming_aerosol_load_mean forward_burnable_cloud_incoming_aerosol_load_effective
forward_burnable_cloud_incoming_aerosol_load_full_window_mean coverage_base_score coverage_score base_quality_index
raw_quality_index pre_response_quality_index duration_window_pre_response_index observer_local_burnable_cloud_load_peak
observer_local_burnable_cloud_load_mean observer_local_burnable_cloud_material_load_peak
observer_local_burnable_cloud_material_load_mean ground_area_burnable_load_mean observer_column_quality_index
observer_column_quality_peak";
const COMPONENT_SIGNED: &str = "material_adjustment incoming_aod_adjustment observer_aod_adjustment quality_response_adjustment duration_adjustment";
const COMPONENT_NONNEGATIVE: &str = "
duration_minutes duration_window_minutes quality_aod550 local_aod550 physically_burnable_steps observation_burnability_veto_steps
observer_return_optical_depth_mean observer_local_return_optical_depth_mean";
const COMPONENT_EXACT: &str = "
scoring_scope score_formula score_max forward_sector_half_angle_deg forward_field_radius_km material_reference_quality
burnable_ratio_factor quality_response_scale quality_response_exponent duration_factor cloud_optics_source
effective_radius_source cloud_optical_depth_source cloud_opacity_proxy_source cams_profile_normalization
visual_response_mapping unavailable_spectral_quantities observer_return_model";
const STEP_UNIT: &str = "
burn_score intensity_score extent_score vividness_score visibility_score burnable_cloud_coverage forward_view_cloud_field_amount
forward_burnable_cloud_field_amount forward_burnable_cloud_ratio forward_candidate_cloud_field_amount
forward_target_eligible_cloud_field_amount forward_solar_geometry_reachable_cloud_field_amount
forward_external_cloud_path_transmitted_field_amount forward_unoccluded_burn_potential_upper_bound solar_incoming_block_risk
forward_high_cloud_amount forward_mid_cloud_amount forward_low_cloud_amount forward_high_burnable_amount
forward_mid_burnable_amount forward_low_burnable_amount observer_local_cloud_field_amount observer_local_burnable_cloud_field_amount
ground_area_cloud_field_amount ground_area_burnable_cloud_field_amount target_deck_connected target_deck_attenuated_fraction
target_deep_risk peak_blocking observer_return_transmission observer_local_return_transmission cloud_geometry_confidence
brightness_color cloud_material_score observer_mid_blocker_opacity observer_low_blocker_opacity local_sky_score remote_sky_score
convective_risk";
const STEP_NONNEGATIVE: &str = "cloud_optical_depth clear_gap_count observer_return_optical_depth observer_local_return_optical_depth cloud_geometry_uncertainty_m quality_aod550";
const STEP_SCORE: &str = "
forward_burnable_cloud_load forward_burnable_cloud_material_load forward_burnable_cloud_incoming_aerosol_load
observer_local_burnable_cloud_load observer_local_burnable_cloud_material_load observer_local_burnable_cloud_incoming_aerosol_load
ground_area_burnable_cloud_load";
const INVALID_ZERO: &str = "
instant_quality_index burn_score intensity_score extent_score vividness_score visibility_score burnable_cloud_coverage
forward_view_cloud_field_amount forward_burnable_cloud_field_amount forward_candidate_cloud_field_amount
forward_target_eligible_cloud_field_amount forward_solar_geometry_reachable_cloud_field_amount
forward_external_cloud_path_transmitted_field_amount forward_unoccluded_burn_potential_upper_bound forward_burnable_cloud_load
forward_burnable_cloud_material_load forward_burnable_cloud_incoming_aerosol_load solar_incoming_block_risk forward_high_cloud_amount
forward_mid_cloud_amount forward_low_cloud_amount forward_high_burnable_amount forward_mid_burnable_amount forward_low_burnable_amount
observer_local_cloud_field_amount observer_local_burnable_cloud_field_amount observer_local_burnable_cloud_load
observer_local_burnable_cloud_material_load observer_local_burnable_cloud_incoming_aerosol_load ground_area_cloud_field_amount
ground_area_burnable_cloud_field_amount ground_area_burnable_cloud_load target_deck_connected target_deck_attenuated_fraction
target_deep_risk cloud_optical_depth peak_blocking clear_gap_count observer_return_optical_depth observer_return_transmission
observer_local_return_optical_depth observer_local_return_transmission cloud_geometry_uncertainty_m cloud_geometry_confidence
forward_burnable_cloud_ratio brightness_color cloud_material_score observer_mid_blocker_opacity observer_low_blocker_opacity
local_sky_score remote_sky_score convective_risk";
const GEOMETRY: [&str; 4] = [
    "dominant_burnable_pressure_family",
    "dominant_burnable_cloud_base_asl_mean_m",
    "dominant_burnable_cloud_top_asl_mean_m",
    "dominant_burnable_target_terrain_asl_mean_m",
];
const DUPLICATES: [(&str, &str); 10] = [
    (
        "burnable_cloud_coverage_effective",
        "burnable_cloud_coverage_effective",
    ),
    (
        "forward_view_cloud_field_amount_peak",
        "forward_view_cloud_field_amount_peak",
    ),
    (
        "forward_burnable_cloud_field_amount_peak",
        "forward_burnable_cloud_field_amount_peak",
    ),
    (
        "forward_burnable_cloud_load_peak",
        "forward_burnable_cloud_load_peak",
    ),
    (
        "forward_burnable_cloud_load_effective",
        "forward_burnable_cloud_load_effective",
    ),
    (
        "forward_burnable_cloud_material_load_peak",
        "forward_burnable_cloud_material_load_peak",
    ),
    (
        "forward_burnable_cloud_material_load_effective",
        "forward_burnable_cloud_material_load_effective",
    ),
    (
        "solar_incoming_block_risk_peak",
        "solar_incoming_block_risk_peak",
    ),
    (
        "forward_burnable_cloud_ratio_peak",
        "forward_burnable_cloud_ratio_peak",
    ),
    ("burnable_duration_minutes", "duration_minutes"),
];

#[derive(Deserialize)]
pub struct Request {
    pub target_date: String,
    pub timezone_name: String,
    pub point: astronomy::Point,
    #[serde(default = "dusk")]
    pub phenomenon: String,
    #[serde(default = "observer")]
    pub name: String,
    #[serde(default = "radius")]
    pub radius_km: f64,
    #[serde(default = "gfs")]
    pub model_source: String,
    #[serde(default = "model")]
    pub forecast_mode: String,
    pub gfs_cycle_override: Option<String>,
    #[serde(flatten)]
    other: Map<String, Value>,
}
fn dusk() -> String {
    "dusk".into()
}
fn observer() -> String {
    "Observer".into()
}
fn gfs() -> String {
    "gfs".into()
}
fn model() -> String {
    "model".into()
}
fn radius() -> f64 {
    1000.0
}
impl Request {
    pub(crate) fn compute_backend(&self) -> Result<&str, String> {
        let backend = match self.other.get("compute_backend") {
            None => "auto",
            Some(value) => value.as_str().ok_or("invalid compute backend type")?,
        };
        if !matches!(backend, "auto" | "cpu") {
            return Err("Rust point scoring currently supports auto/cpu only".into());
        }
        Ok(backend)
    }
    pub(crate) fn with_compute_backend(mut self, backend: Option<&str>) -> Self {
        if let Some(backend) = backend {
            self.other.insert("compute_backend".into(), json!(backend));
        }
        self
    }
    pub(crate) fn stored(
        point: &frames::Point,
        date: &str,
        phenomenon: &str,
        model: &str,
        radius: f64,
        cycle: String,
    ) -> Self {
        Self {
            target_date: date.into(),
            timezone_name: point.timezone_name.clone(),
            point: astronomy::Point {
                lat: point.lat,
                lon: point.lon,
                elevation_m: point.elevation_m,
            },
            phenomenon: phenomenon.into(),
            name: point.name.clone(),
            radius_km: radius,
            model_source: model.into(),
            forecast_mode: "model".into(),
            gfs_cycle_override: Some(cycle),
            other: Map::new(),
        }
    }
    fn check(&self) -> Result<(), String> {
        let allowed="aod550_override reference_time_utc satellite_source include_map_cells compute_backend gfs_resolution grid_region model_cycle_override";
        if self
            .other
            .keys()
            .any(|k| !allowed.split_ascii_whitespace().any(|v| v == k))
            || !matches!(self.model_source.as_str(), "gfs" | "ec")
            || !matches!(self.phenomenon.as_str(), "dawn" | "dusk")
            || !self.radius_km.is_finite()
            || !self.point.lat.is_finite()
            || !self.point.lon.is_finite()
            || !self.point.elevation_m.is_finite()
        {
            return Err("invalid point request identity".into());
        }
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    request: Request,
    core: Value,
}
pub fn execute_validate(params: Params) -> Result<Value, String> {
    validate(&params.request, &params.core)?;
    Ok(params.core)
}
pub(crate) fn validate_core(request: &Request, core: &Value) -> Result<(), String> {
    validate(request, core).map(|_| ())
}
pub fn execute_materialize(params: Params) -> Result<Value, String> {
    materialize(&params.request, &params.core)
}

fn ensure(ok: bool, field: &str) -> Result<(), String> {
    if ok {
        Ok(())
    } else {
        Err(format!("point core contract mismatch: {field}"))
    }
}
fn object<'a>(
    v: &'a Value,
    required: &str,
    optional: &str,
    field: &str,
) -> Result<&'a Map<String, Value>, String> {
    let m = v
        .as_object()
        .ok_or_else(|| format!("{field} must be an object"))?;
    let mut count = 0;
    for key in required.split_ascii_whitespace() {
        ensure(m.contains_key(key), field)?;
        count += 1;
    }
    count += optional
        .split_ascii_whitespace()
        .filter(|key| m.contains_key(*key))
        .count();
    // Schemas contain unique, disjoint required/optional keys. Once every
    // required key is present, cardinality also proves there are no extras.
    ensure(m.len() == count, field)?;
    Ok(m)
}
fn number(v: &Value, field: &str) -> Result<f64, String> {
    v.as_f64()
        .filter(|n| n.is_finite())
        .ok_or_else(|| format!("{field} must be a finite number"))
}
fn text<'a>(v: &'a Value, field: &str) -> Result<&'a str, String> {
    v.as_str()
        .filter(|s| {
            !s.trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
                .is_empty()
        })
        .ok_or_else(|| format!("{field} must be a non-empty string"))
}
fn float32(v: &Value, field: &str) -> Result<f32, String> {
    let value = number(v, field)? as f32;
    ensure(value.is_finite(), field)?;
    Ok(value)
}
fn range(v: &Value, field: &str, low: f64, high: f64) -> Result<f64, String> {
    let n = number(v, field)?;
    ensure(n >= low && n <= high, field)?;
    Ok(n)
}
fn equal_number(v: &Value, expected: f64, field: &str) -> Result<(), String> {
    ensure(number(v, field)? == expected, field)
}
fn integer(v: &Value, low: f64, high: Option<f64>, field: &str) -> Result<(), String> {
    let digits = v
        .as_number()
        .and_then(json_integer)
        .ok_or_else(|| format!("{field} must be an integer"))?;
    // Support counts are Python integers, not finite floating-point fields.
    // Values outside i64 can only satisfy these small bounds when positive
    // and unbounded above.
    match digits.parse::<i64>() {
        Ok(n) => ensure(n as f64 >= low && high.is_none_or(|h| n as f64 <= h), field),
        Err(_) => ensure(!digits.starts_with('-') && high.is_none(), field),
    }
}
fn json_integer(v: &serde_json::Number) -> Option<String> {
    let s = v.to_string();
    if s.bytes().any(|b| matches!(b, b'.' | b'e' | b'E')) {
        None
    } else {
        Some(if s == "-0" { "0".into() } else { s })
    }
}
fn fields_range(m: &Map<String, Value>, fields: &str, low: f64, high: f64) -> Result<(), String> {
    for k in fields.split_ascii_whitespace() {
        range(&m[k], k, low, high)?;
    }
    Ok(())
}
fn fields_nonnegative(m: &Map<String, Value>, fields: &str) -> Result<(), String> {
    for k in fields.split_ascii_whitespace() {
        ensure(number(&m[k], k)? >= 0.0, k)?;
    }
    Ok(())
}
// Python dictionary equality equates numeric JSON representations (including
// bool/number in otherwise untyped metadata), unlike serde_json::Value.
fn python_equal(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Array(a), Value::Array(b)) => {
            a.len() == b.len() && a.iter().zip(b).all(|(a, b)| python_equal(a, b))
        }
        (Value::Object(a), Value::Object(b)) => {
            a.len() == b.len()
                && a.iter()
                    .all(|(k, a)| b.get(k).is_some_and(|b| python_equal(a, b)))
        }
        (Value::Number(a), Value::Number(b)) => match (json_integer(a), json_integer(b)) {
            (Some(a), Some(b)) => a == b,
            (Some(i), None) => b.as_f64().is_some_and(|v| {
                v.is_finite()
                    && v.fract() == 0.0
                    && i == if v == 0.0 {
                        "0".into()
                    } else {
                        format!("{v:.0}")
                    }
            }),
            (None, Some(i)) => a.as_f64().is_some_and(|v| {
                v.is_finite()
                    && v.fract() == 0.0
                    && i == if v == 0.0 {
                        "0".into()
                    } else {
                        format!("{v:.0}")
                    }
            }),
            (None, None) => a.as_f64() == b.as_f64(),
        },
        (Value::Bool(a), Value::Number(b)) | (Value::Number(b), Value::Bool(a)) => {
            b.as_f64() == Some(f64::from(*a))
        }
        _ => a == b,
    }
}
fn geometry(m: &Map<String, Value>) -> Result<(), String> {
    if m[GEOMETRY[0]].is_null() {
        ensure(
            GEOMETRY[1..].iter().all(|&k| m[k].is_null()),
            "geometry without family",
        )
    } else {
        ensure(
            matches!(m[GEOMETRY[0]].as_str(), Some("high" | "middle" | "low")),
            "pressure family",
        )?;
        let base = number(&m[GEOMETRY[1]], GEOMETRY[1])?;
        let top = number(&m[GEOMETRY[2]], GEOMETRY[2])?;
        number(&m[GEOMETRY[3]], GEOMETRY[3])?;
        ensure(top >= base, "cloud top below base")
    }
}
fn color(step: &Map<String, Value>, valid: bool, instant: f64) -> Result<(), String> {
    let c = object(
        &step["color_diagnosis"],
        "type confidence cloud_phase source inputs",
        "",
        "color",
    )?;
    let i=object(&c["inputs"],"quality_strength brightness_color solar_elevation_deg aod550 solar_incoming_illumination_score deep_cloud_risk cloud_phase","","color inputs")?;
    for (name, expected) in [
        ("quality_strength", instant / 2.5),
        (
            "brightness_color",
            number(&step["brightness_color"], "brightness_color")?,
        ),
        (
            "solar_elevation_deg",
            number(&step["solar_elevation_deg"], "solar elevation")?,
        ),
        ("aod550", number(&step["quality_aod550"], "quality AOD")?),
        (
            "deep_cloud_risk",
            number(&step["convective_risk"], "convective risk")?,
        ),
    ] {
        equal_number(&i[name], expected, name)?;
    }
    let illumination = range(
        &i["solar_incoming_illumination_score"],
        "color illumination",
        0.0,
        1.0,
    )?;
    ensure(valid || illumination == 0.0, "invalid color illumination")?;
    let phase = text(&i["cloud_phase"], "color phase")?;
    ensure(
        matches!(phase, "unknown" | "ice" | "water" | "mixed") && c["cloud_phase"] == phase,
        "color phase",
    )?;
    if let Some(p) = step.get("cloud_phase_proxy").filter(|p| !p.is_null()) {
        let p = object(p, "label ice_fraction confidence source", "", "phase proxy")?;
        ensure(p["label"] == phase, "proxy/color phase")?;
        range(&p["confidence"], "phase confidence", 0.0, 1.0)?;
        if !p["ice_fraction"].is_null() {
            range(&p["ice_fraction"], "phase ice fraction", 0.0, 1.0)?;
        }
        text(&p["source"], "phase source")?;
    }
    if let Some(g) = step.get("cloud_genus_proxy").filter(|g| !g.is_null()) {
        let g=object(g,"label status confidence scoring_effect candidate_scores compatible_genera source model_source evidence","","genus proxy")?;
        ensure(
            g["status"] == "proxy" && g["scoring_effect"] == false,
            "display-only genus proxy",
        )?;
        text(&g["label"], "genus label")?;
        range(&g["confidence"], "genus confidence", 0.0, 1.0)?;
        let scores = g["candidate_scores"]
            .as_object()
            .ok_or("genus scores must be an object")?;
        for (k, v) in scores {
            ensure(!k.trim().is_empty(), "genus name")?;
            range(v, "genus score", 0.0, 1.0)?;
        }
        ensure(g["compatible_genera"].is_array(), "compatible genera array")?;
        text(&g["source"], "genus source")?;
        text(&g["model_source"], "genus model")?;
    }
    let expected = characteristics::execute(Operation::Color {
        burn_score: None,
        quality_score: Some(instant / 2.5),
        vividness_score: number(&i["brightness_color"], "color brightness")?,
        solar_elevation_deg: number(&i["solar_elevation_deg"], "color solar")?,
        aod550: number(&i["aod550"], "color AOD")?,
        solar_incoming_illumination_score: illumination,
        deep_cloud_risk: number(&i["deep_cloud_risk"], "color deep")?,
        cloud_phase: phase.into(),
    })?;
    ensure(
        python_equal(&Value::Object(c.clone()), &expected),
        "authoritative color",
    )
}

struct Validated {
    anchor: DateTime<FixedOffset>,
    best: Option<usize>,
}
fn validate(request: &Request, core: &Value) -> Result<Validated, String> {
    request.check()?;
    let c = object(core, ROOT, "", "point core fields")?;
    equal_number(&c["version"], 8.0, "version")?;
    ensure(c["algorithm"] == frames::CORE_ALGORITHM, "algorithm")?;
    ensure(
        c["forecast_mode"] == "model" && c["forecast_mode"] == request.forecast_mode,
        "forecast mode",
    )?;
    ensure(
        c["phenomenon"] == request.phenomenon && c["model_source"] == request.model_source,
        "phenomenon/model",
    )?;
    let date = time_contract::parse_date(text(&c["target_date"], "target date")?)?;
    ensure(
        date == time_contract::parse_date(&request.target_date)?,
        "target date",
    )?;
    let lat = range(&c["lat"], "latitude", -90.0, 90.0)?;
    let lon = range(&c["lon"], "longitude", -180.0, 180.0)?;
    ensure(
        (lat - request.point.lat).abs() <= 1e-9 && (lon - request.point.lon).abs() <= 1e-9,
        "coordinates",
    )?;
    ensure(
        (number(&c["observer_elevation_asl_m"], "elevation")? - request.point.elevation_m).abs()
            <= 1e-6,
        "elevation",
    )?;
    ensure(c["timezone_name"] == request.timezone_name, "timezone")?;
    let anchor = time_contract::parse_zoned(text(&c["anchor_local"], "anchor")?)?;
    ensure(anchor.date_naive() == date, "anchor date")?;
    let tz: chrono_tz::Tz = request
        .timezone_name
        .parse()
        .map_err(|_| "unknown point timezone")?;
    ensure(
        anchor.offset().local_minus_utc()
            == anchor.with_timezone(&tz).offset().fix().local_minus_utc(),
        "anchor offset",
    )?;
    let solar = astronomy::events(astronomy::EventsParams {
        point: request.point,
        date: request.target_date.clone(),
        timezone_name: request.timezone_name.clone(),
        phenomenon: if request.phenomenon == "dawn" {
            astronomy::Phenomenon::Dawn
        } else {
            astronomy::Phenomenon::Dusk
        },
    })?;
    let expected = time_contract::parse_utc(solar["anchor_time_utc"].as_str().unwrap())?;
    // Match datetime.timestamp() subtraction, including its loss of subsecond
    // precision for distant calendar dates; exact Duration comparison differs.
    let epoch = |time: DateTime<Utc>| {
        time.timestamp() as f64 + f64::from(time.timestamp_subsec_micros()) / 1e6
    };
    ensure(
        (epoch(anchor.with_timezone(&Utc)) - epoch(expected)).abs() < 1.0,
        "scalar solar event",
    )?;
    let cycle = time_contract::parse_utc(text(&c["cycle_time_utc"], "cycle")?)?;
    if let Some(pinned) = &request.gfs_cycle_override {
        ensure(
            cycle == time_contract::parse_zoned(pinned)?.with_timezone(&Utc),
            "pinned cycle",
        )?;
    }
    ensure(
        c["point_refinement"] == "raytraced-sky-v4-cpu"
            && c["precision_mode"] == "raytraced-sky-v4-cpu",
        "refinement/precision",
    )?;
    let radius = number(&c["analysis_radius_km"], "radius")?;
    ensure(radius == request.radius_km, "request radius")?;
    let cycles = object(
        &c["model_cycles"],
        &request.model_source,
        "",
        "model cycles",
    )?;
    let weights = object(
        &c["model_weights"],
        &request.model_source,
        "",
        "model weights",
    )?;
    ensure(
        time_contract::parse_utc(text(&cycles[&request.model_source], "model cycle")?)? == cycle,
        "model cycle",
    )?;
    equal_number(&weights[&request.model_source], 1.0, "model weight")?;
    fields_range(c,"confidence_score cloud_geometry_confidence model_disagreement_score overall_score convective_risk",0.0,1.0)?;
    for k in [
        "confidence_kind",
        "forecast_lead_semantics",
        "quality_index_semantics",
        "cloud_geometry_boundary_semantics",
        "cloud_geometry_proxy_source",
    ] {
        text(&c[k], k)?;
    }
    if !c["forecast_lead_hours"].is_null() {
        ensure(
            number(&c["forecast_lead_hours"], "lead")? >= 0.0,
            "nonnegative lead",
        )?;
    }
    equal_number(
        &c["model_disagreement_score"],
        0.0,
        "single-model disagreement",
    )?;
    fields_range(c, "quality_index peak_quality_index", 0.0, 2.5)?;
    for (index, level) in [
        ("quality_index", "quality_level"),
        ("peak_quality_index", "peak_quality_level"),
    ] {
        ensure(
            c[level] == empirical::quality_level(number(&c[index], index)?),
            level,
        )?;
    }
    integer(
        &c["cloud_geometry_support_count"],
        -1.0,
        None,
        "geometry support",
    )?;
    fields_nonnegative(c, "cloud_geometry_uncertainty_m")?;
    let optics = frames::optics(request.model_source == "ec");
    ensure(c["cloud_optics"] == optics, "cloud optics")?;
    let all_components = [
        COMPONENT_TEXT,
        COMPONENT_UNIT,
        COMPONENT_SCORE,
        COMPONENT_SIGNED,
        COMPONENT_NONNEGATIVE,
        COMPONENT_EXACT,
    ]
    .join(" ");
    let q = object(
        &c["quality_components"],
        &all_components,
        "",
        "quality components",
    )?;
    ensure(
        q["scoring_scope"] == quality::SCORING_SCOPE
            && q["score_formula"] == quality::FORMULA
            && q["observer_return_model"] == crate::diagnostics::RETURN_MODEL,
        "quality formula/source",
    )?;
    for k in COMPONENT_TEXT.split_ascii_whitespace() {
        text(&q[k], k)?;
    }
    fields_range(q, COMPONENT_UNIT, 0.0, 1.0)?;
    fields_range(q, COMPONENT_SCORE, 0.0, 2.5)?;
    fields_range(q, COMPONENT_SIGNED, -2.5, 2.5)?;
    fields_nonnegative(q, COMPONENT_NONNEGATIVE)?;
    for (k, expected) in [
        ("score_max", 2.5),
        ("forward_sector_half_angle_deg", 90.0),
        ("forward_field_radius_km", radius),
        ("material_reference_quality", 0.72),
        ("burnable_ratio_factor", 1.0),
        ("quality_response_scale", empirical::RESPONSE_SCALE),
        ("quality_response_exponent", empirical::RESPONSE_EXPONENT),
    ] {
        equal_number(&q[k], expected, k)?;
    }
    range(&q["duration_factor"], "duration factor", 0.0, 1.1)?;
    for (k, expected) in optics.as_object().unwrap() {
        ensure(q[k] == *expected, k)?;
    }
    for k in [
        "physically_burnable_steps",
        "observation_burnability_veto_steps",
    ] {
        let n = number(&q[k], k)?;
        ensure(n == n.floor(), k)?;
    }
    equal_number(
        &q["observation_burnability_veto_steps"],
        0.0,
        "observation veto",
    )?;
    fields_range(c,"burnable_cloud_coverage_effective forward_view_cloud_field_amount_peak forward_burnable_cloud_field_amount_peak solar_incoming_block_risk_peak forward_burnable_cloud_ratio_peak",0.0,1.0)?;
    fields_nonnegative(c,"forward_burnable_cloud_load_peak forward_burnable_cloud_load_effective forward_burnable_cloud_material_load_peak forward_burnable_cloud_material_load_effective burnable_duration_minutes")?;
    for (root, component) in DUPLICATES {
        equal_number(&c[root], number(&q[component], component)?, root)?;
    }
    geometry(c)?;
    let aerosol = object(&c["aerosol"], "aod550 source quality", "", "aerosol")?;
    fields_nonnegative(aerosol, "aod550")?;
    text(&aerosol["source"], "aerosol source")?;
    text(&aerosol["quality"], "aerosol quality")?;
    let steps = c["steps"]
        .as_array()
        .filter(|s| s.len() == 20)
        .ok_or("point core requires 20 canonical steps")?;
    let mut best: Option<usize> = None;
    let mut best_key = (0.0_f32, 0.0_f32, 0.0_f32);
    let mut valid_count = 0;
    let mut overall = 0.0;
    let mut active = 0;
    for (index, value) in steps.iter().enumerate() {
        let s = object(value, STEP, OPTIONAL_STEP, "point step fields")?;
        ensure(
            s["selected_column_diagnostic_source"]
                == if request.model_source == "ec" {
                    "ec-public-tcc-rh-proxy-selected-column-bilinear-diagnostic"
                } else {
                    "gfs-selected-column-bilinear-diagnostic"
                },
            "selected diagnostic source",
        )?;
        let offset = if request.phenomenon == "dawn" {
            -75 + index as i32 * 5
        } else {
            -20 + index as i32 * 5
        };
        equal_number(&s["offset_minutes"], f64::from(offset), "canonical offset")?;
        let valid = s["valid"].as_bool().ok_or("valid must be boolean")?;
        let burnable = s["physically_burnable"]
            .as_bool()
            .ok_or("physically_burnable must be boolean")?;
        let instant = range(&s["instant_quality_index"], "instant index", 0.0, 2.5)?;
        let burn_score = number(&s["burn_score"], "burn score")?;
        ensure(
            (burn_score - instant / 2.5).abs() <= 1e-9,
            "normalized burn score",
        )?;
        ensure(
            s["instant_quality_level"] == empirical::quality_level(instant),
            "instant quality level",
        )?;
        if !valid {
            ensure(!burnable, "invalid active sample")?;
            for k in INVALID_ZERO.split_ascii_whitespace() {
                equal_number(&s[k], 0.0, k)?;
            }
            ensure(
                s[GEOMETRY[0]].is_null()
                    && python_equal(&s["selected_column_cloud_material_class_code"], &json!(0))
                    && python_equal(&s["cloud_geometry_support_count"], &json!(-1)),
                "invalid cloud structure",
            )?;
        }
        fields_range(s, STEP_UNIT, 0.0, 1.0)?;
        let ledger = [
            "forward_burnable_cloud_field_amount",
            "forward_external_cloud_path_transmitted_field_amount",
            "forward_solar_geometry_reachable_cloud_field_amount",
            "forward_target_eligible_cloud_field_amount",
            "forward_view_cloud_field_amount",
            "forward_candidate_cloud_field_amount",
        ];
        if valid {
            for pair in ledger.windows(2) {
                ensure(
                    number(&s[pair[0]], pair[0])? <= number(&s[pair[1]], pair[1])? + 1e-6,
                    "monotonic loss ledger",
                )?;
            }
            ensure(
                number(
                    &s["forward_unoccluded_burn_potential_upper_bound"],
                    "upper bound",
                )? + 1e-6
                    >= number(&s[ledger[0]], ledger[0])?,
                "unoccluded upper bound",
            )?;
        }
        fields_nonnegative(s, STEP_NONNEGATIVE)?;
        fields_range(s, STEP_SCORE, 0.0, 2.5)?;
        range(&s["solar_elevation_deg"], "solar elevation", -90.0, 90.0)?;
        ensure(
            range(&s["solar_azimuth_deg"], "solar azimuth", 0.0, 360.0)? < 360.0,
            "solar azimuth",
        )?;
        integer(
            &s["cloud_geometry_support_count"],
            -1.0,
            None,
            "step support",
        )?;
        integer(
            &s["selected_column_cloud_material_class_code"],
            0.0,
            Some(8.0),
            "material class",
        )?;
        color(s, valid, instant)?;
        text(&s["cloud_geometry_proxy_source"], "step geometry source")?;
        let view = number(&s["forward_view_cloud_field_amount"], "view")?;
        let burn = number(&s["forward_burnable_cloud_field_amount"], "burn")?;
        let block = number(&s["solar_incoming_block_risk"], "block")?;
        let regime = if !valid {
            "invalid"
        } else if view < 0.01 {
            "clear"
        } else if block >= 0.55 && burn < view * 0.55 {
            "solar_incoming_blocked_field"
        } else {
            "forward_cloud_field"
        };
        ensure(s["selected_regime"] == regime, "selected regime")?;
        let scene = object(&s["scene_scores"], SCENE, "", "scene scores")?;
        fields_range(scene, SCENE, 0.0, 1.0)?;
        for (k, expected) in [
            ("clear", 1.0 - view),
            ("forward_cloud_field", view),
            ("burnable_cloud_field", burn),
            ("solar_incoming_blocked_field", block),
        ] {
            equal_number(&scene[k], if valid { expected } else { 0.0 }, k)?;
        }
        equal_number(
            &s["local_sky_score"],
            if valid { 1.0 - block } else { 0.0 },
            "local sky",
        )?;
        equal_number(&s["remote_sky_score"], burn, "remote sky")?;
        geometry(s)?;
        if valid {
            valid_count += 1;
            overall += burn_score;
            if (number(&s["burnable_cloud_coverage"], "coverage")? as f32)
                >= (number(&q["event_active_cloud_threshold"], "active threshold")? as f32)
            {
                active += 1;
            }
            if burnable {
                let key = (
                    instant as f32,
                    float32(&s["burnable_cloud_coverage"], "coverage")?,
                    float32(&s["forward_burnable_cloud_load"], "load")?,
                );
                if best.is_none() || key > best_key {
                    best = Some(index);
                    best_key = key;
                }
            }
        }
    }
    ensure(valid_count > 0, "no valid point samples")?;
    if let Some(index) = best {
        let s = steps[index].as_object().unwrap();
        equal_number(
            &c["best_offset_minutes"],
            number(&s["offset_minutes"], "best offset")?,
            "best offset",
        )?;
        equal_number(
            &c["peak_quality_index"],
            f64::from(best_key.0),
            "peak index",
        )?;
        for k in GEOMETRY {
            ensure(python_equal(&c[k], &s[k]), k)?;
        }
        for k in [
            "convective_risk",
            "cloud_geometry_uncertainty_m",
            "cloud_geometry_confidence",
        ] {
            equal_number(&c[k], number(&s[k], k)?, k)?;
        }
        for k in [
            "cloud_geometry_proxy_source",
            "cloud_geometry_support_count",
        ] {
            ensure(python_equal(&c[k], &s[k]), k)?;
        }
    } else {
        ensure(
            c["best_offset_minutes"].is_null() && c[GEOMETRY[0]].is_null(),
            "unexpected best step",
        )?;
        equal_number(&c["peak_quality_index"], 0.0, "empty peak")?;
        equal_number(&c["convective_risk"], 0.0, "empty convective risk")?;
    }
    equal_number(
        &q["physically_burnable_steps"],
        f64::from(active),
        "active count",
    )?;
    ensure(
        (number(&c["overall_score"], "overall")? - overall / f64::from(valid_count)).abs() <= 1e-7,
        "overall normalized score",
    )?;
    Ok(Validated { anchor, best })
}

fn fixed_iso(time: DateTime<FixedOffset>) -> String {
    let mut result = time.format("%Y-%m-%dT%H:%M:%S").to_string();
    if time.timestamp_subsec_micros() != 0 {
        result.push_str(&format!(".{:06}", time.timestamp_subsec_micros()));
    }
    result.push_str(&time.format("%:z").to_string());
    result
}
fn target_phase(family: &Value) -> Value {
    if family.is_null() {
        Value::Null
    } else {
        json!({"label":"unknown","ice_fraction":null,
        "confidence":0.0,"source":"not-resolved-for-native-burnable-target"})
    }
}
fn add_geometry(row: &mut Value, elevation: f64) {
    let family = !row[GEOMETRY[0]].is_null();
    let base = row[GEOMETRY[1]].as_f64().unwrap_or(0.0);
    let top = row[GEOMETRY[2]].as_f64().unwrap_or(0.0);
    let terrain = row[GEOMETRY[3]].as_f64().unwrap_or(0.0);
    for (k, v) in [
        (
            "dominant_burnable_cloud_base_agl_mean_m",
            (base - terrain).max(0.0),
        ),
        (
            "dominant_burnable_cloud_top_agl_mean_m",
            (top - terrain).max(0.0),
        ),
        (
            "dominant_burnable_cloud_base_above_observer_mean_m",
            base - elevation,
        ),
        (
            "dominant_burnable_cloud_top_above_observer_mean_m",
            top - elevation,
        ),
    ] {
        row[k] = if family { json!(v) } else { Value::Null };
    }
    row["dominant_burnable_cloud_phase"] = target_phase(&row[GEOMETRY[0]]);
}
pub fn materialize(request: &Request, core: &Value) -> Result<Value, String> {
    let validated = validate(request, core)?;
    let elevation = number(&core["observer_elevation_asl_m"], "elevation")?;
    let mut steps = core["steps"].as_array().unwrap().clone();
    for row in &mut steps {
        let offset = row["offset_minutes"].as_f64().unwrap() as i64;
        // The parsed Python anchor has a fixed UTC offset, not a ZoneInfo.
        let local = validated
            .anchor
            .checked_add_signed(Duration::minutes(offset))
            .ok_or("point time overflow")?;
        ensure(
            (1..=9999).contains(&local.year()),
            "local point calendar range",
        )?;
        time_contract::checked(local.with_timezone(&Utc))?;
        row["valid_time_local"] = json!(fixed_iso(local));
        row["valid_time_utc"] = json!(fixed_iso(local.with_timezone(&Utc).fixed_offset()));
        add_geometry(row, elevation);
    }
    let best = validated.best.map(|i| &steps[i]);
    let mut result = json!({});
    for k in [
        "analysis_radius_km",
        "observer_elevation_asl_m",
        "cycle_time_utc",
        "model_cycles",
        "overall_score",
        "peak_quality_index",
        "peak_quality_level",
        "quality_index",
        "quality_level",
        "quality_components",
        "confidence_score",
        "model_disagreement_score",
        "convective_risk",
        "model_weights",
        "aerosol",
        "point_refinement",
    ] {
        result[k] = core[k].clone();
    }
    for k in GEOMETRY {
        result[k] = core[k].clone();
    }
    add_geometry(&mut result, elevation);
    let duration = number(&core["burnable_duration_minutes"], "duration")?.round_ties_even();
    let integral: serde_json::Number = (if duration == 0.0 {
        "0".into()
    } else {
        format!("{duration:.0}")
    })
    .parse()
    .map_err(|e: serde_json::Error| e.to_string())?;
    result["duration_minutes"] = Value::Number(integral.clone());
    result["potential_duration_minutes"] = Value::Number(integral);
    result["burn_score"] = json!(number(&core["peak_quality_index"], "peak")? / 2.5);
    result["observer_name"] = json!(request.name);
    result["phenomenon"] = json!(request.phenomenon);
    result["model_source"] = json!(request.model_source);
    result["forecast_mode"] = json!(request.forecast_mode);
    result["target_date"] = json!(request.target_date);
    result["timezone_name"] = json!(request.timezone_name);
    result["anchor_local"] = json!(fixed_iso(validated.anchor));
    result["best_time_local"] = best.map_or(Value::Null, |b| b["valid_time_local"].clone());
    for k in [
        "intensity_score",
        "extent_score",
        "vividness_score",
        "remote_sky_score",
    ] {
        result[k] = best.map_or(json!(0.0), |b| b[k].clone());
    }
    result["local_sky_score"] = best.map_or(json!(1.0), |b| b["local_sky_score"].clone());
    result["selected_regime"] = best.map_or(json!("clear"), |b| b["selected_regime"].clone());
    result["scene_scores"] = best.map_or(
        json!({"clear":1.0,"forward_cloud_field":0.0,"burnable_cloud_field":0.0,
        "solar_incoming_blocked_field":0.0}),
        |b| b["scene_scores"].clone(),
    );
    for k in ["cloud_phase_proxy", "cloud_genus_proxy"] {
        result[k] = best.and_then(|b| b.get(k)).cloned().unwrap_or(Value::Null);
    }
    result["color_diagnosis"] = best.map_or(json!({}), |b| b["color_diagnosis"].clone());
    result["dominant_burnable_cloud_genus_diagnosis"] =
        characteristics::execute(Operation::Genus {
            dominant_pressure_family: core[GEOMETRY[0]].as_str().map(str::to_owned),
            cloud_phase: result["dominant_burnable_cloud_phase"]
                .get("label")
                .and_then(Value::as_str)
                .map(|s| Phase::Label(s.into())),
            cloud_base_agl_m: result["dominant_burnable_cloud_base_agl_mean_m"].as_f64(),
            cloud_top_agl_m: result["dominant_burnable_cloud_top_agl_mean_m"].as_f64(),
            convective_risk: number(&core["convective_risk"], "convective risk")?,
            model_source: request.model_source.clone(),
        })?;
    let curve = steps
        .iter()
        .map(characteristics::Step::deserialize)
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    result["cloud_evolution"] = characteristics::evolution(&curve, None)?;
    result["timing_window"] = characteristics::window(&curve, 0.25, "forward_cloud_field")?;
    result["source_roles"] =
        json!({"primary":"numerical_model","auxiliary":"satellite_observation"});
    result["steps"] = Value::Array(steps);
    let q = number(&core["quality_index"], "quality")?;
    let peak = number(
        &core["forward_burnable_cloud_field_amount_peak"],
        "peak coverage",
    )? * 100.0;
    let incoming = number(
        &core["quality_components"]["incoming_aod_adjustment"],
        "incoming adjustment",
    )?;
    let observer = number(
        &core["quality_components"]["observer_aod_adjustment"],
        "observer adjustment",
    )?;
    result["summary"]=json!([
        "结果由目标坐标同次完整前方云场直接生成。",
        format!("{}质量指数 {q:.3}/2.5（{}）。",if request.phenomenon=="dawn" {"朝霞"}else{"晚霞"},core["quality_level"].as_str().unwrap()),
        format!("可烧云占比峰值 {peak:.1}%，入射 AOD 修正 {incoming:+.3}，观测 AOD 修正 {observer:+.3}。"),
    ]);
    for k in [
        "nowcast",
        "html_report_path",
        "geometry_window",
        "boundary_uncertainty",
    ] {
        result[k] = Value::Null;
    }
    result["map_token"] = json!("");
    result["map_times"] = json!([]);
    result["html_report_url"] = json!("");
    result["confidence_score_semantics"] = core["confidence_kind"].clone();
    result["data_provenance"] = json!({"model_source":request.model_source,"cycle_utc":core["cycle_time_utc"],
        "gfs_cache_used":request.model_source=="gfs","cams_cache_used":true,"observation_used":false});
    result["duration_status"] = json!("estimated");
    result["compute_backend"] = json!("cpu");
    result["compute_backend_requested"] = json!("auto");
    result["map_capability"] = json!({"available":false,"model_source":request.model_source,
        "reason":"单点结果不携带地图时间轴；请在形势图页面查看区域云场。"});
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeSet;

    #[test]
    fn exact_schema_cardinality_has_no_duplicate_keys() {
        let components = [
            COMPONENT_TEXT,
            COMPONENT_UNIT,
            COMPONENT_SCORE,
            COMPONENT_SIGNED,
            COMPONENT_NONNEGATIVE,
            COMPONENT_EXACT,
        ]
        .join(" ");
        for fields in [ROOT, STEP, OPTIONAL_STEP, SCENE, &components] {
            let keys: Vec<_> = fields.split_ascii_whitespace().collect();
            assert_eq!(
                keys.len(),
                keys.iter().copied().collect::<BTreeSet<_>>().len()
            );
        }
        let required: BTreeSet<_> = STEP.split_ascii_whitespace().collect();
        assert!(OPTIONAL_STEP
            .split_ascii_whitespace()
            .all(|k| !required.contains(k)));
        assert!(object(&json!({"a":1,"b":2}), "a", "b", "test").is_ok());
        assert!(object(&json!({"a":1,"c":2}), "a", "b", "test").is_err());
        assert!(object(&json!({"b":2}), "a", "b", "test").is_err());
    }

    #[test]
    fn python_numeric_equality_does_not_round_large_integers() {
        fn value(s: &str) -> Value {
            serde_json::from_str(s).unwrap()
        }
        assert!(python_equal(&value("1"), &value("1.0")));
        assert!(python_equal(&value("true"), &value("1.0")));
        assert!(python_equal(&value("-0"), &value("0.0")));
        assert!(python_equal(
            &value("9007199254740992"),
            &value("9007199254740992.0")
        ));
        assert!(!python_equal(
            &value("9007199254740993"),
            &value("9007199254740992.0")
        ));
        assert!(!python_equal(
            &value("18446744073709551616"),
            &value("18446744073709551617")
        ));
        let huge = value(&format!("1{}", "0".repeat(400)));
        assert!(integer(&huge, -1.0, None, "support").is_ok());
        assert!(integer(&huge, 0.0, Some(8.0), "class").is_err());
        assert!(integer(&value("-9223372036854775809"), -1.0, None, "support").is_err());
        assert!(integer(&value("3.0"), -1.0, None, "support").is_err());
        assert!(integer(&value("true"), -1.0, None, "support").is_err());
    }
}
