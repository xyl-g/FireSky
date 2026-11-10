//! Expanded optical diagnostics. These quantities are not forecast scores.

use std::f64::consts::PI;
use std::slice;

pub const AOD_WAVELENGTHS: [i32; 5] = [355, 440, 550, 670, 865];
pub const EXTINCTION_WAVELENGTHS: [i32; 3] = [355, 532, 1064];
pub const LEVELS: [i32; 8] = [1000, 925, 850, 700, 600, 500, 400, 300];
pub const MAX_PROFILE_LEVELS: usize = 256;
pub const MAX_AOD_POINTS: usize = 1024;

pub fn normalize_longitude(value: f64) -> f64 {
    (value + 180.0).rem_euclid(360.0) - 180.0
}

pub fn pressure_height(pressure: f64) -> Result<f64, String> {
    if !(pressure > 0.0 && pressure <= 1013.25) {
        return Err("pressure must be within the standard-atmosphere range".into());
    }
    Ok(44330.0 * (1.0 - (pressure / 1013.25).powf(0.190263)))
}

pub fn interpolate_aod(points: &[(f64, f64)], wavelength: f64) -> Result<f64, String> {
    if !wavelength.is_finite() || wavelength <= 0.0 || points.len() > MAX_AOD_POINTS {
        return Err("wavelength must be finite and positive; AOD point limit is 1024".into());
    }
    let mut usable: Vec<_> = points
        .iter()
        .copied()
        .filter(|(_, v)| *v > 0.0 && v.is_finite())
        .collect();
    // Valid schema keys are positive integer wavelengths. Fail closed on
    // malformed keys rather than silently constructing an invalid log axis.
    if usable.iter().any(|(w, _)| !w.is_finite() || *w <= 0.0) {
        return Err("AOD wavelengths must be finite and positive".into());
    }
    usable.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.total_cmp(&b.1)));
    if usable.len() < 2 || wavelength < usable[0].0 || wavelength > usable[usable.len() - 1].0 {
        return Err("wavelength is outside the positive CAMS AOD range".into());
    }
    for pair in usable.windows(2) {
        let [(a, av), (b, bv)] = [pair[0], pair[1]];
        if a <= wavelength && wavelength <= b {
            if wavelength == a {
                return Ok(av);
            }
            if wavelength == b {
                return Ok(bv);
            }
            let fraction = (wavelength.ln() - a.ln()) / (b.ln() - a.ln());
            let result = (av.ln() + fraction * (bv.ln() - av.ln())).exp();
            if !result.is_finite() {
                return Err("spectral AOD interpolation overflow".into());
            }
            return Ok(result);
        }
    }
    Err("could not bracket wavelength".into())
}

pub fn angstrom(
    first: f64,
    second: f64,
    wavelength_a: f64,
    wavelength_b: f64,
) -> Result<f64, String> {
    if first <= 0.0 || second <= 0.0 || !(first + second).is_finite() {
        return Err("AOD values must be finite and positive".into());
    }
    if wavelength_b == 0.0 {
        return Err("Angstrom reference wavelength cannot be zero".into());
    }
    let ratio = first / second;
    let wave_ratio = wavelength_a / wavelength_b;
    if ratio <= 0.0 || wave_ratio <= 0.0 || wave_ratio.is_nan() || wave_ratio == 1.0 {
        return Err("invalid Angstrom logarithm or wavelength separation".into());
    }
    Ok(-ratio.ln() / wave_ratio.ln())
}

fn sorted_profile(values: &[f64], levels: &[f64]) -> Result<Vec<(f64, f64)>, String> {
    if values.len() != levels.len() || levels.len() > MAX_PROFILE_LEVELS {
        return Err(
            "extinction and pressure-level arrays must have matching bounded shapes".into(),
        );
    }
    let mut profile = Vec::with_capacity(levels.len());
    for (&value, &pressure) in values.iter().zip(levels) {
        let height = pressure_height(pressure)?;
        if value.is_finite() && value >= 0.0 {
            profile.push((height, value));
        }
    }
    profile.sort_by(|a, b| a.0.total_cmp(&b.0));
    Ok(profile)
}

fn trapezoid(profile: &[(f64, f64)]) -> f64 {
    profile
        .windows(2)
        .map(|p| (p[1].0 - p[0].0) * (p[1].1 + p[0].1) / 2.0)
        .sum()
}

pub fn integrate(values: &[f64], levels: &[f64]) -> Result<f64, String> {
    let profile = sorted_profile(values, levels)?;
    if profile.len() < 2 {
        return Err("extinction profile has fewer than two valid levels".into());
    }
    Ok(trapezoid(&profile))
}

pub struct MiddleLayer {
    pub count: usize,
    pub peak_km_inv: f64,
    pub optical_depth: f64,
    pub seasoning_fraction: f64,
    pub overloaded_fraction: f64,
}

fn interpolate_profile(profile: &[(f64, f64)], height: f64) -> f64 {
    let upper = profile.partition_point(|p| p.0 <= height);
    if upper == 0 {
        return profile[0].1;
    }
    if upper == profile.len() {
        return profile[upper - 1].1;
    }
    let (a, av) = profile[upper - 1];
    let (b, bv) = profile[upper];
    // np.interp computes slope first, rather than a normalized weight.
    av + (bv - av) / (b - a) * (height - a)
}

pub fn middle_layer(
    values: &[f64],
    levels: &[f64],
    lower: f64,
    upper: f64,
) -> Result<MiddleLayer, String> {
    if !(lower + upper).is_finite() || !(0.0 <= lower && lower < upper) {
        return Err("middle-layer height bounds are invalid".into());
    }
    let profile = sorted_profile(values, levels)?;
    let selected: Vec<_> = profile
        .iter()
        .filter(|p| p.0 >= lower && p.0 <= upper)
        .collect();
    if selected.len() < 2 {
        return Err("middle layer has fewer than two valid pressure levels".into());
    }
    if profile[0].0 > lower || profile[profile.len() - 1].0 < upper {
        return Err("extinction profile does not bracket the middle layer".into());
    }
    let mut integration = Vec::with_capacity(selected.len() + 2);
    integration.push((lower, interpolate_profile(&profile, lower)));
    for p in &selected {
        if p.0 > lower && p.0 < upper {
            integration.push(**p);
        }
    }
    integration.push((upper, interpolate_profile(&profile, upper)));
    let km_values: Vec<_> = selected.iter().map(|p| p.1 * 1000.0).collect();
    Ok(MiddleLayer {
        count: selected.len(),
        peak_km_inv: km_values.iter().copied().fold(0.0, f64::max),
        optical_depth: trapezoid(&integration),
        seasoning_fraction: km_values
            .iter()
            .filter(|&&v| (0.01..=0.02).contains(&v))
            .count() as f64
            / selected.len() as f64,
        overloaded_fraction: km_values.iter().filter(|&&v| v > 0.05).count() as f64
            / selected.len() as f64,
    })
}

pub fn relative_airmass(elevation: f64) -> f64 {
    if !elevation.is_finite() || elevation <= -5.0 {
        return f64::INFINITY;
    }
    let e = elevation.clamp(-5.0, 90.0);
    1.0 / (e.to_radians().sin() + 0.50572 * (e + 6.079).powf(-1.6364))
}

pub fn direct_transmission(aod: f64, elevation: f64) -> f64 {
    let depth = if aod > 0.0 { aod } else { 0.0 };
    let airmass = relative_airmass(elevation);
    if !airmass.is_finite() {
        return 0.0;
    }
    (-depth * airmass).exp()
}

pub fn hg_phase(g: f64, angle: f64) -> Result<f64, String> {
    if angle.is_infinite() {
        return Err("scattering angle cannot be infinite".into());
    }
    // Python min(0.999, NaN) retains its first operand.
    let g = if g < 0.999 { g } else { 0.999 };
    let g = if g > -0.999 { g } else { -0.999 };
    let cosine = angle.to_radians().cos();
    Ok((1.0 - g * g) / (4.0 * PI * (1.0 + g * g - 2.0 * g * cosine).powf(1.5)))
}

pub fn nearest(lats: &[f64], lons: &[f64], lat: f64, lon: f64) -> Result<usize, String> {
    if lats.is_empty() || lats.len() != lons.len() || lats.len() > crate::cams::MAX_SOURCE_CELLS {
        return Err("nearest CAMS point requires nonempty matching bounded coordinates".into());
    }
    let mut best = 0;
    let mut distance = f64::INFINITY;
    for (index, (&a, &b)) in lats.iter().zip(lons).enumerate() {
        let delta = normalize_longitude(b - lon);
        let candidate = (a - lat).abs() + delta.abs();
        if candidate.is_nan() {
            return Ok(index);
        }
        if candidate < distance {
            best = index;
            distance = candidate;
        }
    }
    Ok(best)
}

/// # Safety
/// `args` holds four doubles; `output` holds one nonoverlapping double.
#[no_mangle]
pub unsafe extern "C" fn firesky_spectral_scalar_v1(
    operation: u32,
    args: *const f64,
    output: *mut f64,
) -> i32 {
    if args.is_null() || output.is_null() {
        return -1;
    }
    let a = slice::from_raw_parts(args, 4);
    let result = match operation {
        0 => Ok(relative_airmass(a[0])),
        1 => Ok(direct_transmission(a[0], a[1])),
        2 => hg_phase(a[0], a[1]),
        3 => pressure_height(a[0]),
        4 => angstrom(a[0], a[1], a[2], a[3]),
        _ => return -2,
    };
    let Ok(value) = result else {
        return -3;
    };
    *output = value;
    0
}

/// # Safety
/// Input arrays have `count` doubles, output holds one nonoverlapping double.
#[no_mangle]
pub unsafe extern "C" fn firesky_spectral_aod_v1(
    wavelengths: *const f64,
    values: *const f64,
    count: usize,
    wavelength: f64,
    output: *mut f64,
) -> i32 {
    if count > MAX_AOD_POINTS {
        return -2;
    }
    if wavelengths.is_null() || values.is_null() || output.is_null() {
        return -1;
    }
    let points: Vec<_> = slice::from_raw_parts(wavelengths, count)
        .iter()
        .copied()
        .zip(slice::from_raw_parts(values, count).iter().copied())
        .collect();
    let Ok(value) = interpolate_aod(&points, wavelength) else {
        return -3;
    };
    *output = value;
    0
}

/// # Safety
/// Inputs have `count` doubles. Output has five nonoverlapping doubles.
#[no_mangle]
pub unsafe extern "C" fn firesky_spectral_profile_v1(
    values: *const f64,
    levels: *const f64,
    count: usize,
    mode: u32,
    lower: f64,
    upper: f64,
    output: *mut f64,
) -> i32 {
    if count > MAX_PROFILE_LEVELS || mode > 1 {
        return -2;
    }
    if values.is_null() || levels.is_null() || output.is_null() {
        return -1;
    }
    let values = slice::from_raw_parts(values, count);
    let levels = slice::from_raw_parts(levels, count);
    let output = slice::from_raw_parts_mut(output, 5);
    if mode == 0 {
        let Ok(value) = integrate(values, levels) else {
            return -3;
        };
        output[0] = value;
    } else {
        let Ok(v) = middle_layer(values, levels, lower, upper) else {
            return -3;
        };
        output.copy_from_slice(&[
            v.count as f64,
            v.peak_km_inv,
            v.optical_depth,
            v.seasoning_fraction,
            v.overloaded_fraction,
        ]);
    }
    0
}

/// # Safety
/// Coordinates hold `count` doubles; output points to one nonoverlapping usize.
#[no_mangle]
pub unsafe extern "C" fn firesky_spectral_nearest_v1(
    lats: *const f64,
    lons: *const f64,
    count: usize,
    lat: f64,
    lon: f64,
    output: *mut usize,
) -> i32 {
    if count == 0 || count > crate::cams::MAX_SOURCE_CELLS {
        return -2;
    }
    if lats.is_null() || lons.is_null() || output.is_null() {
        return -1;
    }
    let Ok(index) = nearest(
        slice::from_raw_parts(lats, count),
        slice::from_raw_parts(lons, count),
        lat,
        lon,
    ) else {
        return -3;
    };
    *output = index;
    0
}

/// # Safety
/// Input and output hold `count` nonoverlapping doubles.
#[no_mangle]
pub unsafe extern "C" fn firesky_spectral_normalize_v1(
    values: *const f64,
    count: usize,
    output: *mut f64,
) -> i32 {
    if count > crate::cams::MAX_SOURCE_CELLS {
        return -2;
    }
    if values.is_null() || output.is_null() {
        return -1;
    }
    for (dst, &src) in slice::from_raw_parts_mut(output, count)
        .iter_mut()
        .zip(slice::from_raw_parts(values, count))
    {
        *dst = normalize_longitude(src);
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn log_interpolation_and_phase_are_physical_not_scores() {
        let points = [(400., 0.4), (800., 0.2)];
        assert!((interpolate_aod(&points, 600.).unwrap() - 0.4 * 400. / 600.).abs() < 1e-14);
        assert!((angstrom(0.4, 0.2, 400., 800.).unwrap() - 1.).abs() < 1e-14);
        assert!(angstrom(0.4, 0.2, 400., 0.).is_err());
        assert!(interpolate_aod(&points, 300.).is_err());
        assert!((hg_phase(0., 45.).unwrap() - 1. / (4. * PI)).abs() < 1e-14);
        assert_eq!(direct_transmission(0.1, -5.), 0.);
        assert_eq!(direct_transmission(f64::NAN, 20.), 1.);
        assert_eq!(relative_airmass(f64::NAN), f64::INFINITY);
    }
    #[test]
    fn profile_boundaries_are_interpolated_not_clipped_to_levels() {
        let levels = LEVELS.map(f64::from);
        let values = [0.00002; 8];
        assert!(
            (middle_layer(&values, &levels, 2000., 8000.)
                .unwrap()
                .optical_depth
                - 0.12)
                .abs()
                < 1e-14
        );
        let expected = (pressure_height(300.).unwrap() - pressure_height(1000.).unwrap()) * 0.00002;
        assert!((integrate(&values, &levels).unwrap() - expected).abs() < 1e-14);
        assert!(middle_layer(&values[..4], &levels[..4], 2000., 8000.).is_err());
        assert!(integrate(&[1., 1.], &[1000., f64::NAN]).is_err());
    }
    #[test]
    fn nearest_preserves_scan_order_ties_dateline_and_nan_argmin() {
        assert_eq!(
            nearest(&[0., 0., 1.], &[179., -179., 0.], 0., 180.).unwrap(),
            0
        );
        assert_eq!(nearest(&[0., f64::NAN], &[0., 0.], 0., 0.).unwrap(), 1);
        assert_eq!(nearest(&[1., 0.], &[0., 0.], f64::NAN, 0.).unwrap(), 0);
    }
    #[test]
    fn ffi_bounds_are_checked_before_pointer_access() {
        unsafe {
            assert_eq!(
                firesky_spectral_profile_v1(
                    std::ptr::null(),
                    std::ptr::null(),
                    257,
                    0,
                    0.,
                    0.,
                    std::ptr::null_mut()
                ),
                -2
            );
            assert_eq!(
                firesky_spectral_aod_v1(
                    std::ptr::null(),
                    std::ptr::null(),
                    1025,
                    500.,
                    std::ptr::null_mut()
                ),
                -2
            );
            assert_eq!(
                firesky_spectral_nearest_v1(
                    std::ptr::null(),
                    std::ptr::null(),
                    0,
                    0.,
                    0.,
                    std::ptr::null_mut()
                ),
                -2
            );
        }
    }
}
