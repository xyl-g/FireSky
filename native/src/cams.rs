//! Compact production CAMS optics, not the expanded diagnostic catalog.

use std::slice;

pub const LEVELS: [i32; 11] = [1000, 950, 925, 900, 850, 800, 700, 600, 500, 400, 300];
pub const MAX_SOURCE_CELLS: usize = 5_000_000;
pub const MAX_TARGET_CELLS: usize = 500_000;

pub struct Grid<'a> {
    pub latitudes: &'a [f64],
    pub longitudes: &'a [f64],
    pub aod: &'a [f64],
    /// Pressure-major, in LEVELS order.
    pub extinction: &'a [f64],
}

pub struct Mapped {
    pub aod: Vec<f32>,
    /// Cell-major, in LEVELS order.
    pub profiles: Vec<[f32; 11]>,
    pub covered: Vec<bool>,
}

fn longitude(value: f64) -> f64 {
    (value + 180.0).rem_euclid(360.0) - 180.0
}

fn axis(values: &[f64]) -> Vec<f64> {
    let mut result = values.to_vec();
    result.sort_unstable_by(f64::total_cmp);
    result.dedup_by(|a, b| *a == *b);
    result
}

fn bounds(axis: &[f64], target: f64) -> (usize, usize, f64) {
    let upper = if target.is_nan() {
        axis.len() - 1
    } else {
        axis.partition_point(|&v| v < target).min(axis.len() - 1)
    };
    let lower = upper.saturating_sub(1);
    let denominator = axis[upper] - axis[lower];
    let weight = if denominator > 0.0 {
        ((target - axis[lower]) / denominator).clamp(0.0, 1.0)
    } else {
        0.0
    };
    (lower, upper, weight)
}

pub fn map(grid: &Grid<'_>, target_lats: &[f64], target_lons: &[f64]) -> Result<Mapped, String> {
    let cells = grid.latitudes.len();
    if cells == 0
        || cells > MAX_SOURCE_CELLS
        || grid.longitudes.len() != cells
        || grid.aod.len() != cells
        || grid.extinction.len() != cells * LEVELS.len()
        || target_lats.len() != target_lons.len()
        || target_lats.len() > MAX_TARGET_CELLS
        || grid
            .latitudes
            .iter()
            .chain(grid.longitudes)
            .any(|v| !v.is_finite())
    {
        return Err("invalid CAMS grid dimensions, coordinates or cell limit".into());
    }
    let lons: Vec<_> = grid.longitudes.iter().copied().map(longitude).collect();
    let lat_axis = axis(grid.latitudes);
    let lon_axis = axis(&lons);
    let dense_cells = lat_axis
        .len()
        .checked_mul(lon_axis.len())
        .ok_or("CAMS grid overflow")?;
    if dense_cells > MAX_SOURCE_CELLS {
        return Err("CAMS dense sampling grid exceeds cell limit".into());
    }
    // One index grid replaces twelve floating-point matrices. Last duplicates
    // win and holes remain missing, matching the reference matrix assignment.
    let mut indices = vec![usize::MAX; dense_cells];
    for (index, (&lat, &lon)) in grid.latitudes.iter().zip(&lons).enumerate() {
        let row = lat_axis.partition_point(|&v| v < lat);
        let col = lon_axis.partition_point(|&v| v < lon);
        indices[row * lon_axis.len() + col] = index;
    }
    let mut output = Mapped {
        aod: Vec::with_capacity(target_lats.len()),
        profiles: Vec::with_capacity(target_lats.len()),
        covered: Vec::with_capacity(target_lats.len()),
    };
    for (&lat, &raw_lon) in target_lats.iter().zip(target_lons) {
        let lon = longitude(raw_lon);
        let (a, b, y) = bounds(&lat_axis, lat);
        let (c, d, x) = bounds(&lon_axis, lon);
        let inside = lat >= lat_axis[0]
            && lat <= lat_axis[lat_axis.len() - 1]
            && lon >= lon_axis[0]
            && lon <= lon_axis[lon_axis.len() - 1];
        let corners = [
            indices[a * lon_axis.len() + c],
            indices[a * lon_axis.len() + d],
            indices[b * lon_axis.len() + c],
            indices[b * lon_axis.len() + d],
        ];
        let nearest =
            indices[if y <= 0.5 { a } else { b } * lon_axis.len() + if x <= 0.5 { c } else { d }];
        let mut covered = inside;
        let mut mapped = [0.0f32; 12];
        for (channel, result) in mapped.iter_mut().enumerate() {
            let values = if channel == 0 {
                grid.aod
            } else {
                &grid.extinction[(channel - 1) * cells..channel * cells]
            };
            let value = |i| if i == usize::MAX { f64::NAN } else { values[i] };
            let q = corners.map(value);
            let bilinear = q[0] * (1.0 - y) * (1.0 - x)
                + q[1] * (1.0 - y) * x
                + q[2] * y * (1.0 - x)
                + q[3] * y * x;
            let sampled = if bilinear.is_finite() {
                bilinear
            } else {
                value(nearest)
            };
            covered &= sampled.is_finite();
            *result = sampled as f32;
        }
        covered &= mapped[0] >= 0.0 && mapped[0] <= 10.0 && mapped[1..].iter().all(|&v| v >= 0.0);
        output.aod.push(mapped[0]);
        output.profiles.push(mapped[1..].try_into().unwrap());
        output.covered.push(covered);
    }
    Ok(output)
}

pub fn blend(left: &[f32], right: &[f32], weight: f32) -> Result<Vec<f32>, String> {
    if left.len() != right.len()
        || left.len() > MAX_TARGET_CELLS * 12
        || !weight.is_finite()
        || !(0.0..=1.0).contains(&weight)
    {
        return Err("invalid CAMS temporal interpolation dimensions or weight".into());
    }
    Ok(left
        .iter()
        .zip(right)
        .map(|(&a, &b)| a + (b - a) * weight)
        .collect())
}

pub fn scale_heights(values: &[f64], cells: usize) -> Result<Vec<f64>, String> {
    if cells > MAX_TARGET_CELLS || values.len() != cells * LEVELS.len() {
        return Err("invalid CAMS scale-height dimensions".into());
    }
    let heights = LEVELS.map(|p| 44.330 * (1.0 - (p as f64 / 1013.25).powf(0.1903)));
    let mut output = Vec::with_capacity(cells);
    for cell in 0..cells {
        let alpha = std::array::from_fn::<_, 11, _>(|level| {
            let v = values[level * cells + cell];
            if v.is_finite() && v > 1e-9 && v < 0.02 {
                v
            } else {
                0.0
            }
        });
        let maximum = alpha.iter().copied().fold(0.0f64, f64::max);
        let mut weights = [0.0; 11];
        let mut logs = [0.0; 11];
        let mut count = 0;
        for level in 0..11 {
            if alpha[level] > 0.0 && heights[level] <= 4.5 && alpha[level] >= maximum * 0.02 {
                weights[level] = (alpha[level] / maximum).sqrt();
                logs[level] = alpha[level].ln();
                count += 1;
            }
        }
        let sum: f64 = weights.iter().sum();
        let mut mean_height = 0.0;
        let mut mean_log = 0.0;
        if sum > 0.0 {
            mean_height = weights.iter().zip(heights).map(|(w, h)| w * h).sum::<f64>() / sum;
            mean_log = weights.iter().zip(logs).map(|(w, a)| w * a).sum::<f64>() / sum;
        }
        let mut numerator = 0.0;
        let mut denominator = 0.0;
        for level in 0..11 {
            let h = heights[level] - mean_height;
            numerator += weights[level] * h * (logs[level] - mean_log);
            denominator += weights[level] * h * h;
        }
        let slope = if denominator > 1e-8 {
            numerator / denominator
        } else {
            0.0
        };
        output.push(if count >= 3 && slope < -0.05 {
            (-1.0 / slope).clamp(0.35, 4.0)
        } else {
            1.2
        });
    }
    Ok(output)
}

/// # Safety
/// Pointers must reference nonoverlapping buffers of the documented extents.
#[no_mangle]
pub unsafe extern "C" fn firesky_cams_map_v1(
    lats: *const f64,
    lons: *const f64,
    aod: *const f64,
    extinction: *const f64,
    sources: usize,
    target_lats: *const f64,
    target_lons: *const f64,
    targets: usize,
    out_aod: *mut f32,
    out_profiles: *mut f32,
    out_covered: *mut u8,
) -> i32 {
    if sources == 0 || sources > MAX_SOURCE_CELLS || targets > MAX_TARGET_CELLS {
        return -2;
    }
    if [lats, lons, aod, extinction, target_lats, target_lons]
        .iter()
        .any(|p| p.is_null())
        || out_aod.is_null()
        || out_profiles.is_null()
        || out_covered.is_null()
    {
        return -1;
    }
    let grid = Grid {
        latitudes: slice::from_raw_parts(lats, sources),
        longitudes: slice::from_raw_parts(lons, sources),
        aod: slice::from_raw_parts(aod, sources),
        extinction: slice::from_raw_parts(extinction, sources * 11),
    };
    let Ok(mapped) = map(
        &grid,
        slice::from_raw_parts(target_lats, targets),
        slice::from_raw_parts(target_lons, targets),
    ) else {
        return -3;
    };
    slice::from_raw_parts_mut(out_aod, targets).copy_from_slice(&mapped.aod);
    for (dst, src) in slice::from_raw_parts_mut(out_profiles, targets * 11)
        .chunks_exact_mut(11)
        .zip(mapped.profiles)
    {
        dst.copy_from_slice(&src);
    }
    for (dst, src) in slice::from_raw_parts_mut(out_covered, targets)
        .iter_mut()
        .zip(mapped.covered)
    {
        *dst = u8::from(src);
    }
    0
}

/// # Safety
/// Inputs and output must reference nonoverlapping buffers of `count` floats.
#[no_mangle]
pub unsafe extern "C" fn firesky_cams_blend_v1(
    left: *const f32,
    right: *const f32,
    count: usize,
    weight: f32,
    output: *mut f32,
) -> i32 {
    if count > MAX_TARGET_CELLS * 12 {
        return -2;
    }
    if left.is_null() || right.is_null() || output.is_null() {
        return -1;
    }
    let Ok(values) = blend(
        slice::from_raw_parts(left, count),
        slice::from_raw_parts(right, count),
        weight,
    ) else {
        return -3;
    };
    slice::from_raw_parts_mut(output, count).copy_from_slice(&values);
    0
}

/// # Safety
/// Input holds 11*cells doubles, output holds cells nonoverlapping doubles.
#[no_mangle]
pub unsafe extern "C" fn firesky_cams_scale_heights_v1(
    values: *const f64,
    cells: usize,
    output: *mut f64,
) -> i32 {
    if cells > MAX_TARGET_CELLS {
        return -2;
    }
    if values.is_null() || output.is_null() {
        return -1;
    }
    let Ok(values) = scale_heights(slice::from_raw_parts(values, cells * 11), cells) else {
        return -3;
    };
    slice::from_raw_parts_mut(output, cells).copy_from_slice(&values);
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn mapping_bounds_holes_and_duplicate_last_wins() {
        let ext = [0.001; 55];
        let grid = Grid {
            latitudes: &[0., 0., 1., 1., 0.],
            longitudes: &[0., 1., 0., 1., 0.],
            aod: &[99., 2., 3., 4., 1.],
            extinction: &ext,
        };
        let mapped = map(&grid, &[0.5, 2., f64::NAN], &[0.5, 0.5, 0.5]).unwrap();
        assert_eq!(mapped.aod[0], 2.5);
        assert_eq!(mapped.covered, [true, false, false]);
        let hole = Grid {
            latitudes: &[0., 0., 1.],
            longitudes: &[0., 1., 0.],
            aod: &[1., 2., 3.],
            extinction: &[0.001; 33],
        };
        let mapped = map(&hole, &[0.4, 0.6], &[0.4, 0.6]).unwrap();
        assert_eq!(mapped.aod[0], 1.);
        assert!(mapped.covered[0]);
        assert!(mapped.aod[1].is_nan());
        assert!(!mapped.covered[1]);
    }
    #[test]
    fn temporal_preserves_f32_arithmetic_and_nonfinite_behavior() {
        assert_eq!(
            blend(&[0.1], &[0.3], 0.5).unwrap(),
            [0.1f32 + (0.3f32 - 0.1f32) * 0.5]
        );
        assert!(blend(&[f32::INFINITY], &[f32::INFINITY], 0.).unwrap()[0].is_nan());
        assert!(blend(&[], &[], f32::NAN).is_err());
    }
    #[test]
    fn scale_height_recovers_exponential_and_defaults_invalid_profiles() {
        let values: Vec<_> = LEVELS
            .iter()
            .map(|&p| {
                let height = 44.330 * (1.0 - (p as f64 / 1013.25).powf(0.1903));
                0.001 * (-height / 1.7).exp()
            })
            .collect();
        assert!((scale_heights(&values, 1).unwrap()[0] - 1.7).abs() < 1e-12);
        assert_eq!(scale_heights(&[f64::NAN; 11], 1).unwrap(), [1.2]);
    }
}
