//! Complete theoretical pressure-profile cross-sections over verified per-hour bundles.
use crate::astronomy::Phenomenon;
use crate::native::profile_diagnostics::Stencil;
use crate::native::timeline::{self, ModelTimes, Point};
use crate::tensor_bundle::{source_identity, ProfileTensor, SourceIdentity};
use crate::time_contract::{parse_date, parse_utc};
use serde::Deserialize;
use serde_json::{json, Value};
use std::path::{Component, Path, PathBuf};

const CONTRACT: &str = "rust-cross-section-source-v1";
const RADIUS_KM: f64 = 6371.0;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    source_path: PathBuf,
    source_identity: SourceIdentity,
    point: InputPoint,
    offset_minutes: f64,
    #[serde(default = "default_radius")]
    max_distance_km: f64,
    #[serde(default = "default_step")]
    distance_step_km: f64,
}
fn default_radius() -> f64 {
    1000.0
}
fn default_step() -> f64 {
    25.0
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct InputPoint {
    lat: f64,
    lon: f64,
    elevation_m: f64,
    timezone_name: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Geometry {
    rows: usize,
    cols: usize,
    lat0: f32,
    lon0: f32,
    spacing: f32,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Frame {
    manifest: PathBuf,
    identity: SourceIdentity,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Source {
    contract: String,
    model_source: String,
    terrain_source: Option<String>,
    target_date: String,
    phenomenon: Phenomenon,
    cycle_utc: String,
    hours: Vec<f64>,
    pressure_levels: Vec<f32>,
    geometry: Geometry,
    profile_bundles: Vec<Frame>,
}

fn child(root: &Path, path: &Path) -> Result<PathBuf, String> {
    if path.components().count() != 2
        || path
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
        || path.file_name().and_then(|v| v.to_str()) != Some("manifest.json")
    {
        return Err("profile reference must be a local child manifest".into());
    }
    Ok(root.join(path))
}

pub(crate) fn for_point(
    source_path: PathBuf,
    source_identity: SourceIdentity,
    point: &crate::frames::Point,
    offset_minutes: f64,
) -> Result<Value, String> {
    execute(Params {
        source_path,
        source_identity,
        point: InputPoint {
            lat: point.lat,
            lon: point.lon,
            elevation_m: point.elevation_m,
            timezone_name: point.timezone_name.clone(),
        },
        offset_minutes,
        max_distance_km: default_radius(),
        distance_step_km: default_step(),
    })
}

pub fn execute(params: Params) -> Result<Value, String> {
    if !params.offset_minutes.is_finite()
        || !params.max_distance_km.is_finite()
        || !(0.0..=1000.0).contains(&params.max_distance_km)
        || !params.distance_step_km.is_finite()
        || !(0.1..=1000.0).contains(&params.distance_step_km)
        || (params.max_distance_km + 0.1) / params.distance_step_km > 4096.0
        || params.point.timezone_name.len() > 128
    {
        return Err("cross-section requires bounded finite distances, offset and timezone".into());
    }
    let (source, identity): (Source, _) =
        crate::request_file::read(&params.source_path, 256 * 1024)?;
    if identity != params.source_identity {
        return Err("cross-section source checksum mismatch".into());
    }
    let geometry = &source.geometry;
    let levels = &source.pressure_levels;
    let cells = geometry
        .rows
        .checked_mul(geometry.cols)
        .ok_or("grid overflow")?;
    if source.contract != CONTRACT
        || !matches!(source.model_source.as_str(), "ec" | "gfs")
        || source.hours.is_empty()
        || source.hours.len() > 64
        || source.hours.len() != source.profile_bundles.len()
        || source.hours.iter().any(|v| !v.is_finite() || *v < 0.0)
        || source.hours.windows(2).any(|w| w[0] >= w[1])
        || levels.is_empty()
        || levels.len() > 256
        || levels
            .iter()
            .any(|v| !v.is_finite() || *v <= 0.0 || *v > 1100.0)
        || levels.windows(2).any(|w| w[0] >= w[1])
    {
        return Err("invalid cross-section source contract or time/pressure axes".into());
    }
    let point = Point {
        lat: params.point.lat,
        lon: params.point.lon,
        elevation_m: params.point.elevation_m,
        timezone: params
            .point
            .timezone_name
            .parse()
            .map_err(|_| "unknown IANA timezone")?,
    };
    // Validate geometry even when the requested path has only its origin.
    Stencil::new(
        [point.lat as f32, point.lon as f32],
        [geometry.lat0, geometry.lon0],
        geometry.spacing,
        geometry.rows,
        geometry.cols,
    )?;
    let cycle = parse_utc(&source.cycle_utc)?;
    let rising = matches!(source.phenomenon, Phenomenon::Dawn);
    let prepared = timeline::prepare(
        &[point],
        parse_date(&source.target_date)?,
        rising,
        Some(ModelTimes {
            cycle_epoch: cycle.timestamp() as f64
                + f64::from(cycle.timestamp_subsec_micros()) / 1e6,
            hours: &source.hours,
        }),
    )
    .map_err(|e| e.to_string())?;
    let row = &prepared.rows[0];
    let index = row
        .evaluation
        .iter()
        .position(|&t| ((t - row.anchor) / 60.0 - params.offset_minutes).abs() <= 1e-7)
        .ok_or("cross-section offset must exactly match the event's five-minute grid")?;
    let samples = prepared
        .samples
        .as_ref()
        .ok_or("missing profile time samples")?;
    let sample = &samples[0][index * 9..index * 9 + 9];
    let left = sample[2] as usize;
    let right = sample[3] as usize;
    let weight = sample[4];
    if right < left
        || right > left + 1
        || right >= source.hours.len()
        || !weight.is_finite()
        || !(0.0..=1.0).contains(&weight)
        || (left == right && weight != 0.0)
    {
        return Err("invalid cross-section time interpolation".into());
    }
    let root = params
        .source_path
        .parent()
        .ok_or("cross-section source has no parent")?;
    let open = |index: usize| -> Result<ProfileTensor, String> {
        let frame = &source.profile_bundles[index];
        let tensor = ProfileTensor::open(
            &child(root, &frame.manifest)?,
            frame.identity.clone(),
            cells,
            levels.len(),
        )?;
        tensor.validate_axis(index, source.hours[index], levels)?;
        Ok(tensor)
    };
    // NumPy arange(float64 start/step, dtype=float32) uses a float32 step.
    let count = ((params.max_distance_km + 0.1) / params.distance_step_km).ceil() as usize;
    let distances: Vec<f32> = (0..count)
        .map(|i| i as f32 * params.distance_step_km as f32)
        .collect();
    let mut stencils = Vec::with_capacity(count);
    for &distance in &distances {
        let (lat, lon) = crate::native::destination_point(
            point.lat,
            point.lon,
            f64::from(sample[1]),
            f64::from(distance),
        );
        stencils.push(Stencil::new(
            [lat as f32, lon as f32],
            [geometry.lat0, geometry.lon0],
            geometry.spacing,
            geometry.rows,
            geometry.cols,
        )?);
    }
    let mut selected: Vec<_> = stencils.iter().flat_map(Stencil::cells).collect();
    selected.sort_unstable();
    selected.dedup();
    let first = open(left)?;
    let first_values = first.load_cells(&selected)?;
    let second = if right == left {
        None
    } else {
        Some(open(right)?)
    };
    let second_values = second
        .as_ref()
        .map(|s| s.load_cells(&selected))
        .transpose()?;
    let frames = [
        &first_values[..],
        second_values.as_deref().unwrap_or(&first_values),
    ];
    let mut values = Vec::with_capacity(count);
    let mut terrain = Vec::with_capacity(count);
    for (index, stencil) in stencils.into_iter().enumerate() {
        let stencil = stencil.remapped(|cell| selected.binary_search(&cell).unwrap());
        let mut profile = vec![[0.0; 24]; levels.len()];
        stencil.sample_with(weight, profile.as_flattened_mut(), |second, cell, field| {
            frames[usize::from(second)][cell * levels.len() * 24 + field]
        })?;
        let supported = stencil.has_finite_support(levels.len(), |second, cell, level| {
            frames[usize::from(second)][(cell * levels.len() + level) * 24 + 23]
        });
        let finite: Vec<_> = profile
            .iter()
            .map(|p| p[23])
            .filter(|v| v.is_finite())
            .collect();
        let height = f64::from(numpy_sum(&finite)) / finite.len() as f64;
        if index > 0 && (!supported || !height.is_finite()) {
            return Err(
                "profile lacks remote terrain; observer elevation cannot replace it".into(),
            );
        }
        terrain.push(if index == 0 {
            point.elevation_m
        } else {
            height
        });
        values.push(profile);
    }
    let result = assemble(
        &source,
        point,
        params.max_distance_km,
        &distances,
        &terrain,
        &values,
        f64::from(sample[0]),
        f64::from(sample[1]),
        ((row.evaluation[index] - row.anchor) / 60.0).round_ties_even() as i32,
        crate::astronomy::local_iso(
            crate::assessment::from_epoch(row.evaluation[index])?,
            point.timezone,
        )?,
    );
    first.revalidate()?;
    if let Some(second) = second {
        second.revalidate()?;
    }
    if source_identity(&params.source_path)? != identity {
        return Err("cross-section source changed during execution".into());
    }
    Ok(result)
}

// NumPy's contiguous float32 pairwise reduction (including its eight lanes).
fn numpy_sum(values: &[f32]) -> f32 {
    if values.len() < 8 {
        return values.iter().fold(-0.0, |s, &v| s + v);
    }
    if values.len() > 128 {
        let middle = (values.len() / 2) / 8 * 8;
        return numpy_sum(&values[..middle]) + numpy_sum(&values[middle..]);
    }
    let mut lanes: [f32; 8] = values[..8].try_into().unwrap();
    let end = values.len() - values.len() % 8;
    for chunk in values[8..end].as_chunks::<8>().0 {
        for (a, b) in lanes.iter_mut().zip(chunk) {
            *a += b;
        }
    }
    let sum = ((lanes[0] + lanes[1]) + (lanes[2] + lanes[3]))
        + ((lanes[4] + lanes[5]) + (lanes[6] + lanes[7]));
    values[end..].iter().fold(sum, |s, &v| s + v)
}
fn mean(profile: &[[f32; 24]], field: usize) -> f64 {
    let values: Vec<_> = profile
        .iter()
        .map(|p| p[field])
        .filter(|v| v.is_finite())
        .collect();
    f64::from(numpy_sum(&values) / values.len() as f32)
}
fn rounded(value: f64, places: i32) -> Value {
    if !value.is_finite() {
        return Value::Null;
    }
    // Formatting, unlike multiply/round/divide, preserves binary half-way cases.
    let value = format!("{:.*}", places as usize, value)
        .parse::<f64>()
        .unwrap();
    json!(value)
}

/// Pick the evaluation step closest to the requested offset. Callers derive
/// offsets from report anchors (sunset/sunrise), which can differ from the
/// timeline anchor by sub-minute amounts; exact matching would reject every
/// such request. On-grid offsets still resolve to their exact step.
fn nearest_evaluation_index(evaluation: &[f64], anchor: f64, offset_minutes: f64) -> Option<usize> {
    let target = anchor + offset_minutes * 60.0;
    evaluation
        .iter()
        .enumerate()
        .min_by(|a, b| (a.1 - target).abs().total_cmp(&(b.1 - target).abs()))
        .map(|(index, _)| index)
}

/// Snap a report-derived offset onto the event's five-minute evaluation grid.
/// Desktop point/service reports anchor steps at sunset/sunrise while the
/// frozen profile timeline uses its own anchor; the two can differ by
/// sub-minute amounts. Strict `execute` keeps its exact contract for frozen
/// profile queries; automatic cross-sections snap first.
pub(crate) fn snap_offset_to_event_grid(
    source_path: &Path,
    point: &crate::frames::Point,
    offset_minutes: f64,
) -> Result<f64, String> {
    let (source, _identity): (Source, _) = crate::request_file::read(source_path, 256 * 1024)?;
    let cycle = parse_utc(&source.cycle_utc)?;
    let zone: chrono_tz::Tz = point
        .timezone_name
        .parse()
        .map_err(|_| "unknown observer timezone")?;
    let prepared = timeline::prepare(
        &[Point {
            lat: point.lat,
            lon: point.lon,
            elevation_m: point.elevation_m,
            timezone: zone,
        }],
        parse_date(&source.target_date)?,
        matches!(source.phenomenon, Phenomenon::Dawn),
        Some(ModelTimes {
            cycle_epoch: cycle.timestamp() as f64
                + f64::from(cycle.timestamp_subsec_micros()) / 1e6,
            hours: &source.hours,
        }),
    )
    .map_err(|e| e.to_string())?;
    let row = &prepared.rows[0];
    let index = nearest_evaluation_index(&row.evaluation, row.anchor, offset_minutes)
        .ok_or("cross-section event has no evaluation grid")?;
    Ok((row.evaluation[index] - row.anchor) / 60.0)
}

#[allow(clippy::too_many_arguments)]
fn assemble(
    source: &Source,
    observer: Point,
    radius: f64,
    distances: &[f32],
    terrain: &[f64],
    profiles: &[Vec<[f32; 24]>],
    solar: f64,
    azimuth: f64,
    offset: i32,
    valid_time: String,
) -> Value {
    let levels = &source.pressure_levels;
    let shadow: Vec<f32> = distances
        .iter()
        .map(|&distance| {
            crate::native::required_illumination_height_m(f64::from(distance), solar, 0.0) as f32
        })
        .collect();
    let mut targets = Vec::new();
    let mut amounts = Vec::new();
    let mut status_rows = Vec::new();
    let mut family_rows = Vec::new();
    let mut base_rows = Vec::new();
    let mut top_rows = Vec::new();
    for (distance_index, profile) in profiles.iter().enumerate() {
        let amount: Vec<_> = profile
            .iter()
            .map(|p| crate::native::cloud::effective_profile(p)[0])
            .collect();
        let max_where = |condition: &dyn Fn(f32) -> bool| {
            levels
                .iter()
                .zip(&amount)
                .filter(|(p, _)| condition(**p))
                .map(|(_, &a)| a)
                .fold(0.0_f32, f32::max)
        };
        let upper = max_where(&|p| p <= 300.0);
        let middle = max_where(&|p| p > 400.0 && p <= 700.0);
        let boundary = max_where(&|p| p == 400.0);
        let pair = if levels.contains(&250.0) && levels.contains(&300.0) {
            levels
                .iter()
                .zip(&amount)
                .filter(|(p, _)| **p == 250.0 || **p == 300.0)
                .map(|(_, &a)| a)
                .fold(1.0_f32, f32::min)
        } else {
            0.0
        };
        let adaptive = upper >= 0.30 && !(boundary >= 0.60 && (middle < 0.40 || pair >= 0.80));
        let families: Vec<u8> = levels
            .iter()
            .map(|&p| {
                if p <= 400.0 && !(p == 400.0 && adaptive) {
                    1
                } else if p <= 700.0 {
                    2
                } else {
                    3
                }
            })
            .collect();
        let mut statuses = vec![Value::Null; levels.len()];
        let mut bases = vec![Value::Null; levels.len()];
        let mut tops = vec![Value::Null; levels.len()];
        let distance = f64::from(distances[distance_index]);
        for (code, name, label, base_field) in [
            (1, "high", "高云压力族", 17),
            (2, "middle", "中云压力族", 19),
            (3, "low", "低云压力族", 21),
        ] {
            let cloudy: Vec<_> = families
                .iter()
                .zip(&amount)
                .enumerate()
                .filter(|(_, (&family, &a))| family == code && a >= 0.06)
                .map(|(i, _)| i)
                .collect();
            if cloudy.is_empty() {
                continue;
            }
            let mut base = cloudy
                .iter()
                .map(|&i| profile[i][1])
                .fold(f32::NAN, f32::min) as f64;
            let mut top = cloudy
                .iter()
                .map(|&i| profile[i][1])
                .fold(f32::NAN, f32::max) as f64;
            let aggregate_base = mean(profile, base_field);
            let aggregate_top = mean(profile, base_field + 1);
            if aggregate_base.is_finite()
                && aggregate_top.is_finite()
                && aggregate_top > aggregate_base
                && !(code == 1
                    && adaptive
                    && levels.contains(&400.0)
                    && (aggregate_base < base - 1500.0 || aggregate_top > top + 1500.0))
            {
                base = base.min(aggregate_base);
                top = top.max(aggregate_top);
            }
            if !base.is_finite() || !top.is_finite() {
                continue;
            }
            let lit = base >= f64::from(shadow[distance_index]);
            let horizon = horizon_margin(observer.elevation_m, base, distance);
            let clearance = (1..=distance_index)
                .map(|i| {
                    sight_height(
                        observer.elevation_m,
                        base,
                        distance,
                        f64::from(distances[i]),
                    ) - terrain[i]
                        - 25.0
                })
                .filter(|v| v.is_finite())
                .fold(f64::INFINITY, f64::min);
            let blocked = clearance <= 0.0;
            let status = if !lit {
                1
            } else if horizon <= 0.0 || blocked {
                2
            } else {
                3
            };
            let reason = if status == 3 {
                "云底高于理论照明阈值，且高于几何地平线；此候选状态未评估云体遮挡"
            } else if !lit {
                "云底未达到理论照明高度"
            } else if horizon <= 0.0 && blocked {
                "已高于理论照明阈值，但低于几何地平线且回程地形阻挡"
            } else if horizon <= 0.0 {
                "已高于理论照明阈值，但低于几何地平线"
            } else if blocked {
                "已高于理论照明阈值，但回程视线被地形遮挡"
            } else {
                "已高于理论照明阈值，但回程几何视线不可见"
            };
            let condensate = |field: usize| -> f64 {
                let values: Vec<_> = cloudy
                    .iter()
                    .map(|&i| profile[i][field])
                    .map(|v| if v.is_nan() { 0.0 } else { v })
                    .collect();
                f64::from(numpy_sum(&values))
            };
            let mut phase =
                crate::characteristics::phase(Some(condensate(12)), Some(condensate(11)));
            phase["source"] = if phase["label"] == "unknown" {
                json!("same-target-condensate-unavailable")
            } else {
                json!(format!(
                    "{}-pressure-level-condensate-same-distance-same-pressure-family",
                    source.model_source
                ))
            };
            for &i in &cloudy {
                statuses[i] = json!(status);
                bases[i] = rounded(f64::from(base as f32), 2);
                tops[i] = rounded(f64::from(top as f32), 2);
            }
            targets.push(json!({
                "distance_km":distance,"pressure_family":name,"pressure_family_label":label,
                "cloud_phase":phase,"cloud_base_asl_m":base,"cloud_top_asl_m":top,
                "target_terrain_asl_m":terrain[distance_index],
                "cloud_base_agl_m":(base-terrain[distance_index]).max(0.0),
                "cloud_top_agl_m":(top-terrain[distance_index]).max(0.0),
                "cloud_base_above_observer_m":base-observer.elevation_m,
                "cloud_top_above_observer_m":top-observer.elevation_m,
                "geometric_horizon_margin_m":horizon,"observer_terrain_blocked":blocked,
                "observer_terrain_clearance_m":if clearance.is_finite() {Some(clearance)} else {None},
                "required_base_asl_m":f64::from(shadow[distance_index]),
                "required_base_agl_m":f64::from(shadow[distance_index]-terrain[distance_index] as f32),
                "status":if status==3 {"burnable"} else if status==2 {"illuminated_not_visible"} else {"not_burnable"},
                "status_reason":reason,
            }));
        }
        amounts.push(
            amount
                .iter()
                .map(|&a| rounded(f64::from(a), 4))
                .collect::<Vec<_>>(),
        );
        family_rows.push(families);
        status_rows.push(statuses);
        base_rows.push(bases);
        top_rows.push(tops);
    }
    let matrix = |field: usize, offset: f32| -> Vec<Vec<Value>> {
        profiles
            .iter()
            .map(|p| {
                p.iter()
                    .map(|p| rounded(f64::from(p[field] + offset), 4))
                    .collect()
            })
            .collect()
    };
    json!({
        "model_source":source.model_source,"terrain_source":source.terrain_source,
        "observer":{"lat":observer.lat,"lon":observer.lon,"elevation_asl_m":observer.elevation_m},
        "offset_minutes":offset,"valid_time_local":valid_time,"solar_elevation_deg":solar,
        "solar_azimuth_deg":azimuth,"spatial_sampling":"bilinear-regular-grid","analysis_radius_km":radius,
        "distances_km":distances.iter().map(|&d| f64::from(d)).collect::<Vec<_>>(),
        "terrain_elevation_asl_m":terrain.iter().map(|&v| rounded(v,2)).collect::<Vec<_>>(),
        "pressure_levels_hpa":levels.iter().map(|&v| v as i32).collect::<Vec<_>>(),
        "required_illumination_base_asl_m":shadow.iter().map(|&h| f64::from(h)).collect::<Vec<_>>(),
        "required_illumination_base_agl_m":shadow.iter().zip(terrain).map(|(&h,&t)| rounded(f64::from(h)-t,2)).collect::<Vec<_>>(),
        "theoretical_targets":targets,
        "fields":{
            "geopotential_height_asl_m":matrix(1,0.0),"temperature_c":matrix(2,-273.15),
            "rh_percent":matrix(0,0.0),"cloud_fraction_percent":matrix(6,0.0),"cloud_presence":matrix(13,0.0),
            "cloud_amount":amounts,"cloud_liquid_kg_kg":matrix(11,0.0),"cloud_ice_kg_kg":matrix(12,0.0),
            "omega_pa_s":matrix(3,0.0),"u_wind_ms":matrix(4,0.0),"v_wind_ms":matrix(5,0.0),
            "cloud_layer_status":status_rows,"pressure_family_code":family_rows,
            "cloud_layer_base_asl_m":base_rows,"cloud_layer_top_asl_m":top_rows,
        },
    })
}

fn horizon_margin(observer: f64, target: f64, distance: f64) -> f64 {
    if distance <= 1e-6 {
        return target - observer;
    }
    let radius = RADIUS_KM * 1000.0;
    let horizon_radius = radius.max(radius + observer);
    let depression = -(radius / horizon_radius).min(1.0).acos();
    let angle = distance / RADIUS_KM;
    let denominator = angle.cos() - depression.tan() * angle.sin();
    if denominator <= 1e-9 {
        f64::NEG_INFINITY
    } else {
        radius + target - horizon_radius / denominator
    }
}
fn sight_height(observer: f64, target: f64, distance: f64, sample: f64) -> f64 {
    let distance = distance.max(0.0);
    let sample = sample.clamp(0.0, distance);
    if distance <= 1e-6 || sample <= 1e-6 {
        return observer;
    }
    if sample >= distance - 1e-6 {
        return target;
    }
    let radius = RADIUS_KM * 1000.0;
    let origin = radius + observer;
    let target_radius = radius + target;
    let angle = distance / RADIUS_KM;
    let tangent = (sample / RADIUS_KM).tan();
    let denominator =
        target_radius * angle.sin() - tangent * (target_radius * angle.cos() - origin);
    if denominator.abs() <= 1e-9 {
        return observer + sample / distance * (target - observer);
    }
    let fraction = (tangent * origin / denominator).clamp(0.0, 1.0);
    (origin + fraction * (target_radius * angle.cos() - origin))
        .hypot(fraction * target_radius * angle.sin())
        - radius
}

#[cfg(test)]
mod tests {
    use super::nearest_evaluation_index;

    #[test]
    fn offset_snaps_to_nearest_event_grid_step() {
        let anchor = 1_000_000.25;
        let evaluation: Vec<f64> = (-20..0)
            .map(|step| anchor + f64::from(step * 5) * 60.0)
            .collect();
        // Exact on-grid offsets keep their step.
        assert_eq!(
            nearest_evaluation_index(&evaluation, anchor, -75.0),
            Some(5)
        );
        // Report anchors can sit sub-minute away from the timeline anchor;
        // the derived offset must snap instead of failing the whole forecast.
        assert_eq!(
            nearest_evaluation_index(&evaluation, anchor, -75.0 - 0.25 / 60.0),
            Some(5)
        );
        // The grid spans -100..-5; -72.9 still snaps to the -75 step (5),
        // while -70 lands exactly on step 6.
        assert_eq!(
            nearest_evaluation_index(&evaluation, anchor, -72.9),
            Some(5)
        );
        assert_eq!(
            nearest_evaluation_index(&evaluation, anchor, -70.0),
            Some(6)
        );
        assert_eq!(
            nearest_evaluation_index(&evaluation, anchor, -20.0),
            Some(16)
        );
        assert_eq!(nearest_evaluation_index(&[], anchor, 0.0), None);
    }
}
