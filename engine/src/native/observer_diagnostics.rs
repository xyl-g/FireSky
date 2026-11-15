//! Additional event diagnostics from the authoritative 82-field ledger.
use crate::native::{empirical::MAX_SAMPLES, quality, NATIONAL_OUTPUT_FIELDS};
use rayon::prelude::*;

pub const FIELDS: [&str; 20] = [
    "solar_incoming_block_risk_at_best_cloud",
    "observer_return_optical_depth_mean",
    "observer_return_transmission_mean",
    "observer_local_return_optical_depth_mean",
    "observer_local_return_transmission_mean",
    "ground_area_view_amount_mean",
    "ground_area_burnable_amount_mean",
    "ground_area_burnable_load_mean",
    "forward_high_view_peak",
    "forward_high_view_mean",
    "forward_mid_view_peak",
    "forward_mid_view_mean",
    "forward_low_view_peak",
    "forward_low_view_mean",
    "forward_high_burn_peak",
    "forward_high_burn_mean",
    "forward_mid_burn_peak",
    "forward_mid_burn_mean",
    "forward_low_burn_peak",
    "forward_low_burn_mean",
];

pub fn summarize(
    values: &[f32],
    offsets: &[f64],
    indices: &[usize],
    gates: Option<&[bool]>,
) -> Result<[f64; FIELDS.len()], String> {
    let n = offsets.len();
    if n > MAX_SAMPLES
        || indices.len() > MAX_SAMPLES
        || values.len() != n * NATIONAL_OUTPUT_FIELDS
        || offsets.iter().any(|v| !v.is_finite())
        || values.iter().any(|v| !v.is_finite())
        || indices.iter().any(|&i| i >= n)
        || gates.is_some_and(|g| g.len() != n)
    {
        return Err("invalid observer diagnostic ledger, offsets, selection or gate".into());
    }
    let mut result = [0.0; FIELDS.len()];
    if indices.is_empty() {
        return Ok(result);
    }
    let field = |i: usize, k: usize| f64::from(values[indices[i] * NATIONAL_OUTPUT_FIELDS + k]);
    let selected_offsets: Vec<_> = indices.iter().map(|&i| offsets[i]).collect();
    let widths = quality::sample_widths(&selected_offsets);
    if widths.iter().any(|v| !v.is_finite()) {
        return Err("diagnostic sample widths overflow".into());
    }
    let burn: Vec<_> = indices
        .iter()
        .enumerate()
        .map(|(i, &source)| {
            let allowed = field(i, 5).clamp(0.0, 1.0) >= 0.05 && gates.is_none_or(|g| g[source]);
            let block = field(i, 60).clamp(0.0, 1.0);
            let cap = if block > 1e-9 {
                (field(i, 43) * (1.0 - block + 0.015)).clamp(0.0, 1.0)
            } else {
                1.0
            };
            if allowed {
                field(i, 44).clamp(0.0, 1.0).min(cap)
            } else {
                0.0
            }
        })
        .collect();
    let active: Vec<_> = burn.iter().map(|&v| v >= 0.005).collect();
    let mut peak = 0;
    for i in 1..indices.len() {
        if burn[i] > burn[peak] {
            peak = i;
        }
    }
    result[0] = field(peak, 60).clamp(0.0, 1.0);
    let active_width: f64 = widths
        .iter()
        .zip(&active)
        .filter(|(_, a)| **a)
        .map(|(w, _)| w)
        .sum();
    let active_count = active.iter().filter(|&&a| a).count();
    for (slot, source) in [73, 74, 75, 76].into_iter().enumerate() {
        let transform = |i| {
            if source % 2 == 1 {
                field(i, source).max(0.0)
            } else {
                field(i, source).clamp(0.0, 1.0)
            }
        };
        result[slot + 1] = if active_width > 0.0 {
            (0..indices.len())
                .filter(|&i| active[i])
                .map(|i| transform(i) * widths[i])
                .sum::<f64>()
                / active_width
        } else if active_count > 0 {
            (0..indices.len())
                .filter(|&i| active[i])
                .map(transform)
                .sum::<f64>()
                / active_count as f64
        } else {
            0.0
        };
    }
    for (slot, source) in [57, 58, 59].into_iter().enumerate() {
        result[slot + 5] =
            (0..indices.len()).map(|i| field(i, source)).sum::<f64>() / indices.len() as f64;
    }
    for source in 48..54 {
        let slot = 8 + (source - 48) * 2;
        result[slot] = (0..indices.len())
            .map(|i| field(i, source).clamp(0.0, 1.0))
            .fold(0.0, f64::max);
        result[slot + 1] = (0..indices.len())
            .map(|i| field(i, source).clamp(0.0, 1.0))
            .sum::<f64>()
            / indices.len() as f64;
    }
    if result.iter().any(|v| !v.is_finite()) {
        return Err("diagnostic reductions overflow".into());
    }
    Ok(result)
}

/// Float32 contiguous reduction order used by the original selected-sample
/// AOD mean. Float64 promotion before this mean would alter public metadata.
pub fn sample_aod_mean(values: &[f32]) -> f64 {
    fn sum(values: &[f32]) -> f32 {
        match values.len() {
            0..=7 => values.iter().fold(-0.0, |s, &v| s + v),
            8..=128 => {
                let mut lanes: [f32; 8] = values[..8].try_into().unwrap();
                let end = values.len() - values.len() % 8;
                for chunk in values[8..end].as_chunks::<8>().0 {
                    for i in 0..8 {
                        lanes[i] += chunk[i];
                    }
                }
                let mut result = ((lanes[0] + lanes[1]) + (lanes[2] + lanes[3]))
                    + ((lanes[4] + lanes[5]) + (lanes[6] + lanes[7]));
                for &v in &values[end..] {
                    result += v;
                }
                result
            }
            _ => {
                let half = (values.len() / 2) / 8 * 8;
                sum(&values[..half]) + sum(&values[half..])
            }
        }
    }
    if values.is_empty() {
        0.0
    } else {
        f64::from(sum(values) / values.len() as f32).max(0.0)
    }
}

pub fn summarize_many(
    values: &[f32],
    points: usize,
    offsets: &[f64],
    parallel: bool,
) -> Result<Vec<[f64; FIELDS.len()]>, String> {
    let steps = offsets.len();
    let count = points
        .checked_mul(steps)
        .ok_or("diagnostic size overflow")?;
    if points > 500_000
        || !(1..=MAX_SAMPLES).contains(&steps)
        || count > 10_000_000
        || count.checked_mul(NATIONAL_OUTPUT_FIELDS) != Some(values.len())
    {
        return Err("invalid diagnostic batch dimensions".into());
    }
    let stride = steps * NATIONAL_OUTPUT_FIELDS;
    let mut output = vec![[0.0; FIELDS.len()]; points];
    let fill = |i: usize, target: &mut [f64; FIELDS.len()]| -> Result<(), String> {
        let values = &values[i * stride..(i + 1) * stride];
        let indices: Vec<_> = (0..steps)
            .filter(|&s| values[s * NATIONAL_OUTPUT_FIELDS] >= 0.5)
            .collect();
        *target =
            summarize(values, offsets, &indices, None).map_err(|e| format!("event {i}: {e}"))?;
        Ok(())
    };
    if parallel && points >= 1024 {
        let results: Vec<_> = output
            .par_iter_mut()
            .enumerate()
            .with_min_len(64)
            .map(|(i, row)| fill(i, row))
            .collect();
        for result in results {
            result?;
        }
    } else {
        for (i, row) in output.iter_mut().enumerate() {
            fill(i, row)?;
        }
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn selected_float32_means_match_numpy_boundary_vectors() {
        for (n, expected) in [
            (1, 0x00000000),
            (7, 0x3fa10842),
            (8, 0x3fbbdef8),
            (9, 0x3faa3538),
            (20, 0x3fa97f2c),
            (127, 0x3fc4786d),
            (128, 0x3fc30000),
            (129, 0x3fc1f7ef),
            (255, 0x3fc4bc7a),
            (4096, 0x3fc61a52),
        ] {
            let values: Vec<_> = (0..n).map(|i| ((i * 13) % 97) as f32 / 31.0).collect();
            assert_eq!(
                (sample_aod_mean(&values) as f32).to_bits(),
                expected,
                "n={n}"
            );
        }
    }
    #[test]
    fn selection_gate_and_no_active_are_explicit() {
        let mut values = vec![0.0; 2 * NATIONAL_OUTPUT_FIELDS];
        for (i, row) in values
            .as_chunks_mut::<NATIONAL_OUTPUT_FIELDS>()
            .0
            .iter_mut()
            .enumerate()
        {
            row[43] = 1.0;
            row[44] = 0.5;
            row[5] = 1.0;
            row[60] = i as f32 * 0.5;
            row[73] = 2.0 + i as f32;
        }
        let result = summarize(&values, &[0.0, 5.0], &[0, 1], Some(&[false, true])).unwrap();
        assert_eq!(result[0], 0.5);
        assert_eq!(result[1], 3.0);
        let zero = summarize(&values, &[0.0, 5.0], &[1, 0], Some(&[false, false])).unwrap();
        assert_eq!(zero[0], 0.5); // first selected row wins an all-zero tie
        assert_eq!(zero[1], 0.0);
        assert!(summarize(&values, &[0.0, 5.0], &[2], None).is_err());
    }

    #[test]
    fn ordered_batches_preserve_bits_and_first_error() {
        let points = 1024;
        let mut values = vec![0.0; points * 20 * NATIONAL_OUTPUT_FIELDS];
        for (i, row) in values
            .as_chunks_mut::<NATIONAL_OUTPUT_FIELDS>()
            .0
            .iter_mut()
            .enumerate()
        {
            for (j, field) in row.iter_mut().enumerate() {
                *field = ((i * 13 + j * 7) % 97) as f32 / 97.0;
            }
            row[0] = f32::from(i % 7 != 0);
        }
        let offsets: Vec<_> = (0..20).map(|i| i as f64 * 5.0).collect();
        let serial = summarize_many(&values, points, &offsets, false).unwrap();
        for threads in [1, 4] {
            let pool = rayon::ThreadPoolBuilder::new()
                .num_threads(threads)
                .build()
                .unwrap();
            let parallel = pool
                .install(|| summarize_many(&values, points, &offsets, true))
                .unwrap();
            for (a, b) in serial.iter().flatten().zip(parallel.iter().flatten()) {
                assert_eq!(a.to_bits(), b.to_bits());
            }
        }
        for point in [7, 29] {
            values[point * 20 * NATIONAL_OUTPUT_FIELDS + 5] = f32::NAN;
        }
        assert!(summarize_many(&values, points, &offsets, true)
            .unwrap_err()
            .starts_with("event 7:"));
        assert!(summarize_many(&values, points, &offsets, false)
            .unwrap_err()
            .starts_with("event 7:"));
    }
}
