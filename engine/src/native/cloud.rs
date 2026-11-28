//! Float32 cloud optics shared by regional derivation and the standalone engine.

use std::slice;

pub const OPTICS_INPUT_FIELDS: usize = 4;
pub const OPTICS_OUTPUT_FIELDS: usize = 2;
pub const PROFILE_FIELDS: usize = 24;
pub const EFFECTIVE_FIELDS: usize = 5;

pub fn condensate_component(value: f32) -> f32 {
    if !value.is_finite() || value <= 0.0 {
        0.0
    } else {
        ((value.max(1e-12).log10() + 7.0) / 3.5).clamp(0.0, 1.0)
    }
}

fn nan_to_num(value: f32) -> f32 {
    if value.is_nan() {
        0.0
    } else {
        value.clamp(f32::MIN, f32::MAX)
    }
}

pub fn optics(rh: f32, fraction: f32, liquid: f32, ice: f32) -> [f32; 2] {
    let total = nan_to_num(liquid).max(0.0) + nan_to_num(ice).max(0.0);
    let condensate = condensate_component(total);
    let has_condensate = liquid.is_finite() || ice.is_finite();
    let rh_opacity = ((nan_to_num(rh) - 82.0) / 16.0).clamp(0.0, 1.0);
    if fraction.is_finite() {
        let presence = (fraction / 100.0).clamp(0.0, 1.0);
        let opacity = if fraction > 0.0 {
            (0.22 * rh_opacity + 0.78 * (0.35 + 0.65 * condensate).clamp(0.0, 1.0)).clamp(0.0, 1.0)
        } else {
            0.0
        };
        [presence, opacity]
    } else if has_condensate {
        [
            condensate,
            (0.22 * rh_opacity + 0.78 * condensate).clamp(0.0, 1.0),
        ]
    } else {
        // Humidity alone does not establish a model cloud.
        [0.0, 0.0]
    }
}

pub fn effective_profile(profile: &[f32; PROFILE_FIELDS]) -> [f32; EFFECTIVE_FIELDS] {
    let presence = nan_to_num(profile[13]).clamp(0.0, 1.0);
    let fraction = if !profile[6].is_finite() && profile[14].round_ties_even() == 300.0 {
        profile[7]
    } else {
        profile[6]
    };
    let opacity = optics(profile[0], fraction, profile[11], profile[12])[1];
    let amount = if fraction.is_finite() {
        (nan_to_num(fraction) / 100.0).clamp(0.0, 1.0)
    } else {
        presence
    };
    let explicit = fraction.is_finite() || profile[11].is_finite() || profile[12].is_finite();
    [
        amount,
        amount,
        opacity,
        fraction,
        if explicit { 1.0 } else { 0.0 },
    ]
}

unsafe fn map_rows<const INPUT: usize, const OUTPUT: usize>(
    input: *const f32,
    len: usize,
    output: *mut f32,
    function: impl Fn(&[f32; INPUT]) -> [f32; OUTPUT],
) -> i32 {
    let max = isize::MAX as usize / std::mem::size_of::<f32>() / INPUT.max(OUTPUT);
    if len > max {
        return -1;
    }
    if len == 0 {
        return 0;
    }
    if input.is_null() || output.is_null() {
        return -1;
    }
    let input = slice::from_raw_parts(input, len * INPUT);
    let output = slice::from_raw_parts_mut(output, len * OUTPUT);
    for (row, target) in input
        .chunks_exact(INPUT)
        .zip(output.chunks_exact_mut(OUTPUT))
    {
        target.copy_from_slice(&function(row.try_into().unwrap()));
    }
    0
}

/// # Safety
/// Input holds len * 4 floats; output holds len * 2 floats, without overlap.
#[no_mangle]
pub unsafe extern "C" fn firesky_cloud_optics(
    input: *const f32,
    len: usize,
    output: *mut f32,
) -> i32 {
    map_rows(input, len, output, |row: &[f32; 4]| {
        optics(row[0], row[1], row[2], row[3])
    })
}

/// # Safety
/// Input holds len * 24 floats; output holds len * 5 floats, without overlap.
#[no_mangle]
pub unsafe extern "C" fn firesky_cloud_effective_profiles(
    input: *const f32,
    len: usize,
    output: *mut f32,
) -> i32 {
    map_rows(input, len, output, effective_profile)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn humidity_is_not_a_cloud_and_explicit_zero_is_authoritative() {
        assert_eq!(optics(100.0, f32::NAN, f32::NAN, f32::NAN), [0.0; 2]);
        assert_eq!(optics(100.0, 0.0, 1e-3, 1e-3), [0.0; 2]);
        assert_eq!(optics(100.0, 11.0, 1e-3, 1e-3)[0], 0.11);
    }

    #[test]
    fn cloud_fraction_and_opacity_are_not_multiplied_twice() {
        let thin = optics(90.0, 10.0, 1e-6, 0.0);
        let full = optics(90.0, 100.0, 1e-6, 0.0);
        assert_eq!(thin[1], full[1]);
        assert_eq!(full[0], 1.0);
    }

    #[test]
    fn condensate_mapping_is_continuous_across_thin_cloud_amounts() {
        let low = optics(100.0, f32::NAN, 1e-5, 0.0);
        let higher = optics(100.0, f32::NAN, 1e-4, 0.0);
        assert!((low[0] - (2.0 / 3.5)).abs() < 1e-6);
        assert!((higher[0] - (3.0 / 3.5)).abs() < 1e-6);
        assert!(higher[0] > low[0]);
    }

    #[test]
    fn high_fraction_fallback_uses_ties_to_even_and_preserves_explicit_fraction() {
        let mut profile = [f32::NAN; PROFILE_FIELDS];
        profile[7] = 40.0;
        profile[14] = 300.5;
        assert_eq!(effective_profile(&profile)[0], 0.4);
        profile[6] = 11.0;
        assert_eq!(effective_profile(&profile)[0], 0.11);
        profile[6] = f32::NAN;
        profile[14] = 500.0;
        assert!(effective_profile(&profile)[3].is_nan());
        assert_eq!(effective_profile(&profile)[4], 0.0);
    }

    #[test]
    fn ffi_bounds_are_checked_before_slicing() {
        unsafe {
            assert_eq!(
                firesky_cloud_optics(std::ptr::null(), 1, std::ptr::null_mut()),
                -1
            );
            assert_eq!(
                firesky_cloud_effective_profiles(
                    std::ptr::null(),
                    usize::MAX,
                    std::ptr::null_mut()
                ),
                -1
            );
            assert_eq!(
                firesky_cloud_optics(std::ptr::null(), 0, std::ptr::null_mut()),
                0
            );
        }
    }
}
