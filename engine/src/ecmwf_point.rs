//! EC's independent report contract, not the generic point materializer.
use crate::{
    characteristics::{self, Operation, Phase},
    point_core::{self, Request},
    time_contract,
};
use chrono::{DateTime, Duration, Utc};
use serde::Deserialize;
use serde_json::{json, Value};

pub const CONFIDENCE: &str = "diagnostic_input_coverage_not_statistical_confidence";
const SOURCE: &str = "ec-public-tcc-rh-proxy-selected-column-bilinear-diagnostic";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    request: Request,
    core: Value,
    source_hours: Vec<u32>,
    samples: Vec<[f32; 9]>,
}

pub fn execute(params: Params) -> Result<Value, String> {
    materialize(
        &params.request,
        &params.core,
        &params.source_hours,
        &params.samples,
    )
}

fn iso(time: DateTime<Utc>) -> String {
    crate::astronomy::local_iso(time, chrono_tz::UTC).expect("validated EC UTC calendar")
}

pub(crate) fn domain(radius: f64) -> Result<[f64; 3], String> {
    if !radius.is_finite() || radius <= 0.0 {
        return Err("EC point radius must be finite and positive".into());
    }
    let candidate = radius.min(1000.0);
    let trace = (candidate + 400.0).min(1000.0);
    Ok([candidate, trace, candidate + trace])
}

pub(crate) fn materialize(
    request: &Request,
    core: &Value,
    hours: &[u32],
    samples: &[[f32; 9]],
) -> Result<Value, String> {
    if request.model_source != "ec"
        || request.forecast_mode != "model"
        || hours.is_empty()
        || hours.len() > 64
        || hours.windows(2).any(|v| v[0] >= v[1])
        || hours
            .iter()
            .any(|v| !crate::ecmwf::published_step(i64::from(*v)))
        || samples.len() != 20
        || samples.iter().flatten().any(|v| !v.is_finite())
    {
        return Err("EC report requires one EC model core and canonical source samples".into());
    }
    // The shared validator checks all scoring, geometry and canonical offsets;
    // the separate EC checks below bind sample indices to actual source times.
    point_core::validate_core(request, core)?;
    let cycle =
        time_contract::parse_utc(core["cycle_time_utc"].as_str().ok_or("missing EC cycle")?)?;
    for &hour in hours {
        time_contract::checked(cycle + Duration::hours(i64::from(hour)))?;
    }
    let anchor =
        DateTime::parse_from_rfc3339(core["anchor_local"].as_str().ok_or("missing EC anchor")?)
            .map_err(|_| "invalid EC anchor")?;
    let zone: chrono_tz::Tz = request
        .timezone_name
        .parse()
        .map_err(|_| "invalid EC timezone")?;
    let quality_steps = core["steps"].as_array().ok_or("missing EC quality steps")?;
    let best = core["best_offset_minutes"]
        .as_f64()
        .map(|offset| {
            quality_steps
                .iter()
                .position(|v| v["offset_minutes"].as_f64() == Some(offset))
                .ok_or("invalid EC best event offset")
        })
        .transpose()?;
    let backend = request.compute_backend()?;
    let events = crate::astronomy::events(crate::astronomy::EventsParams {
        point: request.point,
        date: request.target_date.clone(),
        timezone_name: request.timezone_name.clone(),
        phenomenon: if request.phenomenon == "dawn" {
            crate::astronomy::Phenomenon::Dawn
        } else {
            crate::astronomy::Phenomenon::Dusk
        },
    })?;
    let mut steps = Vec::with_capacity(20);
    let mut curve = Vec::with_capacity(20);
    for (q, sample) in quality_steps.iter().zip(samples) {
        if q["selected_column_diagnostic_source"] != SOURCE {
            return Err("EC selected-column diagnostic is not the EC adapter".into());
        }
        let time = anchor
            .with_timezone(&Utc)
            .checked_add_signed(Duration::minutes(
                q["offset_minutes"].as_f64().ok_or("invalid EC offset")? as i64,
            ))
            .ok_or("EC evaluation time overflow")?;
        time_contract::checked(time)?;
        let local = crate::astronomy::local_iso(time, zone)?;
        let mut source_times = Vec::new();
        if sample[6] >= 0.5 {
            for &index in &sample[2..4] {
                if index < 0.0 || index.fract() != 0.0 || index as usize >= hours.len() {
                    return Err("EC native sample references a source outside its tensor".into());
                }
                let value = cycle + Duration::hours(i64::from(hours[index as usize]));
                if !source_times.contains(&value) {
                    source_times.push(value);
                }
            }
            let left = cycle + Duration::hours(i64::from(hours[sample[2] as usize]));
            let right = cycle + Duration::hours(i64::from(hours[sample[3] as usize]));
            // Samples use float32 interpolation weights; retain the native tolerance.
            let weight = if right == left {
                0.0
            } else {
                (time - left)
                    .num_microseconds()
                    .ok_or("EC sample time overflow")? as f64
                    / (right - left).num_microseconds().unwrap() as f64
            };
            if time < left || time > right || (f64::from(sample[4]) - weight).abs() > 2e-6 {
                return Err("EC source sample bracket does not cover evaluation time".into());
            }
        }
        let mut reasons = vec![
            "云场来源：ECMWF IFS public Open Data basic。".to_owned(),
            "TCC 与 PL RH 组成 EC-basic 云柱代理；HCC/MCC/LCC、model-level CC/CLWC/CIWC 保持 unavailable。".into(),
            "事件时刻使用左右 3 小时 EC 源场线性插值；shared native scorer 对有 EC PL u/v 的云族执行 wind-aware 半拉格朗日路径采样。".into(),
            "质量来自 shared native observer-field forward scorer；未调用 legacy assess_timestep。".into(),
            "CAMS aerext532 11 层垂直 extinction profile 已由 native scorer 消费；缺失 profile 时 fail-closed。".into(),
        ];
        if !source_times.is_empty() {
            reasons.push(format!(
                "EC 源时刻：{}",
                source_times
                    .iter()
                    .map(|v| v.format("%Y-%m-%d %H:%MZ").to_string())
                    .collect::<Vec<_>>()
                    .join(", ")
            ));
        }
        let mut step = json!({
            "forecast_hour":((time-cycle).num_microseconds().ok_or("EC lead overflow")? as f64/3_600_000_000.0).round_ties_even() as i64,
            "valid_time_utc":iso(time),"valid_time_local":local,
            "low_cloud_penalty":0.0,"candidate_count":0,
            "burning_cloud_count":if q["physically_burnable"]==true {1}else{0},
            "top_candidate":null,"strong_burning_cloud_count":0,"top_boundary":null,
            "boundary_score":0.0,"boundary_uncertainty":null,"top_cloud_hole":null,
            "cloud_hole_score":0.0,"cross_section":null,"solar_inflow_blockage":null,
            "model_disagreement_score":0.0,
            "cloud_source_times_utc":source_times.iter().copied().map(iso).collect::<Vec<_>>(),
            "reasons":reasons,"geometric_cloud_coverage":q["forward_view_cloud_field_amount"],
            "structure_gate_score":q["cloud_geometry_confidence"],"observation_burnable":true,
            "observation_score_ceiling":null,"satellite_retrieval_material_evidence":null,
            "selected_observer_path_aod550":null,"selected_inflow_path_aod550":null,
            "selected_aerosol_path_variability_score":0.0,
        });
        for key in [
            "solar_elevation_deg",
            "solar_azimuth_deg",
            "burn_score",
            "intensity_score",
            "extent_score",
            "vividness_score",
            "visibility_score",
            "selected_regime",
            "scene_scores",
            "local_sky_score",
            "remote_sky_score",
            "convective_risk",
            "color_diagnosis",
            "forward_view_cloud_field_amount",
            "forward_burnable_cloud_field_amount",
            "observer_local_cloud_field_amount",
        ] {
            step[key] = q[key].clone();
        }
        curve.push(json!({"valid_time_local":local,
            "burn_score":q["instant_quality_index"].as_f64().unwrap()/2.5,
            "instant_quality_index":q["instant_quality_index"],
            "physically_burnable":q["physically_burnable"],
            "burnable_cloud_coverage":q["burnable_cloud_coverage"],"scene_scores":q["scene_scores"],
            "local_sky_score":q["local_sky_score"],"remote_sky_score":q["remote_sky_score"]}));
        steps.push(step);
    }
    let selected = best.map(|i| &steps[i]);
    let duration = core["quality_components"]["duration_minutes"]
        .as_f64()
        .map(|v| v.round_ties_even() as i64);
    let mut summary = vec![
        "ECMWF IFS basic 是独立云模式数值预测，不读取 GFS 或实况缓存。".to_owned(),
        format!("EC cycle {}，评估 {} 个事件时刻。",cycle.format("%Y-%m-%d %H:%MZ"),steps.len()),
        "当前 public 合同使用 TCC + PL RH 云柱代理；HCC/MCC/LCC 和 model-level 云水/云冰不可用，结果属于 EC-basic proxy。".into(),
        "EC surface z 按 geopotential m2/s2 / 9.80665 转为地形米；原始 z 和 native 网格保留在输入缓存/张量中。".into(),
        "每个事件评估时刻都由 shared native scorer 使用对应 CAMS aerext532 11 层垂直 profile 的 3 小时 bracket；未找到完整 CAMS profile 会直接拒绝质量发布。".into(),
        "quality_index 来自 native forward-field scorer；本路径未调用 legacy assess_timestep。".into(),
        "confidence_score=0.42 仅表示输入覆盖诊断，不是统计置信度。".into(),
    ];
    if let Some(step) = selected {
        summary.extend(
            step["reasons"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_str().unwrap().to_owned()),
        );
    } else {
        summary.push("EC IFS 时间窗没有足够云目标。".into());
    }
    let mut report = json!({
        "observer_name":request.name,"phenomenon":request.phenomenon,"model_source":"ec",
        "target_date":request.target_date,"timezone_name":request.timezone_name,"anchor_local":events["anchor_time_local"],
        "cycle_time_utc":iso(cycle),"model_cycles":{"ec":iso(cycle)},
        "aerosol":{"aod550":core["aerosol"]["aod550"],"source":core["aerosol"]["source"],
            "quality":"cams","observer_lat":request.point.lat,"observer_lon":request.point.lon,
            "path_azimuth_deg":null,"path_samples":[],"pm2_5_ug_m3":null,"pm10_ug_m3":null,"particulate_source":null},
        "overall_score":core["overall_score"],"burn_score":core["peak_quality_index"].as_f64().unwrap()/2.5,
        "duration_minutes":duration,"potential_duration_minutes":duration,"confidence_score":0.42,
        "best_time_local":selected.map(|s|&s["valid_time_local"]),"summary":summary,
        "forecast_mode":"model","model_disagreement_score":0.0,"nowcast":null,"model_weights":{"ec":1.0},
        "model_weight_samples":{"ec":steps.len()},"model_component_scores":{"ec":core["overall_score"]},
        "html_report_path":null,"compute_backend_requested":backend,"compute_backend":"cpu",
        "geometry_window":null,"boundary_uncertainty":null,
        "selected_regime":selected.map_or(json!("unknown"),|s|s["selected_regime"].clone()),
        "scene_scores":selected.map_or(json!({}),|s|s["scene_scores"].clone()),
        "color_diagnosis":selected.map_or(json!({}),|s|s["color_diagnosis"].clone()),
    });
    for key in [
        "intensity_score",
        "extent_score",
        "vividness_score",
        "local_sky_score",
        "remote_sky_score",
        "convective_risk",
    ] {
        report[key] = selected.map_or(json!(0.0), |s| s[key].clone());
    }
    let curve: Vec<characteristics::Step> =
        serde_json::from_value(json!(curve)).map_err(|e| e.to_string())?;
    report["cloud_evolution"] = characteristics::evolution(&curve, best.map(|i| i as i64))?;
    report["timing_window"] =
        characteristics::window(&curve, 0.25, report["selected_regime"].as_str().unwrap())?;
    report["steps"] = json!(steps);
    let quality = json!({"quality_index":core["quality_index"],"quality_level":core["quality_level"],
        "quality_components":core["quality_components"],"duration_status":"estimated",
        "best_index":best,"steps":quality_steps});
    let web = web_payload(&report, &quality, request.point.elevation_m);
    Ok(
        json!({"contract":"rust-ecmwf-point-report-v1","report":report,
        "native_quality":quality,"dominant_burnable":dominant(core,best)?,
        "web_report_payload":web,
        "confidence_semantics":CONFIDENCE,"public_forecast_complete":false}),
    )
}

pub(crate) fn web_payload(report: &Value, quality: &Value, elevation: f64) -> Value {
    let mut result = json!({});
    for key in [
        "observer_name",
        "phenomenon",
        "model_source",
        "forecast_mode",
        "compute_backend_requested",
        "compute_backend",
        "target_date",
        "timezone_name",
        "anchor_local",
        "cycle_time_utc",
        "model_cycles",
        "best_time_local",
        "overall_score",
        "burn_score",
        "intensity_score",
        "extent_score",
        "vividness_score",
        "confidence_score",
        "model_disagreement_score",
        "duration_minutes",
        "potential_duration_minutes",
        "model_weights",
        "geometry_window",
        "boundary_uncertainty",
        "selected_regime",
        "scene_scores",
        "local_sky_score",
        "remote_sky_score",
        "convective_risk",
        "color_diagnosis",
        "cloud_evolution",
        "timing_window",
        "summary",
        "nowcast",
        "html_report_path",
    ] {
        result[key] = report[key].clone();
    }
    for key in [
        "quality_index",
        "quality_level",
        "quality_components",
        "duration_status",
    ] {
        result[key] = quality[key].clone();
    }
    let peak = quality["best_index"].as_u64().map_or(0.0, |i| {
        quality["steps"][i as usize]["instant_quality_index"]
            .as_f64()
            .unwrap()
    });
    result["peak_quality_index"] = json!(peak);
    result["peak_quality_level"] = json!(crate::native::empirical::quality_level(peak));
    result["analysis_radius_km"] = quality["quality_components"]["forward_field_radius_km"].clone();
    result["observer_elevation_asl_m"] = json!(elevation);
    result["source_roles"] =
        json!({"primary":"numerical_model","auxiliary":"satellite_observation"});
    let mut aerosol = json!({});
    for key in [
        "aod550",
        "source",
        "quality",
        "pm2_5_ug_m3",
        "pm10_ug_m3",
        "particulate_source",
    ] {
        aerosol[key] = report["aerosol"][key].clone();
    }
    result["aerosol"] = aerosol;
    let mut rows = Vec::with_capacity(20);
    for (step, q) in report["steps"]
        .as_array()
        .unwrap()
        .iter()
        .zip(quality["steps"].as_array().unwrap())
    {
        let mut row = json!({});
        for key in [
            "valid_time_local",
            "cloud_source_times_utc",
            "solar_elevation_deg",
            "intensity_score",
            "extent_score",
            "vividness_score",
            "visibility_score",
            "selected_regime",
            "scene_scores",
            "local_sky_score",
            "remote_sky_score",
            "convective_risk",
            "color_diagnosis",
            "top_candidate",
        ] {
            row[key] = step[key].clone();
        }
        row.as_object_mut()
            .unwrap()
            .extend(q.as_object().unwrap().clone());
        // Match the owning web serializer's explicit override for invalid rows;
        // native_quality retains the authoritative core validity.
        row["valid"] = json!(true);
        row["burn_score"] =
            json!((q["instant_quality_index"].as_f64().unwrap() / 2.5).clamp(0.0, 1.0));
        rows.push(row);
    }
    result["steps"] = json!(rows);
    result
}

fn dominant(core: &Value, best: Option<usize>) -> Result<Value, String> {
    let steps = core["steps"].as_array().unwrap();
    let step = &steps[best.unwrap_or(0)];
    let phase = step.get("cloud_phase_proxy").unwrap_or(&Value::Null);
    let label = phase.get("label").unwrap_or(phase);
    let base = core["dominant_burnable_cloud_base_asl_mean_m"].as_f64();
    let top = core["dominant_burnable_cloud_top_asl_mean_m"].as_f64();
    let terrain = core["dominant_burnable_target_terrain_asl_mean_m"].as_f64();
    let elevation = core["observer_elevation_asl_m"].as_f64();
    let family = &core["dominant_burnable_pressure_family"];
    let risk = core["convective_risk"].as_f64().unwrap();
    let base_agl = base.zip(terrain).map(|(a, b)| a - b);
    let top_agl = top.zip(terrain).map(|(a, b)| a - b);
    let diagnosis = if family.is_null() {
        Value::Null
    } else {
        characteristics::execute(Operation::Genus {
            dominant_pressure_family: family.as_str().map(str::to_owned),
            cloud_phase: Some(Phase::Label(label.as_str().unwrap_or("unknown").to_owned())),
            cloud_base_agl_m: base_agl,
            cloud_top_agl_m: top_agl,
            convective_risk: risk,
            model_source: "ec".into(),
        })?
    };
    Ok(json!({"pressure_family":family,"cloud_phase":label,
        "cloud_base_asl_mean_m":base,"cloud_top_asl_mean_m":top,"target_terrain_asl_mean_m":terrain,
        "cloud_base_agl_mean_m":base_agl,"cloud_top_agl_mean_m":top_agl,
        "cloud_base_above_observer_mean_m":base.zip(elevation).map(|(a,b)|a-b),
        "cloud_top_above_observer_mean_m":top.zip(elevation).map(|(a,b)|a-b),
        "genus_proxy":step.get("cloud_genus_proxy"),"phase_proxy":phase,
        "genus_diagnosis":diagnosis,"convective_risk":risk}))
}
