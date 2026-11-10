//! Empirical response primitives, ported from forward_field_scoring.py.

use std::slice;

pub const MAX_SCORE: f64 = 2.5;
pub const WINDOW_MINUTES: f64 = 20.0;
pub const MAX_SAMPLES: usize = 4096;
pub const RESPONSE_SCALE: f64 = 1.225;
pub const RESPONSE_EXPONENT: f64 = 1.2;

fn piecewise(value: f64, points: &[(f64, f64)]) -> f64 {
    if value.is_nan() {
        return f64::NAN;
    }
    if value <= points[0].0 {
        return points[0].1;
    }
    for pair in points.windows(2) {
        let [(x0, y0), (x1, y1)] = [pair[0], pair[1]];
        if value <= x1 {
            return y0 + (value - x0) / (x1 - x0) * (y1 - y0);
        }
    }
    points.last().unwrap().1
}

pub fn calibrated_quality_response(value: f64) -> f64 {
    if value.is_nan() {
        return f64::NAN;
    }
    (RESPONSE_SCALE * value.max(0.0).powf(RESPONSE_EXPONENT)).clamp(0.0, MAX_SCORE)
}

pub fn inverse_calibrated_quality_response(value: f64) -> f64 {
    if value.is_nan() {
        return f64::NAN;
    }
    (value.clamp(0.0, MAX_SCORE) / RESPONSE_SCALE)
        .powf(1.0 / RESPONSE_EXPONENT)
        .clamp(0.0, MAX_SCORE)
}

pub fn duration_factor(minutes: f64) -> f64 {
    // Python's max(0.0, nan) is 0.0.
    piecewise(
        minutes.max(0.0),
        &[
            (0.0, 0.0),
            (5.0, 0.25),
            (10.0, 0.50),
            (15.0, 0.75),
            (20.0, 1.00),
            (40.0, 1.04),
            (70.0, 1.07),
            (120.0, 1.10),
        ],
    )
}

pub fn cloud_amount_score(amount: f64) -> f64 {
    piecewise(
        amount,
        &[
            (0.00, 0.000),
            (0.02, 0.020),
            (0.05, 0.050),
            (0.12, 0.200),
            (0.25, 0.400),
            (0.40, 0.600),
            (0.55, 0.800),
            (0.70, 1.000),
            (0.85, 1.500),
            (0.95, 2.000),
            (1.00, 2.500),
        ],
    )
}

pub fn quality_level(index: f64) -> &'static str {
    if !index.is_finite() {
        return "无明显火烧云";
    }
    for (upper, label) in [
        (0.001, "无明显火烧云"),
        (0.05, "微微烧"),
        (0.20, "小烧"),
        (0.40, "小烧到中等烧"),
        (0.60, "中等烧"),
        (0.80, "中等烧到大烧"),
        (1.00, "不完美大烧"),
        (1.50, "典型大烧"),
        (2.00, "优质大烧"),
        (f64::INFINITY, "世纪大烧"),
    ] {
        if index < upper {
            return label;
        }
    }
    unreachable!()
}

#[derive(Debug, PartialEq)]
pub struct EventWindow {
    pub weights: Vec<f64>,
    pub window_minutes: f64,
}

/// Widths describe support intervals, not distances between offsets. Offsets
/// only determine stable ordering, matching the Python event-axis convention.
pub fn event_window_weights(
    values: &[f64],
    active: &[u8],
    widths: &[f64],
    offsets: Option<&[f64]>,
) -> Result<EventWindow, &'static str> {
    let n = values.len();
    if n > MAX_SAMPLES
        || active.len() != n
        || widths.len() != n
        || offsets.is_some_and(|items| items.len() != n)
    {
        return Err("aligned arrays of at most 4096 samples are required");
    }
    if values.iter().chain(widths).any(|x| !x.is_finite())
        || offsets.is_some_and(|items| items.iter().any(|x| !x.is_finite()))
        || active.iter().any(|&x| x > 1)
    {
        return Err("samples must be finite and active must contain only 0 or 1");
    }
    let mut order: Vec<usize> = (0..n).collect();
    if let Some(offsets) = offsets {
        order.sort_by(|&a, &b| offsets[a].partial_cmp(&offsets[b]).unwrap());
    }
    let mut edges = Vec::with_capacity(n + 1);
    edges.push(0.0);
    let mut total_active = 0.0;
    for &i in &order {
        let width = widths[i].max(0.0);
        edges.push(edges.last().unwrap() + width);
        if active[i] != 0 {
            total_active += width;
        }
    }
    if !edges[n].is_finite() || !total_active.is_finite() {
        return Err("event duration overflow");
    }
    let mut result = EventWindow {
        weights: vec![0.0; n],
        window_minutes: 0.0,
    };
    if total_active <= 0.0 {
        return Ok(result);
    }
    let denominator = total_active.min(WINDOW_MINUTES);
    let max_start = (edges[n] - WINDOW_MINUTES).max(0.0);
    let mut candidates = vec![0.0, max_start];
    for &edge in &edges {
        candidates.push(edge.clamp(0.0, max_start));
        candidates.push((edge - WINDOW_MINUTES).clamp(0.0, max_start));
    }
    candidates.sort_by(|a, b| a.partial_cmp(b).unwrap());
    candidates.dedup();
    let mut best = f64::NEG_INFINITY;
    for start in candidates {
        let stop = start + WINDOW_MINUTES;
        let weight = |j: usize, i: usize| {
            if active[i] == 0 {
                0.0
            } else {
                (edges[j + 1].min(stop) - edges[j].max(start)).max(0.0) / denominator
            }
        };
        let objective: f64 = order
            .iter()
            .enumerate()
            .map(|(j, &i)| weight(j, i) * values[i])
            .sum();
        if !objective.is_finite() {
            return Err("event objective overflow");
        }
        // Keep the earliest candidate on ties, as numpy.argmax does.
        if objective > best {
            best = objective;
            for (j, &i) in order.iter().enumerate() {
                result.weights[i] = weight(j, i);
            }
        }
    }
    result.window_minutes = result.weights.iter().sum::<f64>() * denominator;
    Ok(result)
}

/// Additive extension to ABI v3; existing scorer layouts are unchanged.
///
/// # Safety
/// Inputs must reference `len` readable elements, outputs `len` writable
/// elements and one writable double respectively. Buffers must not overlap.
/// Null offsets means the input is already ordered.
#[no_mangle]
pub unsafe extern "C" fn firesky_empirical_window_weights(
    values: *const f64,
    active: *const u8,
    widths: *const f64,
    offsets: *const f64,
    len: usize,
    weights: *mut f64,
    window_minutes: *mut f64,
) -> i32 {
    if len > MAX_SAMPLES || window_minutes.is_null() {
        return -1;
    }
    if len == 0 {
        *window_minutes = 0.0;
        return 0;
    }
    if values.is_null() || active.is_null() || widths.is_null() || weights.is_null() {
        return -1;
    }
    let result = event_window_weights(
        slice::from_raw_parts(values, len),
        slice::from_raw_parts(active, len),
        slice::from_raw_parts(widths, len),
        if offsets.is_null() {
            None
        } else {
            Some(slice::from_raw_parts(offsets, len))
        },
    );
    match result {
        Ok(result) => {
            std::ptr::copy_nonoverlapping(result.weights.as_ptr(), weights, len);
            *window_minutes = result.window_minutes;
            0
        }
        Err(_) => -1,
    }
}

/// # Safety
/// Input and output must reference `len` doubles in non-overlapping buffers.
/// Mode 0 applies calibration, mode 1 its inverse.
#[no_mangle]
pub unsafe extern "C" fn firesky_empirical_response(
    values: *const f64,
    len: usize,
    mode: u32,
    output: *mut f64,
) -> i32 {
    if mode > 1 || len > isize::MAX as usize / std::mem::size_of::<f64>() {
        return -1;
    }
    if len == 0 {
        return 0;
    }
    if values.is_null() || output.is_null() {
        return -1;
    }
    let function = if mode == 0 {
        calibrated_quality_response
    } else {
        inverse_calibrated_quality_response
    };
    for (result, &value) in slice::from_raw_parts_mut(output, len)
        .iter_mut()
        .zip(slice::from_raw_parts(values, len))
    {
        *result = function(value);
    }
    0
}

#[no_mangle]
pub extern "C" fn firesky_empirical_duration_factor(minutes: f64) -> f64 {
    duration_factor(minutes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn short_event_preserves_active_mean() {
        let result =
            event_window_weights(&[0.8, 0.8, 0.8, 0.1], &[1; 4], &[3.0, 4.0, 5.0, 6.0], None)
                .unwrap();
        assert_eq!(result.window_minutes, 18.0);
        for (actual, width) in result.weights.iter().zip([3.0, 4.0, 5.0, 6.0]) {
            assert!((actual - width / 18.0).abs() < 1e-14);
        }
    }

    #[test]
    fn strongest_twenty_minutes_include_partial_sample() {
        let result = event_window_weights(
            &[0.8, 0.8, 0.8, 0.8, 0.1],
            &[1; 5],
            &[3.0, 4.0, 5.0, 6.0, 7.0],
            None,
        )
        .unwrap();
        assert_eq!(result.weights, vec![0.15, 0.2, 0.25, 0.3, 0.1]);
        assert!((result.window_minutes - 20.0).abs() < 1e-12);
    }

    #[test]
    fn gaps_do_not_contribute_and_offsets_scatter_back() {
        let result = event_window_weights(
            &[0.1, 0.8, 0.8],
            &[1, 1, 0],
            &[15.0; 3],
            Some(&[20.0, 0.0, 10.0]),
        )
        .unwrap();
        assert_eq!(result.weights, vec![0.0, 0.75, 0.0]);
        assert_eq!(result.window_minutes, 15.0);
    }

    #[test]
    fn empty_invalid_and_negative_widths() {
        assert_eq!(
            event_window_weights(&[], &[], &[], None)
                .unwrap()
                .window_minutes,
            0.0
        );
        assert!(event_window_weights(&[f64::NAN], &[1], &[5.0], None).is_err());
        assert!(event_window_weights(&[1.0], &[2], &[5.0], None).is_err());
        assert!(event_window_weights(&[1.0], &[], &[5.0], None).is_err());
        assert_eq!(
            event_window_weights(&[1.0], &[1], &[-5.0], None)
                .unwrap()
                .weights,
            vec![0.0]
        );
    }

    #[test]
    fn ffi_rejects_null_nonempty_buffers_and_handles_empty_events() {
        let mut minutes = -1.0;
        unsafe {
            assert_eq!(
                firesky_empirical_window_weights(
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    1,
                    std::ptr::null_mut(),
                    &mut minutes,
                ),
                -1
            );
            assert_eq!(minutes, -1.0);
            assert_eq!(
                firesky_empirical_window_weights(
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    0,
                    std::ptr::null_mut(),
                    &mut minutes,
                ),
                0
            );
            assert_eq!(minutes, 0.0);
            assert_eq!(
                firesky_empirical_response(std::ptr::null(), 1, 0, std::ptr::null_mut(),),
                -1
            );
        }
    }

    #[test]
    fn response_and_thresholds() {
        assert!(calibrated_quality_response(f64::NAN).is_nan());
        assert_eq!(calibrated_quality_response(f64::INFINITY), MAX_SCORE);
        assert_eq!(calibrated_quality_response(-1.0), 0.0);
        assert_eq!(quality_level(f64::NAN), "无明显火烧云");
        assert_eq!(quality_level(2.0), "世纪大烧");
        assert_eq!(duration_factor(20.0), 1.0);
        assert_eq!(duration_factor(200.0), 1.1);
        for value in [0.0, 0.1, 0.5, 1.0, 1.5] {
            assert!(
                (inverse_calibrated_quality_response(calibrated_quality_response(value)) - value)
                    .abs()
                    < 1e-14
            );
        }
    }
}
