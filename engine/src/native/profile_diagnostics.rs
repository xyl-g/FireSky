//! Selected observer-column diagnostics, separate from raytraced cloud targets.

use crate::native::{cloud, cloud_structure};
use rayon::prelude::*;

pub const FIELDS: [&str; 7] = [
    "cloud_material_class_code",
    "liquid_condensate_kg_kg",
    "ice_condensate_kg_kg",
    "deep_cloud_risk",
    "vertical_support_count",
    "vertical_uncertainty_m",
    "vertical_confidence",
];

#[derive(Clone, Copy, Debug)]
pub struct Stencil {
    cells: [usize; 4],
    weights: [f32; 4],
    one_hot: Option<usize>,
}

impl Stencil {
    pub fn cells(&self) -> [usize; 4] {
        self.cells
    }

    pub fn remapped(mut self, mut map: impl FnMut(usize) -> usize) -> Self {
        self.cells = self.cells.map(&mut map);
        self
    }

    pub fn new(
        point: [f32; 2],
        origin: [f32; 2],
        spacing: f32,
        rows: usize,
        cols: usize,
    ) -> Result<Self, String> {
        if rows == 0
            || cols == 0
            || rows.checked_mul(cols).is_none_or(|cells| cells > 500_000)
            || !spacing.is_finite()
            || spacing <= 0.0
            || point.iter().chain(&origin).any(|v| !v.is_finite())
        {
            return Err("invalid selected-column grid geometry".into());
        }
        let row = (point[0] - origin[0]) / spacing;
        let max_lon = origin[1] + (cols - 1) as f32 * spacing;
        let lon =
            if origin[1] < -180.0 || max_lon > 180.0 || (max_lon == 180.0 && origin[1] > -180.0) {
                let center = origin[1] + (max_lon - origin[1]) * 0.5;
                point[1] + ((center - point[1]) / 360.0).round() * 360.0
            } else if point[1] == 180.0 && origin[1] == -180.0 && max_lon < 180.0 {
                -180.0
            } else {
                point[1]
            };
        let col = (lon - origin[1]) / spacing;
        if !row.is_finite() || !col.is_finite() {
            return Err("selected-column grid position overflow".into());
        }
        let r0 = row.floor().clamp(0.0, (rows - 1) as f32) as usize;
        let c0 = col.floor().clamp(0.0, (cols - 1) as f32) as usize;
        let r1 = (r0 + 1).min(rows - 1);
        let c1 = (c0 + 1).min(cols - 1);
        let rw = (row - r0 as f32).clamp(0.0, 1.0);
        let cw = (col - c0 as f32).clamp(0.0, 1.0);
        let weights = [
            (1.0 - rw) * (1.0 - cw),
            (1.0 - rw) * cw,
            rw * (1.0 - cw),
            rw * cw,
        ];
        let one_hot = weights.iter().position(|&v| v == 1.0).filter(|_| {
            weights.iter().filter(|&&v| v == 1.0).count() == 1
                && weights.iter().all(|&v| v == 0.0 || v == 1.0)
        });
        Ok(Self {
            cells: [
                r0 * cols + c0,
                r0 * cols + c1,
                r1 * cols + c0,
                r1 * cols + c1,
            ],
            weights,
            one_hot,
        })
    }

    /// Temporal finite fallback precedes horizontal finite-weight normalization.
    /// `fetch` returns a field from one of the two caller-selected time frames.
    pub fn sample_with(
        &self,
        weight: f32,
        output: &mut [f32],
        mut fetch: impl FnMut(bool, usize, usize) -> f32,
    ) -> Result<(), String> {
        if !weight.is_finite() {
            return Err("nonfinite selected-column time weight".into());
        }
        let weight = weight.clamp(0.0, 1.0);
        let mut temporal = |cell, field| {
            let a = fetch(false, cell, field);
            let b = fetch(true, cell, field);
            if a.is_finite() && b.is_finite() {
                a + (b - a) * weight
            } else if a.is_finite() {
                a
            } else {
                b
            }
        };
        for (field, value) in output.iter_mut().enumerate() {
            if let Some(corner) = self.one_hot {
                let v = temporal(self.cells[corner], field);
                *value = if v.is_finite() { v } else { 0.0 };
            } else {
                let mut numerator = 0.0_f32;
                let mut denominator = 0.0_f32;
                for (&cell, &w) in self.cells.iter().zip(&self.weights) {
                    let v = temporal(cell, field);
                    // Keep the four-term float32 reduction order, including zeros.
                    numerator += if v.is_finite() { v } else { 0.0 } * w;
                    denominator += if v.is_finite() { w } else { 0.0 };
                }
                *value = if denominator > 0.0 {
                    numerator / denominator
                } else {
                    0.0
                };
            }
        }
        Ok(())
    }

    /// Missing remote terrain must not be hidden by the sampler's zero fallback.
    pub fn has_finite_support(
        &self,
        levels: usize,
        mut fetch: impl FnMut(bool, usize, usize) -> f32,
    ) -> bool {
        self.cells.iter().zip(self.weights).any(|(&cell, weight)| {
            weight > 0.0
                && (0..levels).any(|level| {
                    fetch(false, cell, level).is_finite() || fetch(true, cell, level).is_finite()
                })
        })
    }
}

fn clean(v: f32) -> f32 {
    if v.is_nan() {
        0.0
    } else {
        v.clamp(f32::MIN, f32::MAX)
    }
}

fn gate(v: f32, low: f32, high: f32) -> f32 {
    let x = ((clean(v) - low) / (high - low).max(1e-6)).clamp(0.0, 1.0);
    x * x * (3.0 - 2.0 * x)
}

fn aggregate_risk(amount: &[f32], levels: &[f32], profile: &[[f32; 24]], a: [f32; 7]) -> f32 {
    let mut high = 0.0_f32;
    let mut middle = 0.0_f32;
    let mut high_fraction = f32::NEG_INFINITY;
    let mut middle_fraction = f32::NEG_INFINITY;
    for ((&amount, &level), row) in amount.iter().zip(levels).zip(profile) {
        let amount = clean(amount).clamp(0.0, 1.0);
        if level <= 400.0 {
            high = high.max(amount);
        } else if level <= 700.0 {
            middle = middle.max(amount);
        }
        // f32::max ignores NaN, like nanmax (all-NaN is cleaned below).
        high_fraction = high_fraction.max(row[7]);
        middle_fraction = middle_fraction.max(row[15]);
    }
    let high_fraction = (clean(high_fraction) / 100.0).clamp(0.0, 1.0);
    let middle_fraction = (clean(middle_fraction) / 100.0).clamp(0.0, 1.0);
    if high_fraction < 0.60
        || middle_fraction < 0.60
        || high <= 0.005
        || middle <= 0.005
        || !a[1..5].iter().all(|v| v.is_finite())
        || a[2] <= a[1]
        || a[4] <= a[3]
    {
        return 0.0;
    }
    let missing = ((middle_fraction - middle).max(0.0) / (1.0 - middle).max(1e-6)).clamp(0.0, 1.0);
    (middle_fraction
        * missing
        * gate((a[4] - a[3]).max(0.0), 1500.0, 5000.0)
        * gate(clean(a[0]).max(0.0) * 3600.0, 0.10, 1.00))
    .clamp(0.0, 1.0)
}

fn level_depth(level: f32) -> f32 {
    match level.round_ties_even() as i32 {
        200 => 2200.0,
        250 => 2000.0,
        300 => 1800.0,
        400 => 1500.0,
        500 => 1200.0,
        600 => 900.0,
        700 => 700.0,
        750 => 550.0,
        800 => 500.0,
        850 => 450.0,
        925 => 300.0,
        _ => 700.0,
    }
}

/// Aggregate fields: precipitation, high base/top, middle base/top, low base/top.
/// The returned confidence is metadata only, never a quality multiplier.
pub fn diagnose(
    profile: &[[f32; 24]],
    levels: &[f32],
    aggregate: [f32; 7],
    average_envelope: bool,
) -> Result<[f32; 7], String> {
    diagnose_with(
        profile,
        levels,
        aggregate,
        average_envelope,
        &mut Scratch::new(levels.len()),
    )
}

struct Scratch {
    amounts: Vec<f32>,
    rows: Vec<[f32; 6]>,
}

impl Scratch {
    fn new(levels: usize) -> Self {
        Self {
            amounts: Vec::with_capacity(levels.min(256)),
            rows: Vec::with_capacity(levels.min(256)),
        }
    }
}

fn diagnose_with(
    profile: &[[f32; 24]],
    levels: &[f32],
    aggregate: [f32; 7],
    average_envelope: bool,
    scratch: &mut Scratch,
) -> Result<[f32; 7], String> {
    if profile.len() != levels.len()
        || levels.is_empty()
        || levels.len() > cloud_structure::MAX_LEVELS
        || levels.iter().any(|v| !v.is_finite())
    {
        return Err("invalid selected-column pressure profile".into());
    }
    let Scratch { amounts, rows } = scratch;
    amounts.clear();
    rows.clear();
    let mut liquid = 0.0_f32;
    let mut ice = 0.0_f32;
    let mut count = 0;
    let mut active_depth = 0.0_f32;
    let mut peak = 0.0_f32;
    for (p, &level) in profile.iter().zip(levels) {
        let effective = cloud::effective_profile(p);
        let amount = effective[0];
        amounts.push(amount);
        rows.push([amount, p[1], p[11], p[12], p[3], effective[4]]);
        if amount >= 0.35 {
            liquid = liquid.max(clean(p[11]).max(0.0));
            ice = ice.max(clean(p[12]).max(0.0));
        }
        if amount >= 0.005 && p[1].is_finite() {
            count += 1;
            active_depth = active_depth.max(level_depth(level));
            peak = peak.max(amount.clamp(0.0, 1.0));
        }
    }
    let risk = aggregate_risk(amounts, levels, profile, aggregate);
    let structure = cloud_structure::classify(rows, levels, Some(risk), None, false)?;
    let boundary = aggregate[1..].iter().any(|&v| v.is_finite() && v > 0.0);
    let mut uncertainty = match count {
        0 => 0.0,
        1 => active_depth,
        _ => 0.5 * active_depth,
    };
    let mut support: f64 = match count {
        0 => 1.0,
        1 => 0.55,
        2 => 0.75,
        _ => 0.90,
    };
    if boundary {
        uncertainty *= if average_envelope { 0.75 } else { 0.5 };
        support = if average_envelope {
            0.75
        } else {
            support.max(0.85)
        };
    }
    let amount_confidence = 0.85_f32 + 0.15_f32 * peak;
    Ok([
        structure[5],
        liquid,
        ice,
        structure[3].clamp(0.0, 1.0),
        count as f32,
        uncertainty,
        (support * f64::from(amount_confidence)).clamp(0.0, 1.0) as f32,
    ])
}

#[derive(Clone, Copy)]
pub struct SelectedSample {
    pub stencil: Stencil,
    pub time_weight: f32,
}

pub struct FramePair<'a> {
    pub profiles: [&'a [f32]; 2],
    pub columns: [&'a [[f32; 36]]; 2],
    pub levels: &'a [f32],
    pub average_envelope: bool,
}

impl FramePair<'_> {
    pub fn diagnose_many(
        &self,
        samples: &[SelectedSample],
        parallel: bool,
    ) -> Result<Vec<[f32; 7]>, String> {
        let cells = self.columns[0].len();
        let levels = self.levels.len();
        if cells == 0
            || cells > 500_000
            || levels == 0
            || levels > 256
            || self.columns[1].len() != cells
            || self.profiles.iter().any(|v| v.len() != cells * levels * 24)
            || samples.len() > 10_000_000
            || samples
                .iter()
                .any(|s| s.stencil.cells.iter().any(|&c| c >= cells))
        {
            return Err("invalid selected-column frame pair".into());
        }
        let initialize = || (vec![[0.0; 24]; levels], Scratch::new(levels));
        let calculate = |scratch: &mut (Vec<[f32; 24]>, Scratch), sample: &SelectedSample| {
            let (profile, diagnostic_scratch) = scratch;
            sample.stencil.sample_with(
                sample.time_weight,
                profile.as_flattened_mut(),
                |second, cell, field| {
                    self.profiles[usize::from(second)][cell * levels * 24 + field]
                },
            )?;
            let mut aggregate = [0.0; 7];
            sample.stencil.sample_with(
                sample.time_weight,
                &mut aggregate,
                |second, cell, field| {
                    self.columns[usize::from(second)][cell][[14, 17, 18, 19, 20, 21, 22][field]]
                },
            )?;
            diagnose_with(
                profile,
                self.levels,
                aggregate,
                self.average_envelope,
                diagnostic_scratch,
            )
        };
        // Indexed collection preserves the first failing input even in parallel.
        let rows: Vec<_> = if parallel && samples.len() >= 1024 {
            samples
                .par_iter()
                .with_min_len(64)
                .map_init(initialize, calculate)
                .collect()
        } else {
            let mut scratch = initialize();
            samples
                .iter()
                .map(|sample| calculate(&mut scratch, sample))
                .collect()
        };
        rows.into_iter()
            .enumerate()
            .map(|(index, result)| result.map_err(|e| format!("selected sample {index}: {e}")))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unwrapped_longitude_axis_samples_equivalent_dateline_points_identically() {
        for origin in [179.0, -181.0] {
            for (positive, negative) in [(179.0, -181.0), (180.0, -180.0), (181.0, -179.0)] {
                let a = Stencil::new([0.25, positive], [0.0, origin], 1.0, 2, 3).unwrap();
                let b = Stencil::new([0.25, negative], [0.0, origin], 1.0, 2, 3).unwrap();
                assert_eq!(a.cells, b.cells);
                assert_eq!(a.weights.map(f32::to_bits), b.weights.map(f32::to_bits));
                let mut actual = [0.0];
                a.sample_with(0.0, &mut actual, |_, cell, _| cell as f32)
                    .unwrap();
                assert_eq!(actual, [positive - 179.0 + 0.75]);
            }
        }
    }

    #[test]
    fn ordinary_longitude_axes_keep_wide_domains_and_outside_clamping() {
        let east = Stencil::new([0.0, 179.0], [0.0, -179.0], 1.0, 1, 359).unwrap();
        assert_eq!(east.cells, [358; 4]);
        assert_eq!(east.weights, [1.0, 0.0, 0.0, 0.0]);
        let outside = Stencil::new([0.0, -181.0], [0.0, -179.0], 1.0, 1, 359).unwrap();
        assert_eq!(outside.cells, [0, 1, 0, 1]);
        assert_eq!(outside.weights, [1.0, 0.0, 0.0, 0.0]);
        let far = Stencil::new([0.5, -179.0], [0.0, 100.0], 1.0, 2, 3).unwrap();
        assert_eq!(far.cells, [0, 1, 3, 4]);
        assert_eq!(far.weights, [0.5, 0.0, 0.5, 0.0]);
    }

    #[test]
    fn exact_dateline_endpoints_share_the_same_column() {
        for (origin, expected) in [(179.0, 1.0), (-180.0, 0.0)] {
            let a = Stencil::new([0.0, 180.0], [0.0, origin], 1.0, 1, 2).unwrap();
            let b = Stencil::new([0.0, -180.0], [0.0, origin], 1.0, 1, 2).unwrap();
            assert_eq!(a.cells, b.cells);
            assert_eq!(a.weights.map(f32::to_bits), b.weights.map(f32::to_bits));
            let mut value = [0.0];
            a.sample_with(0.0, &mut value, |_, cell, _| cell as f32)
                .unwrap();
            assert_eq!(value, [expected]);
        }
        let ordinary_outside = Stencil::new([0.0, 181.0], [0.0, -180.0], 1.0, 1, 2).unwrap();
        assert_eq!(ordinary_outside.cells, [1; 4]);
        assert_eq!(ordinary_outside.weights, [0.0, 1.0, 0.0, 0.0]);
    }

    #[test]
    fn temporal_fallback_is_before_horizontal_normalization() {
        let stencil = Stencil::new([0.5, 0.5], [0.0, 0.0], 1.0, 2, 2).unwrap();
        let frames = [
            [1.0, f32::NAN, 3.0, f32::NAN],
            [f32::NAN, 6.0, 5.0, f32::NAN],
        ];
        let mut out = [0.0];
        stencil
            .sample_with(0.5, &mut out, |right, cell, _| {
                frames[usize::from(right)][cell]
            })
            .unwrap();
        assert_eq!(out, [11.0 / 3.0]);
        let one = Stencil::new([-1.0, -1.0], [0.0, 0.0], 1.0, 2, 2).unwrap();
        one.sample_with(0.5, &mut out, |_, _, _| f32::NAN).unwrap();
        assert_eq!(out, [0.0]);
    }

    #[test]
    fn remapped_stencil_preserves_weights_and_terrain_support() {
        let stencil = Stencil::new([0.5, 0.5], [0.0, 0.0], 1.0, 2, 3).unwrap();
        assert_eq!(stencil.cells(), [0, 1, 3, 4]);
        let mapped = stencil.remapped(|cell| [0, 1, 99, 2, 3][cell]);
        assert_eq!(mapped.cells(), [0, 1, 2, 3]);
        let mut a = [0.0];
        let mut b = [0.0];
        stencil
            .sample_with(0.25, &mut a, |_, cell, _| cell as f32)
            .unwrap();
        mapped
            .sample_with(0.25, &mut b, |_, cell, _| [0., 1., 3., 4.][cell])
            .unwrap();
        assert_eq!(a, b);
        assert!(!mapped.has_finite_support(3, |_, _, _| f32::NAN));
        assert!(mapped.has_finite_support(3, |right, cell, level| {
            if right && cell == 3 && level == 2 {
                5.0
            } else {
                f32::NAN
            }
        }));
        let origin = Stencil::new([0., 0.], [0., 0.], 1., 2, 3).unwrap();
        assert!(!origin.has_finite_support(1, |_, cell, _| if cell == 0 { f32::NAN } else { 5. }));
    }

    #[test]
    fn overflow_and_missing_endpoint_keep_the_reference_fallback_order() {
        let mut out = [0.0];
        let one = Stencil::new([0.0, 0.0], [0.0, 0.0], 1.0, 2, 2).unwrap();
        one.sample_with(
            0.0,
            &mut out,
            |right, _, _| {
                if right {
                    f32::MAX
                } else {
                    -f32::MAX
                }
            },
        )
        .unwrap();
        assert_eq!(out, [0.0]); // Both finite, but even the zero-weight blend overflows.
        one.sample_with(
            0.0,
            &mut out,
            |right, _, _| {
                if right {
                    8.0
                } else {
                    f32::NAN
                }
            },
        )
        .unwrap();
        assert_eq!(out, [8.0]); // Finite fallback is independent of time weight.
        let off = Stencil::new([0.5, 0.5], [0.0, 0.0], 1.0, 2, 2).unwrap();
        let frames = [
            [-f32::MAX, 2.0, f32::NAN, f32::NAN],
            [f32::MAX, 4.0, 8.0, f32::NAN],
        ];
        off.sample_with(0.5, &mut out, |right, cell, _| {
            frames[usize::from(right)][cell]
        })
        .unwrap();
        assert_eq!(out, [5.5]);
        assert!(Stencil::new([0.0, 0.0], [0.0, 0.0], 1.0, usize::MAX, 1).is_err());
    }

    #[test]
    fn envelope_changes_geometry_not_material() {
        let mut p = [0.0; 24];
        p[1] = 8000.0;
        p[6] = 60.0;
        p[13] = 0.6;
        p[14] = 300.0;
        let a = [0.0, 7000.0, 9000.0, 0.0, 0.0, 0.0, 0.0];
        let instant = diagnose(&[p], &[300.0], a, false).unwrap();
        let average = diagnose(&[p], &[300.0], a, true).unwrap();
        assert_eq!(instant[..5], average[..5]);
        assert_eq!(instant[5], 900.0);
        assert_eq!(average[5], 1350.0);
        assert!(instant[6] > average[6]);
    }

    #[test]
    fn parallel_pair_preserves_every_bit_and_first_error() {
        let levels = [200.0, 300.0, 500.0, 850.0];
        let profiles: Vec<_> = (0..4 * levels.len() * 24)
            .map(|i| match i % 24 {
                1 => 1000.0 + (i % 11000) as f32,
                6 | 7 | 15 => (i % 101) as f32,
                11 | 12 => (i % 11) as f32 * 1e-6,
                14 => levels[(i / 24) % levels.len()],
                _ => (i % 13) as f32 * 0.1,
            })
            .collect();
        let columns = [[0.0; 36]; 4];
        let pair = FramePair {
            profiles: [&profiles, &profiles],
            columns: [&columns, &columns],
            levels: &levels,
            average_envelope: false,
        };
        let mut samples: Vec<_> = (0..2049)
            .map(|i| SelectedSample {
                stencil: Stencil::new(
                    [(i % 17) as f32 / 17.0, (i % 19) as f32 / 19.0],
                    [0.0, 0.0],
                    1.0,
                    2,
                    2,
                )
                .unwrap(),
                time_weight: (i % 23) as f32 / 23.0,
            })
            .collect();
        let serial = pair.diagnose_many(&samples, false).unwrap();
        for (sample, actual) in samples.iter().zip(&serial) {
            let mut profile = vec![[0.0; 24]; levels.len()];
            sample
                .stencil
                .sample_with(
                    sample.time_weight,
                    profile.as_flattened_mut(),
                    |right, cell, field| {
                        pair.profiles[usize::from(right)][cell * levels.len() * 24 + field]
                    },
                )
                .unwrap();
            let fresh = diagnose(&profile, &levels, [0.0; 7], false).unwrap();
            assert!(actual
                .iter()
                .zip(fresh)
                .all(|(a, b)| a.to_bits() == b.to_bits()));
        }
        for threads in [1, 4] {
            let pool = rayon::ThreadPoolBuilder::new()
                .num_threads(threads)
                .build()
                .unwrap();
            let parallel = pool.install(|| pair.diagnose_many(&samples, true)).unwrap();
            assert!(serial
                .iter()
                .flatten()
                .zip(parallel.iter().flatten())
                .all(|(a, b)| a.to_bits() == b.to_bits()));
            samples[7].time_weight = f32::NAN;
            samples[29].time_weight = f32::INFINITY;
            let serial_error = pair.diagnose_many(&samples, false).unwrap_err();
            assert!(serial_error.starts_with("selected sample 7:"));
            assert_eq!(
                pool.install(|| pair.diagnose_many(&samples, true))
                    .unwrap_err(),
                serial_error
            );
            samples[7].time_weight = 0.0;
            samples[29].time_weight = 0.0;
        }
    }
}
