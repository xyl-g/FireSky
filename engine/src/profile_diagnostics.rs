//! Observer-column sampling and material/geometry diagnostics.

use crate::tensor_bundle::{BundleWriter, ProfileTensor, MAX_BUNDLE_BYTES};
use firesky_native::profile_diagnostics::{self as native, Stencil};
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::time::Instant;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Params {
    profiles: Vec<Vec<Vec<[Option<f32>; 24]>>>,
    aggregates: Vec<Vec<[Option<f32>; 7]>>,
    #[serde(default)]
    pressure_levels: Vec<f32>,
    points: Vec<[f32; 2]>,
    samples: Vec<[f32; 9]>,
    origin: [f32; 2],
    spacing: f32,
    rows: usize,
    cols: usize,
    #[serde(default)]
    average_envelope: bool,
    #[serde(default)]
    include_sampled: bool,
}

pub fn execute(params: Params) -> Result<Value, String> {
    let levels = params
        .profiles
        .first()
        .and_then(|f| f.first())
        .map_or(0, Vec::len);
    let pressure_levels = if params.pressure_levels.len() == levels {
        params.pressure_levels.clone()
    } else {
        params
            .profiles
            .first()
            .and_then(|f| f.first())
            .into_iter()
            .flatten()
            .map(|row| row[14].unwrap_or(f32::NAN))
            .collect()
    };
    let cells = params
        .rows
        .checked_mul(params.cols)
        .ok_or("grid size overflow")?;
    let frames = params.profiles.len();
    if frames == 0
        || frames > 64
        || cells == 0
        || cells > 4096
        || levels == 0
        || levels > 256
        || pressure_levels.iter().any(|v| !v.is_finite())
        || frames * cells * levels * 24 > 65536
        || params.points.is_empty()
        || params.points.len() > 4096
        || params.points.len() * levels * 24 > 65536
        || params.samples.len() != params.points.len()
        || params.samples.iter().flatten().any(|v| !v.is_finite())
        || params.aggregates.len() != frames
        || params
            .profiles
            .iter()
            .any(|frame| frame.len() != cells || frame.iter().any(|cell| cell.len() != levels))
        || params.aggregates.iter().any(|frame| frame.len() != cells)
    {
        return Err("invalid profile diagnostic shapes or bounded sample count".into());
    }
    let profiles: Vec<Vec<[f32; 24]>> = params
        .profiles
        .into_iter()
        .map(|frame| {
            frame
                .into_iter()
                .flatten()
                .map(|row| row.map(|v| v.unwrap_or(f32::NAN)))
                .collect()
        })
        .collect();
    let aggregates: Vec<Vec<[f32; 7]>> = params
        .aggregates
        .into_iter()
        .map(|frame| {
            frame
                .into_iter()
                .map(|row| row.map(|v| v.unwrap_or(f32::NAN)))
                .collect()
        })
        .collect();
    let mut output = Vec::with_capacity(params.points.len());
    let mut sampled = Vec::new();
    let mut sampled_aggregates = Vec::new();
    for (point, sample) in params.points.iter().zip(&params.samples) {
        let stencil = Stencil::new(
            *point,
            params.origin,
            params.spacing,
            params.rows,
            params.cols,
        )?;
        let left = frame_index(sample[2], frames);
        let right = frame_index(sample[3], frames);
        let mut profile = vec![[0.0; 24]; levels];
        stencil.sample_with(
            sample[4],
            profile.as_flattened_mut(),
            |second, cell, field| {
                profiles[if second { right } else { left }][cell * levels + field / 24][field % 24]
            },
        )?;
        let mut aggregate = [0.0; 7];
        stencil.sample_with(sample[4], &mut aggregate, |second, cell, field| {
            aggregates[if second { right } else { left }][cell][field]
        })?;
        output.push(native::diagnose(
            &profile,
            &pressure_levels,
            aggregate,
            params.average_envelope,
        )?);
        if params.include_sampled {
            sampled.push(profile);
            sampled_aggregates.push(aggregate);
        }
    }
    Ok(json!({
        "contract":"rust-profile-diagnostics-v1",
        "fields":native::FIELDS,
        "values":output,
        "sampled_profiles":if params.include_sampled {json!(sampled)} else {Value::Null},
        "sampled_aggregates":if params.include_sampled {json!(sampled_aggregates)} else {Value::Null},
        "confidence_affects_quality":false,
        "target_phase_proven":false,
    }))
}

pub(crate) fn frame_index(value: f32, frames: usize) -> usize {
    // Python casts to int64 (truncation) before clamping.
    (value as i64).clamp(0, frames as i64 - 1) as usize
}

pub(crate) struct Stored<'a> {
    pub profiles: &'a [ProfileTensor],
    pub columns: &'a [[f32; 36]],
    pub observers: &'a [[f32; 3]],
    pub samples: &'a [f32],
    pub levels: &'a [i32],
    pub rows: usize,
    pub cols: usize,
    pub origin: [f32; 2],
    pub spacing: f32,
    pub boundary_semantics: &'a str,
}

pub(crate) fn append_with_data(
    writer: &mut BundleWriter,
    input: Stored<'_>,
) -> Result<(Value, Vec<[f32; 7]>), String> {
    let started = Instant::now();
    let points = input.observers.len();
    let cells = input
        .rows
        .checked_mul(input.cols)
        .ok_or("profile grid size overflow")?;
    let frames = input.profiles.len();
    if frames == 0
        || frames > 64
        || points == 0
        || points > 500_000
        || input.columns.len() != frames * cells
        || input.samples.len() != points * 20 * 9
        || input.samples.iter().any(|v| !v.is_finite())
    {
        return Err("invalid stored profile sampling shape".into());
    }
    let levels: Vec<_> = input.levels.iter().map(|&v| v as f32).collect();
    let frame_bytes = cells as u128 * levels.len() as u128 * 24 * 4;
    if frame_bytes * 2 > u128::from(MAX_BUNDLE_BYTES) {
        return Err("selected-column two-frame working set exceeds 1 GiB".into());
    }
    let stencils = input
        .observers
        .iter()
        .map(|p| {
            Stencil::new(
                [p[0], p[1]],
                input.origin,
                input.spacing,
                input.rows,
                input.cols,
            )
        })
        .collect::<Result<Vec<_>, _>>()?;
    let mut groups: BTreeMap<(usize, usize), Vec<usize>> = BTreeMap::new();
    for (index, sample) in input.samples.as_chunks::<9>().0.iter().enumerate() {
        groups
            .entry((
                frame_index(sample[2], frames),
                frame_index(sample[3], frames),
            ))
            .or_default()
            .push(index);
    }
    let mut cache: BTreeMap<usize, Vec<f32>> = BTreeMap::new();
    let mut output = vec![[0.0; 7]; points * 20];
    let mut load_ms = 0.0;
    let mut compute_ms = 0.0;
    let mut loads = 0;
    for ((left, right), indices) in groups {
        cache.retain(|&index, _| index == left || index == right);
        let phase = Instant::now();
        for index in [left, right] {
            if let std::collections::btree_map::Entry::Vacant(entry) = cache.entry(index) {
                entry.insert(input.profiles[index].load()?);
                loads += 1;
            }
        }
        load_ms += phase.elapsed().as_secs_f64() * 1000.0;
        let phase = Instant::now();
        let work: Vec<_> = indices
            .iter()
            .map(|&index| native::SelectedSample {
                stencil: stencils[index / 20],
                time_weight: input.samples[index * 9 + 4],
            })
            .collect();
        let pair = native::FramePair {
            profiles: [&cache[&left], &cache[&right]],
            columns: [
                &input.columns[left * cells..(left + 1) * cells],
                &input.columns[right * cells..(right + 1) * cells],
            ],
            levels: &levels,
            average_envelope: input.boundary_semantics == "average-envelope-not-instantaneous",
        };
        let values = pair.diagnose_many(&work, true)?;
        for (index, row) in indices.into_iter().zip(values) {
            output[index] = row;
        }
        compute_ms += phase.elapsed().as_secs_f64() * 1000.0;
    }
    drop(cache);
    writer.f32(
        "profile_diagnostics",
        &[points as u64, 20, 7],
        output.iter().flatten().copied(),
    )?;
    Ok((
        json!({
            "contract":"rust-profile-diagnostics-v1", "fields":native::FIELDS,
            "boundary_temporal_semantics":input.boundary_semantics,
            "sampling_policy":"temporal-finite-fallback-then-horizontal-finite-normalization-f32",
            "confidence_affects_quality":false, "target_phase_proven":false,
            "maximum_resident_profile_frames":2, "frame_payload_bytes":frame_bytes as u64,
            "frame_loads":loads, "profile_load_and_verify_ms":load_ms,
            "sampling_and_classification_ms":compute_ms,
            "composition_and_output_ms":started.elapsed().as_secs_f64() * 1000.0,
        }),
        output,
    ))
}
