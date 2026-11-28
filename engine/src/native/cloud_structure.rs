//! Pressure-profile classification and connected cloud-body material signals.

use std::slice;

pub const INPUT_FIELDS: usize = 6;
pub const OUTPUT_FIELDS: usize = 10;
pub const MAX_LEVELS: usize = 256;
const ACTIVE: f32 = 0.005;
const CONNECTION_AMOUNT: f32 = 0.10;
const CONNECTION_METERS: f32 = 900.0;

fn clean(value: f32) -> f32 {
    if value.is_nan() {
        0.0
    } else {
        value.clamp(f32::MIN, f32::MAX)
    }
}

fn unit(value: f32) -> f32 {
    clean(value).clamp(0.0, 1.0)
}

fn maximum(a: f32, b: f32) -> f32 {
    if a.is_nan() || b.is_nan() {
        f32::NAN
    } else {
        a.max(b)
    }
}

fn minimum(a: f32, b: f32) -> f32 {
    if a.is_nan() || b.is_nan() {
        f32::NAN
    } else {
        a.min(b)
    }
}

struct DeepStructure {
    amount: Vec<f32>,
    structure: Vec<f32>,
    resolved: Vec<[bool; 3]>,
    active: Vec<[bool; 3]>,
    amounts: [f32; 3],
    strengths: [f32; 3],
    base_mid: f32,
    deep: f32,
}

fn family_max(values: &[f32], masks: &[[bool; 3]]) -> [f32; 3] {
    let mut result = [0.0_f32; 3];
    for (&value, mask) in values.iter().zip(masks) {
        for family in 0..3 {
            if mask[family] {
                result[family] = result[family].max(value);
            }
        }
    }
    result
}

fn deep_structure(
    amounts: &[f32],
    levels: &[f32],
    aggregate: Option<f32>,
    masks: Option<&[[bool; 3]]>,
) -> DeepStructure {
    let amount: Vec<f32> = amounts.iter().map(|&v| unit(v)).collect();
    let max_at = |predicate: &dyn Fn(f32) -> bool| {
        levels
            .iter()
            .zip(&amount)
            .filter(|(level, _)| predicate(**level))
            .map(|(_, &v)| v)
            .fold(0.0_f32, f32::max)
    };
    let upper = max_at(&|p| p <= 300.0);
    let middle = max_at(&|p| p > 400.0 && p <= 700.0);
    let boundary = max_at(&|p| p == 400.0);
    let pair = if levels.contains(&250.0) && levels.contains(&300.0) {
        levels
            .iter()
            .zip(&amount)
            .filter(|(p, _)| **p == 250.0 || **p == 300.0)
            .map(|(_, &v)| v)
            .fold(1.0_f32, f32::min)
    } else {
        0.0
    };
    let continuous = boundary >= 0.60 && (middle < 0.40 || pair >= 0.80);
    let resolved: Vec<[bool; 3]> = levels
        .iter()
        .map(|&p| {
            let adaptive = p == 400.0 && upper >= 0.30 && !continuous;
            [
                p <= 400.0 && !adaptive,
                (p > 400.0 && p <= 700.0) || adaptive,
                p > 700.0,
            ]
        })
        .collect();
    let active = masks.unwrap_or(&resolved).to_vec();
    let amounts = family_max(&amount, &active);
    let peak = amount.iter().copied().fold(0.0_f32, f32::max);
    let normalized: Vec<f32> = amount
        .iter()
        .map(|&v| if peak > ACTIVE { v / peak } else { 0.0 })
        .collect();
    let support = normalized.iter().filter(|&&v| v >= 0.20).count();
    let normalize = amounts[0] > ACTIVE && (amounts[0] - peak).abs() <= 1e-6 && support <= 2;
    let structure = if normalize {
        normalized
    } else {
        amount.clone()
    };
    let strengths = family_max(&structure, &active);
    let base_mid = levels
        .iter()
        .zip(&structure)
        .filter(|(p, _)| **p > 400.0 && **p <= 700.0)
        .map(|(_, &v)| v)
        .fold(0.0_f32, f32::max);
    let cloudy = structure.iter().filter(|&&v| v >= 0.35).count() as f32;
    let mut deep = (0.45 * strengths[0].min(strengths[1])
        + 0.35 * strengths[1].min(strengths[2])
        + 0.20 * cloudy / 5.0)
        .clamp(0.0, 1.0);
    if let Some(aggregate) = aggregate {
        deep = deep.max(unit(aggregate));
    }
    DeepStructure {
        amount,
        structure,
        resolved,
        active,
        amounts,
        strengths,
        base_mid,
        deep,
    }
}

fn condensate_presence(value: f32) -> f32 {
    super::cloud::condensate_component(value)
}

fn material(strengths: [f32; 3], deep: f32, thickness: f32, code: usize) -> f32 {
    // NumPy's lookup tables promote this portion of the reference to float64.
    let base = [0.08, 0.93, 0.90, 0.80, 0.76, 0.62, 0.34, 0.22, 0.28][code];
    let sensitivity = [0.35, 0.70, 0.60, 0.50, 0.40, 0.35, 0.35, 0.25, 0.35][code];
    let high = strengths[0].max(0.75 * strengths[1]).clamp(0.0, 1.0);
    let low_penalty = if code == 6 || code == 7 {
        0.0
    } else {
        0.28 * strengths[2]
    };
    let mut thick_penalty = 0.34 * deep + 0.18 * thickness;
    if code == 1 || code == 2 {
        thick_penalty *= 0.55;
    }
    if code == 8 {
        thick_penalty *= 1.35;
    }
    let optical =
        (1.0_f64 - sensitivity * f64::from(0.30 * thickness + 0.20 * deep)).clamp(0.0, 1.0);
    (base * optical * f64::from(0.55 + 0.45 * high)
        - f64::from(low_penalty)
        - f64::from(thick_penalty))
    .clamp(0.0, 1.0) as f32
}

/// Rows contain amount, ASL height, liquid, ice, omega and explicit-evidence flag.
/// The output is high/mid/low amount, deep risk, thickness, class code,
/// column material and three isolated-family material signals.
pub fn classify(
    rows: &[[f32; INPUT_FIELDS]],
    levels: &[f32],
    aggregate: Option<f32>,
    masks: Option<&[[bool; 3]]>,
    derive_layers: bool,
) -> Result<[f32; OUTPUT_FIELDS], String> {
    if rows.len() != levels.len()
        || levels.is_empty()
        || levels.len() > MAX_LEVELS
        || levels.iter().any(|p| !p.is_finite())
        || masks.is_some_and(|m| m.len() != levels.len())
    {
        return Err("cloud profile dimensions or pressure levels are invalid".into());
    }
    let amounts: Vec<f32> = rows.iter().map(|row| row[0]).collect();
    let state = deep_structure(&amounts, levels, aggregate, masks);
    let mut lowest = f32::INFINITY;
    let mut highest = f32::NEG_INFINITY;
    let mut liquid = 0.0_f32;
    let mut ice = 0.0_f32;
    let mut lift = f32::INFINITY;
    let mut explicit = false;
    let mut strongest_high = false;
    for (i, row) in rows.iter().enumerate() {
        if state.structure[i] >= 0.35 {
            lowest = minimum(lowest, row[1]);
            highest = maximum(highest, row[1]);
        }
        liquid = liquid.max(condensate_presence(row[2]));
        ice = ice.max(condensate_presence(row[3]));
        if row[4].is_finite() {
            lift = lift.min(row[4]);
        }
        explicit |= row[5] != 0.0;
        strongest_high |= state.active[i][0] && state.structure[i] >= 0.45;
    }
    if !lift.is_finite() {
        lift = 0.0;
    }
    let depth = if lowest.is_finite() && highest.is_finite() {
        (highest - lowest).max(0.0)
    } else {
        0.0
    };
    let thickness = ((depth - 2800.0) / 5200.0).clamp(0.0, 1.0);
    let [high, mid, low] = state.strengths;
    let deep = state.deep;
    let mut code = if deep >= 0.68 && low >= 0.45 {
        8
    } else if deep >= 0.58 && lift <= -0.25 && low >= 0.20 && (!explicit || ice >= 0.12) {
        4
    } else if low >= 0.62 && high < 0.35 && mid < 0.45 {
        6
    } else if mid >= 0.58 && high < 0.45 && low < 0.45 {
        2
    } else if high >= 0.55 && state.base_mid >= 0.45 {
        3
    } else if high >= 0.50 && strongest_high && (!explicit || ice >= (liquid * 0.65).max(0.12)) {
        1
    } else if mid >= 0.45 {
        5
    } else if low >= 0.45 {
        7
    } else {
        0
    };
    if code == 1 && aggregate.is_some_and(|v| v >= 0.25) && high >= 0.45 {
        code = 3;
    }
    if code == 0 && high >= 0.50 && strongest_high {
        code = 3;
    }
    let mut output = [0.0; OUTPUT_FIELDS];
    output[..3].copy_from_slice(&state.amounts);
    output[3] = deep;
    output[4] = thickness;
    output[5] = code as f32;
    output[6] = material(state.strengths, deep, thickness, code);
    if derive_layers {
        for family in 0..3 {
            let isolated_rows: Vec<_> = rows
                .iter()
                .enumerate()
                .map(|(i, row)| {
                    if state.resolved[i][family] {
                        [
                            state.amount[i],
                            row[1],
                            clean(row[2]).max(0.0),
                            clean(row[3]).max(0.0),
                            row[4],
                            row[5],
                        ]
                    } else {
                        [0.0, row[1], 0.0, 0.0, 0.0, 0.0]
                    }
                })
                .collect();
            let isolated_masks: Vec<_> = state
                .resolved
                .iter()
                .map(|mask| {
                    let mut result = [false; 3];
                    result[family] = mask[family];
                    result
                })
                .collect();
            let isolated = classify(&isolated_rows, levels, None, Some(&isolated_masks), false)?;
            if isolated[family] > ACTIVE {
                output[7 + family] = isolated[6];
            }
        }
    }
    Ok(output)
}

pub fn connected(first: [f32; 3], second: [f32; 3]) -> bool {
    first[0] >= CONNECTION_AMOUNT
        && second[0] >= CONNECTION_AMOUNT
        && first[1..].iter().chain(&second[1..]).all(|v| v.is_finite())
        && first[2] >= second[1] - CONNECTION_METERS
        && first[1] <= second[2] + CONNECTION_METERS
}

/// Each family carries amount, base ASL and top ASL.
pub(crate) fn body_risks(
    amounts: &[f32],
    levels: &[f32],
    aggregate: f32,
    members: &[[bool; 3]],
    families: [[f32; 3]; 3],
) -> [f32; 3] {
    let mut output = [0.0; 3];
    for family in 0..3 {
        let mut body: Vec<bool> = members.iter().map(|mask| mask[family]).collect();
        let mut high_mid_connected = false;
        for adjacent in 0..3 {
            if family.abs_diff(adjacent) != 1 || !connected(families[family], families[adjacent]) {
                continue;
            }
            high_mid_connected |= family < 2 && adjacent < 2;
            for (body, mask) in body.iter_mut().zip(members) {
                *body |= mask[adjacent];
            }
        }
        let amount: Vec<_> = amounts
            .iter()
            .zip(&body)
            .map(|(&v, &keep)| if keep { unit(v) } else { 0.0 })
            .collect();
        let masks: Vec<_> = members
            .iter()
            .zip(&body)
            .map(|(mask, &keep)| mask.map(|v| v && keep))
            .collect();
        output[family] = deep_structure(
            &amount,
            levels,
            Some(if family < 2 && high_mid_connected {
                aggregate
            } else {
                0.0
            }),
            Some(&masks),
        )
        .deep;
    }
    output
}

fn dimensions(cells: usize, levels: usize) -> Option<usize> {
    if levels == 0 || levels > MAX_LEVELS {
        return None;
    }
    cells
        .checked_mul(levels)
        .filter(|&n| n <= isize::MAX as usize / (INPUT_FIELDS * size_of::<f32>()))
}

/// # Safety
/// Input: cells * levels * 6 float32 values; pressure: levels values;
/// aggregate: null or cells values; masks: null or cells * levels * 3 bytes.
/// Output: cells * 10 floats. All buffers are aligned and output does not alias input.
#[no_mangle]
pub unsafe extern "C" fn firesky_cloud_structure_v1(
    input: *const f32,
    pressure: *const f32,
    aggregate: *const f32,
    masks: *const u8,
    cells: usize,
    levels: usize,
    derive_layers: u8,
    output: *mut f32,
) -> i32 {
    let Some(count) = dimensions(cells, levels) else {
        return -1;
    };
    if derive_layers > 1 || cells > isize::MAX as usize / (OUTPUT_FIELDS * size_of::<f32>()) {
        return -1;
    }
    if cells == 0 {
        return 0;
    }
    if input.is_null() || pressure.is_null() || output.is_null() {
        return -1;
    }
    let rows = slice::from_raw_parts(input.cast::<[f32; INPUT_FIELDS]>(), count);
    let pressure = slice::from_raw_parts(pressure, levels);
    if pressure.iter().any(|p| !p.is_finite()) {
        return -1;
    }
    let aggregates = (!aggregate.is_null()).then(|| slice::from_raw_parts(aggregate, cells));
    let masks = (!masks.is_null()).then(|| slice::from_raw_parts(masks.cast::<[u8; 3]>(), count));
    let output = slice::from_raw_parts_mut(output, cells * OUTPUT_FIELDS);
    for cell in 0..cells {
        let range = cell * levels..(cell + 1) * levels;
        let masks: Option<Vec<_>> =
            masks.map(|m| m[range.clone()].iter().map(|m| m.map(|v| v != 0)).collect());
        let result = classify(
            &rows[range],
            pressure,
            aggregates.map(|v| v[cell]),
            masks.as_deref(),
            derive_layers != 0,
        );
        let Ok(result) = result else {
            return -1;
        };
        output[cell * OUTPUT_FIELDS..(cell + 1) * OUTPUT_FIELDS].copy_from_slice(&result);
    }
    0
}

/// # Safety
/// Amount: cells * levels floats; pressure: levels floats; aggregate: cells floats;
/// members: cells * levels * 3 bytes; families: cells * 9 floats.
/// Output: cells * 3 floats; output does not alias input.
#[no_mangle]
pub unsafe extern "C" fn firesky_cloud_body_risks_v1(
    amount: *const f32,
    pressure: *const f32,
    aggregate: *const f32,
    members: *const u8,
    families: *const f32,
    cells: usize,
    levels: usize,
    output: *mut f32,
) -> i32 {
    let Some(count) = dimensions(cells, levels) else {
        return -1;
    };
    if cells > isize::MAX as usize / (9 * size_of::<f32>()) {
        return -1;
    }
    if cells == 0 {
        return 0;
    }
    if amount.is_null()
        || pressure.is_null()
        || aggregate.is_null()
        || members.is_null()
        || families.is_null()
        || output.is_null()
    {
        return -1;
    }
    let amount = slice::from_raw_parts(amount, count);
    let pressure = slice::from_raw_parts(pressure, levels);
    if pressure.iter().any(|p| !p.is_finite()) {
        return -1;
    }
    let aggregate = slice::from_raw_parts(aggregate, cells);
    let members = slice::from_raw_parts(members.cast::<[u8; 3]>(), count);
    let families = slice::from_raw_parts(families.cast::<[[f32; 3]; 3]>(), cells);
    let output = slice::from_raw_parts_mut(output, cells * 3);
    for cell in 0..cells {
        let range = cell * levels..(cell + 1) * levels;
        let members: Vec<_> = members[range.clone()]
            .iter()
            .map(|m| m.map(|v| v != 0))
            .collect();
        let families = families[cell].map(|mut f| {
            f[0] = unit(f[0]);
            f
        });
        output[cell * 3..(cell + 1) * 3].copy_from_slice(&body_risks(
            &amount[range],
            pressure,
            aggregate[cell],
            &members,
            families,
        ));
    }
    0
}

/// # Safety
/// Input: cells * 14 floats (code, cap, three amount/base/top triplets,
/// three material signals); output: cells * 3 floats, not overlapping input.
#[no_mangle]
pub unsafe extern "C" fn firesky_cloud_connected_caps_v1(
    input: *const f32,
    cells: usize,
    output: *mut f32,
) -> i32 {
    if cells > isize::MAX as usize / (14 * size_of::<f32>()) {
        return -1;
    }
    if cells == 0 {
        return 0;
    }
    if input.is_null() || output.is_null() {
        return -1;
    }
    let input = slice::from_raw_parts(input.cast::<[f32; 14]>(), cells);
    let output = slice::from_raw_parts_mut(output, cells * 3);
    for (row, output) in input.iter().zip(output.chunks_exact_mut(3)) {
        let cap = (row[0] == 4.0 || row[0] == 8.0)
            && connected(row[2..5].try_into().unwrap(), row[5..8].try_into().unwrap())
            && connected(
                row[5..8].try_into().unwrap(),
                row[8..11].try_into().unwrap(),
            );
        for i in 0..3 {
            output[i] = if cap {
                minimum(row[11 + i], row[1])
            } else {
                row[11 + i]
            };
        }
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn adaptive_boundary_retains_continuous_upper_deck() {
        let levels = [250.0, 300.0, 400.0, 500.0, 850.0];
        let state = deep_structure(&[0.9, 0.9, 0.8, 0.7, 0.0], &levels, None, None);
        assert!(state.resolved[2][0]);
        let state = deep_structure(&[0.5, 0.5, 0.5, 0.7, 0.0], &levels, None, None);
        assert!(state.resolved[2][1]);
    }

    #[test]
    fn sparse_cirrus_keeps_relative_material_but_absolute_coverage() {
        let rows = [[0.11, 10000.0, 0.0, 1e-5, 0.0, 1.0]];
        let result = classify(&rows, &[300.0], None, None, true).unwrap();
        assert_eq!(result[0], 0.11);
        assert_eq!(result[5], 1.0);
        assert!(result[7] > 0.8);
    }

    #[test]
    fn connected_geometry_requires_significant_amount_and_finite_bounds() {
        assert!(connected([0.1, 6000.0, 10000.0], [0.3, 2000.0, 5100.0]));
        assert!(!connected([0.099, 6000.0, 10000.0], [0.3, 2000.0, 5100.0]));
        assert!(!connected([0.5, f32::NAN, 10000.0], [0.3, 2000.0, 5100.0]));
    }

    #[test]
    fn invalid_dimensions_fail_before_reading_pointers() {
        unsafe {
            assert_eq!(
                firesky_cloud_structure_v1(
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    1,
                    0,
                    1,
                    std::ptr::null_mut()
                ),
                -1
            );
            assert_eq!(
                firesky_cloud_body_risks_v1(
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    usize::MAX,
                    11,
                    std::ptr::null_mut()
                ),
                -1
            );
            assert_eq!(
                firesky_cloud_connected_caps_v1(std::ptr::null(), usize::MAX, std::ptr::null_mut()),
                -1
            );
            assert_eq!(
                firesky_cloud_structure_v1(
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    0,
                    11,
                    1,
                    std::ptr::null_mut()
                ),
                0
            );
        }
    }
}
