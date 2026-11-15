// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 李晓洋
//
// This program is free software: you can redistribute it and/or modify it
// under the terms of the GNU General Public License as published by the Free
// Software Foundation, version 3.

use std::collections::HashMap;
use std::slice;

mod national;
pub use national::{
    assess_batch_with_cancel, assess_batch_with_status, BatchCancel, NationalAssessmentError,
    OUTPUT_FIELDS as NATIONAL_OUTPUT_FIELDS,
};
pub mod astronomy;
pub mod cams;
pub mod cloud;
pub mod cloud_structure;
pub mod derivation;
pub mod empirical;
pub mod gfs;
pub mod observer_diagnostics;
pub mod profile_diagnostics;
pub mod quality;
pub mod solar_time;
pub mod spectral;
pub mod timeline;

const FIELD_COUNT: usize = 20;
const EARTH_RADIUS_KM: f64 = 6371.0;
const MAX_ANALYSIS_RADIUS_KM: f64 = national::MAX_ANALYSIS_RADIUS_KM as f64;
const VIEW_AZIMUTH_HALF_DEG: f64 = 60.0;
const SOLAR_SEMI_DIAMETER_DEG: f64 = 0.2666667;
const CANDIDATE_FEASIBILITY_EPSILON: f64 = 1.0e-3;

const LAT: usize = 0;
const LON: usize = 1;
const DISTANCE: usize = 2;
const BEARING: usize = 3;
const PRESSURE: usize = 4;
const HEIGHT: usize = 5;
const RH: usize = 6;
const TEMPERATURE: usize = 7;
const OMEGA: usize = 8;
const U_WIND: usize = 9;
const V_WIND: usize = 10;
const CLOUD_FRACTION: usize = 11;
const CLOUD_LIQUID: usize = 12;
const CLOUD_ICE: usize = 13;
const SOURCE_LAT: usize = 14;
const SOURCE_LON: usize = 15;
const SOURCE_DISTANCE: usize = 16;
const SOURCE_BEARING: usize = 17;
const VISIBILITY: usize = 18;
const PRECIPITATION: usize = 19;
pub const METRIC_COUNT: usize = 9;
const CANDIDATE_PREFILTER_FIELDS: usize = 7;
const BOUNDARY_PREFILTER_FIELDS: usize = 3;
const AGG_COLUMN_FIELDS: usize = 31;
const PRESSURE_LEVEL_VALUES: [i32; 11] = [200, 250, 300, 400, 500, 600, 700, 750, 800, 850, 925];
// Increment this whenever any exported C ABI signature or row layout changes.
pub const NATIVE_ABI_VERSION: u32 = 3;

type Feature = [f64; FIELD_COUNT];
type GridKey = (i32, i32, i32);

/// Versioned assessment feature row (contract `hsd-assessment-feature-row-v2`):
/// the legacy 20-column row extended with the 2 m surface pair that powers the
/// surface-parcel LCL cloud-base first guess, the satellite footprint geometry
/// and the satellite cloud-probability pair. Absent values travel as NaN;
/// consumers must treat NaN as "unknown", never backfill it as 0. The legacy
/// 20-column layout stays untouched; only the v2 row carries these columns.
pub const FEATURE_ROW_V2_FIELD_COUNT: usize = 27;
pub const FEATURE_ROW_V2_SURFACE_TEMPERATURE_K: usize = 20;
pub const FEATURE_ROW_V2_SURFACE_DEWPOINT_K: usize = 21;
pub const FEATURE_ROW_V2_FOOTPRINT_AZIMUTH_WIDTH_DEG: usize = 22;
pub const FEATURE_ROW_V2_FOOTPRINT_NEAR_DISTANCE_KM: usize = 23;
pub const FEATURE_ROW_V2_FOOTPRINT_FAR_DISTANCE_KM: usize = 24;
pub const FEATURE_ROW_V2_HORIZONTAL_CLOUD_PROBABILITY: usize = 25;
pub const FEATURE_ROW_V2_CLOUD_PROBABILITY_GIVEN_CLOUD: usize = 26;
pub const FEATURE_ROW_V2_NAMES: [&str; FEATURE_ROW_V2_FIELD_COUNT] = [
    "lat",
    "lon",
    "distance_km",
    "bearing_deg",
    "pressure_hpa",
    "geopotential_height_m",
    "rh_percent",
    "temperature_k",
    "omega_pa_s",
    "u_ms",
    "v_ms",
    "cloud_fraction_percent",
    "cloud_liquid_kg_kg",
    "cloud_ice_kg_kg",
    "source_lat",
    "source_lon",
    "source_distance_km",
    "source_bearing_deg",
    "surface_visibility_m",
    "precip_rate_kg_m2_s",
    "surface_temperature_k",
    "surface_dewpoint_k",
    "footprint_azimuth_width_deg",
    "footprint_near_distance_km",
    "footprint_far_distance_km",
    "horizontal_cloud_probability",
    "cloud_probability_given_cloud",
];
const FEATURE_ROW_V2_LCL_BASE_SPREAD_M_PER_K: f64 = 125.0;
const FEATURE_ROW_V2_LCL_BASE_NEAR_FIELD_KM: f64 = 120.0;

pub type FeatureRowV2 = [f64; FEATURE_ROW_V2_FIELD_COUNT];

#[no_mangle]
pub extern "C" fn firesky_native_abi_version() -> u32 {
    NATIVE_ABI_VERSION
}

pub struct NativeGridIndex {
    feature_len: usize,
    by_key: HashMap<GridKey, usize>,
}

struct ColumnAccumulator {
    key: (f64, f64, f64, f64),
    reference: usize,
    layers: [Option<usize>; 11],
}

fn normalize_lon(lon: f64) -> f64 {
    let mut value = (lon + 180.0).rem_euclid(360.0) - 180.0;
    if value == -180.0 && lon > 0.0 {
        value = 180.0;
    }
    value
}

pub fn angular_difference(a: f64, b: f64) -> f64 {
    ((a - b + 180.0).rem_euclid(360.0) - 180.0).abs()
}

fn clamp01(value: f64) -> f64 {
    py_clamp01(value)
}

/// `scoring.clamp(value, low, high)` semantics: `max(low, min(high, value))`.
/// Python's `min(high, NaN)` returns `high`, so a NaN input clamps to the
/// upper bound instead of propagating.
pub fn py_clamp01(value: f64) -> f64 {
    py_clamp(value, 0.0, 1.0)
}

pub fn py_clamp(value: f64, low: f64, high: f64) -> f64 {
    if value.is_nan() {
        return high;
    }
    let bounded = if value < high { value } else { high };
    if bounded < low {
        low
    } else {
        bounded
    }
}

pub fn solar_refraction_deg(geometric_elevation_deg: f64) -> f64 {
    if !geometric_elevation_deg.is_finite() || geometric_elevation_deg >= 85.0 {
        return 0.0;
    }
    let tangent = geometric_elevation_deg.to_radians().tan();
    if tangent.abs() < 1e-12 {
        return 1735.0 / 3600.0;
    }
    let arc_seconds = if geometric_elevation_deg > 5.0 {
        58.1 / tangent - 0.07 / tangent.powi(3) + 0.000086 / tangent.powi(5)
    } else if geometric_elevation_deg > -0.575 {
        let step1 = -12.79 + geometric_elevation_deg * 0.711;
        let step2 = 103.4 + geometric_elevation_deg * step1;
        let step3 = -518.2 + geometric_elevation_deg * step2;
        1735.0 + geometric_elevation_deg * step3
    } else {
        -20.774 / tangent
    };
    arc_seconds / 3600.0
}

pub fn solar_illumination_elevation_deg(geometric_elevation_deg: f64) -> f64 {
    geometric_elevation_deg
        + solar_refraction_deg(geometric_elevation_deg)
        + SOLAR_SEMI_DIAMETER_DEG
}

fn condensate_presence(feature: &[f64]) -> Option<f64> {
    if feature[CLOUD_LIQUID].is_nan() && feature[CLOUD_ICE].is_nan() {
        return None;
    }
    let total = optional_or_zero(feature[CLOUD_LIQUID]).max(0.0)
        + optional_or_zero(feature[CLOUD_ICE]).max(0.0);
    if total <= 0.0 {
        Some(0.0)
    } else {
        Some(clamp01((total.log10() + 8.0) / 3.0))
    }
}

pub fn condensate_component(value: f64) -> f64 {
    if value.is_nan() || value <= 0.0 {
        0.0
    } else {
        clamp01((value.log10() + 8.0) / 3.0)
    }
}

fn explicit_cloud_presence(feature: &[f64], condensate: Option<f64>) -> Option<f64> {
    if feature[CLOUD_FRACTION].is_nan() {
        condensate
    } else {
        let fraction = clamp01(feature[CLOUD_FRACTION] / 100.0);
        Some(match condensate {
            Some(value) => clamp01(fraction * (0.70 + 0.30 * value)),
            None => fraction,
        })
    }
}

fn cloud_presence(feature: &[f64]) -> f64 {
    let rh_presence = clamp01((feature[RH] - 45.0) / 45.0);
    explicit_cloud_presence(feature, condensate_presence(feature))
        .map(clamp01)
        .unwrap_or(rh_presence)
}

fn pressure_level_index(pressure: i32) -> Option<usize> {
    PRESSURE_LEVEL_VALUES
        .iter()
        .position(|value| *value == pressure)
}

fn source_or(feature: &[f64], field: usize, fallback: usize) -> f64 {
    if feature[field].is_nan() {
        feature[fallback]
    } else {
        feature[field]
    }
}

fn feature_cloud_opacity(feature: &[f64]) -> f64 {
    let rh_opacity = clamp01((feature[RH] - 82.0) / 16.0);
    let condensate = condensate_presence(feature);
    let explicit = if feature[CLOUD_FRACTION].is_nan() {
        condensate
    } else {
        let fraction = clamp01(feature[CLOUD_FRACTION] / 100.0);
        let material = condensate.unwrap_or(0.5);
        Some(clamp01(fraction * (0.35 + 0.65 * material)))
    };
    // A valid fraction controls horizontal coverage. For a non-zero cloud
    // amount, RH can still describe optical thickness; explicit 0% is the
    // hard exception and cannot be revived by humidity.
    if feature[CLOUD_FRACTION].is_finite() {
        if feature[CLOUD_FRACTION] <= 0.0 {
            return 0.0;
        }
        return clamp01(0.22 * rh_opacity + 0.78 * explicit.unwrap_or(0.0));
    }
    explicit
        .map(|value| clamp01(0.22 * rh_opacity + 0.78 * value))
        .unwrap_or(rh_opacity)
}

fn feature_blocker_opacity(feature: &[f64]) -> f64 {
    if feature[CLOUD_FRACTION].is_nan() && condensate_presence(feature).is_none() {
        clamp01((feature[RH] - 88.0) / 10.0)
    } else {
        feature_cloud_opacity(feature)
    }
}

fn lightpath_opacity(feature: &[f64], blocker_opacity: f64) -> f64 {
    let layer_factor = if feature[PRESSURE] >= 700.0 {
        1.0
    } else if feature[PRESSURE] >= 500.0 {
        0.82
    } else {
        0.58
    };
    clamp01(blocker_opacity * layer_factor)
}

pub fn curvature_drop_m(distance_km: f64) -> f64 {
    distance_km * distance_km * 1000.0 / (2.0 * EARTH_RADIUS_KM)
}

pub fn apparent_cloud_elevation(distance_km: f64, cloud_height_m: f64) -> f64 {
    let visible_height_m = cloud_height_m - curvature_drop_m(distance_km);
    visible_height_m
        .atan2((distance_km * 1000.0).max(1.0))
        .to_degrees()
}

pub fn estimated_cloud_base(feature: &[f64]) -> f64 {
    let layer_depth_m = match feature[PRESSURE].round() as i32 {
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
        _ => 900.0,
    };
    (feature[HEIGHT] - layer_depth_m).max(0.0)
}

/// Horizontal satellite occupancy on a v2 row. NaN (unknown) means "not
/// carried", which falls through to the model cloud-fraction path; an explicit
/// 0.0 is a real probability and must never be backfilled from humidity.
fn v2_horizontal_probability(feature: &FeatureRowV2) -> Option<f64> {
    let value = feature[FEATURE_ROW_V2_HORIZONTAL_CLOUD_PROBABILITY];
    if value.is_nan() {
        None
    } else {
        Some(clamp01(value))
    }
}

/// v2 cloud opacity. A carried horizontal probability is the complete
/// footprint occupancy: <=0 stays a hard 0 (RH cannot revive it) and a
/// non-zero probability keeps the RH optical-thickness blend. Rows without
/// the probability column take the legacy fraction/condensate path verbatim.
fn v2_feature_cloud_opacity(feature: &FeatureRowV2) -> f64 {
    if let Some(probability) = v2_horizontal_probability(feature) {
        if probability <= 0.0 {
            return 0.0;
        }
        let rh_opacity = clamp01((feature[RH] - 82.0) / 16.0);
        return clamp01(0.22 * rh_opacity + 0.78 * probability);
    }
    feature_cloud_opacity(feature)
}

fn v2_feature_blocker_opacity(feature: &FeatureRowV2) -> f64 {
    if v2_horizontal_probability(feature).is_some() {
        return v2_feature_cloud_opacity(feature);
    }
    feature_blocker_opacity(feature)
}

/// v2 cloud base with the surface-parcel LCL first guess
/// (`base_agl = 125 m/K * (T - Td)` above the observer). Unknown or inverted
/// spread, far field, and an LCL above the deck ceiling all keep the legacy
/// layer-depth table, exactly like `scoring.estimated_cloud_base_m`.
pub fn v2_estimated_cloud_base(feature: &FeatureRowV2, observer_elevation_m: f64) -> f64 {
    let legacy_base_m = estimated_cloud_base(feature);
    if feature[DISTANCE] > FEATURE_ROW_V2_LCL_BASE_NEAR_FIELD_KM {
        return legacy_base_m;
    }
    let temperature_k = feature[FEATURE_ROW_V2_SURFACE_TEMPERATURE_K];
    let dewpoint_k = feature[FEATURE_ROW_V2_SURFACE_DEWPOINT_K];
    if temperature_k.is_nan() || dewpoint_k.is_nan() {
        return legacy_base_m;
    }
    let spread_k = temperature_k - dewpoint_k;
    if !spread_k.is_finite() || spread_k < 0.0 {
        return legacy_base_m;
    }
    let ceiling_m = (feature[HEIGHT] - 100.0).max(0.0);
    let lcl_base_m = observer_elevation_m + FEATURE_ROW_V2_LCL_BASE_SPREAD_M_PER_K * spread_k;
    if lcl_base_m > ceiling_m {
        return legacy_base_m;
    }
    lcl_base_m.clamp(0.0, ceiling_m)
}

pub fn v2_apparent_cloud_elevation(
    distance_km: f64,
    cloud_height_m: f64,
    observer_elevation_m: f64,
) -> f64 {
    let visible_height_m = cloud_height_m - observer_elevation_m - curvature_drop_m(distance_km);
    visible_height_m
        .atan2((distance_km * 1000.0).max(1.0))
        .to_degrees()
}

/// Per-feature assessment metrics for one `hsd-assessment-feature-row-v2` row.
/// Same 9-column output layout as the legacy batch. Horizontal occupancy
/// drives explicit presence and the opacity pair; presence follows the
/// effective Python index semantics (`clamp(explicit) else RH`), matching
/// `scoring.py`'s FeatureSpatialIndex loop where the conditional assignment is
/// shadowed by the final `explicit` composition. The conditional profile still
/// rides the row for the column-aggregation contract and the roundtrip. All
/// values stay NaN-transparent (unknown never becomes 0).
pub fn v2_feature_metrics_row(
    feature: &FeatureRowV2,
    diagnostic_radius_km: f64,
    observer_elevation_m: f64,
) -> [f64; METRIC_COUNT] {
    let condensate = condensate_presence(feature);
    let horizontal = v2_horizontal_probability(feature);
    let explicit = match horizontal {
        Some(probability) => Some(probability),
        None => explicit_cloud_presence(feature, condensate),
    };
    let within_radius = feature[DISTANCE] <= diagnostic_radius_km;
    let rh_presence = clamp01((feature[RH] - 45.0) / 45.0);
    let presence = if !within_radius {
        0.0
    } else {
        explicit.map(clamp01).unwrap_or(rh_presence)
    };
    let cloud_opacity = v2_feature_cloud_opacity(feature);
    let blocker_opacity = v2_feature_blocker_opacity(feature);
    [
        presence,
        if within_radius {
            explicit.unwrap_or(0.0)
        } else {
            0.0
        },
        if within_radius {
            condensate_component(feature[CLOUD_LIQUID])
        } else {
            0.0
        },
        if within_radius {
            condensate_component(feature[CLOUD_ICE])
        } else {
            0.0
        },
        cloud_opacity,
        blocker_opacity,
        lightpath_opacity(feature, blocker_opacity),
        v2_estimated_cloud_base(feature, observer_elevation_m),
        v2_apparent_cloud_elevation(feature[DISTANCE], feature[HEIGHT], observer_elevation_m),
    ]
}

fn sightline_height(target_distance_km: f64, target_height_m: f64, sample_distance_km: f64) -> f64 {
    sightline_height_from_observer(target_distance_km, target_height_m, sample_distance_km, 0.0)
}

fn sightline_height_from_observer(
    target_distance_km: f64,
    target_height_m: f64,
    sample_distance_km: f64,
    observer_elevation_m: f64,
) -> f64 {
    let visible_height_m =
        target_height_m - observer_elevation_m - curvature_drop_m(target_distance_km);
    let apparent = visible_height_m.atan2((target_distance_km * 1000.0).max(1.0));
    observer_elevation_m
        + apparent.tan() * sample_distance_km * 1000.0
        + curvature_drop_m(sample_distance_km)
}

fn solar_ray_height(
    target_distance_km: f64,
    target_height_m: f64,
    sample_distance_km: f64,
    solar_elevation_deg: f64,
) -> f64 {
    // Target-local tangent frame (same as the national.rs kernel): only the
    // delta segment curves away from the ray. The observer->target tilt is
    // already inside `+ target_distance_km / EARTH_RADIUS_KM`; subtracting
    // curvature_drop_m(target) as well double-counted D*delta/R.
    let distance_delta_m = (sample_distance_km - target_distance_km) * 1000.0;
    let local_elevation = solar_illumination_elevation_deg(solar_elevation_deg).to_radians()
        + target_distance_km / EARTH_RADIUS_KM;
    target_height_m
        + local_elevation.tan() * distance_delta_m
        + distance_delta_m * distance_delta_m / (2.0 * EARTH_RADIUS_KM * 1000.0)
}

/// Per-layer deck-edge softness, matching the national scorer's smoothstep
/// widths (high/mid/low). The legacy point path used to ramp linearly over
/// 650/850 m, which read up to 2.5x differently from the regional path on
/// the same physical deck edge and widened thin cirrus into a ~2 km hit band.
fn layer_edge_softness_m(cloud_base_m: f64) -> f64 {
    if cloud_base_m >= 6000.0 {
        450.0
    } else if cloud_base_m >= 2000.0 {
        300.0
    } else {
        180.0
    }
}

fn vertical_intersection(
    ray_height_m: f64,
    cloud_base_m: f64,
    cloud_top_m: f64,
    tolerance_m: f64,
) -> f64 {
    // The caller's tolerance now only caps the layer softness.
    let softness = layer_edge_softness_m(cloud_base_m).min(tolerance_m.max(1.0));
    if ray_height_m < cloud_base_m - softness || ray_height_m > cloud_top_m + softness {
        return 0.0;
    }
    if ray_height_m >= cloud_base_m && ray_height_m <= cloud_top_m {
        return 1.0;
    }
    let edge_gap = (ray_height_m - cloud_base_m)
        .abs()
        .min((ray_height_m - cloud_top_m).abs());
    let t = clamp01(edge_gap / softness);
    1.0 - t * t * (3.0 - 2.0 * t)
}

fn aerosol_extinction(aod550: f64, height_m: f64, scale_height_m: f64) -> f64 {
    let height = height_m.max(0.0);
    let scale_height = scale_height_m.max(1.0);
    (aod550 / scale_height) * (-height / scale_height).exp()
}

fn observer_aerosol_tau(
    aod550: f64,
    target_distance_km: f64,
    target_height_m: f64,
    scale_height_m: f64,
    observer_elevation_m: f64,
) -> f64 {
    if aod550 <= 0.0 || target_distance_km <= 0.0 {
        return 0.0;
    }
    let steps = ((target_distance_km / 10.0) as usize).clamp(16, 96);
    let mut tau = 0.0;
    let mut previous_distance = 0.0;
    let mut previous_height = observer_elevation_m;
    let mut previous_alpha = aerosol_extinction(aod550, 0.0, scale_height_m);
    for index in 1..=steps {
        let distance = target_distance_km * index as f64 / steps as f64;
        let height = sightline_height_from_observer(
            target_distance_km,
            target_height_m,
            distance,
            observer_elevation_m,
        );
        let alpha = aerosol_extinction(
            aod550,
            (height - observer_elevation_m).max(0.0),
            scale_height_m,
        );
        let dx_m = (distance - previous_distance) * 1000.0;
        let dz_m = height - previous_height;
        let ds_m = dx_m.hypot(dz_m);
        tau += 0.5 * (alpha + previous_alpha) * ds_m;
        previous_distance = distance;
        previous_height = height;
        previous_alpha = alpha;
    }
    tau
}

fn solar_inflow_aerosol_tau(
    aod550: f64,
    target_distance_km: f64,
    target_height_m: f64,
    solar_elevation_deg: f64,
    scale_height_m: f64,
    observer_elevation_m: f64,
) -> f64 {
    if aod550 <= 0.0 {
        return 0.0;
    }
    let stop_distance = (target_distance_km + 20.0).max(MAX_ANALYSIS_RADIUS_KM);
    let steps = (((stop_distance - target_distance_km) / 15.0) as usize).clamp(48, 180);
    // The CAMS value is the column above the local model surface: integrate
    // the incoming leg in the same observer-relative AGL coordinates as the
    // return leg, so one physical point gets one extinction value on both
    // legs. (The geometric ground test below stays in MSL terms.)
    let mut tau = 0.0;
    let mut previous_distance = target_distance_km;
    let mut previous_height = target_height_m;
    let mut previous_alpha = aerosol_extinction(
        aod550,
        (previous_height - observer_elevation_m).max(0.0),
        scale_height_m,
    );
    for index in 1..=steps {
        let distance =
            target_distance_km + (stop_distance - target_distance_km) * index as f64 / steps as f64;
        let height = solar_ray_height(
            target_distance_km,
            target_height_m,
            distance,
            solar_elevation_deg,
        );
        if height <= 0.0 {
            let fraction = previous_height / (previous_height - height).max(1.0);
            let ground_distance =
                previous_distance + (distance - previous_distance) * clamp01(fraction);
            let dx_m = (ground_distance - previous_distance) * 1000.0;
            let dz_m = -previous_height;
            let ground_alpha = aerosol_extinction(aod550, 0.0, scale_height_m);
            tau += 0.5 * (previous_alpha + ground_alpha) * dx_m.hypot(dz_m);
            break;
        }
        let alpha = aerosol_extinction(
            aod550,
            (height - observer_elevation_m).max(0.0),
            scale_height_m,
        );
        let dx_m = (distance - previous_distance) * 1000.0;
        let dz_m = height - previous_height;
        tau += 0.5 * (alpha + previous_alpha) * dx_m.hypot(dz_m);
        previous_distance = distance;
        previous_height = height;
        previous_alpha = alpha;
    }
    tau
}

fn aerosol_column_above_fraction(target_height_m: f64, observer_elevation_m: f64) -> f64 {
    (-(target_height_m - observer_elevation_m).max(0.0) / 8500.0).exp()
}

fn grid_key(feature: &Feature) -> GridKey {
    (
        feature[PRESSURE].round() as i32,
        (feature[LAT] * 4.0).round() as i32,
        (normalize_lon(feature[LON]) * 4.0).round() as i32,
    )
}

/// Build a reusable lookup index for an immutable native feature buffer.
///
/// # Safety
///
/// `features_ptr` must be valid for `features_len * FIELD_COUNT` doubles during
/// this call. The returned handle owns only the lookup table, not the features.
#[no_mangle]
pub unsafe extern "C" fn firesky_grid_index_create(
    features_ptr: *const f64,
    features_len: usize,
) -> *mut NativeGridIndex {
    if features_ptr.is_null() {
        return std::ptr::null_mut();
    }
    let values = slice::from_raw_parts(features_ptr, features_len * FIELD_COUNT);
    let features: &[[f64; FIELD_COUNT]] =
        slice::from_raw_parts(values.as_ptr().cast::<Feature>(), features_len);
    let by_key = features
        .iter()
        .enumerate()
        .map(|(position, feature)| (grid_key(feature), position))
        .collect();
    Box::into_raw(Box::new(NativeGridIndex {
        feature_len: features_len,
        by_key,
    }))
}

/// Release a handle returned by `firesky_grid_index_create`.
///
/// # Safety
///
/// `index_ptr` must be null or a live handle returned by this library and must
/// not be used again after this call.
#[no_mangle]
pub unsafe extern "C" fn firesky_grid_index_free(index_ptr: *mut NativeGridIndex) {
    if !index_ptr.is_null() {
        drop(Box::from_raw(index_ptr));
    }
}

/// Calculate scoring metrics for each feature without materializing Python rows.
/// Output layout per row: presence, explicit presence, liquid presence, ice
/// presence, cloud opacity, blocker opacity, light-path opacity, cloud base,
/// apparent cloud-top elevation.
///
/// # Safety
///
/// Input and output pointers must be valid for their declared capacities.
#[no_mangle]
pub unsafe extern "C" fn firesky_feature_metrics_batch(
    features_ptr: *const f64,
    features_len: usize,
    diagnostic_radius_km: f64,
    output_ptr: *mut f64,
    output_capacity: usize,
) -> isize {
    if features_ptr.is_null() || output_ptr.is_null() {
        return -1;
    }
    if output_capacity < features_len {
        return -2;
    }
    let values = slice::from_raw_parts(features_ptr, features_len * FIELD_COUNT);
    let features: &[[f64; FIELD_COUNT]] =
        slice::from_raw_parts(values.as_ptr().cast::<Feature>(), features_len);
    let output_values = slice::from_raw_parts_mut(output_ptr, output_capacity * METRIC_COUNT);
    let output: &mut [[f64; METRIC_COUNT]] = slice::from_raw_parts_mut(
        output_values.as_mut_ptr().cast::<[f64; METRIC_COUNT]>(),
        output_capacity,
    );
    for (position, feature) in features.iter().enumerate() {
        let condensate = condensate_presence(feature);
        let explicit = explicit_cloud_presence(feature, condensate);
        let within_radius = feature[DISTANCE] <= diagnostic_radius_km;
        let rh_presence = clamp01((feature[RH] - 45.0) / 45.0);
        let presence = if within_radius {
            explicit.map(clamp01).unwrap_or(rh_presence)
        } else {
            0.0
        };
        let liquid = if within_radius {
            condensate_component(feature[CLOUD_LIQUID])
        } else {
            0.0
        };
        let ice = if within_radius {
            condensate_component(feature[CLOUD_ICE])
        } else {
            0.0
        };
        let cloud_opacity = feature_cloud_opacity(feature);
        let blocker_opacity = feature_blocker_opacity(feature);
        output[position] = [
            presence,
            if within_radius {
                explicit.unwrap_or(0.0)
            } else {
                0.0
            },
            liquid,
            ice,
            cloud_opacity,
            blocker_opacity,
            lightpath_opacity(feature, blocker_opacity),
            estimated_cloud_base(feature),
            apparent_cloud_elevation(feature[DISTANCE], feature[HEIGHT]),
        ];
    }
    features_len as isize
}

/// Calculate assessment metrics for each `hsd-assessment-feature-row-v2` row
/// (27 columns) without materializing Python rows. Output layout per row is
/// the same 9 metrics as `firesky_feature_metrics_batch`, but the surface/LCL
/// cloud base and the apparent top are observer-elevation aware, and the
/// satellite probability columns participate in presence/opacity. The legacy
/// 20-column ABI keeps its sea-level, probability-free semantics unchanged.
///
/// # Safety
///
/// `features_ptr` must be valid for `features_len * FEATURE_ROW_V2_FIELD_COUNT`
/// doubles and `output_ptr` for `output_capacity * METRIC_COUNT` doubles during
/// this call.
#[no_mangle]
pub unsafe extern "C" fn firesky_feature_metrics_batch_v2(
    features_ptr: *const f64,
    features_len: usize,
    diagnostic_radius_km: f64,
    observer_elevation_m: f64,
    output_ptr: *mut f64,
    output_capacity: usize,
) -> isize {
    if features_ptr.is_null() || output_ptr.is_null() {
        return -1;
    }
    if output_capacity < features_len {
        return -2;
    }
    let values = slice::from_raw_parts(features_ptr, features_len * FEATURE_ROW_V2_FIELD_COUNT);
    let features: &[FeatureRowV2] =
        slice::from_raw_parts(values.as_ptr().cast::<FeatureRowV2>(), features_len);
    let output_values = slice::from_raw_parts_mut(output_ptr, output_capacity * METRIC_COUNT);
    let output: &mut [[f64; METRIC_COUNT]] = slice::from_raw_parts_mut(
        output_values.as_mut_ptr().cast::<[f64; METRIC_COUNT]>(),
        output_capacity,
    );
    for (position, feature) in features.iter().enumerate() {
        output[position] =
            v2_feature_metrics_row(feature, diagnostic_radius_km, observer_elevation_m);
    }
    features_len as isize
}

fn candidate_prefilter_row(
    feature: &Feature,
    position: usize,
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
) -> Option<[f64; CANDIDATE_PREFILTER_FIELDS]> {
    let distance_km = feature[DISTANCE];
    let local_upper = distance_km < 35.0 && feature[PRESSURE] <= 600.0;
    if distance_km > MAX_ANALYSIS_RADIUS_KM || (distance_km < 35.0 && !local_upper) {
        return None;
    }
    let sector_delta_deg = angular_difference(feature[BEARING], solar_azimuth_deg);
    if sector_delta_deg > VIEW_AZIMUTH_HALF_DEG {
        return None;
    }
    let directional_distance_km = distance_km.max(0.0) * sector_delta_deg.to_radians().cos();
    let local_elevation_rad = solar_illumination_elevation_deg(solar_elevation_deg).to_radians()
        + directional_distance_km / EARTH_RADIUS_KM;
    let required_height_m = if local_elevation_rad >= 0.0 {
        0.0
    } else {
        let depression_rad = (-local_elevation_rad).min(20.0_f64.to_radians());
        EARTH_RADIUS_KM * 1000.0 * (1.0 / depression_rad.cos() - 1.0)
    };
    let cloud_base_m = estimated_cloud_base(feature);
    let raw_feasibility = (cloud_base_m - required_height_m + 1200.0) / 3600.0;
    if raw_feasibility <= -CANDIDATE_FEASIBILITY_EPSILON {
        return None;
    }
    let feasibility = clamp01(raw_feasibility);
    let apparent_elevation_deg = apparent_cloud_elevation(distance_km, cloud_base_m);
    let sight_weight = clamp01((apparent_elevation_deg - 0.1) / 1.4);
    if sight_weight <= 0.0 {
        return None;
    }
    let horizon_weight = if apparent_elevation_deg <= 0.1 {
        0.0
    } else if apparent_elevation_deg < 1.5 {
        clamp01((apparent_elevation_deg - 0.1) / 1.4)
    } else if apparent_elevation_deg <= 4.0 {
        1.0
    } else if apparent_elevation_deg < 12.0 {
        1.0 - 0.5 * (apparent_elevation_deg - 4.0) / 8.0
    } else {
        (0.5 - 0.25 * (apparent_elevation_deg - 12.0) / 12.0).clamp(0.25, 1.0)
    };
    Some([
        position as f64,
        sector_delta_deg,
        required_height_m,
        feasibility,
        apparent_elevation_deg,
        sight_weight,
        horizon_weight,
    ])
}

/// Filter and derive candidate-cloud geometry without creating Python objects.
/// Output rows contain feature position, sector delta, required illumination
/// height, feasibility, apparent elevation, sight weight, and horizon weight.
#[no_mangle]
pub unsafe extern "C" fn firesky_candidate_prefilter_batch(
    features_ptr: *const f64,
    features_len: usize,
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
    output_ptr: *mut f64,
    output_capacity: usize,
) -> isize {
    if features_ptr.is_null() || output_ptr.is_null() {
        return -1;
    }
    let values = slice::from_raw_parts(features_ptr, features_len * FIELD_COUNT);
    let features: &[[f64; FIELD_COUNT]] =
        slice::from_raw_parts(values.as_ptr().cast::<Feature>(), features_len);
    let output_values =
        slice::from_raw_parts_mut(output_ptr, output_capacity * CANDIDATE_PREFILTER_FIELDS);
    let output: &mut [[f64; CANDIDATE_PREFILTER_FIELDS]] = slice::from_raw_parts_mut(
        output_values
            .as_mut_ptr()
            .cast::<[f64; CANDIDATE_PREFILTER_FIELDS]>(),
        output_capacity,
    );
    let mut output_len = 0usize;
    for (position, feature) in features.iter().enumerate() {
        if let Some(row) =
            candidate_prefilter_row(feature, position, solar_elevation_deg, solar_azimuth_deg)
        {
            if output_len >= output_capacity {
                return -2;
            }
            output[output_len] = row;
            output_len += 1;
        }
    }
    output_len as isize
}

fn boundary_prefilter_row(
    feature: &Feature,
    position: usize,
    solar_azimuth_deg: f64,
) -> Option<[f64; BOUNDARY_PREFILTER_FIELDS]> {
    let distance_km = feature[DISTANCE];
    if feature[PRESSURE] >= 750.0 || !(40.0..=360.0).contains(&distance_km) {
        return None;
    }
    let sector_delta_deg = angular_difference(feature[BEARING], solar_azimuth_deg);
    if sector_delta_deg > 55.0 {
        return None;
    }
    let opacity = feature_cloud_opacity(feature);
    if opacity <= 0.0 {
        return None;
    }
    Some([position as f64, sector_delta_deg, opacity])
}

#[no_mangle]
pub unsafe extern "C" fn firesky_boundary_prefilter_batch(
    features_ptr: *const f64,
    features_len: usize,
    solar_azimuth_deg: f64,
    output_ptr: *mut f64,
    output_capacity: usize,
) -> isize {
    if features_ptr.is_null() || output_ptr.is_null() {
        return -1;
    }
    let values = slice::from_raw_parts(features_ptr, features_len * FIELD_COUNT);
    let features: &[[f64; FIELD_COUNT]] =
        slice::from_raw_parts(values.as_ptr().cast::<Feature>(), features_len);
    let output_values =
        slice::from_raw_parts_mut(output_ptr, output_capacity * BOUNDARY_PREFILTER_FIELDS);
    let output: &mut [[f64; BOUNDARY_PREFILTER_FIELDS]] = slice::from_raw_parts_mut(
        output_values
            .as_mut_ptr()
            .cast::<[f64; BOUNDARY_PREFILTER_FIELDS]>(),
        output_capacity,
    );
    let mut output_len = 0usize;
    for (position, feature) in features.iter().enumerate() {
        if let Some(row) = boundary_prefilter_row(feature, position, solar_azimuth_deg) {
            if output_len >= output_capacity {
                return -2;
            }
            output[output_len] = row;
            output_len += 1;
        }
    }
    output_len as isize
}

fn interpolate_presence_height(
    features: &[Feature],
    presences: &[f64],
    first: usize,
    second: usize,
) -> f64 {
    let score_delta = presences[second] - presences[first];
    if score_delta.abs() < 1e-6 {
        return 0.5 * (features[first][HEIGHT] + features[second][HEIGHT]);
    }
    let fraction = clamp01((0.45 - presences[first]) / score_delta);
    features[first][HEIGHT] + fraction * (features[second][HEIGHT] - features[first][HEIGHT])
}

/// Aggregate pressure-level features into compact cloud columns.
///
/// # Safety
///
/// Input and output pointers must be valid for their declared capacities. Each
/// output row contains `AGG_COLUMN_FIELDS` doubles.
#[no_mangle]
pub unsafe extern "C" fn firesky_cloud_columns_batch(
    features_ptr: *const f64,
    features_len: usize,
    max_distance_km: f64,
    output_ptr: *mut f64,
    output_capacity: usize,
) -> isize {
    if features_ptr.is_null() || output_ptr.is_null() {
        return -1;
    }
    let feature_values = slice::from_raw_parts(features_ptr, features_len * FIELD_COUNT);
    let features: &[[f64; FIELD_COUNT]] =
        slice::from_raw_parts(feature_values.as_ptr().cast::<Feature>(), features_len);
    let mut columns: Vec<ColumnAccumulator> = Vec::new();
    let mut positions: HashMap<(i64, i64, i64, i64), usize> = HashMap::new();
    for (position, feature) in features.iter().enumerate() {
        if feature[DISTANCE] > max_distance_km {
            continue;
        }
        let pressure = feature[PRESSURE].round() as i32;
        let Some(level_index) = pressure_level_index(pressure) else {
            continue;
        };
        let source_lat = source_or(feature, SOURCE_LAT, LAT);
        let source_lon = source_or(feature, SOURCE_LON, LON);
        let source_distance = source_or(feature, SOURCE_DISTANCE, DISTANCE);
        let source_bearing = source_or(feature, SOURCE_BEARING, BEARING);
        let key = (
            (source_lat * 10000.0).round() as i64,
            (source_lon * 10000.0).round() as i64,
            (source_distance * 10.0).round() as i64,
            (source_bearing * 10.0).round() as i64,
        );
        let column_position = *positions.entry(key).or_insert_with(|| {
            let index = columns.len();
            columns.push(ColumnAccumulator {
                key: (source_lat, source_lon, source_distance, source_bearing),
                reference: position,
                layers: [None; 11],
            });
            index
        });
        let column = &mut columns[column_position];
        if feature[DISTANCE] < features[column.reference][DISTANCE] {
            column.reference = position;
        }
        column.layers[level_index] = Some(position);
    }
    if output_capacity < columns.len() {
        return -2;
    }
    let output_values = slice::from_raw_parts_mut(output_ptr, output_capacity * AGG_COLUMN_FIELDS);
    let output: &mut [[f64; AGG_COLUMN_FIELDS]] = slice::from_raw_parts_mut(
        output_values
            .as_mut_ptr()
            .cast::<[f64; AGG_COLUMN_FIELDS]>(),
        output_capacity,
    );
    let presences: Vec<f64> = features
        .iter()
        .map(|feature| cloud_presence(feature))
        .collect();

    for (output_index, column) in columns.iter().enumerate() {
        let layer_indices: Vec<usize> = column.layers.iter().flatten().copied().collect();
        let max_presence = |levels: std::ops::Range<usize>| -> f64 {
            levels
                .filter_map(|index| column.layers[index])
                .map(|index| presences[index])
                .fold(0.0, f64::max)
        };
        let high_presence = max_presence(0..4);
        let mid_presence = max_presence(4..7);
        let low_presence = max_presence(7..11);
        let cloudy: Vec<usize> = layer_indices
            .iter()
            .copied()
            .filter(|index| presences[*index] >= 0.45)
            .collect();
        let (base_m, top_m) = if cloudy.is_empty() {
            (0.0, 0.0)
        } else {
            let mut ordered = layer_indices.clone();
            ordered.sort_by(|a, b| {
                features[*a][HEIGHT]
                    .partial_cmp(&features[*b][HEIGHT])
                    .unwrap_or(std::cmp::Ordering::Equal)
            });
            let lowest = *cloudy
                .iter()
                .min_by(|a, b| {
                    features[**a][HEIGHT]
                        .partial_cmp(&features[**b][HEIGHT])
                        .unwrap_or(std::cmp::Ordering::Equal)
                })
                .unwrap();
            let highest = *cloudy
                .iter()
                .max_by(|a, b| {
                    features[**a][HEIGHT]
                        .partial_cmp(&features[**b][HEIGHT])
                        .unwrap_or(std::cmp::Ordering::Equal)
                })
                .unwrap();
            let lower_dry = ordered
                .iter()
                .copied()
                .filter(|index| {
                    features[*index][HEIGHT] < features[lowest][HEIGHT] && presences[*index] < 0.45
                })
                .last();
            let upper_dry = ordered.iter().copied().find(|index| {
                features[*index][HEIGHT] > features[highest][HEIGHT] && presences[*index] < 0.45
            });
            (
                lower_dry
                    .map(|index| interpolate_presence_height(features, &presences, index, lowest))
                    .unwrap_or_else(|| estimated_cloud_base(&features[lowest])),
                upper_dry
                    .map(|index| interpolate_presence_height(features, &presences, highest, index))
                    .unwrap_or(features[highest][HEIGHT]),
            )
        };
        let base_m = base_m.max(0.0);
        let top_m = top_m.max(base_m);
        let depth_m = (top_m - base_m).max(0.0);
        let liquid_presence = layer_indices
            .iter()
            .map(|index| condensate_component(features[*index][CLOUD_LIQUID]))
            .fold(0.0, f64::max);
        let ice_presence = layer_indices
            .iter()
            .map(|index| condensate_component(features[*index][CLOUD_ICE]))
            .fold(0.0, f64::max);
        let explicit_evidence = layer_indices
            .iter()
            .filter_map(|index| {
                explicit_cloud_presence(&features[*index], condensate_presence(&features[*index]))
            })
            .fold(0.0, f64::max);
        let deep_risk = clamp01(
            0.45 * high_presence.min(mid_presence)
                + 0.35 * mid_presence.min(low_presence)
                + 0.20 * clamp01((cloudy.len() as f64 - 2.0) / 4.0),
        );
        let thickness_risk = clamp01((depth_m - 2800.0) / 5200.0);
        let strongest_high = (0..4).any(|index| {
            column.layers[index]
                .map(|position| presences[position] >= 0.45)
                .unwrap_or(false)
        });
        let strongest_lift = layer_indices
            .iter()
            .map(|index| features[*index][OMEGA])
            .filter(|value| !value.is_nan())
            .fold(0.0, f64::min);
        let has_explicit = layer_indices.iter().any(|index| {
            let feature = &features[*index];
            !feature[CLOUD_FRACTION].is_nan()
                || !feature[CLOUD_LIQUID].is_nan()
                || !feature[CLOUD_ICE].is_nan()
        });
        let cloud_type = if deep_risk >= 0.68 && low_presence >= 0.45 {
            1
        } else if deep_risk >= 0.58
            && strongest_lift <= -0.25
            && (!has_explicit || ice_presence >= 0.12)
        {
            2
        } else if low_presence >= 0.62 && high_presence < 0.35 && mid_presence < 0.45 {
            3
        } else if mid_presence >= 0.58 && high_presence < 0.45 && low_presence < 0.45 {
            4
        } else if high_presence >= 0.55 && mid_presence >= 0.45 {
            5
        } else if high_presence >= 0.50
            && strongest_high
            && (!has_explicit || ice_presence >= liquid_presence * 0.65)
        {
            6
        } else if mid_presence >= 0.45 {
            7
        } else if low_presence >= 0.45 {
            8
        } else {
            0
        };
        let base_score = match cloud_type {
            1 => 0.28,
            2 => 0.76,
            3 => 0.34,
            4 => 0.90,
            5 => 0.80,
            6 => 0.93,
            7 => 0.62,
            8 => 0.22,
            _ => 0.08,
        };
        let high_enough = high_presence.max(0.75 * mid_presence);
        let low_penalty = if cloud_type == 3 || cloud_type == 8 {
            0.0
        } else {
            0.28 * low_presence
        };
        let mut thick_penalty = 0.34 * deep_risk + 0.18 * thickness_risk;
        if cloud_type == 6 || cloud_type == 4 {
            thick_penalty *= 0.55;
        }
        if cloud_type == 1 {
            thick_penalty *= 1.35;
        }
        // Dynamic optical thickness factor (#7)
        let sensitivity: f64 = match cloud_type {
            6 => 0.70,
            4 => 0.60,
            5 => 0.50,
            7 => 0.40,
            2 => 0.25,
            _ => 0.35,
        };
        let thickness_optical =
            clamp01(1.0 - sensitivity * (0.30 * thickness_risk + 0.20 * deep_risk));
        let burnability = clamp01(
            base_score * thickness_optical * (0.55 + 0.45 * clamp01(high_enough))
                - low_penalty
                - thick_penalty,
        );
        let reference = &features[column.reference];
        let row = &mut output[output_index];
        row.fill(-1.0);
        row[0] = column.key.0;
        row[1] = column.key.1;
        row[2] = column.key.2;
        row[3] = column.key.3;
        row[4] = reference[LAT];
        row[5] = reference[LON];
        row[6] = reference[DISTANCE];
        row[7] = reference[BEARING];
        row[8] = high_presence;
        row[9] = mid_presence;
        row[10] = low_presence;
        row[11] = deep_risk;
        row[12] = thickness_risk;
        row[13] = cloud_type as f64;
        row[14] = burnability;
        row[15] = base_m;
        row[16] = top_m;
        row[17] = liquid_presence;
        row[18] = ice_presence;
        row[19] = explicit_evidence;
        for (level_index, position) in column.layers.iter().enumerate() {
            row[20 + level_index] = position.map(|value| value as f64).unwrap_or(-1.0);
        }
    }
    columns.len() as isize
}

pub fn destination_point(lat: f64, lon: f64, bearing_deg: f64, distance_km: f64) -> (f64, f64) {
    let angular_distance = distance_km / EARTH_RADIUS_KM;
    let bearing = bearing_deg.to_radians();
    let lat1 = lat.to_radians();
    let lon1 = lon.to_radians();
    let lat2 = (lat1.sin() * angular_distance.cos()
        + lat1.cos() * angular_distance.sin() * bearing.cos())
    .asin();
    let lon2 = lon1
        + (bearing.sin() * angular_distance.sin() * lat1.cos())
            .atan2(angular_distance.cos() - lat1.sin() * lat2.sin());
    (lat2.to_degrees(), normalize_lon(lon2.to_degrees()))
}

fn wind_trace_point(feature: &Feature, seconds: f64, forward: bool) -> (f64, f64, f64, f64) {
    let u = optional_or_zero(feature[U_WIND]);
    let v = optional_or_zero(feature[V_WIND]);
    let speed = u.hypot(v);
    let distance = speed * seconds.max(0.0) / 1000.0;
    let wind_bearing = if speed > 0.0 {
        u.atan2(v).to_degrees().rem_euclid(360.0)
    } else {
        0.0
    };
    let bearing = if forward {
        wind_bearing
    } else {
        (wind_bearing + 180.0).rem_euclid(360.0)
    };
    let (lat, lon) = destination_point(feature[LAT], feature[LON], bearing, distance);
    (lat, lon, distance, wind_bearing)
}

fn optional_or_zero(value: f64) -> f64 {
    if value.is_nan() {
        0.0
    } else {
        value
    }
}

fn weighted_optional(rows: &[(&Feature, f64)], field: usize) -> f64 {
    let mut weighted_sum = 0.0;
    let mut weight_sum = 0.0;
    for (feature, weight) in rows {
        let value = feature[field];
        if !value.is_nan() {
            weighted_sum += value * weight;
            weight_sum += weight;
        }
    }
    if weight_sum == 0.0 {
        f64::NAN
    } else {
        weighted_sum / weight_sum
    }
}

fn sample_grid(
    features: &[Feature],
    index: &HashMap<GridKey, usize>,
    pressure: i32,
    lat: f64,
    lon: f64,
    fallback: &Feature,
) -> Feature {
    let lat_scaled = lat * 4.0;
    let lon_scaled = normalize_lon(lon) * 4.0;
    let lat_low = lat_scaled.floor() as i32;
    let lon_low = lon_scaled.floor() as i32;
    let lat_fraction = lat_scaled - f64::from(lat_low);
    let lon_fraction = lon_scaled - f64::from(lon_low);
    let candidates = [
        (
            lat_low,
            lon_low,
            (1.0 - lat_fraction) * (1.0 - lon_fraction),
        ),
        (lat_low + 1, lon_low, lat_fraction * (1.0 - lon_fraction)),
        (lat_low, lon_low + 1, (1.0 - lat_fraction) * lon_fraction),
        (lat_low + 1, lon_low + 1, lat_fraction * lon_fraction),
    ];
    let mut rows: Vec<(&Feature, f64)> = Vec::with_capacity(4);
    for (lat_key, lon_key, weight) in candidates {
        if weight > 0.0 {
            if let Some(position) = index.get(&(pressure, lat_key, lon_key)) {
                rows.push((&features[*position], weight));
            }
        }
    }
    if rows.is_empty() {
        return *fallback;
    }
    let total_weight: f64 = rows.iter().map(|(_, weight)| weight).sum();
    let mut result = *fallback;
    result[HEIGHT] = rows
        .iter()
        .map(|(feature, weight)| feature[HEIGHT] * weight)
        .sum::<f64>()
        / total_weight;
    result[RH] = rows
        .iter()
        .map(|(feature, weight)| feature[RH] * weight)
        .sum::<f64>()
        / total_weight;
    for field in [
        TEMPERATURE,
        OMEGA,
        U_WIND,
        V_WIND,
        CLOUD_FRACTION,
        CLOUD_LIQUID,
        CLOUD_ICE,
        VISIBILITY,
        PRECIPITATION,
    ] {
        result[field] = weighted_optional(&rows, field);
    }
    result
}

fn interpolate_optional(left: f64, right: f64, weight: f64) -> f64 {
    match (left.is_nan(), right.is_nan()) {
        (true, true) => f64::NAN,
        (true, false) => right,
        (false, true) => left,
        (false, false) => left + (right - left) * weight,
    }
}

fn round_to(value: f64, digits: i32) -> f64 {
    let factor = 10_f64.powi(digits);
    (value * factor).round() / factor
}

/// Interpolate a flattened pair of weather grids into caller-owned output memory.
///
/// # Safety
///
/// Every pointer must be non-null and valid for the corresponding element count.
/// Input buffers must remain immutable for the duration of the call, and the output
/// buffer must hold at least `output_capacity * FIELD_COUNT` doubles.
#[no_mangle]
pub unsafe extern "C" fn firesky_interpolate_features(
    left_ptr: *const f64,
    left_len: usize,
    right_ptr: *const f64,
    right_len: usize,
    left_valid_seconds: f64,
    right_valid_seconds: f64,
    evaluation_seconds: f64,
    relevant_azimuth_deg: f64,
    local_radius_km: f64,
    output_ptr: *mut f64,
    output_capacity: usize,
    motion_ptr: *mut f64,
) -> isize {
    if left_ptr.is_null() || right_ptr.is_null() || output_ptr.is_null() || motion_ptr.is_null() {
        return -1;
    }
    let left_values = slice::from_raw_parts(left_ptr, left_len * FIELD_COUNT);
    let right_values = slice::from_raw_parts(right_ptr, right_len * FIELD_COUNT);
    let left: &[[f64; FIELD_COUNT]] =
        slice::from_raw_parts(left_values.as_ptr().cast::<Feature>(), left_len);
    let right: &[[f64; FIELD_COUNT]] =
        slice::from_raw_parts(right_values.as_ptr().cast::<Feature>(), right_len);
    let output_values = slice::from_raw_parts_mut(output_ptr, output_capacity * FIELD_COUNT);
    let output: &mut [[f64; FIELD_COUNT]] = slice::from_raw_parts_mut(
        output_values.as_mut_ptr().cast::<Feature>(),
        output_capacity,
    );
    let motion = slice::from_raw_parts_mut(motion_ptr, 2);

    let left_index: HashMap<GridKey, usize> = left
        .iter()
        .enumerate()
        .map(|(position, feature)| (grid_key(feature), position))
        .collect();
    let right_index: HashMap<GridKey, usize> = right
        .iter()
        .enumerate()
        .map(|(position, feature)| (grid_key(feature), position))
        .collect();
    let weight = if right_valid_seconds == left_valid_seconds {
        0.0
    } else {
        ((evaluation_seconds - left_valid_seconds) / (right_valid_seconds - left_valid_seconds))
            .clamp(0.0, 1.0)
    };
    let seconds_from_left = (evaluation_seconds - left_valid_seconds).max(0.0);
    let seconds_to_right = (right_valid_seconds - evaluation_seconds).max(0.0);
    let mut output_len = 0usize;
    let mut movement_weight = 0.0;
    let mut movement_distance = 0.0;
    let mut movement_east = 0.0;
    let mut movement_north = 0.0;

    for target in left {
        if !relevant_azimuth_deg.is_nan()
            && target[DISTANCE] > local_radius_km
            && angular_difference(target[BEARING], relevant_azimuth_deg) > 18.0
        {
            continue;
        }
        if output_len >= output_capacity {
            return -2;
        }
        let right_target = right_index
            .get(&grid_key(target))
            .map(|position| &right[*position])
            .unwrap_or(target);
        let (left_lat, left_lon, left_distance, left_bearing) =
            wind_trace_point(target, seconds_from_left, false);
        let (right_lat, right_lon, right_distance, right_bearing) =
            wind_trace_point(right_target, seconds_to_right, true);
        let pressure = target[PRESSURE].round() as i32;
        let left_feature = sample_grid(left, &left_index, pressure, left_lat, left_lon, target);
        let right_feature = sample_grid(
            right,
            &right_index,
            pressure,
            right_lat,
            right_lon,
            right_target,
        );
        let cloud_weight = 0.05_f64
            .max(optional_or_zero(target[CLOUD_FRACTION]) / 100.0)
            .max(target[RH] / 100.0);
        let left_motion = (1.0 - weight) * left_distance;
        let right_motion = weight * right_distance;
        let selected_bearing = if left_motion >= right_motion {
            left_bearing
        } else {
            right_bearing
        };
        let distance = left_motion + right_motion;
        movement_weight += cloud_weight;
        movement_distance += distance * cloud_weight;
        movement_east += distance * selected_bearing.to_radians().sin() * cloud_weight;
        movement_north += distance * selected_bearing.to_radians().cos() * cloud_weight;

        let result = &mut output[output_len];
        result.fill(f64::NAN);
        result[LAT] = target[LAT];
        result[LON] = target[LON];
        result[DISTANCE] = target[DISTANCE];
        result[BEARING] = target[BEARING];
        result[PRESSURE] = left_feature[PRESSURE];
        result[HEIGHT] =
            left_feature[HEIGHT] + (right_feature[HEIGHT] - left_feature[HEIGHT]) * weight;
        result[RH] = left_feature[RH] + (right_feature[RH] - left_feature[RH]) * weight;
        for field in [
            TEMPERATURE,
            OMEGA,
            U_WIND,
            V_WIND,
            CLOUD_FRACTION,
            CLOUD_LIQUID,
            CLOUD_ICE,
            VISIBILITY,
            PRECIPITATION,
        ] {
            result[field] = interpolate_optional(left_feature[field], right_feature[field], weight);
        }
        result[SOURCE_LAT] = round_to(
            if target[SOURCE_LAT].is_nan() {
                target[LAT]
            } else {
                target[SOURCE_LAT]
            },
            4,
        );
        result[SOURCE_LON] = round_to(
            if target[SOURCE_LON].is_nan() {
                target[LON]
            } else {
                target[SOURCE_LON]
            },
            4,
        );
        result[SOURCE_DISTANCE] = round_to(
            if target[SOURCE_DISTANCE].is_nan() {
                target[DISTANCE]
            } else {
                target[SOURCE_DISTANCE]
            },
            1,
        );
        result[SOURCE_BEARING] = round_to(
            if target[SOURCE_BEARING].is_nan() {
                target[BEARING]
            } else {
                target[SOURCE_BEARING]
            },
            1,
        );
        output_len += 1;
    }

    let total_weight = if movement_weight == 0.0 {
        1.0
    } else {
        movement_weight
    };
    motion[0] = movement_distance / total_weight;
    motion[1] = if movement_east.hypot(movement_north) > 0.01 {
        movement_east
            .atan2(movement_north)
            .to_degrees()
            .rem_euclid(360.0)
    } else {
        0.0
    };
    output_len as isize
}

/// Interpolate using reusable lookup handles created by `firesky_grid_index_create`.
///
/// # Safety
///
/// Buffer pointers, index handles, and output pointers must all be live and must
/// describe the same immutable buffers used to create the handles.
#[no_mangle]
pub unsafe extern "C" fn firesky_interpolate_features_indexed(
    left_ptr: *const f64,
    left_len: usize,
    left_index_ptr: *const NativeGridIndex,
    right_ptr: *const f64,
    right_len: usize,
    right_index_ptr: *const NativeGridIndex,
    left_valid_seconds: f64,
    right_valid_seconds: f64,
    evaluation_seconds: f64,
    relevant_azimuth_deg: f64,
    local_radius_km: f64,
    output_ptr: *mut f64,
    output_capacity: usize,
    motion_ptr: *mut f64,
) -> isize {
    if left_ptr.is_null()
        || right_ptr.is_null()
        || left_index_ptr.is_null()
        || right_index_ptr.is_null()
        || output_ptr.is_null()
        || motion_ptr.is_null()
    {
        return -1;
    }
    let left_index = &*left_index_ptr;
    let right_index = &*right_index_ptr;
    if left_index.feature_len != left_len || right_index.feature_len != right_len {
        return -3;
    }
    let left_values = slice::from_raw_parts(left_ptr, left_len * FIELD_COUNT);
    let right_values = slice::from_raw_parts(right_ptr, right_len * FIELD_COUNT);
    let left: &[[f64; FIELD_COUNT]] =
        slice::from_raw_parts(left_values.as_ptr().cast::<Feature>(), left_len);
    let right: &[[f64; FIELD_COUNT]] =
        slice::from_raw_parts(right_values.as_ptr().cast::<Feature>(), right_len);
    let output_values = slice::from_raw_parts_mut(output_ptr, output_capacity * FIELD_COUNT);
    let output: &mut [[f64; FIELD_COUNT]] = slice::from_raw_parts_mut(
        output_values.as_mut_ptr().cast::<Feature>(),
        output_capacity,
    );
    let motion = slice::from_raw_parts_mut(motion_ptr, 2);
    let weight = if right_valid_seconds == left_valid_seconds {
        0.0
    } else {
        ((evaluation_seconds - left_valid_seconds) / (right_valid_seconds - left_valid_seconds))
            .clamp(0.0, 1.0)
    };
    let seconds_from_left = (evaluation_seconds - left_valid_seconds).max(0.0);
    let seconds_to_right = (right_valid_seconds - evaluation_seconds).max(0.0);
    let mut output_len = 0usize;
    let mut movement_weight = 0.0;
    let mut movement_distance = 0.0;
    let mut movement_east = 0.0;
    let mut movement_north = 0.0;

    for target in left {
        if !relevant_azimuth_deg.is_nan()
            && target[DISTANCE] > local_radius_km
            && angular_difference(target[BEARING], relevant_azimuth_deg) > 18.0
        {
            continue;
        }
        if output_len >= output_capacity {
            return -2;
        }
        let right_target = right_index
            .by_key
            .get(&grid_key(target))
            .map(|position| &right[*position])
            .unwrap_or(target);
        let (left_lat, left_lon, left_distance, left_bearing) =
            wind_trace_point(target, seconds_from_left, false);
        let (right_lat, right_lon, right_distance, right_bearing) =
            wind_trace_point(right_target, seconds_to_right, true);
        let pressure = target[PRESSURE].round() as i32;
        let left_feature = sample_grid(
            left,
            &left_index.by_key,
            pressure,
            left_lat,
            left_lon,
            target,
        );
        let right_feature = sample_grid(
            right,
            &right_index.by_key,
            pressure,
            right_lat,
            right_lon,
            right_target,
        );
        let cloud_weight = 0.05_f64
            .max(optional_or_zero(target[CLOUD_FRACTION]) / 100.0)
            .max(target[RH] / 100.0);
        let left_motion = (1.0 - weight) * left_distance;
        let right_motion = weight * right_distance;
        let selected_bearing = if left_motion >= right_motion {
            left_bearing
        } else {
            right_bearing
        };
        let distance = left_motion + right_motion;
        movement_weight += cloud_weight;
        movement_distance += distance * cloud_weight;
        movement_east += distance * selected_bearing.to_radians().sin() * cloud_weight;
        movement_north += distance * selected_bearing.to_radians().cos() * cloud_weight;

        let result = &mut output[output_len];
        result.fill(f64::NAN);
        result[LAT] = target[LAT];
        result[LON] = target[LON];
        result[DISTANCE] = target[DISTANCE];
        result[BEARING] = target[BEARING];
        result[PRESSURE] = left_feature[PRESSURE];
        result[HEIGHT] =
            left_feature[HEIGHT] + (right_feature[HEIGHT] - left_feature[HEIGHT]) * weight;
        result[RH] = left_feature[RH] + (right_feature[RH] - left_feature[RH]) * weight;
        for field in [
            TEMPERATURE,
            OMEGA,
            U_WIND,
            V_WIND,
            CLOUD_FRACTION,
            CLOUD_LIQUID,
            CLOUD_ICE,
            VISIBILITY,
            PRECIPITATION,
        ] {
            result[field] = interpolate_optional(left_feature[field], right_feature[field], weight);
        }
        result[SOURCE_LAT] = round_to(
            if target[SOURCE_LAT].is_nan() {
                target[LAT]
            } else {
                target[SOURCE_LAT]
            },
            4,
        );
        result[SOURCE_LON] = round_to(
            if target[SOURCE_LON].is_nan() {
                target[LON]
            } else {
                target[SOURCE_LON]
            },
            4,
        );
        result[SOURCE_DISTANCE] = round_to(
            if target[SOURCE_DISTANCE].is_nan() {
                target[DISTANCE]
            } else {
                target[SOURCE_DISTANCE]
            },
            1,
        );
        result[SOURCE_BEARING] = round_to(
            if target[SOURCE_BEARING].is_nan() {
                target[BEARING]
            } else {
                target[SOURCE_BEARING]
            },
            1,
        );
        output_len += 1;
    }

    let total_weight = if movement_weight == 0.0 {
        1.0
    } else {
        movement_weight
    };
    motion[0] = movement_distance / total_weight;
    motion[1] = if movement_east.hypot(movement_north) > 0.01 {
        movement_east
            .atan2(movement_north)
            .to_degrees()
            .rem_euclid(360.0)
    } else {
        0.0
    };
    output_len as isize
}

/// Return the exact output row count for the interpolation spatial filter.
///
/// # Safety
///
/// `features_ptr` must be non-null and valid for `features_len * FIELD_COUNT` doubles.
#[no_mangle]
pub unsafe extern "C" fn firesky_interpolation_output_len(
    features_ptr: *const f64,
    features_len: usize,
    relevant_azimuth_deg: f64,
    local_radius_km: f64,
) -> isize {
    if features_ptr.is_null() {
        return -1;
    }
    let feature_values = slice::from_raw_parts(features_ptr, features_len * FIELD_COUNT);
    let features: &[[f64; FIELD_COUNT]] =
        slice::from_raw_parts(feature_values.as_ptr().cast::<Feature>(), features_len);
    features
        .iter()
        .filter(|feature| {
            relevant_azimuth_deg.is_nan()
                || feature[DISTANCE] <= local_radius_km
                || angular_difference(feature[BEARING], relevant_azimuth_deg) <= 18.0
        })
        .count() as isize
}

/// Compute all candidate path-clearance weights in one native call.
///
/// # Safety
///
/// Input pointers must be valid for their declared lengths. `output_ptr` must
/// hold at least `output_capacity` doubles.
#[no_mangle]
pub unsafe extern "C" fn firesky_path_clearance_batch(
    features_ptr: *const f64,
    features_len: usize,
    candidate_indices_ptr: *const usize,
    candidate_len: usize,
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
    output_ptr: *mut f64,
    output_capacity: usize,
) -> isize {
    if features_ptr.is_null()
        || candidate_indices_ptr.is_null()
        || output_ptr.is_null()
        || output_capacity < candidate_len
    {
        return -1;
    }

    let feature_values = slice::from_raw_parts(features_ptr, features_len * FIELD_COUNT);
    let features: &[[f64; FIELD_COUNT]] =
        slice::from_raw_parts(feature_values.as_ptr().cast::<Feature>(), features_len);
    let candidate_indices = slice::from_raw_parts(candidate_indices_ptr, candidate_len);
    let output = slice::from_raw_parts_mut(output_ptr, candidate_len);

    let blocker_opacities: Vec<f64> = features
        .iter()
        .map(|feature| feature_blocker_opacity(feature))
        .collect();
    let lightpath_opacities: Vec<f64> = features
        .iter()
        .zip(blocker_opacities.iter())
        .map(|(feature, opacity)| lightpath_opacity(feature, *opacity))
        .collect();
    let cloud_bases: Vec<f64> = features
        .iter()
        .map(|feature| estimated_cloud_base(feature))
        .collect();
    let apparent_tops: Vec<f64> = features
        .iter()
        .map(|feature| apparent_cloud_elevation(feature[DISTANCE], feature[HEIGHT]))
        .collect();

    let mut blocker_bins: Vec<Vec<usize>> = (0..180).map(|_| Vec::new()).collect();
    for (index, feature) in features.iter().enumerate() {
        if blocker_opacities[index] > 0.12 {
            let key = ((feature[BEARING].rem_euclid(360.0) / 2.0).floor() as usize) % 180;
            blocker_bins[key].push(index);
        }
    }

    for (output_index, candidate_index) in candidate_indices.iter().copied().enumerate() {
        if candidate_index >= features_len {
            return -2;
        }
        let candidate = &features[candidate_index];
        let mut legacy_risk: f64 = 0.0;
        let mut sightline_risk: f64 = 0.0;
        let mut inflow_risk: f64 = 0.0;

        for (key, bin) in blocker_bins.iter().enumerate() {
            let center = (key as f64 + 0.5) * 2.0;
            let target_bin_matches = angular_difference(center, candidate[BEARING]) <= 17.0;
            let solar_bin_matches = angular_difference(center, solar_azimuth_deg) <= 19.0;
            if !target_bin_matches {
                continue;
            }
            for blocker_index in bin.iter().copied() {
                if blocker_index == candidate_index {
                    continue;
                }
                let blocker = &features[blocker_index];
                let target_delta = angular_difference(blocker[BEARING], candidate[BEARING]);
                let blocker_distance = blocker[DISTANCE];

                if blocker[PRESSURE] > candidate[PRESSURE]
                    && blocker_distance >= 20.0
                    && blocker_distance <= candidate[DISTANCE] + 220.0
                    && target_delta <= 12.0
                {
                    let opacity = blocker_opacities[blocker_index];
                    if blocker_distance < candidate[DISTANCE] {
                        if apparent_tops[blocker_index]
                            >= apparent_cloud_elevation(
                                candidate[DISTANCE],
                                cloud_bases[candidate_index],
                            ) - 0.25
                        {
                            let distance_factor =
                                clamp01(1.0 - blocker_distance / candidate[DISTANCE].max(1.0));
                            legacy_risk =
                                legacy_risk.max(opacity * (0.55 + 0.45 * distance_factor));
                        }
                    } else if blocker_distance <= candidate[DISTANCE] + 220.0
                        && blocker[HEIGHT] <= cloud_bases[candidate_index] + 1200.0
                    {
                        let distance_factor =
                            clamp01(1.0 - (blocker_distance - candidate[DISTANCE]) / 220.0);
                        legacy_risk = legacy_risk.max(0.65 * opacity * distance_factor);
                    }
                }

                if blocker_distance >= 15.0
                    && blocker_distance <= (candidate[DISTANCE] - 5.0).max(15.0)
                    && blocker_distance < candidate[DISTANCE] - 5.0
                    && target_delta <= 14.0
                {
                    let opacity = lightpath_opacities[blocker_index];
                    let ray_height = sightline_height(
                        candidate[DISTANCE],
                        cloud_bases[candidate_index],
                        blocker_distance,
                    );
                    let intersection = vertical_intersection(
                        ray_height,
                        cloud_bases[blocker_index],
                        blocker[HEIGHT],
                        650.0,
                    );
                    if opacity > 0.0 && intersection > 0.0 {
                        let distance_factor =
                            clamp01(1.0 - blocker_distance / candidate[DISTANCE].max(1.0));
                        sightline_risk = sightline_risk
                            .max(opacity * intersection * (0.55 + 0.45 * distance_factor));
                    }
                }

                if solar_bin_matches
                    && blocker_distance > candidate[DISTANCE] + 8.0
                    && blocker_distance <= MAX_ANALYSIS_RADIUS_KM
                {
                    let gap_km = blocker_distance - candidate[DISTANCE];
                    let bearing_tolerance = if gap_km <= 360.0 { 16.0 } else { 8.0 };
                    let solar_tolerance = if gap_km <= 360.0 { 18.0 } else { 10.0 };
                    let solar_delta = angular_difference(blocker[BEARING], solar_azimuth_deg);
                    if target_delta <= bearing_tolerance && solar_delta <= solar_tolerance {
                        let opacity = lightpath_opacities[blocker_index];
                        let ray_height = solar_ray_height(
                            candidate[DISTANCE],
                            cloud_bases[candidate_index],
                            blocker_distance,
                            solar_elevation_deg,
                        );
                        let intersection = vertical_intersection(
                            ray_height,
                            cloud_bases[blocker_index],
                            blocker[HEIGHT],
                            850.0,
                        );
                        if opacity > 0.0 && intersection > 0.0 {
                            let distance_factor =
                                clamp01(1.0 - (gap_km - 20.0) / (MAX_ANALYSIS_RADIUS_KM - 20.0));
                            let alignment = 1.0
                                - (target_delta / bearing_tolerance)
                                    .max(solar_delta / solar_tolerance);
                            let far_factor = if gap_km <= 360.0 { 1.0 } else { 0.72 };
                            inflow_risk = inflow_risk.max(
                                opacity
                                    * intersection
                                    * clamp01(alignment)
                                    * (0.45 + 0.55 * distance_factor)
                                    * far_factor,
                            );
                        }
                    }
                }
            }
        }
        output[output_index] = clamp01(1.0 - legacy_risk.max(sightline_risk).max(inflow_risk));
    }

    candidate_len as isize
}

/// Compute boundary-cloud path clearance using per-target diagnostic cloud bases.
///
/// # Safety
///
/// Input pointers must be valid for their declared lengths. `output_ptr` must
/// hold at least `output_capacity` doubles.
#[no_mangle]
pub unsafe extern "C" fn firesky_boundary_clearance_batch(
    features_ptr: *const f64,
    features_len: usize,
    target_indices_ptr: *const usize,
    target_heights_ptr: *const f64,
    target_len: usize,
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
    output_ptr: *mut f64,
    output_capacity: usize,
) -> isize {
    if features_ptr.is_null()
        || target_indices_ptr.is_null()
        || target_heights_ptr.is_null()
        || output_ptr.is_null()
        || output_capacity < target_len
    {
        return -1;
    }

    let feature_values = slice::from_raw_parts(features_ptr, features_len * FIELD_COUNT);
    let features: &[[f64; FIELD_COUNT]] =
        slice::from_raw_parts(feature_values.as_ptr().cast::<Feature>(), features_len);
    let target_indices = slice::from_raw_parts(target_indices_ptr, target_len);
    let target_heights = slice::from_raw_parts(target_heights_ptr, target_len);
    let output = slice::from_raw_parts_mut(output_ptr, target_len);

    let blocker_opacities: Vec<f64> = features
        .iter()
        .map(|feature| feature_blocker_opacity(feature))
        .collect();
    let lightpath_opacities: Vec<f64> = features
        .iter()
        .zip(blocker_opacities.iter())
        .map(|(feature, opacity)| lightpath_opacity(feature, *opacity))
        .collect();
    let cloud_bases: Vec<f64> = features
        .iter()
        .map(|feature| estimated_cloud_base(feature))
        .collect();
    let apparent_tops: Vec<f64> = features
        .iter()
        .map(|feature| apparent_cloud_elevation(feature[DISTANCE], feature[HEIGHT]))
        .collect();
    let mut blocker_bins: Vec<Vec<usize>> = (0..180).map(|_| Vec::new()).collect();
    for (index, feature) in features.iter().enumerate() {
        if blocker_opacities[index] > 0.12 {
            let key = ((feature[BEARING].rem_euclid(360.0) / 2.0).floor() as usize) % 180;
            blocker_bins[key].push(index);
        }
    }

    for output_index in 0..target_len {
        let target_index = target_indices[output_index];
        if target_index >= features_len {
            return -2;
        }
        let target = &features[target_index];
        let target_height = target_heights[output_index];
        let target_elevation = apparent_cloud_elevation(target[DISTANCE], target_height);
        let mut legacy_risk: f64 = 0.0;
        let mut sightline_risk: f64 = 0.0;
        let mut inflow_risk: f64 = 0.0;

        for (key, bin) in blocker_bins.iter().enumerate() {
            let center = (key as f64 + 0.5) * 2.0;
            let target_bin_matches = angular_difference(center, target[BEARING]) <= 17.0;
            let solar_bin_matches = angular_difference(center, solar_azimuth_deg) <= 19.0;
            if !target_bin_matches {
                continue;
            }
            for blocker_index in bin.iter().copied() {
                if blocker_index == target_index {
                    continue;
                }
                let blocker = &features[blocker_index];
                let blocker_distance = blocker[DISTANCE];
                let target_delta = angular_difference(blocker[BEARING], target[BEARING]);

                if blocker_distance >= 15.0
                    && blocker_distance < target[DISTANCE] - 5.0
                    && target_delta <= 16.0
                {
                    let blocker_base = cloud_bases[blocker_index];
                    if apparent_tops[blocker_index] >= target_elevation - 0.35
                        || blocker_base <= target_height + 800.0
                    {
                        let distance_factor =
                            clamp01(1.0 - blocker_distance / target[DISTANCE].max(1.0));
                        let layer_factor = if blocker[PRESSURE] >= target[PRESSURE] {
                            1.0
                        } else {
                            0.55
                        };
                        legacy_risk = legacy_risk.max(
                            blocker_opacities[blocker_index]
                                * layer_factor
                                * (0.55 + 0.45 * distance_factor),
                        );
                    }
                }

                if blocker_distance >= 15.0
                    && blocker_distance < target[DISTANCE] - 5.0
                    && target_delta <= 14.0
                {
                    let opacity = lightpath_opacities[blocker_index];
                    let ray_height =
                        sightline_height(target[DISTANCE], target_height, blocker_distance);
                    let intersection = vertical_intersection(
                        ray_height,
                        cloud_bases[blocker_index],
                        blocker[HEIGHT],
                        650.0,
                    );
                    if opacity > 0.0 && intersection > 0.0 {
                        let distance_factor =
                            clamp01(1.0 - blocker_distance / target[DISTANCE].max(1.0));
                        sightline_risk = sightline_risk
                            .max(opacity * intersection * (0.55 + 0.45 * distance_factor));
                    }
                }

                if solar_bin_matches
                    && blocker_distance > target[DISTANCE] + 8.0
                    && blocker_distance <= MAX_ANALYSIS_RADIUS_KM
                {
                    let gap_km = blocker_distance - target[DISTANCE];
                    let bearing_tolerance = if gap_km <= 360.0 { 16.0 } else { 8.0 };
                    let solar_tolerance = if gap_km <= 360.0 { 18.0 } else { 10.0 };
                    let solar_delta = angular_difference(blocker[BEARING], solar_azimuth_deg);
                    if target_delta <= bearing_tolerance && solar_delta <= solar_tolerance {
                        let opacity = lightpath_opacities[blocker_index];
                        let ray_height = solar_ray_height(
                            target[DISTANCE],
                            target_height,
                            blocker_distance,
                            solar_elevation_deg,
                        );
                        let intersection = vertical_intersection(
                            ray_height,
                            cloud_bases[blocker_index],
                            blocker[HEIGHT],
                            850.0,
                        );
                        if opacity > 0.0 && intersection > 0.0 {
                            let distance_factor =
                                clamp01(1.0 - (gap_km - 20.0) / (MAX_ANALYSIS_RADIUS_KM - 20.0));
                            let alignment = 1.0
                                - (target_delta / bearing_tolerance)
                                    .max(solar_delta / solar_tolerance);
                            let far_factor = if gap_km <= 360.0 { 1.0 } else { 0.72 };
                            inflow_risk = inflow_risk.max(
                                opacity
                                    * intersection
                                    * clamp01(alignment)
                                    * (0.45 + 0.55 * distance_factor)
                                    * far_factor,
                            );
                        }
                    }
                }
            }
        }
        output[output_index] = clamp01(1.0 - legacy_risk.max(sightline_risk).max(inflow_risk));
    }

    target_len as isize
}

/// Compute boundary edge contrast and clear-slot score for a batch of targets.
/// Output contains two doubles per target: edge contrast, then clear-slot score.
///
/// # Safety
///
/// Input pointers must be valid for their declared lengths and `output_ptr` must
/// hold at least `target_len * 2` doubles.
#[no_mangle]
pub unsafe extern "C" fn firesky_boundary_context_batch(
    features_ptr: *const f64,
    features_len: usize,
    target_indices_ptr: *const usize,
    target_len: usize,
    solar_azimuth_deg: f64,
    output_ptr: *mut f64,
    output_capacity: usize,
) -> isize {
    if features_ptr.is_null()
        || target_indices_ptr.is_null()
        || output_ptr.is_null()
        || output_capacity < target_len
    {
        return -1;
    }
    let feature_values = slice::from_raw_parts(features_ptr, features_len * FIELD_COUNT);
    let features: &[[f64; FIELD_COUNT]] =
        slice::from_raw_parts(feature_values.as_ptr().cast::<Feature>(), features_len);
    let target_indices = slice::from_raw_parts(target_indices_ptr, target_len);
    let output_values = slice::from_raw_parts_mut(output_ptr, target_len * 2);
    let output: &mut [[f64; 2]] =
        slice::from_raw_parts_mut(output_values.as_mut_ptr().cast::<[f64; 2]>(), target_len);
    let cloud_opacities: Vec<f64> = features
        .iter()
        .map(|feature| feature_cloud_opacity(feature))
        .collect();
    let blocker_opacities: Vec<f64> = features
        .iter()
        .map(|feature| feature_blocker_opacity(feature))
        .collect();
    // pressure_bins: pressure_level → bearing_bin(2°) → Vec<feature_index>
    let mut pressure_bins: HashMap<i32, [Vec<usize>; 180]> = HashMap::new();
    let mut blocker_bins: Vec<Vec<usize>> = (0..180).map(|_| Vec::new()).collect();
    for (index, feature) in features.iter().enumerate() {
        let pkey = feature[PRESSURE].round() as i32;
        let bkey = ((feature[BEARING].rem_euclid(360.0) / 2.0).floor() as usize) % 180;
        pressure_bins
            .entry(pkey)
            .or_insert_with(|| std::array::from_fn(|_| Vec::new()))[bkey]
            .push(index);
        if blocker_opacities[index] > 0.12 {
            blocker_bins[bkey].push(index);
        }
    }

    for (output_index, target_index) in target_indices.iter().copied().enumerate() {
        if target_index >= features_len {
            return -2;
        }
        let target = &features[target_index];
        let target_distance = target[DISTANCE];
        let target_bearing = target[BEARING];
        let mut neighbor_count = 0usize;
        let mut clearest_neighbor = f64::INFINITY;
        let pkey = target[PRESSURE].round() as i32;
        let center_bin =
            ((target_bearing.rem_euclid(360.0) / 2.0).floor() as isize).rem_euclid(180);
        if let Some(bins) = pressure_bins.get(&pkey) {
            // Scan ±14 bins (covers 28° bearing tolerance)
            for offset in -14isize..=14 {
                let bin = ((center_bin + offset).rem_euclid(180)) as usize;
                for &neighbor_index in bins[bin].iter() {
                    if neighbor_index == target_index {
                        continue;
                    }
                    let neighbor = &features[neighbor_index];
                    let sector_delta = angular_difference(neighbor[BEARING], target_bearing);
                    let distance_delta = (neighbor[DISTANCE] - target_distance).abs();
                    let matches = (sector_delta <= 28.0 && distance_delta <= 95.0)
                        || (sector_delta <= 16.0
                            && neighbor[DISTANCE] >= 20.0
                            && neighbor[DISTANCE] < target_distance - 12.0);
                    if matches {
                        neighbor_count += 1;
                        clearest_neighbor = clearest_neighbor.min(cloud_opacities[neighbor_index]);
                    }
                }
            }
        }
        let edge_contrast = if neighbor_count < 3 {
            0.72
        } else {
            clamp01((cloud_opacities[target_index] - clearest_neighbor) / 0.72)
        };

        let mut max_penalty: f64 = 0.0;
        for (key, bin) in blocker_bins.iter().enumerate() {
            let center = (key as f64 + 0.5) * 2.0;
            if angular_difference(center, target_bearing) > 19.0
                && angular_difference(center, solar_azimuth_deg) > 23.0
            {
                continue;
            }
            for blocker_index in bin.iter().copied() {
                if blocker_index == target_index {
                    continue;
                }
                let blocker = &features[blocker_index];
                let blocker_distance = blocker[DISTANCE];
                if blocker[PRESSURE] < target[PRESSURE]
                    || blocker_distance < 15.0
                    || blocker_distance >= target_distance - 8.0
                {
                    continue;
                }
                if angular_difference(blocker[BEARING], target_bearing) > 18.0
                    && angular_difference(blocker[BEARING], solar_azimuth_deg) > 22.0
                {
                    continue;
                }
                let distance_factor = clamp01(1.0 - blocker_distance / target_distance.max(1.0));
                let lower_layer_factor = if blocker[PRESSURE] >= 700.0 {
                    1.0
                } else {
                    0.72
                };
                max_penalty = max_penalty.max(
                    blocker_opacities[blocker_index]
                        * lower_layer_factor
                        * (0.55 + 0.45 * distance_factor),
                );
            }
        }
        output[output_index] = [edge_contrast, clamp01(1.0 - max_penalty)];
    }
    target_len as isize
}

pub fn aerosol_transmission_samples_impl(
    aod550: f64,
    observer_aod550: f64,
    inflow_aod550: f64,
    target_distance_km: f64,
    target_height_m: f64,
    apparent_elevation_deg: f64,
    solar_elevation_deg: f64,
    observer_elevation_m: f64,
) -> [f64; 3] {
    let mut output = [0.0; 3];
    let elevation_rad = apparent_elevation_deg.max(2.0).to_radians();
    let air_mass = (1.0 / elevation_rad.sin().max(0.08)).min(12.0);
    let legacy = clamp01(0.50 + 0.50 * (-0.45 * aod550 * air_mass).exp());

    // Dynamic mixing: atmosphere column fractions
    let above_fraction = aerosol_column_above_fraction(target_height_m, observer_elevation_m);
    let below_fraction = 1.0 - above_fraction;
    let obs_airmass = below_fraction / elevation_rad.sin().max(0.08);
    let sol_rad = solar_elevation_deg.to_radians();
    let inflow_airmass = if sol_rad > 0.0 {
        let total_am = 1.0 / sol_rad.sin().max(0.05);
        total_am * above_fraction
    } else {
        let depression_deg = (-solar_elevation_deg).min(20.0);
        50.0 / (depression_deg + 2.0) * above_fraction
    };
    let total = obs_airmass + inflow_airmass;
    let observer_weight = if total > 0.0 {
        (obs_airmass / total).clamp(0.20, 0.60)
    } else {
        0.30
    };
    let inflow_weight = 1.0 - observer_weight;

    for (index, scale_height_m) in [500.0, 1800.0, 4000.0].iter().copied().enumerate() {
        let observer_tau = observer_aerosol_tau(
            observer_aod550,
            target_distance_km,
            target_height_m,
            scale_height_m,
            observer_elevation_m,
        );
        let inflow_tau = solar_inflow_aerosol_tau(
            inflow_aod550,
            target_distance_km,
            target_height_m,
            solar_elevation_deg,
            scale_height_m,
            observer_elevation_m,
        );
        let combined_tau = observer_weight * observer_tau + inflow_weight * inflow_tau;
        let transmission = (-0.45 * combined_tau.min(12.0)).exp();
        output[index] = (0.35 + 0.65 * transmission).clamp(0.0, legacy);
    }
    output
}

/// Calculate three aerosol light-path transmission samples.
///
/// # Safety
///
/// `output_ptr` must be non-null and hold at least three doubles.
#[no_mangle]
pub unsafe extern "C" fn firesky_aerosol_transmission_samples(
    aod550: f64,
    observer_aod550: f64,
    inflow_aod550: f64,
    target_distance_km: f64,
    target_height_m: f64,
    apparent_elevation_deg: f64,
    solar_elevation_deg: f64,
    observer_elevation_m: f64,
    output_ptr: *mut f64,
    output_capacity: usize,
) -> isize {
    if output_ptr.is_null() || output_capacity < 3 {
        return -1;
    }
    let output = slice::from_raw_parts_mut(output_ptr, 3);
    output.copy_from_slice(&aerosol_transmission_samples_impl(
        aod550,
        observer_aod550,
        inflow_aod550,
        target_distance_km,
        target_height_m,
        apparent_elevation_deg,
        solar_elevation_deg,
        observer_elevation_m,
    ));
    3
}

/// Batch aerosol transmission scoring. Each input row contains eight parameters;
/// each output row contains three samples, their robust mean weight, and spread.
///
/// # Safety
///
/// Input and output pointers must be valid for their declared row counts.
#[no_mangle]
pub unsafe extern "C" fn firesky_aerosol_transmission_batch(
    input_ptr: *const f64,
    input_len: usize,
    output_ptr: *mut f64,
    output_capacity: usize,
) -> isize {
    const INPUT_FIELDS: usize = 8;
    const OUTPUT_FIELDS: usize = 5;
    if input_ptr.is_null() || output_ptr.is_null() {
        return -1;
    }
    if output_capacity < input_len {
        return -2;
    }
    let inputs = slice::from_raw_parts(input_ptr, input_len * INPUT_FIELDS);
    let outputs = slice::from_raw_parts_mut(output_ptr, output_capacity * OUTPUT_FIELDS);
    for row_index in 0..input_len {
        let input = &inputs[row_index * INPUT_FIELDS..(row_index + 1) * INPUT_FIELDS];
        let samples = aerosol_transmission_samples_impl(
            input[0], input[1], input[2], input[3], input[4], input[5], input[6], input[7],
        );
        let minimum = samples.iter().copied().fold(f64::INFINITY, f64::min);
        let maximum = samples.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        let spread = maximum - minimum;
        let weight = ((samples[0] + samples[1] + samples[2]) / 3.0 - 0.15 * spread).clamp(0.0, 1.0);
        let output = &mut outputs[row_index * OUTPUT_FIELDS..(row_index + 1) * OUTPUT_FIELDS];
        output[..3].copy_from_slice(&samples);
        output[3] = weight;
        output[4] = spread.clamp(0.0, 1.0);
    }
    input_len as isize
}

#[cfg(test)]
mod aerosol_tests {
    use super::*;

    fn fine_solar_inflow_tau(
        aod550: f64,
        target_distance_km: f64,
        target_height_m: f64,
        solar_elevation_deg: f64,
        scale_height_m: f64,
    ) -> f64 {
        let stop_distance_km = MAX_ANALYSIS_RADIUS_KM.max(target_distance_km + 20.0);
        let steps = 20_000;
        let mut tau = 0.0;
        let mut previous_distance_km = target_distance_km;
        let mut previous_height_m = target_height_m;
        let mut previous_beta =
            aod550 / scale_height_m * (-previous_height_m / scale_height_m).exp();
        for index in 1..=steps {
            let distance_km = target_distance_km
                + (stop_distance_km - target_distance_km) * index as f64 / steps as f64;
            let height_m = solar_ray_height(
                target_distance_km,
                target_height_m,
                distance_km,
                solar_elevation_deg,
            );
            if height_m <= 0.0 {
                let fraction = previous_height_m / (previous_height_m - height_m);
                let ground_distance_km =
                    previous_distance_km + (distance_km - previous_distance_km) * fraction;
                let ds_m =
                    ((ground_distance_km - previous_distance_km) * 1000.0).hypot(previous_height_m);
                let ground_beta = aod550 / scale_height_m;
                tau += 0.5 * (previous_beta + ground_beta) * ds_m;
                return tau;
            }
            let beta = aod550 / scale_height_m * (-height_m / scale_height_m).exp();
            let ds_m =
                ((distance_km - previous_distance_km) * 1000.0).hypot(height_m - previous_height_m);
            tau += 0.5 * (previous_beta + beta) * ds_m;
            previous_distance_km = distance_km;
            previous_height_m = height_m;
            previous_beta = beta;
        }
        panic!("reference solar ray did not touch the ground");
    }

    #[test]
    fn solar_inflow_touchdown_keeps_the_ground_extinction_endpoint() {
        let reference = fine_solar_inflow_tau(0.25, 40.0, 2000.0, -9.0, 1800.0);
        let actual = solar_inflow_aerosol_tau(0.25, 40.0, 2000.0, -9.0, 1800.0, 0.0);

        assert!((actual / reference - 1.0).abs() <= 0.15);
    }

    #[test]
    fn elevated_observer_uses_the_same_agl_return_profile() {
        let sea_level = observer_aerosol_tau(0.15, 200.0, 7000.0, 1200.0, 0.0);
        let high_station = observer_aerosol_tau(0.15, 200.0, 11000.0, 1200.0, 4000.0);

        assert!((high_station / sea_level - 1.0).abs() <= 0.01);
    }

    #[test]
    fn sea_level_sightline_matches_the_previous_apparent_elevation_geometry() {
        for (target_distance_km, target_height_m, sample_distance_km) in [
            (40.0, 2_000.0, 10.0),
            (180.0, 7_000.0, 90.0),
            (600.0, 12_000.0, 300.0),
        ] {
            let previous = apparent_cloud_elevation(target_distance_km, target_height_m)
                .to_radians()
                .tan()
                * sample_distance_km
                * 1000.0
                + curvature_drop_m(sample_distance_km);
            let current = sightline_height_from_observer(
                target_distance_km,
                target_height_m,
                sample_distance_km,
                0.0,
            );
            assert!((current - previous).abs() <= 1e-10);
        }
    }

    #[test]
    fn aerosol_column_above_fraction_is_bounded_at_the_station() {
        assert_eq!(aerosol_column_above_fraction(-500.0, 0.0), 1.0);
        assert_eq!(aerosol_column_above_fraction(3500.0, 4000.0), 1.0);
        assert!(aerosol_column_above_fraction(10_000.0, 4000.0) < 1.0);
    }
}

// ════════════════════════════════════════════════════════════
// Cloud-hole light-path batch scoring
// ════════════════════════════════════════════════════════════

const COLUMN_FIELDS: usize = 14;
const COL_DISTANCE: usize = 0;
const COL_BEARING: usize = 1;
const COL_HIGH_PRES: usize = 2;
const COL_MID_PRES: usize = 3;
const COL_LOW_PRES: usize = 4;
const COL_DEEP_RISK: usize = 5;
const COL_THICK_RISK: usize = 6;
const COL_BURNABILITY: usize = 7;
const COL_BASE_HEIGHT: usize = 8;
const COL_TOP_HEIGHT: usize = 9;

const SIGNAL_FIELDS: usize = 15;

type Column = [f64; COLUMN_FIELDS];

#[inline]
fn column_path_presence(col: &Column) -> f64 {
    clamp01(
        col[COL_LOW_PRES]
            .max(0.86 * col[COL_MID_PRES])
            .max(0.65 * col[COL_HIGH_PRES]),
    )
}

#[inline]
fn cloud_hole_clear_slot(col: &Column) -> f64 {
    clamp01((0.56 - column_path_presence(col)) / 0.56)
}

fn cloud_hole_edge_contrast(columns: &[Column], hole_idx: usize) -> f64 {
    let hole = &columns[hole_idx];
    let hole_presence = column_path_presence(hole);
    let mut max_neighbor: f64 = 0.0;
    let mut found = false;
    for (i, col) in columns.iter().enumerate() {
        if i == hole_idx {
            continue;
        }
        let az_delta = angular_difference(col[COL_BEARING], hole[COL_BEARING]);
        let dist_delta = (col[COL_DISTANCE] - hole[COL_DISTANCE]).abs();
        let qualifies = (az_delta <= 34.0 && (20.0..=95.0).contains(&dist_delta))
            || (az_delta >= 8.0 && az_delta <= 36.0 && dist_delta <= 80.0);
        if qualifies {
            let p = column_path_presence(col);
            if p > max_neighbor {
                max_neighbor = p;
            }
            found = true;
        }
    }
    if !found {
        return 0.0;
    }
    clamp01((max_neighbor - hole_presence) / 0.62)
}

#[inline]
fn cloud_hole_blocking_risk(col: &Column) -> f64 {
    let low_mid_block = col[COL_LOW_PRES].max(0.82 * col[COL_MID_PRES]);
    let thick_upper_block = col[COL_HIGH_PRES].max(col[COL_MID_PRES])
        * clamp01(
            1.0 - col[COL_BURNABILITY] + 0.45 * col[COL_THICK_RISK] + 0.45 * col[COL_DEEP_RISK],
        );
    clamp01(
        0.66 * low_mid_block
            + 0.46 * col[COL_DEEP_RISK]
            + 0.28 * col[COL_THICK_RISK]
            + 0.30 * thick_upper_block,
    )
}

/// Path clearance scanning only blocker candidates within bearing bins and distance range.
fn cloud_hole_path_clearance_opt(
    columns: &[Column],
    scan_indices: &[usize],
    blocker_values: &[f64],
    hole_idx: usize,
    target_idx: usize,
) -> f64 {
    let hole = &columns[hole_idx];
    let target = &columns[target_idx];
    let min_dist = hole[COL_DISTANCE] + 8.0;
    let max_dist = target[COL_DISTANCE] - 8.0;
    if min_dist >= max_dist {
        return 1.0;
    }
    let span = (target[COL_DISTANCE] - hole[COL_DISTANCE]).max(1.0);
    let mut max_penalty: f64 = 0.0;
    for &idx in scan_indices {
        if idx == hole_idx || idx == target_idx {
            continue;
        }
        let d = columns[idx][COL_DISTANCE];
        if d <= min_dist || d >= max_dist {
            continue;
        }
        if angular_difference(columns[idx][COL_BEARING], target[COL_BEARING]) > 18.0 {
            continue;
        }
        let blocker = blocker_values[idx];
        if blocker <= 0.0 {
            continue;
        }
        let distance_factor = clamp01(1.0 - (d - hole[COL_DISTANCE]) / span);
        let penalty = blocker * (0.55 + 0.45 * distance_factor);
        if penalty > max_penalty {
            max_penalty = penalty;
        }
    }
    if max_penalty == 0.0 {
        1.0
    } else {
        clamp01(1.0 - max_penalty)
    }
}

/// Rear blocking risk scanning only candidates within bearing bins and distance range.
fn cloud_hole_rear_blocking_opt(
    columns: &[Column],
    scan_indices: &[usize],
    blocking_risks: &[f64],
    hole_idx: usize,
    target_idx: usize,
) -> f64 {
    let hole = &columns[hole_idx];
    let target = &columns[target_idx];
    let min_dist = hole[COL_DISTANCE] + 8.0;
    let max_dist = target[COL_DISTANCE] - 8.0;
    let span = (target[COL_DISTANCE] - hole[COL_DISTANCE]).max(1.0);
    // target self-blocking risk
    let target_self = clamp01(
        0.55 * target[COL_DEEP_RISK] + 0.35 * target[COL_THICK_RISK] + 0.28 * target[COL_LOW_PRES],
    );
    let mut max_risk = target_self;
    if min_dist >= max_dist {
        return max_risk;
    }
    for &idx in scan_indices {
        if idx == hole_idx || idx == target_idx {
            continue;
        }
        let d = columns[idx][COL_DISTANCE];
        if d <= min_dist || d >= max_dist {
            continue;
        }
        if angular_difference(columns[idx][COL_BEARING], target[COL_BEARING]) > 18.0
            && angular_difference(columns[idx][COL_BEARING], hole[COL_BEARING]) > 18.0
        {
            continue;
        }
        let risk = blocking_risks[idx];
        if risk <= 0.0 {
            continue;
        }
        let distance_factor = clamp01(1.0 - (d - hole[COL_DISTANCE]) / span);
        let weighted = risk * (0.62 + 0.38 * distance_factor);
        if weighted > max_risk {
            max_risk = weighted;
        }
    }
    max_risk
}

pub fn required_illumination_height_m(
    distance_km: f64,
    solar_elevation_deg: f64,
    sector_delta_deg: f64,
) -> f64 {
    let directional_distance_km = distance_km.max(0.0) * sector_delta_deg.to_radians().cos();
    let local_elevation_rad = solar_illumination_elevation_deg(solar_elevation_deg).to_radians()
        + directional_distance_km / EARTH_RADIUS_KM;
    if local_elevation_rad >= 0.0 {
        return 0.0;
    }
    let depression_rad = (-local_elevation_rad).min(20.0_f64.to_radians());
    EARTH_RADIUS_KM * 1000.0 * (1.0 / depression_rad.cos() - 1.0)
}

#[inline]
fn illumination_feasibility(cloud_height_m: f64, required_height_m: f64) -> f64 {
    clamp01((cloud_height_m - required_height_m + 1200.0) / 3600.0)
}

fn cloud_hole_rear_target_score(
    col: &Column,
    solar_elevation_deg: f64,
    sector_delta_deg: f64,
) -> f64 {
    let high_mid = col[COL_HIGH_PRES].max(0.78 * col[COL_MID_PRES]);
    if high_mid <= 0.0 {
        return 0.0;
    }
    let required_height =
        required_illumination_height_m(col[COL_DISTANCE], solar_elevation_deg, sector_delta_deg);
    let height_score = illumination_feasibility(col[COL_BASE_HEIGHT], required_height);
    let distance_score = clamp01(1.0 - (col[COL_DISTANCE] - 210.0).abs() / 240.0);
    let self_blocking = cloud_hole_blocking_risk(col);
    clamp01(
        high_mid
            * col[COL_BURNABILITY]
            * height_score
            * (0.45 + 0.55 * distance_score)
            * (1.0 - 0.65 * col[COL_DEEP_RISK])
            * (1.0 - 0.36 * self_blocking),
    )
}

/// Batch-score all cloud-hole light-path signals (optimized with bearing-bin indexing
/// and upper-bound pruning to avoid unnecessary inner-loop scans).
///
/// Input: flat array of columns (COLUMN_FIELDS doubles each).
/// Output: flat array of signals (SIGNAL_FIELDS doubles each), sorted by score descending.
///
/// # Safety
///
/// All pointers must be valid for their declared lengths.
#[no_mangle]
pub unsafe extern "C" fn firesky_cloud_hole_batch(
    columns_ptr: *const f64,
    columns_len: usize,
    solar_elevation_deg: f64,
    solar_azimuth_deg: f64,
    output_ptr: *mut f64,
    output_capacity: usize,
) -> isize {
    if columns_ptr.is_null() || output_ptr.is_null() {
        return -1;
    }
    let col_values = slice::from_raw_parts(columns_ptr, columns_len * COLUMN_FIELDS);
    let columns: &[Column] =
        slice::from_raw_parts(col_values.as_ptr().cast::<Column>(), columns_len);
    let output_signals = slice::from_raw_parts_mut(output_ptr, output_capacity * SIGNAL_FIELDS);

    // ── Pre-compute per-column blocker opacity and blocking risk ──
    let blocker_values: Vec<f64> = columns
        .iter()
        .map(|col| {
            clamp01(
                col[COL_LOW_PRES]
                    .max(0.82 * col[COL_MID_PRES])
                    .max(0.45 * col[COL_DEEP_RISK]),
            )
        })
        .collect();

    let blocking_risks: Vec<f64> = columns
        .iter()
        .map(|col| cloud_hole_blocking_risk(col))
        .collect();

    // ── FlatBins spatial index (2° bins, 180 total, zero-alloc) ──
    const NUM_BINS: usize = 180;
    let mut bin_counts = [0usize; NUM_BINS];
    for col in columns.iter() {
        let key = ((col[COL_BEARING].rem_euclid(360.0) / 2.0).floor() as usize) % NUM_BINS;
        bin_counts[key] += 1;
    }
    let mut bin_offsets = [0usize; NUM_BINS + 1];
    for i in 0..NUM_BINS {
        bin_offsets[i + 1] = bin_offsets[i] + bin_counts[i];
    }
    let total_entries = bin_offsets[NUM_BINS];
    let mut flat_indices = vec![0usize; total_entries];
    let mut cursor = [0usize; NUM_BINS];
    for i in 0..NUM_BINS {
        cursor[i] = bin_offsets[i];
    }
    for (i, col) in columns.iter().enumerate() {
        let key = ((col[COL_BEARING].rem_euclid(360.0) / 2.0).floor() as usize) % NUM_BINS;
        flat_indices[cursor[key]] = i;
        cursor[key] += 1;
    }

    // BinBitmap for zero-alloc bin-range scanning
    struct BinBitmap {
        bits: [u64; 3],
    }
    impl BinBitmap {
        #[inline]
        fn new() -> Self {
            BinBitmap { bits: [0; 3] }
        }
        #[inline]
        fn set_range(&mut self, bearing: f64, tolerance_deg: f64) {
            let center =
                ((bearing.rem_euclid(360.0) / 2.0).floor() as isize).rem_euclid(NUM_BINS as isize);
            let half_span = (tolerance_deg / 2.0).ceil() as isize;
            for offset in -half_span..=half_span {
                let bin = ((center + offset).rem_euclid(NUM_BINS as isize)) as usize;
                self.bits[bin / 64] |= 1u64 << (bin % 64);
            }
        }
        #[inline]
        fn union_with(&mut self, other: &BinBitmap) {
            for i in 0..3 {
                self.bits[i] |= other.bits[i];
            }
        }
        fn collect_indices(
            &self,
            bin_offsets: &[usize; NUM_BINS + 1],
            flat_indices: &[usize],
            out: &mut Vec<usize>,
        ) {
            out.clear();
            for word_idx in 0..3 {
                let mut word = self.bits[word_idx];
                while word != 0 {
                    let bit = word.trailing_zeros() as usize;
                    let bin = word_idx * 64 + bit;
                    let start = bin_offsets[bin];
                    let end = bin_offsets[bin + 1];
                    out.extend_from_slice(&flat_indices[start..end]);
                    word &= word - 1;
                }
            }
        }
    }

    // ── Cloud hole minimum penetration threshold (#5) ──
    fn cloud_hole_min_penetration_km(
        hole_distance_km: f64,
        solar_elevation_deg: f64,
        sector_delta_deg: f64,
        cloud_thickness_m: f64,
    ) -> f64 {
        let directional_distance = hole_distance_km * sector_delta_deg.to_radians().cos();
        let local_elevation_rad = solar_illumination_elevation_deg(solar_elevation_deg)
            .to_radians()
            + directional_distance / EARTH_RADIUS_KM;
        if local_elevation_rad >= 0.0 {
            return 0.0;
        }
        let abs_elev = (-local_elevation_rad).min(20.0_f64.to_radians());
        if abs_elev < 1e-6 {
            return f64::INFINITY;
        }
        cloud_thickness_m / (abs_elev.tan() * 1000.0)
    }

    // ── Pre-filter: distance ≤ shared analysis radius, sector ≤ 75° ──
    let filtered: Vec<usize> = columns
        .iter()
        .enumerate()
        .filter_map(|(i, col)| {
            if col[COL_DISTANCE] <= MAX_ANALYSIS_RADIUS_KM
                && angular_difference(col[COL_BEARING], solar_azimuth_deg) <= 75.0
            {
                Some(i)
            } else {
                None
            }
        })
        .collect();

    // ── Identify hole candidates (stricter filter + penetration threshold #5) ──
    let hole_candidates: Vec<usize> = filtered
        .iter()
        .copied()
        .filter(|&i| {
            let col = &columns[i];
            if col[COL_DISTANCE] < 25.0
                || col[COL_DISTANCE] > 260.0
                || angular_difference(col[COL_BEARING], solar_azimuth_deg) > 32.0
            {
                return false;
            }
            // Minimum penetration threshold: filter geometrically impossible holes
            let cloud_thickness_m = col[COL_TOP_HEIGHT].max(500.0);
            let sector_delta = angular_difference(col[COL_BEARING], solar_azimuth_deg);
            let min_penetration = cloud_hole_min_penetration_km(
                col[COL_DISTANCE],
                solar_elevation_deg,
                sector_delta,
                cloud_thickness_m,
            );
            min_penetration <= 50.0
        })
        .collect();

    let mut signals: Vec<[f64; SIGNAL_FIELDS]> = Vec::new();

    for &hole_idx in &hole_candidates {
        let hole = &columns[hole_idx];
        let hole_dist = hole[COL_DISTANCE];
        let sector_delta = angular_difference(hole[COL_BEARING], solar_azimuth_deg);
        let clear_slot = cloud_hole_clear_slot(hole);
        if clear_slot < 0.45 {
            continue;
        }
        let edge_contrast = cloud_hole_edge_contrast(columns, hole_idx);
        if edge_contrast < 0.35 {
            continue;
        }

        // Pre-compute hole bearing bitmap (shared across all targets for rear_blocking)
        let mut hole_bitmap = BinBitmap::new();
        hole_bitmap.set_range(hole[COL_BEARING], 18.0);

        // Reusable scan buffers for inner loop bitmap expansion
        let mut path_buf: Vec<usize> = Vec::with_capacity(256);
        let mut block_buf: Vec<usize> = Vec::with_capacity(256);

        // ── Build valid target list (upper bound = target_score, since alignment ≤ 1) ──
        let mut valid_targets: Vec<(usize, f64)> = Vec::new();
        for &target_idx in &filtered {
            let target = &columns[target_idx];
            if target[COL_DISTANCE] <= hole_dist + 35.0
                || target[COL_DISTANCE] > MAX_ANALYSIS_RADIUS_KM
            {
                continue;
            }
            let target_delta = angular_difference(target[COL_BEARING], solar_azimuth_deg);
            let hole_target_delta = angular_difference(target[COL_BEARING], hole[COL_BEARING]);
            if target_delta > 38.0 || hole_target_delta > 24.0 {
                continue;
            }
            let target_score =
                cloud_hole_rear_target_score(target, solar_elevation_deg, target_delta);
            if target_score < 0.22 {
                continue;
            }
            valid_targets.push((target_idx, target_score));
        }

        // ── Early skip: no valid targets → no signal for this hole ──
        if valid_targets.is_empty() {
            continue;
        }

        // ── Sort by target_score descending for earlier pruning ──
        valid_targets.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

        let mut best_combined: f64 = 0.0;
        let mut best_target_idx: usize = 0;
        let mut best_target_score: f64 = 0.0;
        let mut best_alignment: f64 = 0.0;
        let mut best_clearance: f64 = 0.0;
        let mut best_rear_blocking: f64 = 0.0;

        for &(target_idx, target_score) in &valid_targets {
            // ── Upper bound pruning: alignment ≤ 1, clearance ≤ 1, blocking ≥ 0 ──
            if target_score <= best_combined {
                break; // remaining targets have even lower target_score
            }

            let target = &columns[target_idx];
            let target_delta = angular_difference(target[COL_BEARING], solar_azimuth_deg);
            let hole_target_delta = angular_difference(target[COL_BEARING], hole[COL_BEARING]);
            let alignment =
                clamp01(1.0 - hole_target_delta / 24.0) * clamp01(1.0 - target_delta / 44.0);

            // ── Tighter pruning: target_score × alignment is upper bound ──
            if target_score * alignment <= best_combined {
                continue;
            }

            // ── Inner loop via bearing bins (BinBitmap zero-alloc) ──
            // path_clearance: only target bearing ±18°
            let mut path_bitmap = BinBitmap::new();
            path_bitmap.set_range(target[COL_BEARING], 18.0);
            path_bitmap.collect_indices(&bin_offsets, &flat_indices, &mut path_buf);

            // rear_blocking: union of target ±18° and hole ±18° (Python OR semantics)
            let mut block_bitmap = path_bitmap; // copy target bits
            block_bitmap.union_with(&hole_bitmap);
            block_bitmap.collect_indices(&bin_offsets, &flat_indices, &mut block_buf);

            let clearance = cloud_hole_path_clearance_opt(
                columns,
                &path_buf,
                &blocker_values,
                hole_idx,
                target_idx,
            );
            let rear_blocking = cloud_hole_rear_blocking_opt(
                columns,
                &block_buf,
                &blocking_risks,
                hole_idx,
                target_idx,
            );
            if rear_blocking > 0.72 {
                continue;
            }
            let combined = target_score * alignment * clearance * (1.0 - 0.62 * rear_blocking);
            if combined > best_combined {
                best_combined = combined;
                best_target_idx = target_idx;
                best_target_score = target_score;
                best_alignment = alignment;
                best_clearance = clearance;
                best_rear_blocking = rear_blocking;
            }
        }

        if best_combined <= 0.0 {
            continue;
        }

        let target = &columns[best_target_idx];
        let distance_gap = target[COL_DISTANCE] - hole_dist;
        let distance_gap_score =
            clamp01((distance_gap - 30.0) / 130.0) * clamp01(1.0 - (distance_gap - 260.0) / 260.0);
        let score = clamp01(
            (0.26 * clear_slot
                + 0.22 * edge_contrast
                + 0.24 * best_target_score
                + 0.14 * best_alignment
                + 0.10 * best_clearance
                + 0.04 * distance_gap_score)
                * (1.0 - 0.50 * best_rear_blocking),
        );
        if score <= 0.22 {
            continue;
        }

        if signals.len() >= output_capacity {
            break;
        }

        let mut sig = [0.0f64; SIGNAL_FIELDS];
        sig[0] = hole_dist;
        sig[1] = hole[COL_BEARING];
        sig[2] = sector_delta;
        sig[3] = clear_slot;
        sig[4] = edge_contrast;
        sig[5] = target[COL_DISTANCE];
        sig[6] = target[COL_BEARING];
        sig[7] = best_target_score;
        sig[8] = best_alignment;
        sig[9] = best_clearance;
        sig[10] = best_rear_blocking;
        sig[11] = score;
        sig[12] = target[COL_BURNABILITY];
        sig[13] = target[COL_DEEP_RISK];
        sig[14] = target[COL_THICK_RISK];
        signals.push(sig);
    }

    // Sort by score descending
    signals.sort_by(|a, b| {
        b[11]
            .partial_cmp(&a[11])
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let count = signals.len().min(output_capacity);
    for (i, sig) in signals.iter().take(count).enumerate() {
        let offset = i * SIGNAL_FIELDS;
        output_signals[offset..offset + SIGNAL_FIELDS].copy_from_slice(sig);
    }
    count as isize
}

#[no_mangle]
pub extern "C" fn firesky_national_min_remote_low_target_distance_km() -> f32 {
    national::MIN_REMOTE_LOW_TARGET_DISTANCE_KM
}

/// Version of the native aerosol input checks available at the ABI boundary.
#[no_mangle]
pub extern "C" fn firesky_national_aerosol_validation_version() -> u32 {
    1
}

/// Time-major nationwide assessment. Inputs and output are contiguous float32
/// tensors so Python does not construct per-point feature objects.
unsafe fn national_assess_batch_impl(
    grid: *const f32,
    time_count: usize,
    cell_count: usize,
    aerosol_profiles: *const f32,
    aerosol_profile_len: usize,
    aerosol_level_count: usize,
    observers: *const f32,
    observer_count: usize,
    samples: *const f32,
    sample_count: usize,
    step_count: usize,
    rows: usize,
    cols: usize,
    lat0: f32,
    lon0: f32,
    spacing: f32,
    radius_km: f32,
    output: *mut f32,
) -> i32 {
    if grid.is_null()
        || aerosol_profiles.is_null()
        || observers.is_null()
        || samples.is_null()
        || output.is_null()
    {
        return -1;
    }
    let Some(grid_len) = time_count
        .checked_mul(cell_count)
        .and_then(|value| value.checked_mul(national::GRID_FIELDS))
    else {
        return -2;
    };
    let Some(expected_aerosol_profile_len) = time_count
        .checked_mul(cell_count)
        .and_then(|value| value.checked_mul(national::AEROSOL_PROFILE_LEVELS))
    else {
        return -2;
    };
    if aerosol_level_count != national::AEROSOL_PROFILE_LEVELS
        || aerosol_profile_len != expected_aerosol_profile_len
    {
        return -2;
    }
    let Some(observer_len) = observer_count.checked_mul(national::OBSERVER_FIELDS) else {
        return -2;
    };
    let Some(sample_len) = sample_count.checked_mul(national::SAMPLE_FIELDS) else {
        return -2;
    };
    let Some(output_len) = sample_count.checked_mul(national::OUTPUT_FIELDS) else {
        return -2;
    };
    let grid = slice::from_raw_parts(grid, grid_len);
    let aerosol_profiles = slice::from_raw_parts(aerosol_profiles, aerosol_profile_len);
    let observers = slice::from_raw_parts(observers, observer_len);
    let samples = slice::from_raw_parts(samples, sample_len);
    let output = slice::from_raw_parts_mut(output, output_len);
    match national::assess_batch_with_status(
        grid,
        aerosol_profiles,
        aerosol_level_count,
        time_count,
        cell_count,
        observers,
        observer_count,
        samples,
        sample_count,
        step_count,
        rows,
        cols,
        lat0,
        lon0,
        spacing,
        radius_km,
        output,
    ) {
        Ok(()) => 0,
        Err(national::NationalAssessmentError::InvalidAerosolInputs) => -4,
        Err(national::NationalAssessmentError::InvalidDimensions) => -3,
        // The plain FFI entry never passes a cancel flag; keep the match
        // exhaustive for the shared error enum.
        Err(national::NationalAssessmentError::Cancelled) => -5,
    }
}

#[no_mangle]
pub unsafe extern "C" fn firesky_national_assess_batch(
    grid: *const f32,
    time_count: usize,
    cell_count: usize,
    aerosol_profiles: *const f32,
    aerosol_profile_len: usize,
    aerosol_level_count: usize,
    observers: *const f32,
    observer_count: usize,
    samples: *const f32,
    sample_count: usize,
    step_count: usize,
    rows: usize,
    cols: usize,
    lat0: f32,
    lon0: f32,
    spacing: f32,
    radius_km: f32,
    output: *mut f32,
) -> i32 {
    national_assess_batch_impl(
        grid,
        time_count,
        cell_count,
        aerosol_profiles,
        aerosol_profile_len,
        aerosol_level_count,
        observers,
        observer_count,
        samples,
        sample_count,
        step_count,
        rows,
        cols,
        lat0,
        lon0,
        spacing,
        radius_km,
        output,
    )
}

/// Offline-only selected-sample path diagnostics. This intentionally does not
/// append to the current national frame output or enter the production batch path.
#[no_mangle]
pub unsafe extern "C" fn firesky_national_audit_selected(
    grid: *const f32,
    time_count: usize,
    cell_count: usize,
    aerosol_profiles: *const f32,
    aerosol_profile_len: usize,
    aerosol_level_count: usize,
    observers: *const f32,
    observer_count: usize,
    samples: *const f32,
    sample_count: usize,
    step_count: usize,
    rows: usize,
    cols: usize,
    lat0: f32,
    lon0: f32,
    spacing: f32,
    radius_km: f32,
    audit_indices: *const usize,
    audit_count: usize,
    output: *mut f32,
) -> i32 {
    if grid.is_null() || aerosol_profiles.is_null() || observers.is_null() || samples.is_null() {
        return -1;
    }
    if audit_count > 0 && (audit_indices.is_null() || output.is_null()) {
        return -1;
    }
    let Some(grid_len) = time_count
        .checked_mul(cell_count)
        .and_then(|value| value.checked_mul(national::GRID_FIELDS))
    else {
        return -2;
    };
    let Some(expected_aerosol_profile_len) = time_count
        .checked_mul(cell_count)
        .and_then(|value| value.checked_mul(national::AEROSOL_PROFILE_LEVELS))
    else {
        return -2;
    };
    if aerosol_level_count != national::AEROSOL_PROFILE_LEVELS
        || aerosol_profile_len != expected_aerosol_profile_len
    {
        return -2;
    }
    if audit_count == 0 {
        return 0;
    }
    let Some(observer_len) = observer_count.checked_mul(national::OBSERVER_FIELDS) else {
        return -2;
    };
    let Some(sample_len) = sample_count.checked_mul(national::SAMPLE_FIELDS) else {
        return -2;
    };
    let Some(output_len) = audit_count.checked_mul(national::PATH_AUDIT_FIELDS) else {
        return -2;
    };
    let grid = slice::from_raw_parts(grid, grid_len);
    let aerosol_profiles = slice::from_raw_parts(aerosol_profiles, aerosol_profile_len);
    let observers = slice::from_raw_parts(observers, observer_len);
    let samples = slice::from_raw_parts(samples, sample_len);
    let audit_indices = slice::from_raw_parts(audit_indices, audit_count);
    let output = slice::from_raw_parts_mut(output, output_len);
    if national::audit_selected_batch(
        grid,
        aerosol_profiles,
        aerosol_level_count,
        time_count,
        cell_count,
        observers,
        observer_count,
        samples,
        sample_count,
        step_count,
        rows,
        cols,
        lat0,
        lon0,
        spacing,
        radius_km,
        audit_indices,
        output,
    ) {
        0
    } else {
        -3
    }
}

/// Offline-only per-target diagnostics for selected sample rows. The fixed
/// output is intentionally separate from national frame data and scoring.
#[no_mangle]
pub unsafe extern "C" fn firesky_national_audit_selected_targets(
    grid: *const f32,
    time_count: usize,
    cell_count: usize,
    aerosol_profiles: *const f32,
    aerosol_profile_len: usize,
    aerosol_level_count: usize,
    observers: *const f32,
    observer_count: usize,
    samples: *const f32,
    sample_count: usize,
    step_count: usize,
    rows: usize,
    cols: usize,
    lat0: f32,
    lon0: f32,
    spacing: f32,
    radius_km: f32,
    audit_indices: *const usize,
    audit_count: usize,
    output: *mut f32,
) -> i32 {
    if grid.is_null() || aerosol_profiles.is_null() || observers.is_null() || samples.is_null() {
        return -1;
    }
    if audit_count > 0 && (audit_indices.is_null() || output.is_null()) {
        return -1;
    }
    let Some(grid_len) = time_count
        .checked_mul(cell_count)
        .and_then(|value| value.checked_mul(national::GRID_FIELDS))
    else {
        return -2;
    };
    let Some(expected_aerosol_profile_len) = time_count
        .checked_mul(cell_count)
        .and_then(|value| value.checked_mul(national::AEROSOL_PROFILE_LEVELS))
    else {
        return -2;
    };
    if aerosol_level_count != national::AEROSOL_PROFILE_LEVELS
        || aerosol_profile_len != expected_aerosol_profile_len
    {
        return -2;
    }
    if audit_count == 0 {
        return 0;
    }
    let Some(observer_len) = observer_count.checked_mul(national::OBSERVER_FIELDS) else {
        return -2;
    };
    let Some(sample_len) = sample_count.checked_mul(national::SAMPLE_FIELDS) else {
        return -2;
    };
    let Some(output_len) = audit_count
        .checked_mul(national::TARGET_AUDIT_MAX_RECORDS)
        .and_then(|value| value.checked_mul(national::TARGET_AUDIT_FIELDS))
    else {
        return -2;
    };
    let grid = slice::from_raw_parts(grid, grid_len);
    let aerosol_profiles = slice::from_raw_parts(aerosol_profiles, aerosol_profile_len);
    let observers = slice::from_raw_parts(observers, observer_len);
    let samples = slice::from_raw_parts(samples, sample_len);
    let audit_indices = slice::from_raw_parts(audit_indices, audit_count);
    let output = slice::from_raw_parts_mut(output, output_len);
    if national::audit_selected_targets(
        grid,
        aerosol_profiles,
        aerosol_level_count,
        time_count,
        cell_count,
        observers,
        observer_count,
        samples,
        sample_count,
        step_count,
        rows,
        cols,
        lat0,
        lon0,
        spacing,
        radius_km,
        audit_indices,
        output,
    ) {
        0
    } else {
        -3
    }
}

#[cfg(test)]
mod national_ffi_tests {
    use super::*;
    use std::ptr;

    #[test]
    fn national_ffi_requires_the_exact_full_aerosol_sidecar_contract() {
        let grid = vec![0.0f32; national::GRID_FIELDS];
        let aerosol_profiles = vec![0.0f32; national::AEROSOL_PROFILE_LEVELS];
        let observers = vec![0.0f32; national::OBSERVER_FIELDS];
        let samples = vec![0.0f32; national::SAMPLE_FIELDS];
        let mut output = vec![0.0f32; national::OUTPUT_FIELDS];

        let mut assess = |profile_len, level_count| unsafe {
            firesky_national_assess_batch(
                grid.as_ptr(),
                1,
                1,
                aerosol_profiles.as_ptr(),
                profile_len,
                level_count,
                observers.as_ptr(),
                1,
                samples.as_ptr(),
                1,
                1,
                1,
                1,
                0.0,
                0.0,
                0.25,
                100.0,
                output.as_mut_ptr(),
            )
        };
        assert_eq!(
            assess(
                national::AEROSOL_PROFILE_LEVELS,
                national::AEROSOL_PROFILE_LEVELS
            ),
            0
        );
        assert_eq!(
            assess(
                national::AEROSOL_PROFILE_LEVELS - 1,
                national::AEROSOL_PROFILE_LEVELS
            ),
            -2
        );
        assert_eq!(
            assess(
                national::AEROSOL_PROFILE_LEVELS,
                national::AEROSOL_PROFILE_LEVELS - 1
            ),
            -2
        );

        let audit = unsafe {
            firesky_national_audit_selected(
                grid.as_ptr(),
                1,
                1,
                aerosol_profiles.as_ptr(),
                national::AEROSOL_PROFILE_LEVELS - 1,
                national::AEROSOL_PROFILE_LEVELS,
                observers.as_ptr(),
                1,
                samples.as_ptr(),
                1,
                1,
                1,
                1,
                0.0,
                0.0,
                0.25,
                100.0,
                ptr::null(),
                0,
                ptr::null_mut(),
            )
        };
        let target_audit = unsafe {
            firesky_national_audit_selected_targets(
                grid.as_ptr(),
                1,
                1,
                aerosol_profiles.as_ptr(),
                national::AEROSOL_PROFILE_LEVELS,
                national::AEROSOL_PROFILE_LEVELS - 1,
                observers.as_ptr(),
                1,
                samples.as_ptr(),
                1,
                1,
                1,
                1,
                0.0,
                0.0,
                0.25,
                100.0,
                ptr::null(),
                0,
                ptr::null_mut(),
            )
        };
        assert_eq!(audit, -2);
        assert_eq!(target_audit, -2);
    }
}

#[cfg(test)]
mod feature_row_v2_tests {
    use super::*;

    fn base_row() -> FeatureRowV2 {
        let mut row = [f64::NAN; FEATURE_ROW_V2_FIELD_COUNT];
        row[LAT] = 35.0;
        row[LON] = 138.0;
        row[DISTANCE] = 30.0;
        row[BEARING] = 90.0;
        row[PRESSURE] = 850.0;
        row[HEIGHT] = 1500.0;
        row[RH] = 70.0;
        row
    }

    fn legacy_row(row: &FeatureRowV2) -> Feature {
        let mut legacy = [f64::NAN; FIELD_COUNT];
        legacy.copy_from_slice(&row[..FIELD_COUNT]);
        legacy
    }

    #[test]
    fn v2_row_contract_has_the_declared_field_names() {
        assert_eq!(FEATURE_ROW_V2_FIELD_COUNT, FIELD_COUNT + 7);
        assert_eq!(FEATURE_ROW_V2_NAMES.len(), FEATURE_ROW_V2_FIELD_COUNT);
        assert_eq!(
            FEATURE_ROW_V2_NAMES[FEATURE_ROW_V2_SURFACE_TEMPERATURE_K],
            "surface_temperature_k"
        );
        assert_eq!(
            FEATURE_ROW_V2_NAMES[FEATURE_ROW_V2_SURFACE_DEWPOINT_K],
            "surface_dewpoint_k"
        );
        assert_eq!(
            FEATURE_ROW_V2_NAMES[FEATURE_ROW_V2_FOOTPRINT_AZIMUTH_WIDTH_DEG],
            "footprint_azimuth_width_deg"
        );
        assert_eq!(
            FEATURE_ROW_V2_NAMES[FEATURE_ROW_V2_FOOTPRINT_NEAR_DISTANCE_KM],
            "footprint_near_distance_km"
        );
        assert_eq!(
            FEATURE_ROW_V2_NAMES[FEATURE_ROW_V2_FOOTPRINT_FAR_DISTANCE_KM],
            "footprint_far_distance_km"
        );
        assert_eq!(
            FEATURE_ROW_V2_NAMES[FEATURE_ROW_V2_HORIZONTAL_CLOUD_PROBABILITY],
            "horizontal_cloud_probability"
        );
        assert_eq!(
            FEATURE_ROW_V2_NAMES[FEATURE_ROW_V2_CLOUD_PROBABILITY_GIVEN_CLOUD],
            "cloud_probability_given_cloud"
        );
    }

    #[test]
    fn horizontal_zero_is_a_hard_zero_not_a_humidity_fallback() {
        let mut row = base_row();
        row[FEATURE_ROW_V2_HORIZONTAL_CLOUD_PROBABILITY] = 0.0;
        row[FEATURE_ROW_V2_CLOUD_PROBABILITY_GIVEN_CLOUD] = 0.6;
        let metrics = v2_feature_metrics_row(&row, 500.0, 0.0);
        // Presence/explicit follow the horizontal occupancy (the effective
        // Python index semantics), and the opacity pair stays 0 even though
        // RH=70% would score non-zero.
        assert_eq!(metrics[0], 0.0);
        assert_eq!(metrics[1], 0.0);
        assert_eq!(metrics[4], 0.0);
        assert_eq!(metrics[5], 0.0);
        assert_eq!(metrics[6], 0.0);
        assert!(metrics[0] != clamp01((row[RH] - 45.0) / 45.0));
    }

    #[test]
    fn horizontal_without_conditional_falls_back_to_horizontal_presence() {
        let mut row = base_row();
        row[FEATURE_ROW_V2_HORIZONTAL_CLOUD_PROBABILITY] = 1.0;
        let metrics = v2_feature_metrics_row(&row, 500.0, 0.0);
        assert_eq!(metrics[0], 1.0);
        assert_eq!(metrics[1], 1.0);
        assert_eq!(metrics[4], 0.78); // 0.22 * 0.0 (rh=70) + 0.78 * 1.0
        assert_eq!(metrics[5], 0.78);
    }

    #[test]
    fn unknown_horizontal_stays_unknown_and_takes_the_fraction_path() {
        let mut row = base_row();
        row[CLOUD_FRACTION] = 40.0;
        let metrics = v2_feature_metrics_row(&row, 500.0, 0.0);
        // NaN horizontal must be "carried nowhere", not 0: presence/explicit
        // come from the model fraction and opacity from the legacy blend.
        assert_eq!(metrics[0], 0.4);
        assert_eq!(metrics[1], 0.4);
        assert_eq!(
            metrics[4],
            clamp01(0.22 * 0.0 + 0.78 * clamp01(0.4 * (0.35 + 0.65 * 0.5)))
        );
        assert_eq!(metrics[5], metrics[4]);
    }

    #[test]
    fn unknown_fraction_and_condensate_fall_back_to_rh_presence() {
        let row = base_row();
        let metrics = v2_feature_metrics_row(&row, 500.0, 0.0);
        assert_eq!(metrics[0], clamp01((row[RH] - 45.0) / 45.0));
        assert_eq!(metrics[1], 0.0);
        assert_eq!(metrics[2], 0.0);
        assert_eq!(metrics[3], 0.0);
        assert_eq!(metrics[4], clamp01((row[RH] - 82.0) / 16.0));
        assert_eq!(metrics[5], clamp01((row[RH] - 88.0) / 10.0));
    }

    #[test]
    fn v2_metrics_match_the_legacy_metrics_for_model_rows_at_sea_level() {
        let mut row = base_row();
        row[CLOUD_FRACTION] = 55.0;
        row[CLOUD_LIQUID] = 1.0e-4;
        let metrics = v2_feature_metrics_row(&row, 500.0, 0.0);
        let legacy = legacy_row(&row);
        let condensate = condensate_presence(&legacy);
        let explicit = explicit_cloud_presence(&legacy, condensate);
        let rh_presence = clamp01((legacy[RH] - 45.0) / 45.0);
        let expected = [
            explicit.map(clamp01).unwrap_or(rh_presence),
            explicit.unwrap_or(0.0),
            condensate_component(legacy[CLOUD_LIQUID]),
            condensate_component(legacy[CLOUD_ICE]),
            feature_cloud_opacity(&legacy),
            feature_blocker_opacity(&legacy),
            lightpath_opacity(&legacy, feature_blocker_opacity(&legacy)),
            estimated_cloud_base(&legacy),
            apparent_cloud_elevation(legacy[DISTANCE], legacy[HEIGHT]),
        ];
        assert_eq!(metrics, expected);
    }

    #[test]
    fn surface_lcl_replaces_the_layer_depth_table_only_when_usable() {
        let mut row = base_row();
        row[FEATURE_ROW_V2_SURFACE_TEMPERATURE_K] = 290.0;
        row[FEATURE_ROW_V2_SURFACE_DEWPOINT_K] = 288.5;
        // Legacy table for 850 hPa: 1500 - 450 = 1050 m. LCL: 10 + 125 * 1.5.
        assert_eq!(v2_estimated_cloud_base(&row, 10.0), 197.5);
        assert_eq!(v2_estimated_cloud_base(&row, 0.0), 187.5);
        assert_eq!(estimated_cloud_base(&row), 1050.0);
        // Far field keeps the legacy table.
        row[DISTANCE] = 400.0;
        assert_eq!(v2_estimated_cloud_base(&row, 10.0), 1050.0);
        row[DISTANCE] = 30.0;
        // Inverted spread keeps the legacy table.
        row[FEATURE_ROW_V2_SURFACE_DEWPOINT_K] = 296.0;
        assert_eq!(v2_estimated_cloud_base(&row, 10.0), 1050.0);
        // Unknown surface pair keeps the legacy table.
        row[FEATURE_ROW_V2_SURFACE_TEMPERATURE_K] = f64::NAN;
        row[FEATURE_ROW_V2_SURFACE_DEWPOINT_K] = 288.5;
        assert_eq!(v2_estimated_cloud_base(&row, 10.0), 1050.0);
        // An LCL above the deck ceiling keeps the legacy table.
        row[FEATURE_ROW_V2_SURFACE_TEMPERATURE_K] = 295.0;
        row[FEATURE_ROW_V2_SURFACE_DEWPOINT_K] = 283.0;
        assert_eq!(v2_estimated_cloud_base(&row, 10.0), 1050.0);
    }

    #[test]
    fn apparent_top_subtracts_the_observer_elevation() {
        assert_eq!(
            v2_apparent_cloud_elevation(30.0, 1500.0, 0.0),
            apparent_cloud_elevation(30.0, 1500.0)
        );
        let sea_level = v2_apparent_cloud_elevation(30.0, 1500.0, 0.0);
        let elevated = v2_apparent_cloud_elevation(30.0, 1500.0, 250.0);
        assert!(elevated < sea_level);
        assert_eq!(
            elevated,
            ((1500.0 - 250.0 - curvature_drop_m(30.0)).atan2((30.0_f64 * 1000.0).max(1.0)))
                .to_degrees()
        );
    }

    #[test]
    fn outside_the_diagnostic_radius_presence_gates_to_zero_but_opacity_stays() {
        let mut row = base_row();
        row[FEATURE_ROW_V2_HORIZONTAL_CLOUD_PROBABILITY] = 0.8;
        row[FEATURE_ROW_V2_CLOUD_PROBABILITY_GIVEN_CLOUD] = 0.9;
        row[CLOUD_LIQUID] = 1.0e-4;
        let inside = v2_feature_metrics_row(&row, 500.0, 0.0);
        let outside = v2_feature_metrics_row(&row, 10.0, 0.0);
        assert_eq!(inside[0], 0.8);
        assert_eq!(outside[0], 0.0);
        assert_eq!(outside[1], 0.0);
        assert_eq!(outside[2], 0.0);
        assert_eq!(outside[3], 0.0);
        assert_eq!(outside[4], inside[4]);
        assert_eq!(outside[5], inside[5]);
        assert_eq!(outside[7], inside[7]);
        assert_eq!(outside[8], inside[8]);
    }
}
